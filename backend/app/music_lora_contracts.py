"""Native adapter identities and honest capabilities for the selected runtimes."""
from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import Contract

AdapterEngine = Literal['stable_audio3', 'yue2']
AdapterTarget = Literal['small-music', 'medium', 'ar', 'nar']


class MusicLoraError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


class MusicAdapterSelection(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    strength: float = Field(default=1, ge=0, le=2)


class MusicAdapter(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    engine: AdapterEngine
    target: AdapterTarget
    name: str = Field(min_length=1, max_length=120)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    created_at: str = Field(min_length=1, max_length=64)
    source_job_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')

    @model_validator(mode='after')
    def compatible_target(self) -> Self:
        if not self.name.strip() or (self.engine, self.target) not in {('stable_audio3', 'small-music'), ('stable_audio3', 'medium'), ('yue2', 'ar'), ('yue2', 'nar')}:
            raise ValueError('music_lora_target_mismatch')
        return self


class MusicAdapters(Contract):
    adapters: list[MusicAdapter] = Field(max_length=1000)
    history_incomplete: bool = False


class MusicLoraCapability(Contract):
    engine: Literal['ace_step', 'yue2', 'stable_audio3']
    loading: bool
    training: bool
    reason: Literal['native', 'inference_only', 'unsupported_runtime', 'upgrade_required', 'experimental']


class MusicLoraCapabilities(Contract):
    engines: list[MusicLoraCapability]


MUSIC_LORA_CLIENT_MODELS: list[type[BaseModel]] = [MusicAdapterSelection, MusicAdapter, MusicAdapters, MusicLoraCapability, MusicLoraCapabilities]
