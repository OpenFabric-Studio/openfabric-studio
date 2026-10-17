"""Bounded local replacement controls; public requests contain catalog IDs only."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
import logging
import sqlite3
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import TypeAdapter, ValidationError
from starlette.responses import FileResponse, Response
from starlette.datastructures import UploadFile
from starlette.types import Message
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import replacement_jobs as jobs
from .. import rvc_models
from ..job_lifecycle import run_owned_thread
from ..module_security import require_local_origin
from ..music_analysis_contracts import MusicSourceRef
from ..music_dsp_contracts import AlignmentResult
from ..replacement_contracts import (AlignmentEditRequest, ReplacementActionRequest, ReplacementCapabilities,
    ReplacementMixEditRequest, ReplacementPlanResponse, ReplacementPlansResponse, ReplacementRenderRequest,
    ReplacementRequest, ReplacementStoragePreview, ReplacementUpdateRequest,
    RvcModelImport, RvcModelsResponse, RvcRegisteredModel, ReplacementAssetImport, ReplacementAssetResponse)
from ..video_media import VideoMediaError
from ..video_process import WorkerOutputError

logger = logging.getLogger(__name__)
Identifier = Annotated[str, Path(pattern=r'^[0-9a-f]{32}$', min_length=32, max_length=32)]
_source: TypeAdapter[MusicSourceRef] = TypeAdapter(MusicSourceRef)


class ReplacementRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()
        async def bounded(request: Request) -> Response:
            if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                require_local_origin(request)
            maximum = (rvc_models.MAX_MODEL_BYTES + 65536 + 16384 if request.method == 'POST' and request.url.path.endswith(('/rvc-models', '/assets'))
                else 256 * 1024 if request.url.path.endswith('/alignment') else 65536)
            count = 0
            async def receive() -> Message:
                nonlocal count
                message = await request.receive()
                if message['type'] == 'http.request':
                    body: object = message.get('body', b'')
                    if isinstance(body, bytes):
                        count += len(body)
                    if count > maximum:
                        raise HTTPException(413, 'replacement_invalid_request')
                return message
            try:
                return await handler(Request(request.scope, receive))
            except RequestValidationError as error:
                raise HTTPException(422, 'replacement_invalid_request') from error
            except StarletteHTTPException as error:
                if error.status_code == 400:
                    raise HTTPException(422, 'replacement_invalid_request') from error
                raise
            except jobs.ReplacementError as error:
                if error.current_revision is not None:
                    raise HTTPException(error.status, {'code': error.code, 'current_revision': error.current_revision}) from error
                raise HTTPException(error.status, error.code) from error
            except rvc_models.RvcModelError as error:
                raise HTTPException(error.status, error.code) from error
            except (VideoMediaError, WorkerOutputError, TimeoutError) as error:
                raise HTTPException(409, 'replacement_engine_unavailable') from error
            except (OSError, sqlite3.Error, ValueError, ValidationError) as error:
                logger.exception('Replacement storage or validation failed')
                raise HTTPException(503, 'replacement_storage_failed') from error
        return bounded


router = APIRouter(prefix='/api/voices/replace', tags=['voice-replacement'], route_class=ReplacementRoute)


@router.get('/rvc-models', response_model=RvcModelsResponse)
async def rvc_registry() -> RvcModelsResponse:
    return await run_owned_thread(rvc_models.list_models)


@router.post('/rvc-models', response_model=RvcRegisteredModel, status_code=201)
async def import_rvc(request: Request) -> RvcRegisteredModel:
    async with request.form(max_files=2, max_fields=1, max_part_size=65536) as form:
        if len(form.multi_items()) != len(form) or set(form.keys()) - {'metadata', 'checkpoint', 'index'}:
            raise HTTPException(422, 'replacement_invalid_request')
        raw, checkpoint, index = form.get('metadata'), form.get('checkpoint'), form.get('index')
        if (not isinstance(raw, str) or len(raw.encode()) > 65536 or not isinstance(checkpoint, UploadFile)
                or index is not None and not isinstance(index, UploadFile)):
            raise HTTPException(422, 'replacement_invalid_request')
        try:
            metadata = RvcModelImport.model_validate_json(raw)
        except ValidationError as error:
            raise HTTPException(422, 'replacement_invalid_request') from error
        return await run_owned_thread(rvc_models.import_model, metadata, checkpoint.file,
            checkpoint_name=checkpoint.filename or '', index=index.file if isinstance(index, UploadFile) else None,
            index_name=index.filename if isinstance(index, UploadFile) else None)


@router.post('/assets', response_model=ReplacementAssetResponse, status_code=201)
async def import_asset(request: Request) -> ReplacementAssetResponse:
    async with request.form(max_files=2, max_fields=1, max_part_size=65536) as form:
        if len(form.multi_items()) != len(form) or set(form.keys()) != {'metadata', 'audio'}:
            raise HTTPException(422, 'replacement_invalid_request')
        raw, audio = form.get('metadata'), form.get('audio')
        if not isinstance(raw, str) or len(raw.encode()) > 65536 or not isinstance(audio, UploadFile):
            raise HTTPException(422, 'replacement_invalid_request')
        try:
            metadata = ReplacementAssetImport.model_validate_json(raw)
        except ValidationError as error:
            raise HTTPException(422, 'replacement_invalid_request') from error
        return await jobs.import_asset(metadata, audio.file, filename=audio.filename or '')


@router.delete('/rvc-models/{identifier}', status_code=204)
async def delete_rvc(identifier: Identifier) -> Response:
    await run_owned_thread(rvc_models.delete, identifier)
    return Response(status_code=204)


@router.get('/capabilities', response_model=ReplacementCapabilities)
async def capabilities() -> ReplacementCapabilities:
    return await jobs.capabilities()


@router.post('/storage-preview', response_model=ReplacementStoragePreview)
async def storage_preview(body: ReplacementRequest) -> ReplacementStoragePreview:
    return await jobs.storage_preview(body)


@router.post('/plans', response_model=ReplacementPlanResponse, status_code=201)
async def create(body: ReplacementRequest,
                 request_id: Annotated[str | None, Query(min_length=32, max_length=32, pattern=r'^[0-9a-f]{32}$')] = None) -> ReplacementPlanResponse:
    return await jobs.create(body, request_id=request_id)


@router.get('/plans', response_model=ReplacementPlansResponse)
async def list_plans(source: Annotated[str | None, Query(max_length=2048)] = None,
                     cursor: Annotated[str | None, Query(pattern=r'^[0-9a-f]{32}$', max_length=32)] = None,
                     limit: Annotated[int, Query(ge=1, le=100)] = 100) -> ReplacementPlansResponse:
    parsed: MusicSourceRef | None = None
    if source is not None:
        try:
            if len(source.encode()) > 2048:
                raise ValueError('source filter too large')
            parsed = _source.validate_json(source)
        except (ValueError, ValidationError) as error:
            raise HTTPException(422, 'replacement_invalid_request') from error
    return jobs.list_plans(source=parsed, cursor=cursor, limit=limit)


@router.get('/plans/{identifier}', response_model=ReplacementPlanResponse)
async def get(identifier: Identifier) -> ReplacementPlanResponse:
    return jobs.get(identifier)


@router.patch('/plans/{identifier}', response_model=ReplacementPlanResponse)
async def update(identifier: Identifier, body: ReplacementUpdateRequest) -> ReplacementPlanResponse:
    return await jobs.update(identifier, body)


@router.patch('/plans/{identifier}/mix', response_model=ReplacementPlanResponse)
async def edit_mix(identifier: Identifier, body: ReplacementMixEditRequest) -> ReplacementPlanResponse:
    return await jobs.edit_mix(identifier, body)


@router.get('/plans/{identifier}/alignment', response_model=AlignmentResult | None)
async def alignment(identifier: Identifier) -> AlignmentResult | None:
    return jobs.get(identifier).alignment


@router.patch('/plans/{identifier}/alignment', response_model=ReplacementPlanResponse)
async def edit_alignment(identifier: Identifier, body: AlignmentEditRequest) -> ReplacementPlanResponse:
    return await jobs.edit_alignment(identifier, body)


@router.post('/plans/{identifier}/prepare', response_model=ReplacementPlanResponse, status_code=202)
async def prepare(identifier: Identifier, body: ReplacementActionRequest) -> ReplacementPlanResponse:
    return await jobs.prepare(identifier, body)


@router.post('/plans/{identifier}/preview', response_model=ReplacementPlanResponse, status_code=202)
async def preview(identifier: Identifier, body: ReplacementRenderRequest) -> ReplacementPlanResponse:
    return await jobs.render(identifier, body, action='preview')


@router.post('/plans/{identifier}/render', response_model=ReplacementPlanResponse, status_code=202)
async def render(identifier: Identifier, body: ReplacementRenderRequest) -> ReplacementPlanResponse:
    return await jobs.render(identifier, body)


@router.post('/plans/{identifier}/remix', response_model=ReplacementPlanResponse, status_code=202)
async def remix(identifier: Identifier, body: ReplacementRenderRequest) -> ReplacementPlanResponse:
    return await jobs.render(identifier, body, action='remix')


@router.post('/plans/{identifier}/retry', response_model=ReplacementPlanResponse, status_code=202)
async def retry(identifier: Identifier, body: ReplacementActionRequest) -> ReplacementPlanResponse:
    return await jobs.retry(identifier, body)


@router.post('/plans/{identifier}/cancel', response_model=ReplacementPlanResponse)
async def cancel(identifier: Identifier, request: Request) -> ReplacementPlanResponse:
    if (await request.body()).strip() not in {b'', b'{}'}:
        raise HTTPException(422, 'replacement_invalid_request')
    return await jobs.cancel(identifier)


@router.delete('/plans/{identifier}', status_code=204)
async def delete(identifier: Identifier) -> Response:
    await jobs.delete(identifier)
    return Response(status_code=204)


@router.get('/plans/{identifier}/artifacts/{artifact_id}/audio')
async def artifact(identifier: Identifier, artifact_id: Identifier) -> FileResponse:
    path = await jobs.artifact_path(identifier, artifact_id)
    return FileResponse(path, media_type='audio/wav')
