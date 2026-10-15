"""Bounded, same-origin adapter import and contained safetensors export."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.responses import FileResponse, Response
from starlette.types import Message

from .. import music_lora
from ..job_lifecycle import run_owned_thread
from ..module_security import require_local_origin
from ..music_lora_contracts import MusicAdapter, MusicAdapters, MusicLoraCapabilities, MusicLoraError
from ..resource_admission import admission_lock, require_setup_idle

logger = logging.getLogger(__name__)
MAX_BODY_BYTES = music_lora.MAX_ADAPTER_BYTES + 65536


class MusicLoraRoute(APIRoute):
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
                    if count > MAX_BODY_BYTES:
                        raise HTTPException(413, 'music_lora_adapter_too_large')
                return message

            try:
                return await handler(Request(request.scope, receive))
            except (RequestValidationError, ValidationError) as error:
                raise HTTPException(422, 'music_lora_request_invalid') from error
            except MusicLoraError as error:
                raise HTTPException(error.status, error.code) from error
            except (OSError, ValueError, RecursionError) as error:
                logger.exception('Adapter storage unavailable')
                raise HTTPException(503, 'music_lora_storage_unavailable') from error
        return bounded


router = APIRouter(prefix='/api/music-lora', tags=['music-lora'], route_class=MusicLoraRoute)


@router.get('/capabilities', response_model=MusicLoraCapabilities)
async def capabilities() -> MusicLoraCapabilities:
    return music_lora.capabilities()


@router.get('/adapters', response_model=MusicAdapters)
async def adapters() -> MusicAdapters:
    return await run_owned_thread(music_lora.list_adapters)


@router.post('/adapters', response_model=MusicAdapter)
async def import_adapter(file: Annotated[UploadFile, File()],
    engine: Annotated[str, Form(min_length=1, max_length=20)],
    target: Annotated[str, Form(min_length=1, max_length=20)],
    name: Annotated[str, Form(min_length=1, max_length=120)]) -> MusicAdapter:
    try:
        async with admission_lock:
            require_setup_idle()
            return await run_owned_thread(music_lora.import_stream, file.file,
                filename=file.filename or '', name=name, engine=engine, target=target)
    finally:
        await file.close()


@router.get('/adapters/{identifier}/file')
async def export_adapter(identifier: str) -> FileResponse:
    path = await run_owned_thread(music_lora.export_path, identifier)
    return FileResponse(path, media_type='application/octet-stream', filename=f'{identifier}.safetensors')
