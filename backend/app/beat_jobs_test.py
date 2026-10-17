"""Durable beat render ownership and publication use a temporary real library."""
from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app import beat_jobs, beat_render, beat_store, db
from app.beat_contracts import BeatCreateRequest, BeatError, BeatJobRequest, BeatStep
from app.video_process import WorkerIdentity


class BeatJobsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, value in (('_db', None), ('DATA_DIR', self.root), ('FILES_DIR', self.root / 'files'),
                ('DB_PATH', self.root / 'catalog.db')):
            self.enterContext(patch.object(db, name, value))
        for name, value in (('_tasks', {}), ('_processes', {}), ('_unverified', {}),
                ('_closing', False), ('_lock', asyncio.Lock()), ('_semaphore', asyncio.Semaphore(1))):
            self.enterContext(patch.object(beat_jobs, name, value))
        self.connection = db.get_db()
        self.document = beat_store.create_document(BeatCreateRequest())
        self.request = BeatJobRequest(beat_id=self.document.id, expected_revision=0,
            mode='preview', preview_pattern_id='A', submission_key='a' * 32)

    async def asyncTearDown(self) -> None:
        await beat_jobs.shutdown()
        self.connection.close()

    async def finished(self, identifier: str):
        task = beat_jobs._tasks.get(identifier)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), 20)
        return beat_jobs.get(identifier)

    async def test_real_worker_survives_reads_and_produces_confined_artifacts(self) -> None:
        job = await beat_jobs.start(self.request)
        beat_store.update(self.document.id, 0, self.document.model_copy(update={'title': 'Later edit'}))
        self.assertEqual(beat_jobs.list_jobs(self.document.id)[0].id, job.id)
        duplicate = await beat_jobs.start(self.request)
        self.assertEqual(duplicate.id, job.id)
        complete = await self.finished(job.id)
        self.assertEqual(complete.status, 'done', complete)
        self.assertIsNotNone(complete.manifest)
        self.assertEqual(len(beat_jobs._processes), 0)
        self.assertFalse(beat_jobs.work_busy())
        manifest = beat_render.validate_output(job.id)
        mix = next(item for item in manifest.artifacts if item.kind == 'mix')
        path, artifact = beat_jobs.artifact(job.id, mix.id)
        self.assertEqual(path.name, mix.filename)
        self.assertEqual(artifact.sha256, mix.sha256)
        with self.assertRaises(BeatError):
            beat_jobs.artifact(job.id, 'f' * 32)
        self.assertNotIn(str(self.root), complete.model_dump_json())
        self.assertEqual(await beat_jobs.start(self.request), complete)

    async def test_running_cancel_drains_real_supervisor_before_releasing_owner(self) -> None:
        waiting = asyncio.Event()
        async def hold_output(*args, **kwargs):
            await waiting.wait()
            return b''
        with patch.object(beat_jobs, 'read_owned_output', side_effect=hold_output):
            job = await beat_jobs.start(self.request)
            async with asyncio.timeout(10):
                while job.id not in beat_jobs._processes:
                    await asyncio.sleep(.01)
            process = beat_jobs._processes[job.id]
            cancelled = await beat_jobs.cancel(job.id)
        self.assertEqual(cancelled.status, 'cancelled')
        self.assertIsNotNone(process.returncode)
        self.assertEqual(beat_jobs._processes, {})
        self.assertIsNone(self.connection.execute('SELECT worker_identity_json FROM beat_render_jobs WHERE id=?',
            (job.id,)).fetchone()[0])
        self.assertFalse(beat_jobs.work_busy())

    async def test_recovery_adopts_prepared_audio_without_resubmitting_and_resolves_retained_source(self) -> None:
        request = self.request.model_copy(update={'mode': 'full', 'preview_pattern_id': None})
        job = await beat_jobs.start(request)
        complete = await self.finished(job.id)
        manifest = beat_render.validate_output(job.id)
        mix = next(item for item in manifest.artifacts if item.kind == 'mix')
        source = beat_jobs.resolve_render_source(self.document.id, 0, job.id, mix.sha256)
        self.assertEqual(source.track_id, manifest.track_id)
        self.assertEqual(source.sha256, mix.sha256)
        self.assertEqual(source.path.read_bytes(), (beat_render.workspace(job.id) / 'output' / mix.filename).read_bytes())
        with patch('app.export_provenance.digest', side_effect=AssertionError('metadata projection read audio')):
            self.assertEqual(beat_jobs.render_source_metadata(self.document.id, 0, job.id, mix.sha256), source)
        for revision, checksum in ((1, mix.sha256), (0, '0' * 64)):
            with self.assertRaises(BeatError):
                beat_jobs.resolve_render_source(self.document.id, revision, job.id, checksum)
        (beat_render.workspace(job.id) / 'output' / 'manifest.json').unlink()
        beat_jobs._save(complete.model_copy(update={'status': 'running', 'phase': 'publishing'}))
        with patch.object(beat_jobs, '_execute_worker', new=AsyncMock()) as worker:
            await beat_jobs.recover()
            worker.assert_not_awaited()
        self.assertEqual(beat_jobs.get(job.id).status, 'done')
        self.assertEqual(self.connection.execute('SELECT count(*) FROM tracks').fetchone()[0], 1)
        self.assertEqual(beat_render.validate_output(job.id).events_sha256, manifest.events_sha256)

    async def test_actual_worker_admission_rechecks_space_after_queueing(self) -> None:
        await beat_jobs._semaphore.acquire()
        try:
            job = await beat_jobs.start(self.request)
            await asyncio.sleep(0)
            with patch.object(beat_jobs, 'free_bytes', return_value=0), \
                    patch.object(beat_jobs, '_execute_worker', new=AsyncMock()) as worker:
                beat_jobs._semaphore.release()

                complete = await self.finished(job.id)
                worker.assert_not_awaited()
            self.assertEqual((complete.status, complete.error_code), ('failed', 'beat_disk_full'))
        finally:
            if beat_jobs._semaphore.locked():
                beat_jobs._semaphore.release()

    async def test_forecast_bounds_actual_open_hat_tail_without_thirty_seconds_of_padding(self) -> None:
        self.document.patterns[0].lanes[3].steps[:] = [BeatStep(index=15)]
        changed = beat_store.update(self.document.id, 0, self.document)
        estimate = await beat_jobs.preview(self.request.model_copy(update={'expected_revision': changed.revision}))
        self.assertEqual(estimate.frames, 109200)
        self.assertAlmostEqual(estimate.duration_sec, 2.275)

    async def test_revision_disk_recheck_and_idempotency_never_spawn_invalid_jobs(self) -> None:
        beat_store.update(self.document.id, 0, self.document.model_copy(update={'title': 'new'}))
        with self.assertRaises(BeatError) as stale:
            await beat_jobs.start(self.request)
        self.assertEqual(stale.exception.code, 'beat_revision_conflict')
        self.assertEqual(beat_jobs.list_jobs(), [])
        request = self.request.model_copy(update={'expected_revision': 1})
        with patch.object(beat_jobs, 'free_bytes', return_value=0):
            self.assertFalse((await beat_jobs.preview(request)).can_start)
            with self.assertRaises(BeatError) as disk:
                await beat_jobs.start(request)
            self.assertEqual(disk.exception.code, 'beat_disk_full')
        job = await beat_jobs.start(request)
        with self.assertRaises(BeatError) as conflict:
            await beat_jobs.start(request.model_copy(update={'midi_approximation': True}))
        self.assertEqual(conflict.exception.code, 'beat_submission_conflict')
        await self.finished(job.id)

    async def test_cancel_queued_work_does_not_start_or_cancel_other_jobs(self) -> None:
        await beat_jobs._semaphore.acquire()
        try:
            with patch.object(beat_jobs, '_execute_worker', new=AsyncMock()) as worker:
                job = await beat_jobs.start(self.request)
                await asyncio.sleep(0)
                cancelled = await beat_jobs.cancel(job.id)
                self.assertEqual(cancelled.status, 'cancelled')
                worker.assert_not_awaited()
                self.assertFalse((beat_render.workspace(job.id) / 'output' / 'manifest.json').exists())
        finally:
            beat_jobs._semaphore.release()

    async def test_publication_retry_reuses_exact_audio_and_attaches_one_track(self) -> None:
        request = self.request.model_copy(update={'mode': 'full', 'preview_pattern_id': None})
        with patch.object(db, 'insert_track', side_effect=OSError('private path')):
            job = await beat_jobs.start(request)
            saving = await self.finished(job.id)
        self.assertEqual(saving.status, 'saving')
        before = beat_render.validate_output(job.id)
        with patch.object(beat_jobs, '_execute_worker', new=AsyncMock()) as worker:
            complete = await beat_jobs.retry_save(job.id)
            again = await beat_jobs.retry_save(job.id)
            worker.assert_not_awaited()
        self.assertEqual((complete.status, again.status), ('done', 'done'))
        self.assertIsNotNone(complete.manifest)
        self.assertEqual(complete.manifest, again.manifest)
        after = beat_render.validate_output(job.id)
        self.assertEqual(before.events_sha256, after.events_sha256)
        self.assertEqual(next(item.sha256 for item in before.artifacts if item.kind == 'mix'),
            next(item.sha256 for item in after.artifacts if item.kind == 'mix'))
        self.assertEqual(self.connection.execute('SELECT count(*) FROM tracks').fetchone()[0], 1)

    async def test_recovery_retains_unverified_owner_and_never_kills_a_foreign_receipt(self) -> None:
        await beat_jobs._semaphore.acquire()
        try:
            job = await beat_jobs.start(self.request)
            await beat_jobs.cancel(job.id)
        finally:
            beat_jobs._semaphore.release()
        identity = WorkerIdentity(pid=123, token='d' * 32, receipt='/private/not-our-receipt.json')
        self.connection.execute('UPDATE beat_render_jobs SET worker_identity_json=? WHERE id=?',
            (identity.model_dump_json(), job.id))
        self.connection.commit()
        with patch.object(beat_jobs, 'terminate_verified', new=AsyncMock(return_value=True)) as terminate:
            await beat_jobs.recover()
            terminate.assert_not_awaited()
        self.assertEqual(beat_jobs.get(job.id).error_code, 'beat_cleanup_failed')
        self.assertTrue(beat_jobs.work_busy())
        self.assertEqual(beat_jobs._unverified[job.id], identity)


if __name__ == '__main__':
    unittest.main()
