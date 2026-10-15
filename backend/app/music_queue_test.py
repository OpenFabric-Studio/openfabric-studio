"""Durable queue ordering, control races and interrupted publication."""
from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
import tempfile
import sqlite3
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from app import db, music_queue as queue
from app.music_queue_contracts import (
    MusicQueueBrief, MusicQueueCatalog, MusicQueueCancelRequest, MusicQueueControlRequest,
    MusicQueueEngineSelection, MusicQueueEnqueueRequest, MusicQueueError,
    MusicQueueItem, MusicQueuePayload, MusicQueuePreviewRequest,
    MusicQueueReadiness, MusicQueueReorderRequest, MusicQueueRetryRequest,
    MusicQueueSnapshot,
)
from app.music_queue_engines import EngineResult
from app import music_queue_plan
from app.track_view import track_response


class FakeOwner:
    released = False

    def context(self) -> AbstractContextManager[None]:
        return nullcontext()

    async def release(self) -> bool:
        self.released = True
        return True


class FakeSession:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.events: list[str] = []
        self.current: str | None = None
        self.entered = asyncio.Event()
        self.gate: asyncio.Event | None = None
        self.fail_close = False
        self.fail_generate = False
        self.fail_ensure = False

    async def ensure(self, payload: MusicQueuePayload) -> None:
        if self.fail_ensure and payload.engine == 'ace_step':
            self.events.append('failed_load:ace_step')
            raise MusicQueueError('music_queue_engine_start_failed', 503)
        if self.current != payload.engine:
            await self.close()
            self.current = payload.engine
            self.events.append('load:' + payload.engine)

    async def submit(self, item: MusicQueueItem) -> str:
        self.events.append('submit:' + item.engine)
        return uuid.uuid4().hex

    async def wait(self, item: MusicQueueItem) -> EngineResult:
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.fail_generate:
            return EngineResult(status='failed', error_code='generation_failed')
        destination = self.root / (item.id + '.wav')
        destination.write_bytes(b'fixture; models and publication are mocked')
        identifier = db.insert_track(model=item.engine, title='Fixture', lyrics='', seed=item.resolved_seed,
            duration_ms=1000, wall_ms=10, params={}, audio_path=destination, abc_path=None)
        row = db.get_track(identifier)
        if row is None:
            raise AssertionError('Fixture track disappeared')
        self.events.append('save:' + item.engine)
        return EngineResult(status='done', tracks=[track_response(row)])

    async def cancel(self, item: MusicQueueItem) -> None:
        self.events.append('cancel:' + item.engine)

    async def close(self) -> None:
        if self.current is not None:
            self.events.append('unload:' + self.current)
            if self.fail_close:
                raise MusicQueueError('music_queue_cleanup_failed', 503)
            self.current = None

    async def retry_save(self, item: MusicQueueItem) -> EngineResult:
        self.events.append('retry_save:' + item.engine)
        return await self.wait(item)


class MusicQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temporary)
        self.enterContext(patch.object(db, '_db', None))
        self.enterContext(patch.object(db, 'DATA_DIR', self.root))
        self.enterContext(patch.object(db, 'DB_PATH', self.root / 'catalog.db'))
        self.enterContext(patch.object(db, 'FILES_DIR', self.root / 'files'))
        self.connection = db.get_db()
        self.session = FakeSession(self.root)
        self.owner = FakeOwner()
        self.enterContext(patch.object(queue, 'EngineSession', return_value=self.session))
        self.enterContext(patch.object(queue.ownership, 'reserve', new=AsyncMock(return_value=self.owner)))
        self.enterContext(patch.object(queue, '_task', None))
        self.enterContext(patch.object(queue, '_owner', None))
        self.enterContext(patch.object(queue, '_session', None))
        self.enterContext(patch.object(queue, '_closing', False))
        self.enterContext(patch.object(queue, '_controls', asyncio.Lock()))
        self.ready = [MusicQueueReadiness(engine=engine, supported=True, installed=True,
            weights_ready=True, managed_status='stopped' if engine in ('ace_step', 'yue2') else 'one_shot', ready=True)
            for engine in ('ace_step', 'yue2', 'heartmula', 'stable_audio3')]

        self.enterContext(patch.object(music_queue_plan, 'catalog', new=AsyncMock(return_value=MusicQueueCatalog(engines=self.ready))))
        self.enterContext(patch.object(music_queue_plan, '_weights_ready', return_value=True))
        await queue.recover()

    async def asyncTearDown(self) -> None:
        self.session.fail_close = False
        if self.session.gate is not None:
            self.session.gate.set()
        await queue.shutdown()
        self.connection.close()

    async def request(self, key: str = 'first', variations: int = 2) -> MusicQueueEnqueueRequest:
        body = MusicQueuePreviewRequest(brief=MusicQueueBrief(title=key, description='Warm jazz',
            lyrics='[Verse]\nOne night', duration_seconds=90), selections=[
            MusicQueueEngineSelection(engine='ace_step', variations=variations),
            MusicQueueEngineSelection(engine='yue2', variations=variations)])
        plan = await music_queue_plan.preview(body)
        return MusicQueueEnqueueRequest(**body.model_dump(), revision=queue.snapshot().revision,
            submission_key=key, preview_fingerprint=plan.fingerprint)

    async def finish(self) -> None:
        task = queue._task
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), 3)

    async def test_enqueue_is_atomic_idempotent_and_does_not_start_engines(self) -> None:
        request = await self.request()
        first = await queue.enqueue(request)
        repeated = await queue.enqueue(request)
        self.assertEqual(first.batches[0].id, repeated.batches[0].id)
        self.assertEqual(len(repeated.batches), 1)
        items = repeated.batches[0].items
        self.assertEqual([item.engine for item in items], ['ace_step', 'ace_step', 'yue2', 'yue2'])
        self.assertEqual(len({item.resolved_seed for item in items}), 4)
        self.assertEqual(self.session.events, [])
        changed = request.model_copy(deep=True)
        changed.brief.description = 'A different song'
        with self.assertRaises(MusicQueueError) as failure:
            await queue.enqueue(changed)
        self.assertEqual(failure.exception.code, 'music_queue_submission_conflict')
        self.assertEqual(len(queue.snapshot().batches), 1)

    async def test_queue_saves_all_variations_then_unloads_before_next_model(self) -> None:
        await queue.enqueue(await self.request())
        await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='start'))
        await self.finish()
        self.assertEqual(self.session.events, ['load:ace_step', 'submit:ace_step', 'save:ace_step',
            'submit:ace_step', 'save:ace_step', 'unload:ace_step', 'load:yue2', 'submit:yue2',
            'save:yue2', 'submit:yue2', 'save:yue2', 'unload:yue2'])
        result = queue.snapshot()
        self.assertEqual(result.status, 'idle')
        self.assertTrue(all(item.status == 'done' and len(item.tracks) == 1 for item in result.batches[0].items))
        self.assertTrue(self.owner.released)

    async def test_pause_finishes_current_take_and_releases_before_returning(self) -> None:
        self.session.gate = asyncio.Event()
        await queue.enqueue(await self.request())
        await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='start'))
        await asyncio.wait_for(self.session.entered.wait(), 1)
        await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='pause'))
        self.session.gate.set()
        await self.finish()
        result = queue.snapshot()
        self.assertEqual(result.status, 'paused')
        self.assertEqual([item.status for item in result.batches[0].items], ['done','queued','queued','queued'])
        self.assertEqual(self.session.events[-1], 'unload:ace_step')
        self.assertTrue(self.owner.released)

    async def test_cancel_active_keeps_pending_work_and_already_saved_outputs(self) -> None:
        self.session.gate = asyncio.Event()
        await queue.enqueue(await self.request())
        await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='start'))
        await asyncio.wait_for(self.session.entered.wait(), 1)
        await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='cancel_active'))
        self.assertEqual(queue.snapshot().status, 'paused')
        self.assertEqual([item.status for item in queue.snapshot().batches[0].items], ['cancelled','queued','queued','queued'])
        self.assertEqual(self.session.events[-2:], ['cancel:ace_step','unload:ace_step'])

    async def test_failed_unload_blocks_advancement_and_retains_reservation(self) -> None:
        self.session.fail_close = True
        await queue.enqueue(await self.request(variations=1))
        await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='start'))
        await self.finish()
        result = queue.snapshot()
        self.assertEqual(result.status, 'blocked')
        self.assertFalse(self.owner.released)
        self.assertNotIn('load:yue2', self.session.events)
        self.assertEqual(result.batches[0].items[0].status, 'done')
        self.assertEqual(len(result.batches[0].items[0].tracks), 1)

    async def test_reorder_is_revisioned_and_preserves_original_brief_snapshots(self) -> None:
        first = await queue.enqueue(await self.request('first', 1))
        second = await queue.enqueue(await self.request('second', 1))
        ids = [batch.id for batch in second.batches]
        with self.assertRaises(MusicQueueError):
            queue.reorder(MusicQueueReorderRequest(revision=first.revision, batch_ids=list(reversed(ids))))
        result = queue.reorder(MusicQueueReorderRequest(revision=second.revision, batch_ids=list(reversed(ids))))
        self.assertEqual([batch.brief.title for batch in result.batches], ['second','first'])
        self.assertEqual(result.batches[0].brief.description, 'Warm jazz')

    async def test_cancel_pending_batch_does_not_delete_its_records(self) -> None:
        snapshot = await queue.enqueue(await self.request())
        result = await queue.cancel_batch(MusicQueueCancelRequest(revision=snapshot.revision, batch_id=snapshot.batches[0].id))
        self.assertTrue(all(item.status == 'cancelled' for item in result.batches[0].items))
        self.assertEqual(self.session.events, [])

    async def test_explicit_inference_retry_creates_an_attempt_and_preserves_seed(self) -> None:
        self.session.fail_generate = True
        await queue.enqueue(await self.request(variations=1))
        await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='start'))
        await self.finish()
        prior = queue.snapshot()
        failed = prior.batches[0].items[0]
        result = await queue.retry(MusicQueueRetryRequest(revision=prior.revision, item_id=failed.id, mode='inference'))
        retried = next(item for item in result.batches[0].items if item.retry_of_item_id == failed.id)
        self.assertEqual(retried.resolved_seed, failed.resolved_seed)
        self.assertEqual(retried.attempt, 2)
        self.assertEqual(result.batches[0].items[0].status, 'failed')

    async def test_start_failure_blocks_same_model_group_before_next_model(self) -> None:
        self.session.fail_ensure = True
        await queue.enqueue(await self.request())
        await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='start'))
        await self.finish()
        self.assertEqual(self.session.events.count('failed_load:ace_step'), 1)
        self.assertEqual([item.status for item in queue.snapshot().batches[0].items],
            ['failed', 'blocked', 'done', 'done'])

    async def test_save_retry_uses_same_child_without_loading_or_new_inference(self) -> None:
        state = await queue.enqueue(await self.request(variations=1))
        identifier = state.batches[0].items[0].id
        def failed(state: MusicQueueSnapshot) -> None:
            item = state.batches[0].items[0]
            item.status, item.error_code, item.child_job_id = 'failed', 'save_failed', 'existing-child'
            state.batches[0].items[1].status = 'cancelled'
        queue.store.mutate(failed)
        await queue.retry(MusicQueueRetryRequest(revision=queue.snapshot().revision, item_id=identifier, mode='save'))
        await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='start'))
        await self.finish()
        self.assertEqual(self.session.events, ['retry_save:ace_step', 'save:ace_step'])
        self.assertEqual(queue.snapshot().batches[0].items[0].child_job_id, 'existing-child')

    async def test_restart_waits_for_child_publication_and_preserves_outputs(self) -> None:
        state = await queue.enqueue(await self.request(variations=1))
        identifier = state.batches[0].items[0].id
        def interrupted(state: MusicQueueSnapshot) -> None:
            item = state.batches[0].items[0]
            item.status, item.child_job_id = 'saving', 'existing-child'
            state.active_item_id, state.status = identifier, 'running'
        queue.store.mutate(interrupted)
        track_result = await self.session.wait(queue.snapshot().batches[0].items[0])
        with patch.object(queue, 'recover_child', new=AsyncMock(return_value=track_result)) as recovered:
            await queue.recover()
        recovered.assert_awaited_once()
        self.assertEqual(queue.snapshot().batches[0].items[0].status, 'done')
        self.assertEqual(len(queue.snapshot().batches[0].items[0].tracks), 1)
        self.assertEqual(queue.snapshot().status, 'paused')

    async def test_restart_keeps_cleanup_uncertainty_blocked(self) -> None:
        await queue.enqueue(await self.request(variations=1))
        def interrupted(state: MusicQueueSnapshot) -> None:
            state.batches[0].items[0].status = 'running'
        queue.store.mutate(interrupted)
        with patch.object(queue, 'recover_child', new=AsyncMock(return_value=EngineResult('blocked', error_code='music_queue_cleanup_failed'))):
            await queue.recover()
        self.assertEqual(queue.snapshot().status, 'blocked')
        self.assertEqual(queue.snapshot().error_code, 'music_queue_cleanup_failed')

    async def test_start_with_stale_revision_releases_claim_without_running(self) -> None:
        await queue.enqueue(await self.request())
        revision = queue.snapshot().revision
        async def claim() -> FakeOwner:
            await queue.enqueue(await self.request('second'))
            return self.owner
        with patch.object(queue.ownership, 'reserve', side_effect=claim):
            with self.assertRaises(MusicQueueError) as failure:
                await queue.control(MusicQueueControlRequest(revision=revision, action='start'))
        self.assertEqual(failure.exception.code, 'music_queue_revision_conflict')
        self.assertTrue(self.owner.released)
        self.assertEqual(self.session.events, [])

    async def test_cancel_before_runner_starts_still_releases_reservation(self) -> None:
        await queue.enqueue(await self.request())
        await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='start'))
        await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='cancel_active'))
        self.assertTrue(self.owner.released)
        self.assertEqual(queue.snapshot().status, 'paused')
        self.assertTrue(all(item.status == 'queued' for item in queue.snapshot().batches[0].items))

    async def test_ambiguous_submit_is_interrupted_and_unloaded_before_manual_resume(self) -> None:
        await queue.enqueue(await self.request())
        with patch.object(self.session, 'submit', new=AsyncMock(side_effect=MusicQueueError('music_queue_submission_interrupted'))):
            await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='start'))
            await self.finish()
        state = queue.snapshot()
        self.assertEqual(state.status, 'paused')
        self.assertEqual([item.status for item in state.batches[0].items], ['interrupted', 'queued', 'queued', 'queued'])
        self.assertEqual(self.session.events, ['load:ace_step', 'unload:ace_step'])

    async def test_interrupted_child_unloads_and_pauses_before_next_take(self) -> None:
        await queue.enqueue(await self.request())
        with patch.object(self.session, 'wait', new=AsyncMock(return_value=EngineResult('interrupted', error_code='interrupted'))):
            await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='start'))
            await self.finish()
        self.assertEqual(queue.snapshot().status, 'paused')
        self.assertEqual(self.session.events, ['load:ace_step', 'submit:ace_step', 'unload:ace_step'])

    async def test_legacy_child_cancel_pauses_and_unloads_before_another_take(self) -> None:
        await queue.enqueue(await self.request())
        with patch.object(self.session, 'wait', new=AsyncMock(return_value=EngineResult('cancelled'))):
            await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='start'))
            await self.finish()
        self.assertEqual(queue.snapshot().status, 'paused')
        self.assertEqual(self.session.events, ['load:ace_step', 'submit:ace_step', 'unload:ace_step'])

    async def test_cancel_keeps_partial_publication_even_if_child_not_done(self) -> None:
        await queue.enqueue(await self.request())
        saved = await self.session.wait(queue.snapshot().batches[0].items[0])
        self.session.entered.clear()
        self.session.gate = asyncio.Event()
        await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='start'))
        await self.session.entered.wait()
        with patch.object(queue, 'inspect_child', return_value=EngineResult('cancelled', tracks=saved.tracks)):
            await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='cancel_active'))
        self.assertEqual(queue.snapshot().batches[0].items[0].tracks, saved.tracks)

    async def test_restart_blocked_queue_requires_verified_cleanup_before_start(self) -> None:
        await queue.enqueue(await self.request())
        def blocked(state: MusicQueueSnapshot) -> None:
            state.status, state.error_code = 'blocked', 'music_queue_cleanup_failed'
        queue.store.mutate(blocked)
        with self.assertRaises(MusicQueueError):
            await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='start'))
        self.assertEqual(self.session.events, [])
        with patch.object(queue.ownership, 'reserve', new=AsyncMock(side_effect=queue.ResourceBusyError('native_model_busy'))):
            with self.assertRaises(MusicQueueError):
                await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='cancel_active'))
        self.assertEqual(queue.snapshot().status, 'blocked')
        await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='cancel_active'))
        self.assertEqual(queue.snapshot().status, 'paused')
        self.assertTrue(self.owner.released)

    async def test_missing_retained_save_is_item_failure_with_verified_release(self) -> None:
        state = await queue.enqueue(await self.request())
        identifier = state.batches[0].items[0].id
        def retained(state: MusicQueueSnapshot) -> None:
            state.batches[0].items[0].child_job_id = 'missing-child'
        queue.store.mutate(retained)
        with patch.object(self.session, 'retry_save', new=AsyncMock(side_effect=MusicQueueError('music_queue_save_retry_unavailable'))):
            await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='start'))
            await self.finish()
        result = next(item for item in queue.snapshot().batches[0].items if item.id == identifier)
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.error_code, 'music_queue_save_retry_unavailable')
        self.assertEqual(queue.snapshot().status, 'idle')
        self.assertTrue(self.owner.released)

    async def test_cancelling_stop_request_still_drains_and_releases(self) -> None:
        await queue.enqueue(await self.request())
        queue._owner, queue._session = self.owner, self.session
        entered, gate = asyncio.Event(), asyncio.Event()
        async def release() -> bool:
            entered.set()
            await gate.wait()
            self.owner.released = True
            return True
        with patch.object(self.owner, 'release', side_effect=release):
            stop = asyncio.create_task(queue.stop_active())
            await entered.wait()
            stop.cancel()
            await asyncio.sleep(0)
            self.assertFalse(stop.done())
            gate.set()
            with self.assertRaises(asyncio.CancelledError):
                await stop
        self.assertIsNone(queue._owner)
        self.assertEqual(queue.snapshot().status, 'paused')

    async def test_rejected_pause_does_not_change_runtime_intent(self) -> None:
        self.session.gate = asyncio.Event()
        await queue.enqueue(await self.request())
        await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='start'))
        await self.session.entered.wait()
        with patch.object(queue.store, 'mutate', side_effect=sqlite3.OperationalError('injected write failure')):
            with self.assertRaises(sqlite3.OperationalError):
                await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='pause'))
        self.assertFalse(queue._pause)
        self.assertEqual(queue.snapshot().status, 'running')

    async def test_superseded_attempt_cannot_be_retried_again(self) -> None:
        self.session.fail_generate = True
        await queue.enqueue(await self.request(variations=1))
        await queue.control(MusicQueueControlRequest(revision=queue.snapshot().revision, action='start'))
        await self.finish()
        failed = queue.snapshot().batches[0].items[0]
        await queue.retry(MusicQueueRetryRequest(revision=queue.snapshot().revision, item_id=failed.id, mode='inference'))
        with self.assertRaises(MusicQueueError) as rejected:
            await queue.retry(MusicQueueRetryRequest(revision=queue.snapshot().revision, item_id=failed.id, mode='inference'))
        self.assertEqual(rejected.exception.code, 'music_queue_retry_invalid')


if __name__ == '__main__':
    unittest.main()
