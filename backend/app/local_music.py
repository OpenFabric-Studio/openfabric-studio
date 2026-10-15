"""Local model workers publish durable jobs into the existing track library."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
import wave

from pydantic import ValidationError

from . import db, music_lora
from .contracts import JsonObject
from .gpu_lease import gpu_lease
from .job_lifecycle import await_cleanup, cancel_and_wait, kill_process_tree
from .local_music_contracts import LocalMusicError, LocalMusicJob, LocalMusicJobs, LocalMusicRequest
from .music_lora_contracts import MusicLoraError
from .local_music_runtime import engine_status as engine_status, prepare_worker
from .stems import gpu_lock
from .track_view import track_response
from .video_process import WorkerIdentity, spawn_owned, terminate_verified

logger = logging.getLogger(__name__)
_tasks: dict[str, asyncio.Task[None]] = {}
_unverified: set[str] = set()
_closing = False
_MAX_AUDIO_BYTES = 600 * 1024 * 1024


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE IF NOT EXISTS local_music_jobs (
        id TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
        track_id INTEGER UNIQUE REFERENCES tracks(id) ON DELETE SET NULL,
        worker_json TEXT, output_sha256 TEXT,
        created_at TEXT NOT NULL)''')
    connection.execute('CREATE INDEX IF NOT EXISTS idx_local_music_created ON local_music_jobs(created_at DESC)')
    connection.commit()


def _store(job: LocalMusicJob) -> None:
    connection = db.get_db()
    connection.execute('UPDATE local_music_jobs SET payload_json=? WHERE id=?', (job.model_dump_json(exclude={'track'}), job.id))
    connection.commit()


def _store_identity(identifier: str, identity: WorkerIdentity | None) -> None:
    connection = db.get_db()
    connection.execute('UPDATE local_music_jobs SET worker_json=? WHERE id=?',
        (identity.model_dump_json() if identity is not None else None, identifier))
    connection.commit()


def get(identifier: str) -> LocalMusicJob:
    if re.fullmatch(r'[0-9a-f]{32}', identifier) is None:
        raise LocalMusicError('local_music_job_not_found', 404)
    row = db.get_db().execute('SELECT payload_json,track_id FROM local_music_jobs WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise LocalMusicError('local_music_job_not_found', 404)
    try:
        job = LocalMusicJob.model_validate_json(row['payload_json'])
        if job.id != identifier:
            raise ValueError('mismatched_job_identity')
        if row['track_id'] is not None:
            track = db.get_track(row['track_id'])
            if track is not None:
                job.track = track_response(track)
        return job
    except (ValidationError, ValueError) as error:
        raise LocalMusicError('local_music_storage_unavailable', 503) from error


def list_jobs() -> LocalMusicJobs:
    jobs: list[LocalMusicJob] = []
    incomplete = False
    rows = db.get_db().execute('SELECT id FROM local_music_jobs ORDER BY created_at DESC,rowid DESC LIMIT 1000').fetchall()
    for row in rows:
        try:
            jobs.append(get(row['id']))
        except LocalMusicError:
            incomplete = True
            logger.warning('Local music record retained but unavailable')
    return LocalMusicJobs(jobs=jobs, history_incomplete=incomplete)


def _checked_child(path: Path, parent: Path) -> Path:
    if path.is_symlink() or path.resolve().parent != parent.resolve():
        raise LocalMusicError('local_music_storage_unavailable', 503)
    return path


def _artifact(identifier: str, name: str, *, create: bool = False) -> Path:
    if re.fullmatch(r'[0-9a-f]{32}', identifier) is None:
        raise LocalMusicError('local_music_job_not_found', 404)
    root = _checked_child(db.DATA_DIR / 'local-music', db.DATA_DIR)
    directory = _checked_child(root / identifier, root)
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return _checked_child(directory / name, directory)


def _pending(job: LocalMusicJob) -> Path:
    return _artifact(job.id, 'output.wav')


def _output(job: LocalMusicJob) -> Path:
    files = _checked_child(db.FILES_DIR, db.DATA_DIR)
    directory = _checked_child(files / job.request.engine, files)
    directory.mkdir(parents=True, exist_ok=True)
    return _checked_child(directory / f'{job.id}.wav', directory)


def _validate_audio(path: Path) -> float:
    if not 0 < path.stat().st_size <= _MAX_AUDIO_BYTES:
        raise LocalMusicError('invalid_music_audio', 502)
    try:
        with wave.open(str(path), 'rb') as audio:
            channels, width, rate, frames = audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()
            if channels not in (1, 2) or width not in (2, 3, 4) or not 8000 <= rate <= 192000 or not 0 < frames / rate <= 390:
                raise LocalMusicError('invalid_music_audio', 502)
            remaining = frames
            while remaining:
                count = min(remaining, 65536)
                if len(audio.readframes(count)) != count * channels * width:
                    raise LocalMusicError('invalid_music_audio', 502)
                remaining -= count
            return frames / rate * 1000
    except (wave.Error, EOFError) as error:
        raise LocalMusicError('invalid_music_audio', 502) from error


def _digest(path: Path) -> str:
    if path.stat().st_size > _MAX_AUDIO_BYTES:
        raise LocalMusicError('invalid_music_audio', 502)
    checksum = hashlib.sha256()
    count = 0
    with path.open('rb') as handle:
        while chunk := handle.read(65536):
            count += len(chunk)
            if count > _MAX_AUDIO_BYTES:
                raise LocalMusicError('invalid_music_audio', 502)
            checksum.update(chunk)
    return checksum.hexdigest()


def _save_track(job: LocalMusicJob) -> None:
    if job.track is not None:
        return
    row = db.get_db().execute('SELECT output_sha256 FROM local_music_jobs WHERE id=?', (job.id,)).fetchone()
    checksum: object = row['output_sha256'] if row is not None else None
    output = _output(job)
    if not isinstance(checksum, str) or re.fullmatch(r'[0-9a-f]{64}', checksum) is None or _digest(output) != checksum:
        raise LocalMusicError('local_music_output_changed', 409)
    duration = _validate_audio(output)
    params: JsonObject = {'prompt': job.request.prompt, 'duration_seconds': job.request.duration_seconds,
        'seed': job.request.seed, 'variant': job.request.variant, 'local_music_job_id': job.id}
    if job.request.adapter is not None and job.adapter is not None:
        params['adapter'] = {'id': job.request.adapter.id, 'strength': job.request.adapter.strength}
        manifest: JsonObject = {**job.adapter.model_dump(mode='json'), 'strength': job.request.adapter.strength}
        params['_music_adapters'] = [manifest]
    db.insert_track(model=job.request.engine, title=job.request.title or job.request.engine,
        lyrics=job.request.lyrics, seed=job.request.seed, duration_ms=duration, wall_ms=None,
        params=params, audio_path=output, abc_path=None, local_music_job_id=job.id)


def _publish(job: LocalMusicJob) -> None:
    pending = _pending(job)
    _validate_audio(pending)
    checksum = _digest(pending)
    with pending.open('rb') as handle:
        os.fsync(handle.fileno())
    connection = db.get_db()
    connection.execute('UPDATE local_music_jobs SET output_sha256=? WHERE id=?', (checksum, job.id))
    connection.commit()
    pending.replace(_output(job))
    _save_track(job)


def _clean_pending(identifier: str, *, keep_output: bool = False) -> None:
    # Keep supervisor identity/receipt and validated final output for recovery.
    for name in ('output.wav', 'output.partial.wav', 'raw.wav', 'request.json'):
        if name == 'output.wav' and keep_output:
            continue
        path = _artifact(identifier, name)
        path.unlink(missing_ok=True)


def _require_ready(request: LocalMusicRequest) -> None:
    if request.engine == 'heartmula' and request.adapter is not None:
        from .heart_lora_runtime import status as heart_status
        ready = heart_status()
        if not ready.supported or not ready.installed or not ready.weights_ready:
            raise LocalMusicError('music_training_environment_missing', 409)
        music_lora.resolve(request.adapter, engine='heartmula', target='heartmula-3b')
        return
    status = next((item for item in engine_status().engines if item.id == request.engine), None)
    if status is None or not status.supported:
        raise LocalMusicError('local_music_runtime_unsupported', 409)
    if not status.installed:
        raise LocalMusicError('local_music_engine_not_installed', 409)
    if not status.weights_ready:
        raise LocalMusicError('local_music_weights_missing', 409)
    if request.adapter is not None:
        music_lora.resolve(request.adapter, engine=request.engine, target=request.variant)


async def _run(identifier: str) -> None:
    job = get(identifier)
    publishing = False
    try:
        async with gpu_lease(gpu_lock, 'gpu_busy', job.request.engine):
            _require_ready(job.request)
            if job.adapter is not None and music_lora.get(job.adapter.id) != job.adapter:
                raise MusicLoraError('music_lora_adapter_changed')
            output = _artifact(identifier, 'output.wav', create=True)
            argv, cwd, env = prepare_worker(job.request, output)
            job.status = 'running'
            _store(job)
            process: asyncio.subprocess.Process | None = None
            with _artifact(identifier, 'worker.log').open('xb') as log:
                try:
                    process = await spawn_owned(argv, receipt_path=_artifact(identifier, 'worker.json'), cwd=cwd,
                        env=env, stdout=log.fileno(),
                        on_identity=lambda identity: _store_identity(identifier, identity))
                    async with asyncio.timeout(7200):
                        returncode = await process.wait()
                    if returncode != 0:
                        raise LocalMusicError('local_music_generation_failed', 502)
                finally:
                    try:
                        await await_cleanup(kill_process_tree(process))
                    except (OSError, TimeoutError):
                        _unverified.add(identifier)
                        raise LocalMusicError('local_music_worker_unverified', 503)
                    if process is not None:
                        _store_identity(identifier, None)
            publishing = True
            _publish(job)
            job = get(identifier)
            job.status = 'done'
            job.error_code = None
            _store(job)
    except asyncio.CancelledError:
        job = get(identifier)
        job.status = 'cancelled'
        job.error_code = None
        _store(job)
        raise
    except Exception as error:
        logger.exception('Local music job failed')
        job = get(identifier)
        job.status = 'failed'
        job.error_code = error.code if isinstance(error, (LocalMusicError, MusicLoraError)) else 'local_music_save_failed' if publishing else 'local_music_generation_failed'
        _store(job)
    finally:
        if identifier not in _unverified:
            try:
                retained = db.get_db().execute('SELECT output_sha256 FROM local_music_jobs WHERE id=?', (identifier,)).fetchone()
                _clean_pending(identifier, keep_output=job.status == 'failed' and
                    (job.error_code == 'local_music_save_failed' or retained is not None and retained['output_sha256'] is not None))
            except (OSError, LocalMusicError, sqlite3.Error):
                logger.warning('Local music partial output retained safely', exc_info=True)
        _tasks.pop(identifier, None)


async def submit(request: LocalMusicRequest) -> LocalMusicJob:
    from .resource_admission import admission_lock, native_work_inflight, require_setup_idle
    from .video_jobs import work_busy as video_busy
    from .work_busy import other_work_busy
    async with admission_lock:
        if _closing:
            raise LocalMusicError('local_music_shutting_down', 503)
        require_setup_idle()
        if work_busy() or gpu_lock.locked() or native_work_inflight() or video_busy() or await other_work_busy():
            raise LocalMusicError('local_music_busy', 409)
        _require_ready(request)
        identifier = uuid.uuid4().hex
        job = LocalMusicJob(id=identifier, status='queued', created_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), request=request,
            adapter=music_lora.get(request.adapter.id) if request.adapter is not None else None)
        connection = db.get_db()
        connection.execute('INSERT INTO local_music_jobs(id,payload_json,created_at) VALUES(?,?,?)',
            (identifier, job.model_dump_json(exclude={'track'}), job.created_at))
        connection.commit()
        _tasks[identifier] = asyncio.create_task(_run(identifier))
        return job


def work_busy() -> bool:
    return bool(_unverified) or any(not task.done() for task in _tasks.values())


async def cancel(identifier: str) -> LocalMusicJob:
    task = _tasks.get(identifier)
    await cancel_and_wait(task)
    _tasks.pop(identifier, None)
    job = get(identifier)
    if task is not None and job.status in {'queued', 'running'}:
        job.status = 'cancelled'
        job.error_code = None
        _store(job)
        _clean_pending(identifier)
    return get(identifier)


async def _recover_worker(identifier: str, payload: str | None) -> bool:
    if payload is None:
        _unverified.discard(identifier)
        return True
    try:
        identity = WorkerIdentity.model_validate_json(payload)
        receipt = _artifact(identifier, 'worker.json')
        if Path(identity.receipt) != receipt or not await terminate_verified(identity):
            raise LocalMusicError('local_music_worker_unverified', 503)
        _store_identity(identifier, None)
        _unverified.discard(identifier)
        return True
    except (ValidationError, OSError, LocalMusicError, TimeoutError):
        logger.warning('Local music worker retained without verified ownership')
        _unverified.add(identifier)
        return False


async def recover() -> None:
    global _closing
    _closing = True
    rows = db.get_db().execute('SELECT id,worker_json,output_sha256 FROM local_music_jobs').fetchall()
    for row in rows:
        identifier = row['id']
        if identifier in _tasks:
            continue
        if not await _recover_worker(identifier, row['worker_json']):
            try:
                job = get(identifier)
                job.status = 'failed'
                job.error_code = 'local_music_worker_unverified'
                _store(job)
            except LocalMusicError:
                logger.warning('Unverified local music worker metadata retained')
            continue
        try:
            job = get(identifier)
        except LocalMusicError:
            logger.warning('Local music record retained but skipped during recovery')
            continue
        if job.status not in {'queued', 'running', 'failed'}:
            continue
        try:
            if job.track is not None:
                job.status = 'done'
                job.error_code = None
            elif row['output_sha256'] is not None:
                output = _output(job)
                if not output.is_file():
                    pending = _pending(job)
                    if pending.is_file() and _digest(pending) == row['output_sha256']:
                        _validate_audio(pending)
                        pending.replace(output)
                _save_track(job)
                job.status = 'done'
                job.error_code = None
            elif job.status in {'queued', 'running'} or job.error_code == 'local_music_save_failed':
                if _pending(job).is_file():
                    _publish(job)
                    job.status = 'done'
                    job.error_code = None
                else:
                    if job.status in {'queued', 'running'}:
                        job.status = 'interrupted'
                        job.error_code = 'local_music_interrupted'
        except (OSError, LocalMusicError, ValueError, sqlite3.Error) as error:
            logger.warning('Local music retained audio unavailable during recovery', exc_info=True)
            job.status = 'failed'
            job.error_code = error.code if isinstance(error, (LocalMusicError, MusicLoraError)) else 'local_music_save_failed'
        _store(job)
        try:
            retained = db.get_db().execute('SELECT output_sha256 FROM local_music_jobs WHERE id=?', (identifier,)).fetchone()
            _clean_pending(identifier, keep_output=job.status == 'failed' and
                (job.error_code == 'local_music_save_failed' or retained is not None and retained['output_sha256'] is not None))
        except (OSError, LocalMusicError):
            logger.warning('Local music partial retained without following unsafe paths')
    _closing = False


async def shutdown() -> None:
    global _closing
    _closing = True
    identifiers = tuple(_tasks)
    if identifiers:
        await await_cleanup(asyncio.gather(*(cancel(identifier) for identifier in identifiers), return_exceptions=True))
