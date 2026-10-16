"""Same-origin, bounded requests for durable local caption analysis."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
import logging
from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.responses import Response
from starlette.types import Message
from .. import training_captions
from ..module_security import require_local_origin
from ..training_caption_contracts import TrainingCaptionJob, TrainingCaptionJobs, TrainingCaptionRequest
from ..training_caption_runtime import CaptionError

logger = logging.getLogger(__name__)


class CaptionRoute(APIRoute):
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
                    count += len(message['body'])
                    if count > 128 * 1024:
                        raise HTTPException(413, 'training_caption_request_too_large')
                return message
            try:
                return await handler(Request(request.scope, receive))
            except (RequestValidationError, ValidationError) as error:
                raise HTTPException(422, 'training_caption_request_invalid') from error
            except CaptionError as error:
                raise HTTPException(error.status, error.code) from error
            except (OSError, ValueError) as error:
                logger.exception('Training caption storage unavailable')
                raise HTTPException(503, 'training_caption_storage_unavailable') from error
        return bounded


router = APIRouter(prefix='/api/music-lora/captions', tags=['music-lora'], route_class=CaptionRoute)


@router.get('/jobs', response_model=TrainingCaptionJobs)
async def jobs() -> TrainingCaptionJobs:
    return training_captions.list_jobs()


@router.post('/jobs', response_model=TrainingCaptionJob)
async def submit(body: TrainingCaptionRequest) -> TrainingCaptionJob:
    return await training_captions.submit(body)


@router.get('/jobs/{identifier}', response_model=TrainingCaptionJob)
async def job(identifier: str) -> TrainingCaptionJob:
    return training_captions.get(identifier)


@router.post('/jobs/{identifier}/cancel', response_model=TrainingCaptionJob)
async def cancel(identifier: str) -> TrainingCaptionJob:
    return await training_captions.cancel(identifier)
