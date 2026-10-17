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


class MusicLoraRegionalSource(Contract):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    manifest_id: str = Field(min_length=32, max_length=32, pattern=r'^[0-9a-f]{32}$')
    manifest_sha256: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    excerpt_id: str = Field(min_length=32, max_length=32, pattern=r'^[0-9a-f]{32}$')


class MusicLoraSource(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    track_id: int | None = Field(default=None, strict=True, ge=1)
    dataset: MusicLoraDatasetSource | None = None
    regional_manifest: MusicLoraRegionalSource | None = None

    @model_validator(mode='after')
    def one_source(self) -> Self:
        if sum(value is not None for value in (self.track_id, self.dataset, self.regional_manifest)) != 1:
            raise ValueError('music_training_source_required')
        return self


def source_key(sample: MusicLoraSource) -> tuple[int | None, str | None, int | None, str | None, str | None, str | None, str | None]:
    reference = sample.dataset
    regional = sample.regional_manifest
    return (sample.track_id, reference.dataset_id if reference else None,
        reference.revision if reference else None, reference.sample_id if reference else None,
        regional.manifest_id if regional else None, regional.manifest_sha256 if regional else None,
        regional.excerpt_id if regional else None)
