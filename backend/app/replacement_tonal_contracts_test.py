from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.music_dsp_contracts import EqBand
from app.replacement_tonal_contracts import MatchingEqDiagnostic, TonalBand, BleedWindow, BleedDiagnostic


class TonalContractTests(unittest.TestCase):
    def test_suggestions_share_existing_eq_bounds_and_total_boost_budget(self) -> None:
        bands = [TonalBand(frequency_hz=frequency, status='supported', reference_db=-12.0,
            new_db=-14.0, difference_db=2.0, suggested_gain_db=3.0 if frequency in (125, 250) else 0.0)
            for frequency in (125, 250, 500, 1000, 2000, 4000)]
        report = MatchingEqDiagnostic(status='available', bands=bands,
            suggested_eq_bands=[EqBand(frequency_hz=125.0, gain_db=3.0),
                EqBand(frequency_hz=250.0, gain_db=3.0)], before_difference_db=2.0)
        self.assertEqual(sum(band.gain_db for band in report.suggested_eq_bands), 6.0)
        with self.assertRaises(ValidationError):
            MatchingEqDiagnostic.model_validate({**report.model_dump(),
                'suggested_eq_bands': [{'frequency_hz': 125.0, 'q': 1.0, 'gain_db': 6.0},
                    {'frequency_hz': 250.0, 'q': 1.0, 'gain_db': 1.0}]})

    def test_unsupported_band_never_contains_an_invented_measurement_or_gain(self) -> None:
        TonalBand(frequency_hz=4000, status='unsupported', reason='bandwidth_unavailable')
        with self.assertRaises(ValidationError):
            TonalBand(frequency_hz=4000, status='unsupported', reason='bandwidth_unavailable',
                reference_db=-20.0, suggested_gain_db=2.0)

    def test_absolute_correlation_must_match_signed_measurement_and_lag(self) -> None:
        result = BleedWindow(window_index=0, active_samples=22050, vocal_channel=0,
            backing_channel=1, signed_correlation=-.8, absolute_correlation=.8,
            lag_frames=2205, lag_ms=100.0, polarity='inverted')
        self.assertEqual(result.polarity, 'inverted')
        for update in ({'absolute_correlation': .1}, {'lag_ms': 10.0}, {'polarity': 'positive'}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                BleedWindow.model_validate({**result.model_dump(), **update})

    def test_strength_summary_cannot_disagree_with_actual_window_measurements(self) -> None:
        observation = BleedWindow(window_index=0, active_samples=22050, vocal_channel=0,
            backing_channel=0, signed_correlation=.8, absolute_correlation=.8,
            lag_frames=0, lag_ms=0.0, polarity='positive')
        with self.assertRaises(ValidationError):
            BleedDiagnostic(vocal_artifact_id='a' * 32, backing_artifact_id='b' * 32,
                status='measured', correlation_strength='weak', median_absolute_correlation=.1,
                windows=[observation])

    def test_suggested_bands_cannot_disagree_with_displayed_actual_corrections(self) -> None:
        bands = [TonalBand(frequency_hz=frequency, status='supported', reference_db=-12.0,
            new_db=-14.0, difference_db=2.0, suggested_gain_db=0.0)
            for frequency in (125, 250, 500, 1000, 2000, 4000)]
        with self.assertRaises(ValidationError):
            MatchingEqDiagnostic(status='available', bands=bands,
                suggested_eq_bands=[EqBand(frequency_hz=125.0, gain_db=3.0)], before_difference_db=0.0)


if __name__ == '__main__':
    unittest.main()
