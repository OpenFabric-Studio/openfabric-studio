"""Bounded same-origin access to local model-use acknowledgements."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.responses import Response
from starlette.types import Message

from .. import music_terms
from ..module_security import require_local_origin
from ..music_terms_contracts import (
    MusicTermsAcceptRequest, MusicTermsEngine, MusicTermsOperation,
    MusicTermsSettingsRequest, MusicTermsSettingsResponse, MusicTermsSnapshot,
)

logger = logging.getLogger(__name__)


class MusicTermsRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()

        async def bounded(request: Request) -> Response:
            if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                require_local_origin(request)
            received = 0

            async def receive() -> Message:
                nonlocal received
                message = await request.receive()
                if message['type'] == 'http.request':
                    received += len(message['body'])
                    if received > 65536:
                        raise HTTPException(413, 'music_terms_request_too_large')
                return message

            try:
                return await handler(Request(request.scope, receive))
            except RequestValidationError as error:
                raise HTTPException(422, 'music_terms_request_invalid') from error
            except music_terms.MusicTermsError as error:
                raise HTTPException(error.status, error.code) from error
            except (OSError, ValueError, sqlite3.Error) as error:
                logger.exception('Local music terms storage unavailable')
                raise HTTPException(503, 'music_terms_unavailable') from error
        return bounded


router = APIRouter(prefix='/api/music-terms', tags=['music terms'], route_class=MusicTermsRoute)


def require_usage(engine: MusicTermsEngine, operation: MusicTermsOperation) -> None:
    try:
        music_terms.require(engine, operation)
    except music_terms.MusicTermsError as error:
        raise HTTPException(error.status, error.code) from error
    except (OSError, ValueError, sqlite3.Error) as error:
        logger.exception('Local music terms admission unavailable')
        raise HTTPException(503, 'music_terms_unavailable') from error


@router.get('', response_model=MusicTermsSnapshot)
async def snapshot(engine: MusicTermsEngine, operation: MusicTermsOperation) -> MusicTermsSnapshot:
    return music_terms.snapshot(engine, operation)


@router.post('/accept', response_model=MusicTermsSnapshot)
async def accept(body: MusicTermsAcceptRequest) -> MusicTermsSnapshot:
    return music_terms.accept(body)


@router.patch('/settings', response_model=MusicTermsSettingsResponse)
async def settings(body: MusicTermsSettingsRequest) -> MusicTermsSettingsResponse:
    return music_terms.update_settings(body)
