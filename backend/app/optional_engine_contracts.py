"""Source-of-truth public contracts for optional local-engine calls."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field
from .contracts import Contract

class LocalEngineStatus(Contract):
    id: Literal['kokoro', 'chatterbox', 'wan22', 'rvc']
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


OPTIONAL_ENGINE_CLIENT_MODELS: list[type[BaseModel]] = [
    LocalEngineStatus, LocalEnginesStatus, LocalEngineResponse, LocalInputResponse,
    KokoroRequest, ChatterboxRequest, WanRequest, RvcRequest,
]
