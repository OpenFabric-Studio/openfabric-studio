"""Structured chunk progress never treats a resetting diffusion bar as a job."""
from __future__ import annotations

import json
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.voice_apply_progress import ApplyLogReader, apply_event, parse_event
from app.voice_progress import VoiceProgressTracker


class ApplyProgressTests(unittest.TestCase):
    def event(self, phase: str, current: int = 0, total: int = 0, at: float = 1) -> str:
        return 'REMIQORA_PROGRESS ' + json.dumps({'phase': phase, 'current': current, 'total': total, 'at': at})

    def test_measured_chunk_eta_excludes_loading_analysis_and_first_chunk(self) -> None:
        tracker = VoiceProgressTracker.create('apply', '', now=1)
        tracker.start(now=2)
        for phase, stamp in [('loading', 2), ('analyzing', 62), ('converting', 122)]:
            event = parse_event(self.event(phase, total=8 if phase == 'converting' else 0))
            self.assertIsNotNone(event)
            if event is not None:
                apply_event(tracker, event, now=stamp)
        for current, stamp in [(1, 152), (2, 162), (3, 172)]:
            event = parse_event(self.event('converting', current, 8))
            if event is not None:
                apply_event(tracker, event, now=stamp)
        self.assertEqual(tracker.progress.phase_current, 3)
        self.assertEqual(tracker.progress.estimated_phase_remaining_sec, 50)
        self.assertIsNone(parse_event('100% 30/30 [00:01]'))

    def test_invalid_events_cannot_change_phase_or_counts(self) -> None:
        invalid = ['REMIQORA_PROGRESS nope', self.event('training'), self.event('converting', 4, 3),
                   self.event('converting', -1, 3), self.event('loading', 0, 2),
                   self.event('converting', 0, 100001), 'REMIQORA_PROGRESS ' + 'x' * 20000,
                   'REMIQORA_PROGRESS {"phase":"converting","current":true,"total":3,"at":1}']
        invalid.extend([self.event('loading', at=float('nan')), self.event('loading', at=float('inf'))])
        for value in invalid:
            with self.subTest(value=value[:80]):
                self.assertIsNone(parse_event(value))
        tracker = VoiceProgressTracker.create('apply', '', now=1)
        start = parse_event(self.event('converting', 0, 8))
        next_event = parse_event(self.event('converting', 3, 9))
        stale = parse_event(self.event('loading'))
        if start is not None and next_event is not None and stale is not None:
            apply_event(tracker, start, now=2)
            self.assertFalse(apply_event(tracker, next_event, now=3))
            self.assertFalse(apply_event(tracker, stale, now=4))
            self.assertEqual(tracker.progress.phase_total, 8)

    def test_buffered_events_use_engine_timestamps_for_measured_rate(self) -> None:
        tracker = VoiceProgressTracker.create('apply', '', now=1)
        for current, stamp in [(0, 10), (1, 100), (2, 110), (3, 120)]:
            event = parse_event(self.event('converting', current, 8, at=stamp))
            if event is not None:
                apply_event(tracker, event)
        self.assertEqual(tracker.progress.estimated_phase_remaining_sec, 50)

    def test_incremental_reader_keeps_split_lines_and_bounds_noisy_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'log'
            reader = ApplyLogReader(path)
            event = self.event('converting', 0, 8)
            path.write_text('warning\n' + event[:30])
            self.assertEqual(reader.read(), [])
            with path.open('a') as handle:
                handle.write(event[30:] + '\n')
            self.assertEqual(len(reader.read()), 1)
            self.assertEqual(reader.read(), [])
            with path.open('a') as handle:
                handle.write('x' * 100000 + '\n' + self.event('converting', 1, 8) + '\n')
            events = []
            for _ in range(4):
                events.extend(reader.read())
            self.assertEqual([item.current for item in events], [1])

    def test_truncated_and_replaced_logs_restart_without_old_partial_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'log'
            reader = ApplyLogReader(path)
            path.write_text('x' * 400 + self.event('loading')[:30])
            self.assertEqual(reader.read(), [])
            path.write_text(self.event('analyzing') + '\n')
            self.assertEqual([event.phase for event in reader.read()], ['analyzing'])
            replacement = path.with_suffix('.new')
            replacement.write_text(self.event('converting', 0, 8) + '\n')
            replacement.replace(path)
            self.assertEqual([event.phase for event in reader.read()], ['converting'])

    def test_stale_future_and_terminal_events_are_rejected(self) -> None:
        tracker = VoiceProgressTracker.create('apply', '', now=10)
        for stamp in [9, 100000000000]:
            event = parse_event(self.event('loading', at=stamp))
            if event is not None:
                self.assertFalse(apply_event(tracker, event))
        event = parse_event(self.event('converting', 0, 8, at=20))
        if event is not None:
            self.assertTrue(apply_event(tracker, event))
            tracker.finish('cancelled', now=21)
            self.assertFalse(apply_event(tracker, event, now=22))


class ApplyWatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_final_drain_preserves_events_after_bounded_noisy_reads(self) -> None:
        from app import voice_build
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = voice_build.ApplyJob(voice_id='a' * 32, track_id=1)
            job.timing = VoiceProgressTracker.create('apply', '', now=1)
            job.timing.start(now=2)
            job.timing.phase('loading', total=0, unit='tasks', now=2)
            def line(current: int, at: int) -> str:
                return 'REMIQORA_PROGRESS ' + json.dumps({'phase': 'converting', 'current': current, 'total': 8, 'at': at}) + '\n'
            (root / 'convert.log').write_text(line(0, 10) + 'noise\n' * 20000 + line(1, 100) + line(2, 110) + line(3, 120))
            stop = asyncio.Event()
            stop.set()
            with patch.object(voice_build, 'LOG_DIR', root), patch.object(voice_build, '_write_apply') as persist:
                await asyncio.wait_for(voice_build._watch_apply(job, 'convert', stop), timeout=3)
            self.assertGreaterEqual(persist.call_count, 2)
            self.assertEqual(job.phase, 'converting')
            self.assertEqual(job.timing.progress.phase_current, 3)
            self.assertEqual(job.timing.progress.estimated_phase_remaining_sec, 50)

    async def test_events_from_previous_attempt_do_not_relabel_current_job(self) -> None:
        from app import voice_build
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = voice_build.ApplyJob(voice_id='a' * 32, track_id=1)
            job.phase = 'loading'
            (root / 'convert.log').write_text('REMIQORA_PROGRESS ' + json.dumps(
                {'phase': 'converting', 'current': 8, 'total': 8, 'at': job.timing.progress.queued_at - 10}) + '\n')
            stop = asyncio.Event()
            stop.set()
            with patch.object(voice_build, 'LOG_DIR', root), patch.object(voice_build, '_write_apply') as persist:
                await asyncio.wait_for(voice_build._watch_apply(job, 'convert', stop), timeout=3)
            persist.assert_not_called()
            self.assertEqual(job.phase, 'loading')
            self.assertEqual(job.timing.progress.phase_current, 0)


if __name__ == '__main__':
    unittest.main()
