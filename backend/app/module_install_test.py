from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
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
from app.module_jobs import InstallContext, InstallOutcome, ModuleJobService, ModuleSetupError


class ModuleInstallTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = ModuleEnvironment.for_root(self.root, platform='darwin', architecture='arm64')
        self.context = InstallContext(ModuleJobService(self.environment), 'a' * 32)
        self.enterContext(patch.object(InstallContext, 'update_detail'))

    async def test_music_setup_uses_isolated_dependencies_then_automatic_model_setup(self) -> None:
        from app.module_catalog import DEFINITIONS
        environment = replace(self.environment, uv='/fixed/uv')
        context = InstallContext(ModuleJobService(environment), 'c' * 32)
        async def clone(context: InstallContext, identifier: install.ModuleId, pin: install.SourcePin, target: Path) -> None:
            for marker in DEFINITIONS[identifier].source_markers:
                path = target / marker
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('# pinned source')
        with patch.object(context, 'run', AsyncMock()) as run, patch.object(install, '_clone', clone), patch.object(install, '_check_environment', AsyncMock()), patch.object(install.shutil, 'which', return_value='/fixed/git'), patch.object(install, 'setup_music_models', AsyncMock(return_value=InstallOutcome('verified', 'Models installed automatically.')), create=True) as models:
            for identifier in ('heartmula', 'stable_audio3'):
                outcome = await install.install(context, identifier, True)
                self.assertEqual(outcome.state, 'verified')
                models.assert_awaited_with(context, identifier, True)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 4)
        heart, stable = environment.paths['heartmula'], environment.paths['stable_audio3']
        self.assertEqual(commands[0], ['/fixed/uv', 'venv', '--python', '3.11', str(heart / '.venv')])
        self.assertEqual(commands[1], ['/fixed/uv', 'pip', 'install', '--python', str(engine_python(heart, 'darwin')), '-e', str(heart)])
        self.assertEqual(commands[2], ['/fixed/uv', 'venv', '--python', '3.11', str(stable / '.venv')])
        self.assertEqual(commands[3], ['/fixed/uv', 'pip', 'install', '--python', str(engine_python(stable, 'darwin')), '-r', str(stable / 'optimized/mlx/requirements.txt')])
        self.assertTrue(all('download' not in argument and 'bootstrap' not in argument for command in commands for argument in command))

    async def test_existing_external_music_environment_finishes_automatic_models_without_source_changes(self) -> None:
        from app.module_catalog import DEFINITIONS
        engine = self.root / 'external-ace'
        for marker in DEFINITIONS['ace_step'].source_markers:
            path = engine / marker
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# preserved source')
        python = engine_python(engine, 'darwin')
        python.parent.mkdir(parents=True)
        python.write_text('# interpreter')
        environment = replace(self.environment, paths={**self.environment.paths, 'ace_step': engine})
        context = InstallContext(ModuleJobService(environment), 'd' * 32)
        original = sorted((str(path.relative_to(engine)), path.read_bytes()) for path in engine.rglob('*') if path.is_file())
        with patch.object(install, '_check_environment', AsyncMock()) as check, patch.object(install, 'setup_music_models', AsyncMock(return_value=InstallOutcome('verified', 'Existing models reused.')), create=True) as models:
            outcome = await install.verify(context, 'ace_step', download_models=True)
        self.assertEqual(outcome.state, 'verified')
        check.assert_awaited_once_with(context, 'ace_step', engine)
        models.assert_awaited_once_with(context, 'ace_step', True)
        self.assertEqual(original, sorted((str(path.relative_to(engine)), path.read_bytes()) for path in engine.rglob('*') if path.is_file()))

    async def test_explicit_reinstall_refreshes_only_owned_dependencies(self) -> None:
        from app.module_catalog import DEFINITIONS
        identifiers: tuple[install.ModuleId, ...] = ('ace_step', 'separation', 'speech', 'singing', 'heartmula', 'stable_audio3', 'kokoro', 'chatterbox', 'wan22', 'rvc')
        environment = replace(self.environment, uv='/fixed/uv')
        context = InstallContext(ModuleJobService(environment), 'e' * 32)
        for identifier in identifiers:
            with self.subTest(identifier=identifier):
                target = environment.paths[identifier]
                for marker in DEFINITIONS[identifier].source_markers:
                    path = target / marker
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('# preserved source')
                python = engine_python(target, 'darwin')
                python.parent.mkdir(parents=True)
                python.write_text('# existing interpreter')
                (target / 'checkpoint.bin').write_bytes(b'preserved weights')
                version = install.load_manifest().aceStep.commit if identifier == 'ace_step' else install.SOURCE_PINS[identifier].commit if identifier in install.SOURCE_PINS else hashlib.sha256(install.manifest_path().read_bytes()).hexdigest()
                install._record_owned(context, identifier, target, version)
                original = sorted((str(path.relative_to(target)), path.read_bytes()) for path in target.rglob('*') if path.is_file())
                with patch.object(context, 'run', AsyncMock()) as run, patch.object(install, '_clone', AsyncMock()) as clone, patch.object(install.shutil, 'rmtree') as delete, patch.object(install.shutil, 'which', return_value='/fixed/git'), patch.object(install, '_check_environment', AsyncMock()), patch.object(install, 'setup_music_models', AsyncMock(return_value=InstallOutcome('verified', 'Existing models reused.'))):
                    await install.install(context, identifier, False, reinstall=True)
                clone.assert_not_awaited()
                delete.assert_not_called()
                run.assert_awaited_once()
                command = run.call_args.args[0]
                prefix = ['/fixed/uv', 'sync'] if identifier in ('ace_step', 'separation') else ['/fixed/uv', 'pip', 'install']
                self.assertEqual(command[:len(prefix)], prefix)
                self.assertEqual(command.count('--reinstall'), 1)
                self.assertNotIn('--download-models', command)
                self.assertEqual(original, sorted((str(path.relative_to(target)), path.read_bytes()) for path in target.rglob('*') if path.is_file()))
                with patch.object(context, 'run', AsyncMock()) as normal, patch.object(install.shutil, 'which', return_value='/fixed/git'), patch.object(install, '_check_environment', AsyncMock()), patch.object(install, 'setup_music_models', AsyncMock(return_value=InstallOutcome('verified', 'Existing models reused.'))):
                    await install.install(context, identifier, False)
                normal.assert_awaited_once()
                self.assertNotIn('--reinstall', normal.call_args.args[0])

    async def test_reinstall_refuses_unowned_external_and_excluded_modules_without_changes(self) -> None:
        target = self.environment.paths['speech']
        target.mkdir(parents=True)
        (target / 'private.txt').write_text('keep')
        external = self.root / 'external-speech'
        external.mkdir()
        (external / 'private.txt').write_text('keep external')
        external_environment = replace(self.environment, paths={**self.environment.paths, 'speech': external})
        external_context = InstallContext(ModuleJobService(external_environment), 'f' * 32)
        cases: tuple[tuple[InstallContext, install.ModuleId], ...] = ((self.context, 'speech'), (self.context, 'heartmula'), (external_context, 'speech'), *((self.context, identifier) for identifier in ('media', 'yue2', 'video', 'speaker_review', 'transcription', 'source_import', 'ebooks')))
        with patch.object(InstallContext, 'run', AsyncMock()) as run, patch.object(install, '_clone', AsyncMock()) as clone, patch.object(install.shutil, 'rmtree') as delete:
            for context, identifier in cases:
                with self.subTest(identifier=identifier, external=context is external_context):
                    with self.assertRaisesRegex(ModuleSetupError, 'reinstall_unavailable'):
                        await install.install(context, identifier, False, reinstall=True)
        run.assert_not_awaited()
        clone.assert_not_awaited()
        delete.assert_not_called()
        self.assertEqual((target / 'private.txt').read_text(), 'keep')
        self.assertEqual((external / 'private.txt').read_text(), 'keep external')

    async def test_reinstall_refuses_modified_owned_source_and_unsupported_platform(self) -> None:
        target = self.environment.paths['heartmula']
        target.mkdir(parents=True)
        source = target / 'pyproject.toml'
        source.write_text('# pinned source')
        install._record_owned(self.context, 'heartmula', target, install.SOURCE_PINS['heartmula'].commit)
        source.write_text('# user modification')
        unsupported = replace(self.environment, platform='linux', architecture='x64')
        unsupported_context = InstallContext(ModuleJobService(unsupported), 'd' * 32)
        with patch.object(InstallContext, 'run', AsyncMock()) as run:
            with self.assertRaisesRegex(ModuleSetupError, 'reinstall_unavailable'):
                await install.install(self.context, 'heartmula', False, reinstall=True)
            with self.assertRaisesRegex(ModuleSetupError, 'unsupported_platform'):
                await install.install(unsupported_context, 'heartmula', False, reinstall=True)
        run.assert_not_awaited()
        self.assertEqual(source.read_text(), '# user modification')

    async def test_reinstall_refuses_symlinked_environment_without_running_commands(self) -> None:
        target = self.environment.paths['heartmula']
        target.mkdir(parents=True)
        outside = self.root / 'external-environment'
        outside.mkdir()
        (outside / 'private.txt').write_text('keep')
        (target / '.venv').symlink_to(outside, target_is_directory=True)
        with patch.object(install, '_owned', return_value=True), patch.object(self.context, 'run', AsyncMock()) as run:
            with self.assertRaisesRegex(ModuleSetupError, 'unsafe_install_path'):
                await install.install(self.context, 'heartmula', False, reinstall=True)
        run.assert_not_awaited()
        self.assertEqual((outside / 'private.txt').read_text(), 'keep')

    def test_reinstall_availability_checks_saved_ownership_without_writes(self) -> None:
        target = self.environment.paths['heartmula']
        target.mkdir(parents=True)
        source = target / 'pyproject.toml'
        source.write_text('# pinned source')
        version = install.SOURCE_PINS['heartmula'].commit
        install._record_owned(self.context, 'heartmula', target, version)
        def available(environment: ModuleEnvironment, identifier: install.ModuleId) -> bool:
            before = sorted((str(path.relative_to(self.root)), path.read_bytes(), path.stat().st_mtime_ns) for path in self.root.rglob('*') if path.is_file())
            result = install.reinstall_available(environment, identifier)
            after = sorted((str(path.relative_to(self.root)), path.read_bytes(), path.stat().st_mtime_ns) for path in self.root.rglob('*') if path.is_file())
            self.assertEqual(before, after)
            return result
        self.assertTrue(available(self.environment, 'heartmula'))
        self.assertFalse(available(self.environment, 'speech'))
        self.assertFalse(available(self.environment, 'media'))
        self.assertFalse(available(replace(self.environment, platform='linux', architecture='x64'), 'heartmula'))
        external = self.root / 'external-heart'
        self.assertFalse(available(replace(self.environment, paths={**self.environment.paths, 'heartmula': external}), 'heartmula'))
        source.write_text('# user source')
        self.assertFalse(available(self.environment, 'heartmula'))
        source.write_text('# pinned source')
        install._record_owned(self.context, 'heartmula', target, 'outdated-source-version')
        self.assertFalse(available(self.environment, 'heartmula'))
        install._record_owned(self.context, 'heartmula', target, version)
        outside = self.root / 'external-environment'
        outside.mkdir()
        (target / '.venv').symlink_to(outside, target_is_directory=True)
        self.assertFalse(available(self.environment, 'heartmula'))

    def test_reinstall_availability_bounds_source_reads_and_refuses_receipt_links(self) -> None:
        target = self.environment.paths['heartmula']
        target.mkdir(parents=True)
        source = target / 'pyproject.toml'
        source.write_text('# pinned source')
        install._record_owned(self.context, 'heartmula', target, install.SOURCE_PINS['heartmula'].commit)
        with source.open('ab') as output:
            output.truncate(9 * 1024**2)
        with patch.object(install, '_sha256', side_effect=AssertionError('oversized source must not be hashed')):
            self.assertFalse(install.reinstall_available(self.environment, 'heartmula'))
        receipt = install._owned_path(self.context, 'heartmula')
        outside = self.root / 'external-receipt.json'
        receipt.rename(outside)
        receipt.symlink_to(outside)
        self.assertFalse(install.reinstall_available(self.environment, 'heartmula'))

    def test_owned_media_binaries_are_not_limited_by_the_python_source_budget(self) -> None:
        target = self.environment.paths['media']
        (target / 'bin').mkdir(parents=True)
        for name in ('ffmpeg', 'ffprobe'):
            with (target / 'bin' / name).open('wb') as output:
                output.truncate(9 * 1024**2)
        version = hashlib.sha256(install.manifest_path().read_bytes()).hexdigest()
        install._record_owned(self.context, 'media', target, version)
        self.assertTrue(install._owned(self.context, 'media', target, version))

    async def test_failed_or_cancelled_reinstall_revokes_prior_environment_verification(self) -> None:
        from app.module_catalog import DEFINITIONS
        from app.module_evidence import VerificationReceipt
        environment = replace(self.environment, uv='/fixed/uv')
        context = InstallContext(ModuleJobService(environment), 'c' * 32)
        target = environment.paths['heartmula']
        for marker in DEFINITIONS['heartmula'].source_markers:
            path = target / marker
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# pinned source')
        python = engine_python(target, 'darwin')
        python.parent.mkdir(parents=True)
        python.write_text('# existing interpreter')
        (target / '.venv/lib/python3.11/site-packages').mkdir(parents=True)
        (target / 'checkpoint.bin').write_bytes(b'preserved model')
        install._record_owned(context, 'heartmula', target, install.SOURCE_PINS['heartmula'].commit)
        original = sorted((str(path.relative_to(target)), path.read_bytes()) for path in target.rglob('*') if path.is_file())
        for failure in (ModuleSetupError('installer_failed'), asyncio.CancelledError()):
            with self.subTest(failure=type(failure).__name__):
                with patch.object(context, 'run', AsyncMock()):
                    await install._check_environment(context, 'heartmula', target)
                self.assertTrue(environment_verified(environment, 'heartmula'))
                async def failed_command(argv: list[str], **kwargs: object) -> None:
                    self.assertIn('--reinstall', argv)
                    self.assertFalse(environment_verified(environment, 'heartmula'), 'revoke verification before package mutation starts')
                    raise failure
                with patch.object(context, 'run', side_effect=failed_command), patch.object(install.shutil, 'which', return_value='/fixed/git'):
                    with self.assertRaises(type(failure)):
                        await install.install(context, 'heartmula', False, reinstall=True)
                receipt = VerificationReceipt.model_validate_json((environment.root / '.setup/verification/heartmula.json').read_bytes())
                self.assertFalse(receipt.environment_verified)
                self.assertEqual(original, sorted((str(path.relative_to(target)), path.read_bytes()) for path in target.rglob('*') if path.is_file()))

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

    async def test_native_managed_models_use_the_reviewed_adapter_without_source_patches(self) -> None:
        target = self.environment.paths['yue2']
        (target / 'tools').mkdir(parents=True)
        source = target / 'tools/model_manager_v2.py'
        source.write_bytes(b'# preserved downloader')
        from app import module_native_setup
        with patch.object(module_native_setup, 'setup_native_models', AsyncMock(return_value=InstallOutcome('verified', 'Pinned model setup finished.'))) as models, patch.object(self.context, 'run', AsyncMock()) as run:
            outcome = await install._native_weights(self.context, target, install.load_manifest(), True)
        self.assertEqual(outcome.state, 'verified')
        models.assert_awaited_once_with(self.context, True)
        run.assert_not_awaited()
        self.assertEqual(source.read_bytes(), b'# preserved downloader')

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
        self.assertEqual(install.SOURCE_PINS['heartmula'].commit, '02dd2e22f6a1bf1a2f769a13f8af952a7a4e9cee')
        self.assertEqual(install.SOURCE_PINS['stable_audio3'].commit, '77e0398c26f7d22d801ec1bb036307e8b231f741')
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

    async def test_native_ownership_survives_interrupted_weight_download(self) -> None:
        from app import module_native_setup
        target = self.environment.paths['yue2']
        (target / 'tools').mkdir(parents=True)
        script = target / 'tools/model_manager_v2.py'
        script.write_text('# original')
        assets = install.load_manifest()
        install._record_owned(self.context, 'yue2', target, assets.engine.tag)
        with patch.object(module_native_setup, 'setup_native_models', AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await install._native_weights(self.context, target, assets, True)
        self.assertTrue(install._owned(self.context, 'yue2', target, assets.engine.tag))
        self.assertEqual(script.read_text(), '# original')


if __name__ == '__main__':
    unittest.main()
