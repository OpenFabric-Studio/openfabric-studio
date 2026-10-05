"""Owned cloud music jobs. Recovery can save local output; it never resubmits."""
from __future__ import annotations
import asyncio
import logging
import hashlib
import os
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
import wave
from pydantic import TypeAdapter, ValidationError
from . import db, openrouter_catalog as catalog, openrouter_requests as requests
from .cloud_music_contracts import CloudMusicJob, CloudMusicJobs, CloudMusicSubmitRequest
from .contracts import JsonObject
from .job_lifecycle import await_cleanup
from .openrouter_contracts import OpenRouterMusicRequest
from .openrouter_errors import OpenRouterError
from .track_view import track_response

logger=logging.getLogger(__name__)
_tasks: dict[str,asyncio.Task[None]]={}
_json=TypeAdapter(JsonObject)
_closing=False

@dataclass(frozen=True)
class MusicAudio:
    data: bytes
    format: Literal['wav']


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE IF NOT EXISTS cloud_music_jobs (
        id TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
        track_id INTEGER UNIQUE REFERENCES tracks(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL)''')
    connection.execute('CREATE INDEX IF NOT EXISTS idx_cloud_music_created ON cloud_music_jobs(created_at DESC)')
    connection.commit()


def _store(job: CloudMusicJob) -> None:
    connection=db.get_db()
    connection.execute('UPDATE cloud_music_jobs SET payload_json=? WHERE id=?',(job.model_dump_json(exclude={'receipt','track'}),job.id))
    connection.commit()


def get(identifier: str) -> CloudMusicJob:
    row=db.get_db().execute('SELECT payload_json,track_id FROM cloud_music_jobs WHERE id=?',(identifier,)).fetchone()
    if row is None: raise OpenRouterError('music_job_not_found',404)
    try: job=CloudMusicJob.model_validate_json(row['payload_json'])
    except ValidationError as error: raise OpenRouterError('music_storage_unavailable') from error
    if job.id!=identifier: raise OpenRouterError('music_storage_unavailable')
    try: job.receipt=requests.get(job.receipt_id)
    except OpenRouterError:
        logger.warning('Cloud music receipt unavailable for retained job %s',identifier)
        job.receipt=None
    if row['track_id'] is not None:
        track=db.get_track(row['track_id'])
        if track is not None: job.track=track_response(track)
    if job.receipt is None and job.track is None:
        job.status='submission_unknown'; job.error_code='provider_receipt_unavailable'
    job.can_retry_save=job.status=='failed' and job.receipt is not None and job.output_sha256 is not None and _output(job).is_file()
    return job


def list_jobs() -> CloudMusicJobs:
    rows=db.get_db().execute('SELECT id FROM cloud_music_jobs ORDER BY created_at DESC,rowid DESC LIMIT 1000').fetchall()
    jobs: list[CloudMusicJob]=[]; incomplete=False
    for row in rows:
        try: jobs.append(get(row['id']))
        except OpenRouterError:
            incomplete=True; logger.warning('Cloud music record retained but unavailable')
    return CloudMusicJobs(jobs=jobs,history_incomplete=incomplete)


def _output(job: CloudMusicJob, suffix: str='wav') -> Path:
    directory=db.model_dir('openrouter')
    if directory.is_symlink() or directory.resolve().parent != db.FILES_DIR.resolve(): raise OpenRouterError('music_storage_unavailable')
    path=directory/f'{job.id}.{suffix}'
    if path.is_symlink(): raise OpenRouterError('music_storage_unavailable')
    return path


def _validate(path: Path) -> float:
    with wave.open(str(path), 'rb') as audio:
        channels=audio.getnchannels(); rate=audio.getframerate(); frames=audio.getnframes(); width=audio.getsampwidth()
        if channels not in (1,2) or width not in (2,3,4) or not 8000<=rate<=192000 or not 0<frames/rate<=600:
            raise OpenRouterError('invalid_music_audio',502)
        remaining=frames
        while remaining:
            count=min(remaining,65536)
            if len(audio.readframes(count))!=count*channels*width: raise OpenRouterError('invalid_music_audio',502)
            remaining-=count
        return frames/rate*1000


def _publish(job: CloudMusicJob, result: MusicAudio) -> None:
    if not 0<len(result.data)<=80*1024*1024: raise OpenRouterError('invalid_music_audio',502)
    temporary=_output(job,'pending.wav'); output=_output(job)
    try:
        with temporary.open('xb') as handle: handle.write(result.data); handle.flush(); os.fsync(handle.fileno())
        _validate(temporary)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    _save_track(job)


def _digest(path: Path) -> str:
    if path.stat().st_size>80*1024*1024: raise OpenRouterError('invalid_music_audio',502)
    checksum=hashlib.sha256();count=0
    with path.open('rb') as handle:
        while chunk:=handle.read(65536):
            count+=len(chunk)
            if count>80*1024*1024: raise OpenRouterError('invalid_music_audio',502)
            checksum.update(chunk)
    return checksum.hexdigest()


def _save_track(job: CloudMusicJob) -> None:
    if job.track is not None: return
    output=_output(job)
    if job.output_sha256 is None or _digest(output)!=job.output_sha256: raise OpenRouterError('music_output_changed',409)
    duration=_validate(output)
    receipt=requests.get(job.receipt_id)
    params: JsonObject={'provider':'openrouter','model_id':job.request.model,'prompt':job.request.prompt,
        'receipt':_json.validate_json(receipt.model_dump_json()),'seed':job.request.seed,'cloud_music_job_id':job.id}
    db.insert_track(model='openrouter',title=job.title or 'Cloud music',lyrics='',seed=job.request.seed,duration_ms=duration,
        wall_ms=None,params=params,audio_path=output,abc_path=None,cloud_music_job_id=job.id)


async def _generate(job: CloudMusicJob) -> MusicAudio:
    from .openrouter_client import OpenRouterClient
    result=await OpenRouterClient().complete_music(job.receipt_id,job.request,requests.get(job.receipt_id).quote_id)
    if result.format!='wav': raise OpenRouterError('invalid_music_audio',502)
    return MusicAudio(result.data,'wav')


async def _run(identifier: str) -> None:
    job=get(identifier); job.status='running'; _store(job)
    try:
        result=await _generate(job)
        job.output_sha256=hashlib.sha256(result.data).hexdigest(); _store(job)
        # Publication stays synchronous on the event loop: cancellation cannot
        # abandon a decoding thread that later inserts a completed track.
        _publish(job,result); job=get(identifier); job.status='done'; job.error_code=''; _store(job)
    except asyncio.CancelledError:
        job=get(identifier); job.status='canceled_tracking'; job.error_code='remote_cancellation_unconfirmed'; _store(job)
        raise
    except Exception as error:
        logger.exception('Cloud music job failed')
        job=get(identifier)
        code=error.code if isinstance(error,OpenRouterError) else 'music_generation_failed'
        job.error_code=code; job.status='submission_unknown' if code=='submission_unknown' else 'failed'; _store(job)
    finally:
        _tasks.pop(identifier,None)


def submit(body: CloudMusicSubmitRequest) -> CloudMusicJob:
    if _closing: raise OpenRouterError('provider_shutting_down',503)
    if not body.transfers_confirmed: raise OpenRouterError('cloud_transfer_confirmation_required',422)
    request=OpenRouterMusicRequest(model=body.model,prompt=body.prompt,seed=body.seed)
    quote=catalog.validate_quote(body.quote_id,catalog.music_parameters(request))
    identifier=uuid.uuid4().hex
    receipt=requests.prepare(f'music:{identifier}',quote)
    job=CloudMusicJob(id=identifier,status='queued',created_at=requests.now(),title=body.title,request=request,receipt_id=receipt.id)
    connection=db.get_db()
    connection.execute('INSERT INTO cloud_music_jobs(id,payload_json,created_at) VALUES(?,?,?)',(job.id,job.model_dump_json(exclude={'receipt','track'}),job.created_at)); connection.commit()
    _tasks[identifier]=asyncio.create_task(_run(identifier))
    return get(identifier)


def work_busy() -> bool:
    return any(not task.done() for task in _tasks.values())


async def cancel(identifier: str) -> CloudMusicJob:
    task=_tasks.get(identifier)
    if task is not None:
        task.cancel(); await await_cleanup(asyncio.gather(task,return_exceptions=True))
        _tasks.pop(identifier,None)
        job=get(identifier)
        if job.status in {'queued','running'}:
            job.status='canceled_tracking'; job.error_code='remote_cancellation_unconfirmed'; _store(job)
    return get(identifier)


def retry_save(identifier: str) -> CloudMusicJob:
    if identifier in _tasks: raise OpenRouterError('music_job_busy',409)
    job=get(identifier)
    if job.track is not None: return job
    if not job.can_retry_save: raise OpenRouterError('music_output_missing',409)
    try: _save_track(job)
    except OpenRouterError: raise
    except (OSError,ValueError,sqlite3.Error,wave.Error) as error:
        logger.exception('Cloud music save retry failed'); raise OpenRouterError('music_save_failed') from error
    job=get(identifier); job.status='done'; job.error_code=''; _store(job)
    return get(identifier)


async def recover() -> None:
    global _closing
    _closing=False
    rows=db.get_db().execute('SELECT id FROM cloud_music_jobs').fetchall()
    for row in rows:
        try: job=get(row['id'])
        except OpenRouterError:
            logger.warning('Cloud music record retained but skipped during recovery'); continue
        if job.status not in {'queued','running'}: continue
        try:
            if job.track is not None: job.status='done'; job.error_code=''
            elif _output(job).is_file():
                try: _save_track(job); job.status='done'; job.error_code=''
                except Exception:
                    logger.exception('Cloud music local recovery failed'); job.status='failed'; job.error_code='music_save_failed'
            else:
                job.status='submission_unknown' if job.receipt is not None and job.receipt.state in {'submitting','submission_unknown','submitted'} else 'interrupted'
                job.error_code='submission_unknown' if job.status=='submission_unknown' else 'music_interrupted'
            _store(job)
        except (OpenRouterError,OSError,ValueError):
            logger.warning('Cloud music artifact retained but unavailable during recovery')
            job.status='failed'; job.error_code='music_storage_unavailable'; _store(job)


async def shutdown() -> None:
    global _closing
    _closing=True
    identifiers=list(_tasks)
    if identifiers: await await_cleanup(asyncio.gather(*(cancel(identifier) for identifier in identifiers),return_exceptions=True))
