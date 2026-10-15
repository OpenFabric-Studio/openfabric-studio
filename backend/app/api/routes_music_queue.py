"""Bounded local controls for the durable music queue."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.responses import Response
from starlette.types import Message

from .. import music_queue, music_queue_plan
from ..module_security import require_local_origin
from ..music_lora_contracts import MusicLoraError
from ..music_queue_contracts import (
    MusicQueueCancelRequest, MusicQueueCatalog, MusicQueueControlRequest,
    MusicQueueEnqueueRequest, MusicQueueError, MusicQueuePreview,
    MusicQueuePreviewRequest, MusicQueueReorderRequest, MusicQueueRetryRequest,
    MusicQueueSnapshot,
)
from ..resource_admission import ResourceBusyError

logger = logging.getLogger(__name__)


class MusicQueueRoute(APIRoute):
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
                    body: object = message.get('body', b'')
                    if isinstance(body, bytes):
                        count += len(body)
                    if count > 131072:
                        raise HTTPException(413, 'music_queue_request_too_large')
                return message

            try:
                return await handler(Request(request.scope, receive))
            except RequestValidationError as error:
                raise HTTPException(422, 'music_queue_request_invalid') from error
            except (MusicQueueError, MusicLoraError) as error:
                raise HTTPException(error.status, error.code) from error
            except ResourceBusyError as error:
                raise HTTPException(409, error.code) from error
            except (OSError, ValueError, sqlite3.Error) as error:
                logger.exception('Music queue storage or validation failed')
                raise HTTPException(503, 'music_queue_storage_unavailable') from error
        return bounded


router = APIRouter(prefix='/api/music-queue', tags=['music-queue'], route_class=MusicQueueRoute)


@router.get('', response_model=MusicQueueSnapshot)
async def snapshot() -> MusicQueueSnapshot:
    return music_queue.snapshot()


@router.get('/catalog', response_model=MusicQueueCatalog)
async def catalog() -> MusicQueueCatalog:
    return await music_queue_plan.catalog()


@router.post('/preview', response_model=MusicQueuePreview)
async def preview(body: MusicQueuePreviewRequest) -> MusicQueuePreview:
    return await music_queue_plan.preview(body)


@router.post('/batches', response_model=MusicQueueSnapshot)
async def enqueue(body: MusicQueueEnqueueRequest) -> MusicQueueSnapshot:
    return await music_queue.enqueue(body)


@router.post('/control', response_model=MusicQueueSnapshot)
async def control(body: MusicQueueControlRequest) -> MusicQueueSnapshot:
    return await music_queue.control(body)


@router.post('/reorder', response_model=MusicQueueSnapshot)
async def reorder(body: MusicQueueReorderRequest) -> MusicQueueSnapshot:
    return music_queue.reorder(body)


@router.post('/retry', response_model=MusicQueueSnapshot)
async def retry(body: MusicQueueRetryRequest) -> MusicQueueSnapshot:
    return await music_queue.retry(body)


@router.post('/batches/{identifier}/cancel', response_model=MusicQueueSnapshot)
async def cancel(identifier: str, body: MusicQueueCancelRequest) -> MusicQueueSnapshot:
    if identifier != body.batch_id:
        raise MusicQueueError('music_queue_batch_mismatch', 422)
    return await music_queue.cancel_batch(body)
