"""Bounded app-owned contracts for optional reference preparation."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .contracts import Contract

ReferenceId = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]
SubtitleLanguage = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,34}$')]
ReferenceStageName = Literal['download', 'lyrics', 'separation', 'melody']
ReferenceStageStatus = Literal['queued', 'running', 'done', 'skipped', 'failed', 'missing_tool', 'cancelled']
ReferenceErrorCode = Literal[
    'invalid_url', 'provider_not_allowed', 'private_address', 'redirect_blocked',
    'network_failed', 'network_limit', 'duration_limit', 'size_limit',
    'yt_dlp_missing', 'ffmpeg_missing', 'subtitle_language_required',
    'subtitle_language_unavailable', 'subtitles_unavailable', 'unsupported_subtitles',
    'whisper_missing', 'whisper_model_missing', 'separation_unavailable',
    'melody_unavailable', 'tool_failed', 'tool_timeout', 'invalid_audio',
    'source_missing', 'track_missing', 'import_interrupted', 'import_failed',
    'invalid_abc', 'unsupported_abc', 'range_unavailable', 'lyrics_empty', 'busy',
]


class ReferenceContract(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class ReferenceToolCapability(ReferenceContract):
    available: bool
    reason: ReferenceErrorCode | None = None
    setup_hint: str = Field(default='', max_length=500)


class ReferenceSeparationCapability(ReferenceToolCapability):
    id: Literal['fast', 'high', 'roformer']


class ReferenceCapabilities(ReferenceContract):
    source_import: ReferenceToolCapability
    subtitles: ReferenceToolCapability
    whisper: ReferenceToolCapability
    separation: list[ReferenceSeparationCapability]
    melody: ReferenceToolCapability
    max_duration_seconds: int = 600
    max_source_bytes: int = 536_870_912


class ReferenceProbeRequest(ReferenceContract):
    url: str = Field(min_length=1, max_length=2048)


class ReferenceSource(ReferenceContract):
    provider: Literal['youtube'] = 'youtube'
    canonical_url: str = Field(max_length=200)
    video_id: str = Field(pattern=r'^[A-Za-z0-9_-]{11}$')
    title: str = Field(max_length=500)
    duration_seconds: float = Field(gt=0, le=600)
    subtitle_languages: list[SubtitleLanguage] = Field(default_factory=list, max_length=64)


class ReferenceImportRequest(ReferenceProbeRequest):
    kind: Literal['url'] = 'url'
    title: str = Field(default='', max_length=500)
    lyrics_source: Literal['none', 'subtitles', 'whisper'] = 'none'
    subtitle_language: SubtitleLanguage | None = None
    separation: Literal['none', 'fast', 'high', 'roformer'] = 'none'
    melody: bool = False

    @model_validator(mode='after')
    def selected_language(self) -> ReferenceImportRequest:
        if self.lyrics_source == 'subtitles' and self.subtitle_language is None:
            raise ValueError('subtitle_language_required')
        return self


class ReferenceTrackPreparationRequest(ReferenceContract):
    kind: Literal['track'] = 'track'
    track_id: int = Field(gt=0, le=9_007_199_254_740_991, strict=True)
    source_version_id: ReferenceId | None = None
    lyrics_source: Literal['none', 'whisper'] = 'none'
    subtitle_language: SubtitleLanguage | None = None
    separation: Literal['none', 'fast', 'high', 'roformer'] = 'none'
    melody: bool = True


class ReferenceLyricLine(ReferenceContract):
    text: str = Field(min_length=1, max_length=2000)
    start_seconds: float | None = Field(default=None, ge=0, le=600)
    end_seconds: float | None = Field(default=None, ge=0, le=600)
    language: SubtitleLanguage | None = None
    provenance: Literal['provided_subtitles', 'whisper', 'user_text'] = 'user_text'

    @model_validator(mode='after')
    def ordered_time(self) -> ReferenceLyricLine:
        if (self.start_seconds is None) != (self.end_seconds is None):
            raise ValueError('Both timestamps are required')
        if self.start_seconds is not None and self.end_seconds is not None and self.end_seconds <= self.start_seconds:
            raise ValueError('Subtitle end must follow start')
        return self


class ReferenceLyrics(ReferenceContract):
    lyrics: str = Field(max_length=100_000)
    lines: list[ReferenceLyricLine] = Field(max_length=4096)
    warnings: list[str] = Field(default_factory=list, max_length=16)


class ReferenceStage(ReferenceContract):
    name: ReferenceStageName
    status: ReferenceStageStatus
    error_code: ReferenceErrorCode | None = None


class ReferenceImport(ReferenceContract):
    id: ReferenceId
    status: Literal['queued', 'running', 'done', 'partial', 'failed', 'cancelled']
    request: ReferenceImportRequest | ReferenceTrackPreparationRequest
    source: ReferenceSource | None = None
    track_id: int | None = Field(default=None, gt=0)
    audio_url: str | None = None
    vocal_audio_url: str | None = None
    stages: list[ReferenceStage] = Field(min_length=4, max_length=4)
    lyrics: ReferenceLyrics | None = None
    abc: str | None = Field(default=None, max_length=100_000)
    created_at: str = Field(max_length=64)
    updated_at: str = Field(max_length=64)


class ReferenceImportsResponse(ReferenceContract):
    imports: list[ReferenceImport]


class ReferenceDeleteResponse(ReferenceContract):
    deleted: bool
    track_retained: bool


class ReferenceAbcRequest(ReferenceContract):
    abc: str = Field(min_length=1, max_length=100_000)
    transpose_semitones: int = Field(default=0, ge=-24, le=24, strict=True)
    tempo_bpm: int | None = Field(default=None, ge=20, le=300, strict=True)
    target_min_midi: int | None = Field(default=None, ge=24, le=96, strict=True)
    target_max_midi: int | None = Field(default=None, ge=24, le=96, strict=True)

    @model_validator(mode='after')
    def range_pair(self) -> ReferenceAbcRequest:
        if (self.target_min_midi is None) != (self.target_max_midi is None):
            raise ValueError('Both range boundaries are required')
        if self.target_min_midi is not None and self.target_max_midi is not None and self.target_min_midi > self.target_max_midi:
            raise ValueError('Pitch range is inverted')
        return self


class ReferenceAbcResponse(ReferenceContract):
    abc: str = Field(max_length=100_000)
    note_count: int = Field(ge=1, le=20_000)
    min_midi: int = Field(ge=0, le=127)
    max_midi: int = Field(ge=0, le=127)
    applied_transpose_semitones: int = Field(ge=-48, le=48)


class ReferenceLyricsRequest(ReferenceContract):
    text: str = Field(min_length=1, max_length=100_000)
    format: Literal['plain', 'srt', 'vtt'] = 'plain'
    language: SubtitleLanguage | None = None
    section_size: int = Field(default=8, ge=1, le=32, strict=True)


REFERENCE_CLIENT_MODELS: list[type[BaseModel]] = [
    ReferenceToolCapability, ReferenceSeparationCapability, ReferenceCapabilities,
    ReferenceProbeRequest, ReferenceSource, ReferenceImportRequest,
    ReferenceTrackPreparationRequest, ReferenceLyricLine, ReferenceLyrics,
    ReferenceStage, ReferenceImport, ReferenceImportsResponse, ReferenceDeleteResponse,
    ReferenceAbcRequest, ReferenceAbcResponse, ReferenceLyricsRequest,
]
