from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from app.voice_measure import measure_audio


class VoiceAudioMeasurementTests(unittest.TestCase):
    def test_stereo_measurement_uses_all_channels_and_preserves_duration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "stereo.wav"
            audio = np.column_stack((np.full(8000, 0.5), np.zeros(8000)))
            sf.write(path, audio, 8000, subtype="FLOAT")
            measured = measure_audio(str(path), 0.75)
            self.assertAlmostEqual(measured["duration_sec"], 1.0)
            self.assertAlmostEqual(measured["duration_delta_sec"], 0.25)
            self.assertAlmostEqual(measured["level_db"], 20 * math.log10(math.sqrt(0.125)))
            self.assertAlmostEqual(measured["peak"], 0.5)
            self.assertEqual(measured["clipped_fraction"], 0.0)

    def test_silent_audio_has_finite_floor_and_no_full_scale_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "silence.wav"
            sf.write(path, np.zeros(8000), 8000)
            measured = measure_audio(str(path), 1.0)
            self.assertTrue(all(math.isfinite(value) for value in measured.values()))
            self.assertEqual(measured["level_db"], -120.0)
            self.assertEqual(measured["peak"], 0.0)
            self.assertEqual(measured["clipped_fraction"], 0.0)

    def test_full_scale_fraction_does_not_normalize_away_peaks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "peaks.wav"
            sf.write(path, np.array([1.2, -1.0, 0.0, 0.5]), 8000, subtype="FLOAT")
            measured = measure_audio(str(path), 4 / 8000)
            self.assertAlmostEqual(measured["peak"], 1.2, places=6)
            self.assertEqual(measured["clipped_fraction"], 0.5)

    def test_nonfinite_decoder_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid.wav"
            sf.write(path, np.array([0.0, np.nan]), 8000, subtype="FLOAT")
            with self.assertRaisesRegex(ValueError, "invalid_audio"):
                measure_audio(str(path), 1.0)
