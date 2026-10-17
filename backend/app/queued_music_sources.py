"""Queue-owned immutable inputs; a replay never resolves the mutable library again."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import hashlib
from pathlib import Path

from pydantic import Field, TypeAdapter, ValidationError

from . import song_sources
from .atomic_files import write_object
from .beat_song_mix_contracts import BeatSongMixError
from .contracts import JsonObject
from .job_lifecycle import await_cleanup
from .music_analysis_contracts import MusicAnalysisSha256
from .music_conditioning_contracts import ConditioningContract, StableMusicConditioning
from .music_queue_contracts import MusicQueueError
from .queued_music_source_contracts import QueuedMusicSource
from .song_from_beat_contracts import AceMusicConditioning

QueueConditioning = StableMusicConditioning | AceMusicConditioning


class _Intake(ConditioningContract):
    request_sha256: MusicAnalysisSha256
    inputs: list[QueuedMusicSource] = Field(max_length=3)


_lock = asyncio.Lock()
_tasks: set[asyncio.Task[object]] = set()
_closing = False
_object = TypeAdapter(JsonObject)


def _source_error(error: OSError | BeatSongMixError, fallback: str) -> MusicQueueError:
    code = 'music_queue_cleanup_failed' if isinstance(error, BeatSongMixError) and error.code == 'song_cleanup_failed' else fallback
    return MusicQueueError(code)


async def resolve(retained: QueuedMusicSource, options: QueueConditioning) -> Path:
    if retained.snapshot.source != options.source or retained.snapshot.sha256 != options.source_sha256:
        raise MusicQueueError('conditioning_source_changed')
    if options.mode in ('ace_complete', 'ace_lego') and not 10 <= retained.snapshot.duration_sec <= 300:
        raise MusicQueueError('conditioning_source_invalid')
    try:
        return await song_sources.resolve_snapshot(song_sources.owned_root(retained.owner_id), retained.snapshot)
    except (OSError, BeatSongMixError) as error:
        raise _source_error(error, 'conditioning_source_changed') from error


@asynccontextmanager
async def _operation() -> AsyncIterator[None]:
    task = asyncio.current_task()
    if task is None or _closing:
        raise MusicQueueError('music_queue_busy')
    _tasks.add(task)
    try:
        async with _lock:
            if _closing:
                raise MusicQueueError('music_queue_busy')
            yield
    finally:
        _tasks.discard(task)


def _receipt(submission_key: str, request_sha256: str, *, create: bool = False) -> Path:
    receipt_id = hashlib.sha256(('queue-input:' + submission_key + ':' + request_sha256).encode()).hexdigest()[:32]
    return song_sources.owned_root(receipt_id, create=create) / 'queue-intake.json'


async def _read(receipt: Path, request_sha256: str, options: list[QueueConditioning]) -> list[QueuedMusicSource] | None:
    if not receipt.exists() and not receipt.is_symlink():
        return None
    if receipt.is_symlink() or not receipt.is_file() or receipt.stat().st_size > 2 * 1024**2:
        raise MusicQueueError('conditioning_snapshot_invalid')
    try:
        saved = _Intake.model_validate_json(receipt.read_bytes())
    except (OSError, ValidationError) as error:
        raise MusicQueueError('conditioning_snapshot_invalid') from error
    if saved.request_sha256 != request_sha256 or len(saved.inputs) != len(options):
        raise MusicQueueError('conditioning_snapshot_invalid')
    for retained, option in zip(saved.inputs, options):
        await resolve(retained, option)
    return saved.inputs


async def load_existing(submission_key: str, request_sha256: str, options: list[QueueConditioning]) -> list[QueuedMusicSource]:
    """Recover intake even when queue publication failed before its DB transaction."""
    if not options:
        return []
    async with _operation():
        saved = await _read(_receipt(submission_key, request_sha256, create=True), request_sha256, options)
        if saved is not None:
            return saved
        inputs: list[QueuedMusicSource] = []
        for index, option in enumerate(options):
            if option.source.kind != 'version' and option.source.kind != 'beat_render':
                raise MusicQueueError('conditioning_task_unsupported')
            owner_id, source_id = _source_ids(submission_key, request_sha256, index)
            try:
                snapshot = await song_sources.load_retained_source(song_sources.owned_root(owner_id, create=True),
                    source_id, option.source, option.source_sha256, max_bytes=512 * 1024**2, max_seconds=380)
            except (OSError, BeatSongMixError) as error:
                raise _source_error(error, 'conditioning_snapshot_invalid') from error
            if snapshot is None:
                return []
            inputs.append(QueuedMusicSource(owner_id=owner_id, snapshot=snapshot))
        return inputs


def _source_ids(submission_key: str, request_sha256: str, index: int) -> tuple[str, str]:
    owner_id = hashlib.sha256(f'queue-source:{submission_key}:{request_sha256}:{index}'.encode()).hexdigest()[:32]
    return owner_id, hashlib.sha256(('queue-raw:' + owner_id).encode()).hexdigest()[:32]


async def retain(submission_key: str, request_sha256: str, options: list[QueueConditioning]) -> list[QueuedMusicSource]:
    if not options:
        return []
    if len(options) > 3 or _closing:
        raise MusicQueueError('music_queue_busy')
    async with _operation():
        receipt = _receipt(submission_key, request_sha256, create=True)
        existing = await _read(receipt, request_sha256, options)
        if existing is not None:
            return existing
        inputs: list[QueuedMusicSource] = []
        for index, option in enumerate(options):
            source = option.source
            if source.kind != 'version' and source.kind != 'beat_render':
                raise MusicQueueError('conditioning_task_unsupported')
            owner_id, source_id = _source_ids(submission_key, request_sha256, index)
            try:
                snapshot = await song_sources.retain_source(source, song_sources.owned_root(owner_id, create=True),
                    source_id=source_id, expected_sha256=option.source_sha256, max_bytes=512 * 1024**2,
                    max_seconds=380, disk_reserve_bytes=64 * 1024**2)
            except (OSError, BeatSongMixError) as error:
                raise _source_error(error, 'conditioning_source_changed') from error
            if snapshot.source_bytes > 512 * 1024**2 or snapshot.duration_sec > 380:
                raise MusicQueueError('conditioning_source_invalid')
            inputs.append(QueuedMusicSource(owner_id=owner_id, snapshot=snapshot))
            await resolve(inputs[-1], option)
        saved = _Intake(request_sha256=request_sha256, inputs=inputs)
        write_object(receipt, _object.validate_json(saved.model_dump_json()))
        return inputs


def work_busy() -> bool:
    return bool(_tasks)


async def recover() -> None:
    global _closing
    _closing = False


async def shutdown() -> None:
    global _closing
    _closing = True
    tasks = tuple(task for task in _tasks if task is not asyncio.current_task())
    for task in tasks:
        task.cancel()
    if tasks:
        await await_cleanup(asyncio.gather(*tasks, return_exceptions=True))
