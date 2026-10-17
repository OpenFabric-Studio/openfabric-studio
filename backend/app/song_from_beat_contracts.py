"""Typed retained-source ACE tasks; no vendor paths or asserted source proof."""
from __future__ import annotations

from typing import Annotated, Literal, Self, TypeAlias

from pydantic import BaseModel, Field, model_validator

from .music_analysis_contracts import MusicAnalysisSha256, VersionMusicSource
from .music_conditioning_contracts import BeatRenderConditioningSource, ConditioningContract, ConditioningErrorCode

AceTrackRole = Literal['woodwinds', 'brass', 'fx', 'synth', 'strings', 'percussion', 'keyboard',
    'guitar', 'bass', 'drums', 'backing_vocals', 'vocals']
AceSongSource: TypeAlias = Annotated[VersionMusicSource | BeatRenderConditioningSource, Field(discriminator='kind')]


class _AceSource(ConditioningContract):
    source: AceSongSource
    source_sha256: MusicAnalysisSha256

    @model_validator(mode='after')
    def bound_source(self) -> Self:
        if self.source.kind == 'beat_render' and self.source.sha256 != self.source_sha256:
            raise ValueError('conditioning_source_changed')
        return self


class AceCompleteConditioning(_AceSource):
    mode: Literal['ace_complete']
    track_classes: list[AceTrackRole] = Field(min_length=1, max_length=12)

    @model_validator(mode='after')
    def unique_roles(self) -> Self:
        if len(set(self.track_classes)) != len(self.track_classes):
            raise ValueError('conditioning_invalid_request')
        return self


class AceLegoConditioning(_AceSource):
    mode: Literal['ace_lego']
    track_name: AceTrackRole


AceMusicConditioning: TypeAlias = Annotated[AceCompleteConditioning | AceLegoConditioning, Field(discriminator='mode')]


class SongFromBeatCapability(ConditioningContract):
    mode: Literal['ace_complete', 'ace_lego']
    base_model: Literal['acestep-v15-base'] = 'acestep-v15-base'
    proof_status: Literal['source_verified', 'unverified']
    runtime_verified: Literal[False] = False
    installed: bool
    weights_ready: bool
    ready: bool
    source_commit: str = Field(pattern=r'^[0-9a-f]{40}$', min_length=40, max_length=40)
    proof_sha256: MusicAnalysisSha256
    output_semantics: Literal['generated_full_mix'] = 'generated_full_mix'
    minimum_source_seconds: Literal[10] = 10
    maximum_source_seconds: Literal[300] = 300
    reason: ConditioningErrorCode | None = None
    notes: str = Field(min_length=1, max_length=1500)


class SongFromBeatCapabilities(ConditioningContract):
    modes: list[SongFromBeatCapability] = Field(min_length=2, max_length=2)


class RetainedAceConditioning(ConditioningContract):
    """Server-only receipt; a vendor JSON field cannot assert this proof."""
    conditioning: AceMusicConditioning
    source_frames: int = Field(ge=80000, le=57600000)
    source_rate_hz: int = Field(ge=8000, le=192000)
    source_channels: Literal[1, 2]
    source_bytes: int = Field(ge=1, le=512 * 1024**2)
    source_commit: str = Field(pattern=r'^[0-9a-f]{40}$', min_length=40, max_length=40)
    source_proof_sha256: MusicAnalysisSha256
    model_sha256: MusicAnalysisSha256
    artifacts_sha256: MusicAnalysisSha256
    output_semantics: Literal['generated_full_mix'] = 'generated_full_mix'


SONG_FROM_BEAT_CLIENT_MODELS: list[type[BaseModel]] = [AceCompleteConditioning, AceLegoConditioning,
    SongFromBeatCapabilities]
