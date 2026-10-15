"""App-owned immutable music plans and revisioned queue controls."""
from __future__ import annotations

from typing import Annotated, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import Contract, SavedTrack
from .generation_contracts import AceGenerationSettings
from .local_music_contracts import LocalMusicRequest
from .music_lora_contracts import MusicAdapter, MusicAdapterSelection
from .yue_contracts import YueSubmitRequest

MusicQueueEngine = Literal['ace_step', 'yue2', 'heartmula', 'stable_audio3']
MusicQueueId = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]
MusicQueueFingerprint = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
MusicQueueSeed = Annotated[int, Field(ge=0, le=2147483647, strict=True)]
MusicQueueRevision = Annotated[int, Field(ge=0, le=9_007_199_254_740_991, strict=True)]
MusicQueueItemStatus = Literal['queued', 'loading', 'submitting', 'running', 'saving', 'unloading', 'done', 'failed', 'cancelled', 'interrupted', 'blocked']


class MusicQueueContract(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)


class MusicQueueError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


class MusicQueueErrorResponse(MusicQueueContract):
    detail: str = Field(min_length=1, max_length=120)


class MusicQueueBrief(MusicQueueContract):
    title: str = Field(default='', max_length=120)
    description: str = Field(min_length=1, max_length=3500)
    lyrics: str = Field(default='', max_length=20000)
    instrumental: bool = False
    duration_seconds: int = Field(ge=10, le=380)
    bpm: float | None = Field(default=None, ge=20, le=300)
    key: str = Field(default='', max_length=80)
    language: str = Field(default='', max_length=80)

    @model_validator(mode='after')
    def validate_intent(self) -> Self:
        if not self.description.strip():
            raise ValueError('music_queue_description_required')
        if self.instrumental and self.lyrics.strip():
            raise ValueError('music_queue_instrumental_lyrics')
        return self


class MusicQueueEngineSelection(MusicQueueContract):
    engine: MusicQueueEngine
    variations: int = Field(default=1, ge=1, le=8)
    variant: Literal['small-music', 'medium'] = 'small-music'
    precision: Literal['q8_0', 'q4_0'] = 'q8_0'
    adapter: MusicAdapterSelection | None = None
    ar_adapter: MusicAdapterSelection | None = None
    nar_adapter: MusicAdapterSelection | None = None

    @model_validator(mode='after')
    def validate_configuration(self) -> Self:
        if self.engine != 'stable_audio3' and (self.variant != 'small-music' or self.adapter is not None):
            raise ValueError('music_queue_adapter_or_variant_unsupported')
        if self.engine != 'yue2' and (self.precision != 'q8_0' or self.ar_adapter is not None or self.nar_adapter is not None):
            raise ValueError('music_queue_yue_configuration_unsupported')
        return self


class MusicQueuePreviewRequest(MusicQueueContract):
    brief: MusicQueueBrief
    selections: list[MusicQueueEngineSelection] = Field(min_length=1, max_length=4)

    @model_validator(mode='after')
    def unique_engines(self) -> Self:
        if len({selection.engine for selection in self.selections}) != len(self.selections):
            raise ValueError('music_queue_duplicate_engine')
        return self


class MusicQueueReadiness(MusicQueueContract):
    engine: MusicQueueEngine
    supported: bool
    installed: bool
    weights_ready: bool
    managed_status: Literal['stopped', 'starting', 'running', 'stopping', 'error', 'one_shot']
    owned_instance: MusicQueueId | None = None
    ready: bool
    error_code: str = Field(default='', max_length=120)
    setup_href: str = Field(default='/settings', max_length=200)


class MusicQueueCatalog(MusicQueueContract):
    engines: list[MusicQueueReadiness] = Field(max_length=4)
    adapters: list[MusicAdapter] = Field(default_factory=list, max_length=1000)


class MusicQueueAdjustment(MusicQueueContract):
    field: str = Field(min_length=1, max_length=80)
    code: str = Field(min_length=1, max_length=120)
    requested: str = Field(max_length=200)
    effective: str = Field(max_length=200)


class MusicQueueModelPlan(MusicQueueContract):
    selection: MusicQueueEngineSelection
    readiness: MusicQueueReadiness
    compatible: bool
    error_code: str = Field(default='', max_length=120)
    effective_duration_seconds: int | None = Field(default=None, ge=10, le=380)
    duration_mode: Literal['target', 'maximum', 'model_led']
    adjustments: list[MusicQueueAdjustment] = Field(default_factory=list, max_length=20)
    warnings: list[Annotated[str, Field(max_length=120)]] = Field(default_factory=list, max_length=20)
    adapters: list[MusicAdapter] = Field(default_factory=list, max_length=2)


class MusicQueueAceRequest(MusicQueueContract):
    engine: Literal['ace_step']
    request: AceGenerationSettings


class MusicQueueYueRequest(MusicQueueContract):
    engine: Literal['yue2']
    request: YueSubmitRequest


class MusicQueueHeartRequest(MusicQueueContract):
    engine: Literal['heartmula']
    request: LocalMusicRequest

    @model_validator(mode='after')
    def matching_engine(self) -> Self:
        if self.request.engine != self.engine:
            raise ValueError('music_queue_payload_engine_mismatch')
        return self


class MusicQueueStableRequest(MusicQueueContract):
    engine: Literal['stable_audio3']
    request: LocalMusicRequest

    @model_validator(mode='after')
    def matching_engine(self) -> Self:
        if self.request.engine != self.engine:
            raise ValueError('music_queue_payload_engine_mismatch')
        return self


MusicQueuePayload: TypeAlias = Annotated[
    MusicQueueAceRequest | MusicQueueYueRequest | MusicQueueHeartRequest | MusicQueueStableRequest,
    Field(discriminator='engine'),
]


class MusicQueueNormalizedItem(MusicQueueContract):
    engine: MusicQueueEngine
    variation_index: int = Field(ge=1, le=8)
    payload: MusicQueuePayload
    adapters: list[MusicAdapter] = Field(default_factory=list, max_length=2)

    @model_validator(mode='after')
    def matching_engine(self) -> Self:
        if self.engine != self.payload.engine:
            raise ValueError('music_queue_payload_engine_mismatch')
        return self


class MusicQueuePreview(MusicQueueContract):
    brief: MusicQueueBrief
    selections: list[MusicQueueEngineSelection] = Field(min_length=1, max_length=4)
    models: list[MusicQueueModelPlan] = Field(min_length=1, max_length=4)
    items: list[MusicQueueNormalizedItem] = Field(max_length=32)
    total_items: int = Field(ge=1, le=32)
    can_enqueue: bool
    fingerprint: MusicQueueFingerprint


class MusicQueueItem(MusicQueueNormalizedItem):
    id: MusicQueueId
    batch_id: MusicQueueId
    resolved_seed: MusicQueueSeed
    child_job_id: str | None = Field(default=None, min_length=1, max_length=128)
    status: MusicQueueItemStatus = 'queued'
    tracks: list[SavedTrack] = Field(default_factory=list, max_length=8)
    error_code: str = Field(default='', max_length=120)
    attempt: int = Field(default=1, ge=1, le=1000)
    retry_of_item_id: MusicQueueId | None = None


class MusicQueueBatch(MusicQueueContract):
    id: MusicQueueId
    created_at: str = Field(min_length=1, max_length=64)
    brief: MusicQueueBrief
    selections: list[MusicQueueEngineSelection] = Field(min_length=1, max_length=4)
    models: list[MusicQueueModelPlan] = Field(min_length=1, max_length=4)
    preview_fingerprint: MusicQueueFingerprint
    items: list[MusicQueueItem] = Field(max_length=1000)


class MusicQueueSnapshot(MusicQueueContract):
    revision: MusicQueueRevision
    status: Literal['idle', 'running', 'pausing', 'paused', 'blocked']
    active_item_id: MusicQueueId | None = None
    error_code: str = Field(default='', max_length=120)
    history_incomplete: bool = False
    batches: list[MusicQueueBatch] = Field(default_factory=list, max_length=1000)


class MusicQueueEnqueueRequest(MusicQueuePreviewRequest):
    revision: MusicQueueRevision
    submission_key: str = Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9_-]+$')
    preview_fingerprint: MusicQueueFingerprint


class MusicQueueControlRequest(MusicQueueContract):
    revision: MusicQueueRevision
    action: Literal['start', 'pause', 'cancel_active']


class MusicQueueReorderRequest(MusicQueueContract):
    revision: MusicQueueRevision
    batch_ids: list[MusicQueueId] = Field(max_length=1000)

    @model_validator(mode='after')
    def unique_batches(self) -> Self:
        if len(set(self.batch_ids)) != len(self.batch_ids):
            raise ValueError('music_queue_duplicate_batch')
        return self


class MusicQueueRetryRequest(MusicQueueContract):
    revision: MusicQueueRevision
    item_id: MusicQueueId
    mode: Literal['save', 'inference']


class MusicQueueCancelRequest(MusicQueueContract):
    revision: MusicQueueRevision
    batch_id: MusicQueueId


MUSIC_QUEUE_CLIENT_MODELS: list[type[BaseModel]] = [
    MusicQueueErrorResponse, MusicQueueBrief, MusicQueueEngineSelection, MusicQueuePreviewRequest,
    MusicQueueReadiness, MusicQueueCatalog, MusicQueueAdjustment, MusicQueueModelPlan,
    MusicQueueAceRequest, MusicQueueYueRequest, MusicQueueHeartRequest, MusicQueueStableRequest,
    MusicQueueNormalizedItem, MusicQueuePreview, MusicQueueItem, MusicQueueBatch, MusicQueueSnapshot,
    MusicQueueEnqueueRequest, MusicQueueControlRequest, MusicQueueReorderRequest,
    MusicQueueRetryRequest, MusicQueueCancelRequest,
]
