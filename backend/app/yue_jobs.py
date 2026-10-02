"""Backend-owned YuE runs, serialized native use and durable lossless output."""
from __future__ import annotations

import asyncio
import base64
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import soundfile as sf
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from . import audio_versions, db, native_yue, video_jobs
from .config import MODELS, yue2_specs, YUE2_PROGRESS_AVAILABLE
from .contracts import JsonObject
from .job_lifecycle import await_cleanup, cancel_and_wait
from .orchestrator.manager import manager
from .orchestrator.state import ModelStatus
from .resource_admission import NativeLease, ResourceBusyError, reserve_native
from .track_view import track_response
from .native_artifacts import score_text
from .yue_contracts import YueJobResponse, YueNativeProgress, YueSubmitRequest

logger = logging.getLogger(__name__)
_json = TypeAdapter(JsonObject)
_tasks: dict[str, asyncio.Task[None]] = {}
_leases: dict[str, NativeLease] = {}
_native_requested: set[str] = set()
_serial = asyncio.Lock()
_schema_connection: sqlite3.Connection | None = None
_ACTIVE = {'queued', 'running', 'stopping'}
_COMPACT_ARENAS = {
    'yue2.model_weight_context_mb': '64', 'yue2.vae_weight_context_mb': '64',
    'yue2.ar_prefill_graph_arena_mb': '256', 'yue2.ar_decode_graph_arena_mb': '128',
    'yue2.nar_graph_arena_mb': '256', 'yue2.vae_graph_arena_mb': '128',
}


class _StoredJob(BaseModel):
    request: YueSubmitRequest
    response: YueJobResponse
    track_id: int | None = None


class _NativeModel(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    id: str = Field(max_length=128)
    loaded: bool
    session_options: dict[str, str] | None = None


class _NativeModels(BaseModel):
    data: list[_NativeModel] = Field(max_length=100)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute('CREATE TABLE IF NOT EXISTS yue_jobs (id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, track_id INTEGER UNIQUE REFERENCES tracks(id) ON DELETE SET NULL)')


def ensure_schema() -> None:
    global _schema_connection
    connection = db.get_db()
    if _schema_connection is not connection:
        migrate(connection)
        _schema_connection = connection


def _read(job_id: str) -> _StoredJob:
    ensure_schema()
    row = db.get_db().execute('SELECT payload_json, track_id FROM yue_jobs WHERE id=?', (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, detail='job_not_found')
    stored = _StoredJob.model_validate_json(row['payload_json'])
    stored.track_id = row['track_id']
    return stored


def _store(job: _StoredJob) -> None:
    db.get_db().execute('UPDATE yue_jobs SET payload_json=? WHERE id=?', (job.model_dump_json(), job.response.id))
    db.get_db().commit()


def _engine_running() -> bool:
    return manager.state.models['yue2'].status == ModelStatus.RUNNING


def progress_path() -> Path:
    # Same configured library root as the process environment. Subdirectories
    # and symlinks must never redirect native telemetry outside this library.
    root = db.FILES_DIR.resolve().parent
    path = root / 'yue2-progress.json'
    if path.resolve() != path:
        raise ValueError('Invalid progress path')
    return path


def _progress(job: YueJobResponse) -> YueNativeProgress | None:
    if job.status != 'running' or job.stage != 'generating':
        return None
    try:
        path = progress_path()
        if path.stat().st_size > 8192:
            return None
        snapshot = YueNativeProgress.model_validate_json(path.read_bytes())
        now_ms = int(time.time() * 1000)
        if snapshot.run_id != job.id or not now_ms - 60_000 <= snapshot.updated_ms <= now_ms + 5000:
            return None
        return snapshot
    except (OSError, ValueError, ValidationError):
        return None


def get_job(job_id: str) -> YueJobResponse:
    stored = _read(job_id)
    result = stored.response
    result.native_progress_available = YUE2_PROGRESS_AVAILABLE
    if stored.track_id is not None:
        row = db.get_track(stored.track_id)
        result.track = track_response(row) if row is not None else None
    if result.started_at and result.status in _ACTIVE:
        result.elapsed_seconds = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(result.started_at)).total_seconds())
    result.progress = _progress(result)
    progress = result.progress
    # This estimates remaining work in THIS countable phase only. Planning
    # and semantic caps are not completion percentages or whole-song ETAs.
    if progress and progress.phase in ('acoustic', 'decode') and progress.total and progress.current >= 2:
        elapsed = (progress.updated_ms - progress.phase_started_ms) / 1000
        if elapsed >= 2:
            result.phase_eta_seconds = max(0, elapsed / progress.current * (progress.total - progress.current))
    return result


def list_jobs() -> list[YueJobResponse]:
    ensure_schema()
    rows = db.get_db().execute('SELECT id FROM yue_jobs ORDER BY created_at DESC, rowid DESC').fetchall()
    # Always retain active jobs in the visible result; completed track metadata
    # itself follows the separately configured generation history limit.
    result: list[YueJobResponse] = []
    completed = 0
    for row in rows:
        job = get_job(row['id'])
        if job.status == 'done' and job.track is None:
            continue
        if job.status in _ACTIVE or completed < 100:
            result.append(job)
        if job.status not in _ACTIVE:
            completed += 1
    return result


def delete_job(job_id: str) -> None:
    job = _read(job_id)
    if job.response.status in _ACTIVE or job.track_id is not None or job_id in _leases or job_id in _tasks:
        raise HTTPException(409, detail='job_busy')
    db.get_db().execute('DELETE FROM yue_jobs WHERE id=?', (job_id,))
    db.get_db().commit()


def ensure_track_removable(track_id: int) -> None:
    ensure_schema()
    rows = db.get_db().execute('SELECT payload_json FROM yue_jobs WHERE track_id=?', (track_id,)).fetchall()
    if any(_StoredJob.model_validate_json(row['payload_json']).response.status in _ACTIVE for row in rows):
        raise HTTPException(409, detail='job_busy')


def work_busy() -> bool:
    if _leases or any(not task.done() for task in _tasks.values()):
        return True
    ensure_schema()
    return any(_StoredJob.model_validate_json(row['payload_json']).response.status in _ACTIVE
               for row in db.get_db().execute('SELECT payload_json FROM yue_jobs').fetchall())


def submit(request: YueSubmitRequest) -> YueJobResponse:
    ensure_schema()
    if not _engine_running():
        raise HTTPException(503, detail='model_inactive')
    if request.voice_id:
        from .voice_build import public_voice
        if not public_voice(request.voice_id).get('usable'):
            raise HTTPException(409, detail='voice_not_ready')
    if sum(job.status in _ACTIVE for job in list_jobs()) >= 100:
        raise HTTPException(429, detail='generation_queue_full')
    job_id = uuid.uuid4().hex
    response = YueJobResponse(id=job_id, status='queued', stage='queued', created_at=_now(),
        title=request.title or request.options.style[:500], lyrics=request.lyrics, seed=request.seed,
        precision=request.precision, options=request.options, voice_id=request.voice_id)
    stored = _StoredJob(request=request, response=response)
    db.get_db().execute('INSERT INTO yue_jobs(id,payload_json,created_at) VALUES(?,?,?)',
                        (job_id, stored.model_dump_json(), response.created_at))
    db.get_db().commit()
    launch(job_id)
    return response


async def _native_json(endpoint: str, payload: JsonObject | None) -> JsonObject:
    return await native_yue.send(endpoint, payload)


async def _load_model(job_id: str, precision: str) -> None:
    options = {**_COMPACT_ARENAS, 'yue2.model_gguf': f'yue2-3b-{precision}.gguf'}
    models = _NativeModels.model_validate(await _native_json('/v1/models?include_session_options=true', None))
    current = next((model for model in models.data if model.id == 'yue2' and model.loaded), None)
    if current is not None and current.session_options is not None and all(current.session_options.get(key) == value for key, value in options.items()):
        return
    _native_requested.add(job_id)
    spec = yue2_specs()['yue2']
    options_json: JsonObject = {key: value for key, value in options.items()}
    payload: JsonObject = {**spec, 'load_options': {}, 'session_options': options_json}
    await _native_json('/v1/models/load', payload)


async def _restart_owned_engine() -> None:
    await manager.restart_model('yue2')


def _save_audio(job: _StoredJob, result: JsonObject) -> int:
    payload = result.get('audio')
    if not isinstance(payload, str) or not payload or len(payload) > 256 * 1024 ** 2:
        raise ValueError('Missing generated audio')
    root = db.FILES_DIR.resolve()
    destination = db.model_dir('yue2').resolve() / f'yue_{job.response.id}.wav'
    if not destination.resolve().is_relative_to(root):
        raise ValueError('Invalid generation destination')
    partial = destination.with_suffix('.wav.partial')
    try:
        with partial.open('xb') as out:
            for offset in range(0, len(payload), 65536):
                out.write(base64.b64decode(payload[offset:offset + 65536], validate=True))
        info = sf.info(str(partial))
        if info.format not in ('WAV', 'WAVEX', 'RF64') or info.frames <= 0 or not 8000 <= info.samplerate <= 192000 or not 1 <= info.channels <= 8 or info.duration > 1200:
            raise ValueError('Invalid generated audio')
        partial.replace(destination)
        score = score_text(result)
        abc_path: Path | None = None
        if score:
            abc_path = destination.with_suffix('.abc')
            abc_path.write_text(score, encoding='utf-8')
        return _catalog_audio(job, destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        # A complete artifact is retained on catalog failure for recovery.
        raise


def _catalog_audio(job: _StoredJob, destination: Path) -> int:
    root = db.FILES_DIR.resolve()
    if destination.resolve() != destination or not destination.is_relative_to(root) or not 0 < destination.stat().st_size <= 192 * 1024**2:
        raise ValueError('Invalid completed audio')
    info = sf.info(str(destination))
    if info.format not in ('WAV', 'WAVEX', 'RF64') or info.frames <= 0 or not 8000 <= info.samplerate <= 192000 or not 1 <= info.channels <= 8 or info.duration > 1200:
        raise ValueError('Invalid completed audio')
    linked = _read(job.response.id).track_id
    if linked is not None:
        audio_versions.retain_original(linked, destination)
        return linked
    abc_path = destination.with_suffix('.abc')
    if abc_path.exists() and (abc_path.resolve() != abc_path or abc_path.stat().st_size > 400_000):
        raise ValueError('Invalid completed score')
    params = _json.validate_json(job.request.options.model_dump_json(exclude_none=True))
    params.update({'precision': job.request.precision, 'seed': job.request.seed})
    if job.request.settings is not None:
        params['_generation_settings'] = _json.validate_json(job.request.settings.model_dump_json())
    wall_ms = job.response.elapsed_seconds * 1000
    track_id = db.insert_track(model='yue2', title=job.response.title, lyrics=job.request.lyrics,
        seed=job.request.seed, duration_ms=info.duration * 1000, wall_ms=wall_ms, params=params,
        audio_path=destination, abc_path=abc_path if abc_path.is_file() else None, yue_job_id=job.response.id)
    audio_versions.retain_original(track_id, destination)
    return track_id


async def _release(job_id: str) -> None:
    lease = _leases.pop(job_id, None)
    if lease is not None:
        await lease.release()


async def _stop_owned(job: _StoredJob, terminal: str) -> bool:
    job.response.status = 'stopping'
    job.response.stage = 'stopping'
    _store(job)
    try:
        if job.response.id in _native_requested:
            await await_cleanup(_restart_owned_engine())
        _native_requested.discard(job.response.id)
        await _release(job.response.id)
        job.response.status = 'cancelled' if terminal == 'cancelled' else 'failed'
        job.response.stage = job.response.status
        _store(job)
        return True
    except Exception:
        logger.exception('YuE owned engine recovery failed')
        # Keep the lease: a still-running native process must not free resources
        # for another generation/video merely because the HTTP request ended.
        job.response.error_code = 'engine_recovery_required'
        _store(job)
        return False


async def _worker(job_id: str) -> None:
    job = _read(job_id)
    try:
        if _serial.locked():
            job.response.queue_reason = 'waiting_for_yue_job'
            _store(job)
        async with _serial:
            await native_yue.recover_quarantined()
            waited = time.monotonic()
            while True:
                try:
                    _leases[job_id] = await reserve_native(video_jobs.work_busy, model_id='yue2', exclusive=True)
                    break
                except ResourceBusyError as exc:
                    job.response.queue_reason = str(exc)
                    _store(job)
                    if time.monotonic() - waited > 10800:
                        raise TimeoutError('Resource admission timed out')
                    await asyncio.sleep(0.25)
            if not _engine_running():
                raise RuntimeError('Engine stopped')
            started = time.monotonic()
            job.response.status, job.response.stage = 'running', 'loading'
            job.response.queue_reason, job.response.started_at = '', _now()
            _store(job)
            await _load_model(job_id, job.request.precision)
            job.response.stage = 'generating'
            _store(job)
            _native_requested.add(job_id)
            options = _json.validate_json(job.request.options.model_dump_json(exclude_none=True))
            options['remiqora_run_id'] = job_id
            result = await _native_json('/v1/tasks/run', {'model': 'yue2', 'request': {
                'lyrics': job.request.lyrics, 'seed': job.request.seed, 'options': options}})
            _native_requested.discard(job_id)
            job.response.elapsed_seconds = time.monotonic() - started
            job.response.stage = 'saving'
            _store(job)
            # Publication has no await boundary: cancellation cannot discard an
            # audio file after its track transaction succeeds.
            job.track_id = _save_audio(job, result)
            _store(job)
            await await_cleanup(_finalize_completed(job))
    except asyncio.CancelledError:
        if job.response.status != 'done':
            await await_cleanup(_stop_owned(job, 'cancelled'))
        raise
    except Exception:
        logger.exception('YuE generation failed')
        job.response.error_code = 'generation_failed'
        await await_cleanup(_stop_owned(job, 'failed'))
    finally:
        if job.response.status not in ('stopping',):
            await _release(job_id)


async def _finalize_completed(job: _StoredJob) -> None:
    await _release(job.response.id)
    if job.request.voice_id and job.track_id is not None:
        from .voice_build import record_apply_failure, start_apply
        try:
            start_apply(job.request.voice_id, job.track_id)
        except Exception:
            logger.exception('Unable to start YuE voice conversion')
            record_apply_failure(job.request.voice_id, job.track_id, 'not_ready')
    job.response.status, job.response.stage = 'done', 'done'
    job.response.error_code = ''
    _store(job)


def launch(job_id: str) -> None:
    if job_id in _tasks:
        return
    task = asyncio.create_task(_worker(job_id), name=f'yue:{job_id}')
    _tasks[job_id] = task
    def finished(completed: asyncio.Task[None]) -> None:
        if _tasks.get(job_id) is completed:
            _tasks.pop(job_id, None)
        if not completed.cancelled() and completed.exception() is not None:
            logger.error('YuE worker failed', exc_info=completed.exception())
    task.add_done_callback(finished)


async def cancel(job_id: str) -> YueJobResponse:
    job = _read(job_id)
    if job.response.status not in _ACTIVE:
        return get_job(job_id)
    if job.track_id is not None:
        # Inference completed and publication succeeded. Finish the already
        # captured voice handoff rather than erase a durable completion.
        return get_job(job_id)
    task = _tasks.get(job_id)
    if task is not None:
        await cancel_and_wait(task)
        remaining = _read(job_id)
        if remaining.response.status == 'queued':
            # A task cancelled before its first bytecode never runs its own
            # finally/except. No native work was started in that case.
            await _stop_owned(remaining, 'cancelled')
    else:
        await _stop_owned(job, 'cancelled')
    return get_job(job_id)


def recover() -> None:
    ensure_schema()
    for row in db.get_db().execute('SELECT id FROM yue_jobs').fetchall():
        job = _read(row['id'])
        if job.response.status in _ACTIVE or (job.response.status == 'failed' and job.response.stage == 'failed'):
            destination = db.model_dir('yue2').resolve() / f'yue_{job.response.id}.wav'
            if destination.is_file() and (job.response.stage == 'saving' or job.track_id is not None or job.response.status == 'failed'):
                try:
                    job.track_id = _catalog_audio(job, destination)
                    job.response.status, job.response.stage, job.response.error_code = 'done', 'done', ''
                    if job.request.voice_id:
                        from .voice_build import record_apply_failure, apply_status
                        # Recovery must not duplicate an unknown partially
                        # started conversion. The preserved original is usable;
                        # conversion can be explicitly requested from its card.
                        if apply_status(job.track_id).get('status') == 'idle':
                            record_apply_failure(job.request.voice_id, job.track_id, 'interrupted')
                    _store(job)
                    continue
                except Exception:
                    logger.exception('Unable to recover completed YuE audio')
            job.response.status, job.response.stage, job.response.error_code = 'failed', 'failed', 'interrupted'
            _store(job)


async def shutdown() -> None:
    for task in list(_tasks.values()):
        await cancel_and_wait(task)
    _tasks.clear()
    # Quarantined leases are released only after a verified managed process
    # stop. Failing cleanup remains visible to the enclosing lifespan.
    if _leases:
        await manager.stop_all()
        for job_id in list(_leases):
            await _release(job_id)
            _native_requested.discard(job_id)
