"""App-owned YuE requests, lifecycle and bounded native telemetry."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from .contracts import SavedTrack
from .generation_contracts import GenerationContract, YueGenerationSettings

YueRunId = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]


class YueOptions(GenerationContract):
    style: str = Field(min_length=1, max_length=100_000)
    cot: Literal['off', 'melody', 'full'] = 'off'
    cfg_scale: float | None = Field(default=None, ge=0, le=20)
    # Upper bounds below are application resource limits, not native maxima.
    num_inference_steps: int = Field(default=8, ge=1, le=256)
    abc: str | None = Field(default=None, max_length=100_000)
    semantic_temperature: float | None = Field(default=None, ge=0, le=5)
    semantic_top_p: float | None = Field(default=None, gt=0, le=1)
    semantic_top_k: int | None = Field(default=None, ge=1, le=100_000)
    semantic_repetition_penalty: float | None = Field(default=None, gt=0, le=100)
    semantic_penalty_window: int | None = Field(default=None, ge=1, le=9000)
    semantic_min_tokens: int | None = Field(default=None, ge=0, le=9000)
    semantic_max_tokens: int | None = Field(default=None, ge=1, le=9000)
    abc_temperature: float | None = Field(default=None, ge=0, le=5)
    abc_top_p: float | None = Field(default=None, gt=0, le=1)
    abc_top_k: int | None = Field(default=None, ge=1, le=100_000)
    abc_repetition_penalty: float | None = Field(default=None, gt=0, le=100)
    abc_penalty_window: int | None = Field(default=None, ge=1, le=4096)
    abc_min_tokens: int | None = Field(default=None, ge=0, le=4096)
    abc_max_tokens: int | None = Field(default=None, ge=1, le=4096)

    @model_validator(mode='after')
    def compatible_options(self) -> YueOptions:
        for minimum, maximum in ((self.semantic_min_tokens, self.semantic_max_tokens),
                                 (self.abc_min_tokens, self.abc_max_tokens)):
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError('Minimum token count exceeds maximum')
        if self.abc and self.cot == 'off':
            raise ValueError('External ABC requires melody planning')
        if not self.style.strip():
            raise ValueError('Style is empty')
        return self


class YueSubmitRequest(GenerationContract):
    title: str = Field(default='', max_length=500)
    lyrics: str = Field(min_length=1, max_length=100_000)
    seed: int = Field(ge=0, le=9_007_199_254_740_991)
    options: YueOptions
    precision: Literal['q8_0', 'q4_0'] = 'q8_0'
    voice_id: YueRunId | None = None
    settings: YueGenerationSettings | None = None


class YueNativeProgress(GenerationContract):
    run_id: YueRunId
    phase: Literal['planning', 'semantic', 'acoustic', 'decode', 'done']
    current: int = Field(ge=0, le=100_000_000)
    total: int | None = Field(default=None, ge=1, le=100_000_000)
    started_ms: int = Field(ge=0)
    phase_started_ms: int = Field(ge=0)
    updated_ms: int = Field(ge=0)

    @model_validator(mode='after')
    def chronological(self) -> YueNativeProgress:
        if not self.started_ms <= self.phase_started_ms <= self.updated_ms:
            raise ValueError('Invalid progress timestamps')
        if self.total is not None and self.current > self.total:
            raise ValueError('Invalid progress count')
        return self


class YueJobResponse(GenerationContract):
    native_progress_available: bool = False
    id: YueRunId
    status: Literal['queued', 'running', 'stopping', 'done', 'failed', 'cancelled']
    stage: str = Field(max_length=120)
    queue_reason: str = Field(default='', max_length=120)
    created_at: str = Field(max_length=64)
    started_at: str | None = None
    elapsed_seconds: float = Field(default=0, ge=0)
    phase_eta_seconds: float | None = Field(default=None, ge=0)
    progress: YueNativeProgress | None = None
    title: str = Field(max_length=500)
    lyrics: str = Field(max_length=100_000)
    seed: int
    precision: Literal['q8_0', 'q4_0']
    options: YueOptions
    voice_id: YueRunId | None
    error_code: str = Field(default='', max_length=120)
    track: SavedTrack | None = None


class YueJobsResponse(GenerationContract):
    jobs: list[YueJobResponse]


class DeleteYueJobResponse(GenerationContract):
    deleted: bool


YUE_CLIENT_MODELS: list[type[BaseModel]] = [YueOptions, YueSubmitRequest, YueNativeProgress, YueJobResponse, YueJobsResponse, DeleteYueJobResponse]
