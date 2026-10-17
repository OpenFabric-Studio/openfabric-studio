"""Bounded local controls; the durable owner performs all audio work."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
import logging
import sqlite3
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import TypeAdapter, ValidationError
from starlette.responses import Response
from starlette.types import Message

from .. import music_analysis
from ..module_security import require_local_origin
from ..music_analysis_contracts import (AnalysisResult, MusicAnalysisJob, MusicAnalysisOverrides,
    MusicAnalysisRequest, MusicAnalysisStoragePreview, MusicSourceRef)
from ..video_media import VideoMediaError
from ..video_process import WorkerOutputError

logger = logging.getLogger(__name__)
_source: TypeAdapter[MusicSourceRef] = TypeAdapter(MusicSourceRef)


class MusicAnalysisRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()

        async def bounded(request: Request) -> Response:
            if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                require_local_origin(request)
            maximum = 2 * 1024**2 if request.method == 'PATCH' else 131072
            count = 0

            async def receive() -> Message:
                nonlocal count
                message = await request.receive()
                if message['type'] == 'http.request':
                    body: object = message.get('body', b'')
                    if isinstance(body, bytes):
                        count += len(body)
                    if count > maximum:
                        raise HTTPException(413, 'analysis_request_too_large')
                return message

            try:
                return await handler(Request(request.scope, receive))
            except RequestValidationError as error:
                raise HTTPException(422, 'analysis_request_invalid') from error
            except music_analysis.MusicAnalysisError as error:
                raise HTTPException(error.status, error.code) from error
            except (VideoMediaError, WorkerOutputError, TimeoutError) as error:
                raise HTTPException(409, 'analysis_unavailable') from error
            except (OSError, sqlite3.Error, ValueError, ValidationError) as error:
                logger.exception('Music analysis storage or validation failed')
                raise HTTPException(503, 'analysis_storage_unavailable') from error
        return bounded


router = APIRouter(prefix='/api/music/analysis', tags=['music-analysis'], route_class=MusicAnalysisRoute)


@router.get('', response_model=list[MusicAnalysisJob])
async def list_jobs(source: Annotated[str | None, Query(max_length=2048)] = None) -> list[MusicAnalysisJob]:
    parsed: MusicSourceRef | None = None
    if source is not None:
        try:
            if len(source.encode()) > 2048:
                raise ValueError('source filter too large')
            parsed = _source.validate_json(source)
        except (ValidationError, ValueError) as error:
            raise HTTPException(422, 'analysis_request_invalid') from error
    return music_analysis.list_jobs(parsed)


@router.post('/preview', response_model=MusicAnalysisStoragePreview)
async def preview(body: MusicAnalysisRequest) -> MusicAnalysisStoragePreview:
    return await music_analysis.preview(body)


@router.post('', response_model=MusicAnalysisJob, status_code=202)
async def start(body: MusicAnalysisRequest) -> MusicAnalysisJob:
    return await music_analysis.start(body)


@router.get('/{identifier}', response_model=MusicAnalysisJob)
async def get(identifier: str) -> MusicAnalysisJob:
    return music_analysis.get(identifier)


async def _empty(request: Request) -> None:
    if (await request.body()).strip() not in {b'', b'{}'}:
        raise HTTPException(422, 'analysis_request_invalid')


@router.post('/{identifier}/cancel', response_model=MusicAnalysisJob)
async def cancel(identifier: str, request: Request) -> MusicAnalysisJob:
    await _empty(request)
    return await music_analysis.cancel(identifier)


@router.post('/{identifier}/retry', response_model=MusicAnalysisJob)
async def retry(identifier: str, request: Request) -> MusicAnalysisJob:
    await _empty(request)
    return await music_analysis.retry(identifier)


@router.patch('/{identifier}/overrides', response_model=AnalysisResult)
async def put_overrides(identifier: str, body: MusicAnalysisOverrides) -> AnalysisResult:
    return music_analysis.put_overrides(identifier, body)
