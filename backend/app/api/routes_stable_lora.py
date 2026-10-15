"""Bounded same-origin requests for backend-owned native adapter training."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
import logging
import sqlite3
from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.responses import Response
from starlette.types import Message
from .. import stable_lora
from ..module_security import require_local_origin
from ..music_lora_contracts import MusicLoraError
from ..stable_lora_contracts import StableLoraTrainingJob, StableLoraTrainingJobs, StableLoraTrainingRequest, StableLoraTrainingStatus

logger = logging.getLogger(__name__)


class StableTrainingRoute(APIRoute):
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
                    if count > 2 * 1024 * 1024:
                        raise HTTPException(413, 'stable_lora_request_too_large')
                return message
            try:
                return await handler(Request(request.scope, receive))
            except (RequestValidationError, ValidationError) as error:
                raise HTTPException(422, 'stable_lora_request_invalid') from error
            except MusicLoraError as error:
                raise HTTPException(error.status, error.code) from error
            except (OSError, ValueError, sqlite3.Error) as error:
                logger.exception('Stable training storage failure')
                raise HTTPException(503, 'stable_lora_storage_unavailable') from error
        return bounded


router = APIRouter(prefix='/api/music-lora/stable-training', tags=['music-lora'], route_class=StableTrainingRoute)


@router.get('/status', response_model=StableLoraTrainingStatus)
async def status() -> StableLoraTrainingStatus:
    return stable_lora.status()


@router.get('/jobs', response_model=StableLoraTrainingJobs)
async def jobs() -> StableLoraTrainingJobs:
    return stable_lora.list_jobs()


@router.post('/jobs', response_model=StableLoraTrainingJob)
async def submit(body: StableLoraTrainingRequest) -> StableLoraTrainingJob:
    return await stable_lora.submit(body)


@router.get('/jobs/{identifier}', response_model=StableLoraTrainingJob)
async def job(identifier: str) -> StableLoraTrainingJob:
    return stable_lora.get(identifier)


@router.post('/jobs/{identifier}/cancel', response_model=StableLoraTrainingJob)
async def cancel(identifier: str) -> StableLoraTrainingJob:
    return await stable_lora.cancel(identifier)
