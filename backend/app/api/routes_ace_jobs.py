"""Durable ACE generation API; browser polling observes backend-owned work."""
from __future__ import annotations
import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import TypeAdapter, ValidationError

from .. import ace_jobs, video_jobs
from ..audio_encoding import AudioEncodingError
from ..resource_admission import ResourceBusyError, native_admission
from ..contracts import AceJobResponse, AceJobReleaseResponse, AceJobsResponse, AceJobQueryResponse, AceQueryRequest, AceAdoptRequest, JsonObject
from ..generation_contracts import AceGenerationSettings

router = APIRouter(prefix='/api/ace-jobs', tags=['ace-jobs'])
logger = logging.getLogger(__name__)


@router.post('', response_model=AceJobReleaseResponse)
async def submit(params: str = Form(..., max_length=262144), title: str = Form('', max_length=500),
                 voice_id: str | None = Form(None), ctx_audio: Annotated[UploadFile | None, File()] = None,
                 ref_audio: Annotated[UploadFile | None, File()] = None,
                 settings: Annotated[str | None, Form(max_length=262144)] = None) -> AceJobReleaseResponse:
    try:
        parsed = TypeAdapter(JsonObject).validate_json(params)
        # Snapshots and encoder metadata are owned here, never trusted as vendor
        # fields supplied inside the opaque native parameter object.
        parsed = {key: value for key, value in parsed.items() if not key.startswith('_')}
        if settings is not None:
            snapshot = AceGenerationSettings.model_validate_json(settings)
            parsed['_generation_settings'] = TypeAdapter(JsonObject).validate_json(snapshot.model_dump_json())
        async with native_admission(video_jobs.work_busy):
            return await ace_jobs.submit(parsed, title, voice_id, ctx_audio, style_audio=ref_audio)
    except ResourceBusyError as exc:
        raise HTTPException(status_code=409, detail=exc.code) from exc
    except AudioEncodingError as exc:
        raise HTTPException(status_code=503, detail=exc.code) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail='invalid_generation_params') from exc
    except httpx.HTTPStatusError as exc:
        logger.exception('ACE rejected generation submission')
        status = 429 if exc.response.status_code == 429 else 502
        raise HTTPException(status_code=status, detail='generation_submission_failed') from exc
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception('ACE generation submission failed')
        raise HTTPException(status_code=502, detail='generation_submission_failed') from exc


@router.get('', response_model=AceJobsResponse)
async def jobs() -> AceJobsResponse:
    return AceJobsResponse(jobs=ace_jobs.list_jobs())


@router.post('/adopt', response_model=AceJobResponse)
async def adopt(body: AceAdoptRequest) -> AceJobResponse:
    # This starts an asyncio task, so keep the endpoint on the event loop.
    return ace_jobs.adopt(body.task_id, body.params, body.title, body.voice_id, body.track_ids)


@router.post('/query', response_model=AceJobQueryResponse)
async def query(body: AceQueryRequest) -> AceJobQueryResponse:
    return AceJobQueryResponse(data=[ace_jobs.get_job(task_id) for task_id in body.task_id_list])


async def _cancel(task_id: str) -> AceJobResponse:
    try:
        return await ace_jobs.cancel(task_id)
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception('ACE cancellation failed')
        raise HTTPException(status_code=502, detail='generation_cancel_failed') from exc


@router.post('/cancel-all', response_model=AceJobsResponse)
async def cancel_all() -> AceJobsResponse:
    result = []
    for job in ace_jobs.list_jobs():
        if job.status in ('queued', 'running'):
            result.append(await _cancel(job.task_id))
    return AceJobsResponse(jobs=result)


@router.post('/{task_id}/cancel', response_model=AceJobResponse)
async def cancel(task_id: str) -> AceJobResponse:
    return await _cancel(task_id)


@router.post('/{task_id}/retry-save', response_model=AceJobResponse)
async def retry(task_id: str) -> AceJobResponse:
    return await ace_jobs.retry_save(task_id)


@router.delete('/{task_id}')
async def delete(task_id: str) -> dict[str, bool]:
    await ace_jobs.delete(task_id)
    return {'deleted': True}
