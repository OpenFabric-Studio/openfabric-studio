"""Silence tightening and quality selection. Runs with the Seed-VC Python."""
from __future__ import annotations

import unittest

import numpy as np

from app.prep_vocal import prepare_vocal


def _tone(sample_rate: int, seconds: float, freq: float, amp: float) -> np.ndarray:
    time = np.arange(int(sample_rate * seconds)) / sample_rate
    wave = amp * np.sin(2 * np.pi * freq * time)
    wave += 0.35 * amp * np.sin(2 * np.pi * freq * 2 * time)
    wave += 0.15 * amp * np.sin(2 * np.pi * freq * 3 * time)
    return wave.astype(np.float32)


def _noise(seconds: float, sample_rate: int, amp: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(0.0, amp, int(sample_rate * seconds)).astype(np.float32)


def _gap(seconds: float, sample_rate: int) -> np.ndarray:
    return np.zeros(int(sample_rate * seconds), dtype=np.float32)


def _max_internal_gap(audio: np.ndarray, sample_rate: int, thresh: float = 0.02) -> float:
    mono = audio.mean(axis=1) if audio.ndim == 2 else audio
    frame = max(1, int(0.02 * sample_rate))
    count = mono.shape[0] // frame
    if count <= 0:
        return 0.0
    rms = np.sqrt(np.mean(mono[: count * frame].reshape(count, frame) ** 2, axis=1))
    silent = rms < thresh
    best = 0
    run = 0
    runs: list[int] = []
    for flag in silent:
        if flag:
            run += 1
        elif run:
            runs.append(run)
            run = 0
    if run:
        runs.append(run)
    # Leading and trailing holes are not gaps between singing.
    if silent[0] and runs:
        runs = runs[1:]
    if silent[-1] and runs:
        runs = runs[:-1]
    best = max(runs) if runs else 0
    return best * frame / sample_rate


class SilenceTests(unittest.TestCase):
    def test_a_long_hole_shrinks_to_one_second(self):
        sample_rate = 44100
        audio = np.concatenate([
            _tone(sample_rate, 1.0, 220, 0.5),
            _gap(4.0, sample_rate),
            _tone(sample_rate, 1.0, 247, 0.5),
        ])
        prepared, report = prepare_vocal(audio[:, None], sample_rate)
        gap = _max_internal_gap(prepared, sample_rate)
        self.assertLessEqual(gap, 1.05)
        self.assertGreaterEqual(gap, 0.15)
        self.assertLessEqual(report["max_gap_sec"], 1.05)
        self.assertGreater(report["pitch"], 0.4)
        self.assertLess(report["output_seconds"], 2.7)
        self.assertGreater(report["output_seconds"], 1.9)

    def test_a_quiet_noise_floor_between_phrases_shrinks(self):
        sample_rate = 16000
        audio = np.concatenate([
            _tone(sample_rate, 1.0, 220, 0.5),
            _noise(4.0, sample_rate, 0.008, 4),
            _tone(sample_rate, 1.0, 247, 0.5),
        ])
        prepared, report = prepare_vocal(audio[:, None], sample_rate)
        gap = _max_internal_gap(prepared, sample_rate)
        self.assertLessEqual(gap, 1.05)
        self.assertLess(report["output_seconds"], 3.0)

    def test_a_short_breath_stays(self):
        sample_rate = 44100
        audio = np.concatenate([
            _tone(sample_rate, 1.0, 220, 0.5),
            _gap(0.4, sample_rate),
            _tone(sample_rate, 1.0, 220, 0.5),
        ])
        prepared, _report = prepare_vocal(audio[:, None], sample_rate)
        gap = _max_internal_gap(prepared, sample_rate)
        self.assertGreater(gap, 0.25)
        self.assertLess(gap, 0.55)

    def test_soft_singing_is_kept(self):
        sample_rate = 44100
        audio = np.concatenate([
            _tone(sample_rate, 1.0, 220, 0.55),
            _tone(sample_rate, 2.0, 196, 0.12),
            _tone(sample_rate, 1.0, 220, 0.55),
        ])
        prepared, report = prepare_vocal(audio[:, None], sample_rate)
        self.assertGreater(report["output_seconds"], 0.85 * (audio.shape[0] / sample_rate))


class QualityTests(unittest.TestCase):
    def test_sub_second_candidates_are_rejected_before_training_selection(self):
        from app.prep_vocal import analyze_segments
        report = analyze_segments(_tone(16000, .7, 220, .3), 16000)
        self.assertFalse(report[0].accepted)
        self.assertIn('too_short', report[0].reasons)

    def test_noise_between_phrases_is_dropped(self):
        sample_rate = 16000
        audio = np.concatenate([
            _noise(3.0, sample_rate, 0.2, 1),
            _gap(0.6, sample_rate),
            _tone(sample_rate, 3.0, 220, 0.45),
        ])
        prepared, report = prepare_vocal(audio[:, None], sample_rate, max_keep_seconds=60)
        self.assertGreater(prepared.shape[0], int(2.0 * sample_rate))
        self.assertLess(report["output_seconds"], 4.5)
        self.assertGreater(_pitch(prepared, sample_rate), 0.45)

    def test_a_long_mix_keeps_the_clearest_singing(self):
        sample_rate = 16000
        parts = []
        for index in range(6):
            parts.append(_tone(sample_rate, 2.0, 196 + index * 10, 0.5))
            parts.append(_gap(0.6, sample_rate))
            parts.append(_noise(2.0, sample_rate, 0.25, 10 + index))
            parts.append(_gap(0.6, sample_rate))
        audio = np.concatenate(parts)
        prepared, report = prepare_vocal(
            audio[:, None],
            sample_rate,
            limit_duration=True,
            max_keep_seconds=4.0,
        )
        self.assertLessEqual(report["output_seconds"], 6.5)
        self.assertGreater(report["output_seconds"], 3.0)
        self.assertGreater(_pitch(prepared, sample_rate), 0.45)

    def test_a_short_vocal_is_not_cut_down_to_the_cap(self):
        sample_rate = 16000
        audio = _tone(sample_rate, 4.0, 220, 0.4)
        _prepared, report = prepare_vocal(
            audio[:, None],
            sample_rate,
            limit_duration=True,
            max_keep_seconds=15 * 60,
        )
        self.assertGreater(report["output_seconds"], 3.4)


def _pitch(audio: np.ndarray, sample_rate: int) -> float:
    from app.prep_vocal import _pitch_strength

    mono = audio.mean(axis=1) if audio.ndim == 2 else audio
    return _pitch_strength(mono, sample_rate)


class ScreeningRegressionTests(unittest.TestCase):
    def test_noise_only_is_rejected_instead_of_retaining_best_failure(self):
        prepared, report = prepare_vocal(_noise(3.0, 16000, 0.15, 9), 16000)
        self.assertEqual(prepared.shape[0], 0)
        self.assertEqual(report['kept'], 0)
        self.assertGreater(report['dropped'], 0)

    def test_out_of_band_tone_cannot_alias_into_periodicity(self):
        from app.prep_vocal import _pitch_strength
        sr = 48000
        time = np.arange(sr) / sr
        ultrasonic = (0.3 * np.sin(2 * np.pi * 15800 * time)).astype(np.float32)
        self.assertLess(_pitch_strength(ultrasonic, sr), 0.1)

    def test_opposite_phase_stereo_is_not_silent(self):
        tone = _tone(16000, 2.0, 220, 0.3)
        prepared, report = prepare_vocal(np.column_stack([tone, -tone]), 16000)
        self.assertGreater(prepared.shape[0], 25000)
        self.assertGreater(report['pitch'], 0.4)
        np.testing.assert_allclose(prepared[:, 0], -prepared[:, 1], atol=1e-6)

    def test_segment_report_preserves_original_boundaries_and_is_not_identity_claim(self):
        from app import prep_vocal
        analyzer = getattr(prep_vocal, 'analyze_segments', None)
        self.assertTrue(callable(analyzer), 'typed original-time segment analyzer is missing')
        audio = np.concatenate([_gap(4.0, 16000), _tone(16000, 2.0, 220, 0.3)])
        segments = analyzer(audio, 16000)
        accepted = [segment for segment in segments if segment.accepted]
        self.assertEqual(len(accepted), 1)
        self.assertAlmostEqual(accepted[0].start_sec, 4.0, places=2)
        self.assertAlmostEqual(accepted[0].end_sec, 6.0, places=2)
        self.assertIn('identity_unverified', accepted[0].reasons)
        self.assertIn('periodicity_is_not_voice_detection', accepted[0].reasons)
        self.assertTrue(any('too_quiet' in segment.reasons for segment in segments))

    def test_clipped_material_has_explicit_rejection_reason(self):
        from app import prep_vocal
        analyzer = getattr(prep_vocal, 'analyze_segments', None)
        self.assertTrue(callable(analyzer), 'typed original-time segment analyzer is missing')
        segments = analyzer(np.ones(16000, dtype=np.float32), 16000)
        self.assertTrue(segments)
        self.assertFalse(segments[0].accepted)
        self.assertIn('clipped', segments[0].reasons)


class OriginalTimeBoundaryTests(unittest.TestCase):
    def test_nonfinite_segment_is_rejected_with_finite_metrics(self):
        from app.prep_vocal import analyze_segments
        audio = _tone(16000, 2, 220, 0.3)
        audio[200] = np.nan
        segments = analyze_segments(audio, 16000)
        self.assertFalse(segments[0].accepted)
        self.assertIn('nonfinite', segments[0].reasons)
        self.assertTrue(np.isfinite([segments[0].score, segments[0].level_db, segments[0].peak]).all())

    def test_rejected_long_silence_partitions_original_time_in_bounded_windows(self):
        from app.prep_vocal import analyze_segments
        segments = analyze_segments(_gap(25, 16000), 16000)
        self.assertEqual([(segment.start_sec, segment.end_sec) for segment in segments], [(0, 10), (10, 20), (20, 25)])
        self.assertTrue(all(not segment.accepted for segment in segments))

    def test_analyze_cli_reports_all_rejections_without_writing_audio(self):
        import contextlib
        import io
        import json
        import tempfile
        from pathlib import Path
        import soundfile as sf
        from app.prep_vocal import main
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'noise.wav'
            sf.write(path, _noise(2, 16000, 0.2, 10), 16000, subtype='FLOAT')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(['prep_vocal.py', '--analyze', str(path)])
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload['schema_version'], 2)
            self.assertEqual(payload['input_seconds'], 2)
            self.assertTrue(payload['segments'])
            self.assertTrue(all(not segment['accepted'] for segment in payload['segments']))
            self.assertEqual(list(Path(directory).iterdir()), [path])


class SafeMonoTests(unittest.TestCase):
    def test_antiphase_vocal_survives_public_downmix_without_mutating_original(self):
        from app import prep_vocal
        downmix = getattr(prep_vocal, 'safe_mono', None)
        self.assertTrue(callable(downmix), 'public safe_mono helper is missing')
        tone = _tone(16000, 2, 220, 0.3)
        stereo = np.column_stack([tone, -tone])
        before = stereo.copy()
        mono = downmix(stereo)
        np.testing.assert_array_equal(stereo, before)
        np.testing.assert_allclose(mono, tone, atol=1e-7)

    def test_mono_cli_preserves_antiphase_vocal_and_original_file(self):
        import tempfile
        from pathlib import Path
        import soundfile as sf
        from app.prep_vocal import main
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'original.wav'
            dest = Path(directory) / 'mono.wav'
            tone = _tone(16000, 2, 220, 0.3)
            sf.write(source, np.column_stack([tone, -tone]), 16000, subtype='FLOAT')
            original_bytes = source.read_bytes()
            self.assertEqual(main(['prep_vocal.py', '--mono', str(source), str(dest)]), 0)
            mono, rate = sf.read(dest, always_2d=True)
            self.assertEqual(mono.shape, (32000, 1))
            self.assertEqual(rate, 16000)
            np.testing.assert_allclose(mono[:, 0], tone, atol=1e-7)
            self.assertEqual(source.read_bytes(), original_bytes)

    def test_mono_cli_refuses_to_replace_original(self):
        import tempfile
        from pathlib import Path
        import soundfile as sf
        from app.prep_vocal import main
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'original.wav'
            sf.write(source, _tone(16000, 1, 220, 0.3), 16000, subtype='FLOAT')
            original = source.read_bytes()
            self.assertEqual(main(['prep_vocal.py', '--mono', str(source), str(source)]), 2)
            self.assertEqual(source.read_bytes(), original)
