"""Bounded same-origin API for backend-owned local music jobs."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.responses import Response
from starlette.types import Message

from .. import local_music
from ..local_music_contracts import LocalMusicEngines, LocalMusicError, LocalMusicJob, LocalMusicJobs, LocalMusicRequest
from ..music_lora_contracts import MusicLoraError
from ..module_security import require_local_origin

logger = logging.getLogger(__name__)


class LocalMusicRoute(APIRoute):
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
                        raise HTTPException(413, 'local_music_request_too_large')
                return message

            try:
                return await handler(Request(request.scope, receive))
            except RequestValidationError as error:
                raise HTTPException(422, 'local_music_request_invalid') from error
            except (LocalMusicError, MusicLoraError) as error:
                raise HTTPException(error.status, error.code) from error
            except (OSError, ValueError, sqlite3.Error) as error:
                logger.exception('Local music API storage failure')
                raise HTTPException(503, 'local_music_storage_unavailable') from error
        return bounded


router = APIRouter(prefix='/api/local-music', tags=['local-music'], route_class=LocalMusicRoute)


@router.get('/engines', response_model=LocalMusicEngines)
async def engines() -> LocalMusicEngines:
    return local_music.engine_status()


@router.get('/jobs', response_model=LocalMusicJobs)
async def jobs() -> LocalMusicJobs:
    return local_music.list_jobs()


@router.post('/jobs', response_model=LocalMusicJob)
async def submit(body: LocalMusicRequest) -> LocalMusicJob:
    return await local_music.submit(body)


@router.get('/jobs/{identifier}', response_model=LocalMusicJob)
async def job(identifier: str) -> LocalMusicJob:
    return local_music.get(identifier)


@router.post('/jobs/{identifier}/cancel', response_model=LocalMusicJob)
async def cancel(identifier: str) -> LocalMusicJob:
    return await local_music.cancel(identifier)
