"""App-owned wire contracts. Generate client types and decoders from these models."""
from __future__ import annotations

import math
from typing import Annotated, Literal, TypeAlias
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue as PydanticJsonValue, StrictBool


def finite_json(value: PydanticJsonValue) -> PydanticJsonValue:
    """Pydantic accepts JSON NaN extensions; our wire contract accepts finite JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Non-finite JSON number')
    if isinstance(value, list):
        for item in value:
            finite_json(item)
    elif isinstance(value, dict):
        for item in value.values():
            finite_json(item)
    return value


JsonValue: TypeAlias = Annotated[PydanticJsonValue, AfterValidator(finite_json)]

JsonObject = dict[str, JsonValue]
JobStatus = Literal['queued', 'running', 'done', 'failed', 'cancelled']
TrackOrigin = Literal['ace_step', 'yue2', 'editor', 'upload', 'openrouter']


class Contract(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)


class SavedTrack(Contract):
    id: int
    short_id: int | None
    is_favorite: bool = False
    model: TrackOrigin
    created_at: str
    title: str
    lyrics: str
    seed: int | None
    duration_ms: float | None
    wall_ms: float | None
    params: JsonObject
    filename: str
    audio_url: str
    abc_url: str | None
    stems: dict[str, str] | None
    midi: dict[str, str] | None


class SetTrackFavoriteRequest(Contract):
    is_favorite: StrictBool


class TracksResponse(Contract):
    data: list[SavedTrack]


class AceJobResponse(Contract):
    task_id: str
    status: JobStatus
    created_at: str
    title: str
    lyrics: str
    audio_format: Literal['mp3', 'wav', 'flac']
    batch_size: int = Field(ge=1, le=8)
    params: JsonObject
    progress: float = Field(ge=0, le=1)
    stage: str
    error: str
    error_code: str
    tracks: list[SavedTrack]
    voice_id: str | None


class AceJobReleaseResponse(Contract):
    task_id: str
    status: JobStatus
    queue_position: int


class AceJobsResponse(Contract):
    jobs: list[AceJobResponse]


class AceJobQueryResponse(Contract):
    data: list[AceJobResponse]


class AceQueryRequest(Contract):
    task_id_list: list[str] = Field(max_length=100)


class AceAdoptRequest(Contract):
    task_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')
    params: JsonObject
    title: str = Field(max_length=500)
    voice_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    track_ids: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list, max_length=8)


class AceSubmitParams(Contract):
    """Validate fields that affect local storage; vendor parameters remain opaque JSON."""
    model_config = ConfigDict(extra='allow', allow_inf_nan=False)
    audio_format: Literal['mp3', 'wav', 'flac'] = 'mp3'
    batch_size: int = Field(default=1, ge=1, le=8)
    lyrics: str = ''


CLIENT_MODELS: list[type[BaseModel]] = [SavedTrack, SetTrackFavoriteRequest, TracksResponse, AceJobResponse, AceJobReleaseResponse, AceJobsResponse, AceJobQueryResponse, AceAdoptRequest]
