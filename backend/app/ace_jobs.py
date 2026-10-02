"""Backend-owned ACE batches: persist every candidate before voice conversion.

Jobs and candidate identities live in SQLite. A failed download can be retried
without duplicating saved candidates. Native engine jobs themselves are volatile;
shutdown records interrupted work rather than pretending GPU inference resumed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from pathlib import Path
from typing import BinaryIO
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
from fastapi import HTTPException, UploadFile
from pydantic import BaseModel, Field, TypeAdapter

from . import db
from .audio_encoding import AudioEncodingSettings, load_settings
from . import audio_exports, audio_versions
from .contracts import AceJobReleaseResponse, AceJobResponse, AceSubmitParams, JsonObject, JsonValue
from .track_view import track_response
from .config import MODELS
from .orchestrator.manager import manager
from .orchestrator.state import ModelStatus
from .job_lifecycle import await_cleanup
from .voice_build import start_apply, public_voice, record_apply_failure

logger = logging.getLogger(__name__)
_json: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_object = TypeAdapter(JsonObject)
_tasks: dict[str, asyncio.Task[None]] = {}
_deleting: set[str] = set()
_ACTIVE = {'queued', 'running'}


class EngineResult(BaseModel):
    task_id: str
    status: int
    result: str


class Candidate(BaseModel):
    file: str = Field(min_length=1)
    metas: JsonObject = Field(default_factory=dict)
    seed_value: str = ''


_candidates = TypeAdapter(list[Candidate])
_engine_results = TypeAdapter(list[EngineResult])


def ensure_schema() -> None:
    # Migrations run once during connection initialization. Never issue DDL
    # from a history read: executescript would commit an unrelated transaction.
    db.get_db()


def _store(job: AceJobResponse, results: list[JsonObject] | None = None) -> None:
    connection = db.get_db()
    if results is None:
        connection.execute('UPDATE ace_jobs SET payload_json = ? WHERE task_id = ?', (job.model_dump_json(), job.task_id))
    else:
        connection.execute('UPDATE ace_jobs SET payload_json = ?, results_json = ? WHERE task_id = ?', (job.model_dump_json(), json.dumps(results, allow_nan=False), job.task_id))
    connection.commit()


def record_job(task_id: str, params: JsonObject, title: str, voice_id: str | None, track_ids: tuple[int, ...] = ()) -> None:
    ensure_schema()
    validated = AceSubmitParams.model_validate(params)
    job = AceJobResponse(task_id=task_id, status='queued', created_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), title=title, lyrics=validated.lyrics, audio_format=validated.audio_format, batch_size=validated.batch_size, params=params, progress=0, stage='queued', error='', error_code='', tracks=[], voice_id=voice_id)
    connection = db.get_db()
    connection.execute('BEGIN IMMEDIATE')
    try:
        connection.execute('INSERT INTO ace_jobs(task_id, payload_json, created_at) VALUES (?, ?, ?)', (task_id, job.model_dump_json(), job.created_at))
        for index, track_id in enumerate(track_ids):
            connection.execute('INSERT INTO ace_candidates(task_id,candidate_index,track_id) VALUES(?,?,?)', (task_id, index, track_id))
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def get_job(task_id: str) -> AceJobResponse:
    row = db.get_db().execute('SELECT payload_json FROM ace_jobs WHERE task_id = ?', (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail='job_not_found')
    job = AceJobResponse.model_validate_json(row['payload_json'])
    tracks = db.get_db().execute('SELECT t.* FROM ace_candidates c JOIN tracks t ON c.track_id=t.id WHERE c.task_id=? ORDER BY c.candidate_index', (task_id,)).fetchall()
    job.tracks = [track_response(track) for track in tracks]
    # The track rename endpoint is also used on generation cards.
    if job.tracks:
        job.title = job.tracks[0].title
    return job


def list_jobs() -> list[AceJobResponse]:
    ensure_schema()
    return [get_job(row['task_id']) for row in db.get_db().execute('SELECT task_id FROM ace_jobs ORDER BY created_at DESC, rowid DESC').fetchall()]


def work_busy() -> bool:
    """Admission includes durable queued work before upstream stats catch up."""
    ensure_schema()
    if any(not task.done() for task in _tasks.values()):
        return True
    rows = db.get_db().execute('SELECT payload_json FROM ace_jobs').fetchall()
    return any(AceJobResponse.model_validate_json(row['payload_json']).status in _ACTIVE for row in rows)


def adopt(task_id: str, params: JsonObject, title: str, voice_id: str | None, track_ids: list[int]) -> AceJobResponse:
    ensure_schema()
    existing = db.get_db().execute('SELECT 1 FROM ace_jobs WHERE task_id=?', (task_id,)).fetchone()
    if existing:
        return get_job(task_id)
    if len(set(track_ids)) != len(track_ids):
        raise HTTPException(status_code=422, detail='invalid_candidate_tracks')
    for track_id in track_ids:
        track = db.get_track(track_id)
        linked = db.get_db().execute('SELECT 1 FROM ace_candidates WHERE track_id=?', (track_id,)).fetchone()
        if not track or track['model'] != 'ace_step' or linked:
            raise HTTPException(status_code=422, detail='invalid_candidate_tracks')
    record_job(task_id, params, title, voice_id, tuple(track_ids))
    launch(task_id)
    return get_job(task_id)


def _engine_running() -> bool:
    return manager.state.models['ace_step'].status == ModelStatus.RUNNING


async def _engine_json(client: httpx.AsyncClient, endpoint: str, payload: JsonObject) -> JsonValue:
    response = await client.post(MODELS['ace_step'].proxy_target + endpoint, json=payload)
    response.raise_for_status()
    data = _json.validate_json(response.content)
    if isinstance(data, dict) and 'code' in data and 'data' in data:
        if data['code'] not in (0, 200):
            raise ValueError('Engine rejected request')
        return data['data']
    return data


def _audio_part(audio: UploadFile) -> tuple[str, BinaryIO, str | None]:
    """Bound each independent native upload without buffering it in memory."""
    filename = Path((audio.filename or 'reference.wav').replace('\\', '/')).name
    if Path(filename).suffix.lower() not in {'.wav', '.flac', '.mp3', '.ogg', '.m4a', '.aac', '.aif', '.aiff', '.opus'}:
        raise HTTPException(status_code=422, detail='invalid_reference_audio')
    position = audio.file.tell()
    audio.file.seek(0, 2)
    size = audio.file.tell()
    audio.file.seek(position)
    if not 0 < size <= 512 * 1024 ** 2:
        raise HTTPException(status_code=422, detail='invalid_reference_audio')
    return filename, audio.file, audio.content_type


async def submit(params: JsonObject, title: str, voice_id: str | None, audio: UploadFile | None,
                 style_audio: UploadFile | None = None) -> AceJobReleaseResponse:
    if not _engine_running():
        raise HTTPException(status_code=503, detail='model_inactive')
    validated = AceSubmitParams.model_validate(params)
    # Retain a lossless source for independent voice versions and exports.
    # Internal metadata is server-owned and must never reach the native engine.
    snapshot = params.get('_generation_settings')
    clean = {key: value for key, value in params.items() if key not in {'_source_audio_format', '_encoding_settings', '_generation_settings'}}
    engine_params: JsonObject = {**clean, 'audio_format': 'wav', 'batch_size': validated.batch_size}
    params = {**clean, 'audio_format': validated.audio_format, 'batch_size': validated.batch_size,
              '_source_audio_format': 'wav', '_encoding_settings': _object.validate_json(load_settings().model_dump_json())}
    if isinstance(snapshot, dict):
        params['_generation_settings'] = snapshot
    if voice_id:
        voice = public_voice(voice_id)
        if not voice.get('usable'):
            raise HTTPException(status_code=409, detail='voice_not_ready')
    async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
        if audio is None and style_audio is None:
            result = await _engine_json(client, '/release_task', engine_params)
        else:
            fields = {key: json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value) for key, value in engine_params.items() if value is not None}
            files: dict[str, tuple[str, BinaryIO, str | None]] = {}
            if audio is not None:
                files['ctx_audio'] = _audio_part(audio)
            if style_audio is not None:
                files['ref_audio'] = _audio_part(style_audio)
            response = await client.post(MODELS['ace_step'].proxy_target + '/release_task', data=fields, files=files)
            response.raise_for_status()
            wrapped = _object.validate_json(response.content)
            if wrapped.get('code', 200) not in (0, 200):
                raise ValueError('Engine rejected request')
            result = wrapped.get('data', wrapped)
        released = AceJobReleaseResponse.model_validate(result)
        try:
            record_job(released.task_id, params, title, voice_id)
        except Exception:
            # Do not leave an unowned engine job if recording its identity fails.
            try:
                await _engine_json(client, '/cancel_task', {'task_id': released.task_id})
            except Exception:
                logger.exception('Unable to cancel unrecorded ACE task')
            raise
    launch(released.task_id)
    return released


def audio_endpoint(path: str) -> str:
    parsed = urlsplit(path)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if parsed.scheme or parsed.netloc or parsed.fragment or parsed.path != '/v1/audio' or len(pairs) != 1 or pairs[0][0] != 'path' or not pairs[0][1]:
        raise ValueError('Invalid engine audio endpoint')
    return '/v1/audio?' + urlencode(pairs)


async def _download(client: httpx.AsyncClient, endpoint: str, dest: Path) -> None:
    async with client.stream('GET', MODELS['ace_step'].proxy_target + endpoint) as response:
        response.raise_for_status()
        size = 0
        with dest.open('xb') as output:
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                # Guard a broken engine stream without buffering songs in memory.
                if size > 2 * 1024 ** 3:
                    raise ValueError('Generated audio is too large')
                output.write(chunk)
        if size == 0:
            raise ValueError('Generated audio is empty')


def _duration(candidate: Candidate) -> float | None:
    value = candidate.metas.get('duration')
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    try:
        milliseconds = float(value) * 1000
    except OverflowError:
        return None
    return milliseconds if math.isfinite(milliseconds) else None


def _seed(candidate: Candidate, index: int) -> int | None:
    values = candidate.seed_value.split(',')
    try:
        seed = int(values[index] if index < len(values) else values[0])
        return seed if -(2 ** 63) <= seed <= 2 ** 63 - 1 else None
    except ValueError:
        return None


def _candidate_path(job: AceJobResponse, index: int) -> Path:
    # Stable identity bridges a process death between the file rename and the
    # track+candidate transaction. Never use an engine-supplied ID as a path.
    identity = hashlib.sha256(job.task_id.encode('utf-8')).hexdigest()
    source_format = 'wav' if job.params.get('_source_audio_format') == 'wav' else job.audio_format
    return db.model_dir('ace_step') / f'ace_{identity}_{index}.{source_format}'


async def save_results(client: httpx.AsyncClient, task_id: str, results: list[JsonObject]) -> None:
    if task_id in _deleting:
        return
    job = get_job(task_id)
    if job.status == 'cancelled':
        return
    job.status, job.stage, job.error, job.error_code = 'running', 'saving', '', ''
    _store(job, results)
    try:
        candidates = _candidates.validate_python(results)
        if not candidates:
            raise ValueError('No generated audio')
        for index, candidate in enumerate(candidates):
            exists = db.get_db().execute('SELECT 1 FROM ace_candidates WHERE task_id=? AND candidate_index=?', (task_id, index)).fetchone()
            if exists:
                continue
            endpoint = audio_endpoint(candidate.file)
            dest = _candidate_path(job, index)
            partial = dest.with_suffix(dest.suffix + '.partial')
            try:
                partial.unlink(missing_ok=True)
                if not dest.is_file():
                    await _download(client, endpoint, partial)
                    partial.replace(dest)
                db.insert_track(model='ace_step', title=job.title, lyrics=job.lyrics, seed=_seed(candidate, index), duration_ms=_duration(candidate), wall_ms=None, params=job.params, audio_path=dest, abc_path=None, ace_candidate=(task_id, index))
            except BaseException:
                partial.unlink(missing_ok=True)
                # Keep a completed file when SQLite or the process fails.
                # Retry can register it even after native temporary files expire.
                raise
        # Register all immutable originals before any conversion can publish a
        # new default. Captured settings survive save retries and later edits.
        if job.params.get('_source_audio_format') == 'wav':
            settings = AudioEncodingSettings.model_validate(job.params.get('_encoding_settings'))
            saved = db.get_db().execute('SELECT candidate_index, track_id FROM ace_candidates WHERE task_id=? ORDER BY candidate_index', (task_id,)).fetchall()
            for row in saved:
                original = audio_versions.retain_original(row['track_id'], _candidate_path(job, row['candidate_index']))
                await audio_exports.create_export(row['track_id'], original.id, job.audio_format, settings, update_default=True)
        rows = db.get_db().execute('SELECT candidate_index, track_id FROM ace_candidates WHERE task_id=? AND voice_started=0 ORDER BY candidate_index', (task_id,)).fetchall()
        voice_failed = False
        for row in rows:
            if job.voice_id and row['track_id'] is not None:
                try:
                    start_apply(job.voice_id, row['track_id'])
                except Exception:
                    logger.exception('Unable to start voice conversion for ACE track %s', row['track_id'])
                    record_apply_failure(job.voice_id, row['track_id'], 'not_ready')
                    voice_failed = True
            db.get_db().execute('UPDATE ace_candidates SET voice_started=1 WHERE task_id=? AND candidate_index=?', (task_id, row['candidate_index']))
        db.get_db().commit()
        job.status, job.progress, job.stage = 'done', 1, 'done'
        if voice_failed:
            job.error_code, job.error = 'voice_failed', 'Audio was saved; voice conversion could not start.'
        _store(job)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception('Unable to persist ACE task %s', task_id)
        job.status, job.error_code, job.error = 'failed', 'save_failed', 'Could not save all generated audio. Retry saving this batch.'
        _store(job)


async def _monitor(task_id: str) -> None:
    job = get_job(task_id)
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
            row = db.get_db().execute('SELECT results_json FROM ace_jobs WHERE task_id=?', (task_id,)).fetchone()
            if row and row['results_json']:
                results = TypeAdapter(list[JsonObject]).validate_json(row['results_json'])
                await save_results(client, task_id, results)
                return
            started = time.monotonic()
            while True:
                if not _engine_running():
                    raise RuntimeError('Model stopped')
                raw = await _engine_json(client, '/query_result', {'task_id_list': [task_id]})
                entries = _engine_results.validate_python(raw)
                entry = next((item for item in entries if item.task_id == task_id), None)
                if entry is None:
                    raise ValueError('Missing engine result')
                results = TypeAdapter(list[JsonObject]).validate_json(entry.result)
                if entry.status == 1:
                    await save_results(client, task_id, results)
                    return
                if entry.status == 2:
                    job.status, job.error_code, job.error = 'failed', 'generation_failed', 'Generation failed. Check the model log.'
                    _store(job)
                    return
                if time.monotonic() - started > 3 * 3600:
                    raise TimeoutError('Generation timed out')
                if results:
                    progress, stage = results[0].get('progress'), results[0].get('stage')
                    if isinstance(progress, (int, float)) and not isinstance(progress, bool):
                        job.progress = max(0, min(1, progress))
                    if isinstance(stage, str):
                        job.stage = stage[:120]
                    job.status = 'queued' if job.stage == 'queued' else 'running'
                    _store(job)
                await asyncio.sleep(2)
    except asyncio.CancelledError:
        job = get_job(task_id)
        if job.status in _ACTIVE:
            job.status, job.error_code, job.error = 'failed', 'interrupted', 'Generation was interrupted.'
            _store(job)
        raise
    except Exception:
        logger.exception('ACE task %s interrupted', task_id)
        job.status, job.error_code, job.error = 'failed', 'interrupted', 'Generation was interrupted. Check the model log.'
        _store(job)


def launch(task_id: str) -> None:
    if task_id in _tasks or task_id in _deleting:
        return
    task = asyncio.create_task(_monitor(task_id), name=f'ace:{task_id}')
    _tasks[task_id] = task
    def finished(completed: asyncio.Task[None]) -> None:
        if _tasks.get(task_id) is completed:
            _tasks.pop(task_id, None)
        if not completed.cancelled() and completed.exception() is not None:
            logger.error('ACE monitor failed', exc_info=completed.exception())
    task.add_done_callback(finished)


async def retry_save(task_id: str) -> AceJobResponse:
    if task_id in _deleting:
        raise HTTPException(status_code=409, detail='job_deleting')
    job = get_job(task_id)
    if job.status in _ACTIVE or task_id in _tasks:
        return job
    row = db.get_db().execute('SELECT results_json FROM ace_jobs WHERE task_id=?', (task_id,)).fetchone()
    if job.status != 'failed' or not row or not row['results_json']:
        raise HTTPException(status_code=409, detail='no_results_to_retry')
    job.status, job.stage, job.error, job.error_code = 'running', 'saving', '', ''
    _store(job)
    launch(task_id)
    return job


async def cancel(task_id: str) -> AceJobResponse:
    job = get_job(task_id)
    if job.status not in _ACTIVE:
        return job
    # Ask the engine first. If it fails, do not falsely claim inference stopped.
    if _engine_running():
        async with httpx.AsyncClient(timeout=15) as client:
            result = await _engine_json(client, '/cancel_task', {'task_id': task_id})
            if isinstance(result, dict) and result.get('status') == 'succeeded':
                return get_job(task_id)
    job = get_job(task_id)
    if job.status not in _ACTIVE:
        return job
    job.status, job.stage, job.error, job.error_code = 'cancelled', 'cancelled', '', 'cancelled'
    _store(job)
    task = _tasks.get(task_id)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return get_job(task_id)


async def delete(task_id: str) -> None:
    if task_id in _deleting:
        raise HTTPException(status_code=409, detail='job_deleting')
    job = get_job(task_id)
    if job.status in _ACTIVE:
        raise HTTPException(status_code=409, detail='job_active')
    # Reserve the entire batch before the first drain awaits. A save retry
    # must not create a candidate after deletion has captured the track list.
    _deleting.add(task_id)
    try:
        await await_cleanup(_delete_inner(task_id))
    finally:
        _deleting.discard(task_id)


async def _delete_inner(task_id: str) -> None:
    task = _tasks.get(task_id)
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    job = get_job(task_id)
    # Deleting a generation card deletes its tracks, matching the existing UI.
    for track in job.tracks:
        from .voice_build import protect_track_removal
        async with audio_versions.protect_track_versions_removal(track.id), protect_track_removal(track.id), audio_exports.protect_track_exports_removal(track.id):
            audio_exports.delete_track_exports(track.id)
            db.delete_track(track.id)
    row = db.get_db().execute('SELECT results_json FROM ace_jobs WHERE task_id=?', (task_id,)).fetchone()
    results = TypeAdapter(list[JsonObject]).validate_json(row['results_json']) if row and row['results_json'] else []
    for index in range(max(job.batch_size, len(results))):
        path = _candidate_path(job, index)
        path.unlink(missing_ok=True)
        path.with_suffix(path.suffix + '.partial').unlink(missing_ok=True)
    db.get_db().execute('DELETE FROM ace_jobs WHERE task_id=?', (task_id,))
    db.get_db().commit()


def recover() -> None:
    for job in list_jobs():
        if job.status in _ACTIVE or (job.status == 'failed' and job.error_code == 'interrupted'):
            row = db.get_db().execute('SELECT results_json FROM ace_jobs WHERE task_id=?', (job.task_id,)).fetchone()
            if row and row['results_json']:
                launch(job.task_id)
            else:
                job.status, job.error_code, job.error = 'failed', 'interrupted', 'Generation was interrupted.'
                _store(job)


async def shutdown() -> None:
    tasks = list(_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()
