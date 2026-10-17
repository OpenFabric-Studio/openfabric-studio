from __future__ import annotations

import unittest
from pydantic import ValidationError
from app.regional_acoustic_contracts import RegionalAcousticScanRequest, RegionalAcousticDecision


class AcousticContractTests(unittest.TestCase):
    def test_scan_is_bound_to_saved_review_without_paths_and_strict_ids(self) -> None:
        payload = {'dataset_id': 'a' * 32, 'review_revision': 1, 'review_sha256': 'b' * 64, 'submission_key': 'c' * 32}
        self.assertEqual(RegionalAcousticScanRequest.model_validate(payload).review_revision, 1)
        for change in ({'dataset_id': 'a' * 32 + '\n'}, {'review_sha256': 'b' * 64 + '\n'},
                {'path': '/private/music.wav'}, {'review_revision': True}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                RegionalAcousticScanRequest.model_validate({**payload, **change})

    def test_distinct_review_requires_attribution_and_a_retained_reason(self) -> None:
        payload = {'candidate_id': 'a' * 64, 'decision': 'distinct_after_review', 'reviewed_by': 'Fixture reviewer',
            'reviewed_at': '2026-10-17', 'note': 'Compared the aligned phrases; separate performances.'}
        self.assertEqual(RegionalAcousticDecision.model_validate(payload).decision, 'distinct_after_review')
        with self.assertRaises(ValidationError):
            RegionalAcousticDecision.model_validate({**payload, 'note': ''})
