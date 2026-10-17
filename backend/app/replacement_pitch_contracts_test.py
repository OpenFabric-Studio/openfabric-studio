from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.replacement_pitch_contracts import (DiagnosticArtifactIdentity, DiagnosticWindow,
    PitchArtifactReport, PitchRecommendation, PitchWorkerArtifact, PitchWorkerInput,
    ReplacementPitchReport)


class PitchContractTests(unittest.TestCase):
    def test_common_identity_and_half_open_window_are_strict_and_bounded(self) -> None:
        body = {'artifact_id': 'a' * 32, 'sha256': 'b' * 64, 'sample_rate_hz': 48000,
            'channels': 2, 'duration_frames': 48000}
        self.assertEqual(DiagnosticArtifactIdentity.model_validate(body).duration_sec, 1)
        for update in ({'artifact_id': 'a' * 33}, {'sha256': 'no'}, {'channels': True},
                       {'duration_frames': 48000 * 7200 + 1}, {'sample_rate_hz': 7999}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                DiagnosticArtifactIdentity.model_validate({**body, **update})
        for start, end in ((1, 1), (2, 1), (-1, 1), (False, 1)):
            with self.subTest(start=start, end=end), self.assertRaises(ValidationError):
                DiagnosticWindow(start_frame=start, end_frame=end)

    def test_recommendation_cannot_exceed_total_bound_or_claim_automatic_acceptance(self) -> None:
        suggestion = PitchRecommendation(pitch_shift_semitones=2, pitch_nudge_semitones=0.0)
        self.assertTrue(suggestion.requires_review)
        for body in ({'pitch_shift_semitones': 2, 'pitch_nudge_semitones': .2},
                     {'pitch_shift_semitones': 0, 'pitch_nudge_semitones': float('nan')},
                     {'pitch_shift_semitones': 1, 'pitch_nudge_semitones': 0.0, 'requires_review': False}):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                PitchRecommendation.model_validate(body)

    def test_worker_input_rejects_duplicate_roles_and_unbound_timeline(self) -> None:
        identity = DiagnosticArtifactIdentity(artifact_id='a' * 32, sha256='b' * 64,
            sample_rate_hz=48000, channels=1, duration_frames=48000)
        reference = PitchWorkerArtifact(identity=identity, role='reference')
        self.assertEqual(PitchWorkerInput(artifact_root='/private/test', artifacts=[reference]).artifacts, [reference])
        for inputs in ([reference, reference], [PitchWorkerArtifact(identity=identity, role='candidate')]):
            with self.subTest(inputs=inputs), self.assertRaises(ValidationError):
                PitchWorkerInput(artifact_root='/private/test', artifacts=inputs)

    def test_report_rejects_recommendation_over_unknown_artifacts_or_wrong_roles(self) -> None:
        artifacts = [{'identity': {'artifact_id': letter * 32, 'sha256': letter * 64,
            'sample_rate_hz': 48000, 'channels': 1, 'duration_frames': 720000},
            'role': role, 'applied_pitch_semitones': 0.0, 'status': 'unknown',
            'unknown_reason': 'no_reliable_pitch', 'sampled_sec': 0.0, 'analyzed_sec': 0.0,
            'voiced_sec': 0.0, 'reliable_voiced_sec': 0.0} for letter, role in (('a', 'reference'), ('b', 'candidate'))]
        body = {'configuration_sha256': 'c' * 64, 'runtime': {'python': '3.12'}, 'artifacts': artifacts,
            'comparisons': [{'kind': 'prepared_fit', 'reference_artifact_id': 'a' * 32,
                'candidate_artifact_id': 'b' * 32, 'status': 'observed', 'paired_windows': 3,
                'supported_windows': 3, 'median_delta_semitones': 1.0, 'spread_semitones': 0.0,
                'recommendation': {'pitch_shift_semitones': 1, 'pitch_nudge_semitones': 0.0}}]}
        with self.assertRaises(ValidationError):
            ReplacementPitchReport.model_validate(body)

    def test_observed_aggregate_pitch_quantiles_must_be_ordered(self) -> None:
        body = {'identity': {'artifact_id': 'a' * 32, 'sha256': 'b' * 64,
                'sample_rate_hz': 48000, 'channels': 1, 'duration_frames': 240000},
            'role': 'reference', 'applied_pitch_semitones': 0.0, 'status': 'observed',
            'sampled_sec': 5.0, 'analyzed_sec': 4.88, 'voiced_sec': 4.88, 'reliable_voiced_sec': 4.88,
            'pitch_p05_hz': 440.0, 'pitch_median_hz': 220.0, 'pitch_p95_hz': 330.0,
            'windows': [{'window': {'start_frame': 0, 'end_frame': 240000}, 'analyzed_sec': 4.88,
                'voiced_sec': 4.88, 'reliable_voiced_sec': 4.88, 'pitch_p05_hz': 220.0,
                'pitch_median_hz': 220.0, 'pitch_p95_hz': 220.0, 'median_voicing_probability': .95}]}
        with self.assertRaises(ValidationError):
            PitchArtifactReport.model_validate(body)
