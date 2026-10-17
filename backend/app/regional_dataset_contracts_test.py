"""Dataset permissions and excerpts remain source-scoped, finite and path-free."""
from __future__ import annotations

import unittest
from pydantic import ValidationError

from app.regional_dataset_contracts import RegionalDatasetReviewRequest, RegionalRecordingReview


def recording(identifier: str = 'a', **changes: object) -> dict[str, object]:
    checksum = identifier * 64
    return {'id': identifier * 32, 'source': {'dataset_id': 'd' * 32, 'revision': 2, 'sample_id': identifier},
        'source_sha256': checksum, 'caption': 'Declared plucked-string phrase', 'captions_reviewed': True,
        'rights': [{'id': identifier * 32, 'source_sha256': checksum, 'evidence_sha256': 'e' * 64,
            'restriction': 'conditional', 'reviewed_by': 'Fixture reviewer', 'reviewed_at': '2026-10-17',
            'permissions': {'reference': 'allowed', 'training': 'allowed', 'source_redistribution': 'unknown',
                'adapter_distribution': 'unknown'}, 'private_reference': 'fixture-contract',
            'public_attribution': 'Example performer, used with scoped permission.'}], **changes}


class RegionalDatasetContractTests(unittest.TestCase):
    def test_inventory_keeps_declared_people_acquisition_and_original_filename_without_paths(self) -> None:
        value = RegionalRecordingReview.model_validate(recording(author='Declared author', performer='Declared performer',
            acquired_at='2026-10-17', original_filename='original-session.wav'))
        self.assertEqual(value.original_filename, 'original-session.wav')
        with self.assertRaises(ValidationError):
            RegionalRecordingReview.model_validate(recording(original_filename='/private/session.wav'))

    def test_rights_cannot_be_copied_to_a_different_source_or_accept_private_paths(self) -> None:
        value = RegionalRecordingReview.model_validate(recording())
        self.assertEqual(value.rights[0].permissions.source_redistribution, 'unknown')
        for changes in ({'source_sha256': 'f' * 64}, {'source_path': '/private/recording.wav'},
                {'rights': [{**recording()['rights'][0], 'private_reference': '/private/contract.pdf'}]}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                RegionalRecordingReview.model_validate({**recording(), **changes})

    def test_review_binds_saved_dataset_revision_and_nonempty_frame_excerpts(self) -> None:
        payload = {'dataset_id': 'd' * 32, 'dataset_revision': 2, 'expected_revision': 0,
            'tradition': 'Unreviewed fixture tradition', 'recordings': [recording()],
            'excerpts': [{'id': 'f' * 32, 'recording_id': 'a' * 32, 'start_frame': 0, 'end_frame': 48000}]}
        self.assertEqual(RegionalDatasetReviewRequest.model_validate(payload).split_seed, 42)
        for changes in ({'recordings': [recording(), recording()]}, {'dataset_revision': 3},
                {'excerpts': [{**payload['excerpts'][0], 'end_frame': 0}]},
                {'excerpts': [{**payload['excerpts'][0], 'recording_id': 'b' * 32}]},
                {'dataset_id': 'd' * 32 + '\n'}, {'split_seed': True}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                RegionalDatasetReviewRequest.model_validate({**payload, **changes})
