from __future__ import annotations

import asyncio
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
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
