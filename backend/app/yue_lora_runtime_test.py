"""Headless YuE2 commands stay fixed, local and separate from user settings."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import yue_lora_runtime as runtime
from app.music_training_contracts import MusicLoraTrainingRequest


class YueLoraRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.run_dir = self.root / ('a' * 32)
        self.run_dir.mkdir()
        trainer, comfy = self.root / 'trainer', self.root / 'comfy'
        trainer.mkdir(); (comfy / 'comfy/ldm/yue2').mkdir(parents=True)
        (trainer / 'train_cli.py').write_text('')
        (comfy / 'comfy/ldm/yue2/model.py').write_text('')
        base, python, ffmpeg = self.root / 'base.safetensors', self.root / 'python', self.root / 'ffmpeg'
        for file in (base, python, ffmpeg):
            file.write_bytes(b'installed')
        self.environment = runtime.YueLoraEnvironment(python, trainer, comfy, base, ffmpeg, 'mps')
        self.source = self.root / 'source.wav'; self.source.write_bytes(b'original')
        self.request = MusicLoraTrainingRequest(engine='yue2', name='Style', samples=[{'track_id': 1, 'caption': 'Warm piano'}], max_steps=2)

    def test_backend_manifest_uses_typed_settings_and_refuses_overwrite(self) -> None:
        argv, cwd, env = runtime.prepare_worker(self.request, self.run_dir, [self.source], self.environment)
        self.assertEqual(cwd, self.run_dir)
        self.assertEqual(argv[0], str(self.environment.python))
        self.assertTrue(argv[1].endswith('scripts/yue_lora_worker.py'))
        self.assertEqual(argv[-2:], ['--request', str(self.run_dir / 'request.json')])
        self.assertEqual(env['HF_HUB_OFFLINE'], '1')
        manifest = json.loads((self.run_dir / 'request.json').read_bytes())
        self.assertEqual(manifest['request']['engine'], 'yue2')
        self.assertEqual(manifest['sources'], [str(self.source)])
        self.assertNotIn('argv', manifest)
        with self.assertRaises(FileExistsError):
            runtime.prepare_worker(self.request, self.run_dir, [self.source], self.environment)

    def test_fixed_cli_disables_unsupported_auxiliary_and_head_training(self) -> None:
        argv = runtime.training_argv(self.request, self.run_dir, self.environment)
        self.assertEqual(argv[argv.index('--conditioning') + 1], 'compact')
        self.assertEqual(argv[argv.index('--prefix-mode') + 1], 'off')
        self.assertEqual(argv[argv.index('--devices') + 1], 'mps')
        self.assertEqual(argv[argv.index('--alpha') + 1], str(self.request.rank))
        for unsafe in ('--use-semantic', '--train-acoustic-head', '--semantic-head', '--existing-lora', '--transcribe'):
            self.assertNotIn(unsafe, argv)
        self.assertEqual(argv[argv.index('--out') + 1], str(self.run_dir / 'trainer.safetensors'))

    def test_rejects_missing_runtime_wrong_engine_and_mismatched_sources(self) -> None:
        (self.environment.trainer_root / 'train_cli.py').unlink()
        self.assertFalse(runtime.runtime_ready(self.environment))
        with self.assertRaises(ValueError):
            runtime.prepare_worker(self.request, self.run_dir, [self.source], self.environment)
        (self.environment.trainer_root / 'train_cli.py').write_text('')
        with self.assertRaises(ValueError):
            runtime.prepare_worker(self.request, self.run_dir, [], self.environment)
        other = self.request.model_copy(update={'engine': 'heartmula'})
        with self.assertRaises(ValueError):
            runtime.training_argv(other, self.run_dir, self.environment)

    def test_default_paths_and_readiness_use_configured_managed_root(self) -> None:
        from app.module_catalog import ModuleEnvironment
        managed = ModuleEnvironment.for_root(self.root / 'configured', platform='darwin', architecture='arm64', data_dir=self.root / 'library')
        actual = runtime.default_environment(managed)
        self.assertEqual(actual.trainer_root, managed.root / 'training/yue2/trainer')
        self.assertTrue(actual.base_checkpoint.is_relative_to(managed.root / 'training-assets/yue2'))
        with patch('app.music_training_setup.environment_ready', return_value=True), patch('app.music_training_setup.weights_ready', return_value=False):
            status = runtime.status(managed)
        self.assertTrue(status.supported)
        self.assertTrue(status.installed)
        self.assertFalse(status.weights_ready)
        self.assertEqual(status.validation, 'experimental')
