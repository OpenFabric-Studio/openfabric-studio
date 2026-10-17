"""Local model workers publish durable jobs into the existing track library."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import errno
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
import wave

import numpy as np
import soundfile as sf

from pydantic import TypeAdapter, ValidationError

from . import db, music_lora
from .contracts import JsonObject
from .export_provenance_contracts import MusicEvidence
from .music_evidence import capture_music_evidence, serialize_music_evidence
from .gpu_lease import gpu_lease
from .job_lifecycle import await_cleanup, cancel_and_wait, kill_process_tree
from .local_music_contracts import LocalMusicError, LocalMusicJob, LocalMusicJobs, LocalMusicRequest
from .music_lora_contracts import MusicLoraError
from .module_catalog import available_bytes
from .local_music_runtime import engine_status as engine_status, prepare_worker
from .stems import gpu_lock
from .track_view import track_response
from .video_process import WorkerIdentity, spawn_owned, terminate_verified
from . import music_conditioning
from .music_conditioning import RetainedConditioning
from .music_evidence import merge_music_evidence
from .music_dsp import splice_inpaint
from .video_process import read_owned_output
from .queued_music_source_contracts import QueuedMusicSource

logger = logging.getLogger(__name__)
_tasks: dict[str, asyncio.Task[None]] = {}
_unverified: set[str] = set()
_closing = False
_MAX_AUDIO_BYTES = 600 * 1024 * 1024
_DISK_RESERVE_BYTES = 64 * 1024**2
_object = TypeAdapter(JsonObject)


class _StoredJob(LocalMusicJob):
    music_evidence: MusicEvidence | None = None
    conditioning_snapshot: RetainedConditioning | None = None
    conditioning_final_sha256: str | None = None
    retained_input: QueuedMusicSource | None = None


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
        payload: object = json.loads(row['payload_json'])
        request = payload.get('request') if isinstance(payload, dict) else None
        if isinstance(request, dict) and request.get('engine') == 'heartmula':
            raise LocalMusicError('local_music_engine_removed', 410)
        job = _StoredJob.model_validate_json(row['payload_json'])
        if job.id != identifier:
            raise ValueError('mismatched_job_identity')
        if row['track_id'] is not None:
            track = db.get_track(row['track_id'])
            if track is not None:
                job.track = track_response(track)
        return job
    except (ValidationError, ValueError) as error:
        raise LocalMusicError('local_music_storage_unavailable', 503) from error


@dataclass(frozen=True)
class CompletedLocalMusicAudio:
    path: Path
    sha256: str
    track_id: int


def completed_audio(identifier: str) -> CompletedLocalMusicAudio:
    """Project the owned original; consumers must copy/hash its expected digest."""
    job = get(identifier)
    row = db.get_db().execute('SELECT track_id,output_sha256,worker_json FROM local_music_jobs WHERE id=?', (identifier,)).fetchone()
    if (job.status != 'done' or job.track is None or row is None or row['track_id'] != job.track.id
            or identifier in _tasks or identifier in _unverified or row['worker_json'] is not None
            or job.track.model != job.request.engine or job.track.params.get('local_music_job_id') != identifier):
        raise LocalMusicError('local_music_output_changed')
    checksum: object = row['output_sha256']
    if not isinstance(checksum, str) or re.fullmatch(r'[0-9a-f]{64}', checksum) is None:
        raise LocalMusicError('local_music_output_changed')
    files = _checked_child(db.FILES_DIR, db.DATA_DIR)
    folder = _checked_child(files / job.request.engine, files)
    path = _checked_child(folder / (job.id + '.wav'), folder)
    if not path.is_file() or not 0 < path.stat().st_size <= _MAX_AUDIO_BYTES:
        raise LocalMusicError('local_music_output_changed')
    return CompletedLocalMusicAudio(path=path.resolve(), sha256=checksum, track_id=job.track.id)


def list_jobs() -> LocalMusicJobs:
    jobs: list[LocalMusicJob] = []
    incomplete = False
    rows = db.get_db().execute('SELECT id FROM local_music_jobs ORDER BY created_at DESC,rowid DESC')
    for row in rows:
        try:
            jobs.append(get(row['id']))
            if len(jobs) >= 1000:
                break
        except LocalMusicError as error:
            if error.status == 410:
                continue
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


def _require_disk_space(path: Path, required: int) -> None:
    free = available_bytes(path)
    if free is None or free < required:
        raise LocalMusicError('insufficient_disk')


def _require_generation_space(request: LocalMusicRequest) -> None:
    # Two simultaneous PCM files at accepted format maxima, ten seconds of padding and a metadata reserve.
    # shortcut: vendor scratch is unmeasured; refine the admission floor after target-hardware peak-disk trials.
    required = (request.duration_seconds + 10) * 192000 * 2 * 4 * 2 + _DISK_RESERVE_BYTES
    for path in (db.DATA_DIR / 'local-music', db.FILES_DIR):
        _require_disk_space(path, required)


def _publish_audio(pending: Path, output: Path) -> None:
    _require_disk_space(output.parent, _DISK_RESERVE_BYTES)
    try:
        os.link(pending, output, follow_symlinks=False)
    except FileExistsError as error:
        raise LocalMusicError('local_music_output_exists') from error
    except OSError as error:
        if error.errno == errno.ENOSPC:
            raise LocalMusicError('insufficient_disk') from error
        raise


def _validate_audio(path: Path) -> float:
    size = path.stat().st_size
    if not 0 < size <= _MAX_AUDIO_BYTES:
        raise LocalMusicError('invalid_music_audio', 502)
    try:
        # libsndfile clamps info.frames to physically present data; validate declared RIFF/chunk lengths too.
        with path.open('rb') as handle:
            header = handle.read(12)
            if header[:4] != b'RIFF' or header[8:] != b'WAVE' or int.from_bytes(header[4:8], 'little') + 8 != size:
                raise LocalMusicError('invalid_music_audio', 502)
            position = 12
            chunks = 0
            while position < size:
                chunks += 1
                if chunks > 1024:
                    raise LocalMusicError('invalid_music_audio', 502)
                handle.seek(position)
                chunk = handle.read(8)
                if len(chunk) != 8:
                    raise LocalMusicError('invalid_music_audio', 502)
                position += 8 + int.from_bytes(chunk[4:], 'little')
                position += position % 2
                if position > size:
                    raise LocalMusicError('invalid_music_audio', 502)
        info = sf.info(path)
        if (info.format not in ('WAV', 'WAVEX') or info.channels not in (1, 2)
                or info.subtype not in ('PCM_16', 'PCM_24', 'PCM_32', 'FLOAT')
                or not 8000 <= info.samplerate <= 192000 or not 0 < info.frames / info.samplerate <= 390):
            raise LocalMusicError('invalid_music_audio', 502)
        count = 0
        with sf.SoundFile(path) as audio:
            while len(block := audio.read(65536, dtype='float32', always_2d=True)):
                count += len(block)
                if count > info.frames or not np.all(np.isfinite(block)):
                    raise LocalMusicError('invalid_music_audio', 502)
        if count != info.frames:
            raise LocalMusicError('invalid_music_audio', 502)
        return info.frames / info.samplerate * 1000
    except (RuntimeError, ValueError, EOFError) as error:
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
    _require_disk_space(db.DATA_DIR, _DISK_RESERVE_BYTES)
    if not isinstance(checksum, str) or re.fullmatch(r'[0-9a-f]{64}', checksum) is None or _digest(output) != checksum:
        raise LocalMusicError('local_music_output_changed', 409)
    duration = _validate_audio(output)
    _validate_conditioned_output(job, output)
    params: JsonObject = {'prompt': job.request.prompt, 'duration_seconds': job.request.duration_seconds,
        'seed': job.request.seed, 'variant': job.request.variant, 'local_music_job_id': job.id}
    if isinstance(job, _StoredJob) and job.music_evidence is not None:
        params['_music_evidence'] = serialize_music_evidence(job.music_evidence)
    if job.request.conditioning is not None and job.conditioning_recipe is not None:
        params['_music_conditioning'] = _object.validate_json(job.conditioning_recipe.model_dump_json())
        params['conditioning_source'] = _object.validate_json(job.request.conditioning.source.model_dump_json())
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
    _validate_conditioned_output(job, pending)
    checksum = _digest(pending)
    with pending.open('rb') as handle:
        os.fsync(handle.fileno())
    connection = db.get_db()
    connection.execute('UPDATE local_music_jobs SET output_sha256=? WHERE id=?', (checksum, job.id))
    connection.commit()
    _publish_audio(pending, _output(job))
    _save_track(job)


def _clean_pending(identifier: str, *, keep_output: bool = False) -> None:
    # Keep supervisor identity/receipt and validated final output for recovery.
    for name in ('output.wav', 'output.partial.wav', 'raw.wav', 'request.json', 'conditioning-resampled.wav'):
        if name == 'output.wav' and keep_output:
            continue
        path = _artifact(identifier, name)
        path.unlink(missing_ok=True)


def _validate_conditioned_output(job: LocalMusicJob, path: Path) -> None:
    if job.request.conditioning is None:
        return
    if (not isinstance(job, _StoredJob) or job.conditioning_snapshot is None or job.conditioning_recipe is None
            or job.conditioning_recipe != job.conditioning_snapshot.recipe
            or job.conditioning_recipe.source_sha256 != job.request.conditioning.source_sha256
            or job.conditioning_final_sha256 != _digest(path)
            or _digest(_artifact(job.id, job.conditioning_snapshot.original_name)) != job.conditioning_recipe.source_sha256
            or _digest(_artifact(job.id, 'conditioning.wav')) != job.conditioning_recipe.prepared_sha256):
        raise LocalMusicError('conditioning_snapshot_invalid')
    info = sf.info(path)
    recipe = job.conditioning_recipe
    expected = ((recipe.source_rate_hz, recipe.source_channels, recipe.source_frames) if recipe.mode == 'inpaint'
        else (44100, 2, recipe.prepared_frames))
    if (info.samplerate, info.channels, info.frames) != expected:
        raise LocalMusicError('conditioning_output_invalid')


async def _conditioning_command(identifier: str, argv: list[str], timeout: float) -> bytes:
    """CPU preparation shares the existing durable job's exact process ownership."""
    process: asyncio.subprocess.Process | None = None
    try:
        process = await spawn_owned(argv, receipt_path=_artifact(identifier, 'worker.json'),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            on_identity=lambda identity: _store_identity(identifier, identity))
        output = await read_owned_output(process, max_bytes=1024**2, timeout=timeout)
        if process.returncode != 0:
            raise LocalMusicError('conditioning_output_invalid')
        return output
    finally:
        try:
            await await_cleanup(kill_process_tree(process))
        except (OSError, TimeoutError) as error:
            _unverified.add(identifier)
            raise LocalMusicError('local_music_worker_unverified', 503) from error
        if process is not None:
            _store_identity(identifier, None)


def _require_ready(request: LocalMusicRequest) -> None:
    status = next((item for item in engine_status().engines if item.id == request.engine), None)
    if status is None or not status.supported:
        raise LocalMusicError('local_music_runtime_unsupported', 409)
    if not status.installed:
        raise LocalMusicError('local_music_engine_not_installed', 409)
    selected_ready = status.medium_weights_ready if request.variant == 'medium' else status.small_weights_ready
    if not selected_ready:
        raise LocalMusicError('local_music_weights_missing', 409)
    if request.adapter is not None:
        music_lora.resolve(request.adapter, engine=request.engine, target=request.variant)


async def _run(identifier: str) -> None:
    job = get(identifier)
    publishing = False
    try:
        output = _artifact(identifier, 'output.wav', create=True).resolve()
        snapshot: RetainedConditioning | None = None
        if job.request.conditioning is not None:
            if not isinstance(job, _StoredJob):
                raise LocalMusicError('conditioning_snapshot_invalid')
            proof = await music_conditioning.verify_runtime(job.request.variant)
            async def prepare_command(argv: list[str], timeout: float) -> bytes:
                return await _conditioning_command(identifier, argv, timeout)
            snapshot = await music_conditioning.prepare(job.request, output.parent, run=prepare_command, proof=proof,
                retained_input=job.retained_input)
            job.conditioning_snapshot = snapshot
            job.conditioning_recipe = snapshot.recipe
            job.music_evidence = merge_music_evidence([job.music_evidence, snapshot.source_music_evidence])
            _store(job)
            music_conditioning.release(identifier)
        async with gpu_lease(gpu_lock, 'gpu_busy', job.request.engine):
            _require_ready(job.request)
            _require_generation_space(job.request)
            if job.adapter is not None and music_lora.get(job.adapter.id) != job.adapter:
                raise MusicLoraError('music_lora_adapter_changed')
            worker_output = _artifact(identifier, 'generated.wav').resolve() if snapshot is not None and snapshot.recipe.mode == 'inpaint' else output
            if worker_output.exists() or worker_output.is_symlink():
                raise LocalMusicError('local_music_output_exists')
            argv, cwd, env = (prepare_worker(job.request, worker_output, conditioning=snapshot) if snapshot is not None
                else prepare_worker(job.request, worker_output))
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
        if snapshot is not None:
            if snapshot.recipe.mode == 'inpaint':
                recipe = snapshot.recipe
                if recipe.region_start_frame is None or recipe.region_end_frame is None:
                    raise LocalMusicError('conditioning_invalid_region')
                await splice_inpaint(output.parent / snapshot.original_name, worker_output, output,
                    sample_rate_hz=recipe.source_rate_hz, channels=recipe.source_channels,
                    source_frames=recipe.source_frames, output_frames=recipe.source_frames,
                    start_frame=recipe.region_start_frame, end_frame=recipe.region_end_frame,
                    crossfade_frames=recipe.crossfade_frames, run=prepare_command)
            if not isinstance(job, _StoredJob):
                raise LocalMusicError('conditioning_snapshot_invalid')
            job.conditioning_final_sha256 = _digest(output)
            _store(job)
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
        if isinstance(error, (LocalMusicError, MusicLoraError)):
            job.error_code = error.code
        elif isinstance(error, OSError) and error.errno == errno.ENOSPC:
            job.error_code = 'insufficient_disk'
        else:
            job.error_code = 'local_music_save_failed' if publishing else 'local_music_generation_failed'
        _store(job)
    finally:
        music_conditioning.release(identifier)
        if identifier not in _unverified:
            try:
                retained = db.get_db().execute('SELECT output_sha256 FROM local_music_jobs WHERE id=?', (identifier,)).fetchone()
                _clean_pending(identifier, keep_output=job.request.conditioning is not None or job.status == 'failed' and
                    (job.error_code in {'local_music_save_failed', 'insufficient_disk', 'local_music_output_exists'} or retained is not None and retained['output_sha256'] is not None))
            except (OSError, LocalMusicError, sqlite3.Error):
                logger.warning('Local music partial output retained safely', exc_info=True)
        _tasks.pop(identifier, None)


async def submit(request: LocalMusicRequest, *, retained_input: QueuedMusicSource | None = None) -> LocalMusicJob:
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
        if retained_input is not None and (request.conditioning is None or
                request.conditioning.source != retained_input.snapshot.source or
                request.conditioning.source_sha256 != retained_input.snapshot.sha256):
            raise LocalMusicError('conditioning_snapshot_invalid')
        _require_generation_space(request)
        identifier = uuid.uuid4().hex
        job = _StoredJob(id=identifier, status='queued', created_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), request=request,
            music_evidence=capture_music_evidence(request.engine),
            adapter=music_lora.get(request.adapter.id) if request.adapter is not None else None, retained_input=retained_input)
        if request.conditioning is not None and retained_input is None:
            await music_conditioning.register(identifier, request.conditioning.source)
        try:
            connection = db.get_db()
            connection.execute('INSERT INTO local_music_jobs(id,payload_json,created_at) VALUES(?,?,?)',
                (identifier, job.model_dump_json(exclude={'track'}), job.created_at))
            connection.commit()
            _tasks[identifier] = asyncio.create_task(_run(identifier))
            _tasks[identifier].add_done_callback(lambda _: music_conditioning.release(identifier))
        except BaseException:
            music_conditioning.release(identifier)
            raise
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
        except LocalMusicError as error:
            if error.status != 410:
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
                        _publish_audio(pending, output)
                _save_track(job)
                job.status = 'done'
                job.error_code = None
            elif job.status in {'queued', 'running'} or job.error_code in {'local_music_save_failed', 'insufficient_disk'}:
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
            if isinstance(error, (LocalMusicError, MusicLoraError)):
                job.error_code = error.code
            elif isinstance(error, OSError) and error.errno == errno.ENOSPC:
                job.error_code = 'insufficient_disk'
            else:
                job.error_code = 'local_music_save_failed'
        _store(job)
        try:
            retained = db.get_db().execute('SELECT output_sha256 FROM local_music_jobs WHERE id=?', (identifier,)).fetchone()
            _clean_pending(identifier, keep_output=job.request.conditioning is not None or job.status == 'failed' and
                (job.error_code in {'local_music_save_failed', 'insufficient_disk'} or retained is not None and retained['output_sha256'] is not None))
        except (OSError, LocalMusicError):
            logger.warning('Local music partial retained without following unsafe paths')
    _closing = False


async def shutdown() -> None:
    global _closing
    _closing = True
    identifiers = tuple(_tasks)
    if identifiers:
        await await_cleanup(asyncio.gather(*(cancel(identifier) for identifier in identifiers), return_exceptions=True))
