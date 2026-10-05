"""Local Kokoro, Chatterbox, Wan 2.2 TI2V-5B, and RVC calls.

Song videos stay on LTX. These routes do not download weights.
"""
from __future__ import annotations

import re
from pathlib import Path
from collections.abc import Awaitable, Callable, Coroutine

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.routing import APIRoute
from starlette.responses import Response
from starlette.types import Message
from fastapi.responses import FileResponse


from ..optional_engine_contracts import (LocalEngineStatus, LocalEnginesStatus, LocalEngineResponse, LocalInputResponse, KokoroRequest, ChatterboxRequest, WanRequest, RvcRequest)
from ..module_security import require_local_origin
from ..optional_engines import (
    CHATTERBOX_LANGUAGES, KOKORO_RUNTIME, KOKORO_VOICES, OptionalEngineError, RVC_RUNTIME,
    WAN_RUNTIME, LocalRun, local_root, contained_output, convert_rvc, installed, narrate_kokoro, render_wan, speak_chatterbox,
    video_engine_preference,
)

class _BoundedRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()

        async def bounded(request: Request) -> Response:
            if request.method in {"GET", "HEAD", "OPTIONS"}:
                return await handler(request)
            require_local_origin(request)
            limit = _MAX_INPUT_BYTES + 1024 * 1024 if request.url.path.endswith("/inputs") else 64 * 1024
            declared = request.headers.get("content-length")
            if declared is not None:
                try:
                    size = int(declared)
                except ValueError as exc:
                    raise HTTPException(400, detail={"code": "input_refused", "detail": "Invalid request size."}) from exc
                if size < 0 or size > limit:
                    raise HTTPException(413, detail={"code": "input_refused", "detail": "That upload is too large."})
            count = 0

            async def receive() -> Message:
                nonlocal count
                message = await request.receive()
                body: object = message.get("body", b"")
                if isinstance(body, bytes):
                    count += len(body)
                if count > limit:
                    raise HTTPException(413, detail={"code": "input_refused", "detail": "That upload is too large."})
                return message

            return await handler(Request(request.scope, receive))
        return bounded


router = APIRouter(prefix='/api/local-engines', tags=['local engines'], route_class=_BoundedRoute)


_MEDIA_ENGINES = {'kokoro', 'chatterbox', 'wan22', 'rvc'}
_MEDIA_TYPES = {'.wav': 'audio/wav', '.mp4': 'video/mp4'}
_INPUT_SUFFIXES = {'.wav', '.flac', '.mp3', '.png', '.jpg', '.jpeg', '.webp', '.pth'}
_MAX_INPUT_BYTES = 200 * 1024 * 1024


def _media_url(path: str | None) -> str | None:
    if not path:
        return None
    file = Path(path)
    if file.parent.name not in _MEDIA_ENGINES or file.suffix.lower() not in _MEDIA_TYPES:
        return None
    if not re.fullmatch(r'[0-9a-f]{32}', file.stem):
        return None
    return f'/api/local-engines/{file.parent.name}/media/{file.stem}'


async def _call(action: Callable[[], Awaitable[LocalRun]]) -> LocalEngineResponse:
    try:
        result = await action()
    except OptionalEngineError as exc:
        status = 400 if exc.code.endswith('_refused') or exc.code in ('text_required', 'audio_missing', 'image_missing', 'kokoro_voice_refused') else 409 if exc.code in ('engine_not_installed', 'weights_missing') else 409 if exc.code in ('engine_busy', 'worker_identity_unverified') else 500
        raise HTTPException(status, detail={'code': exc.code, 'detail': exc.detail}) from exc
    output = str(result.output_path) if result.output_path else None
    return LocalEngineResponse(status=result.status, detail=result.detail,
                               output_path=output, media_url=_media_url(output),
                               runtime=result.runtime)


@router.get('', response_model=LocalEnginesStatus)
def local_engine_status() -> LocalEnginesStatus:
    try:
        preference = video_engine_preference()
    except OptionalEngineError as exc:
        raise HTTPException(400, detail={'code': exc.code, 'detail': exc.detail}) from exc
    return LocalEnginesStatus(
        video_preference=preference,
        note='Song videos and the default video engine stay on LTX. Set OPENFABRIC_VIDEO_ENGINE=wan22 only to record a preference. Call POST /api/local-engines/wan with engine wan22 to render TI2V-5B.',
        engines=[
            LocalEngineStatus(id='kokoro', installed=installed('kokoro'), setup_script='setup_kokoro.sh', runtime=KOKORO_RUNTIME, voices=[voice for names in KOKORO_VOICES.values() for voice in names]),
            LocalEngineStatus(id='chatterbox', installed=installed('chatterbox'), setup_script='setup_chatterbox.sh', runtime='Original and multilingual Chatterbox on cuda, cpu, or mps. Turbo is refused. GPT-SoVITS is unchanged.', languages=list(CHATTERBOX_LANGUAGES)),
            LocalEngineStatus(id='wan22', installed=installed('wan22'), setup_script='setup_wan22.sh', runtime=WAN_RUNTIME),
            LocalEngineStatus(id='rvc', installed=installed('rvc'), setup_script='setup_rvc.sh', runtime=RVC_RUNTIME),
        ],
    )


@router.post('/kokoro', response_model=LocalEngineResponse)
async def create_kokoro(body: KokoroRequest, request: Request) -> LocalEngineResponse:
    require_local_origin(request)
    if body.voice not in KOKORO_VOICES[body.lang]:
        raise HTTPException(400, detail={'code': 'kokoro_voice_refused', 'detail': 'Kokoro only speaks its preset voices. It does not clone a person.'})
    return await _call(lambda: narrate_kokoro(body.text, voice=body.voice, lang=body.lang))


@router.post('/chatterbox', response_model=LocalEngineResponse)
async def create_chatterbox(body: ChatterboxRequest, request: Request) -> LocalEngineResponse:
    require_local_origin(request)
    if body.model == 'multilingual' and body.language_id not in CHATTERBOX_LANGUAGES:
        raise HTTPException(400, detail={'code': 'chatterbox_language_refused', 'detail': 'That language is not in the Chatterbox multilingual list.'})
    return await _call(lambda: speak_chatterbox(body.text, model=body.model, audio_prompt_path=body.audio_prompt_path, language_id=body.language_id))


@router.post('/wan', response_model=LocalEngineResponse)
async def create_wan(body: WanRequest, request: Request) -> LocalEngineResponse:
    require_local_origin(request)
    return await _call(lambda: render_wan(body.prompt, variant=body.variant, image_path=body.image_path, width=body.width, height=body.height, num_frames=body.num_frames))


@router.post('/rvc', response_model=LocalEngineResponse)
async def create_rvc(body: RvcRequest, request: Request) -> LocalEngineResponse:
    require_local_origin(request)
    return await _call(lambda: convert_rvc(body.model_path, body.input_path))


@router.post('/inputs', response_model=LocalInputResponse)
def upload_local_input(file: UploadFile, request: Request) -> LocalInputResponse:
    """Save a clip, still, or trained .pth the browser picked. Weights are not downloaded."""
    require_local_origin(request)
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in _INPUT_SUFFIXES:
        raise HTTPException(400, detail={'code': 'input_refused', 'detail': 'Choose a wav, flac, mp3, png, jpg, webp, or pth file.'})
    from ..optional_engines import stage_input_path
    try:
        dest = stage_input_path(suffix)
    except OptionalEngineError as exc:
        raise HTTPException(409, detail={"code": exc.code, "detail": exc.detail}) from exc
    total = 0
    try:
        with dest.open('wb') as handle:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_INPUT_BYTES:
                    raise HTTPException(400, detail={'code': 'input_refused', 'detail': 'That file is larger than 200 MB.'})
                handle.write(chunk)
        if total < 16:
            raise HTTPException(400, detail={'code': 'input_refused', 'detail': 'That file is empty.'})
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return LocalInputResponse(path=str(dest))


@router.get('/{engine}/media/{file_id}')
def local_engine_media(engine: str, file_id: str) -> FileResponse:
    if engine not in _MEDIA_ENGINES or not re.fullmatch(r'[0-9a-f]{32}', file_id):
        raise HTTPException(404, detail='not_found')
    from ..config import DATA_DIR
    try:
        root = local_root(engine)
    except OptionalEngineError as exc:
        raise HTTPException(404, detail='not_found') from exc
    for suffix, media_type in _MEDIA_TYPES.items():
        try:
            path = contained_output(root / f'{file_id}{suffix}')
        except OptionalEngineError as exc:
            raise HTTPException(404, detail='not_found') from exc
        if path.is_file():
            return FileResponse(path, media_type=media_type, filename=path.name)
    raise HTTPException(404, detail='not_found')
