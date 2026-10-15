from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import heart_lora_runtime
from app.local_music_contracts import LocalMusicRequest
from app.module_catalog import ModuleEnvironment
from app.music_lora_contracts import MusicLoraError
from app.music_training_contracts import MusicLoraTrainingRequest


class HeartLoraRuntimeTests(unittest.TestCase):
    def test_worker_plan_uses_isolated_runtime_and_explicit_readonly_components(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            env = ModuleEnvironment.for_root(root, platform='darwin', architecture='arm64')
            run = root / ('1' * 32);run.mkdir()
            source = root / 'source.wav';source.write_bytes(b'audio')
            base, decoder, tokenizer = root / 'original-base', root / 'original-codec', root / 'tokenizer.json'
            request = MusicLoraTrainingRequest(engine='heartmula', name='test', samples=[{'track_id': 1, 'caption': 'synth'}])
            with patch.object(heart_lora_runtime, 'configured_environment', return_value=env), \
                 patch('app.music_training_setup.environment_ready', return_value=True), \
                 patch('app.music_training_setup.weights_ready', return_value=True), \
                 patch('app.music_training_setup.heart_model_paths', return_value=(base, decoder, tokenizer)):
                argv, cwd, child_env = heart_lora_runtime.prepare_worker(request, run, [source])
            self.assertEqual(cwd, run)
            self.assertEqual(argv[0], str(root / 'training/heartmula/trainer-venv/bin/python'))
            self.assertEqual(child_env['HF_HUB_OFFLINE'], '1')
            payload = json.loads((run / 'request.json').read_text())
            self.assertEqual(payload['runtime']['base_path'], str(base))
            self.assertEqual(payload['runtime']['codec_python'], str(root / 'training/heartmula/codec-venv/bin/python'))
            self.assertEqual(payload['request_sha256'], heart_lora_runtime.fingerprint(request))
            self.assertEqual(payload['sources'], [str(source)])

    def test_generation_plan_passes_adapter_without_mlx_seed_or_generation_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            env = ModuleEnvironment.for_root(root, platform='darwin', architecture='arm64')
            run = root / ('2' * 32);run.mkdir()
            raw = run / 'raw.wav'
            request = LocalMusicRequest(engine='heartmula', prompt='synth', duration_seconds=5)
            with patch.object(heart_lora_runtime, 'configured_environment', return_value=env), \
                 patch('app.music_training_setup.environment_ready', return_value=True), \
                 patch('app.music_training_setup.weights_ready', return_value=True), \
                 patch('app.music_training_setup.heart_model_paths', return_value=(root / 'base', root / 'codec', root / 'tokenizer')):
                command, python = heart_lora_runtime.generation_command(request, root / 'adapter.safetensors', raw)
            self.assertEqual(command[1:3], ['--mode', 'generate'])
            self.assertEqual(python, root / 'training/heartmula/trainer-venv/bin/python')
            payload = json.loads((run / 'inference.json').read_text())
            self.assertEqual(payload['adapter_path'], str(root / 'adapter.safetensors'))
            self.assertEqual(payload['output_path'], str(raw))
            self.assertEqual(payload['runtime']['device'], 'mps')
            self.assertFalse(raw.exists())

    def test_missing_environment_prevents_request_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            env = ModuleEnvironment.for_root(root, platform='darwin', architecture='arm64')
            request = MusicLoraTrainingRequest(engine='heartmula', name='test', samples=[{'track_id': 1, 'caption': 'synth'}])
            with patch.object(heart_lora_runtime, 'configured_environment', return_value=env), \
                 patch('app.music_training_setup.environment_ready', return_value=False), \
                 patch('app.music_training_setup.weights_ready', return_value=False), self.assertRaises(MusicLoraError):
                heart_lora_runtime.prepare_worker(request, root, [root / 'source.wav'])
            self.assertFalse((root / 'request.json').exists())
