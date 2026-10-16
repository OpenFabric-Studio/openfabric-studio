"""Bounded recording references and durable local training-caption results."""
from __future__ import annotations

from typing import Literal, Self
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .contracts import Contract
from .music_training_source_contracts import MusicLoraSource, source_key


class TrainingCaptionRequest(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    samples: list[MusicLoraSource] = Field(min_length=1, max_length=100)
    segment_seconds: int = Field(default=12, strict=True, ge=6, le=30)

    @model_validator(mode='after')
    def distinct_sources(self) -> Self:
        if len({source_key(source) for source in self.samples}) != len(self.samples):
            raise ValueError('training_caption_duplicate_source')
        return self


class TrainingCaptionItem(Contract):
    source: MusicLoraSource
    status: Literal['queued', 'running', 'done', 'failed'] = 'queued'
    caption: str = Field(default='', max_length=4000)
    error_code: str | None = Field(default=None, max_length=80)


class TrainingCaptionJob(Contract):
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    status: Literal['queued', 'running', 'done', 'partial', 'failed', 'cancelled', 'interrupted']
    request: TrainingCaptionRequest
    items: list[TrainingCaptionItem] = Field(min_length=1, max_length=100)
    created_at: str = Field(max_length=64)
    updated_at: str = Field(max_length=64)
    error_code: str | None = Field(default=None, max_length=80)


class TrainingCaptionJobs(Contract):
    jobs: list[TrainingCaptionJob] = Field(max_length=1000)


TRAINING_CAPTION_CLIENT_MODELS: list[type[BaseModel]] = [TrainingCaptionRequest, TrainingCaptionItem,
    TrainingCaptionJob, TrainingCaptionJobs]
