"""Durable HeartMuLa/YuE2 training jobs; reconnection never restarts an optimizer."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path, PureWindowsPath
import re
import shutil
import sqlite3
import time
import uuid

from pydantic import ValidationError
from scripts.stable_lora_worker import digest

from . import db
from .gpu_lease import gpu_lease
from .job_lifecycle import await_cleanup, cancel_and_wait, kill_process_tree, run_owned_thread
from .music_lora import find_by_job as find_by_job, register_file as register_file
from .music_lora_contracts import MusicLoraError
from .music_training_contracts import MusicLoraPackageCompletion, MusicLoraTrainingCompletion, MusicLoraTrainingProgress, MusicLoraTrainingJob, MusicLoraTrainingJobs, MusicLoraTrainingRequest, MusicTrainingKind
from .music_training_runtime import artifact_base, fingerprint as fingerprint, prepare_package_worker as prepare_package_worker, prepare_worker as prepare_worker, status as status
from .stems import gpu_lock
from .video_process import WorkerIdentity, spawn_owned, terminate_verified

logger = logging.getLogger(__name__)
_tasks: dict[str, asyncio.Task[None]] = {}
_unverified: set[str] = set()
_closing = False


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE IF NOT EXISTS music_training_jobs (
        id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, worker_json TEXT,
        created_at TEXT NOT NULL)''')
    connection.execute('CREATE INDEX IF NOT EXISTS idx_music_training_created ON music_training_jobs(created_at DESC)')
    connection.commit()


def _store(job: MusicLoraTrainingJob) -> None:
    connection = db.get_db()
    connection.execute('UPDATE music_training_jobs SET payload_json=? WHERE id=?', (job.model_dump_json(), job.id))
    connection.commit()


def _store_identity(identifier: str, identity: WorkerIdentity | None) -> None:
    connection = db.get_db()
    connection.execute('UPDATE music_training_jobs SET worker_json=? WHERE id=?', (identity.model_dump_json() if identity else None, identifier))
    connection.commit()


def _child(path: Path, parent: Path) -> Path:
    if path.is_symlink() or path.resolve().parent != parent.resolve():
        raise MusicLoraError('music_training_storage_unavailable', 503)
    return path


def _directory(identifier: str, *, create: bool = False) -> Path:
    if re.fullmatch(r'[0-9a-f]{32}', identifier) is None:
        raise MusicLoraError('music_training_job_not_found', 404)
    root = _child(db.DATA_DIR / 'music-lora-training', db.DATA_DIR)
    directory = _child(root / identifier, root)
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.mkdir(mode=0o700)
    return directory.resolve()


def _artifact(identifier: str, name: str) -> Path:
    root = _directory(identifier)
    relative = Path(name)
    if relative.is_absolute() or PureWindowsPath(name).is_absolute() or any(part in ('..', '.') for part in relative.parts) or '\\' in name:
        raise MusicLoraError('music_training_storage_unavailable', 503)
    current = root
    for part in relative.parts:
        current = _child(current / part, current)
    return current


def get(identifier: str) -> MusicLoraTrainingJob:
    if re.fullmatch(r'[0-9a-f]{32}', identifier) is None:
        raise MusicLoraError('music_training_job_not_found', 404)
    row = db.get_db().execute('SELECT payload_json FROM music_training_jobs WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise MusicLoraError('music_training_job_not_found', 404)
    try:
        job = MusicLoraTrainingJob.model_validate_json(row['payload_json'])
        if job.id != identifier:
            raise ValueError('mismatched_job')
        if job.status == 'running':
            try:
                progress = _artifact(identifier, 'stage.json')
                if progress.is_file() and progress.stat().st_size <= 4096:
                    observed = MusicLoraTrainingProgress.model_validate_json(progress.read_bytes())
                    if (observed.total_steps not in (None, job.request.max_steps)
                            or observed.step is not None and observed.step > job.request.max_steps
                            or job.progress.step is not None and (observed.step is None or observed.step < job.progress.step)):
                        raise ValueError('music_training_progress_invalid')
                    if observed != job.progress:
                        job.progress = observed
                        _store(job)
            except (ValidationError, ValueError, OSError, MusicLoraError):
                logger.warning('Music adapter training progress unavailable; retained job state used')
        return job
    except (ValidationError, ValueError, OSError) as error:
        raise MusicLoraError('music_training_storage_unavailable', 503) from error


def list_jobs() -> MusicLoraTrainingJobs:
    jobs: list[MusicLoraTrainingJob] = []
    incomplete = False
    for row in db.get_db().execute('SELECT id FROM music_training_jobs ORDER BY created_at DESC,rowid DESC LIMIT 1000').fetchall():
        try:
            jobs.append(get(row['id']))
        except MusicLoraError:
            logger.warning('Music adapter training record retained but unavailable')
            incomplete = True
    return MusicLoraTrainingJobs(jobs=jobs, history_incomplete=incomplete)


def _sources(request: MusicLoraTrainingRequest) -> list[Path]:
    from .music_training_sources import source_path
    return [source_path(sample, prefix='music_training') for sample in request.samples]


def _require_ready(request: MusicLoraTrainingRequest) -> None:
    current = status(request.engine)
    if not current.supported:
        raise MusicLoraError('music_training_runtime_unsupported')
    if not current.installed:
        raise MusicLoraError('music_training_environment_missing')
    if not current.weights_ready:
        raise MusicLoraError('music_training_weights_missing')


def _publish(job: MusicLoraTrainingJob) -> None:
    if job.kind == 'nvidia-package':
        completion_path = _artifact(job.id, 'package-completion.json')
        if completion_path.stat().st_size > 4096:
            raise MusicLoraError('music_training_completion_invalid', 422)
        package = MusicLoraPackageCompletion.model_validate_json(completion_path.read_bytes())
        if package.job_id != job.id or package.engine != job.request.engine or package.request_sha256 != fingerprint(job.request):
            raise MusicLoraError('music_training_completion_invalid', 422)
        archive = _artifact(job.id, package.archive)
        if archive.stat().st_size != package.bytes or digest(archive, 64 * 1024**3) != package.sha256:
            raise MusicLoraError('music_training_checkpoint_changed', 409)
        job.package_sha256 = package.sha256
        return
    target = 'nar' if job.request.engine == 'yue2' else 'heartmula-3b'
    existing = find_by_job(job.id)
    if existing is not None:
        if existing.engine != job.request.engine or existing.target != target:
            raise MusicLoraError('music_training_completion_invalid', 422)
        job.adapter = existing
        return
    completion_path = _artifact(job.id, 'completion.json')
    if completion_path.stat().st_size > 65536:
        raise MusicLoraError('music_training_completion_invalid', 422)
    completion = MusicLoraTrainingCompletion.model_validate_json(completion_path.read_bytes())
    if completion.job_id != job.id or completion.request_sha256 != fingerprint(job.request) or completion.engine != job.request.engine or completion.max_steps != job.request.max_steps:
        raise MusicLoraError('music_training_completion_invalid', 422)
    checkpoint = _artifact(job.id, completion.checkpoint)
    if checkpoint.suffix != '.safetensors' or digest(checkpoint) != completion.sha256 or digest(_artifact(job.id, 'dataset.json'), 2 * 1024 * 1024) != completion.dataset_sha256 or artifact_base(checkpoint, job.request) != completion.base_sha256:
        raise MusicLoraError('music_training_checkpoint_changed', 409)
    job.adapter = register_file(checkpoint, name=job.request.name, engine=job.request.engine, target=target, source_job_id=job.id)


def package_path(identifier: str) -> Path:
    job = get(identifier)
    if job.kind != 'nvidia-package' or job.status != 'done' or job.package_sha256 is None:
        raise MusicLoraError('music_training_package_unavailable', 404)
    archive = _artifact(identifier, 'package.zip')
    if digest(archive, 64 * 1024**3) != job.package_sha256:
        raise MusicLoraError('music_training_checkpoint_changed', 409)
    return archive


def _cleanup(identifier: str) -> None:
    root = _directory(identifier)
    for path in root.glob('.music-training-package-*'):
        private = _child(path, root)
        if private.is_dir():
            shutil.rmtree(private)
    for path in root.glob('.package.zip.*.part'):
        _child(path, root).unlink(missing_ok=True)
    directory = _artifact(identifier, 'audio')
    if directory.is_dir():
        for path in directory.glob('*.partial.wav'):
            _child(path, directory).unlink(missing_ok=True)


def _done_progress(job: MusicLoraTrainingJob) -> MusicLoraTrainingProgress:
    if job.kind == 'nvidia-package':
        return MusicLoraTrainingProgress(stage='done')
    return MusicLoraTrainingProgress(stage='done', step=job.request.max_steps, total_steps=job.request.max_steps,
        loss=job.progress.loss, seconds_per_step=job.progress.seconds_per_step, updated_at=job.progress.updated_at)


async def _run(identifier: str) -> None:
    job = get(identifier)
    try:
        async with gpu_lease(gpu_lock, 'gpu_busy', 'Music adapter training'):
            if job.kind == 'training':
                _require_ready(job.request)
            sources = _sources(job.request)
            prepare = prepare_worker if job.kind == 'training' else prepare_package_worker
            argv, cwd, env = prepare(job.request, _directory(identifier), sources)
            job.status = 'running'
            job.started_at = datetime.now(timezone.utc).isoformat()
            _store(job)
            process: asyncio.subprocess.Process | None = None
            with _artifact(identifier, 'worker.log').open('xb') as log:
                try:
                    process = await spawn_owned(argv, receipt_path=_artifact(identifier, 'worker.json'), cwd=cwd, env=env,
                        stdout=log.fileno(), on_identity=lambda identity: _store_identity(identifier, identity))
                    if await process.wait() != 0:
                        raise MusicLoraError('music_training_failed', 502)
                finally:
                    try:
                        await await_cleanup(kill_process_tree(process))
                    except (OSError, TimeoutError):
                        _unverified.add(identifier)
                        raise MusicLoraError('music_training_worker_unverified', 503)
                    if process is not None:
                        _store_identity(identifier, None)
            job.progress = get(identifier).progress
            job.progress.stage = 'publishing'
            _store(job)
            await run_owned_thread(_publish, job)
            job.status = 'done'
            job.finished_at = datetime.now(timezone.utc).isoformat()
            job.progress = _done_progress(job)
            job.error_code = None
            _store(job)
    except asyncio.CancelledError:
        published = job.adapter
        package_sha256 = job.package_sha256
        job = get(identifier)
        job.adapter = published
        job.package_sha256 = package_sha256
        job.status = 'done' if published is not None or package_sha256 is not None else 'cancelled'
        job.finished_at = datetime.now(timezone.utc).isoformat()
        if job.status == 'done':
            job.progress = _done_progress(job)
        job.error_code = None
        _store(job)
        raise
    except Exception as error:
        logger.exception('Music adapter training failed')
        job = get(identifier)
        job.status = 'failed'
        job.finished_at = datetime.now(timezone.utc).isoformat()
        job.error_code = error.code if isinstance(error, MusicLoraError) else 'music_training_failed'
        _store(job)
    finally:
        try:
            if identifier not in _unverified:
                _cleanup(identifier)
        except (OSError, MusicLoraError):
            logger.warning('Music adapter training partial retained safely')
        _tasks.pop(identifier, None)


async def submit(request: MusicLoraTrainingRequest, *, kind: MusicTrainingKind = 'training') -> MusicLoraTrainingJob:
    from .resource_admission import admission_lock, native_work_inflight, require_setup_idle
    from .video_jobs import work_busy as video_busy
    from .work_busy import other_work_busy
    async with admission_lock:
        if _closing:
            raise MusicLoraError('music_training_shutting_down', 503)
        require_setup_idle()
        if work_busy() or gpu_lock.locked() or native_work_inflight() or video_busy() or await other_work_busy():
            raise MusicLoraError('music_training_busy')
        if kind == 'training':
            _require_ready(request)
        _sources(request)
        identifier = uuid.uuid4().hex
        _directory(identifier, create=True)
        job = MusicLoraTrainingJob(id=identifier, kind=kind, status='queued', created_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), request=request)
        connection = db.get_db()
        connection.execute('INSERT INTO music_training_jobs(id,payload_json,created_at) VALUES(?,?,?)',
            (identifier, job.model_dump_json(exclude={'adapter'}), job.created_at))
        connection.commit()
        _tasks[identifier] = asyncio.create_task(_run(identifier))
        return job


def work_busy() -> bool:
    return bool(_unverified) or any(not task.done() for task in _tasks.values())


async def cancel(identifier: str) -> MusicLoraTrainingJob:
    task = _tasks.get(identifier)
    await cancel_and_wait(task)
    _tasks.pop(identifier, None)
    job = get(identifier)
    if task is not None and job.status in {'queued', 'running'}:
        job.status = 'cancelled'
        job.finished_at = datetime.now(timezone.utc).isoformat()
        _store(job)
        _cleanup(identifier)
    return get(identifier)


async def recover() -> None:
    global _closing
    _closing = True
    rows = db.get_db().execute('SELECT id,worker_json FROM music_training_jobs').fetchall()
    for row in rows:
        identifier = row['id']
        if identifier in _tasks:
            continue
        try:
            if row['worker_json'] is not None:
                identity = WorkerIdentity.model_validate_json(row['worker_json'])
                if Path(identity.receipt) != _artifact(identifier, 'worker.json') or not await terminate_verified(identity):
                    raise MusicLoraError('music_training_worker_unverified', 503)
                _store_identity(identifier, None)
            _unverified.discard(identifier)
        except (ValidationError, OSError, MusicLoraError, TimeoutError):
            _unverified.add(identifier)
            logger.warning('Music adapter training worker retained without verified ownership')
            try:
                job = get(identifier)
                job.status = 'failed'
                job.finished_at = job.finished_at or datetime.now(timezone.utc).isoformat()
                job.error_code = 'music_training_worker_unverified'
                _store(job)
            except MusicLoraError:
                logger.warning('Unverified training worker metadata retained')
            continue
        try:
            job = get(identifier)
            if job.status not in {'queued', 'running', 'failed', 'cancelled', 'interrupted'}:
                continue
            complete = _artifact(identifier, 'package-completion.json' if job.kind == 'nvidia-package' else 'completion.json').is_file()
            if complete or job.adapter is not None or job.kind == 'training' and find_by_job(identifier) is not None:
                await run_owned_thread(_publish, job)
                job.status = 'done'
                job.finished_at = job.finished_at or datetime.now(timezone.utc).isoformat()
                job.progress = _done_progress(job)
                job.error_code = None
            elif job.status in {'queued', 'running'}:
                job.status = 'interrupted'
                job.finished_at = job.finished_at or datetime.now(timezone.utc).isoformat()
                job.error_code = 'music_training_interrupted'
            _store(job)
            _cleanup(identifier)
        except (ValidationError, OSError, MusicLoraError, ValueError, sqlite3.Error) as error:
            logger.warning('Music adapter training artifacts retained during recovery', exc_info=True)
            try:
                job = get(identifier)
                job.status = 'failed'
                job.finished_at = job.finished_at or datetime.now(timezone.utc).isoformat()
                job.error_code = error.code if isinstance(error, MusicLoraError) else 'music_training_completion_invalid' if isinstance(error, (ValueError, ValidationError)) else 'music_training_recovery_failed'
                _store(job)
            except (MusicLoraError, sqlite3.Error):
                logger.warning('Unavailable training metadata retained during recovery')
    _closing = False


async def shutdown() -> None:
    global _closing
    _closing = True
    if _tasks:
        await await_cleanup(asyncio.gather(*(cancel(identifier) for identifier in tuple(_tasks)), return_exceptions=True))
