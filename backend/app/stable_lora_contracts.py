"""Bounded fresh-run Stable Audio 3 adapter training contracts."""
from __future__ import annotations

from typing import Literal, Self
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .contracts import Contract
from .music_lora_contracts import MusicAdapter
from .music_training_contracts import MusicLoraTrainingProgress
from .music_training_source_contracts import MusicLoraSource, source_key

StableLoraVariant = Literal['small-music', 'medium']
StableLoraStage = Literal['preparing', 'encoding', 'loading', 'training', 'publishing', 'done']


class StableLoraSample(MusicLoraSource):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    caption: str = Field(min_length=1, max_length=4000)

    @model_validator(mode='after')
    def nonblank_caption(self) -> Self:
        if not self.caption.strip():
            raise ValueError('stable_lora_caption_required')
        return self


class StableLoraTrainingRequest(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=120)
    variant: StableLoraVariant = 'small-music'
    samples: list[StableLoraSample] = Field(min_length=1, max_length=100)
    max_steps: int = Field(default=1000, ge=1, le=100000, strict=True)
    rank: Literal[8, 16, 32] = 16
    learning_rate: float = Field(default=0.0001, ge=0.0000001, le=0.01, strict=True)
    seed: int = Field(default=42, ge=0, le=2147483647, strict=True)

    @model_validator(mode='after')
    def validate_dataset(self) -> Self:
        if not self.name.strip():
            raise ValueError('stable_lora_name_required')
        if len({source_key(sample) for sample in self.samples}) != len(self.samples):
            raise ValueError('stable_lora_duplicate_track')
        return self


class StableLoraProgress(MusicLoraTrainingProgress):
    stage: StableLoraStage = 'preparing'


class StableLoraTrainingJob(Contract):
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    status: Literal['queued', 'running', 'done', 'failed', 'cancelled', 'interrupted']
    stage: StableLoraStage = 'preparing'
    created_at: str
    started_at: str | None = Field(default=None, max_length=64)
    finished_at: str | None = Field(default=None, max_length=64)
    request: StableLoraTrainingRequest
    progress: StableLoraProgress = Field(default_factory=StableLoraProgress)
    adapter: MusicAdapter | None = None
    error_code: str | None = Field(default=None, max_length=80)

    @model_validator(mode='after')
    def legacy_progress(self) -> Self:
        if 'progress' not in self.model_fields_set:
            self.progress.stage = self.stage
        return self


class StableLoraTrainingJobs(Contract):
    jobs: list[StableLoraTrainingJob] = Field(max_length=1000)
    history_incomplete: bool = False


class StableLoraTrainingStatus(Contract):
    supported: bool
    installed: bool
    small_weights_ready: bool
    medium_weights_ready: bool


class StableLoraCompletion(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    job_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    request_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    checkpoint: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    dataset_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    variant: StableLoraVariant
    max_steps: int = Field(ge=1, le=100000, strict=True)


STABLE_LORA_CLIENT_MODELS: list[type[BaseModel]] = [StableLoraSample, StableLoraTrainingRequest, StableLoraProgress, StableLoraTrainingJob, StableLoraTrainingJobs, StableLoraTrainingStatus]
