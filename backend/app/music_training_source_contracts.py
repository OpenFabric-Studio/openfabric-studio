"""Immutable references to existing library tracks or saved dataset recordings."""
from __future__ import annotations

from typing import Self
from pydantic import ConfigDict, Field, model_validator
from .contracts import Contract


class MusicLoraDatasetSource(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    dataset_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    revision: int = Field(strict=True, ge=0, le=9007199254740991)
    sample_id: str = Field(min_length=1, max_length=200, pattern=r'^[A-Za-z0-9_-]+$')


class MusicLoraSource(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    track_id: int | None = Field(default=None, strict=True, ge=1)
    dataset: MusicLoraDatasetSource | None = None

    @model_validator(mode='after')
    def one_source(self) -> Self:
        if (self.track_id is None) == (self.dataset is None):
            raise ValueError('music_training_source_required')
        return self


def source_key(sample: MusicLoraSource) -> tuple[int | None, str | None, int | None, str | None]:
    reference = sample.dataset
    return (sample.track_id, reference.dataset_id if reference else None,
        reference.revision if reference else None, reference.sample_id if reference else None)
