"""Conservative cleanup must retain clean sustained timbre and dynamics."""
from __future__ import annotations

import unittest
import numpy as np
from app.clean_vocal import clean_vocal


class CleanupRegressionTests(unittest.TestCase):
    def test_clean_sustain_retains_harmonic_timbre(self):
        sr = 16000
        time = np.arange(3 * sr) / sr
        dry = (0.2 * np.sin(2 * np.pi * 220 * time) + 0.1 * np.sin(2 * np.pi * 440 * time))[:, None]
        cleaned = clean_vocal(dry, sr)
        relative_error = np.linalg.norm(cleaned[sr:-sr] - dry[sr:-sr]) / np.linalg.norm(dry[sr:-sr])
        self.assertLess(relative_error, 0.01)

    def test_cleanup_does_not_normalize_soft_and_loud_phrases(self):
        sr = 16000
        time = np.arange(sr) / sr
        phrase = 0.3 * np.sin(2 * np.pi * 220 * time)
        dry = np.concatenate([phrase, phrase * 0.1, phrase])[:, None]
        cleaned = clean_vocal(dry, sr)
        for start, end in [(sr // 4, 3 * sr // 4), (5 * sr // 4, 7 * sr // 4)]:
            dry_rms = np.sqrt(np.mean(dry[start:end] ** 2))
            wet_rms = np.sqrt(np.mean(cleaned[start:end] ** 2))
            self.assertAlmostEqual(float(wet_rms / dry_rms), 1.0, delta=0.01)

    def test_cleanup_is_bounded_and_does_not_mutate_original(self):
        sr = 16000
        rng = np.random.default_rng(4)
        audio = rng.normal(0, 0.1, (sr, 2)).astype(np.float32)
        original = audio.copy()
        cleaned = clean_vocal(audio, sr)
        np.testing.assert_array_equal(audio, original)
        self.assertEqual(cleaned.shape, original.shape)
        self.assertTrue(np.isfinite(cleaned).all())
        self.assertLessEqual(np.max(np.abs(cleaned)), np.max(np.abs(original)) + 1e-5)

    def test_short_clip_does_not_fail_stft_window_constraints(self):
        audio = np.array([[0.1], [-0.1]], dtype=np.float32)
        np.testing.assert_array_equal(clean_vocal(audio, 16000), audio)

    def test_nonfinite_audio_fails_before_generating_unusable_output(self):
        audio = np.array([[0.1], [np.nan]], dtype=np.float32)
        with self.assertRaisesRegex(ValueError, 'nonfinite_audio'):
            clean_vocal(audio, 16000)
