"""Candidate screening fixtures; shared sound is not recording identity."""
from __future__ import annotations

import unittest
import numpy as np
from app import regional_acoustic_helper as acoustic


def phrase(seed: int = 4, seconds: int = 12) -> np.ndarray[tuple[int, ...], np.dtype[np.float32]]:
    rng = np.random.default_rng(seed)
    audio = np.zeros(seconds * 8000, dtype=np.float32)
    for start in range(0, len(audio), 2400):
        size = min(2400, len(audio) - start)
        times = np.arange(size) / 8000
        frequency = float(rng.choice([163, 227, 311, 419, 557, 733, 947, 1259]))
        envelope = np.sin(np.linspace(0, np.pi, size)) ** 2
        audio[start:start + size] = .3 * envelope * (np.sin(2 * np.pi * frequency * times)
            + .3 * np.sin(4 * np.pi * frequency * times))
    return audio[:, None]


class AcousticKernelTests(unittest.TestCase):
    def test_gain_polarity_pcm_quantization_and_shifted_crop_are_candidates(self) -> None:
        original = phrase()
        reference = acoustic.fingerprint_window(original)
        for transformed in (-original * .23, np.round(original * 32767).astype(np.int16).astype(np.float32) / 32767,
                original[2733:-4000]):
            with self.subTest(frames=len(transformed)):
                match = acoustic.compare_fingerprints(reference, acoustic.fingerprint_window(transformed))
                self.assertIsNotNone(match)
                assert match is not None
                self.assertGreaterEqual(match.distinct_anchors, 6)
                self.assertGreaterEqual(match.aligned_seconds, 3)

    def test_same_instrument_rhythm_and_duration_do_not_establish_shared_audio(self) -> None:
        self.assertIsNone(acoustic.compare_fingerprints(acoustic.fingerprint_window(phrase(4)),
            acoustic.fingerprint_window(phrase(8))))

    def test_modest_noise_eq_and_float_overshoot_remain_candidates(self) -> None:
        source = phrase()
        reference = acoustic.fingerprint_window(source)
        noise = np.random.default_rng(42).normal(0, .0005, source.shape).astype(np.float32)
        equalized = np.convolve(source[:, 0], [.2, .6, .2], mode='same').astype(np.float32)[:, None]
        for audio in (source + noise, equalized, source * 5):
            with self.subTest(peak=float(np.max(np.abs(audio)))):
                self.assertIsNotNone(acoustic.compare_fingerprints(reference, acoustic.fingerprint_window(audio)))

    def test_channel_evidence_is_kept_when_the_other_channel_disagrees(self) -> None:
        left = np.concatenate((phrase(4), phrase(8)), axis=1)
        right = np.concatenate((phrase(10), phrase(4)), axis=1)
        match = acoustic.compare_fingerprints(acoustic.fingerprint_window(left), acoustic.fingerprint_window(right))
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual((match.left_channel, match.right_channel), (0, 1))

    def test_dense_postings_deadlines_and_vote_caps_fail_instead_of_clearing(self) -> None:
        from unittest.mock import patch
        fingerprint = acoustic.fingerprint_window(phrase())
        for changes in ({'MAX_POSTINGS': 0}, {'MAX_PAIR_VOTES': 0}, {'MAX_HASHES': 1}):
            with self.subTest(changes=changes), patch.multiple(acoustic, **changes):
                with self.assertRaises(acoustic.AcousticBudgetError):
                    acoustic.compare_fingerprints(fingerprint, fingerprint)
        with self.assertRaises(acoustic.AcousticBudgetError):
            acoustic.compare_fingerprints(fingerprint, fingerprint, clock=lambda: 2., deadline=1.)

    def test_silence_constant_tone_short_audio_and_channel_cancellation_are_honest(self) -> None:
        for audio in (np.zeros((64000, 1), dtype=np.float32),
                np.sin(np.arange(64000, dtype=np.float32)[:, None] * .2) * .1, phrase()[:8000]):
            self.assertFalse(acoustic.fingerprint_window(audio).informative)
        source = phrase()
        stereo = np.concatenate((source, -source), axis=1)
        self.assertIsNotNone(acoustic.compare_fingerprints(acoustic.fingerprint_window(stereo),
            acoustic.fingerprint_window(stereo[:, ::-1].copy())))

    def test_nonfinite_oversized_and_out_of_range_input_fail_before_fft(self) -> None:
        from unittest.mock import patch
        for audio in (np.full((8000, 1), np.nan, dtype=np.float32),
                np.full((8000, 1), 17, dtype=np.float32), np.zeros((8000 * 120 + 1, 1), dtype=np.float32)):
            with self.subTest(shape=audio.shape), patch.object(np.fft, 'rfft', side_effect=AssertionError('FFT before bounds')):
                with self.assertRaises(acoustic.AcousticBudgetError):
                    acoustic.fingerprint_window(audio)
