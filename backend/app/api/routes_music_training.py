"""Bounded same-origin requests for backend-owned native adapter training."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
import logging
import sqlite3
from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.responses import FileResponse, Response
from starlette.types import Message
from .. import music_training
from ..job_lifecycle import run_owned_thread
from ..module_security import require_local_origin
from ..music_lora_contracts import MusicLoraError
from ..music_training_contracts import MusicLoraTrainingJob, MusicLoraTrainingJobs, MusicLoraTrainingRequest, MusicLoraTrainingStatuses, YueLoraPairSettings

logger = logging.getLogger(__name__)


class MusicTrainingRoute(APIRoute):
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
                        raise HTTPException(413, 'music_training_request_too_large')
                return message
            try:
                return await handler(Request(request.scope, receive))
            except (RequestValidationError, ValidationError) as error:
                raise HTTPException(422, 'music_training_request_invalid') from error
            except MusicLoraError as error:
                raise HTTPException(error.status, error.code) from error
            except (OSError, ValueError, sqlite3.Error) as error:
                logger.exception('Music adapter training storage failure')
                raise HTTPException(503, 'music_training_storage_unavailable') from error
        return bounded


router = APIRouter(prefix='/api/music-lora/training', tags=['music-lora'], route_class=MusicTrainingRoute)


@router.get('/status', response_model=MusicLoraTrainingStatuses)
async def status() -> MusicLoraTrainingStatuses:
    return MusicLoraTrainingStatuses(engines=[music_training.status('yue2')])


@router.get('/jobs', response_model=MusicLoraTrainingJobs)
async def jobs() -> MusicLoraTrainingJobs:
    return music_training.list_jobs()


@router.post('/jobs', response_model=MusicLoraTrainingJob)
async def submit(body: MusicLoraTrainingRequest) -> MusicLoraTrainingJob:
    return await music_training.submit(body)


@router.post('/packages', response_model=MusicLoraTrainingJob)
async def package(body: MusicLoraTrainingRequest) -> MusicLoraTrainingJob:
    return await music_training.submit(body, kind='nvidia-package')


@router.get('/jobs/{identifier}', response_model=MusicLoraTrainingJob)
async def job(identifier: str) -> MusicLoraTrainingJob:
    return music_training.get(identifier)


@router.post('/jobs/{identifier}/cancel', response_model=MusicLoraTrainingJob)
async def cancel(identifier: str) -> MusicLoraTrainingJob:
    return await music_training.cancel(identifier)


@router.post('/jobs/{identifier}/planner', response_model=MusicLoraTrainingJob)
async def planner(identifier: str, body: YueLoraPairSettings) -> MusicLoraTrainingJob:
    return await music_training.submit_planner(identifier, body)


@router.get('/jobs/{identifier}/package')
async def download_package(identifier: str) -> FileResponse:
    path = await run_owned_thread(music_training.package_path, identifier)
    return FileResponse(path, media_type='application/zip', filename=f'music-training-{identifier}.zip')
