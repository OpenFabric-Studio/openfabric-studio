from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.voice_mix_contracts import VoiceMixSettings


class VoiceMixContractTests(unittest.TestCase):
    def test_opt_in_defaults_preserve_explicit_measurement_policy(self) -> None:
        settings = VoiceMixSettings()
        self.assertEqual(settings.vocal_level, 'match_original')
        self.assertEqual(settings.loudness.profile, 'off')
        self.assertEqual(settings.eq_bands, [])
        self.assertIsNone(settings.compression)

    def test_settings_reject_nonfinite_coercions_unknown_options_and_hidden_manual_gain(self) -> None:
        for body in ({'manual_gain_db': 1.0}, {'vocal_level': 'manual', 'manual_gain_db': 12.05},
                {'manual_gain_db': float('nan')}, {'manual_gain_db': True}, {'limiter': True},
                {'eq_bands': [{'frequency_hz': 100.0, 'gain_db': 6.0}, {'frequency_hz': 1000.0, 'gain_db': 1.0}]}):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                VoiceMixSettings.model_validate(body)

    def test_manual_level_is_an_explicit_bounded_choice(self) -> None:
        settings = VoiceMixSettings(vocal_level='manual', manual_gain_db=-6.0)
        self.assertEqual(settings.manual_gain_db, -6.0)
