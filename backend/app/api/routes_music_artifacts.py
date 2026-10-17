"""Reviewed local artifact cleanup; HTTP never accepts a filesystem path."""
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

from .. import music_artifacts
from ..module_security import require_local_origin
from ..music_artifact_contracts import (ConfirmMusicArtifactCleanup, MusicArtifactCleanupPlan,
    MusicArtifactCleanupReceipt, MusicArtifactCleanupRequest, MusicArtifactInventory)

logger = logging.getLogger(__name__)


class MusicArtifactRoute(APIRoute):
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
                        raise HTTPException(413, 'music_artifact_limit')
                return message

            try:
                return await handler(Request(request.scope, receive))
            except RequestValidationError as error:
                raise HTTPException(422, 'music_artifact_invalid') from error
            except music_artifacts.MusicArtifactError as error:
                raise HTTPException(error.status, error.code) from error
            except (OSError, sqlite3.Error, ValueError, ValidationError) as error:
                logger.exception('Music artifact storage operation failed')
                raise HTTPException(503, 'music_artifact_storage_unavailable') from error
        return bounded


router = APIRouter(prefix='/api/music/artifacts', tags=['music-artifacts'], route_class=MusicArtifactRoute)


@router.get('', response_model=MusicArtifactInventory)
async def inventory() -> MusicArtifactInventory:
    return await music_artifacts.inventory()


@router.post('/cleanup-plans', response_model=MusicArtifactCleanupPlan, status_code=201)
async def plan(body: MusicArtifactCleanupRequest) -> MusicArtifactCleanupPlan:
    return await music_artifacts.create_plan(body)


@router.get('/cleanup-plans/{identifier}', response_model=MusicArtifactCleanupPlan)
async def get_plan(identifier: str) -> MusicArtifactCleanupPlan:
    return music_artifacts.get_plan(identifier)


@router.get('/cleanup-plans/{identifier}/receipt', response_model=MusicArtifactCleanupReceipt)
async def get_receipt(identifier: str) -> MusicArtifactCleanupReceipt:
    return music_artifacts.get_receipt(identifier)


@router.post('/cleanup-plans/{identifier}/confirm', response_model=MusicArtifactCleanupReceipt)
async def confirm(identifier: str, body: ConfirmMusicArtifactCleanup) -> MusicArtifactCleanupReceipt:
    return await music_artifacts.confirm_plan(identifier, body)
