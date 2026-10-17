from __future__ import annotations

import math
import unittest
from unittest.mock import patch

import numpy as np
from numpy.typing import NDArray

from app.music_analysis_features import MusicAnalysisFeatureError, analyze_features


RATE = 22050


def tones(frequencies: tuple[float, ...], seconds: float = 4.0, amplitude: float = .5) -> NDArray[np.float32]:
    times = np.arange(round(seconds * RATE), dtype=np.float64) / RATE
    signal = sum((np.sin(2 * math.pi * frequency * times) for frequency in frequencies), np.zeros_like(times))
    return np.asarray(signal * amplitude / len(frequencies), dtype=np.float32)[:, None]


class MusicAnalysisFeaturesTests(unittest.TestCase):
    def test_silence_preserves_all_musical_unknowns_without_nan(self) -> None:
        result = analyze_features(np.zeros((RATE * 2, 2), dtype=np.float32), RATE)
        self.assertIn('silence', result.warnings)
        self.assertEqual(result.key.status, 'unknown')
        self.assertIsNone(result.key.tonic_pc)
        self.assertIsNone(result.tempo.bpm)
        self.assertIsNone(result.meter.beats_per_bar)
        self.assertEqual(result.beats_sec, [])
        self.assertEqual(result.downbeats_sec, [])
        self.assertEqual(result.vocal_activity.status, 'unknown')
        self.assertNotIn('NaN', result.model_dump_json())
        self.assertTrue(all(point.rms_dbfs == -200.0 and point.centroid_hz == 0.0 for point in result.energy))

    def test_clicks_produce_energy_without_manufacturing_meter_or_vocals(self) -> None:
        samples = np.zeros((RATE * 4, 1), dtype=np.float32)
        samples[::RATE // 2] = 1
        result = analyze_features(samples, RATE)
        self.assertTrue(any(point.rms_dbfs > -60 for point in result.energy))
        self.assertEqual(result.meter.status, 'unknown')
        self.assertEqual(result.vocal_activity.intervals, [])
        self.assertEqual(result.key.status, 'unknown')
        self.assertIsNone(result.tempo.confidence)

    def test_tone_has_expected_rms_centroid_and_overlap_band_fraction(self) -> None:
        result = analyze_features(tones((1000.0,), amplitude=.5), RATE)
        point = result.energy[0]
        self.assertAlmostEqual(point.rms_dbfs, 20 * math.log10(.5 / math.sqrt(2)), delta=.02)
        self.assertAlmostEqual(point.centroid_hz, 1000.0, delta=2.0)
        self.assertGreater(point.overlap_band_power, .99)

    def test_float_pcm_overshoot_preserves_samples_and_true_rms_through_amplitude_sixteen(self) -> None:
        for amplitude in (1.5, 16.0, -16.0):
            samples = np.full((RATE * 2, 1), amplitude, dtype=np.float32)
            original = samples.copy()
            with self.subTest(amplitude=amplitude):
                result = analyze_features(samples, RATE)
                self.assertAlmostEqual(result.energy[0].rms_dbfs, 20 * math.log10(abs(amplitude)), places=6)
                self.assertNotIn('NaN', result.model_dump_json())
                np.testing.assert_array_equal(samples, original)

    def test_supported_odd_sample_rate_preserves_its_time_units(self) -> None:
        result = analyze_features(np.zeros((8001, 1), dtype=np.float32), 8001)
        self.assertEqual(result.analysis_sample_rate_hz, 8001)
        self.assertEqual(result.duration_sec, 1.0)
        self.assertEqual(result.hop_sec, 1024 / 8001)

    def test_minor_triad_candidates_do_not_default_to_relative_major(self) -> None:
        result = analyze_features(tones((440.0, 523.251, 659.255)), RATE)
        self.assertTrue(result.key.candidates)
        self.assertEqual((result.key.candidates[0].tonic_pc, result.key.candidates[0].mode), (9, 'minor'))
        self.assertIsNone(result.key.confidence)
        self.assertNotEqual((result.key.tonic_pc, result.key.mode), (0, 'major'))

    def test_clear_major_triad_has_an_uncalibrated_known_candidate(self) -> None:
        result = analyze_features(tones((261.626, 329.628, 391.995)), RATE)
        self.assertEqual((result.key.status, result.key.tonic_pc, result.key.mode), ('known', 0, 'major'))
        self.assertIsNone(result.key.confidence)
        self.assertEqual(result.key.method, 'triad-weighted-diatonic-correlation-v1')

    def test_detuned_triads_and_repeated_noise_remain_unknown(self) -> None:
        detuned = tones(tuple(frequency * 2 ** (40 / 1200) for frequency in (440.0, 523.251, 659.255)))
        noise = np.random.default_rng(17).uniform(-.5, .5, (RATE, 1)).astype(np.float32)
        for samples in (detuned, np.tile(noise, (4, 1))):
            with self.subTest(frames=len(samples)):
                result = analyze_features(samples, RATE)
                self.assertEqual(result.key.status, 'unknown')
                self.assertIsNone(result.key.confidence)

    def test_antiphase_stereo_retains_power_and_spectral_evidence(self) -> None:
        mono = tones((261.626, 329.628, 391.995))
        stereo = np.concatenate((mono, -mono), axis=1)
        original = stereo.copy()
        reference, result = analyze_features(mono, RATE), analyze_features(stereo, RATE)
        np.testing.assert_array_equal(stereo, original)
        self.assertAlmostEqual(result.energy[0].rms_dbfs, reference.energy[0].rms_dbfs, places=6)
        self.assertAlmostEqual(result.energy[0].centroid_hz, reference.energy[0].centroid_hz, places=6)
        self.assertEqual(result.key.candidates, reference.key.candidates)
        self.assertNotIn('silence', result.warnings)

    def test_independent_stereo_keys_warn_and_remain_unknown(self) -> None:
        samples = np.concatenate((tones((261.626, 329.628, 391.995)), tones((369.994, 466.164, 554.365))), axis=1)
        result = analyze_features(samples, RATE)
        self.assertIn('channel_disagreement', result.warnings)
        self.assertEqual(result.key.status, 'unknown')

    def test_stable_local_key_disagreement_invalidates_a_single_global_key(self) -> None:
        samples = np.concatenate((tones((261.626, 329.628, 391.995)), tones((369.994, 466.164, 554.365), amplitude=.1)))
        result = analyze_features(samples, RATE)
        self.assertIn('key_changes', result.warnings)
        self.assertEqual(result.key.status, 'unknown')
        self.assertEqual(result.key.unknown_reason, 'unstable')
        self.assertEqual([(change.key.tonic_pc, change.key.mode) for change in result.key_changes], [(0, 'major'), (6, 'major')])

    def test_generic_sections_group_repeated_regions_without_semantic_labels(self) -> None:
        a = tones((220.0,), 4.0, .5)
        b = tones((1760.0,), 4.0, .06)
        samples = np.concatenate((a, b, a))
        first, second = analyze_features(samples, RATE), analyze_features(samples, RATE)
        self.assertEqual(first, second)
        self.assertEqual([section.label for section in first.sections], ['A', 'B', 'A'])
        self.assertAlmostEqual(first.sections[0].end_sec, 4.0, delta=1.1)
        self.assertAlmostEqual(first.sections[1].end_sec, 8.0, delta=1.1)
        self.assertTrue(all(section.musical_label is None and section.bars is None and section.confidence is None for section in first.sections))

    def test_subhop_audio_is_bounded_and_marked_short(self) -> None:
        for frames in (1, 100, RATE // 10):
            with self.subTest(frames=frames):
                result = analyze_features(np.zeros((frames, 1), dtype=np.float32), RATE)
                self.assertIn('short_audio', result.warnings)
                self.assertLessEqual(len(result.energy), 3)
                self.assertAlmostEqual(result.duration_sec, frames / RATE)

    def test_invalid_and_oversized_inputs_fail_before_fft(self) -> None:
        too_long = np.broadcast_to(np.zeros((1, 2), dtype=np.float32), (RATE * 120 + 1, 2))
        for samples, rate in ((np.zeros((0, 1), dtype=np.float32), RATE),
                (np.zeros((100, 3), dtype=np.float32), RATE), (np.zeros(100, dtype=np.float32), RATE),
                (np.full((100, 1), np.nan, dtype=np.float32), RATE), (np.full((100, 1), np.inf, dtype=np.float32), RATE),
                (np.full((100, 1), 16.1, dtype=np.float32), RATE),
                (np.full((100, 1), np.finfo(np.float32).max, dtype=np.float32), RATE), (np.zeros((100, 1), dtype=np.float64), RATE),
                (np.zeros((100, 1), dtype=np.float32), True), (np.zeros((100, 1), dtype=np.float32), 192000),
                (too_long, RATE)):
            with self.subTest(shape=samples.shape, rate=rate), patch.object(np.fft, 'rfft', side_effect=AssertionError('FFT must not start')), \
                    self.assertRaises(MusicAnalysisFeatureError):
                analyze_features(samples, rate)

    def test_long_window_has_bounded_fft_allocations_and_output(self) -> None:
        samples = np.broadcast_to(np.zeros((1, 2), dtype=np.float32), (RATE * 120, 2))
        original = np.fft.rfft
        sizes: list[int] = []
        def bounded(values: NDArray[np.float64], axis: int = -1) -> NDArray[np.complex128]:
            sizes.append(values.size)
            return original(values, axis=axis)
        with patch.object(np.fft, 'rfft', side_effect=bounded):
            result = analyze_features(samples, RATE)
        self.assertLessEqual(max(sizes), 8192)
        self.assertLessEqual(len(result.energy), 4000)
        self.assertLess(len(result.model_dump_json()), 1024**2)


if __name__ == '__main__':
    unittest.main()
