"""The model-free worker validates immutable plans, private audio and real progress."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sys
import signal
import tempfile
import unittest
from unittest.mock import patch
import wave

import numpy as np

from app.music_training_contracts import MusicLoraTrainingCompletion
from app.music_training_contracts import MusicLoraTrainingRequest
from app.yue_lora_runtime import YueLoraEnvironment, prepare_worker
from app.yue_lora_convert_test import PREFIX, fixture

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import yue_lora_worker as worker


class YueLoraWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.directory = self.root / ('b' * 32)
        self.directory.mkdir()
        trainer, comfy = self.root / 'trainer', self.root / 'comfy'
        trainer.mkdir(); (comfy / 'comfy/ldm/yue2').mkdir(parents=True)
        (trainer / 'train_cli.py').write_text('')
        (comfy / 'comfy/ldm/yue2/model.py').write_text('')
        self.base = self.root / 'base.safetensors'; self.base.write_bytes(b'base')
        self.source = self.root / 'source.wav'
        with wave.open(str(self.source), 'wb') as audio:
            audio.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
            audio.writeframes(bytes(48000 * 4))
        self.request = MusicLoraTrainingRequest(engine='yue2', name='Style', max_steps=2,
            samples=[{'track_id': 1, 'caption': 'Warm piano', 'instrumental': True}])
        self.environment = YueLoraEnvironment(Path(sys.executable), trainer, comfy, self.base, Path('/usr/bin/true'), 'mps')
        prepare_worker(self.request, self.directory, [self.source], self.environment)
        self.plan = worker.read_plan(self.directory / 'request.json')

    def test_manifest_digest_and_paths_are_checked_before_work(self) -> None:
        self.assertEqual(self.plan.request, self.request)
        path = self.directory / 'request.json'
        value = json.loads(path.read_bytes())
        value['request']['max_steps'] = 3
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            worker.read_plan(path)

    def test_termination_unwinds_owned_worker_cleanup(self) -> None:
        with self.assertRaises(SystemExit) as stopped:
            worker.cancel(signal.SIGTERM, None)
        self.assertEqual(stopped.exception.code, 143)

    def test_dataset_copy_uses_exact_caption_without_touching_source(self) -> None:
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        def transcode(argv: list[str], **kwargs: object) -> None:
            Path(argv[-1]).write_bytes(self.source.read_bytes())
        with patch.object(worker.subprocess, 'run', side_effect=transcode):
            worker.prepare_dataset(self.plan)
        caption = json.loads((self.directory / 'audio/000.json').read_bytes())
        self.assertEqual(caption, {'style': 'Warm piano', 'lyrics': ''})
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), source_hash)
        manifest = json.loads((self.directory / 'dataset.json').read_bytes())
        self.assertEqual(manifest['samples'][0]['source_sha256'], source_hash)
        with self.assertRaises(FileExistsError):
            worker.prepare_dataset(self.plan)

    def test_only_optimizer_logs_advance_training_and_nonfinite_is_rejected(self) -> None:
        self.assertFalse(worker.update_progress(self.plan, 'INFO loading checkpoint', 0))
        self.assertTrue(worker.update_progress(self.plan, 'INFO YuE2 acoustic step 1/2  loss 0.4321 avg20 0.4321', 0))
        value = json.loads((self.directory / 'stage.json').read_bytes())
        self.assertEqual((value['stage'], value['step'], value['total_steps'], value['loss']), ('training', 1, 2, 0.4321))
        for line in ('YuE2 acoustic step 1/2 loss nan', 'YuE2 acoustic step 3/2 loss 0.1', 'YuE2 acoustic step 1/3 loss 0.1'):
            with self.assertRaises(ValueError):
                worker.update_progress(self.plan, line, 0)

    def test_step_rate_uses_only_time_since_a_previous_optimizer_report(self) -> None:
        worker.update_progress(self.plan, 'YuE2 acoustic step 1/2 loss 0.5', 0)
        initial = json.loads((self.directory / 'stage.json').read_bytes())
        self.assertIsNone(initial.get('seconds_per_step'))
        worker.update_progress(self.plan, 'YuE2 acoustic step 2/2 loss 0.4', 1, elapsed_seconds=12.5)
        measured = json.loads((self.directory / 'stage.json').read_bytes())
        self.assertEqual(measured['seconds_per_step'], 12.5)

    def test_failed_transcode_removes_only_partial_audio(self) -> None:
        def fail(argv: list[str], **kwargs: object) -> None:
            Path(argv[-1]).write_bytes(b'partial')
            raise OSError('interrupted encoder')
        with patch.object(worker.subprocess, 'run', side_effect=fail), self.assertRaises(OSError):
            worker.prepare_dataset(self.plan)
        self.assertEqual(list((self.directory / 'audio').iterdir()), [])
        self.assertTrue(self.source.is_file())

    def test_source_changes_during_transcode_cannot_publish_a_different_dataset(self) -> None:
        def change(argv: list[str], **kwargs: object) -> None:
            Path(argv[-1]).write_bytes(self.source.read_bytes())
            self.source.write_bytes(self.source.read_bytes() + b'concurrent change')
        with patch.object(worker.subprocess, 'run', side_effect=change), self.assertRaisesRegex(ValueError, 'source_changed'):
            worker.prepare_dataset(self.plan)
        self.assertEqual(list((self.directory / 'audio').iterdir()), [])
        self.assertFalse((self.directory / 'dataset.json').exists())
        self.assertTrue(self.source.read_bytes().endswith(b'concurrent change'))

    def test_base_mismatch_fails_before_any_trainer_launch(self) -> None:
        with patch.object(worker, 'verify_sources'), patch.object(worker.subprocess, 'Popen') as launch:
            with self.assertRaises(ValueError):
                worker.run(self.plan)
        launch.assert_not_called()

    def test_finite_completed_run_publishes_a_native_adapter_and_exact_receipt(self) -> None:
        original_digest = worker.digest
        def checksum(path: Path, maximum: int = worker.BASE_BYTES) -> str:
            return worker.BASE_SHA256 if path == self.base else original_digest(path, maximum)
        def transcode(argv: list[str], **kwargs: object) -> None:
            Path(argv[-1]).write_bytes(self.source.read_bytes())
        checkpoint = self.directory / 'trainer.safetensors'
        fixture(checkpoint, {
            PREFIX + 'self_attn.q_proj.lora_down.weight': np.ones((self.request.rank, 2048), dtype=np.float32),
            PREFIX + 'self_attn.q_proj.lora_up.weight': np.ones((2048, self.request.rank), dtype=np.float32),
            PREFIX + 'self_attn.q_proj.alpha': np.array(self.request.rank, dtype=np.float32),
        }, overrides={'steps': 2, 'final_loss': 0.4, 'rank': self.request.rank, 'alpha': self.request.rank})
        class Child:
            stdout = io.StringIO('INFO YuE2 acoustic step 1/2 loss 0.5\nINFO YuE2 acoustic step 2/2 loss 0.4\n')
            def wait(self) -> int:
                return 0
            def poll(self) -> int:
                return 0
        with patch.object(worker, 'verify_sources'), patch.object(worker, 'BASE_BYTES', 4), patch.object(worker, 'digest', side_effect=checksum), patch.object(worker.subprocess, 'run', side_effect=transcode), patch.object(worker.subprocess, 'Popen', return_value=Child()), patch('time.monotonic', side_effect=[1000., 1012.5]):
            worker.run(self.plan)
        receipt = MusicLoraTrainingCompletion.model_validate_json((self.directory / 'completion.json').read_bytes())
        self.assertEqual((receipt.engine, receipt.max_steps, receipt.checkpoint), ('yue2', 2, 'adapter.safetensors'))
        self.assertEqual(receipt.sha256, original_digest(self.directory / 'adapter.safetensors'))
        stage = json.loads((self.directory / 'stage.json').read_bytes())
        self.assertEqual((stage['stage'], stage['loss'], stage['step']), ('done', .4, 2))
        self.assertEqual(stage['seconds_per_step'], 12.5)
