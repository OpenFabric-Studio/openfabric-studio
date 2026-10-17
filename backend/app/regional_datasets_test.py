"""Exact content/provenance clusters are split before excerpts reach training."""
from __future__ import annotations

import unittest

from app.regional_dataset_contracts import RegionalDatasetReviewRequest, RegionalRecordingInspection
from app.regional_dataset_contracts_test import recording
from app.regional_datasets import split_recordings


def review(*identifiers: str) -> RegionalDatasetReviewRequest:
    rows = [recording(identifier, original_recording_sha256=identifier * 64,
        performer_group_sha256=identifier * 64, session_group_sha256=identifier * 64) for identifier in identifiers]
    return RegionalDatasetReviewRequest.model_validate({'dataset_id': 'd' * 32, 'dataset_revision': 2,
        'expected_revision': 0, 'tradition': 'User-declared fixture', 'recordings': rows,
        'excerpts': [{'id': identifier * 32, 'recording_id': identifier * 32, 'start_frame': 0,
            'end_frame': 48000} for identifier in identifiers]})


def inspections(value: RegionalDatasetReviewRequest) -> list[RegionalRecordingInspection]:
    return [RegionalRecordingInspection(recording_id=item.id, source_sha256=item.source_sha256,
        decoded_pcm_sha256=item.source_sha256, source_bytes=192044, sample_rate_hz=48000, channels=2,
        frames=48000) for item in value.recordings]


class RegionalSplitTests(unittest.TestCase):
    def test_known_instrument_technique_strata_are_represented_when_group_budget_allows(self) -> None:
        value = review('a', 'b', 'c', 'd', 'e', 'f', '0', '1')
        baseline = split_recordings(value, inspections(value))
        previous_holdout = {item.recording_id for item in baseline.recordings if item.split == 'holdout'}
        rows = [item.model_copy(update={'instrument': 'Fixture A' if item.id in previous_holdout else 'Fixture B',
            'technique': 'Reviewed fixture articulation'}) for item in value.recordings]
        actual = split_recordings(value.model_copy(update={'recordings': rows}), inspections(value))
        instruments = {item.id: item.instrument for item in rows}
        for split in ('train', 'holdout'):
            self.assertEqual({instruments[item.recording_id] for item in actual.recordings if item.split == split}, {'Fixture A', 'Fixture B'})

    def test_split_is_deterministic_and_evaluation_only_siblings_never_train(self) -> None:
        value = review('a', 'b', 'c', 'e')
        rows = list(value.recordings)
        rows[1] = rows[1].model_copy(update={'original_recording_sha256': rows[0].original_recording_sha256,
            'evaluation_only': True})
        value = value.model_copy(update={'recordings': rows})
        result = split_recordings(value, inspections(value))
        split = {item.recording_id: item.split for item in result.recordings}
        self.assertEqual((split['a' * 32], split['b' * 32]), ('holdout', 'holdout'))
        self.assertGreaterEqual(result.training_recordings, 1)
        self.assertEqual(result, split_recordings(value, list(reversed(inspections(value)))))

    def test_reference_only_permission_can_retain_evaluation_audio_without_training_permission(self) -> None:
        value = review('a', 'b', 'c')
        rows = list(value.recordings)
        evidence = rows[0].rights[0].model_copy(update={'permissions': rows[0].rights[0].permissions.model_copy(update={'training': 'blocked'})})
        rows[0] = rows[0].model_copy(update={'evaluation_only': True, 'rights': [evidence]})
        result = split_recordings(value.model_copy(update={'recordings': rows}), inspections(value))
        self.assertEqual(result.recordings[0].split, 'holdout')
        self.assertIsNone(result.recordings[0].exclusion_reason)

    def test_exact_decoded_copy_and_shared_performer_or_session_join_transitively(self) -> None:
        value = review('a', 'b', 'c')
        rows = list(value.recordings)
        rows[1] = rows[1].model_copy(update={'performer_group_sha256': rows[0].performer_group_sha256})
        rows[2] = rows[2].model_copy(update={'session_group_sha256': rows[1].session_group_sha256})
        result = split_recordings(value.model_copy(update={'recordings': rows}), inspections(value))
        self.assertEqual(result.independent_groups, 1)
        self.assertFalse(result.evaluation_sufficient)
        self.assertEqual(len({item.cluster_sha256 for item in result.recordings}), 1)
        actual = inspections(value)
        actual[1] = actual[1].model_copy(update={'decoded_pcm_sha256': actual[0].decoded_pcm_sha256})
        result = split_recordings(value, actual)
        self.assertEqual(result.recordings[0].cluster_sha256, result.recordings[1].cluster_sha256)

    def test_unknown_conflicting_and_restricted_rights_are_excluded_without_claiming_distribution(self) -> None:
        value = review('a', 'b', 'c', 'e')
        rows = list(value.recordings)
        rows[0] = rows[0].model_copy(update={'rights': []})
        restricted = rows[1].rights[0].model_copy(update={'restriction': 'noncommercial'})
        rows[1] = rows[1].model_copy(update={'rights': [restricted]})
        denied = rows[2].rights[0].model_copy(update={'id': 'f' * 32,
            'permissions': rows[2].rights[0].permissions.model_copy(update={'training': 'blocked'})})
        rows[2] = rows[2].model_copy(update={'rights': [rows[2].rights[0], denied]})
        result = split_recordings(value.model_copy(update={'recordings': rows}), inspections(value))
        reasons = {item.recording_id: item.exclusion_reason for item in result.recordings}
        self.assertEqual([reasons[item * 32] for item in ('a', 'b', 'c')], ['rights_unknown', 'restricted_license', 'rights_conflict'])
        self.assertFalse(any(item.adapter_distribution_permitted for item in result.recordings))
        self.assertIn('near_copy_detection_unavailable', result.warnings)

    def test_changed_original_hash_and_outside_excerpt_fail_before_any_split(self) -> None:
        value = review('a', 'b')
        actual = inspections(value)
        actual[0] = actual[0].model_copy(update={'source_sha256': 'f' * 64})
        with self.assertRaisesRegex(ValueError, 'regional_dataset_source_changed'):
            split_recordings(value, actual)
        actual = inspections(value)
        actual[0] = actual[0].model_copy(update={'frames': 1})
        with self.assertRaisesRegex(ValueError, 'regional_dataset_invalid'):
            split_recordings(value, actual)

    def test_renamed_record_ids_do_not_reshuffle_content_and_conflicting_exact_copies_stay_excluded(self) -> None:
        value = review('a', 'b', 'c')
        before = split_recordings(value, inspections(value))
        rows = [item.model_copy(update={'id': 'f' * 32}) if item.id == 'a' * 32 else item for item in value.recordings]
        excerpts = [item.model_copy(update={'recording_id': 'f' * 32}) if item.recording_id == 'a' * 32 else item for item in value.excerpts]
        renamed = value.model_copy(update={'recordings': rows, 'excerpts': excerpts})
        after = split_recordings(renamed, inspections(renamed))
        self.assertEqual([(item.cluster_sha256, item.split) for item in before.recordings],
            [(item.cluster_sha256, item.split) for item in after.recordings])
        denied = rows[1].rights[0].model_copy(update={'permissions': rows[1].rights[0].permissions.model_copy(update={'training': 'blocked'})})
        rows[1] = rows[1].model_copy(update={'rights': [denied]})
        altered = renamed.model_copy(update={'recordings': rows})
        actual = inspections(altered)
        actual[1] = actual[1].model_copy(update={'decoded_pcm_sha256': actual[0].decoded_pcm_sha256})
        result = split_recordings(altered, actual)
        self.assertEqual([item.split for item in result.recordings[:2]], ['excluded', 'excluded'])
