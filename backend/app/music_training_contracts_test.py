from __future__ import annotations

import unittest
import hashlib
import json
from pydantic import ValidationError
from app import music_training_contracts as contracts
from app.music_training_contracts import MusicLoraTrainingRequest, MusicLoraTrainingProgress
from app.music_training_runtime import fingerprint


class MusicTrainingContractsTests(unittest.TestCase):
    def test_removed_engine_is_rejected_and_status_lists_only_support_yue(self) -> None:
        with self.assertRaises(ValidationError):
            MusicLoraTrainingRequest(engine='heartmula', name='Removed', samples=[{'track_id': 1, 'caption': 'Piano'}])
        supported = contracts.MusicLoraTrainingStatus(engine='yue2', supported=True, installed=True, weights_ready=True)
        with self.assertRaises(ValidationError):
            contracts.MusicLoraTrainingStatuses(engines=[supported, supported])

    def test_ar_completion_is_only_allowed_for_one_saved_yue_source(self) -> None:
        self.assertIn('yue_ar_source_job_id', MusicLoraTrainingRequest.model_fields)
        base = {'engine': 'yue2', 'name': 'Existing', 'samples': [{'track_id': 1, 'caption': 'Voice', 'lyrics': 'Hello'}],
            'yue_ar_source_job_id': 'a' * 32}
        request = MusicLoraTrainingRequest.model_validate(base)
        self.assertEqual(request.yue_ar_source_job_id, 'a' * 32)
        for change in ({'engine': 'heartmula'}, {'yue_pair': {}}, {'yue_ar_source_job_id': '../source'}, {'yue_ar_source_job_id': ''}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                MusicLoraTrainingRequest.model_validate({**base, **change})
        job = contracts.MusicLoraTrainingJob(id='b' * 32, status='done', created_at='2026-10-16', request=request)
        self.assertFalse(job.can_train_missing_ar)

    def test_pair_settings_allow_matching_lyrics_and_preserve_legacy_fingerprint(self) -> None:
        base = {'engine': 'yue2', 'name': 'Voice', 'samples': [{'track_id': 1, 'caption': 'Vocals'}]}
        legacy = MusicLoraTrainingRequest.model_validate(base)
        expected = {'engine': 'yue2', 'name': 'Voice', 'samples': [{'track_id': 1, 'caption': 'Vocals', 'lyrics': '', 'instrumental': False}],
            'max_steps': 1000, 'rank': 16, 'learning_rate': 0.0001, 'seed': 42, 'segment_seconds': 12}
        self.assertEqual(fingerprint(legacy), hashlib.sha256(json.dumps(expected, separators=(',', ':')).encode()).hexdigest())
        self.assertIn('yue_pair', MusicLoraTrainingRequest.model_fields)
        pair = MusicLoraTrainingRequest.model_validate({**base, 'yue_pair': {}, 'samples': [{'track_id': 1, 'caption': 'Vocals', 'lyrics': 'Hello'}]})
        self.assertIsNotNone(pair.yue_pair)
        if pair.yue_pair is None:
            self.fail('Expected paired settings')
        self.assertEqual((pair.yue_pair.planner_steps, pair.yue_pair.planner_learning_rate), (100, 0.00005))
        self.assertNotEqual(fingerprint(pair), fingerprint(legacy))
        for engine, settings in (('heartmula', {}), ('yue2', {'planner_steps': True}), ('yue2', {'planner_steps': 0}),
                ('yue2', {'planner_steps': 100001}), ('yue2', {'planner_learning_rate': True}),
                ('yue2', {'planner_learning_rate': 0.0}), ('yue2', {'planner_learning_rate': float('inf')})):
            with self.subTest(engine=engine, settings=settings), self.assertRaises(ValidationError):
                MusicLoraTrainingRequest.model_validate({**base, 'engine': engine, 'yue_pair': settings})

    def test_pair_receipt_requires_exactly_one_artifact_for_each_target(self) -> None:
        self.assertTrue(hasattr(contracts, 'MusicLoraTrainingPairCompletion'))
        receipt = {'job_id': 'a' * 32, 'engine': 'yue2', 'request_sha256': 'b' * 64, 'dataset_sha256': 'c' * 64,
            'base_sha256': 'd' * 64, 'max_steps': 3, 'planner_steps': 2,
            'artifacts': [{'target': 'ar', 'checkpoint': 'ar.safetensors', 'sha256': 'e' * 64},
                {'target': 'nar', 'checkpoint': 'nar.safetensors', 'sha256': 'f' * 64}]}
        contracts.MusicLoraTrainingPairCompletion.model_validate(receipt)
        for change in ({'artifacts': receipt['artifacts'][:1]}, {'artifacts': [receipt['artifacts'][0], receipt['artifacts'][0]]},
                {'engine': 'heartmula'}, {'planner_steps': True}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                contracts.MusicLoraTrainingPairCompletion.model_validate({**receipt, **change})

    def test_target_and_transcription_progress_are_explicit_and_old_jobs_remain_valid(self) -> None:
        self.assertIn('target', MusicLoraTrainingProgress.model_fields)
        progress = MusicLoraTrainingProgress(stage='transcribing', target='ar')
        self.assertEqual(progress.target, 'ar')
        old = contracts.MusicLoraTrainingJob(id='a' * 32, status='done', created_at='2026-10-15',
            request={'engine': 'yue2', 'name': 'Old', 'samples': [{'track_id': 1, 'caption': 'Strings'}]})
        self.assertEqual(old.adapters, [])
        self.assertIsNone(old.request.yue_pair)

    def test_paired_readiness_is_explicit_but_optional_for_legacy_yue_jobs(self) -> None:
        self.assertIn('paired_ready', contracts.MusicLoraTrainingStatus.model_fields)
        status = contracts.MusicLoraTrainingStatus(engine='yue2', supported=True, installed=True, weights_ready=True)
        self.assertIsNone(status.paired_ready)

    def test_short_captioned_fresh_run_has_safe_defaults(self) -> None:
        request = MusicLoraTrainingRequest(engine='yue2', name='Warm strings', samples=[{'track_id': 1, 'caption': 'Warm strings'}])
        self.assertEqual(request.segment_seconds, 12)
        self.assertEqual(request.rank, 16)
        self.assertFalse(request.samples[0].instrumental)

    def test_invalid_and_ambiguous_training_inputs_are_rejected(self) -> None:
        base: dict[str, object] = {'engine': 'yue2', 'name': 'New adapter', 'samples': [{'track_id': 1, 'caption': 'Piano'}]}
        for change in ({'name': ' '}, {'engine': 'unknown'}, {'rank': 0}, {'seed': True}, {'segment_seconds': 31},
                       {'learning_rate': float('nan')}, {'max_steps': 1.5}, {'resume': '/private/weights'},
                       {'samples': [{'track_id': 1, 'caption': 'Piano'}, {'track_id': 1, 'caption': 'Duplicate'}]},
                       {'samples': [{'track_id': 1, 'caption': ' '}]},
                       {'samples': [{'track_id': 1, 'caption': 'Piano', 'instrumental': True, 'lyrics': 'words'}]}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                MusicLoraTrainingRequest.model_validate({**base, **change})

    def test_acoustic_training_does_not_pretend_to_train_lyrics(self) -> None:
        with self.assertRaises(ValidationError):
            MusicLoraTrainingRequest(engine='yue2', name='Voice', samples=[{'track_id': 1, 'caption': 'Vocals', 'lyrics': 'Hello'}])

    def test_progress_must_be_finite_and_bounded(self) -> None:
        for values in ({'step': 11, 'total_steps': 10}, {'loss': float('inf')}, {'stage': 'invented'}, {'step': -1},
                       {'seconds_per_step': 0}, {'seconds_per_step': float('inf')}, {'seconds_per_step': True}, {'seconds_per_step': 86401}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                MusicLoraTrainingProgress.model_validate(values)
