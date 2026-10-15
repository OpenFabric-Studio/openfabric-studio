"""App-owned, non-destructive ACE adapter export contracts."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import Contract


class LoraExportRequest(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)
    lora_output_dir: str = Field(min_length=1, max_length=4096)
    export_path: str = Field(min_length=1, max_length=4096)


class LoraExportResponse(Contract):
    export_path: str = Field(min_length=1, max_length=4096)
    adapter_path: str = Field(min_length=1, max_length=4096)
    source: str = Field(min_length=1, max_length=4096)
    message: str = Field(min_length=1, max_length=200)


class LoraExportOptionsResponse(Contract):
    can_pick: bool


class LoraExportPickResponse(Contract):
    path: str | None = Field(max_length=4096)


class LoraTrainedFilesRequest(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)
    lora_output_dir: str = Field(min_length=1, max_length=4096)


class LoraTrainedCheckpoint(Contract):
    source: str = Field(min_length=1, max_length=4096)
    adapter_path: str = Field(min_length=1, max_length=4096)
    kind: Literal['final', 'checkpoint']


class LoraTrainedFilesResponse(Contract):
    checkpoint: LoraTrainedCheckpoint | None


LORA_EXPORT_CLIENT_MODELS: list[type[BaseModel]] = [
    LoraExportRequest, LoraExportResponse, LoraExportOptionsResponse, LoraExportPickResponse,
    LoraTrainedFilesRequest, LoraTrainedCheckpoint, LoraTrainedFilesResponse,
]
