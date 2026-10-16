from __future__ import annotations

import unittest
from pydantic import ValidationError
from app.training_caption_contracts import TrainingCaptionRequest, TrainingCaptionItem
from app.music_training_source_contracts import MusicLoraSource


class TrainingCaptionContractsTests(unittest.TestCase):
    def test_accepts_bounded_sources_without_training_captions(self) -> None:
        request = TrainingCaptionRequest(samples=[MusicLoraSource(track_id=1)])
        self.assertEqual(request.segment_seconds, 12)
        self.assertEqual(TrainingCaptionItem(source=request.samples[0]).status, 'queued')

    def test_rejects_unbounded_duplicate_and_untrusted_inputs(self) -> None:
        for value in ({'samples': []}, {'samples': [{'track_id': 1}] * 101},
                      {'samples': [{'track_id': 1}] * 2}, {'samples': [{'track_id': 1}], 'segment_seconds': True},
                      {'samples': [{'track_id': 1}], 'segment_seconds': 31},
                      {'samples': [{'track_id': 1, 'path': '/private/source.wav'}]}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                TrainingCaptionRequest.model_validate(value)
