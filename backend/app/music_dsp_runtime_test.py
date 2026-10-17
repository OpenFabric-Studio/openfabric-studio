from __future__ import annotations

import asyncio
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app import music_dsp_runtime as runtime
from app.module_catalog import ModuleEnvironment
from app.module_install import DownloadArtifact
from app.module_jobs import InstallContext, ModuleJobService, ModuleSetupError


class MusicDspRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.environment = ModuleEnvironment.for_root(self.root, platform='darwin', architecture='arm64')
        self.context = InstallContext(ModuleJobService(self.environment), 'a' * 32)
        self.target = self.environment.paths['music_dsp']
        self.enterContext(patch.object(InstallContext, 'update_detail'))
        self.run = self.enterContext(patch.object(InstallContext, 'run', new_callable=AsyncMock))
        self.executable = b'#!/bin/sh\nprintf 4.0.0\n'
        self.source = b'synthetic corresponding source fixture'
        self.archive = self.make_archive()
        self.enterContext(patch.object(runtime, 'BINARY_SHA256', hashlib.sha256(self.executable).hexdigest()))
        self.enterContext(patch.object(runtime, 'BINARY_ARCHIVE', DownloadArtifact(url='https://example.test/binary.tar.bz2', bytes=len(self.archive), sha256=hashlib.sha256(self.archive).hexdigest())))
        self.enterContext(patch.object(runtime, 'SOURCE_ARCHIVE', DownloadArtifact(url='https://example.test/source.tar.bz2', bytes=len(self.source), sha256=hashlib.sha256(self.source).hexdigest())))
        self.enterContext(patch('app.module_install.download_artifact', side_effect=self.download))

    def make_archive(self, *, unsafe: bool = False) -> bytes:
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:bz2') as archive:
            for name, data in (('rubberband', self.executable), ('COPYING', b'GPL fixture'), ('README.md', b'Test fixture'), ('CHANGELOG', b'4.0.0')):
                member = tarfile.TarInfo('rubberband-4.0.0-gpl-executable-macos/' + name)
                member.size = len(data); member.mode = 0o755 if name == 'rubberband' else 0o644
                archive.addfile(member, io.BytesIO(data))
            if unsafe:
                member = tarfile.TarInfo('link'); member.type = tarfile.SYMTYPE; member.linkname = '/outside'
                archive.addfile(member)
        return stream.getvalue()

    async def download(self, artifact: DownloadArtifact, destination: Path, context: InstallContext) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.source if 'source' in artifact.url else self.archive)
        return destination

    async def test_installs_only_reviewed_files_and_detects_modified_binary_or_notice(self) -> None:
        await runtime.setup(self.context, self.target)
        status = runtime.status(self.environment)
        self.assertTrue(status.ready)
        self.assertEqual((self.target / 'bin/rubberband').read_bytes(), self.executable)
        self.assertEqual((self.target / 'rubberband-4.0.0-source.tar.bz2').read_bytes(), self.source)
        self.assertIsNotNone(runtime.native(self.environment))
        (self.target / 'COPYING').write_bytes(b'changed')
        self.assertFalse(runtime.status(self.environment).ready)
        self.assertIsNone(runtime.native(self.environment))
        self.assertFalse((self.root / 'global-bin').exists())

    async def test_existing_destination_is_preserved_and_not_replaced(self) -> None:
        self.target.mkdir(parents=True); (self.target / 'user.wav').write_bytes(b'keep')
        with self.assertRaises(ModuleSetupError):
            await runtime.setup(self.context, self.target)
        self.assertEqual((self.target / 'user.wav').read_bytes(), b'keep')
        self.run.assert_not_awaited()

    async def test_archive_symlink_and_changed_source_refuse_promotion(self) -> None:
        for unsafe in (True, False):
            with self.subTest(unsafe=unsafe):
                self.archive = self.make_archive(unsafe=unsafe)
                artifact = DownloadArtifact(url='https://example.test/binary.tar.bz2', bytes=len(self.archive), sha256=hashlib.sha256(self.archive).hexdigest())
                with patch.object(runtime, 'BINARY_ARCHIVE', artifact):
                    if not unsafe:
                        self.source = b'changed source'
                    with self.assertRaises(ModuleSetupError):
                        await runtime.setup(self.context, self.target)
                self.assertFalse(self.target.exists())

    async def test_cancelled_probe_retains_private_stage_without_publishing(self) -> None:
        self.run.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await runtime.setup(self.context, self.target)
        self.assertFalse(self.target.exists())

    async def test_other_platforms_and_symlinked_runtime_are_unavailable(self) -> None:
        linux = ModuleEnvironment.for_root(self.root, platform='linux', architecture='x86_64')
        self.assertFalse(runtime.status(linux).ready)
        with self.assertRaises(ModuleSetupError) as error:
            await runtime.setup(InstallContext(ModuleJobService(linux), 'b' * 32), linux.paths['music_dsp'])
        self.assertEqual(error.exception.code, 'unsupported_platform')
        await runtime.setup(self.context, self.target)
        retained = self.target.with_name('retained'); self.target.rename(retained); self.target.symlink_to(retained, target_is_directory=True)
        self.assertFalse(runtime.status(self.environment).ready)
        self.assertEqual((retained / 'bin/rubberband').read_bytes(), self.executable)
