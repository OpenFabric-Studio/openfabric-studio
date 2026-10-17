from __future__ import annotations

import unittest
from pydantic import TypeAdapter, ValidationError

from app.music_analysis_contracts import AnalysisResult, MusicAnalysisId, MusicAnalysisJob, MusicAnalysisOverrides, MusicAnalysisRequest, MusicAnalysisSha256, MusicSourceRef


def result_payload() -> dict[str, object]:
    return {'id': 'a' * 32, 'source': {'kind': 'version', 'track_id': 1, 'version_id': 'b' * 32},
        'audio_sha256': 'c' * 64, 'sample_rate_hz': 48000, 'channels': 2, 'duration_frames': 480000,
        'duration_sec': 10.0, 'analysis_sample_rate_hz': 22050, 'hop_sec': 1024 / 22050,
        'analyzer_revision': 'numpy-summary-v1', 'configuration_sha256': 'd' * 64,
        'loudness': {'duration_sec': 10.0, 'source_sha256': 'c' * 64}, 'created_at': '2026-10-17T00:00:00Z'}


class MusicAnalysisContractsTests(unittest.TestCase):
    def test_identifiers_publish_exact_lengths_for_cross_language_regex_parity(self) -> None:
        for alias, length in ((MusicAnalysisId, 32), (MusicAnalysisSha256, 64)):
            schema = TypeAdapter(alias).json_schema()
            with self.subTest(length=length):
                self.assertEqual(schema.get('minLength'), length)
                self.assertEqual(schema.get('maxLength'), length)

    def test_newline_terminated_identifiers_and_digests_are_rejected(self) -> None:
        for alias, length in ((MusicAnalysisId, 32), (MusicAnalysisSha256, 64)):
            parser = TypeAdapter(alias)
            for suffix in ('\n', '\r\n'):
                with self.subTest(length=length, suffix=repr(suffix)), self.assertRaises(ValidationError):
                    parser.validate_python('a' * length + suffix)
        for change in ({'id': 'a' * 32 + '\n'}, {'audio_sha256': 'c' * 64 + '\n'},
                {'configuration_sha256': 'd' * 64 + '\n'},
                {'source': {'kind': 'version', 'track_id': 1, 'version_id': 'b' * 32 + '\n'}},
                {'source': {'kind': 'stem', 'track_id': 1, 'stem': 'vocals', 'sha256': 'c' * 64 + '\n'}}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                AnalysisResult.model_validate({**result_payload(), **change})

    def test_source_discriminator_is_required_at_request_and_result_boundaries(self) -> None:
        source = {'track_id': 1, 'version_id': 'b' * 32}
        with self.assertRaises(ValidationError):
            TypeAdapter(MusicSourceRef).validate_python(source)
        with self.assertRaises(ValidationError):
            MusicAnalysisRequest.model_validate({'source': source})
        with self.assertRaises(ValidationError):
            AnalysisResult.model_validate({**result_payload(), 'source': source})

    def test_source_union_is_strict_and_accepts_only_retained_identifiers(self) -> None:
        parser = TypeAdapter(MusicSourceRef)
        for source in ({'kind': 'version', 'track_id': 1, 'version_id': 'a' * 32},
                {'kind': 'stem', 'track_id': 2, 'stem': 'accompaniment', 'sha256': 'b' * 64},
                {'kind': 'production_asset', 'asset_id': 'c' * 32, 'sha256': 'd' * 64}):
            self.assertEqual(parser.validate_python(source).kind, source['kind'])
        for source in ({'kind': 'version', 'track_id': True, 'version_id': 'a' * 32},
                {'kind': 'version', 'track_id': '1', 'version_id': 'a' * 32},
                {'kind': 'stem', 'track_id': 1, 'stem': 'mix', 'sha256': 'b' * 64},
                {'kind': 'production_asset', 'asset_id': '../secret', 'sha256': 'd' * 64},
                {'kind': 'path', 'path': '/private/audio.wav'}):
            with self.subTest(source=source), self.assertRaises(ValidationError):
                parser.validate_python(source)
        with self.assertRaises(ValidationError):
            MusicAnalysisRequest.model_validate({'source': result_payload()['source'], 'profile': 'music-v1', 'path': '/private/audio.wav'})

    def test_result_reuses_audio_metrics_and_preserves_unknowns(self) -> None:
        result = AnalysisResult.model_validate(result_payload())
        self.assertEqual(result.loudness.measurement_method, 'ffmpeg-loudnorm-oversampled-v1')
        self.assertIsNone(result.key.tonic_pc)
        self.assertIsNone(result.tempo.bpm)
        self.assertIsNone(result.meter.beats_per_bar)
        self.assertEqual(result.vocal_activity.status, 'unknown')
        self.assertEqual(AnalysisResult.model_validate_json(result.model_dump_json()), result)
        with self.assertRaises(ValidationError):
            result.duration_sec = 12.0

    def test_invalid_numeric_units_unknown_claims_and_duration_mismatch_are_rejected(self) -> None:
        for change in ({'duration_sec': float('nan')}, {'duration_sec': float('inf')}, {'duration_sec': -1.0},
                {'duration_sec': 7201.0}, {'duration_sec': 11.0}, {'duration_frames': True}, {'channels': 3},
                {'tempo': {'status': 'unknown', 'bpm': 120.0}},
                {'key': {'status': 'unknown', 'tonic_pc': 0, 'mode': 'major'}},
                {'meter': {'status': 'unknown', 'beats_per_bar': 4}},
                {'key': {'status': 'known', 'tonic_pc': 12, 'mode': 'major'}},
                {'loudness': {'duration_sec': 10.0, 'source_sha256': 'f' * 64}},
                {'loudness': {'duration_sec': 10.0, 'source_sha256': 'c' * 64, 'full_scale_fraction': True}},
                {'created_at': '2026-02-30T00:00:00Z'}, {'output_path': '/private/output.wav'}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                AnalysisResult.model_validate({**result_payload(), **change})

    def test_beats_sections_and_energy_obey_order_and_source_bounds(self) -> None:
        section = {'id': 'e' * 32, 'start_sec': 0.0, 'end_sec': 4.0, 'group': 'A', 'label': 'A'}
        for change in ({'beats_sec': [1.0, 1.0]}, {'beats_sec': [2.0, 1.0]}, {'beats_sec': [-1.0]},
                {'beats_sec': [10.0]}, {'beats_sec': [1.0], 'downbeats_sec': [2.0]},
                {'sections': [section, {**section, 'id': 'f' * 32, 'start_sec': 3.0, 'end_sec': 6.0}]},
                {'sections': [{**section, 'end_sec': 11.0}]},
                {'energy': [{'time_sec': 10.0, 'rms_dbfs': -10.0, 'centroid_hz': 100.0, 'overlap_band_power': .5}]},
                {'energy': [{'time_sec': 0.0, 'rms_dbfs': float('nan'), 'centroid_hz': 100.0, 'overlap_band_power': .5}]},
                {'beats_sec': [float(index) / 10000 for index in range(50001)]},
                {'warnings': ['invented_warning']}, {'warnings': ['silence'], 'beats_sec': [1.0]}):
            with self.subTest(fields=list(change)), self.assertRaises(ValidationError):
                AnalysisResult.model_validate({**result_payload(), **change})

    def test_energy_allows_unclipped_float_pcm_rms_above_zero_dbfs(self) -> None:
        point = {'time_sec': 0.0, 'rms_dbfs': 24.082399653, 'centroid_hz': 0.0, 'overlap_band_power': 0.0}
        result = AnalysisResult.model_validate({**result_payload(), 'energy': [point]})
        self.assertEqual(result.energy[0].rms_dbfs, point['rms_dbfs'])
        with self.assertRaises(ValidationError):
            AnalysisResult.model_validate({**result_payload(), 'energy': [{**point, 'rms_dbfs': 25.01}]})

    def test_job_terminal_and_progress_state_is_consistent(self) -> None:
        base = {'id': 'e' * 32, 'source': result_payload()['source'], 'created_at': '2026-10-17T00:00:00Z'}
        self.assertIsNone(MusicAnalysisJob.model_validate(base).progress)
        for change in ({'status': 'done'}, {'status': 'failed'}, {'attempt': True},
                {'progress': {'current': 2, 'total': 1, 'unit': 'windows'}}, {'error_code': '/private/traceback'}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                MusicAnalysisJob.model_validate({**base, **change})
        job = MusicAnalysisJob.model_validate({**base, 'status': 'done', 'phase': 'complete', 'result': result_payload()})
        self.assertIsNotNone(job.result)

    def test_overrides_are_revisioned_and_do_not_mutate_raw_result(self) -> None:
        raw = AnalysisResult.model_validate(result_payload())
        override = MusicAnalysisOverrides(analysis_id=raw.id, expected_revision=0, beats_sec=[1.0, 2.0])
        self.assertEqual(override.expected_revision, 0)
        self.assertEqual(raw.beats_sec, [])
        for fields in ({}, {'expected_revision': True, 'beats_sec': []}, {'beats_sec': [2.0, 1.0]},
                {'key': {'status': 'unknown', 'tonic_pc': 0}}, {'path': '/private/audio.wav'}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                MusicAnalysisOverrides.model_validate({'analysis_id': raw.id, 'expected_revision': 0, **fields})


if __name__ == '__main__':
    unittest.main()
