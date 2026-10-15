"""Optional CPU-only worker regressions; no models, downloads or user libraries."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('torch') is not None, 'isolated trainer dependencies are optional')
class HeartLoraWorkerTests(unittest.TestCase):
    def test_progress_persists_measured_optimizer_rate(self) -> None:
        import json
        from scripts.heart_lora_worker import stage
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage(root, 'training', step=2, total=3, loss=.4, seconds_per_step=12.5)
            self.assertEqual(json.loads((root / 'stage.json').read_bytes())['seconds_per_step'], 12.5)

    def test_loss_excludes_prompt_frames(self) -> None:
        import math
        import torch
        from scripts.heart_lora_worker import token_loss
        first = torch.zeros((1, 3, 5), requires_grad=True)
        rest = torch.zeros((1, 3, 7, 5), requires_grad=True)
        tokens = torch.zeros((1, 4, 9), dtype=torch.long)
        frames = torch.tensor([[False, False, False, True]])
        loss = token_loss((first, rest), tokens, frames)
        self.assertAlmostEqual(float(loss.detach()), 2 * math.log(5), places=6)
        loss.backward()
        self.assertIsNotNone(first.grad)
        if first.grad is not None:
            self.assertTrue(torch.equal(first.grad[:, :2], torch.zeros_like(first.grad[:, :2])))
            self.assertGreater(float(first.grad[:, 2:].abs().sum()), 0)

    def test_batch_preserves_codebook_channels_and_marks_only_audio_targets(self) -> None:
        import numpy as np
        import torch
        from scripts.heart_lora_worker import training_batch
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'tokens.npy'
            raw = np.arange(24, dtype=np.int64).reshape(8, 3)
            np.save(path, raw, allow_pickle=False)
            with patch('scripts.heart_lora_worker.prompt_ids', return_value=([128000, 7, 0, 128001], 2)):
                tokens, mask, frames, start = training_batch(path, path, {'caption': 'synth'}, torch.device('cpu'))
            self.assertEqual(start, 2)
            np.testing.assert_array_equal(tokens[0, 4:-1, :8].numpy(), raw.T)
            self.assertTrue(bool((tokens[0, -1, :8] == 8193).all()))
            self.assertFalse(bool(frames[:, :4].any()))
            self.assertTrue(bool(frames[:, 4:].all()))
            self.assertFalse(bool(mask[:, :4, :8].any()))
            self.assertFalse(bool(mask[:, 4:, -1].any()))
            np.save(path, np.full((8, 3), 8192, dtype=np.int64), allow_pickle=False)
            with self.assertRaises(ValueError):
                training_batch(path, path, {'caption': 'synth'}, torch.device('cpu'))

    def test_timestep_formula_matches_cpu_reference_without_device_type_string(self) -> None:
        import math
        import torch
        from scripts.heart_lora_worker import codec_timestep_embedding
        module = torch.nn.Module()
        module.flow_t_size = 512
        values = torch.tensor([0., .1, .5, 1.])
        angles = values[:, None] * torch.exp(-math.log(10000) * torch.arange(256) / 256)[None] * 1000
        expected = torch.cat((torch.cos(angles), torch.sin(angles)), dim=-1)
        self.assertTrue(torch.equal(codec_timestep_embedding(module, values), expected))

    def test_rejects_symlinked_request_files(self) -> None:
        from scripts.heart_lora_worker import regular_file
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / 'request.json';source.write_text('{}')
            link = root / 'link.json'
            try:
                link.symlink_to(source)
            except OSError:
                self.skipTest('symlinks unavailable')
            with self.assertRaises(ValueError):
                regular_file(str(link))

    @unittest.skipUnless(importlib.util.find_spec('soundfile') is not None, 'codec audio dependency is optional')
    def test_private_snapshot_keeps_source_and_rejects_changed_input(self) -> None:
        import numpy as np
        import soundfile as sf
        from scripts.heart_lora_worker import file_hash, snapshot_audio
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / 'source.wav'
            expected = np.linspace(-.1, .1, 8000, dtype=np.float32)
            sf.write(source, expected, 8000, subtype='FLOAT')
            original = source.read_bytes()
            samples, rate, provenance = snapshot_audio(source, root, 0, 6, '')
            self.assertEqual(rate, 8000)
            np.testing.assert_array_equal(samples[:, 0], expected)
            self.assertEqual(provenance['source_sha256'], file_hash(source))
            self.assertEqual(source.read_bytes(), original)
            with patch('scripts.heart_lora_worker.file_hash', side_effect=['1' * 64, '2' * 64]), self.assertRaisesRegex(ValueError, 'source_changed'):
                snapshot_audio(source, root, 1, 6, '')
            self.assertFalse((root / 'audio/001.wav').exists())
