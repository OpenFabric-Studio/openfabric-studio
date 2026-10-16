"""Durable caption batches; completed descriptions survive navigation and failure."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path
import re
import uuid
from . import db
from .job_lifecycle import await_cleanup, request_cancel
from .music_training_sources import source_path
from .music_lora_contracts import MusicLoraError
from .resource_admission import NativeLease, ResourceBusyError, reserve_idle_native
from .training_caption_contracts import TrainingCaptionItem, TrainingCaptionJob, TrainingCaptionJobs, TrainingCaptionRequest
from .training_caption_runtime import CaptionError, CaptionSession
from .video_projects import atomic_text

logger = logging.getLogger(__name__)
_tasks: dict[str, asyncio.Task[None]] = {}
_quarantined: list[tuple[NativeLease, CaptionSession]] = []
_closing = False
_cancel_requested: set[str] = set()
_ACTIVE = {'queued', 'running'}
# 100 captions of 4,000 characters can expand sixfold through JSON escaping.
_MAX_RECORD_BYTES = 4 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root() -> Path:
    root = db.DATA_DIR / 'training-captions'
    if root.is_symlink() or root.resolve().parent != db.DATA_DIR.resolve():
        raise CaptionError('training_caption_storage_unavailable', 503)
    return root


def _path(identifier: str) -> Path:
    if re.fullmatch(r'[0-9a-f]{32}', identifier) is None:
        raise CaptionError('training_caption_job_not_found', 404)
    path = _root() / (identifier + '.json')
    if path.is_symlink():
        raise CaptionError('training_caption_storage_unavailable', 503)
    return path


def _store(job: TrainingCaptionJob) -> None:
    job.updated_at = _now()
    content = job.model_dump_json()
    if len(content.encode('utf-8')) > _MAX_RECORD_BYTES:
        raise CaptionError('training_caption_storage_unavailable', 503)
    atomic_text(_path(job.id), content)


def get(identifier: str) -> TrainingCaptionJob:
    path = _path(identifier)
    if not path.exists():
        raise CaptionError('training_caption_job_not_found', 404)
    try:
        with path.open('rb') as source:
            content = source.read(_MAX_RECORD_BYTES + 1)
        if len(content) > _MAX_RECORD_BYTES:
            raise ValueError('caption_record_too_large')
        job = TrainingCaptionJob.model_validate_json(content)
        if job.id != identifier or [item.source for item in job.items] != job.request.samples:
            raise ValueError('caption_record_mismatch')
        return job
    except (OSError, ValueError) as error:
        raise CaptionError('training_caption_storage_unavailable', 503) from error


def list_jobs() -> TrainingCaptionJobs:
    paths = sorted(_root().glob('*.json'), key=lambda path: (path.stem not in _tasks, -path.stat().st_mtime))[:1000]
    jobs: list[TrainingCaptionJob] = []
    for path in paths:
        try:
            jobs.append(get(path.stem))
        except CaptionError:
            logger.warning('Unavailable caption record preserved: %s', path.name)
    return TrainingCaptionJobs(jobs=jobs)


def work_busy() -> bool:
    return bool(_quarantined) or any(not task.done() for task in _tasks.values())


def _matching(request: TrainingCaptionRequest) -> TrainingCaptionJob | None:
    for identifier in tuple(_tasks):
        existing = get(identifier)
        if existing.status in _ACTIVE and existing.request == request:
            return existing
    return None


async def submit(request: TrainingCaptionRequest) -> TrainingCaptionJob:
    if _closing:
        raise CaptionError('training_caption_shutting_down', 503)
    if existing := _matching(request):
        return existing
    for source in request.samples:
        try:
            source_path(source, prefix='music_training')
        except MusicLoraError as error:
            raise CaptionError('training_caption_source_invalid', error.status) from error
    try:
        lease = await reserve_idle_native()
    except ResourceBusyError as error:
        if existing := _matching(request):
            return existing
        raise CaptionError('training_caption_busy') from error
    try:
        if _closing:
            raise CaptionError('training_caption_shutting_down', 503)
        # No await between taking admission and publishing the owned worker.
        created = _now()
        job = TrainingCaptionJob(id=uuid.uuid4().hex, status='queued', request=request,
            items=[TrainingCaptionItem(source=source) for source in request.samples], created_at=created, updated_at=created)
        _store(job)
        _tasks[job.id] = asyncio.create_task(_run(job, lease), name='training-caption:' + job.id)
        return job
    except BaseException:
        await lease.release()
        raise


def _fail_pending(job: TrainingCaptionJob, code: str) -> None:
    for item in job.items:
        if item.status in ('queued', 'running'):
            item.status, item.error_code = 'failed', code


async def _run(job: TrainingCaptionJob, lease: NativeLease) -> None:
    session = CaptionSession()
    try:
        if job.id in _cancel_requested:
            raise asyncio.CancelledError
        job.status = 'running'
        _store(job)
        await session.start()
        for item in job.items:
            item.status = 'running'
            _store(job)
            try:
                source = source_path(item.source, prefix='music_training')
                item.caption = await session.analyze(source, job.request.segment_seconds)
                item.status = 'done'
            except (CaptionError, MusicLoraError, OSError, ValueError) as error:
                if session.inflight:
                    raise
                item.status = 'failed'
                item.error_code = error.code if isinstance(error, CaptionError) else 'training_caption_source_invalid'
            _store(job)
        completed = sum(item.status == 'done' for item in job.items)
        job.status = 'done' if completed == len(job.items) else 'partial' if completed else 'failed'
    except asyncio.CancelledError:
        job.status, job.error_code = 'cancelled', 'training_caption_cancelled'
        _fail_pending(job, 'training_caption_cancelled')
    except Exception as error:
        logger.exception('Local training-caption job failed')
        job.status = 'failed'
        job.error_code = error.code if isinstance(error, CaptionError) else 'training_caption_failed'
        _fail_pending(job, job.error_code)
    finally:
        async def close_and_release() -> None:
            await session.close()
            await lease.release()
        closing = asyncio.gather(close_and_release(), return_exceptions=True)
        try:
            await await_cleanup(closing)
        except asyncio.CancelledError:
            pass  # Owned cleanup finished; its result determines whether admission is safe.
        outcome = closing.result()[0]
        if isinstance(outcome, BaseException):
            _quarantined.append((lease, session))
            job.status, job.error_code = 'failed', 'training_caption_stop_failed'
            logger.error('Caption native work unverified; reservation retained', exc_info=outcome)
        try:
            _store(job)
        finally:
            _tasks.pop(job.id, None)
            _cancel_requested.discard(job.id)


async def cancel(identifier: str) -> TrainingCaptionJob:
    job = get(identifier)
    task = _tasks.get(identifier)
    if task is not None:
        _cancel_requested.add(identifier)
        if job.status != 'queued':
            request_cancel(task)
        await await_cleanup(asyncio.gather(task, return_exceptions=True))
    return get(identifier)


async def recover() -> None:
    global _closing
    _closing = False
    for job in list_jobs().jobs:
        if job.status in _ACTIVE:
            job.status, job.error_code = 'interrupted', 'training_caption_interrupted'
            _fail_pending(job, 'training_caption_interrupted')
            _store(job)


async def shutdown() -> None:
    global _closing
    _closing = True
    await asyncio.gather(*(cancel(identifier) for identifier in tuple(_tasks)))
    for lease, session in tuple(_quarantined):
        await await_cleanup(session.close())
        await lease.release()
        _quarantined.remove((lease, session))
