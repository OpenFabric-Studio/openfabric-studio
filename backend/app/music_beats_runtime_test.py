from __future__ import annotations

import hashlib
import asyncio
import shutil
from pathlib import Path, PureWindowsPath
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app import music_beats_runtime as runtime
from app.music_beats import BeatAnalysisRecipe, BeatRuntimeIdentity
import numpy as np


class BeatRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.site = runtime.site_packages(self.root)
        self.site.mkdir(parents=True)
        self.python = runtime.python(self.root)
        self.python.parent.mkdir(); self.python.write_bytes(b'test interpreter')
        self.source = self.site / 'beat_this'; self.source.mkdir()
        (self.source / 'inference.py').write_bytes(b'fixture source')
        self.source_hash = runtime.digest({'inference.py': hashlib.sha256(b'fixture source').hexdigest()})
        self.enterContext(patch.object(runtime, 'SOURCE_SHA256', self.source_hash))
        self.enterContext(patch.object(runtime, 'SOURCE_NAMES', ('inference.py',)))
        for name, version in runtime.PACKAGES.items():
            metadata = self.site / (name.replace('-', '_') + '-' + version + '.dist-info/METADATA')
            metadata.parent.mkdir(); metadata.write_text('Name: ' + name + '\nVersion: ' + version + '\n')
        self.weights = b'opaque fixture checkpoint'
        self.enterContext(patch.object(runtime, 'WEIGHTS_SHA256', hashlib.sha256(self.weights).hexdigest()))
        self.enterContext(patch.object(runtime, 'WEIGHTS_BYTES', len(self.weights)))
        (self.root / 'weights').mkdir(); (self.root / 'weights/small0.ckpt').write_bytes(self.weights)
        identity = runtime.dependency_identity(self.root, '3.12.13')
        (self.root / runtime.RECEIPT_NAME).write_text(identity.model_dump_json())

    def test_read_only_identity_requires_actual_matching_checkpoint(self) -> None:
        identity = runtime.identity(self.root)
        self.assertEqual(identity.weights_sha256, hashlib.sha256(self.weights).hexdigest())
        (self.root / 'weights/small0.ckpt').unlink()
        with self.assertRaises(runtime.BeatRuntimeError) as error:
            runtime.identity(self.root)
        self.assertEqual(error.exception.code, 'analysis_weights_missing')
        self.assertEqual(runtime.identity(self.root, require_weights=False), identity)

    def test_changed_source_packages_python_or_checkpoint_fail_closed(self) -> None:
        for path in (self.source / 'inference.py', self.python, self.root / 'weights/small0.ckpt',
                     next(self.site.glob('numpy-*.dist-info/METADATA'))):
            previous = path.read_bytes()
            with self.subTest(path=path):
                path.write_bytes(previous.replace(b'Version: 2.2.6', b'Version: 0.0.1') if path.name == 'METADATA' else previous + b'changed')
                with self.assertRaises(runtime.BeatRuntimeError):
                    runtime.identity(self.root)
                path.write_bytes(previous)

    def test_windows_relative_source_names_match_the_portable_identity_manifest(self) -> None:
        nested = self.source / 'model/beat_tracker.py'; nested.parent.mkdir(); nested.write_bytes(b'nested fixture')
        names = ('inference.py', 'model/beat_tracker.py')
        expected = runtime.digest({name: hashlib.sha256((self.source / name).read_bytes()).hexdigest() for name in names})
        windows_source = PureWindowsPath(str(self.source))
        with patch.object(runtime, 'SOURCE_NAMES', names), patch.object(runtime, 'SOURCE_SHA256', expected), \
                patch.object(Path, 'rglob', return_value=iter([windows_source / name for name in names])):
            identity = runtime.dependency_identity(self.root, '3.12.13')
        self.assertEqual(identity.source_sha256, expected)

    def test_symlinked_weights_and_runtime_are_rejected(self) -> None:
        weight = self.root / 'weights/small0.ckpt'
        other = self.root / 'other.ckpt'; weight.rename(other); weight.symlink_to(other)
        with self.assertRaises(runtime.BeatRuntimeError):
            runtime.identity(self.root)
        weight.unlink(); other.rename(weight)
        link = self.root / 'linked'; link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(runtime.BeatRuntimeError):
            runtime.identity(link)

    def test_unverified_dependency_environment_is_not_adopted(self) -> None:
        (self.root / runtime.RECEIPT_NAME).unlink()
        with self.assertRaises(runtime.BeatRuntimeError):
            runtime.identity(self.root)

    def test_setup_reuses_verified_dependencies_without_install_or_download(self) -> None:
        from app.module_catalog import ModuleEnvironment
        from app.module_jobs import InstallContext, ModuleJobService
        context = InstallContext(ModuleJobService(ModuleEnvironment.for_root(self.root)), 'a' * 32)
        with patch.object(context, 'run', new=AsyncMock()) as run, \
                patch.object(context, 'update_detail'), patch('app.module_install.download_artifact', new=AsyncMock()) as download:
            outcome = asyncio.run(runtime.setup(context, self.root))
        self.assertEqual(outcome.state, 'manual')
        run.assert_not_awaited(); download.assert_not_awaited()

    def test_verified_setup_is_read_only_at_zero_free_space(self) -> None:
        from app.module_catalog import ModuleEnvironment
        from app.module_jobs import InstallContext, ModuleJobService
        context = InstallContext(ModuleJobService(ModuleEnvironment.for_root(self.root)), 'a' * 32)
        before = {path.relative_to(self.root): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.root.rglob('*') if path.is_file()}
        usage = shutil.disk_usage(self.root)._replace(free=0)
        with patch.object(runtime.shutil, 'disk_usage', return_value=usage), \
                patch.object(context, 'run', new=AsyncMock()) as run, patch.object(context, 'update_detail'), \
                patch('app.module_install.download_artifact', new=AsyncMock()) as download:
            for download_models, state in ((False, 'manual'), (True, 'verified')):
                with self.subTest(download_models=download_models):
                    self.assertEqual(asyncio.run(runtime.setup(context, self.root, download_models=download_models)).state, state)
        after = {path.relative_to(self.root): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(after, before)
        run.assert_not_awaited(); download.assert_not_awaited()

    def test_missing_checkpoint_checks_only_its_known_download_and_temporary_reserve(self) -> None:
        from app import module_install
        from app.module_catalog import ModuleEnvironment
        from app.module_jobs import InstallContext, ModuleJobService, ModuleSetupError
        context = InstallContext(ModuleJobService(ModuleEnvironment.for_root(self.root)), 'a' * 32)
        checkpoint = self.root / 'weights/small0.ckpt'; checkpoint.unlink()
        required = runtime.WEIGHTS_BYTES + runtime.WEIGHTS_BYTES // 2
        usage = shutil.disk_usage(self.root)

        async def downloaded(artifact: module_install.DownloadArtifact, destination: Path, owner: InstallContext) -> Path:
            self.assertEqual(artifact.bytes, len(self.weights))
            self.assertEqual(artifact.sha256, hashlib.sha256(self.weights).hexdigest())
            destination.write_bytes(self.weights)
            return destination

        with patch.object(context, 'run', new=AsyncMock()) as run, patch.object(context, 'update_detail'), \
                patch.object(module_install, 'download_artifact', new=AsyncMock(side_effect=downloaded)) as download:
            with patch.object(runtime.shutil, 'disk_usage', return_value=usage._replace(free=required - 1)):
                with self.assertRaises(ModuleSetupError) as error:
                    asyncio.run(runtime.setup(context, self.root, download_models=True))
            self.assertEqual(error.exception.code, 'insufficient_disk')
            self.assertFalse(checkpoint.exists()); download.assert_not_awaited()
            with patch.object(runtime.shutil, 'disk_usage', return_value=usage._replace(free=required)):
                self.assertEqual(asyncio.run(runtime.setup(context, self.root, download_models=True)).state, 'verified')
        self.assertEqual(checkpoint.read_bytes(), self.weights)
        run.assert_not_awaited(); download.assert_awaited_once()

    def test_unknown_dependency_install_still_requires_its_minimum_floor(self) -> None:
        from app.module_catalog import ModuleEnvironment
        from app.module_jobs import InstallContext, ModuleJobService, ModuleSetupError
        context = InstallContext(ModuleJobService(ModuleEnvironment.for_root(self.root)), 'a' * 32)
        target = self.root / 'fresh-low-disk'
        usage = shutil.disk_usage(self.root)._replace(free=2 * 1024**3 - 1)
        with patch.object(runtime.shutil, 'disk_usage', return_value=usage), \
                patch.object(context, 'run', new=AsyncMock()) as run, patch.object(context, 'update_detail'), \
                patch('app.module_install.download_artifact', new=AsyncMock()) as download:
            with self.assertRaises(ModuleSetupError) as error:
                asyncio.run(runtime.setup(context, target, download_models=True))
        self.assertEqual(error.exception.code, 'insufficient_disk')
        run.assert_not_awaited(); download.assert_not_awaited()

    def test_cancelled_install_has_resumable_marker_and_preserves_existing_foreign_root(self) -> None:
        from app.module_catalog import ModuleEnvironment
        from app.module_jobs import InstallContext, ModuleJobService, ModuleSetupError
        context = InstallContext(ModuleJobService(ModuleEnvironment.for_root(self.root)), 'a' * 32)
        target = self.root / 'fresh'
        with patch.object(context, 'run', new=AsyncMock(side_effect=asyncio.CancelledError())), patch.object(context, 'update_detail'):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(runtime.setup(context, target))
        runtime.initialize_marker(context, target)
        self.assertEqual(runtime.SetupMarker.model_validate_json((target / runtime.RECEIPT_NAME).read_bytes()), runtime.SetupMarker())
        foreign = self.root / 'foreign'; foreign.mkdir(); (foreign / 'mine.wav').write_bytes(b'preserved')
        with self.assertRaises(ModuleSetupError):
            runtime.initialize_marker(context, foreign)
        self.assertEqual((foreign / 'mine.wav').read_bytes(), b'preserved')

    def test_installer_ownership_survives_cancel_between_dependencies_and_model_download(self) -> None:
        from app import module_install
        from app.module_catalog import ModuleEnvironment
        from app.module_jobs import InstallContext, ModuleJobService
        environment = ModuleEnvironment.for_root(self.root)
        target = environment.paths['music_beats']
        context = InstallContext(ModuleJobService(environment), 'a' * 32)

        async def run(argv: list[str], **options: object) -> None:
            if 'venv' in argv:
                shutil.copytree(self.root / '.venv', target / '.venv')
                (target / '.venv/pyvenv.cfg').write_text('version = 3.12.13\n')
            if '-c' in argv:
                Path(argv[-1]).write_text('3.12.13')

        async def downloaded(artifact: module_install.DownloadArtifact, destination: Path, owner: InstallContext) -> Path:
            destination.write_bytes(self.weights)
            return destination

        with patch.object(context, 'run', side_effect=run), patch.object(context, 'update_detail'), \
                patch.object(module_install, 'download_artifact', new=AsyncMock(side_effect=asyncio.CancelledError())) as download:
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(module_install.install(context, 'music_beats', True))
            self.assertTrue(module_install._owned(context, 'music_beats', target, runtime.REVISION))
            download.side_effect = downloaded
            result = asyncio.run(module_install.install(context, 'music_beats', True))
            self.assertEqual(result.state, 'verified')
            self.assertTrue(module_install._owned(context, 'music_beats', target, runtime.REVISION))

    def test_changed_source_is_rejected_before_import_probe(self) -> None:
        from app.module_catalog import ModuleEnvironment
        from app.module_jobs import InstallContext, ModuleJobService
        context = InstallContext(ModuleJobService(ModuleEnvironment.for_root(self.root)), 'a' * 32)
        target = self.root / 'fresh-source'
        imported = False

        async def run(argv: list[str], **options: object) -> None:
            nonlocal imported
            if 'venv' in argv:
                shutil.copytree(self.root / '.venv', target / '.venv')
                (runtime.site_packages(target) / 'beat_this/inference.py').write_bytes(b'changed code')
            if '-c' in argv:
                imported = 'beat_this' in argv[argv.index('-c') + 1]
                if argv[-1].endswith('.txt'):
                    Path(argv[-1]).write_text('3.12.13')

        with patch.object(context, 'run', side_effect=run), patch.object(context, 'update_detail'):
            with self.assertRaises(runtime.BeatRuntimeError):
                asyncio.run(runtime.setup(context, target))
        self.assertFalse(imported)


class BeatInferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_child_failure_retains_its_private_input_for_owner_quarantine(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        identity = BeatRuntimeIdentity(source_sha256='a' * 64, weights_sha256='b' * 64,
            python_sha256='c' * 64, python_version='3.12.13', packages={str(n): '1' for n in range(8)})
        recipe = BeatAnalysisRecipe(runtime_root=str(root), identity=identity)
        with self.assertRaises(RuntimeError):
            await runtime.infer_window(np.zeros((22050, 1), dtype=np.float32), root, recipe,
                AsyncMock(side_effect=RuntimeError('unverified child drain')))
        self.assertTrue((root / 'beats-input.f32').is_file())
        self.assertTrue((root / 'beats-request.json').is_file())
