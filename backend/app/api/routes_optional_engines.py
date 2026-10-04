"""Local Kokoro, Chatterbox, Wan 2.2 TI2V-5B, and RVC calls.

Song videos stay on LTX. These routes do not download weights.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import Field

from ..contracts import Contract
from ..module_security import require_local_origin
from ..optional_engines import (
    CHATTERBOX_LANGUAGES, KOKORO_RUNTIME, KOKORO_VOICES, OptionalEngineError, RVC_RUNTIME,
    WAN_RUNTIME, convert_rvc, installed, narrate_kokoro, render_wan, speak_chatterbox,
    video_engine_preference,
)

router = APIRouter(prefix='/api/local-engines', tags=['local engines'])


class LocalEngineStatus(Contract):
    id: str
    installed: bool
    setup_script: str
    runtime: str
    voices: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)


class LocalEnginesStatus(Contract):
    video_engine: Literal['ltx'] = 'ltx'
    video_preference: str
    note: str
    engines: list[LocalEngineStatus]


class LocalEngineResponse(Contract):
    status: str
    detail: str
    output_path: str | None = None
    media_url: str | None = None
    runtime: str = ''


class LocalInputResponse(Contract):
    path: str


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


class KokoroRequest(Contract):
    text: str = Field(min_length=1, max_length=4000)
    voice: str = 'af_heart'
    lang: Literal['a', 'b'] = 'a'


class ChatterboxRequest(Contract):
    text: str = Field(min_length=1, max_length=4000)
    model: Literal['original', 'multilingual'] = 'original'
    audio_prompt_path: str | None = None
    language_id: str = 'en'


class WanRequest(Contract):
    engine: Literal['wan22']
    prompt: str = Field(min_length=1, max_length=2000)
    variant: Literal['ti2v-5b'] = 'ti2v-5b'
    image_path: str | None = None
    width: int = Field(default=832, ge=256, le=1280)
    height: int = Field(default=480, ge=256, le=1280)
    num_frames: int = Field(default=17, ge=5, le=81)


class RvcRequest(Contract):
    model_path: str = Field(min_length=1, max_length=1000)
    input_path: str = Field(min_length=1, max_length=1000)


def _call(action):
    try:
        result = action()
    except OptionalEngineError as exc:
        status = 400 if exc.code.endswith('_refused') or exc.code in ('text_required', 'audio_missing', 'image_missing', 'kokoro_voice_refused') else 409 if exc.code in ('engine_not_installed', 'weights_missing') else 500
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
def create_kokoro(body: KokoroRequest, request: Request) -> LocalEngineResponse:
    require_local_origin(request)
    if body.voice not in KOKORO_VOICES[body.lang]:
        raise HTTPException(400, detail={'code': 'kokoro_voice_refused', 'detail': 'Kokoro only speaks its preset voices. It does not clone a person.'})
    return _call(lambda: narrate_kokoro(body.text, voice=body.voice, lang=body.lang))


@router.post('/chatterbox', response_model=LocalEngineResponse)
def create_chatterbox(body: ChatterboxRequest, request: Request) -> LocalEngineResponse:
    require_local_origin(request)
    if body.model == 'multilingual' and body.language_id not in CHATTERBOX_LANGUAGES:
        raise HTTPException(400, detail={'code': 'chatterbox_language_refused', 'detail': 'That language is not in the Chatterbox multilingual list.'})
    return _call(lambda: speak_chatterbox(body.text, model=body.model, audio_prompt_path=body.audio_prompt_path, language_id=body.language_id))


@router.post('/wan', response_model=LocalEngineResponse)
def create_wan(body: WanRequest, request: Request) -> LocalEngineResponse:
    require_local_origin(request)
    return _call(lambda: render_wan(body.prompt, variant=body.variant, image_path=body.image_path, width=body.width, height=body.height, num_frames=body.num_frames))


@router.post('/rvc', response_model=LocalEngineResponse)
def create_rvc(body: RvcRequest, request: Request) -> LocalEngineResponse:
    require_local_origin(request)
    return _call(lambda: convert_rvc(body.model_path, body.input_path))


@router.post('/inputs', response_model=LocalInputResponse)
def upload_local_input(file: UploadFile, request: Request) -> LocalInputResponse:
    """Save a clip, still, or trained .pth the browser picked. Weights are not downloaded."""
    require_local_origin(request)
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in _INPUT_SUFFIXES:
        raise HTTPException(400, detail={'code': 'input_refused', 'detail': 'Choose a wav, flac, mp3, png, jpg, webp, or pth file.'})
    from ..optional_engines import stage_input_path
    dest = stage_input_path(suffix)
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
    root = (DATA_DIR / 'outputs' / 'local-engines' / engine).resolve()
    for suffix, media_type in _MEDIA_TYPES.items():
        path = (root / f'{file_id}{suffix}').resolve()
        if path.parent != root:
            raise HTTPException(404, detail='not_found')
        if path.is_file():
            return FileResponse(path, media_type=media_type, filename=path.name)
    raise HTTPException(404, detail='not_found')
