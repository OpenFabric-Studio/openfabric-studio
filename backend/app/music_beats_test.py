from __future__ import annotations

import math
import unittest

from pydantic import ValidationError

from app.music_beats import BeatWindowResult, TimedBeatWindow, stitch_windows


class MusicBeatsTests(unittest.TestCase):
    def test_rejects_unbounded_unsorted_nonfinite_or_detached_downbeats(self) -> None:
        for changes in ({'beats_sec': [1.0, 1.0]}, {'beats_sec': [math.nan]},
                        {'beats_sec': [10.0]}, {'downbeats_sec': [1.5]},
                        {'beats_sec': [True]}, {'duration_sec': 121.0}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                BeatWindowResult.model_validate({'duration_sec': 10.0, 'beats_sec': [1.0],
                    'downbeats_sec': [], **changes})

    def test_keeps_central_events_offsets_and_explicit_boundary_uncertainty(self) -> None:
        first = BeatWindowResult(duration_sec=120.0, beats_sec=[float(n) / 2 for n in range(240)],
            downbeats_sec=[float(n) * 2 for n in range(60)])
        second = BeatWindowResult(duration_sec=20.0, beats_sec=[float(n) / 2 for n in range(40)],
            downbeats_sec=[float(n) * 2 for n in range(10)])
        result = stitch_windows([TimedBeatWindow(0.0, 0.0, 115.0, first),
            TimedBeatWindow(110.0, 115.0, 130.0, second)], 130.0)
        self.assertEqual(result.beats_sec, [float(n) / 2 for n in range(260)])
        self.assertEqual(result.tempo.bpm, 120.0)
        self.assertIsNone(result.tempo.confidence)
        self.assertTrue(result.tempo.half_double_ambiguous)
        self.assertIn('window_boundary_uncertain', result.warnings)
        self.assertIn('meter_unknown', result.warnings)
        self.assertEqual(result.meter.status, 'unknown')

    def test_irregular_or_short_grid_does_not_fabricate_constant_tempo(self) -> None:
        for beats in ([0.0, 1.0], [0.0, .5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0]):
            result = stitch_windows([TimedBeatWindow(0.0, 0.0, 10.0,
                BeatWindowResult(duration_sec=10.0, beats_sec=beats))], 10.0)
            self.assertEqual(result.tempo.status, 'unknown')
            self.assertIsNone(result.tempo.confidence)

    def test_empty_model_output_leaves_grid_unknown(self) -> None:
        result = stitch_windows([TimedBeatWindow(0.0, 0.0, 10.0,
            BeatWindowResult(duration_sec=10.0))], 10.0)
        self.assertEqual(result.beats_sec, [])
        self.assertEqual(result.tempo.status, 'unknown')
        self.assertIn('beat_grid_uncertain', result.warnings)

    def test_window_gaps_or_overlap_are_rejected_instead_of_hidden(self) -> None:
        for start in (4.0, 6.0):
            with self.subTest(start=start), self.assertRaises(ValueError):
                stitch_windows([TimedBeatWindow(0.0, 0.0, 5.0, BeatWindowResult(duration_sec=6.0)),
                    TimedBeatWindow(4.0, start, 10.0, BeatWindowResult(duration_sec=6.0))], 10.0)

    def test_boundary_duplicate_is_deduplicated_and_downbeat_stays_bound(self) -> None:
        result = stitch_windows([TimedBeatWindow(0.0, 0.0, 5.0,
            BeatWindowResult(duration_sec=6.0, beats_sec=[4.99], downbeats_sec=[4.99])),
            TimedBeatWindow(4.0, 5.0, 10.0,
            BeatWindowResult(duration_sec=6.0, beats_sec=[1.0], downbeats_sec=[1.0]))], 10.0)
        self.assertEqual(result.beats_sec, [4.99])
        self.assertEqual(result.downbeats_sec, [4.99])
