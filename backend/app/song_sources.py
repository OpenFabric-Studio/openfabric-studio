"""Reusable enqueue-time source retention; no queue, model work or public paths."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from fractions import Fraction
import math
import os
from pathlib import Path
import shutil
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, Field, TypeAdapter

from . import audio_versions, beat_jobs, beat_store, db
from .audio_quality import INPUT
from .atomic_files import write_object
from .beat_contracts import BeatError, BeatId, BeatSha256, document_sha256
from .beat_song_mix_contracts import BeatSongMixError, SongSource, SongSourceInfo, SongSourceSnapshot
from .export_provenance import track_component
from .job_lifecycle import await_cleanup, kill_process_tree, run_owned_thread
from .music_evidence import shareable_music_evidence, track_music_evidence
from .video_io import copy_verified, hash_file
from .video_media import tool
from .video_process import WorkerIdentity, drain_receipt, read_owned_output, spawn_owned, terminate_verified
from .video_projects import VideoProjectError

MAX_BYTES = 8 * 1024**3
_id = TypeAdapter(BeatId)
_sha = TypeAdapter(BeatSha256)
_probe_tasks: dict[str, asyncio.Task[bytes]] = {}
_probe_quarantine: set[str] = set()
_probe_closing = False
_capture_paths: set[Path] = set()


def _probe_root() -> Path:
    return _root(db.FILES_DIR / '_song_sources' / '_probes', create=True)


def _probe_folder(identifier: str) -> Path:
    folder = _probe_root() / _id.validate_python(identifier)
    if folder.is_symlink() or folder.resolve() != folder:
        raise BeatSongMixError('song_cleanup_failed')
    return folder


def _probe_identity(folder: Path, identity: WorkerIdentity) -> None:
    if Path(identity.receipt) != folder / 'worker.json':
        raise BeatSongMixError('song_cleanup_failed')
    write_object(folder / 'identity.json', {'pid': identity.pid, 'token': identity.token, 'receipt': 'worker.json'})


async def _probe_command(identifier: str, argv: list[str]) -> bytes:
    folder = _probe_folder(identifier)
    process: asyncio.subprocess.Process | None = None
    try:
        process = await spawn_owned(argv, receipt_path=folder / 'worker.json', stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, on_identity=lambda value: _probe_identity(folder, value))
        output = await read_owned_output(process, max_bytes=1024**2, timeout=60)
        if process.returncode != 0:
            raise BeatSongMixError('song_conditioning_invalid')
        return output
    finally:
        try:
            if process is None:
                # A failure during spawn may itself be an unproven drain. The
                # persisted directory/receipt stays quarantined, even before
                # on_identity managed to return a live handle.
                raise BeatSongMixError('song_cleanup_failed')
            await await_cleanup(kill_process_tree(process))
            if process.returncode is None:
                raise BeatSongMixError('song_cleanup_failed')
        except BaseException:
            _probe_quarantine.add(identifier)
            raise
        else:
            if process.stdin is not None:
                process.stdin.close()
            await run_owned_thread(shutil.rmtree, folder)
            _probe_quarantine.discard(identifier)


async def _run_probe(argv: list[str]) -> bytes:
    if _probe_closing or _probe_quarantine or len(_probe_tasks) >= 32:
        raise BeatSongMixError('song_cleanup_failed')
    identifier = uuid.uuid4().hex
    folder = _probe_folder(identifier)
    folder.mkdir(exist_ok=False)
    entered = False

    async def operation() -> bytes:
        nonlocal entered
        entered = True
        return await _probe_command(identifier, argv)

    task = asyncio.create_task(operation(), name='song-source-probe:' + identifier)
    _probe_tasks[identifier] = task
    try:
        return await task
    finally:
        _probe_tasks.pop(identifier, None)
        if not entered:
            # Cancellation before the owned coroutine began cannot have
            # spawned a child. An empty preallocated directory is not proof
            # of an unknown worker and must not wedge the next startup.
            await run_owned_thread(shutil.rmtree, folder)


def work_busy() -> bool:
    return bool(_probe_tasks or _probe_quarantine or _capture_paths)


async def _recover_probes() -> None:
    folder = _probe_root()
    for candidate in folder.iterdir():
        identifier = candidate.name
        try:
            _id.validate_python(identifier)
        except ValueError:
            _probe_quarantine.add(identifier)
            continue
        if identifier in _probe_tasks:
            continue
        _probe_quarantine.add(identifier)
        try:
            directory = _probe_folder(identifier)
            identity_path, receipt = directory / 'identity.json', directory / 'worker.json'
            if identity_path.is_file() and not identity_path.is_symlink() and identity_path.stat().st_size <= 16384:
                stored = WorkerIdentity.model_validate_json(identity_path.read_bytes())
                if stored.receipt != 'worker.json' or receipt.is_symlink() or receipt.resolve() != receipt:
                    continue
                identity = WorkerIdentity(pid=stored.pid, token=stored.token, receipt=str(receipt))
                if not await terminate_verified(identity):
                    continue
            else:
                # Exact receipt proof handles spawn interruption before the
                # parent persisted identity. No receipt means no safe guess.
                await drain_receipt(receipt)
            await run_owned_thread(shutil.rmtree, directory)
            _probe_quarantine.discard(identifier)
        except (OSError, ValueError, BeatSongMixError):
            continue


async def recover() -> None:
    global _probe_closing
    _probe_closing = True
    try:
        await _recover_probes()
    finally:
        _probe_closing = False


async def shutdown() -> None:
    global _probe_closing
    _probe_closing = True
    for task in list(_probe_tasks.values()):
        task.cancel()
        await await_cleanup(asyncio.gather(task, return_exceptions=True))
    await _recover_probes()


def _root(path: Path, *, create: bool = False) -> Path:
    base = db.FILES_DIR.resolve()
    candidate = path.absolute()
    # The configured root may contain a platform alias (/var -> /private/var).
    # Everything below that trusted root must have a single canonical identity.
    relative = (candidate.relative_to(base) if candidate.is_relative_to(base) else
        candidate.relative_to(db.FILES_DIR.absolute()) if candidate.is_relative_to(db.FILES_DIR.absolute()) else None)
    if relative is None or path.is_symlink() or db.FILES_DIR.is_symlink():
        raise BeatSongMixError('song_storage_unavailable')
    candidate = base / relative
    if candidate.resolve() != candidate or not candidate.is_relative_to(base):
        raise BeatSongMixError('song_storage_unavailable')
    if create:
        candidate.mkdir(parents=True, exist_ok=True)
    if not candidate.is_dir():
        raise BeatSongMixError('song_storage_unavailable')
    return candidate


def owned_root(owner_id: str, *, create: bool = False) -> Path:
    """Default relative namespace; callers may supply their own FILES_DIR root."""
    return _root(db.FILES_DIR / '_song_sources' / _id.validate_python(owner_id), create=create)


def _source_path(source: SongSource) -> tuple[Path, int, str]:
    try:
        if source.kind == 'beat_render':
            resolved = beat_jobs.render_source_metadata(source.beat_id, source.beat_revision, source.render_id, source.sha256)
            path, track, version = resolved.path, resolved.track_id, resolved.version_id
        else:
            path = audio_versions.resolve_source(source.track_id, source.version_id)
            track, version = source.track_id, source.version_id
        if (path.is_symlink() or path.resolve() != path or not path.is_relative_to(db.FILES_DIR.resolve())
                or not path.is_file() or path.suffix.lower() not in {'.wav', '.mp3', '.flac', '.ogg', '.m4a'}):
            raise BeatSongMixError('song_source_missing')
        return path, track, version
    except (OSError, HTTPException, BeatError) as error:
        raise BeatSongMixError('song_source_missing') from error


class _Stream(BaseModel):
    sample_rate: str = Field(max_length=12)
    channels: int = Field(ge=1, le=2)
    duration: str | None = Field(default=None, max_length=64)
    duration_ts: int | None = Field(default=None, ge=1)
    time_base: str | None = Field(default=None, max_length=64)


class _Format(BaseModel):
    duration: str | None = Field(default=None, max_length=64)


class _Probe(BaseModel):
    streams: list[_Stream] = Field(min_length=1, max_length=1)
    format: _Format = Field(default_factory=_Format)


@dataclass(frozen=True)
class SourceFacts:
    sample_rate_hz: int
    channels: int
    frames: int
    source_bytes: int

    @property
    def duration_sec(self) -> float:
        return self.frames / self.sample_rate_hz


async def probe(path: Path) -> SourceFacts:
    output = await _run_probe([tool('ffprobe'), '-v', 'error', *INPUT, '-select_streams', 'a:0',
        '-show_entries', 'stream=sample_rate,channels,duration,duration_ts,time_base:format=duration',
        '-of', 'json', str(path)])
    try:
        value = _Probe.model_validate_json(output)
        stream = value.streams[0]
        duration = (float(stream.duration_ts * Fraction(stream.time_base))
            if stream.duration_ts is not None and stream.time_base is not None else float(stream.duration or value.format.duration or '0'))
        rate, size = int(stream.sample_rate), path.stat().st_size
        if not 8000 <= rate <= 192000 or not math.isfinite(duration) or not 0 < duration <= 21600 or not 0 < size <= MAX_BYTES:
            raise ValueError('source_bounds')
        return SourceFacts(rate, stream.channels, round(duration * rate), size)
    except (OSError, ValueError, OverflowError) as error:
        raise BeatSongMixError('song_conditioning_invalid') from error


async def inspect_source(source: SongSource, *, expected_sha256: str | None = None) -> SongSourceInfo:
    """Resolve DB identity synchronously, hash/probe in caller-owned async work."""
    path, track, version = _source_path(source)
    checksum = await hash_file(path, max_bytes=MAX_BYTES)
    if expected_sha256 is not None and checksum != _sha.validate_python(expected_sha256):
        raise BeatSongMixError('song_source_changed')
    if source.kind == 'beat_render' and checksum != source.sha256:
        raise BeatSongMixError('song_source_changed')
    facts = await probe(path)
    component = track_component(track, version, checksum)
    document = None
    musical_duration = None
    if source.kind == 'beat_render':
        document = beat_store.get_revision(source.beat_id, source.beat_revision)
        metadata = beat_jobs.render_source_metadata(source.beat_id, source.beat_revision, source.render_id, source.sha256)
        if document_sha256(document) != metadata.document_sha256:
            raise BeatSongMixError('song_source_changed')
        manifest = beat_jobs.get(source.render_id).manifest
        if manifest is None:
            raise BeatSongMixError('song_source_changed')
        musical_duration = manifest.musical_frames / manifest.sample_rate_hz
    if await hash_file(path, max_bytes=MAX_BYTES) != checksum:
        raise BeatSongMixError('song_source_changed')
    return SongSourceInfo(source=source, sha256=checksum, source_bytes=facts.source_bytes,
        sample_rate_hz=facts.sample_rate_hz, channels=facts.channels, frames=facts.frames,
        duration_sec=facts.duration_sec, musical_duration_sec=musical_duration, track_id=track, version_id=version,
        content_origin=component.content_origin, music_evidence=shareable_music_evidence(track_music_evidence(track)),
        beat_document=document)


def _fsync(path: Path) -> None:
    with path.open('rb') as handle:
        os.fsync(handle.fileno())
    if os.name != 'nt':
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _bounds(snapshot: SongSourceInfo, max_bytes: int, max_seconds: float) -> None:
    if (type(max_bytes) is not int or not 0 < max_bytes <= MAX_BYTES or isinstance(max_seconds, bool)
            or not math.isfinite(max_seconds) or not 0 < max_seconds <= 21600
            or snapshot.source_bytes > max_bytes or snapshot.duration_sec > max_seconds):
        raise BeatSongMixError('song_conditioning_invalid')


def _read_snapshot(path: Path, source_id: str, source: SongSource, expected_sha256: str,
                   max_bytes: int, max_seconds: float) -> SongSourceSnapshot:
    if path.is_symlink() or path.resolve() != path or path.stat().st_size > 8 * 1024**2:
        raise BeatSongMixError('song_source_changed')
    snapshot = SongSourceSnapshot.model_validate_json(path.read_bytes())
    if snapshot.source_id != source_id or snapshot.source != source or snapshot.sha256 != expected_sha256:
        raise BeatSongMixError('song_source_changed')
    _bounds(snapshot, max_bytes, max_seconds)
    return snapshot


def _write_snapshot(path: Path, snapshot: SongSourceSnapshot) -> None:
    from .contracts import JsonObject
    body = snapshot.model_dump_json()
    if len(body.encode()) > 8 * 1024**2:
        raise BeatSongMixError('song_conditioning_invalid')
    write_object(path, TypeAdapter(JsonObject).validate_json(body))


async def load_retained_source(root: Path, source_id: str, source: SongSource, expected_sha256: str, *,
                               max_bytes: int = MAX_BYTES, max_seconds: float = 21600) -> SongSourceSnapshot | None:
    """Recover a completed copy before catalog lookup, including an interrupted journal write.

    Intent/completion are backend-owned metadata, never an HTTP upload. A
    complete snapshot with damaged bytes fails; an unfinished copy is not used.
    """
    base = _root(root)
    folder = base / _id.validate_python(source_id)
    if not folder.exists():
        return None
    _root(folder)
    ready, intent = folder / 'source.json', folder / 'intent.json'
    path = ready if ready.exists() else intent
    if not path.exists():
        raise BeatSongMixError('song_source_changed')
    snapshot = _read_snapshot(path, source_id, source, _sha.validate_python(expected_sha256), max_bytes, max_seconds)
    raw = folder / snapshot.raw_filename
    if not raw.exists() and not ready.exists():
        return None
    try:
        await resolve_snapshot(base, snapshot)
    except BeatSongMixError as error:
        if error.code in ('song_cleanup_failed', 'beat_mix_busy', 'song_storage_unavailable'):
            raise
        if ready.exists():
            raise
        return None
    except OSError:
        if ready.exists():
            raise
        return None
    if not ready.exists():
        await run_owned_thread(_write_snapshot, ready, snapshot)
    return snapshot


async def retain_source(source: SongSource, destination_root: Path, *, source_id: str,
                        expected_sha256: str, max_bytes: int = MAX_BYTES, max_seconds: float = 21600,
                        disk_reserve_bytes: int = 512 * 1024**2) -> SongSourceSnapshot:
    """Exclusively copy and fsync before enqueue; no later catalog lookup needed.

    destination_root is backend-owned, not accepted from an HTTP caller. The
    returned snapshot is an internal proof; accepting one from a user would
    merely turn an assertion into provenance and is deliberately unsupported.
    """
    root, source_id = _root(destination_root), _id.validate_python(source_id)
    folder = root / source_id
    if folder in _capture_paths or _probe_closing:
        raise BeatSongMixError('beat_mix_busy')
    _capture_paths.add(folder)
    try:
        reused = await load_retained_source(root, source_id, source, expected_sha256, max_bytes=max_bytes, max_seconds=max_seconds)
        if reused is not None:
            return reused
        path, _, _ = _source_path(source)
        if type(max_bytes) is not int or not 0 < max_bytes <= MAX_BYTES or path.stat().st_size > max_bytes:
            raise BeatSongMixError('song_conditioning_invalid')
        intent = folder / 'intent.json'
        if intent.exists():
            snapshot = _read_snapshot(intent, source_id, source, expected_sha256, max_bytes, max_seconds)
        else:
            info = await inspect_source(source, expected_sha256=expected_sha256)
            _bounds(info, max_bytes, max_seconds)
            snapshot = SongSourceSnapshot.model_validate({**info.model_dump(), 'source_id': source_id, 'raw_filename': 'raw' + path.suffix.lower()})
        _bounds(snapshot, max_bytes, max_seconds)
        if (type(disk_reserve_bytes) is not int or not 0 <= disk_reserve_bytes <= 16 * 1024**3):
            raise BeatSongMixError('song_conditioning_invalid')
        if shutil.disk_usage(root).free < snapshot.source_bytes + disk_reserve_bytes + 65536:
            raise BeatSongMixError('insufficient_disk')
        if not folder.exists():
            folder.mkdir(exist_ok=False)
        _root(folder)
        if not intent.exists():
            await run_owned_thread(_write_snapshot, intent, snapshot)
        raw = folder / snapshot.raw_filename
        if raw.exists():
            # Only an unfinished, hash-invalid copy reaches here. It belongs
            # to this exact persisted intent, not another source or a symlink.
            if raw.is_symlink() or raw.resolve() != raw:
                raise BeatSongMixError('song_source_changed')
            raw.unlink()
        await copy_verified(path, raw, snapshot.sha256, max_bytes=max_bytes)
        await run_owned_thread(_fsync, raw)
        facts = await probe(raw)
        if (facts.sample_rate_hz, facts.channels, facts.frames, facts.source_bytes) != (
                snapshot.sample_rate_hz, snapshot.channels, snapshot.frames, snapshot.source_bytes):
            raise BeatSongMixError('song_source_changed')
        await run_owned_thread(_write_snapshot, folder / 'source.json', snapshot)
        return snapshot
    finally:
        _capture_paths.discard(folder)


def snapshot_path(root: Path, snapshot: SongSourceSnapshot) -> Path:
    """Metadata-only confined projection; resolve_snapshot verifies the bytes."""
    base = _root(root)
    path = base / snapshot.source_id / snapshot.raw_filename
    if path.is_symlink() or path.resolve() != path or not path.is_file() or not path.is_relative_to(base):
        raise BeatSongMixError('song_source_missing')
    return path


async def resolve_snapshot(root: Path, snapshot: SongSourceSnapshot) -> Path:
    path = snapshot_path(root, snapshot)
    try:
        if await hash_file(path, max_bytes=MAX_BYTES) != snapshot.sha256:
            raise BeatSongMixError('song_source_changed')
        facts = await probe(path)
        if (facts.sample_rate_hz, facts.channels, facts.frames, facts.source_bytes) != (
                snapshot.sample_rate_hz, snapshot.channels, snapshot.frames, snapshot.source_bytes):
            raise BeatSongMixError('song_source_changed')
        return path
    except (OSError, VideoProjectError) as error:
        raise BeatSongMixError('song_source_changed') from error
