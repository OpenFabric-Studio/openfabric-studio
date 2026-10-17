"""Durable CPU beat jobs, using the application's existing owned supervisor.

SQLite owns document snapshots, admission and track attachment. A browser only
observes jobs; disconnecting never cancels them. No native model is involved.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import errno
import hashlib
import logging
import math
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import uuid

from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError

from . import beat_render, beat_store, db
from .beat_compile import CompiledBeat, compile_events, compiled_events_json
from .beat_contracts import (BeatDocument, BeatError, BeatErrorCode, BeatId, BeatInstrumentManifest,
    BeatJob, BeatJobRequest, BeatRenderManifest, BeatArtifact, BeatSha256, BeatStoragePreview, canonical_json)
from .contracts import JsonObject
from .job_lifecycle import await_cleanup, cancel_and_wait, kill_process_tree, run_owned_thread
from .video_process import (WorkerIdentity, WorkerOutputError, read_owned_output, spawn_owned,
    terminate_verified)

logger = logging.getLogger(__name__)
_lock = asyncio.Lock()
_semaphore = asyncio.Semaphore(1)
_tasks: dict[str, asyncio.Task[None]] = {}
_processes: dict[str, asyncio.subprocess.Process] = {}
_unverified: dict[str, WorkerIdentity] = {}
_closing = False
_id = TypeAdapter(BeatId)
MAX_JOBS = 1000
MAX_QUEUED = 8
RESERVE = 256 * 1024 * 1024


@dataclass(frozen=True)
class _Record:
    job: BeatJob
    document: BeatDocument
    kit: BeatInstrumentManifest
    identity: WorkerIdentity | None
    track_id: int | None


def _identifier(value: str) -> str:
    try:
        return _id.validate_python(value)
    except ValidationError as exc:
        raise BeatError('beat_job_not_found') from exc


def _record(identifier: str) -> _Record:
    row = db.get_db().execute('SELECT * FROM beat_render_jobs WHERE id=?', (_identifier(identifier),)).fetchone()
    if row is None:
        raise BeatError('beat_job_not_found')
    job = BeatJob.model_validate_json(row['payload_json'])
    document = BeatDocument.model_validate_json(row['document_json'])
    kit = BeatInstrumentManifest.model_validate_json(row['kit_json'])
    identity = WorkerIdentity.model_validate_json(row['worker_identity_json']) if row['worker_identity_json'] else None
    raw_track: object = row['track_id']
    track_id = raw_track if isinstance(raw_track, int) and not isinstance(raw_track, bool) else None
    if (job.id != identifier or job.beat_id != document.id or job.beat_revision != document.revision
            or job.request.beat_id != document.id or job.request.expected_revision != document.revision):
        raise BeatError('beat_storage_unavailable')
    return _Record(job, document, kit, identity, track_id)


def _save(job: BeatJob) -> None:
    connection = db.get_db()
    connection.execute('UPDATE beat_render_jobs SET payload_json=? WHERE id=?',
        (canonical_json(job).decode('ascii'), job.id))
    connection.commit()


def _identity(identifier: str, value: WorkerIdentity | None) -> None:
    connection = db.get_db()
    connection.execute('UPDATE beat_render_jobs SET worker_identity_json=? WHERE id=?',
        (value.model_dump_json() if value is not None else None, identifier))
    connection.commit()


def get(identifier: str) -> BeatJob:
    record = _record(identifier)
    job = record.job
    if job.status == 'running':
        path = beat_render.workspace(identifier) / 'progress.json'
        try:
            from .atomic_files import read_object
            if path.is_symlink() or path.stat().st_size > 4096:
                return job
            values = read_object(path)
            done, total = values.get('done'), values.get('total')
            if isinstance(done, int) and not isinstance(done, bool) and 0 <= done <= 30240000 and total is None:
                return job.model_copy(update={'progress_frames': done, 'total_frames': None})
            if (isinstance(done, int) and not isinstance(done, bool) and isinstance(total, int)
                    and not isinstance(total, bool) and 0 <= done <= total <= 30240000 and total > 0):
                return job.model_copy(update={'progress_frames': done, 'total_frames': total})
        except (OSError, ValueError):
            pass
    return job


def list_jobs(beat_id: str | None = None) -> list[BeatJob]:
    if beat_id is not None:
        _identifier(beat_id)
    rows = db.get_db().execute('SELECT id FROM beat_render_jobs WHERE (? IS NULL OR beat_id=?) ORDER BY created_at DESC,rowid DESC LIMIT 1000',
        (beat_id, beat_id)).fetchall()
    return [get(row['id']) for row in rows]


def free_bytes(path: Path) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def _forecast(document: BeatDocument, request: BeatJobRequest, kit: BeatInstrumentManifest) -> BeatStoragePreview:
    compiled = compile_events(document, beat_render.RATE, kit, preview_pattern_id=request.preview_pattern_id,
        occurrence_index=request.occurrence_index)
    frames = beat_render.render_frame_bound(compiled)
    lanes = len(document.patterns[0].lanes)
    # FLOAT mix plus aligned stems, uncompressed share bundle, and retained dry
    # original version at full publication. JSON/MIDI budget is bounded by core.
    audio = frames * 8 * (lanes + 1) + (lanes + 1) * 4096
    output = audio * 2 + (frames * 8 if request.mode == 'full' else 0)
    # Bound both source/event JSON and tuning sidecars (including all 16 curve
    # knots per note), twice because the bundle retains a second copy. MIDI
    # itself has the core's independent one-million-message ceiling.
    metadata = 2 * (len(canonical_json(document)) + len(compiled_events_json(compiled))
        + len(compiled.events) * 4096 + 12000000 + 8 * 1024 * 1024)
    required = math.ceil((output + metadata) * 1.25) + RESERVE
    available = free_bytes(beat_render._root())
    return BeatStoragePreview(output_bytes=output, temporary_bytes=metadata, required_free_bytes=required,
        free_bytes=available, can_start=available is not None and available >= required,
        frames=frames, duration_sec=frames / beat_render.RATE)


def _snapshot(request: BeatJobRequest) -> tuple[BeatDocument, BeatInstrumentManifest]:
    document = beat_store.get(request.beat_id)
    if document.revision != request.expected_revision:
        raise BeatError('beat_revision_conflict')
    kit = beat_render.load_kit(document.kit_id, document.kit_manifest_sha256)
    return document, kit


async def preview(request: BeatJobRequest) -> BeatStoragePreview:
    document, kit = _snapshot(request)
    return await run_owned_thread(_forecast, document, request, kit)


async def start(request: BeatJobRequest) -> BeatJob:
    checksum = hashlib.sha256(canonical_json(request)).hexdigest()
    async with _lock:
        connection = db.get_db()
        existing = connection.execute('SELECT request_hash,job_id FROM beat_submissions WHERE submission_key=?',
            (request.submission_key,)).fetchone()
        if existing is not None:
            if existing['request_hash'] != checksum:
                raise BeatError('beat_submission_conflict')
            return get(existing['job_id'])
        if _closing or _unverified:
            raise BeatError('beat_cleanup_failed')
        if len(_tasks) >= MAX_QUEUED + 1:
            raise BeatError('beat_queue_full')
        if connection.execute('SELECT count(*) FROM beat_render_jobs').fetchone()[0] >= MAX_JOBS:
            raise BeatError('beat_queue_full')
        document, kit = _snapshot(request)
        estimate = await run_owned_thread(_forecast, document, request, kit)
        if not estimate.can_start:
            raise BeatError('beat_disk_full')
        # No transaction spans the await; recheck current revision before insert.
        job = BeatJob(id=uuid.uuid4().hex, beat_id=document.id, beat_revision=document.revision,
            request=request, status='queued', phase='queued', created_at=beat_store.now())
        connection.execute('BEGIN IMMEDIATE')
        try:
            row = connection.execute('SELECT revision,archived FROM beat_documents WHERE id=?', (document.id,)).fetchone()
            if row is None or row['archived'] or row['revision'] != request.expected_revision:
                raise BeatError('beat_revision_conflict')
            connection.execute('INSERT INTO beat_render_jobs(id,beat_id,beat_revision,request_hash,payload_json,document_json,kit_json,created_at) VALUES(?,?,?,?,?,?,?,?)',
                (job.id, document.id, document.revision, checksum, canonical_json(job).decode('ascii'),
                    canonical_json(document).decode('ascii'), canonical_json(kit).decode('ascii'), job.created_at))
            connection.execute('INSERT INTO beat_submissions(submission_key,request_hash,job_id) VALUES(?,?,?)',
                (request.submission_key, checksum, job.id))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        task = asyncio.create_task(_run(job.id))
        _tasks[job.id] = task
        return job


async def _drain(identifier: str, process: asyncio.subprocess.Process) -> None:
    try:
        await await_cleanup(kill_process_tree(process))
    except BaseException:
        identity = _record(identifier).identity
        if identity is not None:
            _unverified[identifier] = identity
        raise
    else:
        _processes.pop(identifier, None)
        _identity(identifier, None)
        if process.stdin is not None:
            process.stdin.close()


async def _execute_worker(identifier: str) -> BeatRenderManifest:
    record = _record(identifier)
    root = beat_render.workspace(identifier, create=True)
    request = beat_render.BeatWorkerInput(render_id=identifier, document=record.document,
        request=record.job.request, kit=record.kit)
    await run_owned_thread(beat_render._bytes, root / 'request.json', canonical_json(request))
    environment = {**os.environ, 'OPENFABRIC_DATA_DIR': str(db.FILES_DIR.parent)}
    process = await spawn_owned([sys.executable, '-m', 'app.beat_render', '--request', str(root / 'request.json'),
        '--files-dir', str(db.FILES_DIR.resolve())],
        receipt_path=root / 'worker-receipt.json', cwd=Path(__file__).resolve().parents[1], env=environment,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        on_identity=lambda identity: _identity(identifier, identity))
    _processes[identifier] = process
    draining = False
    try:
        output = await read_owned_output(process, max_bytes=65536, timeout=3600)
        draining = True
        await _drain(identifier, process)
        if process.returncode != 0:
            failure = beat_render.BeatWorkerFailure.model_validate_json(output)
            try:
                completed = await run_owned_thread(beat_render.validate_output, identifier, prepared=True)
            except BeatError:
                pass
            else:
                if (completed.document_sha256 == hashlib.sha256(canonical_json(record.document)).hexdigest()
                        and completed.beat_id == record.document.id and completed.beat_revision == record.document.revision):
                    raise BeatError('beat_save_failed')
            raise BeatError(failure.error_code)
        completion = beat_render.BeatWorkerResult.model_validate_json(output)
        manifest = await run_owned_thread(beat_render.validate_output, identifier)
        if completion.render_id != identifier or completion.manifest_sha256 != hashlib.sha256(canonical_json(manifest)).hexdigest():
            raise BeatError('beat_worker_failed')
        await run_owned_thread(_expected_manifest, record, manifest)
        return manifest
    finally:
        if not draining:
            await _drain(identifier, process)


def _expected_manifest(record: _Record, manifest: BeatRenderManifest) -> CompiledBeat:
    compiled = compile_events(record.document, beat_render.RATE, record.kit,
        preview_pattern_id=record.job.request.preview_pattern_id, occurrence_index=record.job.request.occurrence_index)
    if (manifest.render_id != record.job.id or manifest.beat_id != compiled.beat_id
            or manifest.beat_revision != compiled.beat_revision or manifest.document_sha256 != compiled.document_sha256
            or manifest.kit_manifest_sha256 != compiled.kit_manifest_sha256 or manifest.seed != compiled.seed
            or manifest.renderer_version != compiled.renderer_version or manifest.algorithm_version != compiled.algorithm_version
            or manifest.events_sha256 != compiled.events_sha256 or manifest.musical_frames != compiled.duration_frames
            or manifest.frames > compiled.maximum_render_frames):
        raise BeatError('beat_worker_failed')
    return compiled


async def _publish(identifier: str) -> BeatJob:
    record = _record(identifier)
    manifest = await run_owned_thread(beat_render.validate_output, identifier)
    compiled = await run_owned_thread(_expected_manifest, record, manifest)
    if record.identity is not None or identifier in _unverified or identifier in _processes:
        raise BeatError('beat_cleanup_failed')
    if record.job.request.mode == 'full':
        from .audio_versions import retain_original
        mix = next(item for item in manifest.artifacts if item.kind == 'mix')
        source = beat_render.workspace(identifier) / 'output' / mix.filename
        track_id = record.track_id
        if track_id is None:
            params: JsonObject = {'beat_id': record.document.id, 'beat_revision': record.document.revision,
                'beat_render_id': identifier, 'beat_document_sha256': manifest.document_sha256,
                'beat_events_sha256': manifest.events_sha256, 'source_ancestry_complete': True,
                '_music_evidence': beat_render.sample_evidence(record.kit, compiled).model_dump(mode='json')}
            track_id = db.insert_track(model='beat', title=record.document.title, lyrics='', seed=record.document.seed,
                duration_ms=manifest.frames * 1000 / beat_render.RATE, wall_ms=None, params=params,
                audio_path=source, abc_path=None, beat_job_id=identifier)
        # This helper writes through the app's shared SQLite connection; keep
        # its transaction on the event-loop thread with the track attachment.
        retain_original(track_id, source)
        manifest = manifest.model_copy(update={'track_id': track_id})
        await run_owned_thread(beat_render._bytes, beat_render.workspace(identifier) / 'output' / 'manifest.json', canonical_json(manifest))
    job = record.job.model_copy(update={'status': 'done', 'phase': 'done', 'finished_at': beat_store.now(),
        'error_code': None, 'manifest': manifest, 'progress_frames': manifest.frames, 'total_frames': manifest.frames})
    _save(job)
    return job


def _terminal(identifier: str, code: BeatErrorCode, *, cancelled: bool = False) -> None:
    record = _record(identifier)
    status = 'blocked' if identifier in _unverified else 'cancelled' if cancelled else 'failed'
    _save(record.job.model_copy(update={'status': status, 'phase': status,
        'error_code': 'beat_cleanup_failed' if identifier in _unverified else code, 'finished_at': beat_store.now()}))


async def _run(identifier: str) -> None:
    try:
        async with _semaphore:
            if _closing or _unverified:
                raise BeatError('beat_cleanup_failed')
            record = _record(identifier)
            estimate = await run_owned_thread(_forecast, record.document, record.job.request, record.kit)
            if not estimate.can_start:
                raise BeatError('beat_disk_full')
            _save(record.job.model_copy(update={'status': 'running', 'phase': 'rendering', 'started_at': beat_store.now()}))
            manifest = await _execute_worker(identifier)
            current = _record(identifier).job
            _save(current.model_copy(update={'status': 'saving', 'phase': 'publishing', 'manifest': manifest}))
            try:
                await _publish(identifier)
            except (OSError, sqlite3.Error, ValueError, BeatError):
                logger.exception('Retaining completed beat render for publication retry: %s', identifier)
                _save(_record(identifier).job.model_copy(update={'status': 'saving', 'phase': 'saving',
                    'error_code': 'beat_save_failed', 'finished_at': beat_store.now()}))
    except asyncio.CancelledError:
        _terminal(identifier, 'beat_cancelled', cancelled=True)
    except (BeatError, OSError, ValidationError, WorkerOutputError, TimeoutError, RuntimeError, sqlite3.Error) as exc:
        code: BeatErrorCode = exc.code if isinstance(exc, BeatError) else 'beat_disk_full' if isinstance(exc, OSError) and exc.errno == errno.ENOSPC else 'beat_worker_failed'
        logger.warning('Beat job failed: %s (%s)', identifier, code)
        if code == 'beat_save_failed' and identifier not in _unverified:
            _save(_record(identifier).job.model_copy(update={'status': 'saving', 'phase': 'saving',
                'error_code': code, 'finished_at': beat_store.now()}))
        else:
            _terminal(identifier, code)
    finally:
        _tasks.pop(identifier, None)


async def cancel(identifier: str) -> BeatJob:
    identifier = _identifier(identifier)
    async with _lock:
        job = get(identifier)
        task = _tasks.get(identifier)
        if task is None or task.done():
            return job
        publishing = job.status == 'saving'
        if not publishing:
            _save(job.model_copy(update={'status': 'cancelling', 'phase': 'cancelling'}))
            task.cancel()
    if publishing:
        # Once durable audio is being attached, drain publication instead of
        # interrupting between track insertion and its retained original copy.
        await await_cleanup(asyncio.gather(task, return_exceptions=True))
        return get(identifier)
    await cancel_and_wait(task)
    # A task cancelled before its coroutine starts cannot run its finally block.
    if identifier in _tasks and task.done():
        _tasks.pop(identifier, None)
        _terminal(identifier, 'beat_cancelled', cancelled=True)
    return get(identifier)


async def retry_save(identifier: str) -> BeatJob:
    async with _lock:
        job = get(identifier)
        if job.status == 'done':
            return job
        if job.status != 'saving' or identifier in _tasks:
            raise BeatError('beat_save_failed')
        try:
            await run_owned_thread(beat_render.recover_manifest, identifier)
            return await _publish(identifier)
        except (OSError, sqlite3.Error, ValueError, BeatError) as exc:
            raise BeatError('beat_save_failed') from exc


def artifact(identifier: str, artifact_id: str) -> tuple[Path, BeatArtifact]:
    _identifier(artifact_id)
    record = _record(identifier)
    if record.job.status not in ('done', 'saving') or record.identity is not None or identifier in _unverified:
        raise BeatError('beat_save_failed')
    manifest = record.job.manifest
    if manifest is None:
        raise BeatError('beat_save_failed')
    item = next((item for item in manifest.artifacts if item.id == artifact_id), None)
    if item is None:
        raise BeatError('beat_not_found')
    return beat_render.resolve_artifact(identifier, item), item


@dataclass(frozen=True)
class BeatRenderSource:
    """Backend-only immutable projection of an exact published retained source."""
    beat_id: str
    beat_revision: int
    render_id: str
    sha256: str
    path: Path
    track_id: int
    version_id: str
    frames: int
    sample_rate_hz: int
    document_sha256: str
    events_sha256: str
    kit_manifest_sha256: str


def render_source_metadata(beat_id: str, beat_revision: int, render_id: str, sha256: str) -> BeatRenderSource:
    """Project exact retained IDs/path; caller must asynchronously verify bytes.

    This avoids synchronous rehashing during repeated preview/admission reads.
    It is not permission to use unchecked source bytes in a worker or export.
    """
    from . import audio_version_store, audio_versions
    try:
        TypeAdapter(BeatSha256).validate_python(sha256)
        record = _record(render_id)
        job = record.job
        if (job.status != 'done' or job.request.mode != 'full' or job.beat_id != beat_id
                or isinstance(beat_revision, bool) or job.beat_revision != beat_revision or record.track_id is None):
            raise BeatError('beat_not_found')
        manifest = record.job.manifest
        if manifest is None:
            raise BeatError('beat_save_failed')
        mixes = [item for item in manifest.artifacts if item.kind == 'mix']
        if len(mixes) != 1 or mixes[0].sha256 != sha256 or manifest.track_id != record.track_id:
            raise BeatError('beat_sample_changed')
        versions = audio_version_store.list_for_track(db.get_db(), record.track_id)
        original = next((item.public for item in versions if item.public.kind == 'original' and item.public.status == 'done'), None)
        if original is None:
            raise BeatError('beat_not_found')
        source = audio_versions.resolve_source(record.track_id, original.id)
        return BeatRenderSource(beat_id=beat_id, beat_revision=beat_revision, render_id=render_id,
            sha256=sha256, path=source, track_id=record.track_id, version_id=original.id,
            frames=manifest.frames, sample_rate_hz=manifest.sample_rate_hz,
            document_sha256=manifest.document_sha256, events_sha256=manifest.events_sha256,
            kit_manifest_sha256=manifest.kit_manifest_sha256)
    except (OSError, ValueError, HTTPException) as exc:
        raise BeatError('beat_not_found') from exc


def resolve_render_source(beat_id: str, beat_revision: int, render_id: str, sha256: str) -> BeatRenderSource:
    """Hash-verified projection for callers that need immediate byte proof."""
    from .export_provenance import ProvenanceError, digest
    source = render_source_metadata(beat_id, beat_revision, render_id, sha256)
    try:
        if digest(source.path) != sha256:
            raise BeatError('beat_sample_changed')
    except (OSError, ProvenanceError) as exc:
        raise BeatError('beat_sample_changed') from exc
    return source


def work_busy() -> bool:
    return bool(_tasks or _processes or _unverified)


async def recover() -> None:
    global _closing
    async with _lock:
        _closing = True
    try:
        for job in list_jobs():
            record = _record(job.id)
            if record.identity is not None:
                expected = beat_render.workspace(job.id) / 'worker-receipt.json'
                verified = (Path(record.identity.receipt) == expected and not expected.is_symlink()
                    and await terminate_verified(record.identity))
                if not verified:
                    _unverified[job.id] = record.identity
                    _terminal(job.id, 'beat_cleanup_failed')
                    continue
                _identity(job.id, None)
                _unverified.pop(job.id, None)
            if job.status in ('running', 'queued', 'cancelling', 'saving'):
                try:
                    await run_owned_thread(beat_render.recover_manifest, job.id)
                    _save(job.model_copy(update={'status': 'saving', 'phase': 'saving', 'error_code': None}))
                    await _publish(job.id)
                except (OSError, ValueError, BeatError, sqlite3.Error):
                    if (beat_render.workspace(job.id) / 'output' / 'prepared-manifest.json').is_file():
                        _save(get(job.id).model_copy(update={'status': 'saving', 'phase': 'saving', 'error_code': 'beat_save_failed'}))
                    else:
                        _save(job.model_copy(update={'status': 'interrupted', 'phase': 'interrupted',
                            'error_code': 'beat_interrupted', 'finished_at': beat_store.now()}))
    finally:
        async with _lock:
            _closing = False


async def shutdown() -> None:
    global _closing
    async with _lock:
        _closing = True
        identifiers = list(_tasks)
    for identifier in identifiers:
        await await_cleanup(cancel(identifier))
