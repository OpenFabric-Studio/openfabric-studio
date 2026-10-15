from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import hashlib
import subprocess
import sys
import wave

from app import stable_lora_runtime as runtime
from app.music_lora_contracts import MusicLoraError
from app.stable_lora_contracts import StableLoraSample, StableLoraTrainingRequest
from app.module_catalog import ModuleEnvironment
from app.stable_lora_test import checkpoint_bytes


class StableLoraRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = ModuleEnvironment.for_root(self.root / 'engine', platform='darwin', architecture='arm64')

    def weights(self, variant: str) -> None:
        for path in runtime.training_weights(self.environment, variant):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'local weights')

    def test_status_requires_training_base_and_matching_encoder_not_arc_files(self) -> None:
        with patch.object(runtime, 'configured_environment', return_value=self.environment), patch.object(runtime, 'environment_verified', return_value=True):
            status = runtime.status()
            self.assertFalse(status.small_weights_ready)
            self.assertFalse(status.medium_weights_ready)
            self.weights('small-music')
            status = runtime.status()
            self.assertTrue(status.small_weights_ready)
            self.assertFalse(status.medium_weights_ready)

    def test_worker_argv_is_native_offline_bounded_and_does_not_resume(self) -> None:
        self.weights('small-music')
        run = self.root / ('a' * 32)
        run.mkdir()
        source = self.root / 'source.wav'
        source.write_bytes(b'fixture')
        request = StableLoraTrainingRequest(name='../unsafe user label', samples=[StableLoraSample(track_id=1, caption='Exact private caption')], max_steps=7)
        with patch.object(runtime, 'configured_environment', return_value=self.environment), patch.object(runtime, 'environment_verified', return_value=True):
            argv, cwd, env = runtime.prepare_worker(request, run, [source])
        payload = json.loads((run / 'request.json').read_text())
        train = payload['train_argv']
        self.assertIn('--adapter-type', train)
        self.assertIn('lora', train)
        self.assertIn('--grad-checkpoint', train)
        self.assertIn('--no-conditioner-lora', train)
        self.assertNotIn('--lora-ckpt-path', train)
        self.assertNotIn(request.name, train)
        self.assertIn('dit_sm-music-base_f16.npz', ' '.join(train))
        self.assertNotIn('dit_sm-music_f16.npz', ' '.join(train))
        self.assertNotIn('Exact private caption', argv)
        self.assertEqual(cwd, run)
        self.assertEqual(env['HF_HUB_OFFLINE'], '1')
        self.assertEqual(payload['sources'], [str(source)])

    def test_missing_selected_variant_refused_without_launch_or_download(self) -> None:
        self.weights('small-music')
        request = StableLoraTrainingRequest(name='Test', variant='medium', samples=[StableLoraSample(track_id=1, caption='Test')])
        with patch.object(runtime, 'configured_environment', return_value=self.environment), patch.object(runtime, 'environment_verified', return_value=True):
            with self.assertRaises(MusicLoraError):
                runtime.prepare_worker(request, self.root / ('a' * 32), [self.root / 'source.wav'])
        self.assertFalse((self.root / ('a' * 32)).exists())

    def worker_fixture(self) -> tuple[Path, Path, bytes]:
        run = (self.root / ('b' * 32)).resolve()
        run.mkdir()
        source = self.root / 'original.wav'
        with wave.open(str(source), 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b'\0\0' * 1600)
        original = source.read_bytes()
        encode = self.root / 'encode.py'
        encode.write_text('import json,sys\nfrom pathlib import Path\n'
            'source=Path(sys.argv[1]);output=Path(sys.argv[2]);output.mkdir()\n'
            'for path in source.glob("[0-9][0-9][0-9].wav"):\n'
            ' (output/path.with_suffix(".npy").name).write_bytes(b"x"*256)\n'
            ' (output/path.with_suffix(".json").name).write_text(path.with_suffix(".json").read_text())\n')
        train = self.root / 'train.py'
        train.write_text('from pathlib import Path\n'
            'root=Path("checkpoints/training/abcdef01/checkpoints");root.mkdir(parents=True)\n'
            f'(root/"training-step=3-epoch=1.safetensors").write_bytes({checkpoint_bytes()!r})\n'
            'Path("loss_by_timestep.bin").write_bytes(b"private telemetry")\n')
        request = StableLoraTrainingRequest(name='Test', rank=8, max_steps=3, samples=[StableLoraSample(track_id=1, caption='Exact caption, with tags in order')])
        payload = {'request': request.model_dump(), 'request_sha256': runtime.fingerprint(request), 'sources': [str(source.resolve())],
            'preencode_argv': [sys.executable, str(encode), str(run / 'audio'), str(run / 'latents')],
            'train_argv': [sys.executable, str(train)], 'ffmpeg': runtime.tool('ffmpeg')}
        (run / 'request.json').write_text(json.dumps(payload))
        return run, source, original

    def test_worker_normalizes_private_copy_keeps_exact_caption_and_proves_final_step(self) -> None:
        run, source, original = self.worker_fixture()
        worker = Path(__file__).resolve().parents[1] / 'scripts/stable_lora_worker.py'
        result = subprocess.run([sys.executable, str(worker), '--request', str(run / 'request.json')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(source.read_bytes(), original)
        with wave.open(str(run / 'audio/000.wav')) as audio:
            self.assertEqual(audio.getnchannels(), 2)
            self.assertEqual(audio.getframerate(), 44100)
        self.assertEqual(json.loads((run / 'latents/000.json').read_text())['text'], 'Exact caption, with tags in order')
        marker = json.loads((run / 'completion.json').read_text())
        self.assertEqual(marker['max_steps'], 3)
        self.assertEqual(marker['sha256'], hashlib.sha256((run / marker['checkpoint']).read_bytes()).hexdigest())
        self.assertTrue((run / 'loss_by_timestep.bin').is_file())
        self.assertFalse((self.root / 'loss_by_timestep.bin').exists())

    def test_worker_detects_encoder_zero_exit_with_missing_samples_before_training(self) -> None:
        run, _source, _original = self.worker_fixture()
        encode = self.root / 'encode.py'
        encode.write_text('print("native encode per-file error")\n')
        worker = Path(__file__).resolve().parents[1] / 'scripts/stable_lora_worker.py'
        result = subprocess.run([sys.executable, str(worker), '--request', str(run / 'request.json')], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((run / 'completion.json').exists())
        self.assertFalse((run / 'checkpoints').exists())
