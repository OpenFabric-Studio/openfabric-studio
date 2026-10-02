"""Public immutable track-audio version contracts."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from .contracts import Contract, JobStatus
from .voice_contracts import VoiceJobProgress

AudioVersionId = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]


class AudioVersion(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

    id: AudioVersionId
    track_id: int = Field(gt=0)
    kind: Literal['original', 'voice']
    voice_id: AudioVersionId | None = None
    voice_name: str | None = Field(default=None, max_length=200)
    status: JobStatus
    created_at: str
    filename: str | None = None
    audio_url: str | None = None
    error_code: str = ''
    duration_ms: float | None = Field(default=None, ge=0)
    source_version_id: AudioVersionId | None = None
    job_progress: VoiceJobProgress | None = None


class TrackAudioVersionsResponse(Contract):
    track_id: int = Field(gt=0)
    original_available: bool
    versions: list[AudioVersion] = Field(default_factory=list)


class CreateAudioVersionRequest(Contract):
    model_config = ConfigDict(extra='forbid')
    voice_id: AudioVersionId
