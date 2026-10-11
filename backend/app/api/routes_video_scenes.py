"""Fixed-owned scene media; only a backend native dialog can select local paths."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from starlette.responses import Response
from starlette.types import Message
from .. import video_scenes as scenes
from ..module_security import require_local_origin
from ..resource_admission import ResourceBusyError
from ..video_media import VideoMediaError
from ..video_scene_contracts import DeletedVideoSceneCutResponse, VideoSceneCut, VideoSceneCutRequest, VideoSceneCutsResponse, VideoScenePreview, VideoScenePreviewRequest, VideoSceneReadiness, VideoSceneSource, VideoSceneSourcesResponse


class SceneRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()
        async def guarded(request: Request) -> Response:
            if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                require_local_origin(request)
            limit = scenes.MAX_UPLOAD_BYTES+65536 if request.method == 'POST' and request.url.path.endswith('/scenes/sources') else 65536
            count = 0
            async def receive() -> Message:
                nonlocal count
                message = await request.receive()
                if message['type'] == 'http.request':
                    body: object = message.get('body', b'')
                    if isinstance(body, bytes):
                        count += len(body)
                    if count > limit:
                        raise HTTPException(413, 'upload_too_large')
                return message
            try:
                return await handler(Request(request.scope, receive))
            except RequestValidationError as error:
                raise HTTPException(422, 'scene_request_invalid') from error
            except ResourceBusyError as error:
                raise HTTPException(409, error.code) from error
            except (scenes.VideoSceneError, VideoMediaError) as error:
                status = 404 if error.code == 'not_found' else 503 if error.code in {'storage_unavailable', 'worker_unresolved'} else 409 if error.code in {'busy', 'cut_not_ready', 'source_changed', 'cut_changed'} else 400
                raise HTTPException(status, error.code) from error
            except (OSError, ValueError, TimeoutError) as error:
                raise HTTPException(503, 'storage_unavailable') from error
        return guarded


router = APIRouter(prefix='/api/videos/scenes', tags=['videos'], route_class=SceneRoute)


@router.get('/readiness', response_model=VideoSceneReadiness)
def readiness() -> VideoSceneReadiness:
    return scenes.readiness()


@router.get('/sources', response_model=VideoSceneSourcesResponse)
def list_sources() -> VideoSceneSourcesResponse:
    return scenes.list_sources()


@router.post('/sources', response_model=VideoSceneSource)
async def create(video: Annotated[UploadFile, File()]) -> VideoSceneSource:
    return await scenes.create(video)


@router.post('/sources/pick', response_model=VideoSceneSource)
async def pick() -> VideoSceneSource:
    return await scenes.pick()


@router.get('/sources/{source_id}', response_model=VideoSceneSource)
def get_source(source_id: str) -> VideoSceneSource:
    return scenes.get_source(source_id)


@router.post('/sources/{source_id}/preview', response_model=VideoScenePreview)
async def preview(source_id: str, body: VideoScenePreviewRequest) -> VideoScenePreview:
    return await scenes.preview(source_id, body)


@router.get('/sources/{source_id}/previews/{preview_id}')
def preview_media(source_id: str, preview_id: str) -> FileResponse:
    return FileResponse(scenes.preview_media(source_id, preview_id), media_type='video/mp4', headers={'Cache-Control': 'no-store'})


@router.get('/sources/{source_id}/cuts', response_model=VideoSceneCutsResponse)
def list_cuts(source_id: str) -> VideoSceneCutsResponse:
    return scenes.list_cuts(source_id)


@router.post('/sources/{source_id}/cuts', response_model=VideoSceneCut)
async def create_cut(source_id: str, body: VideoSceneCutRequest) -> VideoSceneCut:
    return await scenes.create_cut(source_id, body)


@router.get('/sources/{source_id}/cuts/{cut_id}', response_model=VideoSceneCut)
def get_cut(source_id: str, cut_id: str) -> VideoSceneCut:
    return scenes.get_cut(source_id, cut_id)


@router.post('/sources/{source_id}/cuts/{cut_id}/cancel', response_model=VideoSceneCut)
async def cancel(source_id: str, cut_id: str) -> VideoSceneCut:
    return await scenes.cancel(source_id, cut_id)


@router.delete('/sources/{source_id}/cuts/{cut_id}', response_model=DeletedVideoSceneCutResponse)
async def delete_cut(source_id: str, cut_id: str) -> DeletedVideoSceneCutResponse:
    return await scenes.delete_cut(source_id, cut_id)


@router.get('/sources/{source_id}/cuts/{cut_id}/media')
async def cut_media(source_id: str, cut_id: str) -> FileResponse:
    path, cut = await scenes.resolve_cut(source_id, cut_id)
    return FileResponse(path, media_type='video/mp4', filename=cut.name+'.mp4', headers={'Cache-Control': 'no-store'})
