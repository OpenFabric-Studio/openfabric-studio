"""Durable, supervised native Stable training; recovery never resumes training."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import math
from pathlib import Path, PureWindowsPath
import re
import sqlite3
import time
import uuid

from pydantic import ValidationError
from scripts.stable_lora_worker import checkpoint_digest, digest

from . import db
from .gpu_lease import gpu_lease
from .job_lifecycle import await_cleanup, cancel_and_wait, kill_process_tree
from .music_lora import find_by_job as find_by_job, register_file as register_file
from .music_lora_contracts import MusicLoraError
from .stable_lora_contracts import StableLoraCompletion, StableLoraProgress, StableLoraTrainingJob, StableLoraTrainingJobs, StableLoraTrainingRequest
from .stable_lora_runtime import fingerprint as fingerprint, prepare_worker as prepare_worker, status as status
from .stems import gpu_lock
from .video_process import WorkerIdentity, spawn_owned, terminate_verified

logger = logging.getLogger(__name__)
_tasks: dict[str, asyncio.Task[None]] = {}
_unverified: set[str] = set()
_closing = False
_NATIVE_STEP = re.compile(r'^Step ([0-9]{1,6}), Epoch [0-9]+:.*\]$')
_NATIVE_LOSS = re.compile(r'train/loss=([^,\]\s]+)')
_NATIVE_RATE = re.compile(r'([0-9.eE+\-]+)\s*(s/it|it/s)')
_STAGES = ('preparing', 'encoding', 'loading', 'training', 'publishing', 'done')


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE IF NOT EXISTS stable_lora_jobs (
        id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, worker_json TEXT,
        created_at TEXT NOT NULL)''')
    connection.execute('CREATE INDEX IF NOT EXISTS idx_stable_lora_created ON stable_lora_jobs(created_at DESC)')
    connection.commit()


def _store(job: StableLoraTrainingJob) -> None:
    connection = db.get_db()
    connection.execute('UPDATE stable_lora_jobs SET payload_json=? WHERE id=?', (job.model_dump_json(), job.id))
    connection.commit()


def _store_identity(identifier: str, identity: WorkerIdentity | None) -> None:
    connection = db.get_db()
    connection.execute('UPDATE stable_lora_jobs SET worker_json=? WHERE id=?', (identity.model_dump_json() if identity else None, identifier))
    connection.commit()


def _child(path: Path, parent: Path) -> Path:
    if path.is_symlink() or path.resolve().parent != parent.resolve():
        raise MusicLoraError('stable_lora_storage_unavailable', 503)
    return path


def _directory(identifier: str, *, create: bool = False) -> Path:
    if re.fullmatch(r'[0-9a-f]{32}', identifier) is None:
        raise MusicLoraError('stable_lora_job_not_found', 404)
    root = _child(db.DATA_DIR / 'stable-lora-training', db.DATA_DIR)
    directory = _child(root / identifier, root)
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.mkdir(mode=0o700)
    return directory.resolve()


def _artifact(identifier: str, name: str) -> Path:
    root = _directory(identifier)
    relative = Path(name)
    if relative.is_absolute() or PureWindowsPath(name).is_absolute() or any(part in ('..', '.') for part in relative.parts) or '\\' in name:
        raise MusicLoraError('stable_lora_storage_unavailable', 503)
    current = root
    for part in relative.parts:
        current = _child(current / part, current)
    return current


def _refresh_progress(job: StableLoraTrainingJob) -> None:
    previous = job.progress.model_copy(deep=True)
    try:
        path = _artifact(job.id, 'stage.json')
        if path.is_file() and path.stat().st_size <= 4096:
            stage = StableLoraProgress.model_validate_json(path.read_bytes()).stage
            if stage == 'training' and job.progress.step is None:
                stage = 'loading'
            if _STAGES.index(stage) > _STAGES.index(job.progress.stage):
                job.progress.stage = stage
                job.progress.updated_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except (ValidationError, OSError, MusicLoraError):
        logger.warning('Stable training stage unavailable; retained job state used')
    try:
        log = _artifact(job.id, 'worker.log')
        if log.is_file():
            reported_at = datetime.fromtimestamp(log.stat().st_mtime, timezone.utc).isoformat()
            with log.open('rb') as handle:
                handle.seek(max(0, log.stat().st_size - 65536))
                tail = handle.read(65536).decode('utf-8', errors='replace')
            latest = job.progress.step or 0
            for line in tail.splitlines():
                match = _NATIVE_STEP.fullmatch(line.strip())
                if match is not None and int(match[1]) == 0 and job.progress.stage in {'preparing', 'encoding', 'loading'}:
                    job.progress.stage = 'training'
                    job.progress.updated_at = reported_at
                loss_match, rate_match = _NATIVE_LOSS.search(line), _NATIVE_RATE.search(line)
                if match is None or loss_match is None or rate_match is None:
                    continue
                step = int(match[1])
                try:
                    loss, rate = float(loss_match[1]), float(rate_match[1])
                    seconds = rate if rate_match[2] == 's/it' else 1 / rate
                except (ValueError, ZeroDivisionError, OverflowError):
                    continue
                if not latest < step <= job.request.max_steps or not math.isfinite(loss) or not math.isfinite(seconds) or not 0 < seconds <= 86400:
                    continue
                latest = step
                job.progress.step = step
                job.progress.total_steps = job.request.max_steps
                job.progress.loss = loss
                job.progress.seconds_per_step = seconds
                # Native bars have no timestamp; retain this log time until a newer step.
                job.progress.updated_at = reported_at
            if latest and job.progress.stage in {'preparing', 'encoding', 'loading', 'training'}:
                job.progress.stage = 'publishing' if latest == job.request.max_steps else 'training'
    except (ValidationError, OSError, MusicLoraError):
        logger.warning('Stable training progress unavailable; retained job state used')
    job.stage = job.progress.stage
    if job.progress != previous:
        _store(job)


def get(identifier: str) -> StableLoraTrainingJob:
    if re.fullmatch(r'[0-9a-f]{32}', identifier) is None:
        raise MusicLoraError('stable_lora_job_not_found', 404)
    row = db.get_db().execute('SELECT payload_json FROM stable_lora_jobs WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise MusicLoraError('stable_lora_job_not_found', 404)
    try:
        job = StableLoraTrainingJob.model_validate_json(row['payload_json'])
        if job.id != identifier:
            raise ValueError('mismatched_job')
        if job.status == 'running' or 'progress' not in job.model_fields_set:
            _refresh_progress(job)
        return job
    except (ValidationError, ValueError, OSError) as error:
        raise MusicLoraError('stable_lora_storage_unavailable', 503) from error


def list_jobs() -> StableLoraTrainingJobs:
    jobs: list[StableLoraTrainingJob] = []
    incomplete = False
    for row in db.get_db().execute('SELECT id FROM stable_lora_jobs ORDER BY created_at DESC,rowid DESC LIMIT 1000').fetchall():
        try:
            jobs.append(get(row['id']))
        except MusicLoraError:
            logger.warning('Stable training record retained but unavailable')
            incomplete = True
    return StableLoraTrainingJobs(jobs=jobs, history_incomplete=incomplete)


def _sources(request: StableLoraTrainingRequest) -> list[Path]:
    from .music_training_sources import source_path
    return [source_path(sample, prefix='stable_lora') for sample in request.samples]


def _require_ready(request: StableLoraTrainingRequest) -> None:
    current = status()
    if not current.supported:
        raise MusicLoraError('stable_lora_runtime_unsupported')
    if not current.installed:
        raise MusicLoraError('stable_lora_engine_not_installed')
    if not (current.medium_weights_ready if request.variant == 'medium' else current.small_weights_ready):
        raise MusicLoraError('stable_lora_training_weights_missing')


def _publish(job: StableLoraTrainingJob) -> None:
    existing = find_by_job(job.id)
    if existing is not None:
        job.adapter = existing
        return
    completion_path = _artifact(job.id, 'completion.json')
    if completion_path.stat().st_size > 65536:
        raise MusicLoraError('stable_lora_completion_invalid', 422)
    completion = StableLoraCompletion.model_validate_json(completion_path.read_bytes())
    if completion.job_id != job.id or completion.request_sha256 != fingerprint(job.request) or completion.variant != job.request.variant or completion.max_steps != job.request.max_steps:
        raise MusicLoraError('stable_lora_completion_invalid', 422)
    checkpoint = _artifact(job.id, completion.checkpoint)
    if checkpoint.suffix != '.safetensors' or checkpoint_digest(checkpoint, max_steps=job.request.max_steps,
            variant=job.request.variant, rank=job.request.rank) != completion.sha256 or digest(_artifact(job.id, 'dataset.json'), 2 * 1024 * 1024) != completion.dataset_sha256:
        raise MusicLoraError('stable_lora_checkpoint_changed', 409)
    job.adapter = register_file(checkpoint, name=job.request.name, engine='stable_audio3', target=job.request.variant, source_job_id=job.id)


def _cleanup(identifier: str) -> None:
    directory = _artifact(identifier, 'audio')
    if directory.is_dir():
        for path in directory.glob('*.partial.wav'):
            _child(path, directory).unlink(missing_ok=True)


async def _run(identifier: str) -> None:
    job = get(identifier)
    try:
        async with gpu_lease(gpu_lock, 'gpu_busy', 'Stable Audio 3 adapter training'):
            _require_ready(job.request)
            sources = _sources(job.request)
            argv, cwd, env = prepare_worker(job.request, _directory(identifier), sources)
            job.status = 'running'
            job.started_at = datetime.now(timezone.utc).isoformat()
            _store(job)
            process: asyncio.subprocess.Process | None = None
            with _artifact(identifier, 'worker.log').open('xb') as log:
                try:
                    process = await spawn_owned(argv, receipt_path=_artifact(identifier, 'worker.json'), cwd=cwd, env=env,
                        stdout=log.fileno(), on_identity=lambda identity: _store_identity(identifier, identity))
                    if await process.wait() != 0:
                        raise MusicLoraError('stable_lora_training_failed', 502)
                finally:
                    try:
                        await await_cleanup(kill_process_tree(process))
                    except (OSError, TimeoutError):
                        _unverified.add(identifier)
                        raise MusicLoraError('stable_lora_worker_unverified', 503)
                    if process is not None:
                        _store_identity(identifier, None)
            job = get(identifier)
            job.stage = job.progress.stage = 'publishing'
            _store(job)
            _publish(job)
            job.status = 'done'
            job.stage = job.progress.stage = 'done'
            job.progress.step = job.progress.total_steps = job.request.max_steps
            job.finished_at = datetime.now(timezone.utc).isoformat()
            job.error_code = None
            _store(job)
    except asyncio.CancelledError:
        job = get(identifier)
        job.status = 'cancelled'
        job.finished_at = datetime.now(timezone.utc).isoformat()
        job.error_code = None
        _store(job)
        raise
    except Exception as error:
        logger.exception('Stable adapter training failed')
        job = get(identifier)
        job.status = 'failed'
        job.finished_at = datetime.now(timezone.utc).isoformat()
        job.error_code = error.code if isinstance(error, MusicLoraError) else 'stable_lora_training_failed'
        _store(job)
    finally:
        try:
            if identifier not in _unverified:
                _cleanup(identifier)
        except (OSError, MusicLoraError):
            logger.warning('Stable training partial retained safely')
        _tasks.pop(identifier, None)


async def submit(request: StableLoraTrainingRequest) -> StableLoraTrainingJob:
    from .music_lora_names import name_transaction, require_available
    from .resource_admission import admission_lock, native_work_inflight, require_setup_idle
    from .video_jobs import work_busy as video_busy
    from .work_busy import other_work_busy
    async with admission_lock:
        if _closing:
            raise MusicLoraError('stable_lora_shutting_down', 503)
        require_setup_idle()
        if work_busy() or gpu_lock.locked() or native_work_inflight() or video_busy() or await other_work_busy():
            raise MusicLoraError('stable_lora_busy')
        _require_ready(request)
        _sources(request)
        with name_transaction():
            require_available('stable_audio3', request.name)
            identifier = uuid.uuid4().hex
            _directory(identifier, create=True)
            job = StableLoraTrainingJob(id=identifier, status='queued', created_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), request=request)
            connection = db.get_db()
            connection.execute('INSERT INTO stable_lora_jobs(id,payload_json,created_at) VALUES(?,?,?)',
                (identifier, job.model_dump_json(exclude={'adapter'}), job.created_at))
            connection.commit()
        _tasks[identifier] = asyncio.create_task(_run(identifier))
        return job


def work_busy() -> bool:
    return bool(_unverified) or any(not task.done() for task in _tasks.values())


async def cancel(identifier: str) -> StableLoraTrainingJob:
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
    rows = db.get_db().execute('SELECT id,worker_json FROM stable_lora_jobs').fetchall()
    for row in rows:
        identifier = row['id']
        if identifier in _tasks:
            continue
        try:
            if row['worker_json'] is not None:
                identity = WorkerIdentity.model_validate_json(row['worker_json'])
                if Path(identity.receipt) != _artifact(identifier, 'worker.json') or not await terminate_verified(identity):
                    raise MusicLoraError('stable_lora_worker_unverified', 503)
                _store_identity(identifier, None)
            _unverified.discard(identifier)
        except (ValidationError, OSError, MusicLoraError, TimeoutError):
            _unverified.add(identifier)
            logger.warning('Stable training worker retained without verified ownership')
            try:
                job = get(identifier)
                job.status = 'failed'
                job.finished_at = job.finished_at or datetime.now(timezone.utc).isoformat()
                job.error_code = 'stable_lora_worker_unverified'
                _store(job)
            except MusicLoraError:
                logger.warning('Unverified training worker metadata retained')
            continue
        try:
            job = get(identifier)
            if job.status not in {'queued', 'running', 'failed'}:
                continue
            if job.adapter is not None or find_by_job(identifier) is not None or _artifact(identifier, 'completion.json').is_file():
                _publish(job)
                job.status = 'done'
                job.stage = job.progress.stage = 'done'
                job.progress.step = job.progress.total_steps = job.request.max_steps
                job.finished_at = job.finished_at or datetime.now(timezone.utc).isoformat()
                job.error_code = None
            elif job.status in {'queued', 'running'}:
                job.status = 'interrupted'
                job.finished_at = job.finished_at or datetime.now(timezone.utc).isoformat()
                job.error_code = 'stable_lora_interrupted'
            _store(job)
            _cleanup(identifier)
        except (ValidationError, OSError, MusicLoraError, ValueError, sqlite3.Error) as error:
            logger.warning('Stable training artifacts retained during recovery', exc_info=True)
            try:
                job = get(identifier)
                job.status = 'failed'
                job.finished_at = job.finished_at or datetime.now(timezone.utc).isoformat()
                job.error_code = error.code if isinstance(error, MusicLoraError) else 'stable_lora_completion_invalid' if isinstance(error, (ValueError, ValidationError)) else 'stable_lora_recovery_failed'
                _store(job)
            except (MusicLoraError, sqlite3.Error):
                logger.warning('Unavailable training metadata retained during recovery')
    _closing = False


async def shutdown() -> None:
    global _closing
    _closing = True
    if _tasks:
        await await_cleanup(asyncio.gather(*(cancel(identifier) for identifier in tuple(_tasks)), return_exceptions=True))
