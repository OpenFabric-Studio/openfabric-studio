"""Durable, sequential music coordination; existing jobs own audio publication."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
import logging
import secrets
from typing import Literal
import uuid

from . import music_queue_ownership as ownership, music_queue_store as store
from .job_lifecycle import await_cleanup
from .music_queue_contracts import (
    MusicQueueBatch, MusicQueueCancelRequest, MusicQueueControlRequest,
    MusicQueueEnqueueRequest, MusicQueueError, MusicQueueItem, MusicQueueItemStatus,
    MusicQueuePayload, MusicQueuePreviewRequest, MusicQueueReorderRequest,
    MusicQueueRetryRequest, MusicQueueSnapshot, payload_conditioning,
)
from .music_queue_engines import EngineResult, EngineSession, inspect_child, recover_child
from .music_queue_plan import preview, request_hash as hash_request
from . import queued_music_sources
from .resource_admission import ResourceBusyError

logger = logging.getLogger(__name__)
_task: asyncio.Task[None] | None = None
_owner: ownership.MusicQueueOwner | None = None
_session: EngineSession | None = None
_closing = False
_pause = False
_stop_status: Literal['cancelled', 'interrupted'] = 'cancelled'
_controls = asyncio.Lock()
_active = {'loading', 'submitting', 'running', 'saving', 'unloading'}


def snapshot() -> MusicQueueSnapshot:
    return store.snapshot()


def _item(state: MusicQueueSnapshot, identifier: str) -> MusicQueueItem:
    for batch in state.batches:
        for item in batch.items:
            if item.id == identifier:
                return item
    raise MusicQueueError('music_queue_item_missing', 404)


def _batch(state: MusicQueueSnapshot, identifier: str) -> MusicQueueBatch:
    for batch in state.batches:
        if batch.id == identifier:
            return batch
    raise MusicQueueError('music_queue_batch_missing', 404)


def _pending(state: MusicQueueSnapshot) -> MusicQueueItem | None:
    return next((item for batch in state.batches for item in batch.items if item.status == 'queued'), None)


def _seed(payload: MusicQueuePayload, seed: int) -> MusicQueuePayload:
    if payload.engine == 'yue2':
        settings = payload.request.settings
        return payload.model_copy(update={'request': payload.request.model_copy(update={
            'seed': seed, 'settings': settings.model_copy(update={'seed': seed}) if settings is not None else None})})
    return payload.model_copy(update={'request': payload.request.model_copy(update={'seed': seed})})


async def enqueue(request: MusicQueueEnqueueRequest, *, admit: Callable[[], None] | None = None) -> MusicQueueSnapshot:
    body = MusicQueuePreviewRequest(brief=request.brief, selections=request.selections)
    request_hash = hash_request(body)
    prior = store.lookup_submission(request.submission_key, request_hash)
    if prior is not None:
        return prior
    if _closing:
        raise MusicQueueError('music_queue_busy')
    if admit is not None:
        admit()
    options = [selection.conditioning for selection in request.selections if selection.conditioning is not None]
    inputs = await queued_music_sources.load_existing(request.submission_key, request_hash, options)
    plan = await preview(body, retained_inputs=inputs) if inputs else await preview(body)
    if plan.fingerprint != request.preview_fingerprint:
        raise MusicQueueError('music_queue_preview_changed')
    if not plan.can_enqueue:
        raise MusicQueueError('music_queue_plan_unavailable')
    inputs = await queued_music_sources.retain(request.submission_key, request_hash, options)
    if _closing:
        raise MusicQueueError('music_queue_busy')
    identifier = uuid.uuid4().hex
    seeds: set[int] = set()
    items: list[MusicQueueItem] = []
    for normalized in plan.items:
        seed = secrets.randbelow(2147483648)
        while seed in seeds:
            seed = secrets.randbelow(2147483648)
        seeds.add(seed)
        items.append(MusicQueueItem(**normalized.model_dump(exclude={'payload'}),
            payload=_seed(normalized.payload, seed), id=uuid.uuid4().hex,
            batch_id=identifier, resolved_seed=seed,
            retained_input=next((retained for retained in inputs
                if (option := payload_conditioning(normalized.payload)) is not None
                and retained.snapshot.source == option.source
                and retained.snapshot.sha256 == option.source_sha256), None)))
    batch = MusicQueueBatch(id=identifier, created_at=datetime.now(timezone.utc).isoformat(),
        brief=plan.brief, selections=plan.selections, models=plan.models,
        preview_fingerprint=plan.fingerprint, items=items)
    return store.enqueue(batch, submission_key=request.submission_key,
        request_hash=request_hash, revision=request.revision)


def _phase(identifier: str, status: MusicQueueItemStatus) -> None:
    def change(state: MusicQueueSnapshot) -> None:
        _item(state, identifier).status = status
    store.mutate(change, bump=False)


def _result(identifier: str, result: EngineResult) -> None:
    def change(state: MusicQueueSnapshot) -> None:
        item = _item(state, identifier)
        item.status = result.status
        item.error_code = result.error_code
        if result.status == 'done' and not result.tracks:
            item.status = 'failed'
            item.error_code = 'music_queue_output_missing'
        item.tracks = result.tracks
    store.mutate(change, bump=False)


def _blocked() -> None:
    def change(state: MusicQueueSnapshot) -> None:
        state.status = 'blocked'
        state.error_code = 'music_queue_cleanup_failed'
        if state.active_item_id is not None:
            item = _item(state, state.active_item_id)
            if item.status in _active:
                item.status, item.error_code = 'blocked', 'music_queue_cleanup_failed'
    store.mutate(change)


async def _release() -> bool:
    global _owner, _session
    owner, session = _owner, _session
    if owner is None:
        return True
    try:
        with owner.context():
            if session is not None:
                await session.close()
            if not await owner.release():
                raise MusicQueueError('music_queue_cleanup_failed', 503)
    except Exception:
        logger.exception('Music queue could not prove resource cleanup')
        _blocked()
        return False
    _owner = None
    _session = None
    return True


async def _validate(item: MusicQueueItem) -> None:
    batch = _batch(snapshot(), item.batch_id)
    inputs = [candidate.retained_input for candidate in batch.items if candidate.retained_input is not None]
    plan = await preview(MusicQueuePreviewRequest(brief=batch.brief, selections=batch.selections), retained_inputs=inputs)
    model = next(model for model in plan.models if model.selection.engine == item.engine)
    if not model.compatible or not model.readiness.ready:
        raise MusicQueueError(model.error_code or model.readiness.error_code or 'music_queue_plan_unavailable')
    normalized = next((candidate for candidate in plan.items
        if candidate.engine == item.engine and candidate.variation_index == item.variation_index), None)
    if normalized is None or _seed(normalized.payload, item.resolved_seed) != item.payload or normalized.adapters != item.adapters:
        raise MusicQueueError('music_queue_preview_changed')


async def _run() -> None:
    global _pause
    owner, session = _owner, _session
    if owner is None or session is None:
        return
    current: MusicQueueItem | None = None
    blocked = False
    try:
        with owner.context():
            while not _pause and not _closing:
                current = _pending(snapshot())
                if current is None:
                    break
                identifier = current.id
                def activate(state: MusicQueueSnapshot) -> None:
                    state.active_item_id = identifier
                    _item(state, identifier).status = 'loading'
                store.mutate(activate)
                stage = 'loading'
                try:
                    if current.child_job_id is not None:
                        # A save retry retains the original child and never submits inference.
                        stage = 'saving'
                        _phase(identifier, 'saving')
                        result = await session.retry_save(current)
                    else:
                        await _validate(current)
                        await session.ensure(current.payload)
                        stage = 'submitting'
                        _phase(identifier, 'submitting')
                        child = await session.submit(current)
                        def submitted(state: MusicQueueSnapshot) -> None:
                            item = _item(state, identifier)
                            item.child_job_id = child
                            item.status = 'running'
                        store.mutate(submitted, bump=False)
                        stage = 'running'
                        current = _item(snapshot(), identifier)
                        result = await session.wait(current)
                    _result(identifier, result)
                    if result.status in ('interrupted', 'cancelled'):
                        _pause = True
                    if result.status == 'blocked':
                        blocked = True
                        break
                except MusicQueueError as error:
                    if error.code == 'music_queue_cleanup_failed':
                        blocked = True
                        break
                    ambiguous = error.code == 'music_queue_submission_interrupted'
                    _result(identifier, EngineResult('interrupted' if ambiguous else 'failed', current.tracks, error.code))
                    if ambiguous:
                        _pause = True
                    if stage == 'loading':
                        batch_id, engine = current.batch_id, current.engine
                        def block_group(state: MusicQueueSnapshot) -> None:
                            for item in _batch(state, batch_id).items:
                                if item.engine == engine and item.status == 'queued':
                                    item.status, item.error_code = 'blocked', error.code
                        store.mutate(block_group, bump=False)
                    # Startup/submit uncertainty must drain before any next child.
                    await session.close()
                current = None
    except asyncio.CancelledError:
        if current is not None:
            current = _item(snapshot(), current.id)
            with owner.context():
                try:
                    await session.cancel(current)
                    cancelled = inspect_child(current) if current.child_job_id is not None else None
                    if cancelled is not None and cancelled.status == 'done':
                        _result(current.id, cancelled)
                    else:
                        ambiguous = current.status == 'submitting' and current.child_job_id is None
                        _result(current.id, EngineResult('interrupted' if ambiguous else _stop_status,
                            cancelled.tracks if cancelled is not None else current.tracks,
                            'music_queue_submission_interrupted' if ambiguous else
                            'music_queue_interrupted' if _stop_status == 'interrupted' else ''))
                except Exception:
                    logger.exception('Music queue child cancellation failed')
                    blocked = True
    except Exception:
        logger.exception('Music queue runner failed')
        blocked = True
    finally:
        released = await _release()
        if blocked or not released:
            _blocked()
        else:
            def finish(state: MusicQueueSnapshot) -> None:
                state.active_item_id = None
                state.status = 'paused' if _pending(state) is not None else 'idle'
                state.error_code = ''
            store.mutate(finish)


async def _drain_stop(status: Literal['cancelled', 'interrupted']) -> None:
    global _pause, _stop_status, _owner
    _pause, _stop_status = True, status
    task = _task
    if _owner is None and snapshot().status == 'blocked':
        try:
            _owner = await ownership.reserve()
        except ResourceBusyError as error:
            raise MusicQueueError('music_queue_cleanup_failed', 503) from error
    if task is not None and not task.done():
        if not task.cancelling():
            task.cancel()
        await await_cleanup(asyncio.gather(task, return_exceptions=True))
    # A task cancelled before its first instruction never enters its finally.
    if _owner is not None and not await _release():
        raise MusicQueueError('music_queue_cleanup_failed', 503)
    def finish(state: MusicQueueSnapshot) -> None:
        state.active_item_id = None
        state.status = 'paused' if _pending(state) is not None else 'idle'
        state.error_code = ''
    store.mutate(finish)


async def _stop(status: Literal['cancelled', 'interrupted']) -> None:
    global _pause, _stop_status
    _pause, _stop_status = True, status
    if _task is not None and not _task.done() and not _task.cancelling():
        _task.cancel()
    await await_cleanup(_drain_stop(status))


async def control(request: MusicQueueControlRequest) -> MusicQueueSnapshot:
    global _task, _owner, _session, _pause
    async with _controls:
        state = snapshot()
        if request.revision != state.revision:
            raise MusicQueueError('music_queue_revision_conflict')
        if request.action == 'cancel_active':
            await _stop('cancelled')
        elif request.action == 'pause':
            def pause(state: MusicQueueSnapshot) -> None:
                state.status = 'pausing' if _task is not None and not _task.done() else 'paused'
            store.mutate(pause, revision=request.revision)
            _pause = True
        else:
            if state.status == 'blocked':
                raise MusicQueueError('music_queue_cleanup_failed', 503)
            if _closing or _owner is not None or _task is not None and not _task.done():
                raise MusicQueueError('music_queue_busy')
            if _pending(state) is None:
                raise MusicQueueError('music_queue_empty')
            try:
                _owner = await ownership.reserve()
            except ResourceBusyError as error:
                raise MusicQueueError(error.code) from error
            try:
                _session = EngineSession()
                _pause = False
                def start(state: MusicQueueSnapshot) -> None:
                    state.status = 'running'
                    state.error_code = ''
                store.mutate(start, revision=request.revision)
                ownership.register_stop_handler(stop_active)
                with _owner.context():
                    _task = asyncio.create_task(_run(), name='music-queue')
            except BaseException:
                await _release()
                raise
        return snapshot()


def reorder(request: MusicQueueReorderRequest) -> MusicQueueSnapshot:
    def change(state: MusicQueueSnapshot) -> None:
        eligible = [batch for batch in state.batches if batch.items and all(
            item.status == 'queued' and item.child_job_id is None and item.attempt == 1 for item in batch.items)]
        if set(request.batch_ids) != {batch.id for batch in eligible}:
            raise MusicQueueError('music_queue_reorder_invalid')
        lookup = {batch.id: batch for batch in eligible}
        ordered = iter(request.batch_ids)
        state.batches = [lookup[next(ordered)] if batch.id in lookup else batch for batch in state.batches]
    return store.mutate(change, revision=request.revision)


async def cancel_batch(request: MusicQueueCancelRequest) -> MusicQueueSnapshot:
    def change(state: MusicQueueSnapshot) -> None:
        batch = _batch(state, request.batch_id)
        if any(item.id == state.active_item_id for item in batch.items):
            raise MusicQueueError('music_queue_batch_active')
        for item in batch.items:
            if item.status == 'queued':
                item.status = 'cancelled'
    return store.mutate(change, revision=request.revision)


async def retry(request: MusicQueueRetryRequest) -> MusicQueueSnapshot:
    def change(state: MusicQueueSnapshot) -> None:
        item = _item(state, request.item_id)
        if item.status not in ('failed', 'interrupted', 'cancelled', 'blocked') or item.id == state.active_item_id:
            raise MusicQueueError('music_queue_retry_invalid')
        if any(candidate.retry_of_item_id == item.id for candidate in _batch(state, item.batch_id).items):
            raise MusicQueueError('music_queue_retry_invalid')
        if request.mode == 'save':
            if item.child_job_id is None or item.error_code not in (
                    'save_failed', 'save_interrupted', 'local_music_save_failed', 'local_music_save_interrupted'):
                raise MusicQueueError('music_queue_save_retry_unavailable')
            item.status, item.error_code = 'queued', ''
        else:
            replacement = item.model_copy(deep=True, update={'id': uuid.uuid4().hex,
                'child_job_id': None, 'status': 'queued', 'tracks': [], 'error_code': '',
                'attempt': item.attempt + 1, 'retry_of_item_id': item.id})
            _batch(state, item.batch_id).items.append(replacement)
    return store.mutate(change, revision=request.revision)


async def recover() -> None:
    global _closing, _pause
    _closing, _pause = False, True
    await queued_music_sources.recover()
    ownership.register_stop_handler(stop_active)
    results: dict[str, EngineResult] = {}
    for batch in snapshot().batches:
        for item in batch.items:
            if item.status in _active or item.child_job_id is not None and item.status not in ('done', 'queued'):
                recovered = await recover_child(item)
                results[item.id] = recovered if recovered is not None else EngineResult('interrupted',
                    item.tracks, 'music_queue_interrupted')
    def reconcile(state: MusicQueueSnapshot) -> None:
        for batch in state.batches:
            for item in batch.items:
                result = results.get(item.id)
                if result is not None:
                    item.status, item.tracks, item.error_code = result.status, result.tracks, result.error_code
        state.active_item_id = None
        uncertain = any(result.status == 'blocked' for result in results.values())
        state.status = 'blocked' if uncertain else 'paused' if _pending(state) is not None else 'idle'
        state.error_code = 'music_queue_cleanup_failed' if uncertain else ''
    store.mutate(reconcile)


async def stop_active() -> None:
    async with _controls:
        await _stop('cancelled')


async def shutdown() -> None:
    global _closing
    _closing = True
    await queued_music_sources.shutdown()
    async with _controls:
        await _stop('interrupted')
