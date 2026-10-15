"""Bounded local music requests and durable job responses."""
from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import Contract, SavedTrack
from .music_lora_contracts import MusicAdapter, MusicAdapterSelection

LocalMusicEngineId = Literal['heartmula', 'stable_audio3']


class LocalMusicError(Exception):
    """Stable user-facing code; technical details stay in backend logs."""

    def __init__(self, code: str, status: int = 409) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


class LocalMusicRequest(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    engine: LocalMusicEngineId
    title: str = Field(default='', max_length=500)
    prompt: str = Field(min_length=1, max_length=4000)
    lyrics: str = Field(default='', max_length=20000)
    duration_seconds: int = Field(ge=5, le=380, strict=True)
    seed: int = Field(default=0, ge=0, le=2147483647, strict=True)
    variant: Literal['small-music', 'medium'] = 'small-music'
    adapter: MusicAdapterSelection | None = None

    @model_validator(mode='after')
    def validate_engine_inputs(self) -> Self:
        if not self.prompt.strip():
            raise ValueError('music_prompt_required')
        if self.engine == 'heartmula' and self.adapter is not None:
            raise ValueError('heartmula_lora_unsupported')
        if self.engine == 'heartmula' and self.duration_seconds > 240:
            raise ValueError('heartmula_duration_invalid')
        if self.engine == 'heartmula' and self.variant != 'small-music':
            raise ValueError('heartmula_variant_invalid')
        if self.engine == 'stable_audio3':
            if self.lyrics.strip():
                raise ValueError('stable_audio3_instrumental_only')
            if self.variant == 'small-music' and self.duration_seconds > 120:
                raise ValueError('stable_audio3_duration_invalid')
        return self


class LocalMusicEngineStatus(Contract):
    id: LocalMusicEngineId
    installed: bool
    weights_ready: bool
    supported: bool
    runtime: str
    detail: str


class LocalMusicEngines(Contract):
    engines: list[LocalMusicEngineStatus]


class LocalMusicJob(Contract):
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    status: Literal['queued', 'running', 'done', 'failed', 'cancelled', 'interrupted']
    created_at: str
    request: LocalMusicRequest
    track: SavedTrack | None = None
    adapter: MusicAdapter | None = None
    error_code: str | None = Field(default=None, max_length=80)


class LocalMusicJobs(Contract):
    jobs: list[LocalMusicJob] = Field(max_length=1000)
    history_incomplete: bool = False


LOCAL_MUSIC_CLIENT_MODELS: list[type[BaseModel]] = [LocalMusicRequest, LocalMusicEngineStatus, LocalMusicEngines, LocalMusicJob, LocalMusicJobs]
