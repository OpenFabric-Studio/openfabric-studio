from __future__ import annotations

import unittest
from pydantic import ValidationError
from app.music_training_contracts import MusicLoraTrainingRequest, MusicLoraTrainingProgress


class MusicTrainingContractsTests(unittest.TestCase):
    def test_short_captioned_fresh_run_has_safe_defaults(self) -> None:
        request = MusicLoraTrainingRequest(engine='yue2', name='Warm strings', samples=[{'track_id': 1, 'caption': 'Warm strings'}])
        self.assertEqual(request.segment_seconds, 12)
        self.assertEqual(request.rank, 16)
        self.assertFalse(request.samples[0].instrumental)

    def test_invalid_and_ambiguous_training_inputs_are_rejected(self) -> None:
        base: dict[str, object] = {'engine': 'heartmula', 'name': 'New adapter', 'samples': [{'track_id': 1, 'caption': 'Piano'}]}
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
