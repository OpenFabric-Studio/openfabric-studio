"""Immutable originals and independently owned cloned-voice versions."""
from __future__ import annotations

import asyncio
import logging
import math
import re
import shutil
import sqlite3
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import Field, TypeAdapter, ValidationError, field_validator

from . import db
from . import audio_version_store as store
from .atomic_files import read_object
from .audio_version_contracts import AudioVersion, AudioVersionId, TrackAudioVersionsResponse
from .contracts import Contract, JobStatus, JsonObject
from .job_lifecycle import await_cleanup
from .voice_contracts import VoiceJobProgress
from .voice_progress import read_progress

logger = logging.getLogger(__name__)
_ACTIVE = {'queued', 'running'}
_EXTENSIONS = {'wav', 'mp3', 'flac', 'ogg', 'opus', 'm4a'}
_deleting: set[int] = set()


class _ApplyReceipt(Contract):
    status: JobStatus
    audio_version_id: AudioVersionId | None = None
    error_code: str = Field(default='', max_length=200)
    job_progress: VoiceJobProgress | None = None

    @field_validator('job_progress', mode='before')
    @classmethod
    def optional_progress(cls, value: object) -> VoiceJobProgress | None:
        return read_progress(value)


def _receipt(worker_track_id: int) -> _ApplyReceipt | None:
    from .voice_build import voices_root
    try:
        return _ApplyReceipt.model_validate(read_object(voices_root() / '_apply' / f'{worker_track_id}.json'))
    except (OSError, ValueError):
        return None


def _track(track_id: int) -> sqlite3.Row:
    row = db.get_track(track_id)
    if row is None:
        raise HTTPException(status_code=404, detail='track_missing')
    return row


def _params(row: sqlite3.Row) -> JsonObject:
    raw: object = row['params_json']
    if not isinstance(raw, str):
        raise HTTPException(status_code=409, detail='invalid_track_provenance')
    try:
        return TypeAdapter(JsonObject).validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=409, detail='invalid_track_provenance') from exc


def _id(version_id: str) -> None:
    if re.fullmatch(r'[0-9a-f]{32}', version_id) is None:
        raise HTTPException(status_code=404, detail='audio_version_missing')


def _contained(path: Path, *, exists: bool = True) -> Path:
    root = db.FILES_DIR.resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or (exists and not resolved.is_file()):
        raise HTTPException(status_code=409, detail='audio_version_unavailable')
    return resolved


def _directory(track_id: int) -> Path:
    path = db.FILES_DIR / '_versions' / str(track_id)
    _contained(path, exists=False)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _duration(row: sqlite3.Row) -> float | None:
    value: object = row['duration_ms']
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def _snapshot(source: Path, destination: Path) -> None:
    source = _contained(source)
    _contained(destination, exists=False)
    partial = destination.with_suffix(destination.suffix + '.partial')
    try:
        with source.open('rb') as reader, partial.open('xb') as writer:
            shutil.copyfileobj(reader, writer, 1024 * 1024)
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def _new_record(track_id: int, kind: Literal['original', 'voice'], source: Path, *, voice_id: str | None = None,
                source_version_id: str | None = None, worker_track_id: int | None = None,
                status: JobStatus = 'done', captured_source: Path | None = None) -> store.StoredAudioVersion:
    from .voice_build import _voice_name
    row = _track(track_id)
    version_id = uuid.uuid4().hex
    extension = source.suffix.lower().lstrip('.') if kind == 'original' else 'wav'
    if extension not in _EXTENSIONS:
        raise HTTPException(status_code=409, detail='audio_version_unavailable')
    destination = _directory(track_id) / f'{version_id}.{kind}.{extension}'
    public = AudioVersion(id=version_id, track_id=track_id, kind=kind, voice_id=voice_id,
        voice_name=(_voice_name(voice_id) or None) if voice_id is not None else None, status=status,
        created_at=datetime.now(timezone.utc).isoformat(), duration_ms=_duration(row), source_version_id=source_version_id)
    captured = _contained(source) if status == 'done' else (
        _contained(captured_source, exists=False) if captured_source is not None else None)
    record = store.StoredAudioVersion(public, destination, worker_track_id, captured)
    try:
        if status == 'done':
            _snapshot(source, destination)
        store.insert(db.get_db(), record)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return record


def retain_original(track_id: int, source: Path) -> AudioVersion:
    """Capture once, before default-path publication; later calls never replace it."""
    _track(track_id)
    existing = next((item for item in store.list_for_track(db.get_db(), track_id) if item.public.kind == 'original'), None)
    if existing is not None:
        return _public(existing)
    return _public(_new_record(track_id, 'original', source))


def _recover_legacy(track_id: int) -> None:
    if store.list_for_track(db.get_db(), track_id):
        return
    row = _track(track_id)
    raw_path: object = row['audio_path']
    if not isinstance(raw_path, str):
        return
    path = Path(raw_path)
    params = _params(row)
    if params.get('source') == 'voice_variant':
        raise HTTPException(status_code=404, detail='track_missing')
    try:
        path = _contained(path)
    except HTTPException:
        return
    if params.get('source') == 'voice_replacement':
        from .voice_build import _apply_paths, VoiceBuildError
        try:
            source, _destination = _apply_paths(row)
            retain_original(track_id, source)
        except VoiceBuildError:
            pass
        receipt = _receipt(track_id)
        if receipt is None or receipt.status != 'done':
            # Replacement accepts a dry decoded placeholder before conversion.
            # Its filename alone is never evidence of completed voice conversion.
            return
    elif not path.name.endswith('.voiced.wav'):
        retain_original(track_id, path)
        return
    else:
        # Prior conversion named the deterministic sibling; only one matching
        # original with a supported extension can be recovered unambiguously.
        stem = path.name.removesuffix('.voiced.wav')
        extension = params.get('audio_format')
        candidates = [path.with_name(f'{stem}.{ext}') for ext in _EXTENSIONS
                      if extension is None or extension == ext]
        contained: list[Path] = []
        for candidate in candidates:
            try:
                contained.append(_contained(candidate))
            except HTTPException:
                pass
        if len(contained) == 1:
            retain_original(track_id, contained[0])
    originals = [item for item in store.list_for_track(db.get_db(), track_id) if item.public.kind == 'original']
    voice_id = params.get('voice_id')
    known_voice = voice_id if isinstance(voice_id, str) and re.fullmatch(r'[0-9a-f]{32}', voice_id) else None
    _new_record(track_id, 'voice', path, voice_id=known_voice,
        source_version_id=originals[0].public.id if originals else None)


def _stored(track_id: int, version_id: str) -> store.StoredAudioVersion:
    _id(version_id)
    record = store.get(db.get_db(), version_id)
    if record is None or record.public.track_id != track_id:
        raise HTTPException(status_code=404, detail='audio_version_missing')
    return record


def _public(record: store.StoredAudioVersion) -> AudioVersion:
    public = record.public.model_copy(deep=True)
    if record.worker_track_id is not None:
        from . import voice_build
        active = voice_build._applies.get(record.worker_track_id)
        if active is not None and active.audio_version_id == public.id:
            public.job_progress = read_progress(voice_build._apply_payload(active).get('job_progress'))
        else:
            receipt = _receipt(record.worker_track_id)
            if receipt is not None and receipt.audio_version_id == public.id:
                public.job_progress = receipt.job_progress
    if public.status == 'done' and record.path is not None:
        try:
            path = _contained(record.path)
        except HTTPException:
            public.status = 'failed'
            public.error_code = 'audio_version_unavailable'
        else:
            public.filename = path.name
            public.audio_url = f'/api/tracks/{public.track_id}/versions/{public.id}/audio'
    return public


def _reconcile(record: store.StoredAudioVersion) -> None:
    from . import voice_build
    if record.public.status not in _ACTIVE:
        return
    worker = record.worker_track_id
    active = voice_build._applies.get(worker) if worker is not None else None
    if active is not None and active.audio_version_id == record.public.id:
        return
    status: JobStatus = 'failed'
    error = 'interrupted'
    if worker is not None:
        voice_build.apply_status(worker)  # Freeze an interrupted receipt once.
        receipt = _receipt(worker)
        if receipt is not None and receipt.audio_version_id == record.public.id and receipt.status not in _ACTIVE:
            status, error = receipt.status, receipt.error_code
    store.set_state(db.get_db(), record.public.id, status, error)


def list_versions(track_id: int) -> TrackAudioVersionsResponse:
    _track(track_id)
    _recover_legacy(track_id)
    for record in store.list_for_track(db.get_db(), track_id):
        _reconcile(record)
    versions = [_public(record) for record in store.list_for_track(db.get_db(), track_id)]
    return TrackAudioVersionsResponse(track_id=track_id,
        original_available=any(item.kind == 'original' and item.status == 'done' for item in versions), versions=versions)


def resolve_source(track_id: int, version_id: str) -> Path:
    """Return only an available immutable version; never infer the latest audio."""
    _track(track_id)
    record = _stored(track_id, version_id)
    _reconcile(record)
    record = _stored(track_id, version_id)
    if record.public.status != 'done' or record.path is None:
        raise HTTPException(status_code=409, detail='audio_version_unavailable')
    return _contained(record.path)


def _original(track_id: int) -> store.StoredAudioVersion:
    listed = list_versions(track_id)
    original = next((item for item in listed.versions if item.kind == 'original' and item.status == 'done'), None)
    if original is None:
        raise HTTPException(status_code=409, detail='original_audio_missing')
    return _stored(track_id, original.id)


def prepare_application(track_id: int, voice_id: str) -> AudioVersion:
    """Attach legacy automatic application to the immutable original catalog."""
    original = _original(track_id)
    if original.path is None:
        raise HTTPException(status_code=409, detail='original_audio_missing')
    row = _track(track_id)
    raw_current: object = row['audio_path']
    captured = Path(raw_current) if isinstance(raw_current, str) else None
    return _new_record(track_id, 'voice', original.path, voice_id=voice_id,
        source_version_id=original.public.id, worker_track_id=track_id, status='queued', captured_source=captured).public


def application_paths(worker_track_id: int, version_id: str | None) -> tuple[Path, Path]:
    if version_id is None:
        raise HTTPException(status_code=409, detail='original_audio_missing')
    record = store.get(db.get_db(), version_id)
    if record is None or record.worker_track_id != worker_track_id or record.path is None or record.public.source_version_id is None:
        raise HTTPException(status_code=409, detail='original_audio_missing')
    source = resolve_source(record.public.track_id, record.public.source_version_id)
    destination = _contained(record.path, exists=False)
    if source == destination:
        raise HTTPException(status_code=409, detail='invalid_track_provenance')
    return source, destination


def sync_application(version_id: str | None, worker_track_id: int, status: str, error_code: str) -> None:
    if version_id is None:
        return
    record = store.get(db.get_db(), version_id)
    if record is None or record.worker_track_id != worker_track_id:
        raise ValueError('invalid_version_worker')
    validated: JobStatus = TypeAdapter(JobStatus).validate_python(status)
    store.set_state(db.get_db(), version_id, validated, error_code)


def version_for_worker(worker_track_id: int) -> str | None:
    rows = db.get_db().execute('SELECT id FROM audio_versions WHERE worker_track_id=? AND track_id!=?',
                              (worker_track_id, worker_track_id)).fetchall()
    if len(rows) != 1:
        return None
    value: object = rows[0]['id']
    return value if isinstance(value, str) else None


def _rollback(record: store.StoredAudioVersion) -> None:
    store.remove(db.get_db(), record.public.id)
    if record.worker_track_id is not None and record.worker_track_id != record.public.track_id:
        db.delete_track(record.worker_track_id)
    if record.path is not None:
        _contained(record.path, exists=False).unlink(missing_ok=True)


def start_version(track_id: int, voice_id: str) -> AudioVersion:
    from . import voice_build
    if track_id in _deleting:
        raise HTTPException(status_code=409, detail='audio_version_busy')
    voice_build.require_ready_voice(voice_id)
    row = _track(track_id)
    original = _original(track_id)
    if original.path is None:
        raise HTTPException(status_code=409, detail='original_audio_missing')
    record = _new_record(track_id, 'voice', original.path, voice_id=voice_id,
        source_version_id=original.public.id, status='queued')
    try:
        if record.path is None:
            raise ValueError('missing_version_path')
        child = db.insert_track(model='upload', title=str(row['title']), lyrics='', seed=None,
            duration_ms=_duration(row), wall_ms=None,
            params={'source':'voice_variant','parent_track_id':track_id,'parent_source_version_id':original.public.id,
                    'audio_version_id':record.public.id,'voice_id':voice_id}, audio_path=record.path, abc_path=None,
                    audio_version_id=record.public.id)
        record = _stored(track_id, record.public.id)
        voice_build.start_apply(voice_id, child)
    except BaseException:
        _rollback(_stored(track_id, record.public.id))
        raise
    return _public(_stored(track_id, record.public.id))


async def cancel_version(track_id: int, version_id: str) -> AudioVersion:
    from .voice_build import cancel_apply
    _track(track_id)
    record = _stored(track_id, version_id)
    _reconcile(record)
    record = _stored(track_id, version_id)
    if record.public.status in _ACTIVE or record.public.error_code == 'apply_cleanup_failed':
        if record.worker_track_id is not None:
            await cancel_apply(record.worker_track_id)
    return _public(_stored(track_id, version_id))


async def retry_version(track_id: int, version_id: str) -> AudioVersion:
    from . import voice_build
    if track_id in _deleting:
        raise HTTPException(status_code=409, detail='audio_version_busy')
    record = _stored(track_id, version_id)
    _reconcile(record)
    record = _stored(track_id, version_id)
    if record.public.kind != 'voice' or record.public.status not in {'failed', 'cancelled'} or record.public.voice_id is None or record.worker_track_id is None:
        raise HTTPException(status_code=409, detail='audio_version_busy')
    voice_build.require_ready_voice(record.public.voice_id)
    application_paths(record.worker_track_id, record.public.id)
    voice_build.start_apply(record.public.voice_id, record.worker_track_id, audio_version_id=record.public.id)
    return _public(_stored(track_id, version_id))


@asynccontextmanager
async def protect_track_versions_removal(track_id: int) -> AsyncIterator[None]:
    from .voice_build import cancel_apply
    if track_id in _deleting:
        raise HTTPException(status_code=409, detail='audio_version_busy')
    _deleting.add(track_id)
    try:
        workers = {record.worker_track_id for record in store.list_for_track(db.get_db(), track_id) if record.worker_track_id is not None}
        async def drain() -> None:
            outcomes = await asyncio.gather(*(cancel_apply(worker) for worker in workers), return_exceptions=True)
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    raise HTTPException(status_code=409, detail='apply_cleanup_failed') from outcome
        await await_cleanup(drain())
        yield
    finally:
        _deleting.discard(track_id)


def remove_track_files(track_id: int) -> None:
    """Synchronous deletion after the public async drain guard has finished."""
    from .voice_build import application_active
    records = store.list_for_track(db.get_db(), track_id)
    if any(record.worker_track_id is not None and application_active(record.worker_track_id) for record in records):
        raise HTTPException(status_code=409, detail='audio_version_busy')
    for record in records:
        if record.worker_track_id is not None and record.worker_track_id != track_id:
            db.delete_track(record.worker_track_id)
        if record.path is not None:
            try:
                _contained(record.path, exists=False).unlink(missing_ok=True)
            except HTTPException:
                logger.warning('Refused escaped audio version file for track %s', track_id)
        if record.captured_source is not None:
            _remove_unshared_capture(track_id, record.captured_source)
    directory = db.FILES_DIR / '_versions' / str(track_id)
    try:
        _contained(directory, exists=False)
        directory.rmdir()
    except (OSError, HTTPException):
        pass


def _remove_unshared_capture(track_id: int, path: Path) -> None:
    """Clean a proven prior file while retaining independent/shared track sources."""
    try:
        source = _contained(path, exists=False)
    except HTTPException:
        return
    rows = db.get_db().execute('SELECT audio_path FROM tracks WHERE id!=?', (track_id,)).fetchall()
    for row in rows:
        value: object = row['audio_path']
        if isinstance(value, str) and Path(value).resolve() == source:
            return
    version_rows = db.get_db().execute('SELECT audio_path,captured_source_path FROM audio_versions WHERE track_id!=?', (track_id,)).fetchall()
    for row in version_rows:
        for name in ('audio_path', 'captured_source_path'):
            value = row[name]
            if isinstance(value, str) and Path(value).resolve() == source:
                return
    source.unlink(missing_ok=True)


async def recover() -> None:
    """Reconcile interrupted statuses; never restart model inference."""
    for record in store.list_active(db.get_db()):
        _reconcile(record)


async def shutdown() -> None:
    from .voice_build import application_active, cancel_apply
    rows = db.get_db().execute('SELECT DISTINCT worker_track_id FROM audio_versions WHERE worker_track_id IS NOT NULL').fetchall()
    workers: set[int] = set()
    for row in rows:
        worker: object = row['worker_track_id']
        if isinstance(worker, int) and application_active(worker):
            workers.add(worker)
    await await_cleanup(asyncio.gather(*(cancel_apply(worker) for worker in workers)))
