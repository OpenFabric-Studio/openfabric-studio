"""Durable YuE2 training jobs; reconnection never restarts an optimizer."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import errno
import logging
from pathlib import Path, PureWindowsPath
import re
import shutil
import sqlite3
import time
import uuid

from pydantic import TypeAdapter, ValidationError
from scripts.stable_lora_worker import digest

from . import db
from .contracts import JsonObject
from .gpu_lease import gpu_lease
from .job_lifecycle import await_cleanup, cancel_and_wait, kill_process_tree, run_owned_thread
from .music_lora import find_by_job as find_by_job, register_file as register_file, resolve, validate_file
from .music_lora_contracts import MusicAdapterSelection, MusicLoraError
from .module_catalog import available_bytes
from .music_training_contracts import MusicLoraPackageCompletion, MusicLoraTrainingCompletion, MusicLoraTrainingPairCompletion, MusicLoraTrainingProgress, MusicLoraTrainingJob, MusicLoraTrainingJobs, MusicLoraTrainingRequest, MusicTrainingKind, YueLoraPairSettings
from .music_training_runtime import artifact_base, fingerprint as fingerprint, prepare_package_worker as prepare_package_worker, prepare_worker as prepare_worker, status as status
from .stems import gpu_lock
from .video_process import WorkerIdentity, spawn_owned, terminate_verified
from .yue_planner_source import PlannerSource

logger = logging.getLogger(__name__)
_tasks: dict[str, asyncio.Task[None]] = {}
_unverified: set[str] = set()
_closing = False
_object = TypeAdapter(JsonObject)
_DISK_RESERVE_BYTES = 64 * 1024**2


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


def _retired_training(payload: str) -> bool:
    try:
        request = _object.validate_json(payload).get('request')
        return isinstance(request, dict) and request.get('engine') == 'heartmula'
    except ValueError:
        return False


def get(identifier: str, *, _capability: bool = True) -> MusicLoraTrainingJob:
    if re.fullmatch(r'[0-9a-f]{32}', identifier) is None:
        raise MusicLoraError('music_training_job_not_found', 404)
    row = db.get_db().execute('SELECT payload_json FROM music_training_jobs WHERE id=?', (identifier,)).fetchone()
    if row is None or _retired_training(row['payload_json']):
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
                    paired = job.request.yue_pair
                    ar_only = job.request.yue_ar_source_job_id is not None
                    limit = paired.planner_steps if paired is not None and observed.target == 'ar' else job.request.max_steps
                    phases = {None: 0, 'ar': 1, 'nar': 2}
                    if (observed.total_steps not in (None, limit)
                            or observed.step is not None and observed.step > limit
                            or paired is None and not ar_only and observed.target is not None
                            or ar_only and observed.target == 'nar'
                            or (paired is not None or ar_only) and observed.target is None
                                and (observed.stage not in {'preparing', 'transcribing'} or observed.step is not None)
                            or phases[observed.target] < phases[job.progress.target]
                            or observed.target == job.progress.target and job.progress.step is not None
                                and (observed.step is None or observed.step < job.progress.step)):
                        raise ValueError('music_training_progress_invalid')
                    if observed != job.progress:
                        job.progress = observed
                        _store(job)
            except (ValidationError, ValueError, OSError, MusicLoraError):
                logger.warning('Music adapter training progress unavailable; retained job state used')
        job.can_train_missing_ar = _planner_available(job) if _capability else False
        return job
    except (ValidationError, ValueError, OSError) as error:
        raise MusicLoraError('music_training_storage_unavailable', 503) from error


def list_jobs() -> MusicLoraTrainingJobs:
    jobs: list[MusicLoraTrainingJob] = []
    incomplete = False
    retained = 0
    for row in db.get_db().execute('SELECT id,payload_json FROM music_training_jobs ORDER BY created_at DESC,rowid DESC'):
        if _retired_training(row['payload_json']):
            continue
        retained += 1
        try:
            jobs.append(get(row['id']))
        except MusicLoraError:
            logger.warning('Music adapter training record retained but unavailable')
            incomplete = True
        if retained == 1000:
            break
    return MusicLoraTrainingJobs(jobs=jobs, history_incomplete=incomplete)


def _sources(request: MusicLoraTrainingRequest) -> list[Path]:
    if request.yue_ar_source_job_id is not None:
        return [audio.path for audio in _planner_source(request.yue_ar_source_job_id).audio]
    from .music_training_sources import source_path
    return [source_path(sample, prefix='music_training') for sample in request.samples]


def _require_disk_space(path: Path, required: int) -> None:
    free = available_bytes(path)
    if free is None or free < required:
        raise MusicLoraError('insufficient_disk')


def _require_work_space(request: MusicLoraTrainingRequest, sources: list[Path], kind: MusicTrainingKind) -> None:
    """Admission floor for the pinned worker, not a measured peak-disk guarantee."""
    pcm = len(sources) * (request.segment_seconds + 1) * 48000 * 2 * 2
    if kind == 'nvidia-package':
        required = 2 * (sum(source.stat().st_size for source in sources) + (pcm if request.yue_pair is not None else 0))
    else:
        latents = len(sources) * (request.segment_seconds * 25 + 1) * 64 * 2
        # Pinned YuE2-3B: 28 layers, merged QKV/MLP; BF16 exports and two FP32 AdamW moments.
        parameters = 28 * request.rank * (6144 + 4096 + 14336 + 8192)
        native = 28 * request.rank * (4096 + 3072 + 3072 + 4096 + 3 * 8192) * 4
        phases = [request.max_steps, *([request.yue_pair.planner_steps] if request.yue_pair is not None else [])]
        checkpoints = sum((steps + 24) // 25 for steps in phases)
        required = 2 * pcm + 2 * latents + checkpoints * (parameters * 10 + 2 * 1024**2) + 2 * native * len(phases)
        # shortcut: 2 MiB/checkpoint plus 64 MiB cover serialization/log allowances; measure vendor scratch on hardware.
    required += _DISK_RESERVE_BYTES
    for path in (db.DATA_DIR, db.DATA_DIR / 'music-lora-training', db.DATA_DIR / 'music-lora'):
        _require_disk_space(path, required)


def _planner_source(identifier: str) -> PlannerSource:
    from .yue_planner_source import verify
    try:
        return verify(get(identifier, _capability=False), _directory(identifier))
    except (ValueError, OSError, MusicLoraError) as error:
        raise MusicLoraError('music_training_planner_source_unavailable', 422) from error


def _planner_child_exists(identifier: str) -> bool:
    from . import music_lora
    if music_lora.find_by_job(identifier, target='ar') is not None:
        return True
    for row in db.get_db().execute('SELECT payload_json FROM music_training_jobs').fetchall():
        if _retired_training(row['payload_json']):
            continue
        child = MusicLoraTrainingJob.model_validate_json(row['payload_json'])
        if child.request.yue_ar_source_job_id == identifier and (child.status in {'queued', 'running', 'done'}
                or child.adapter is not None or music_lora.find_by_job(child.id, target='ar') is not None
                or _artifact(child.id, 'completion.json').is_file()):
            return True
    return False


def _planner_available(job: MusicLoraTrainingJob) -> bool:
    from .yue_planner_source import available
    if (job.status != 'done' or job.kind != 'training' or job.request.engine != 'yue2'
            or job.request.yue_pair is not None or job.request.yue_ar_source_job_id is not None
            or job.adapter is None or job.adapter.target != 'nar'):
        return False
    try:
        if _planner_child_exists(job.id):
            return False
        return available(job, _directory(job.id))
    except (ValueError, OSError, MusicLoraError):
        return False


def _require_planner_request(request: MusicLoraTrainingRequest) -> PlannerSource:
    identifier = request.yue_ar_source_job_id
    if identifier is None:
        raise MusicLoraError('music_training_planner_request_invalid', 422)
    source = _planner_source(identifier)
    parent = get(identifier, _capability=False)
    expected = parent.request.model_copy(update={'yue_ar_source_job_id': identifier,
        'max_steps': request.max_steps, 'learning_rate': request.learning_rate})
    if request != expected:
        raise MusicLoraError('music_training_planner_request_invalid', 422)
    return source


def _require_ready(request: MusicLoraTrainingRequest) -> None:
    current = status(request.engine)
    if not current.supported:
        raise MusicLoraError('music_training_runtime_unsupported')
    if not current.installed:
        raise MusicLoraError('music_training_environment_missing')
    if not current.weights_ready:
        raise MusicLoraError('music_training_weights_missing')
    if (request.yue_pair is not None or request.yue_ar_source_job_id is not None) and current.paired_ready is not True:
        raise MusicLoraError(current.error_code or 'yue_lora_score_unprepared')


def _publish_pair(job: MusicLoraTrainingJob, completion_path: Path) -> None:
    paired = job.request.yue_pair
    completion = MusicLoraTrainingPairCompletion.model_validate_json(completion_path.read_bytes())
    if (paired is None or completion.job_id != job.id or completion.request_sha256 != fingerprint(job.request)
            or completion.max_steps != job.request.max_steps or completion.planner_steps != paired.planner_steps
            or digest(_artifact(job.id, 'dataset.json'), 2 * 1024 * 1024) != completion.dataset_sha256):
        raise MusicLoraError('music_training_completion_invalid', 422)
    artifacts = sorted(completion.artifacts, key=lambda artifact: artifact.target)
    for artifact in artifacts:
        checkpoint = _artifact(job.id, artifact.checkpoint)
        if (checkpoint.suffix != '.safetensors' or digest(checkpoint) != artifact.sha256
                or artifact_base(checkpoint, job.request, target=artifact.target) != completion.base_sha256):
            raise MusicLoraError('music_training_checkpoint_changed', 409)
        validate_file(checkpoint, engine='yue2', target=artifact.target)
    job.adapters = []
    job.adapter = None
    for artifact in artifacts:
        existing = find_by_job(job.id, target=artifact.target)
        if existing is not None:
            if existing.engine != 'yue2' or existing.sha256 != artifact.sha256:
                raise MusicLoraError('music_training_completion_invalid', 422)
            resolve(MusicAdapterSelection(id=existing.id), engine='yue2', target=artifact.target)
        else:
            _require_disk_space(db.DATA_DIR / 'music-lora', _artifact(job.id, artifact.checkpoint).stat().st_size + _DISK_RESERVE_BYTES)
            existing = register_file(_artifact(job.id, artifact.checkpoint), name=job.request.name,
                engine='yue2', target=artifact.target, source_job_id=job.id)
        job.adapters.append(existing)
        if artifact.target == 'nar':
            job.adapter = existing


def _publication_complete(job: MusicLoraTrainingJob) -> bool:
    if job.kind == 'nvidia-package':
        return job.package_sha256 is not None
    if job.request.yue_pair is not None or job.request.yue_ar_source_job_id is not None:
        return len(job.adapters) == 2 and {adapter.target for adapter in job.adapters} == {'ar', 'nar'}
    return job.adapter is not None


def _publish_planner(job: MusicLoraTrainingJob) -> None:
    from .yue_planner_source import dataset_audio
    source = _require_planner_request(job.request)
    completion_path = _artifact(job.id, 'completion.json')
    if completion_path.stat().st_size > 65536:
        raise MusicLoraError('music_training_completion_invalid', 422)
    completion = MusicLoraTrainingCompletion.model_validate_json(completion_path.read_bytes())
    if (completion.job_id != job.id or completion.engine != 'yue2' or completion.request_sha256 != fingerprint(job.request)
            or completion.max_steps != job.request.max_steps
            or digest(_artifact(job.id, 'dataset.json'), 2 * 1024**2) != completion.dataset_sha256):
        raise MusicLoraError('music_training_completion_invalid', 422)
    checkpoint = _artifact(job.id, completion.checkpoint)
    if (checkpoint.suffix != '.safetensors' or digest(checkpoint) != completion.sha256
            or artifact_base(checkpoint, job.request, target='ar') != completion.base_sha256):
        raise MusicLoraError('music_training_checkpoint_changed', 409)
    dataset_audio(job, _directory(job.id), expected=source.audio)
    validate_file(checkpoint, engine='yue2', target='ar')
    existing = find_by_job(job.id, target='ar')
    if existing is None:
        _require_disk_space(db.DATA_DIR / 'music-lora', checkpoint.stat().st_size + _DISK_RESERVE_BYTES)
        existing = register_file(checkpoint, name=job.request.name, engine='yue2', target='ar', source_job_id=job.id)
    elif existing.engine != 'yue2' or existing.sha256 != completion.sha256:
        raise MusicLoraError('music_training_completion_invalid', 422)
    resolve(MusicAdapterSelection(id=existing.id), engine='yue2', target='ar')
    job.adapter = existing
    job.adapters = [existing, source.adapter]


def _publish(job: MusicLoraTrainingJob) -> None:
    _require_disk_space(db.DATA_DIR, _DISK_RESERVE_BYTES)
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
    if job.request.yue_pair is not None:
        completion_path = _artifact(job.id, 'completion.json')
        if completion_path.stat().st_size > 65536:
            raise MusicLoraError('music_training_completion_invalid', 422)
        _publish_pair(job, completion_path)
        return
    if job.request.yue_ar_source_job_id is not None:
        _publish_planner(job)
        return
    target = 'nar'
    existing = find_by_job(job.id)
    if existing is not None:
        if existing.engine != job.request.engine or existing.target != target:
            raise MusicLoraError('music_training_completion_invalid', 422)
        job.adapter = existing
        job.adapters = [existing]
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
    _require_disk_space(db.DATA_DIR / 'music-lora', checkpoint.stat().st_size + _DISK_RESERVE_BYTES)
    job.adapter = register_file(checkpoint, name=job.request.name, engine=job.request.engine, target=target, source_job_id=job.id)
    job.adapters = [job.adapter]


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
        target='nar' if job.request.yue_pair is not None else 'ar' if job.request.yue_ar_source_job_id is not None else job.progress.target,
        loss=job.progress.loss, seconds_per_step=job.progress.seconds_per_step, updated_at=job.progress.updated_at)


async def _run(identifier: str) -> None:
    job = get(identifier)
    try:
        async with gpu_lease(gpu_lock, 'gpu_busy', 'Music adapter training'):
            if job.kind == 'training':
                _require_ready(job.request)
            planner_source = _require_planner_request(job.request) if job.request.yue_ar_source_job_id is not None else None
            sources = [audio.path for audio in planner_source.audio] if planner_source is not None else _sources(job.request)
            _require_work_space(job.request, sources, job.kind)
            prepare = prepare_worker if job.kind == 'training' else prepare_package_worker
            if planner_source is not None:
                argv, cwd, env = prepare_worker(job.request, _directory(identifier), sources, prepared_audio=planner_source.audio)
            else:
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
        adapters = job.adapters
        package_sha256 = job.package_sha256
        job = get(identifier)
        job.adapter = published
        job.adapters = adapters
        job.package_sha256 = package_sha256
        job.status = 'done' if _publication_complete(job) else 'cancelled'
        job.finished_at = datetime.now(timezone.utc).isoformat()
        if job.status == 'done':
            job.progress = _done_progress(job)
        job.error_code = None
        _store(job)
        raise
    except Exception as error:
        logger.exception('Music adapter training failed')
        published, adapters = job.adapter, job.adapters
        job = get(identifier)
        job.adapter, job.adapters = published, adapters
        job.status = 'failed'
        job.finished_at = datetime.now(timezone.utc).isoformat()
        if isinstance(error, MusicLoraError):
            job.error_code = error.code
        elif isinstance(error, OSError) and error.errno == errno.ENOSPC:
            job.error_code = 'insufficient_disk'
        else:
            job.error_code = 'music_training_failed'
        _store(job)
    finally:
        try:
            if identifier not in _unverified:
                _cleanup(identifier)
        except (OSError, MusicLoraError):
            logger.warning('Music adapter training partial retained safely')
        _tasks.pop(identifier, None)


async def submit(request: MusicLoraTrainingRequest, *, kind: MusicTrainingKind = 'training') -> MusicLoraTrainingJob:
    from .music_lora_names import name_transaction, require_available
    from .resource_admission import admission_lock, native_work_inflight, require_setup_idle
    from .video_jobs import work_busy as video_busy
    from .work_busy import other_work_busy
    async with admission_lock:
        if _closing:
            raise MusicLoraError('music_training_shutting_down', 503)
        family = request.yue_ar_source_job_id
        planner_source: PlannerSource | None = None
        if family is not None:
            if kind != 'training':
                raise MusicLoraError('music_training_planner_request_invalid', 422)
            planner_source = _require_planner_request(request)
            if _planner_child_exists(family):
                raise MusicLoraError('music_training_planner_exists', 409)
        require_setup_idle()
        if work_busy() or gpu_lock.locked() or native_work_inflight() or video_busy() or await other_work_busy():
            raise MusicLoraError('music_training_busy')
        if kind == 'training':
            _require_ready(request)
        sources = [audio.path for audio in planner_source.audio] if planner_source is not None else _sources(request)
        _require_work_space(request, sources, kind)
        with name_transaction():
            if family is not None and _planner_child_exists(family):
                raise MusicLoraError('music_training_planner_exists', 409)
            require_available(request.engine, request.name, family_job_id=family)
            identifier = uuid.uuid4().hex
            _directory(identifier, create=True)
            job = MusicLoraTrainingJob(id=identifier, kind=kind, status='queued', created_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), request=request)
            connection = db.get_db()
            connection.execute('INSERT INTO music_training_jobs(id,payload_json,created_at) VALUES(?,?,?)',
                (identifier, job.model_dump_json(exclude={'adapter'}), job.created_at))
            connection.commit()
        _tasks[identifier] = asyncio.create_task(_run(identifier))
        return job


async def submit_planner(identifier: str, settings: YueLoraPairSettings) -> MusicLoraTrainingJob:
    parent = get(identifier, _capability=False)
    request = parent.request.model_copy(update={'yue_ar_source_job_id': identifier,
        'max_steps': settings.planner_steps, 'learning_rate': settings.planner_learning_rate})
    return await submit(request)


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
    rows = db.get_db().execute('SELECT id,payload_json,worker_json FROM music_training_jobs').fetchall()
    for row in rows:
        publication: MusicLoraTrainingJob | None = None
        identifier = row['id']
        retired = _retired_training(row['payload_json'])
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
            if retired:
                continue
            try:
                job = get(identifier)
                job.status = 'failed'
                job.finished_at = job.finished_at or datetime.now(timezone.utc).isoformat()
                job.error_code = 'music_training_worker_unverified'
                _store(job)
            except MusicLoraError:
                logger.warning('Unverified training worker metadata retained')
            continue
        if retired:
            continue
        try:
            job = get(identifier)
            if job.status not in {'queued', 'running', 'failed', 'cancelled', 'interrupted'}:
                continue
            complete = _artifact(identifier, 'package-completion.json' if job.kind == 'nvidia-package' else 'completion.json').is_file()
            if complete or job.adapter is not None or job.kind == 'training' and find_by_job(identifier) is not None:
                publication = job
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
                if publication is not None:
                    job.adapter, job.adapters = publication.adapter, publication.adapters
                job.status = 'failed'
                job.finished_at = job.finished_at or datetime.now(timezone.utc).isoformat()
                if isinstance(error, MusicLoraError):
                    job.error_code = error.code
                elif isinstance(error, OSError) and error.errno == errno.ENOSPC:
                    job.error_code = 'insufficient_disk'
                else:
                    job.error_code = 'music_training_completion_invalid' if isinstance(error, (ValueError, ValidationError)) else 'music_training_recovery_failed'
                _store(job)
            except (MusicLoraError, sqlite3.Error):
                logger.warning('Unavailable training metadata retained during recovery')
    _closing = False


async def shutdown() -> None:
    global _closing
    _closing = True
    if _tasks:
        await await_cleanup(asyncio.gather(*(cancel(identifier) for identifier in tuple(_tasks)), return_exceptions=True))
