from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

from app import local_music_runtime as runtime
from app.local_music_contracts import LocalMusicError, LocalMusicRequest
from app.module_catalog import ModuleEnvironment
from scripts.local_music_worker import WorkerCommand, run as run_worker


class LocalMusicRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = ModuleEnvironment.for_root(self.root, platform='darwin', architecture='arm64')

    def weights(self, engine: str, variant: str = 'small-music') -> None:
        directory = self.env.paths['stable_audio3']
        names = ('optimized/mlx/models/mlx/t5gemma_f16.npz',
            'optimized/mlx/models/mlx/dit_sm-music_f16.npz' if variant == 'small-music' else 'optimized/mlx/models/mlx/dit_medium_f16.npz',
            'optimized/mlx/models/mlx/same_s_decoder_f32.npz' if variant == 'small-music' else 'optimized/mlx/models/mlx/same_l_decoder_f32.npz')
        for name in names:
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'local weights')

    def test_status_never_installs_or_downloads_and_requires_verified_environment(self) -> None:
        result = runtime.engine_status(self.env)
        self.assertEqual([item.id for item in result.engines], ['stable_audio3'])
        self.assertFalse(any(item.installed or item.weights_ready for item in result.engines))
        self.assertEqual(list(self.root.iterdir()), [])
        self.weights('stable_audio3')
        with patch.object(runtime, 'environment_verified', return_value=False):
            stable = runtime.engine_status(self.env).engines[0]
        self.assertTrue(stable.weights_ready)
        self.assertFalse(stable.installed)

    def test_selected_stable_variant_must_have_all_its_weights(self) -> None:
        self.weights('stable_audio3')
        request = LocalMusicRequest(engine='stable_audio3', prompt='piano', variant='medium', duration_seconds=30)
        with patch.object(runtime, 'configured_environment', return_value=self.env), patch.object(runtime, 'environment_verified', return_value=True):
            with self.assertRaises(LocalMusicError) as caught:
                runtime.prepare_worker(request, self.root / 'job/output.wav')
        self.assertEqual(caught.exception.code, 'local_music_weights_missing')

    def test_stable_status_reports_each_generation_variant_separately(self) -> None:
        self.weights('stable_audio3')
        with patch.object(runtime, 'environment_verified', return_value=True):
            status = runtime.engine_status(self.env).engines[0]
        self.assertTrue(status.weights_ready)
        self.assertTrue(status.model_dump().get('small_weights_ready'))
        self.assertFalse(status.model_dump().get('medium_weights_ready'))
        from app.stable_lora_runtime import training_weights
        for path in training_weights(self.env, 'medium'):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'training weights')
        with patch.object(runtime, 'environment_verified', return_value=True):
            self.assertFalse(runtime.engine_status(self.env).engines[0].model_dump().get('medium_weights_ready'))
        self.weights('stable_audio3', 'medium')
        with patch.object(runtime, 'environment_verified', return_value=True):
            self.assertTrue(runtime.engine_status(self.env).engines[0].model_dump().get('medium_weights_ready'))


    def test_command_uses_offline_env_real_cli_flags_and_keeps_text_out_of_process_arguments(self) -> None:
        self.weights('stable_audio3')
        output = self.root / 'job/output.wav'
        output.parent.mkdir()
        request = LocalMusicRequest(engine='stable_audio3', prompt='private song prompt', duration_seconds=30, seed=42)
        with patch.object(runtime, 'configured_environment', return_value=self.env), patch.object(runtime, 'environment_verified', return_value=True):
            argv, cwd, env = runtime.prepare_worker(request, output)
        self.assertNotIn(request.prompt, argv)
        self.assertEqual(env['HF_HUB_OFFLINE'], '1')
        self.assertEqual(env['TRANSFORMERS_OFFLINE'], '1')
        self.assertEqual(env.get('PYTHONUNBUFFERED'), '1')
        payload = json.loads((output.parent / 'request.json').read_text())
        self.assertIn('--dit', payload['argv'])
        self.assertIn('sm-music', payload['argv'])
        self.assertIn('--seed', payload['argv'])
        self.assertEqual(cwd, self.env.paths['stable_audio3'] / 'optimized/mlx')

    def test_stable_lora_resolves_only_managed_matching_variant(self) -> None:
        self.weights('stable_audio3')
        output = self.root / 'job/output.wav'; output.parent.mkdir()
        request = LocalMusicRequest(engine='stable_audio3', prompt='Piano', duration_seconds=30,
            adapter={'id': 'a' * 32, 'strength': 0.7})
        adapter = self.root / 'managed.safetensors'
        with patch.object(runtime, 'configured_environment', return_value=self.env), patch.object(runtime, 'environment_verified', return_value=True), patch('app.music_lora.resolve', return_value=adapter) as resolve:
            runtime.prepare_worker(request, output)
        resolve.assert_called_once_with(request.adapter, engine='stable_audio3', target='small-music')
        command = json.loads((output.parent / 'request.json').read_text())['argv']
        self.assertEqual(command[command.index('--lora') + 1:][-2:], [str(adapter), 'strength=0.7'])


    def test_worker_runs_native_cli_then_normalizes_pcm_without_model_packages(self) -> None:
        root = self.root / 'fake-engine'
        root.mkdir()
        script = root / 'generate.py'
        raw = self.root / 'raw.wav'
        output = self.root / 'output.wav'
        script.write_text('import sys, wave\nwith wave.open(sys.argv[1], "wb") as audio:\n audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000); audio.writeframes(b"\\0\\0" * 800)\n')
        request = self.root / 'request.json'
        request.write_text(json.dumps({'argv': [str(script), str(raw)], 'cwd': str(root), 'raw_output': str(raw), 'output': str(output), 'ffmpeg': runtime.tool('ffmpeg'), 'seed': None}))
        worker = Path(__file__).resolve().parents[1] / 'scripts/local_music_worker.py'
        result = subprocess.run([sys.executable, str(worker), '--request', str(request)], capture_output=True, text=True, env=os.environ.copy())
        self.assertEqual(result.returncode, 0, result.stderr)
        with wave.open(str(output)) as audio:
            self.assertEqual(audio.getsampwidth(), 2)
            self.assertEqual(audio.getnframes(), 800)
        self.assertFalse(raw.exists())

    def test_worker_does_not_follow_a_normalization_symlink(self) -> None:
        root = self.root / 'fake-engine'
        root.mkdir()
        script = root / 'generate.py'
        raw = self.root / 'raw.wav'
        output = self.root / 'output.wav'
        original = self.root / 'original.wav'
        original.write_bytes(b'keep this file')
        (self.root / 'output.partial.wav').symlink_to(original)
        script.write_text('import sys, wave\nwith wave.open(sys.argv[1], "wb") as audio:\n audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000); audio.writeframes(b"\\0\\0" * 800)\n')
        request = self.root / 'request.json'
        request.write_text(json.dumps({'argv': [str(script), str(raw)], 'cwd': str(root), 'raw_output': str(raw), 'output': str(output), 'ffmpeg': runtime.tool('ffmpeg'), 'seed': None}))
        worker = Path(__file__).resolve().parents[1] / 'scripts/local_music_worker.py'
        result = subprocess.run([sys.executable, str(worker), '--request', str(request)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        self.assertEqual(original.read_bytes(), b'keep this file')

    def test_completed_audio_survives_a_raw_cleanup_error(self) -> None:
        root = self.root / 'fake-engine'
        root.mkdir()
        script = root / 'generate.py'
        raw = self.root / 'raw.wav'
        output = self.root / 'output.wav'
        script.write_text('import sys, wave\nwith wave.open(sys.argv[1], "wb") as audio:\n audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000); audio.writeframes(b"\\0\\0" * 800)\n')
        command = WorkerCommand([str(script), str(raw)], root, raw, output, runtime.tool('ffmpeg'))
        original_cwd, original_argv, original_path = Path.cwd(), sys.argv.copy(), sys.path.copy()
        try:
            with patch.object(Path, 'unlink', side_effect=PermissionError('raw file locked')):
                run_worker(command)
            self.assertTrue(output.is_file())
        finally:
            os.chdir(original_cwd)
            sys.argv = original_argv
            sys.path[:] = original_path

    def test_worker_rejects_malformed_or_unbounded_launch_spec(self) -> None:
        worker = Path(__file__).resolve().parents[1] / 'scripts/local_music_worker.py'
        request = self.root / 'request.json'
        request.write_text('{"argv": [123], "seed": true}')
        result = subprocess.run([sys.executable, str(worker), '--request', str(request)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'output.wav').exists())
