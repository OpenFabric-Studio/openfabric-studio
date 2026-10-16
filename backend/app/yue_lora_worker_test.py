"""The model-free worker validates immutable plans, private audio and real progress."""
from __future__ import annotations

import hashlib
from dataclasses import replace
import io
import json
from pathlib import Path
import sys
import signal
import os
import tempfile
import unittest
from unittest.mock import patch
import wave

import numpy as np

from app.music_training_contracts import MusicLoraTrainingCompletion, MusicLoraTrainingPairCompletion
from app.music_training_contracts import MusicLoraTrainingRequest
from app.yue_lora_runtime import YueLoraEnvironment, prepare_worker
from app.yue_lora_convert_test import PREFIX, AR_PREFIX, fixture

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

    def pair_plan(self, *, lyrics: str = '') -> worker.TrainingPlan:
        cli, model, spec = self.root / 'audiocpp_cli', self.root / 'score.gguf', self.root / 'sheetsage2.json'
        cli.write_bytes(b'installed'); cli.chmod(0o755)
        model.write_bytes(b'GGUF' + bytes(32)); spec.write_text('{"family":"sheetsage2"}')
        env = replace(self.environment, score_cli=cli, score_model=model, score_model_spec=spec)
        request = MusicLoraTrainingRequest.model_validate({**self.request.model_dump(), 'yue_pair': {'planner_steps': 3},
            'samples': [{'track_id': 1, 'caption': 'Warm piano', 'lyrics': lyrics}]})
        return replace(self.plan, request=request, environment=env, fingerprint=worker.fingerprint(request))

    def checkpoint(self, name: str, *, target: str = 'nar', steps: int = 2) -> None:
        prefix = AR_PREFIX if target == 'ar' else PREFIX
        fixture(self.directory / name, {
            prefix + 'self_attn.q_proj.lora_down.weight': np.ones((self.request.rank, 2048), dtype=np.float32),
            prefix + 'self_attn.q_proj.lora_up.weight': np.ones((2048, self.request.rank), dtype=np.float32),
            prefix + 'self_attn.q_proj.alpha': np.array(self.request.rank, dtype=np.float32),
        }, overrides={'kind': 'planner' if target == 'ar' else 'acoustic', 'train_abc': True, 'train_semantic': False,
            'steps': steps, 'final_loss': .4, 'rank': self.request.rank, 'alpha': self.request.rank})

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

    def test_paired_plan_round_trip_preserves_score_paths(self) -> None:
        plan = self.pair_plan()
        (self.directory / 'request.json').unlink()
        prepare_worker(plan.request, self.directory, [self.source], plan.environment)
        self.assertEqual(worker.read_plan(self.directory / 'request.json').environment, plan.environment)

    def test_paired_lyrics_are_preserved_and_long_source_is_rejected_before_trimming(self) -> None:
        plan = self.pair_plan(lyrics='[Verse]\nA matching line')
        def transcode(argv: list[str], **kwargs: object) -> None:
            Path(argv[-1]).write_bytes(self.source.read_bytes())
        with patch.object(worker.subprocess, 'run', side_effect=transcode):
            worker.prepare_dataset(plan)
        sidecar = json.loads((self.directory / 'audio/000.json').read_bytes())
        self.assertEqual(sidecar['lyrics'], '[Verse]\nA matching line')
        manifest = json.loads((self.directory / 'dataset.json').read_bytes())
        self.assertEqual(manifest['samples'][0]['lyrics'], '[Verse]\nA matching line')
        other = self.root / ('c' * 32); other.mkdir()
        with wave.open(str(self.source), 'wb') as audio:
            audio.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
            audio.writeframes(bytes((plan.request.segment_seconds + 1) * 48000 * 4))
        with patch.object(worker.subprocess, 'run') as launch, self.assertRaisesRegex(ValueError, 'lyrics_require_matching_trimmed_clip'):
            worker.prepare_dataset(replace(plan, directory=other))
        launch.assert_not_called()

    def test_score_failure_or_rest_fallback_never_launches_training(self) -> None:
        plan = self.pair_plan()
        original_digest = worker.digest
        def checksum(path: Path, maximum: int = worker.BASE_BYTES) -> str:
            return worker.BASE_SHA256 if path == self.base else original_digest(path, maximum)
        def transcode(argv: list[str], **kwargs: object) -> None:
            Path(argv[-1]).write_bytes(self.source.read_bytes())
        class Child:
            stdout = io.StringIO('transcription failed\n')
            def wait(self, timeout: int | None = None) -> int:
                return 0
            def poll(self) -> int:
                return 0
        def launch(argv: list[str], **kwargs: object) -> Child:
            self.assertIn('--family', argv, 'Trainer launched before a usable score')
            output = Path(argv[argv.index('--out-dir') + 1]); output.mkdir(exist_ok=True)
            (output / 'score.abc').write_text('X:1\nM:4/4\nL:1/8\nK:C\nz8|z8|')
            return Child()
        with patch.object(worker, 'verify_sources'), patch.object(worker, 'BASE_BYTES', 4), patch.object(worker, 'digest', side_effect=checksum), patch.object(worker.subprocess, 'run', side_effect=transcode), patch.object(worker.subprocess, 'Popen', side_effect=launch) as children:
            with self.assertRaisesRegex(ValueError, 'score_invalid'):
                worker.run(plan)
        self.assertEqual(children.call_count, 1)
        self.assertFalse((self.directory / 'completion.json').exists())

    def test_pair_runs_score_then_planner_then_acoustic_and_publishes_both(self) -> None:
        plan = self.pair_plan()
        original_digest = worker.digest
        def checksum(path: Path, maximum: int = worker.BASE_BYTES) -> str:
            return worker.BASE_SHA256 if path == self.base else original_digest(path, maximum)
        def transcode(argv: list[str], **kwargs: object) -> None:
            Path(argv[-1]).write_bytes(self.source.read_bytes())
        launched: list[str] = []
        class Child:
            def __init__(self, lines: str) -> None:
                self.stdout = io.StringIO(lines)
            def wait(self, timeout: int | None = None) -> int:
                return 0
            def poll(self) -> int:
                return 0
        def launch(argv: list[str], **kwargs: object) -> Child:
            if '--out-dir' in argv:
                launched.append('score')
                output = Path(argv[argv.index('--out-dir') + 1]); output.mkdir(exist_ok=True)
                (output / 'score.abc').write_text('X:1\nM:4/4\nL:1/8\nK:C\nCDEF GABc|')
                return Child('score extracted\n')
            target = 'ar' if argv[3] == 'planner' else 'nar'
            launched.append(target)
            self.assertTrue((self.directory / 'audio/000.abc').is_file())
            steps = 3 if target == 'ar' else 2
            self.checkpoint(f'trainer-{target}.safetensors', target=target, steps=steps)
            kind = 'planner' if target == 'ar' else 'acoustic'
            return Child(''.join(f'YuE2 {kind} step {step}/{steps} loss 0.4\n' for step in range(1, steps + 1)))
        with patch.object(worker, 'verify_sources'), patch.object(worker, 'BASE_BYTES', 4), patch.object(worker, 'digest', side_effect=checksum), patch.object(worker.subprocess, 'run', side_effect=transcode), patch.object(worker.subprocess, 'Popen', side_effect=launch):
            worker.run(plan)
        self.assertEqual(launched, ['score', 'ar', 'nar'])
        receipt = MusicLoraTrainingPairCompletion.model_validate_json((self.directory / 'completion.json').read_bytes())
        self.assertEqual((receipt.planner_steps, receipt.max_steps), (3, 2))
        self.assertEqual({item.target for item in receipt.artifacts}, {'ar', 'nar'})
        for item in receipt.artifacts:
            self.assertEqual(item.sha256, original_digest(self.directory / item.checkpoint))
        manifest = json.loads((self.directory / 'dataset.json').read_bytes())
        self.assertEqual(manifest['samples'][0]['score_sha256'], original_digest(self.directory / 'audio/000.abc'))
        stage = json.loads((self.directory / 'stage.json').read_bytes())
        self.assertEqual((stage['stage'], stage['target'], stage['total_steps']), ('done', 'nar', 2))

    def test_failed_or_cancelled_planner_never_starts_acoustic_or_writes_receipt(self) -> None:
        self.assertTrue(hasattr(worker, 'run_training'), 'Training phases must have owned boundaries')
        for failure in (RuntimeError('planner failed'), SystemExit(143)):
            with self.subTest(failure=failure):
                plan = self.pair_plan()
                with patch.object(worker, 'verify_sources'), patch.object(worker, 'BASE_BYTES', 4), patch.object(worker, 'digest', return_value=worker.BASE_SHA256), patch.object(worker, 'prepare_dataset'), patch.object(worker, 'extract_scores'), patch.object(worker, 'run_training', side_effect=failure) as train:
                    with self.assertRaises(type(failure)):
                        worker.run(plan)
                self.assertEqual(train.call_count, 1)
                self.assertEqual(train.call_args.kwargs['target'], 'ar')
                self.assertFalse((self.directory / 'completion.json').exists())

    def test_prepared_scores_allow_portable_pair_without_cli_and_reject_tampering(self) -> None:
        plan = self.pair_plan()
        (self.directory / 'request.json').unlink()
        prepare_worker(plan.request, self.directory, [self.source], plan.environment)
        score = self.directory / 'prepared.abc'
        score.write_text('X:1\nM:4/4\nL:1/8\nK:C\nCDEF GABc|')
        path = self.directory / 'request.json'
        payload = json.loads(path.read_bytes())
        payload['prepared_scores'] = [{'path': str(score), 'sha256': worker.digest(score)}]
        for field in ('score_cli', 'score_model', 'score_model_spec', 'score_backend'):
            payload['runtime'].pop(field)
        path.write_text(json.dumps(payload))
        try:
            prepared = worker.read_plan(path)
        except ValueError as error:
            self.fail(f'Portable paired plan rejected checked prepared scores: {error}')
        def transcode(argv: list[str], **kwargs: object) -> None:
            Path(argv[-1]).write_bytes(self.source.read_bytes())
        with patch.object(worker.subprocess, 'run', side_effect=transcode):
            worker.prepare_dataset(prepared)
        with patch.object(worker.subprocess, 'Popen') as launch:
            worker.extract_scores(prepared)
        launch.assert_not_called()
        self.assertEqual((self.directory / 'audio/000.abc').read_text().strip(), score.read_text())
        score.write_text('X:1\nM:4/4\nL:1/8\nK:C\nAAAA|')
        with self.assertRaisesRegex(ValueError, 'score_changed'):
            worker.read_plan(path)

    def test_owned_child_is_terminated_and_reaped_on_cancellation(self) -> None:
        self.assertTrue(hasattr(worker, 'run_logged'), 'Each native child must be owned')
        events: list[str] = []
        class Child:
            stdout = io.StringIO('model starting\n')
            stopped = False
            def poll(self) -> int | None:
                return 143 if self.stopped else None
            def terminate(self) -> None:
                events.append('terminate'); self.stopped = True
            def wait(self, timeout: int | None = None) -> int:
                events.append('wait'); return 143
        child = Child()
        def cancel(line: str) -> None:
            raise SystemExit(143)
        with patch.object(worker.subprocess, 'Popen', return_value=child), self.assertRaises(SystemExit):
            worker.run_logged(['native'], self.directory / 'owned.log', cancel)
        self.assertEqual(events, ['terminate', 'wait'])
        self.assertTrue(child.stdout.closed)

    def test_failed_acoustic_retains_planner_and_partial_checkpoints_without_pair_receipt(self) -> None:
        plan = self.pair_plan()
        phases: list[str] = []
        class Child:
            def __init__(self, kind: str, steps: int, code: int) -> None:
                self.code = code
                self.stdout = io.StringIO(''.join(f'YuE2 {kind} step {step}/{steps} loss 0.4\n' for step in range(1, steps + 1)))
            def wait(self, timeout: int | None = None) -> int:
                return self.code
            def poll(self) -> int:
                return self.code
        def launch(argv: list[str], **kwargs: object) -> Child:
            phases.append(argv[3])
            if argv[3] == 'planner':
                self.checkpoint('trainer-ar.safetensors', target='ar', steps=3)
                return Child('planner', 3, 0)
            (self.directory / 'trainer-nar_000001.safetensors').write_bytes(b'partial')
            return Child('acoustic', 2, 1)
        with patch.object(worker, 'verify_sources'), patch.object(worker, 'BASE_BYTES', 4), patch.object(worker, 'digest', return_value=worker.BASE_SHA256), patch.object(worker, 'prepare_dataset'), patch.object(worker, 'extract_scores'), patch.object(worker.subprocess, 'Popen', side_effect=launch):
            with self.assertRaisesRegex(RuntimeError, 'child_failed'):
                worker.run(plan)
        self.assertEqual(phases, ['planner', 'acoustic'])
        self.assertTrue((self.directory / 'trainer-ar.safetensors').is_file())
        self.assertEqual((self.directory / 'trainer-nar_000001.safetensors').read_bytes(), b'partial')
        self.assertFalse((self.directory / 'completion.json').exists())

    def test_wrong_stage_logs_and_incomplete_output_fail_before_acoustic(self) -> None:
        for lines in ('YuE2 acoustic step 1/3 loss .4\n', 'YuE2 planner step 1/3 loss .4\n'):
            with self.subTest(lines=lines):
                plan = self.pair_plan()
                class Child:
                    stdout = io.StringIO(lines)
                    def wait(self, timeout: int | None = None) -> int:
                        return 0
                    def poll(self) -> int:
                        return 0
                log = self.directory / 'trainer-ar.log'
                log.unlink(missing_ok=True)
                with patch.object(worker, 'verify_sources'), patch.object(worker, 'BASE_BYTES', 4), patch.object(worker, 'digest', return_value=worker.BASE_SHA256), patch.object(worker, 'prepare_dataset'), patch.object(worker, 'extract_scores'), patch.object(worker.subprocess, 'Popen', return_value=Child()) as launch:
                    with self.assertRaisesRegex(ValueError, 'progress_invalid|training_incomplete'):
                        worker.run(plan)
                self.assertEqual(launch.call_count, 1)
                self.assertFalse((self.directory / 'completion.json').exists())


    def ar_plan(self) -> worker.TrainingPlan:
        from app.yue_lora_runtime import PreparedAudio
        pair = self.pair_plan()
        request = MusicLoraTrainingRequest.model_validate({**self.request.model_dump(),
            'yue_ar_source_job_id': 'c' * 32, 'max_steps': 3, 'learning_rate': 0.00005})
        (self.directory / 'request.json').unlink(missing_ok=True)
        prepare_worker(request, self.directory, [self.source], pair.environment,
            prepared_audio=(PreparedAudio(self.source, worker.digest(self.source)),))
        return worker.read_plan(self.directory / 'request.json')

    def test_missing_ar_copies_exact_cached_excerpt_without_reencoding(self) -> None:
        plan = self.ar_plan()
        before = self.source.read_bytes()
        with patch.object(worker.subprocess, 'run') as encoder:
            worker.prepare_dataset(plan)
        encoder.assert_not_called()
        self.assertEqual((self.directory / 'audio/000.wav').read_bytes(), before)
        self.assertEqual(self.source.read_bytes(), before)
        sample = json.loads((self.directory / 'dataset.json').read_bytes())['samples'][0]
        self.assertEqual(sample['source_sha256'], worker.digest(self.source))
        self.assertEqual(sample['audio_sha256'], worker.digest(self.source))
        self.assertEqual(sample['caption'], self.request.samples[0].caption)
        self.assertEqual(sample['original_duration_seconds'], 1)

    def test_missing_ar_rejects_cached_changes_before_copy_or_score_launch(self) -> None:
        plan = self.ar_plan()
        self.source.write_bytes(self.source.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'prepared_audio'):
            worker.read_plan(self.directory / 'request.json')
        with patch.object(worker.subprocess, 'Popen') as launch, self.assertRaisesRegex(ValueError, 'prepared_audio'):
            worker.prepare_dataset(plan)
        launch.assert_not_called()
        self.assertEqual(list((self.directory / 'audio').iterdir()), [])
        self.assertFalse((self.directory / 'dataset.json').exists())

    def test_missing_ar_plan_requires_exact_prepared_audio_paths_and_mode(self) -> None:
        self.ar_plan()
        path = self.directory / 'request.json'
        saved = json.loads(path.read_bytes())
        for prepared in (None, [], [{'path': str(self.root / 'missing.wav'), 'sha256': worker.digest(self.source)}]):
            with self.subTest(prepared=prepared):
                payload = {**saved}
                if prepared is None:
                    payload.pop('prepared_audio')
                else:
                    payload['prepared_audio'] = prepared
                path.write_text(json.dumps(payload))
                with self.assertRaisesRegex(ValueError, 'prepared_audio'):
                    worker.read_plan(path)
        payload = {**saved, 'request': self.request.model_dump(), 'request_sha256': worker.fingerprint(self.request)}
        path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, 'prepared_audio'):
            worker.read_plan(path)

    def test_missing_ar_runs_scores_and_planner_only_with_single_receipt(self) -> None:
        plan = self.ar_plan()
        before = self.source.read_bytes()
        original_digest = worker.digest
        def checksum(path: Path, maximum: int = worker.BASE_BYTES) -> str:
            return worker.BASE_SHA256 if path == self.base else original_digest(path, maximum)
        score_script = self.root / 'score.py'
        score_script.write_text("from pathlib import Path; import sys; p=Path(sys.argv[1]); p.mkdir(exist_ok=True); (p/'score.abc').write_text(" + repr('X:1\nM:4/4\nL:1/8\nK:C\nCDEF GABc|') + "); print('score extracted',flush=True)")
        train_script = self.root / 'train.py'
        train_script.write_text("for step in range(1,4): print(f'YuE2 planner step {step}/3 loss 0.4',flush=True)")
        self.checkpoint('trainer-ar.safetensors', target='ar', steps=3)
        launched: list[str] = []
        def score_command(audio: Path, output: Path, environment: YueLoraEnvironment) -> list[str]:
            launched.append('score'); return [sys.executable, str(score_script), str(output)]
        def train_command(request: MusicLoraTrainingRequest, directory: Path, environment: YueLoraEnvironment, *, target: str) -> list[str]:
            launched.append(target); self.assertEqual(target, 'ar')
            return [sys.executable, str(train_script)]
        with patch.object(worker, 'verify_sources'), patch.object(worker, 'BASE_BYTES', 4), patch.object(worker, 'digest', side_effect=checksum), patch.object(worker, 'score_argv', side_effect=score_command), patch.object(worker, 'training_argv', side_effect=train_command), patch.object(worker.subprocess, 'run') as encoder:
            worker.run(plan)
        encoder.assert_not_called()
        self.assertEqual(launched, ['score', 'ar'])
        self.assertEqual(self.source.read_bytes(), before)
        receipt = MusicLoraTrainingCompletion.model_validate_json((self.directory / 'completion.json').read_bytes())
        self.assertEqual((receipt.checkpoint, receipt.max_steps), ('adapter-ar.safetensors', 3))
        self.assertEqual(receipt.sha256, original_digest(self.directory / 'adapter-ar.safetensors'))
        self.assertFalse((self.directory / 'adapter-nar.safetensors').exists())
        self.assertFalse((self.directory / 'trainer-nar.log').exists())
        stage = json.loads((self.directory / 'stage.json').read_bytes())
        self.assertEqual((stage['stage'], stage['target'], stage['total_steps'], stage['step']), ('done', 'ar', 3, 3))

    def test_missing_ar_failed_or_cancelled_child_never_publishes_or_runs_nar(self) -> None:
        for failure in (RuntimeError('planner failed'), SystemExit(143)):
            with self.subTest(failure=failure):
                plan = self.ar_plan()
                with patch.object(worker, 'verify_sources'), patch.object(worker, 'BASE_BYTES', 4), patch.object(worker, 'digest', return_value=worker.BASE_SHA256), patch.object(worker, 'prepare_dataset'), patch.object(worker, 'extract_scores'), patch.object(worker, 'run_training', side_effect=failure) as train:
                    with self.assertRaises(type(failure)):
                        worker.run(plan)
                self.assertEqual(train.call_count, 1)
                self.assertEqual(train.call_args.kwargs['target'], 'ar')
                self.assertFalse((self.directory / 'completion.json').exists())

    def test_real_owned_child_exits_on_progress_cancellation(self) -> None:
        def stop(line: str) -> None:
            raise SystemExit(143)
        script = self.root / 'sleep.py'
        pid = self.root / 'child.pid'
        script.write_text('import os,sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(str(os.getpid())); print("ready",flush=True); time.sleep(30)')
        with self.assertRaises(SystemExit):
            worker.run_logged([sys.executable, str(script), str(pid)], self.directory / 'real-cancel.log', stop)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid.read_text()), 0)

    def test_real_owned_child_failure_cannot_publish_completion(self) -> None:
        with self.assertRaisesRegex(RuntimeError, 'child_failed'):
            worker.run_logged([sys.executable, '-c', 'raise SystemExit(7)'], self.directory / 'real-failure.log', lambda line: None)
        self.assertFalse((self.directory / 'completion.json').exists())
