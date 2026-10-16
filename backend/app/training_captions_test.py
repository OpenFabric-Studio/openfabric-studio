from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from app import db, training_captions as captions, resource_admission
from app.training_caption_contracts import TrainingCaptionRequest, TrainingCaptionJob, TrainingCaptionItem
from app.music_training_source_contracts import MusicLoraSource
from app.training_caption_runtime import CaptionError, CaptionSession


class TrainingCaptionJobsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for target, name, value in ((db, 'DATA_DIR', self.root), (captions, '_tasks', {}),
                (captions, '_quarantined', []), (captions, '_closing', False),
                (resource_admission, 'admission_lock', asyncio.Lock()), (resource_admission, '_native_inflight', 0)):
            self.enterContext(patch.object(target, name, value))
        self.enterContext(patch('app.work_busy.other_work_busy', new=AsyncMock(return_value=False)))
        self.enterContext(patch('app.video_jobs.work_busy', return_value=False))
        self.enterContext(patch.object(captions, 'source_path', return_value=self.root / 'source.wav'))
        self.enterContext(patch.object(CaptionSession, 'start', new=AsyncMock()))
        self.enterContext(patch.object(CaptionSession, 'close', new=AsyncMock()))
        self.analyze = self.enterContext(patch.object(CaptionSession, 'analyze', new=AsyncMock(return_value='Warm guitar and drums.')))

    async def asyncTearDown(self) -> None:
        await captions.shutdown()
        self.temp.cleanup()

    def body(self) -> TrainingCaptionRequest:
        return TrainingCaptionRequest(samples=[MusicLoraSource(track_id=1), MusicLoraSource(track_id=2)])

    async def finish(self, identifier: str) -> None:
        task = captions._tasks.get(identifier)
        if task:
            await task

    async def test_sequential_results_persist_and_release_admission(self) -> None:
        job = await captions.submit(self.body())
        self.assertTrue(resource_admission.native_work_inflight())
        await self.finish(job.id)
        result = captions.get(job.id)
        self.assertEqual(result.status, 'done')
        self.assertEqual([item.caption for item in result.items], ['Warm guitar and drums.'] * 2)
        self.assertEqual(self.analyze.await_count, 2)
        self.assertFalse(resource_admission.native_work_inflight())
        self.assertEqual(captions.list_jobs().jobs[0], result)

    async def test_maximum_accepted_unicode_and_control_captions_round_trip(self) -> None:
        sources = [MusicLoraSource(track_id=index + 1) for index in range(100)]
        request = TrainingCaptionRequest(samples=sources)
        for caption in ('🌊' * 4000, '\x00' * 4000):
            with self.subTest(character=repr(caption[0])):
                job = TrainingCaptionJob(id='c' * 32, status='done', request=request,
                    items=[TrainingCaptionItem(source=source, status='done', caption=caption) for source in sources],
                    created_at='2026-10-16', updated_at='2026-10-16')
                captions._store(job)
                self.assertEqual(captions.get(job.id), job)
                self.assertEqual(captions.list_jobs().jobs[0], job)

    async def test_oversized_write_is_rejected_before_replacing_saved_record(self) -> None:
        job = await captions.submit(self.body())
        await self.finish(job.id)
        saved = captions.get(job.id)
        previous = captions._path(job.id).read_bytes()
        saved.items[0].caption = 'Changed caption.'
        with patch.object(captions, '_MAX_RECORD_BYTES', 64):
            with self.assertRaises(CaptionError):
                captions._store(saved)
        self.assertEqual(captions._path(job.id).read_bytes(), previous)
        self.assertEqual(captions.get(job.id).items[0].caption, 'Warm guitar and drums.')

    async def test_one_failure_preserves_completed_peers(self) -> None:
        self.analyze.side_effect = [CaptionError('training_caption_analysis_failed'), 'Bass and strings.']
        job = await captions.submit(self.body())
        await self.finish(job.id)
        result = captions.get(job.id)
        self.assertEqual(result.status, 'partial')
        self.assertEqual(result.items[0].error_code, 'training_caption_analysis_failed')
        self.assertEqual(result.items[1].caption, 'Bass and strings.')

    async def test_duplicate_inflight_submit_reconnects_same_job(self) -> None:
        blocked = asyncio.Event()
        async def analyze(_source: Path, _seconds: int) -> str:
            await blocked.wait()
            return 'Guitar.'
        self.analyze.side_effect = analyze
        first = await captions.submit(self.body())
        second = await captions.submit(self.body())
        self.assertEqual(first.id, second.id)
        blocked.set()
        await self.finish(first.id)

    async def test_cancellation_retains_done_items_and_waits_for_drain(self) -> None:
        entered = asyncio.Event()
        count = 0
        async def analyze(_source: Path, _seconds: int) -> str:
            nonlocal count
            count += 1
            if count == 2:
                entered.set()
                await asyncio.Event().wait()
            return 'Guitar.'
        self.analyze.side_effect = analyze
        job = await captions.submit(self.body())
        await entered.wait()
        cancelled = await captions.cancel(job.id)
        self.assertEqual(cancelled.status, 'cancelled')
        self.assertEqual(cancelled.items[0].caption, 'Guitar.')
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_failed_close_quarantines_reservation(self) -> None:
        self.analyze.side_effect = CaptionError('training_caption_native_unavailable')
        with patch.object(CaptionSession, 'close', new=AsyncMock(side_effect=CaptionError('training_caption_stop_failed'))):
            job = await captions.submit(self.body())
            await self.finish(job.id)
        self.assertTrue(captions.work_busy())
        self.assertTrue(resource_admission.native_work_inflight())
        self.assertEqual(captions.get(job.id).error_code, 'training_caption_stop_failed')
        await captions.shutdown()
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_admission_retained_until_resident_model_cleanup_completes(self) -> None:
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def close() -> None:
            entered.set()
            await proceed.wait()
        with patch.object(CaptionSession, 'close', new=AsyncMock(side_effect=close)):
            job = await captions.submit(self.body())
            await entered.wait()
            self.assertTrue(resource_admission.native_work_inflight())
            self.assertTrue(captions.work_busy())
            proceed.set()
            await self.finish(job.id)
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_cancellation_during_successful_close_does_not_quarantine_drained_engine(self) -> None:
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def close() -> None:
            entered.set()
            await proceed.wait()
        with patch.object(CaptionSession, 'close', new=AsyncMock(side_effect=close)):
            job = await captions.submit(self.body())
            await entered.wait()
            cancelling = asyncio.create_task(captions.cancel(job.id))
            await asyncio.sleep(0)
            self.assertTrue(resource_admission.native_work_inflight())
            proceed.set()
            await cancelling
        self.assertFalse(captions.work_busy())
        self.assertFalse(resource_admission.native_work_inflight())
        self.assertEqual([item.caption for item in captions.get(job.id).items], ['Warm guitar and drums.'] * 2)

    async def test_recovery_marks_interruption_without_discarding_caption(self) -> None:
        self.analyze.return_value = 'Guitar.'
        job = await captions.submit(self.body())
        await self.finish(job.id)
        saved = captions.get(job.id)
        saved.status = 'running'
        saved.items[1].status, saved.items[1].caption = 'running', ''
        captions._store(saved)
        await captions.recover()
        result = captions.get(job.id)
        self.assertEqual(result.status, 'interrupted')
        self.assertEqual(result.items[0].caption, 'Guitar.')
        self.assertEqual(result.items[1].status, 'failed')

    async def test_cancel_before_worker_starts_still_releases_reservation(self) -> None:
        job = await captions.submit(self.body())
        result = await captions.cancel(job.id)
        self.assertEqual(result.status, 'cancelled')
        self.assertFalse(resource_admission.native_work_inflight())
        self.analyze.assert_not_awaited()

    async def test_busy_work_does_not_start_native_or_publish_job(self) -> None:
        with patch('app.work_busy.other_work_busy', new=AsyncMock(return_value=True)):
            with self.assertRaises(CaptionError):
                await captions.submit(self.body())
        self.assertEqual(captions.list_jobs().jobs, [])
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_concurrent_duplicate_post_reconnects_after_idle_probe(self) -> None:
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def idle() -> bool:
            entered.set()
            await proceed.wait()
            return False
        with patch('app.work_busy.other_work_busy', new=idle):
            first = asyncio.create_task(captions.submit(self.body()))
            await entered.wait()
            second = asyncio.create_task(captions.submit(self.body()))
            await asyncio.sleep(0)
            proceed.set()
            results = await asyncio.gather(first, second)
        self.assertEqual(results[0].id, results[1].id)
        await self.finish(results[0].id)

    async def test_shutdown_during_idle_probe_cannot_publish_late_worker(self) -> None:
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def idle() -> bool:
            entered.set()
            await proceed.wait()
            return False
        with patch('app.work_busy.other_work_busy', new=idle):
            pending = asyncio.create_task(captions.submit(self.body()))
            await entered.wait()
            await captions.shutdown()
            proceed.set()
            with self.assertRaises(CaptionError):
                await pending
        self.assertEqual(captions.list_jobs().jobs, [])
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_native_mutations_rejected_through_caption_job_lifetime(self) -> None:
        with patch.object(captions, 'work_busy', return_value=True):
            for reserve in (lambda: resource_admission.reserve_native(lambda: False), resource_admission.reserve_native_control):
                with self.assertRaises(resource_admission.ResourceBusyError):
                    await reserve()
