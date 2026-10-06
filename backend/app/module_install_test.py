from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

import httpx

from app import module_install as install
from app.module_catalog import ModuleEnvironment, engine_python
from app.module_evidence import environment_verified
from app.module_jobs import InstallContext, ModuleJobService, ModuleSetupError


class ModuleInstallTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = ModuleEnvironment.for_root(self.root, platform='darwin', architecture='arm64')
        self.context = InstallContext(ModuleJobService(self.environment), 'a' * 32)

    async def test_cancel_waits_for_staging_deletion_before_releasing_setup(self) -> None:
        await self._assert_staging_deletion_drained(shutdown=False)

    async def test_shutdown_waits_for_staging_deletion_before_releasing_setup(self) -> None:
        await self._assert_staging_deletion_drained(shutdown=True)

    async def _assert_staging_deletion_drained(self, *, shutdown: bool) -> None:
        from app import module_catalog as catalog, module_jobs as jobs
        from app.module_contracts import ModuleInstallRequest, ModulePlanRequest
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        actual = install.shutil.rmtree
        def delete(path: Path) -> None:
            entered.set()
            release.wait(5)
            actual(path)
            finished.set()
        async def installer(context: InstallContext, identifier: catalog.ModuleId, download: bool) -> jobs.InstallOutcome:
            staged = context.workspace / 'speech-source'
            staged.mkdir(parents=True)
            await install._clone(context, 'speech', install.SOURCE_PINS['speech'], context.environment.paths['speech'])
            return jobs.InstallOutcome('verified', 'unused')
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))), patch.object(jobs, '_application_busy', return_value=False), patch.object(install.shutil, 'rmtree', delete):
            service = ModuleJobService(self.environment, installer=installer)
            reviewed = await catalog.plan(ModulePlanRequest(features=['media']), self.environment)
            created = await service.create(ModuleInstallRequest(features=['media'], plan_token=reviewed.plan_token))
            cancelled: asyncio.Task[jobs.ModuleInstallJob] | asyncio.Task[None] | None = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                cancelled = asyncio.create_task(service.shutdown()) if shutdown else asyncio.create_task(service.cancel(created.id))
                await asyncio.sleep(0.05)
                self.assertFalse(cancelled.done(), 'cancel must wait for the deletion thread')
                self.assertTrue(jobs.work_busy(), 'scratch cannot be reused until deletion drains')
                with self.assertRaisesRegex(ModuleSetupError, 'setup_busy'):
                    await ModuleJobService(self.environment).create(ModuleInstallRequest(features=['media'], plan_token=reviewed.plan_token))
            finally:
                release.set()
                if cancelled is not None:
                    await cancelled
                await service.shutdown()
                self.assertTrue(await asyncio.to_thread(finished.wait, 2))
            self.assertEqual(service.get(created.id).state, 'interrupted' if shutdown else 'cancelled')
            self.assertFalse(jobs.work_busy())
            staged = service.environment.root / '.setup/staging' / created.id / 'speech-source'
            staged.mkdir()
            (staged / 'new-source').write_text('retained after cleanup')
            await asyncio.sleep(0.01)
            self.assertEqual((staged / 'new-source').read_text(), 'retained after cleanup')

    async def test_verified_download_resumes_known_artifact_only(self) -> None:
        content = b'verified-content'
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
        target = self.root / 'cache' / 'file.bin'
        target.parent.mkdir()
        target.with_suffix('.bin.part').write_bytes(content[:4])
        def response(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers['range'], 'bytes=4-')
            return httpx.Response(206, headers={'content-range': f'bytes 4-{len(content)-1}/{len(content)}'}, content=content[4:])
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            await install.download_artifact(artifact, target, self.context, client=client)
        self.assertEqual(target.read_bytes(), content)

    async def test_bad_hash_preserves_existing_artifact(self) -> None:
        target = self.root / 'cache' / 'file.bin'
        target.parent.mkdir()
        target.write_bytes(b'old-good-file')
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256='0' * 64, bytes=3)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'bad'))) as client:
            with self.assertRaisesRegex(ModuleSetupError, 'artifact_integrity'):
                await install.download_artifact(artifact, target, self.context, client=client)
        self.assertEqual(target.read_bytes(), b'old-good-file')

    async def test_transient_download_retries_are_bounded_and_keep_existing_artifact(self) -> None:
        content = b'verified retry'
        target = self.root / 'cache' / 'retry.bin'
        target.parent.mkdir()
        target.write_bytes(b'original')
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
        calls = 0
        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(503) if calls < 3 else httpx.Response(200, content=content)
        with patch.object(install.asyncio, 'sleep', new=AsyncMock()):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                try:
                    await install.download_artifact(artifact, target, self.context, client=client)
                except httpx.HTTPError:
                    self.fail('a transient asset response must retry within the reviewed attempt bound')
        self.assertEqual(calls, 3)
        self.assertEqual(target.read_bytes(), content)

    async def test_short_download_resumes_received_bytes_without_starting_again(self) -> None:
        content = b'verified partial transfer'
        target = self.root / 'cache' / 'short.bin'
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
        calls = 0
        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(200, content=content[:4])
            self.assertEqual(request.headers.get('range'), 'bytes=4-')
            return httpx.Response(206, headers={'content-range': f'bytes 4-{len(content)-1}/{len(content)}'}, content=content[4:])
        with patch.object(install.asyncio, 'sleep', new=AsyncMock()):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                try:
                    await install.download_artifact(artifact, target, self.context, client=client)
                except ModuleSetupError:
                    self.fail('an incomplete transfer must retain and resume its received prefix')
        self.assertEqual(calls, 2)
        self.assertEqual(target.read_bytes(), content)

    async def test_inconsistent_resume_range_is_rejected_before_publication(self) -> None:
        content = b'verified range'
        target = self.root / 'cache' / 'range.bin'
        target.parent.mkdir()
        target.with_name(target.name + '.part').write_bytes(content[:4])
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
        response = httpx.Response(206, headers={'content-range': 'bytes 4-999/1000'}, content=content[4:])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
            with self.assertRaisesRegex(ModuleSetupError, 'artifact_integrity'):
                await install.download_artifact(artifact, target, self.context, client=client)
        self.assertFalse(target.exists())

    async def test_stalled_transfer_finishes_with_a_stable_error_and_no_publication(self) -> None:
        class Stalled(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                await asyncio.Event().wait()
                yield b'unreachable'
        target = self.root / 'cache' / 'stall.bin'
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256='0' * 64, bytes=16)
        with patch.object(install, 'DOWNLOAD_STALL_SECONDS', .01, create=True), patch.object(install.asyncio, 'sleep', new=AsyncMock()):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Stalled()))) as client:
                try:
                    await asyncio.wait_for(install.download_artifact(artifact, target, self.context, client=client), .3)
                except ModuleSetupError as error:
                    self.assertEqual(error.code, 'download_stalled')
                except TimeoutError:
                    self.fail('the downloader must bound a stalled stream itself')
                else:
                    self.fail('a stalled transfer cannot publish an artifact')
        self.assertFalse(target.exists())

    async def test_retry_exhaustion_and_integrity_failure_never_replace_prior_data(self) -> None:
        target = self.root / 'cache' / 'prior.bin'
        target.parent.mkdir()
        target.write_bytes(b'original')
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256=hashlib.sha256(b'new').hexdigest(), bytes=3)
        with patch.object(install.asyncio, 'sleep', AsyncMock()):
            for status, content, code, attempts in ((503, b'', 'download_failed', 3), (403, b'', 'download_failed', 1), (200, b'bad', 'artifact_integrity', 1)):
                calls = 0
                def response(request: httpx.Request) -> httpx.Response:
                    nonlocal calls
                    calls += 1
                    return httpx.Response(status, content=content)
                async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                    with self.assertRaisesRegex(ModuleSetupError, code):
                        await install.download_artifact(artifact, target, self.context, client=client)
                self.assertEqual(calls, attempts)
                self.assertEqual(target.read_bytes(), b'original')

    async def test_cancel_during_backoff_retains_received_prefix_and_closes_stream(self) -> None:
        target = self.root / 'cache' / 'cancel.bin'
        artifact = install.DownloadArtifact(url='https://example.invalid/pinned', sha256=hashlib.sha256(b'complete').hexdigest(), bytes=8)
        entered = asyncio.Event()
        actual_sleep = asyncio.sleep
        async def backoff(delay: float) -> None:
            entered.set()
            await asyncio.Event().wait()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'comp'))) as client:
            with patch.object(install.asyncio, 'sleep', side_effect=backoff):
                task = asyncio.create_task(install.download_artifact(artifact, target, self.context, client=client))
                await asyncio.wait_for(entered.wait(), 1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            await actual_sleep(0)
        self.assertFalse(target.exists())
        self.assertEqual(target.with_name(target.name + '.part').read_bytes(), b'comp')

    async def test_native_layers_apply_to_existing_resume_patch_and_remain_resumable(self) -> None:
        import subprocess
        target = self.environment.paths['yue2']
        (target / 'tools').mkdir(parents=True)
        repository = Path(__file__).resolve().parents[2]
        source = repository / 'desktop/test/fixtures/model_manager_v2.py'
        (target / 'tools/model_manager_v2.py').write_bytes(source.read_bytes())
        subprocess.run(['git', 'apply', str(repository / 'external/patches/yue-model-resume.patch')], cwd=target, check=True, capture_output=True)
        commands: list[list[str]] = []
        async def run(argv: list[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None, download_progress: bool = False) -> None:
            commands.append(argv)
            if argv[0] == 'git':
                result = await asyncio.to_thread(subprocess.run, argv, cwd=cwd, capture_output=True)
                if result.returncode:
                    raise ModuleSetupError('installer_failed')
            else:
                self.assertTrue(download_progress)
                self.assertIn('--progress', argv)
        with patch.object(self.context, 'run', side_effect=run):
            await install._native_weights(self.context, target, install.load_manifest(), True)
            commands.clear()
            await install._native_weights(self.context, target, install.load_manifest(), True)
        self.assertEqual(sum(argv[0] == 'git' for argv in commands), 1, 'final-layer identity check must allow reviewed resume')
        self.assertIn('DOWNLOAD_ATTEMPTS = 3', (target / 'tools/model_manager_v2.py').read_text())

    async def test_speaker_setup_is_dependency_only_even_when_weights_selected(self) -> None:
        from app import speaker_review
        from app.speaker_review_contracts import SpeakerReviewCapability
        target = self.environment.paths['speaker_review']
        with patch.object(self.context, 'run', AsyncMock()) as run, patch.object(speaker_review, 'capability', return_value=SpeakerReviewCapability(available=False, deps_available=True, reason='speaker_weights_missing')):
            outcome = await install.install(self.context, 'speaker_review', True)
        self.assertEqual(outcome.state, 'manual')
        self.assertEqual(run.call_count, 1)
        command = run.call_args.args[0]
        self.assertTrue(command[1].endswith('/scripts/setup_speaker_review.py'))
        self.assertEqual(command[command.index('--runtime-root') + 1], str(target))
        self.assertNotIn('--download-models', command)
        self.assertIn('No weights', outcome.detail)

    def test_zip_traversal_and_tar_links_are_rejected_before_extraction(self) -> None:
        archive = self.root / 'bad.zip'
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr('../outside', 'unsafe')
        with self.assertRaisesRegex(ModuleSetupError, 'archive_unsafe'):
            install.extract_archive(archive, self.root / 'staged')
        self.assertFalse((self.root / 'outside').exists())
        archive = self.root / 'bad.tar.gz'
        with tarfile.open(archive, 'w:gz') as output:
            member = tarfile.TarInfo('escaped')
            member.type = tarfile.SYMTYPE
            member.linkname = '/private'
            output.addfile(member)
        with self.assertRaisesRegex(ModuleSetupError, 'archive_unsafe'):
            install.extract_archive(archive, self.root / 'staged')

    async def test_external_and_unowned_existing_installations_are_preserved(self) -> None:
        target = self.environment.paths['speech']
        target.mkdir(parents=True)
        private = target / 'private.txt'
        private.write_text('keep')
        with patch.object(self.context, 'run', AsyncMock(side_effect=AssertionError('must not run'))):
            outcome = await install.install(self.context, 'speech', True)
        self.assertEqual(outcome.state, 'manual')
        self.assertEqual(private.read_text(), 'keep')

    def test_platform_interpreter_and_source_pins_are_fixed(self) -> None:
        self.assertEqual(install.SOURCE_PINS['speech'].commit, '48b1a0169a28582a8984402f82cf438d3bfa6aca')
        self.assertEqual(install.SOURCE_PINS['singing'].commit, '51383efd921027683c89e5348211d93ff12ac2a8')
        self.assertEqual(install.SOURCE_PINS['kokoro'].commit, 'dfb907a02bba8152ca444717ca5d78747ccb4bec')
        self.assertEqual(install.SOURCE_PINS['chatterbox'].commit, '5de7a54aa4e5e2baadb0182dde554908b48b85c2')
        self.assertEqual(install.SOURCE_PINS['wan22'].commit, '87db56a51758fefb748a359b90a5283bb8ba4837')
        self.assertEqual(install.SOURCE_PINS['rvc'].commit, '81eed5e8f68b6bed1789f682fe78cdd324495afc')
        self.assertNotIn('14B', install.SOURCE_PINS['wan22'].repository)
        self.assertEqual(install.venv_python(Path('/engine'), 'win32'), Path('/engine/.venv/Scripts/python.exe'))

    async def test_explicit_external_verification_changes_only_managed_receipt(self) -> None:
        engine = self.root / 'external-speech'
        (engine / 'GPT_SoVITS').mkdir(parents=True)
        (engine / 'api.py').write_text('# source')
        python = engine_python(engine, self.environment.platform)
        python.parent.mkdir(parents=True)
        python.write_text('# interpreter')
        (engine / '.venv/lib/python3.11/site-packages/torch-2.dist-info').mkdir(parents=True)
        paths = dict(self.environment.paths)
        paths['speech'] = engine
        environment = ModuleEnvironment.for_root(self.root, paths=paths, platform='darwin', architecture='arm64')
        context = InstallContext(ModuleJobService(environment), 'b' * 32)
        original = sorted(str(path.relative_to(engine)) for path in engine.rglob('*'))
        with patch.object(context, 'run', AsyncMock()) as run:
            outcome = await install.verify(context, 'speech')
        self.assertEqual(outcome.state, 'manual')
        self.assertTrue(environment_verified(environment, 'speech'))
        self.assertEqual(original, sorted(str(path.relative_to(engine)) for path in engine.rglob('*')))
        self.assertTrue(all('install' not in argument for call in run.call_args_list for argument in call.args[0]))

    async def test_native_patch_ownership_survives_interrupted_weight_download(self) -> None:
        target = self.environment.paths['yue2']
        (target / 'tools').mkdir(parents=True)
        script = target / 'tools/model_manager_v2.py'
        script.write_text('# original')
        assets = install.load_manifest()
        install._record_owned(self.context, 'yue2', target, assets.engine.tag)
        async def run(argv: list[str], **_kwargs: object) -> None:
            if '--reverse' in argv:
                raise ModuleSetupError('installer_failed')
            if argv[:2] == ['git', 'apply'] and '--check' not in argv:
                script.write_text('# verified patch applied')
            if 'install' in argv:
                raise asyncio.CancelledError()
        with patch.object(self.context, 'run', run), patch.object(install.shutil, 'which', return_value='/fixed/git'):
            with self.assertRaises(asyncio.CancelledError):
                await install._native_weights(self.context, target, assets, True)
        self.assertTrue(install._owned(self.context, 'yue2', target, assets.engine.tag))


if __name__ == '__main__':
    unittest.main()
