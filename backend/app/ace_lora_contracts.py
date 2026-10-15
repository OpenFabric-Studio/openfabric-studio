"""Bounded ACE adapter controls owned by the application."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from .contracts import Contract

AceLoraErrorCode = Literal['ace_lora_path_missing', 'ace_lora_invalid_adapter', 'ace_lora_already_loaded',
    'ace_lora_quantized_unsupported', 'ace_lora_not_loaded', 'ace_lora_busy', 'ace_lora_model_inactive',
    'ace_lora_operation_failed', 'ace_lora_status_unavailable', 'ace_lora_selection_mismatch']
AdapterName = Annotated[str, Field(min_length=1, max_length=500, pattern=r'\S')]


class AceLoraStatus(Contract):
    model_config = ConfigDict(strict=True, allow_inf_nan=False)
    lora_loaded: StrictBool
    use_lora: StrictBool
    lora_scale: float = Field(ge=0, le=1)
    active_adapter: AdapterName | None
    adapters: list[AdapterName] = Field(max_length=64)

    @model_validator(mode='after')
    def enabled_adapter(self) -> AceLoraStatus:
        if self.use_lora and (not self.lora_loaded or self.active_adapter is None or self.active_adapter not in self.adapters):
            raise ValueError('Enabled adapter identity is unavailable')
        return self


class AceLoraFailure(Contract):
    error: AceLoraErrorCode


class AceLoraLoadRequest(Contract):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    lora_path: str = Field(min_length=1, max_length=4096)
    adapter_name: AdapterName | None = None


class AceLoraScaleRequest(Contract):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    scale: float = Field(ge=0, le=1)
    adapter_name: AdapterName | None = None


class AceLoraToggleRequest(Contract):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    use_lora: StrictBool


ACE_LORA_CLIENT_MODELS: list[type[BaseModel]] = [AceLoraStatus, AceLoraFailure, AceLoraLoadRequest,
    AceLoraScaleRequest, AceLoraToggleRequest]
