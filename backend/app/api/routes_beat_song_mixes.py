"""Local-only reviewed beat/song plans; URLs never choose filesystem paths."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
import errno
import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.types import Message

from .. import beat_song_mix, beat_song_mix_store, beat_song_render, song_sources
from ..beat_contracts import BeatError
from ..beat_project_contracts import MusicProjectPublication
from ..beat_song_mix_contracts import (BeatSongMixDraft, BeatSongMixError, BeatSongMixFailure, BeatSongMixJob,
    BeatSongMixJobs, BeatSongMixPlan, BeatSongMixPreview, BeatSongMixStartRequest, BeatSongMixUpdate,
    BeatSongTransposePreview, BeatSongTransposeRequest, SongSourceInfo, SongSourceRequest)
from ..job_lifecycle import run_owned_thread
from ..module_security import require_local_origin
from ..video_projects import VideoProjectError

logger = logging.getLogger(__name__)


class BeatSongRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()

        async def bounded(request: Request) -> Response:
            if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                require_local_origin(request)
            count = 0

            async def receive() -> Message:
                nonlocal count
                message = await request.receive()
                if message['type'] == 'http.request':
                    chunk: object = message.get('body', b'')
                    if isinstance(chunk, bytes):
                        count += len(chunk)
                    if count > 2 * 1024**2:
                        raise HTTPException(413, 'beat_mix_budget_exceeded')
                return message

            try:
                return await handler(Request(request.scope, receive))
            except RequestValidationError as error:
                raise HTTPException(422, 'beat_mix_invalid') from error
            except BeatSongMixError as error:
                status = 404 if error.code in ('beat_mix_not_found', 'song_source_missing') else 503 if error.code == 'song_storage_unavailable' else 409
                return JSONResponse(status_code=status, content={'detail': error.code,
                    'error': BeatSongMixFailure(code=error.code).model_dump(mode='json')})
            except (VideoProjectError, BeatError, FileNotFoundError) as error:
                raise HTTPException(409, 'song_source_changed') from error
            except (OSError, sqlite3.Error, ValidationError, ValueError, RuntimeError) as error:
                logger.exception('Beat/song storage or validation failed')
                full = isinstance(error, OSError) and error.errno == errno.ENOSPC
                raise HTTPException(409 if full else 503, 'insufficient_disk' if full else 'song_storage_unavailable') from error
        return bounded


router = APIRouter(prefix='/api/beat-song-mixes', tags=['beat-song-mixes'], route_class=BeatSongRoute)


async def _empty(request: Request) -> None:
    if (await request.body()).strip() not in (b'', b'{}'):
        raise HTTPException(422, 'beat_mix_invalid')


@router.post('/sources/inspect', response_model=SongSourceInfo)
async def inspect(body: SongSourceRequest) -> SongSourceInfo:
    return await song_sources.inspect_source(body.source)


@router.post('/transpose-preview', response_model=BeatSongTransposePreview)
async def transpose(body: BeatSongTransposeRequest) -> BeatSongTransposePreview:
    return await beat_song_mix.transpose_preview(body)


@router.post('/plans', response_model=BeatSongMixPreview)
async def preview(body: BeatSongMixDraft) -> BeatSongMixPreview:
    return await beat_song_mix.preview(body)


@router.get('/plans/{plan_id}', response_model=BeatSongMixPlan)
async def get_plan(plan_id: str) -> BeatSongMixPlan:
    return beat_song_mix_store.get_plan(plan_id)


@router.patch('/plans/{plan_id}', response_model=BeatSongMixPreview)
async def update(plan_id: str, body: BeatSongMixUpdate) -> BeatSongMixPreview:
    return await beat_song_mix.update_plan(plan_id, body)


@router.get('', response_model=BeatSongMixJobs)
async def jobs() -> BeatSongMixJobs:
    return BeatSongMixJobs(jobs=beat_song_mix.list_jobs())


@router.post('', response_model=BeatSongMixJob, status_code=202)
async def start(body: BeatSongMixStartRequest) -> BeatSongMixJob:
    return await beat_song_mix.start(body)


@router.get('/{job_id}', response_model=BeatSongMixJob)
async def get_job(job_id: str) -> BeatSongMixJob:
    return beat_song_mix.get_job(job_id)


@router.post('/{job_id}/cancel', response_model=BeatSongMixJob)
async def cancel(job_id: str, request: Request) -> BeatSongMixJob:
    await _empty(request)
    return await beat_song_mix.cancel(job_id)


@router.post('/{job_id}/retry-save', response_model=BeatSongMixJob)
async def retry_save(job_id: str, request: Request) -> BeatSongMixJob:
    await _empty(request)
    return await beat_song_mix.retry_save(job_id)


@router.post('/{job_id}/project', response_model=MusicProjectPublication)
async def project(job_id: str, request: Request) -> MusicProjectPublication:
    await _empty(request)
    return beat_song_mix.project(job_id)


@router.get('/{job_id}/artifacts/{artifact_id}')
async def artifact(job_id: str, artifact_id: str) -> FileResponse:
    # Resolve owner/DB metadata on its thread, then stream-hash only the selected
    # confined asset off the event loop. Do not scan every stem for a download.
    item = beat_song_mix.artifact_metadata(job_id, artifact_id)
    path = await run_owned_thread(beat_song_render.resolve_artifact, job_id, item)
    return FileResponse(path, media_type=item.media_type, filename=item.filename)
