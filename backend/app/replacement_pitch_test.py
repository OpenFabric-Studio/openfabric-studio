from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from app.music_dsp_contracts import WarpAnchor
from app.replacement_pitch_contracts import (DiagnosticArtifactIdentity, PitchWorkerArtifact,
    PitchWorkerInput)
from app.voice_contracts import VoiceCoverageMeasurement, VoicePitchBin
from app.voice_coverage import CoverageError
from app import replacement_pitch as pitch


def measured(note: int, seconds: float = 4.88) -> VoiceCoverageMeasurement:
    hz = 440 * 2 ** ((note - 69) / 12)
    return VoiceCoverageMeasurement(duration_sec=5.0, analyzed_sec=4.88, voiced_sec=seconds,
        reliable_voiced_sec=seconds, pitch_p05_hz=hz, pitch_median_hz=hz, pitch_p95_hz=hz,
        median_voicing_probability=.95, pitch_bins=[VoicePitchBin(midi_note=note, seconds=seconds)],
        source_sample_rate=16000)


class PitchSamplingTests(unittest.TestCase):
    def test_import_and_sampler_do_not_import_optional_dsp(self) -> None:
        result = subprocess.run([sys.executable, '-c',
            "import sys; from app.replacement_pitch import sample_windows; sample_windows(48000,48000); "
            "print([name for name in ('numpy','scipy','soundfile','librosa') if name in sys.modules])"],
            cwd=Path(__file__).parents[1], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), '[]')

    def test_short_is_covered_and_whole_song_sampling_is_disjoint_and_bounded(self) -> None:
        for seconds in (.01, 4.0, 15.0, 59.99, 60.0, 130.0, 7200.0):
            frames = round(seconds * 48000)
            with self.subTest(seconds=seconds):
                windows = pitch.sample_windows(frames, 48000)
                self.assertLessEqual(len(windows), 12)
                self.assertEqual(windows[0].start_frame, 0)
                self.assertEqual(windows[-1].end_frame, frames)
                self.assertLessEqual(sum(item.end_frame - item.start_frame for item in windows), 60 * 48000)
                self.assertTrue(all(item.end_frame - item.start_frame <= 5 * 48000 for item in windows))
                self.assertTrue(all(a.end_frame <= b.start_frame for a, b in zip(windows, windows[1:])))
                if seconds <= 60:
                    self.assertEqual(sum(item.end_frame - item.start_frame for item in windows), frames)
        for frames, rate in ((True, 48000), (1, False), (48000 * 7200 + 1, 48000), (1, 7000)):
            with self.subTest(frames=frames, rate=rate), self.assertRaises(ValueError):
                pitch.sample_windows(frames, rate)


class ReplacementPitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()

    def artifact(self, role: str, frequency: float = 220, seconds: float = 15.0, *,
                 antiphase: bool = False) -> PitchWorkerArtifact:
        from pydantic import TypeAdapter
        from app.replacement_pitch_contracts import PitchRole
        identifier = hashlib.sha256(role.encode()).hexdigest()[:32]
        time = np.arange(round(seconds * 16000), dtype=np.float32) / 16000
        wave = np.asarray(.2 * np.sin(2 * np.pi * frequency * time), dtype=np.float32)
        data = np.column_stack((wave, -wave)) if antiphase else wave[:, None]
        path = self.root / (identifier + '.wav')
        sf.write(path, data, 16000, subtype='FLOAT')
        return PitchWorkerArtifact(identity=DiagnosticArtifactIdentity(artifact_id=identifier,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), sample_rate_hz=16000,
            channels=2 if antiphase else 1, duration_frames=len(wave)), role=TypeAdapter(PitchRole).validate_python(role))

    def request(self, *artifacts: PitchWorkerArtifact, current: float = 0.0,
                reviewed: bool = True) -> PitchWorkerInput:
        return PitchWorkerInput(artifact_root=str(self.root), artifacts=list(artifacts),
            alignment_reviewed=reviewed, current_pitch_semitones=current)

    def test_actual_cpu_transposed_antiphase_take_proposes_total_setting_without_mutating_audio(self) -> None:
        reference = self.artifact('reference', 220)
        candidate = self.artifact('candidate', 220 * 2 ** (-2 / 12), antiphase=True)
        request = self.request(reference, candidate)
        result = pitch.analyze_pitch(request)
        self.assertEqual(result.artifacts[0].status, 'observed')
        self.assertAlmostEqual(result.artifacts[0].pitch_median_hz or 0, 220, delta=8)
        comparison = result.comparisons[0]
        self.assertEqual(comparison.supported_windows, 3)
        self.assertIsNotNone(comparison.recommendation)
        assert comparison.recommendation is not None
        self.assertAlmostEqual(comparison.recommendation.total_semitones, 2, delta=.21)
        self.assertTrue(comparison.recommendation.requires_review)
        self.assertIsNone(result.confidence)
        self.assertEqual(request.current_pitch_semitones, 0)
        for artifact in (reference, candidate):
            self.assertEqual(hashlib.sha256((self.root / (artifact.identity.artifact_id + '.wav')).read_bytes()).hexdigest(),
                artifact.identity.sha256)

    def test_silence_and_unavailable_estimator_remain_unknown(self) -> None:
        reference = self.artifact('reference', 0, seconds=1)
        silent = pitch.analyze_pitch(self.request(reference))
        self.assertEqual(silent.artifacts[0].unknown_reason, 'no_reliable_pitch')
        self.assertIsNone(silent.artifacts[0].pitch_median_hz)
        for error in (CoverageError('engine_incompatible'), ImportError('optional scipy unavailable')):
            with self.subTest(error=type(error).__name__), patch('app.voice_coverage.analyze_coverage', side_effect=error):
                unavailable = pitch.analyze_pitch(self.request(reference))
            self.assertEqual(unavailable.artifacts[0].unknown_reason, 'analysis_unavailable')
            self.assertIn('analysis_unavailable', unavailable.warnings)

    def test_actual_cpu_noise_has_no_invented_range_or_pitch_recommendation(self) -> None:
        reference = self.artifact('reference', seconds=1)
        path = self.root / (reference.identity.artifact_id + '.wav')
        sf.write(path, np.random.default_rng(42).normal(0, .05, (16000, 1)), 16000, subtype='FLOAT')
        identity = reference.identity.model_copy(update={'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        report = pitch.analyze_pitch(self.request(reference.model_copy(update={'identity': identity})))
        self.assertEqual(report.artifacts[0].status, 'unknown')
        self.assertIsNone(report.artifacts[0].pitch_median_hz)
        self.assertEqual(report.comparisons, [])

    def test_long_artifact_only_reads_twelve_disjoint_five_second_windows(self) -> None:
        reference = self.artifact('reference', seconds=130)
        lengths: list[int] = []
        def observed(samples: np.ndarray[tuple[int, ...], np.dtype[np.float32]], rate: int) -> VoiceCoverageMeasurement:
            lengths.append(len(samples))
            return measured(69)
        with patch('app.voice_coverage.analyze_coverage', side_effect=observed):
            report = pitch.analyze_pitch(self.request(reference))
        self.assertEqual(lengths, [5 * 16000] * 12)
        self.assertEqual(report.artifacts[0].sampled_sec, 60)
        self.assertEqual(report.artifacts[0].identity.duration_sec, 130)
        self.assertEqual(report.artifacts[0].windows[-1].window.end_frame, 130 * 16000)

    def test_unreviewed_alignment_and_inconsistent_or_out_of_bound_difference_never_recommend(self) -> None:
        reference, candidate = self.artifact('reference'), self.artifact('candidate')
        cases = ((False, [69, 69, 69, 67, 67, 67], 'alignment_unreviewed'),
            (True, [69, 69, 69, 67, 69, 71], 'inconsistent_pitch_difference'),
            (True, [69, 69, 69, 57, 57, 57], 'shift_outside_advisory_bound'))
        for reviewed, notes, reason in cases:
            with self.subTest(reason=reason), patch('app.voice_coverage.analyze_coverage',
                    side_effect=[measured(note) for note in notes]):
                report = pitch.analyze_pitch(self.request(reference, candidate, reviewed=reviewed))
                self.assertEqual(report.comparisons[0].reason, reason)
                self.assertIsNone(report.comparisons[0].recommendation)

    def test_existing_pitch_is_not_added_twice_and_conversion_drift_never_recommends(self) -> None:
        reference, candidate, converted = self.artifact('reference'), self.artifact('candidate'), self.artifact('converted')
        for applied in (0.0, 2.0):
            adjusted = candidate.model_copy(update={'applied_pitch_semitones': applied})
            notes = [69] * 3 + ([67] * 3 if applied == 0 else [69] * 3) + [72] * 3
            with self.subTest(applied=applied), patch('app.voice_coverage.analyze_coverage',
                    side_effect=[measured(note) for note in notes]):
                report = pitch.analyze_pitch(self.request(reference, adjusted, converted, current=2))
                self.assertEqual(report.comparisons[0].status, 'no_change')
                self.assertIsNone(report.comparisons[0].recommendation)
                self.assertEqual(report.comparisons[1].kind, 'conversion_drift')
                self.assertIsNone(report.comparisons[1].recommendation)

    def test_convert_mode_intentional_seven_semitone_input_has_only_conversion_drift(self) -> None:
        reference, candidate, converted = self.artifact('reference'), self.artifact('candidate'), self.artifact('converted')
        candidate = candidate.model_copy(update={'applied_pitch_semitones': 7.0})
        request = PitchWorkerInput(artifact_root=str(self.root), artifacts=[reference, candidate, converted],
            current_pitch_semitones=7.0, prepared_recommendation=False)
        with patch('app.voice_coverage.analyze_coverage', side_effect=[measured(note) for note in [69] * 3 + [76] * 6]):
            result = pitch.analyze_pitch(request)
        self.assertEqual(len(result.comparisons), 1)
        self.assertEqual(result.comparisons[0].kind, 'conversion_drift')
        self.assertEqual(result.comparisons[0].status, 'no_change')
        self.assertAlmostEqual(result.comparisons[0].median_delta_semitones or 0, 0)
        self.assertIsNone(result.comparisons[0].recommendation)

    def test_reviewed_nonzero_alignment_maps_native_windows_and_keeps_offsets(self) -> None:
        reference, candidate = self.artifact('reference', seconds=20), self.artifact('candidate', seconds=15)
        candidate = candidate.model_copy(update={'timeline': 'prepared'})
        request = PitchWorkerInput(artifact_root=str(self.root), artifacts=[reference, candidate], alignment_reviewed=True,
            alignment_anchors=[WarpAnchor(source_sec=0.0, target_sec=5.0), WarpAnchor(source_sec=15.0, target_sec=20.0)])
        with patch('app.voice_coverage.analyze_coverage', return_value=measured(69)):
            result = pitch.analyze_pitch(request)
        self.assertEqual(result.artifacts[0].windows[0].window.start_frame, 5 * 16000)
        self.assertEqual(result.artifacts[1].windows[0].window.start_frame, 0)
        self.assertEqual(result.comparisons[0].paired_windows, 3)

    def test_stretching_alignment_never_allocates_a_source_window_over_five_seconds(self) -> None:
        reference, candidate = self.artifact('reference', seconds=15), self.artifact('candidate', seconds=20)
        candidate = candidate.model_copy(update={'timeline': 'prepared'})
        request = PitchWorkerInput(artifact_root=str(self.root), artifacts=[reference, candidate], alignment_reviewed=True,
            alignment_anchors=[WarpAnchor(source_sec=0.0, target_sec=0.0), WarpAnchor(source_sec=20.0, target_sec=15.0)])
        def observed(samples: np.ndarray[tuple[int, ...], np.dtype[np.float32]], rate: int) -> VoiceCoverageMeasurement:
            seconds = len(samples) / rate
            supported = max(0.0, seconds - .12)
            hz = 220.0
            return VoiceCoverageMeasurement(duration_sec=seconds, analyzed_sec=supported, voiced_sec=supported,
                reliable_voiced_sec=supported, pitch_p05_hz=hz, pitch_median_hz=hz, pitch_p95_hz=hz,
                median_voicing_probability=.95, pitch_bins=[VoicePitchBin(midi_note=57, seconds=supported)], source_sample_rate=rate)
        with patch('app.voice_coverage.analyze_coverage', side_effect=observed):
            report = pitch.analyze_pitch(request)
        for artifact in report.artifacts:
            self.assertTrue(all(item.window.end_frame - item.window.start_frame <= 5 * 16000 for item in artifact.windows))
            self.assertLessEqual(artifact.sampled_sec, 60)
        self.assertEqual(report.comparisons[0].status, 'no_change')

    def test_stale_hash_header_symlink_and_nonfinite_pcm_fail_without_publication(self) -> None:
        reference = self.artifact('reference', seconds=1)
        with patch('app.voice_coverage.analyze_coverage') as estimator:
            changed = reference.model_copy(update={'identity': reference.identity.model_copy(update={'sha256': 'a' * 64})})
            with self.assertRaises(pitch.PitchDiagnosticError):
                pitch.analyze_pitch(self.request(changed))
            wrong_header = reference.model_copy(update={'identity': reference.identity.model_copy(update={'channels': 2})})
            with self.assertRaises(pitch.PitchDiagnosticError):
                pitch.analyze_pitch(self.request(wrong_header))
            estimator.assert_not_called()
        path = self.root / (reference.identity.artifact_id + '.wav')
        sf.write(path, np.full((16000, 1), np.nan, dtype=np.float32), 16000, subtype='FLOAT')
        changed = reference.model_copy(update={'identity': reference.identity.model_copy(update={
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})})
        with self.assertRaises(pitch.PitchDiagnosticError):
            pitch.analyze_pitch(self.request(changed))
        saved = self.root / 'saved.wav'
        path.rename(saved); path.symlink_to(saved)
        with self.assertRaises(pitch.PitchDiagnosticError):
            pitch.analyze_pitch(self.request(changed))

    def test_source_change_during_estimator_discards_entire_report(self) -> None:
        reference = self.artifact('reference')
        path = self.root / (reference.identity.artifact_id + '.wav')
        def altered(*args: object) -> VoiceCoverageMeasurement:
            with path.open('r+b') as handle:
                handle.seek(-4, 2); handle.write(b'\0\0\0\0')
            return measured(69)
        with patch('app.voice_coverage.analyze_coverage', side_effect=altered), self.assertRaises(pitch.PitchDiagnosticError):
            pitch.analyze_pitch(self.request(reference))
