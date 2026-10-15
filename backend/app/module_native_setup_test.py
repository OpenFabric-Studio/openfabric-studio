from __future__ import annotations

import asyncio
from collections.abc import Mapping
import errno
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from app import module_native_setup as native
from app import module_install
from app.module_catalog import ModuleEnvironment
from app.module_jobs import InstallContext, ModuleJobService, ModuleSetupError


class NativeSetupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.engine = self.root / 'external-engine'
        self.environment = ModuleEnvironment.for_root(self.root / 'managed', platform='darwin',
            architecture='arm64', paths={'yue2': self.engine})
        self.context = InstallContext(ModuleJobService(self.environment), 'a' * 32)
        binary = self.engine / 'build/macos-metal-release/bin/audiocpp_server'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'native executable')
        binary.chmod(0o755)
        manager = self.engine / 'tools/model_manager_v2.py'
        manager.parent.mkdir()
        manager.write_text('# external customized downloader must remain untouched\n')

    def write_model(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == '.gguf':
            path.write_bytes(struct.pack('<4sIQQ', b'GGUF', 3, 1, 1) + b'payload')
        elif path.suffix == '.json':
            path.write_text('{"model_type":"test"}')
        else:
            path.write_text('IQ== 0\nIg== 1\n')

    def complete(self) -> None:
        for relative in native.REQUIRED_FILES:
            self.write_model(self.engine / 'models' / relative)

    async def test_complete_external_native_setup_reuses_models_without_writes(self) -> None:
        self.complete()
        before = {path: path.read_bytes() for path in self.engine.rglob('*') if path.is_file()}
        with patch.object(self.context, 'run', AsyncMock()) as run:
            result = await native.setup_native_models(self.context, True)
        self.assertEqual(result.state, 'verified')
        run.assert_not_awaited()
        self.assertTrue(native.native_environment_present(self.environment))
        self.assertTrue(native.native_setup_present(self.environment))
        self.assertEqual(before, {path: path.read_bytes() for path in self.engine.rglob('*') if path.is_file()})

    async def test_missing_models_without_download_selection_are_actionable(self) -> None:
        with patch.object(self.context, 'run', AsyncMock()) as run:
            result = await native.setup_native_models(self.context, False)
        self.assertEqual(result.state, 'manual')
        self.assertIn('model downloads', result.detail.lower())
        self.assertTrue(native.native_environment_present(self.environment))
        self.assertFalse(native.native_setup_present(self.environment))
        run.assert_not_awaited()

    async def test_malformed_existing_models_are_preserved_as_a_conflict(self) -> None:
        self.complete()
        damaged = self.engine / 'models/Yue2-3B-GGUF/yue2-3b-q8_0.gguf'
        damaged.write_bytes(b'<html>not a GGUF model</html>')
        with patch.object(self.context, 'run', AsyncMock()) as run:
            result = await native.setup_native_models(self.context, True)
        self.assertEqual(result.state, 'manual')
        self.assertIn('preserved', result.detail)
        self.assertFalse(native.native_setup_present(self.environment))
        self.assertEqual(damaged.read_bytes(), b'<html>not a GGUF model</html>')
        run.assert_not_awaited()

    async def test_models_directory_symlink_escape_is_rejected_before_writes(self) -> None:
        outside = self.root / 'unrelated'
        outside.mkdir()
        (self.engine / 'models').symlink_to(outside, target_is_directory=True)
        with patch.object(self.context, 'run', AsyncMock()) as run:
            with self.assertRaises(ModuleSetupError) as caught:
                await native.setup_native_models(self.context, True)
        self.assertEqual(caught.exception.code, 'unsafe_install_path')
        self.assertFalse(native.native_setup_present(self.environment))
        self.assertEqual(list(outside.iterdir()), [])
        run.assert_not_awaited()

    async def test_missing_models_use_reviewed_owned_downloader_and_preserve_existing_files(self) -> None:
        existing = self.engine / 'models/Yue2-3B-GGUF/sidecars/yue2-model-config.json'
        self.write_model(existing)
        original_manager = (self.engine / 'tools/model_manager_v2.py').read_bytes()
        original = existing.read_bytes()
        downloads: list[list[str]] = []

        async def run(argv: list[str], *, cwd: Path | None = None,
                      env: Mapping[str, str] | None = None, timeout: float = 3600,
                      download_progress: bool = False) -> None:
            if argv[0] == 'git':
                subprocess.run(argv, cwd=cwd, check=True, capture_output=True)
            elif '--publish' in argv:
                native.publish_models(Path(argv[-2]), Path(argv[-1]))
            else:
                downloads.append(argv)
                self.assertIsNotNone(env)
                self.assertEqual(env['HF_ENDPOINT'] if env is not None else '', 'https://huggingface.co')
                specs = Path(argv[argv.index('--specs-dir') + 1])
                models = Path(argv[argv.index('--models-root') + 1])
                document: object = json.loads((specs / 'required.json').read_text())
                self.assertIsInstance(document, dict)
                self.assertNotIn('"revision": "main"', (specs / 'required.json').read_text())
                for revision in native.MODEL_REVISIONS.values():
                    self.assertIn(revision, (specs / 'required.json').read_text())
                package = argv[argv.index('install') + 1]
                for definition in native.PACKAGES:
                    if definition.identifier == package:
                        for relative in definition.files:
                            target = models / definition.directory / relative
                            if not (self.engine / 'models' / definition.directory / relative).exists():
                                self.write_model(target)

        with patch.object(self.context, 'run', side_effect=run), patch.object(self.context, 'update_detail'):
            result = await native.setup_native_models(self.context, True)
        self.assertEqual(result.state, 'verified')
        self.assertEqual(len(downloads), 5)
        self.assertTrue(native.native_setup_present(self.environment))
        self.assertEqual(existing.read_bytes(), original)
        self.assertEqual((self.engine / 'tools/model_manager_v2.py').read_bytes(), original_manager)
        for argv in downloads:
            self.assertNotIn('--overwrite', argv)
            self.assertTrue(Path(argv[argv.index('--models-root') + 1]).is_relative_to(self.context.workspace))
            self.assertEqual(argv[1], '-I')
            script = Path(argv[2])
            self.assertTrue(script.is_relative_to(self.context.workspace))
            self.assertIn('DOWNLOAD_ATTEMPTS = 3', script.read_text())

    def test_publication_never_overwrites_an_existing_model(self) -> None:
        source = self.root / 'staging'
        relative = Path(native.REQUIRED_FILES[0])
        self.write_model(source / relative)
        destination = self.engine / 'models'
        self.write_model(destination / relative)
        (destination / relative).write_bytes(b'preserve original')
        with self.assertRaises(ModuleSetupError) as caught:
            native.publish_models(source, destination)
        self.assertEqual(caught.exception.code, 'installation_conflict')
        self.assertEqual((destination / relative).read_bytes(), b'preserve original')

    def test_publication_can_resume_after_a_previous_file_was_published(self) -> None:
        source = self.root / 'staging'
        relative = Path(native.REQUIRED_FILES[0])
        self.write_model(source / relative)
        destination = self.engine / 'models'
        native.publish_models(source, destination)
        original = (destination / relative).stat()
        native.publish_models(source, destination)
        self.assertEqual((destination / relative).stat().st_ino, original.st_ino)

    def test_cross_volume_publication_copies_then_links_and_cleans_temporary_files(self) -> None:
        source = self.root / 'staging'
        relative = Path(native.REQUIRED_FILES[0])
        self.write_model(source / relative)
        destination = self.engine / 'models'
        link = os.link
        def cross_volume(first: Path, second: Path) -> None:
            if first == source / relative:
                raise OSError(errno.EXDEV, 'different volume')
            link(first, second)
        with patch.object(native.os, 'link', side_effect=cross_volume):
            native.publish_models(source, destination)
        self.assertEqual((source / relative).read_bytes(), (destination / relative).read_bytes())
        self.assertEqual(list(destination.rglob('*.partial')), [])
        native.publish_models(source, destination)

    async def test_cancellation_drains_publication_before_releasing_setup(self) -> None:
        models = self.context.workspace / 'native-model-setup/models'
        for relative in native.REQUIRED_FILES:
            self.write_model(models / relative)
        entered, release = threading.Event(), threading.Event()
        publish = native.publish_models
        def blocked(source: Path, destination: Path) -> None:
            entered.set()
            release.wait(5)
            publish(source, destination)
        with patch.object(native, '_prepare_downloader', AsyncMock(return_value=self.context.workspace / 'manager.py')), patch.object(self.context, 'run', AsyncMock()), patch.object(self.context, 'update_detail'), patch.object(native, 'publish_models', side_effect=blocked):
            task = asyncio.create_task(native.setup_native_models(self.context, True))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(native.native_setup_present(self.environment))

    def test_approval_manifest_changes_when_a_reviewed_revision_changes(self) -> None:
        original = native.native_model_manifest_hash()
        with patch.dict(native.MODEL_REVISIONS, {'audio-cpp/Yue2-3B-GGUF': '0' * 40}):
            self.assertNotEqual(native.native_model_manifest_hash(), original)

    def test_invalid_sidecars_tokenizer_and_gguf_headers_are_not_ready(self) -> None:
        self.complete()
        cases = (
            ('Yue2-3B-GGUF/sidecars/yue2-model-config.json', b'{}'),
            ('Yue2-3B-GGUF/sidecars/yue2-generation-config.json', b'<html>failed</html>'),
            ('Yue2-3B-GGUF/sidecars/yue2-qwen.tiktoken', b'not-a-token not-a-rank\n'),
            ('Yue2-3B-GGUF/yue2-vae-f16.gguf', struct.pack('<4sIQQ', b'GGUF', 1, 1, 1)),
        )
        for relative, content in cases:
            with self.subTest(relative=relative):
                path = self.engine / 'models' / relative
                previous = path.read_bytes()
                path.write_bytes(content)
                self.assertFalse(native.native_setup_present(self.environment))
                path.write_bytes(previous)

    async def test_packaged_setup_fetches_only_pinned_reviewed_downloader_when_needed(self) -> None:
        fixture = Path(__file__).resolve().parents[2] / 'desktop/test/fixtures/model_manager_v2.py'
        staging = self.context.workspace / 'native-model-setup'
        staging.mkdir(parents=True)
        async def download(artifact: module_install.DownloadArtifact, destination: Path,
                           context: InstallContext, *, on_progress: object) -> Path:
            self.assertEqual(artifact.sha256, native._DOWNLOADER_SHA256)
            self.assertEqual(artifact.bytes, fixture.stat().st_size)
            self.assertIn('39f9013463053e206aa160f8453d734f78999b9d', artifact.url)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(fixture.read_bytes())
            return destination
        with patch.object(native, '__file__', str(self.root / 'packaged/backend/app/module_native_setup.py')), patch.object(module_install, 'download_artifact', side_effect=download) as downloaded, patch.object(self.context, 'run', AsyncMock()):
            script = await native._prepare_downloader(self.context, staging)
        downloaded.assert_awaited_once()
        self.assertEqual(script.read_bytes(), fixture.read_bytes())

    def test_non_executable_binary_is_not_an_environment(self) -> None:
        (self.engine / 'build/macos-metal-release/bin/audiocpp_server').chmod(0o644)
        self.assertFalse(native.native_environment_present(self.environment))
