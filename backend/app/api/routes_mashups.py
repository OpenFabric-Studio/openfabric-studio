"""Bounded local-only mashup requests; filenames never select input paths."""
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

from .. import mashup_render, mashup_store, mashups
from ..beat_project_contracts import MusicProjectPublication
from ..job_lifecycle import run_owned_thread
from ..mashup_contracts import (MashupCreateRequest, MashupEditPlanRequest, MashupError,
    MashupFailure, MashupGeneratePlanRequest, MashupJob, MashupJobRequest, MashupJobs,
    MashupSourcePreview, MashupSourcePreviewRequest, MashupState, MashupStates,
    MashupStoragePreview, MashupTimingUpdate)
from ..module_security import require_local_origin
from ..video_media import VideoMediaError
from ..video_projects import VideoProjectError

logger = logging.getLogger(__name__)


class MashupRoute(APIRoute):
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
                        raise HTTPException(413, 'mashup_budget_exceeded')
                return message

            try:
                return await handler(Request(request.scope, receive))
            except RequestValidationError as error:
                raise HTTPException(422, 'mashup_invalid') from error
            except MashupError as error:
                failure = MashupFailure(code=error.code, conflicting_ids=error.conflicting_ids)
                status = 404 if error.code in ('mashup_not_found', 'mashup_job_not_found', 'mashup_source_missing') else 503 if error.code == 'mashup_storage_unavailable' else 409
                return JSONResponse(status_code=status, content={'detail': error.code, 'error': failure.model_dump(mode='json')})
            except VideoProjectError as error:
                raise HTTPException(409, 'mashup_source_changed') from error
            except FileNotFoundError as error:
                raise HTTPException(404, 'mashup_source_missing') from error
            except (OSError, sqlite3.Error, ValidationError, VideoMediaError, ValueError, RuntimeError) as error:
                logger.exception('Mashup storage or validation failed')
                raise HTTPException(409 if isinstance(error, OSError) and error.errno == errno.ENOSPC else 503,
                    'insufficient_disk' if isinstance(error, OSError) and error.errno == errno.ENOSPC else 'mashup_storage_unavailable') from error
        return bounded


router = APIRouter(prefix='/api/mashups', tags=['mashups'], route_class=MashupRoute)


async def _empty(request: Request) -> None:
    if (await request.body()).strip() not in (b'', b'{}'):
        raise HTTPException(422, 'mashup_invalid')


@router.post('/source-preview', response_model=MashupSourcePreview)
async def source_preview(body: MashupSourcePreviewRequest) -> MashupSourcePreview:
    return await mashups.source_preview(body)


@router.get('', response_model=MashupStates)
async def list_documents() -> MashupStates:
    return MashupStates(documents=mashup_store.list_documents())


@router.post('', response_model=MashupState, status_code=202)
async def create(body: MashupCreateRequest) -> MashupState:
    return await mashups.create(body)


@router.get('/jobs/{job_id}', response_model=MashupJob)
async def get_job(job_id: str) -> MashupJob:
    return mashups.get_job(job_id)


@router.post('/jobs/{job_id}/cancel', response_model=MashupJob)
async def cancel_job(job_id: str, request: Request) -> MashupJob:
    await _empty(request)
    return await mashups.cancel(job_id)


@router.post('/jobs/{job_id}/retry-save', response_model=MashupJob)
async def retry_save(job_id: str, request: Request) -> MashupJob:
    await _empty(request)
    return await mashups.retry_save(job_id)


@router.post('/jobs/{job_id}/project', response_model=MusicProjectPublication)
async def project(job_id: str, request: Request) -> MusicProjectPublication:
    await _empty(request)
    return mashups.project(job_id)


@router.get('/jobs/{job_id}/artifacts/{artifact_id}')
async def artifact(job_id: str, artifact_id: str) -> FileResponse:
    item = mashups.artifact_metadata(job_id, artifact_id)
    path = await run_owned_thread(mashup_render.resolve_artifact, job_id, item)
    return FileResponse(path, media_type=item.media_type, filename=item.filename)


@router.get('/{mashup_id}', response_model=MashupState)
async def get(mashup_id: str) -> MashupState:
    return mashup_store.get(mashup_id)


@router.get('/{mashup_id}/jobs', response_model=MashupJobs)
async def jobs(mashup_id: str) -> MashupJobs:
    mashup_store.get(mashup_id)
    return MashupJobs(jobs=mashup_store.list_jobs(mashup_id))


@router.patch('/{mashup_id}/timing', response_model=MashupState)
async def timing(mashup_id: str, body: MashupTimingUpdate) -> MashupState:
    return mashups.update_timing(mashup_id, body)


@router.post('/{mashup_id}/plan', response_model=MashupState)
async def generate_plan(mashup_id: str, body: MashupGeneratePlanRequest) -> MashupState:
    return mashups.generate_plan(mashup_id, body)


@router.patch('/{mashup_id}/plan', response_model=MashupState)
async def edit_plan(mashup_id: str, body: MashupEditPlanRequest) -> MashupState:
    return mashups.edit_plan(mashup_id, body)


@router.post('/{mashup_id}/storage-preview', response_model=MashupStoragePreview)
async def storage_preview(mashup_id: str, body: MashupJobRequest) -> MashupStoragePreview:
    return await mashups.storage_preview(mashup_id, body)


@router.post('/{mashup_id}/preview', response_model=MashupJob, status_code=202)
async def preview(mashup_id: str, body: MashupJobRequest) -> MashupJob:
    if body.mode != 'preview':
        raise MashupError('mashup_invalid')
    return await mashups.start_render(mashup_id, body)


@router.post('/{mashup_id}/render', response_model=MashupJob, status_code=202)
async def render(mashup_id: str, body: MashupJobRequest) -> MashupJob:
    if body.mode != 'full':
        raise MashupError('mashup_invalid')
    return await mashups.start_render(mashup_id, body)


@router.post('/{mashup_id}/cancel', response_model=MashupState)
async def cancel(mashup_id: str, request: Request) -> MashupState:
    await _empty(request)
    return await mashups.cancel_document(mashup_id)


@router.post('/{mashup_id}/retry-preparation', response_model=MashupState)
async def retry_preparation(mashup_id: str, request: Request) -> MashupState:
    await _empty(request)
    return await mashups.retry_preparation(mashup_id)
