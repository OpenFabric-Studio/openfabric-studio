"""Measured voice timing excludes warm-up and never invents legacy timestamps."""
from __future__ import annotations

import unittest

from app.voice_progress import VoiceProgressTracker, finish_progress, read_progress


class VoiceProgressTests(unittest.TestCase):
    def test_two_comparable_deltas_required_and_warmup_excluded(self) -> None:
        tracker = VoiceProgressTracker.create('build', 'review', now=10)
        tracker.start(now=20)
        tracker.phase('training', total=200, unit='steps', now=30)
        tracker.advance(1, now=130)  # First completed step can include model warm-up.
        self.assertIsNone(tracker.progress.estimated_phase_remaining_sec)
        tracker.advance(2, now=132)
        self.assertIsNone(tracker.progress.estimated_phase_remaining_sec)
        tracker.advance(3, now=134)
        self.assertEqual(tracker.progress.estimated_phase_remaining_sec, 394)
        tracker.phase('publishing', total=1, unit='tasks', now=135)
        self.assertIsNone(tracker.progress.estimated_phase_remaining_sec)

    def test_resume_baseline_and_duplicate_polls_do_not_create_estimate(self) -> None:
        tracker = VoiceProgressTracker.create('build', 'review', now=1)
        tracker.start(now=2)
        tracker.phase('training', current=200, total=500, unit='steps', now=3)
        tracker.advance(200, now=9)
        tracker.advance(201, now=10)
        tracker.advance(201, now=20)
        self.assertIsNone(tracker.progress.estimated_phase_remaining_sec)
        tracker.advance(202, now=22)
        self.assertIsNone(tracker.progress.estimated_phase_remaining_sec)
        tracker.advance(203, now=24)
        self.assertIsNotNone(tracker.progress.estimated_phase_remaining_sec)

    def test_finish_and_reload_preserve_original_timestamps(self) -> None:
        tracker = VoiceProgressTracker.create('preparation', 'review', now=10)
        tracker.start(now=11)
        tracker.phase('slicing', total=5, unit='samples', now=12)
        tracker.advance(2, now=13)
        finish_progress(tracker.progress, 'cancelled', now=15)
        finish_progress(tracker.progress, 'cancelled', now=99)
        reloaded = read_progress(tracker.progress.model_dump(mode='json'))
        self.assertIsNotNone(reloaded)
        if reloaded is not None:
            self.assertEqual(reloaded.started_at, 11)
            self.assertEqual(reloaded.finished_at, 15)
            self.assertEqual(reloaded.phase_current, 2)
            self.assertIsNone(reloaded.estimated_phase_remaining_sec)

    def test_legacy_or_invalid_timing_remains_unknown(self) -> None:
        self.assertIsNone(read_progress(None))
        self.assertIsNone(read_progress({'queued_at': 'invented'}))

    def test_cleanup_terminal_status_changes_without_restarting_finished_clock(self) -> None:
        tracker = VoiceProgressTracker.create('apply', '', now=10)
        tracker.start(now=11)
        tracker.phase('converting', total=8, unit='chunks', now=12)
        tracker.finish('failed', now=15)
        tracker.progress.queue_reason = 'gpu_busy'
        tracker.progress.queue_label = 'Waiting'
        tracker.progress.estimated_phase_remaining_sec = 90
        tracker.finish('cancelled', now=99)
        self.assertEqual(tracker.progress.status, 'cancelled')
        self.assertEqual(tracker.progress.finished_at, 15)
        self.assertEqual(tracker.progress.queue_reason, '')
        self.assertEqual(tracker.progress.queue_label, '')
        self.assertIsNone(tracker.progress.estimated_phase_remaining_sec)
        tracker.finish('failed', now=120)
        self.assertEqual(tracker.progress.status, 'failed')
        self.assertEqual(tracker.progress.finished_at, 15)


if __name__ == '__main__':
    unittest.main()
