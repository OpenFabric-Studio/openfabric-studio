"""App-owned character replacement; media stays beneath the videos library."""
from __future__ import annotations
from collections.abc import Callable, Coroutine
from typing import Annotated, NoReturn

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from starlette.types import Message

from .. import character_swaps as swaps
from ..character_swap_contracts import CharacterSwapJob, CharacterSwapReadiness, CharacterSwapRevisionRequest, CharacterSwapsResponse, SubmitCharacterSwapRequest
from ..resource_admission import ResourceBusyError
from ..video_media import VideoMediaError
from ..module_security import require_local_origin
from ..video_scenes import VideoSceneError


class CharacterSwapRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        original = super().get_route_handler()
        async def handle(request: Request) -> Response:
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                require_local_origin(request)
            limit = 65536
            if request.method == 'POST' and request.url.path == '/api/videos/character-swaps/from-scene':
                limit += swaps.MAX_IMAGE_BYTES
            elif request.method == 'POST' and request.url.path == '/api/videos/character-swaps':
                limit += swaps.MAX_VIDEO_BYTES + swaps.MAX_IMAGE_BYTES
            count = 0
            async def receive() -> Message:
                nonlocal count
                message = await request.receive()
                if message['type'] == 'http.request':
                    body: object = message.get('body', b'')
                    if isinstance(body, bytes):
                        count += len(body)
                    if count > limit:
                        raise HTTPException(413, detail='upload_too_large')
                return message
            return await original(Request(request.scope, receive))
        return handle


router = APIRouter(prefix='/api/videos/character-swaps', tags=['videos'], route_class=CharacterSwapRoute)


def _raise(error: swaps.CharacterSwapError | VideoMediaError | VideoSceneError | ResourceBusyError | OSError | ValueError) -> NoReturn:
    code = error.code if isinstance(error, (swaps.CharacterSwapError, VideoMediaError, VideoSceneError, ResourceBusyError)) else 'storage_unavailable'
    status = 404 if code == 'not_found' else 409 if code in {'busy', 'revision_conflict', 'already_submitted', 'module_setup_busy', 'cut_not_ready', 'cut_changed', 'source_changed'} else 400
    raise HTTPException(status_code=status, detail=code) from error


@router.get('/readiness', response_model=CharacterSwapReadiness)
def readiness() -> CharacterSwapReadiness:
    try:
        return swaps.readiness()
    except (swaps.CharacterSwapError, OSError, ValueError) as error:
        _raise(error)


@router.get('', response_model=CharacterSwapsResponse)
def list_jobs() -> CharacterSwapsResponse:
    try:
        return swaps.list_jobs()
    except (swaps.CharacterSwapError, OSError, ValueError) as error:
        _raise(error)


@router.post('', response_model=CharacterSwapJob)
async def create(video: Annotated[UploadFile, File()], image: Annotated[UploadFile, File()],
                 name: Annotated[str, Form(max_length=120)] = 'Character swap', start_sec: Annotated[float, Form(ge=0, le=21600)] = 0,
                 seconds: Annotated[int, Form(ge=2, le=8)] = 2, seed: Annotated[int, Form(ge=0, le=2147483647)] = 42) -> CharacterSwapJob:
    try:
        return await swaps.create(video, image, name=name, start_sec=start_sec, seconds=seconds, seed=seed)
    except (swaps.CharacterSwapError, VideoMediaError, ResourceBusyError, OSError, ValueError) as error:
        _raise(error)


@router.post('/from-scene', response_model=CharacterSwapJob)
async def create_from_scene(scene_id: Annotated[str, Form(pattern=r'^[0-9a-f]{32}$')],
                            cut_id: Annotated[str, Form(pattern=r'^[0-9a-f]{32}$')],
                            image: Annotated[UploadFile, File()],
                            name: Annotated[str, Form(max_length=120)] = 'Character swap',
                            start_sec: Annotated[float, Form(ge=0, le=21600)] = 0,
                            seconds: Annotated[int, Form(ge=2, le=8)] = 2,
                            seed: Annotated[int, Form(ge=0, le=2147483647)] = 42) -> CharacterSwapJob:
    try:
        return await swaps.create_from_scene(scene_id, cut_id, image, name=name,
                                              start_sec=start_sec, seconds=seconds, seed=seed)
    except (swaps.CharacterSwapError, VideoSceneError, VideoMediaError, ResourceBusyError, OSError, ValueError) as error:
        _raise(error)


@router.get('/{identifier}', response_model=CharacterSwapJob)
def get(identifier: str) -> CharacterSwapJob:
    try:
        return swaps.get(identifier)
    except (swaps.CharacterSwapError, OSError, ValueError) as error:
        _raise(error)


@router.post('/{identifier}/quote', response_model=CharacterSwapJob)
async def quote(identifier: str, body: CharacterSwapRevisionRequest) -> CharacterSwapJob:
    try:
        return await swaps.quote(identifier, body)
    except (swaps.CharacterSwapError, ResourceBusyError, OSError, ValueError) as error:
        _raise(error)


@router.post('/{identifier}/submit', response_model=CharacterSwapJob)
async def submit(identifier: str, body: SubmitCharacterSwapRequest) -> CharacterSwapJob:
    try:
        return await swaps.submit(identifier, body)
    except (swaps.CharacterSwapError, ResourceBusyError, OSError, ValueError) as error:
        _raise(error)


@router.post('/{identifier}/resume', response_model=CharacterSwapJob)
async def resume(identifier: str, body: CharacterSwapRevisionRequest) -> CharacterSwapJob:
    try:
        return await swaps.resume(identifier, body)
    except (swaps.CharacterSwapError, ResourceBusyError, OSError, ValueError) as error:
        _raise(error)


@router.post('/{identifier}/stop', response_model=CharacterSwapJob)
async def stop(identifier: str, body: CharacterSwapRevisionRequest) -> CharacterSwapJob:
    try:
        return await swaps.stop(identifier, body)
    except (swaps.CharacterSwapError, OSError, ValueError) as error:
        _raise(error)


@router.get('/{identifier}/{kind}')
def media(identifier: str, kind: str) -> FileResponse:
    names = {'source': 'source.video', 'clip': 'selected.mp4', 'image': 'reference.png', 'output': 'output.mp4', 'frame-original': 'source-frame.png', 'frame-edited': 'edited-frame.png'}
    try:
        name = names.get(kind)
        if name is None:
            raise swaps.CharacterSwapError('not_found')
        job = swaps.get(identifier)
        if kind == 'output' and job.status != 'ready':
            raise swaps.CharacterSwapError('not_found')
        if kind == 'frame-edited' and not job.edited_frame_url:
            raise swaps.CharacterSwapError('not_found')
        path = swaps.artifact(identifier, name)
        if not path.is_file():
            raise swaps.CharacterSwapError('not_found')
        return FileResponse(path, filename=job.source_name if kind == 'source' else name,
                            headers={'Cache-Control': 'no-store'})
    except (swaps.CharacterSwapError, OSError, ValueError) as error:
        _raise(error)
