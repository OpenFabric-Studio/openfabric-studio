"""Path-free retained-source conditioning and honest native capability evidence."""
from __future__ import annotations

from typing import Annotated, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import Contract
from .music_analysis_contracts import (MusicAnalysisId, MusicAnalysisSha256, ProductionMusicSource,
    StemMusicSource, VersionMusicSource)


class ConditioningContract(Contract):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)


class BeatRenderConditioningSource(ConditioningContract):
    kind: Literal['beat_render']
    beat_id: MusicAnalysisId
    beat_revision: int = Field(ge=0, le=9007199254740991)
    render_id: MusicAnalysisId
    sha256: MusicAnalysisSha256


MusicConditioningSource: TypeAlias = Annotated[VersionMusicSource | StemMusicSource | ProductionMusicSource
    | BeatRenderConditioningSource, Field(discriminator='kind')]


class StableAudioConditioning(ConditioningContract):
    mode: Literal['audio_to_audio']
    source: MusicConditioningSource
    source_sha256: MusicAnalysisSha256
    # Reserve at least 1% input weight: near-one sigma can round to pure noise in FP16.
    noise_level: float = Field(ge=0.01, le=0.99)

    @model_validator(mode='after')
    def bound_source_digest(self) -> Self:
        if self.source.kind != 'version' and self.source.sha256 != self.source_sha256:
            raise ValueError('conditioning_source_changed')
        return self


class StableInpaintConditioning(ConditioningContract):
    mode: Literal['inpaint']
    source: MusicConditioningSource
    source_sha256: MusicAnalysisSha256
    start_sec: float = Field(ge=0, lt=380)
    end_sec: float = Field(gt=0, le=380)
    crossfade_sec: float = Field(default=0.02, ge=0, le=0.05)

    @model_validator(mode='after')
    def coherent_region(self) -> Self:
        if (self.start_sec >= self.end_sec or self.crossfade_sec * 2 > self.end_sec - self.start_sec
                or self.source.kind != 'version' and self.source.sha256 != self.source_sha256):
            raise ValueError('conditioning_invalid_region')
        return self


StableMusicConditioning: TypeAlias = Annotated[StableAudioConditioning | StableInpaintConditioning,
    Field(discriminator='mode')]
ConditioningMode = Literal['audio_to_audio', 'inpaint']
ConditioningErrorCode = Literal['conditioning_source_missing', 'conditioning_source_changed',
    'conditioning_source_invalid', 'conditioning_invalid_region', 'conditioning_invalid_request',
    'conditioning_source_busy', 'conditioning_encoder_missing', 'conditioning_model_changed',
    'conditioning_runtime_changed', 'conditioning_runtime_unavailable', 'conditioning_inpaint_format_unsupported',
    'conditioning_inpaint_duration_mismatch', 'conditioning_native_source_unused', 'conditioning_snapshot_invalid',
    'conditioning_output_invalid', 'conditioning_task_unsupported', 'insufficient_disk']


class MusicConditioningRecipe(ConditioningContract):
    source_sha256: MusicAnalysisSha256
    prepared_sha256: MusicAnalysisSha256
    source_rate_hz: int = Field(ge=8000, le=192000)
    source_channels: Literal[1, 2]
    source_frames: int = Field(ge=1, le=72960000)
    prepared_rate_hz: Literal[44100] = 44100
    prepared_channels: Literal[2] = 2
    prepared_frames: int = Field(ge=1, le=16758000)
    prepared_gain_db: float = Field(default=0, ge=-120, le=0)
    mode: ConditioningMode
    noise_level: float | None = Field(default=None, ge=0.01, le=0.99)
    region_start_frame: int | None = Field(default=None, ge=0, le=72960000)
    region_end_frame: int | None = Field(default=None, ge=1, le=72960000)
    crossfade_frames: int = Field(default=0, ge=0, le=9600)
    source_commit: str = Field(min_length=40, max_length=40, pattern=r'^[0-9a-f]{40}$')
    source_script_sha256: MusicAnalysisSha256
    encoder_sha256: MusicAnalysisSha256
    output_semantics: Literal['generated_full_mix', 'original_pcm_outside_region']

    @model_validator(mode='after')
    def truthful_output(self) -> Self:
        if self.mode == 'audio_to_audio':
            if (self.noise_level is None or self.region_start_frame is not None or self.region_end_frame is not None
                    or self.crossfade_frames != 0 or self.output_semantics != 'generated_full_mix'):
                raise ValueError('conditioning_invalid_request')
        elif (self.noise_level is not None or self.region_start_frame is None or self.region_end_frame is None
                or self.region_start_frame >= self.region_end_frame or self.region_end_frame > self.source_frames
                or self.crossfade_frames * 2 > self.region_end_frame - self.region_start_frame
                or self.output_semantics != 'original_pcm_outside_region'):
            raise ValueError('conditioning_invalid_request')
        return self


class MusicConditioningCapability(ConditioningContract):
    engine: Literal['ace_step', 'stable_audio3', 'yue2']
    mode: Literal['ace_complete', 'ace_lego', 'ace_cover', 'audio_to_audio', 'inpaint', 'yue_audio_conditioning']
    proof_status: Literal['source_verified', 'unverified']
    runtime_verified: Literal[False] = False
    enabled_small: bool
    enabled_medium: bool
    source_commit: str | None = Field(default=None, min_length=40, max_length=40, pattern=r'^[0-9a-f]{40}$')
    proof_sha256: MusicAnalysisSha256 | None = None
    output_semantics: Literal['generated_full_mix', 'original_pcm_outside_region', 'unverified']
    reason: ConditioningErrorCode | None = None
    notes: str = Field(min_length=1, max_length=1500)


class MusicConditioningCapabilities(ConditioningContract):
    modes: list[MusicConditioningCapability] = Field(min_length=1, max_length=6)


class MusicConditioningSourceRequest(ConditioningContract):
    source: MusicConditioningSource


class MusicConditioningSourceInfo(ConditioningContract):
    """Read-only retained-source metadata; native conditioning has a separate 380s cap."""
    source: MusicConditioningSource
    source_sha256: MusicAnalysisSha256
    source_bytes: int = Field(ge=1, le=512 * 1024**2)
    source_rate_hz: int = Field(ge=8000, le=192000)
    source_channels: Literal[1, 2]
    source_frames: int = Field(ge=1, le=1382400000)
    duration_sec: float = Field(gt=0, le=7200)


class MusicConditioningPreview(ConditioningContract):
    source: MusicConditioningSource
    source_sha256: MusicAnalysisSha256
    source_bytes: int = Field(ge=1, le=512 * 1024**2)
    source_rate_hz: int = Field(ge=8000, le=192000)
    source_channels: Literal[1, 2]
    source_frames: int = Field(ge=1, le=72960000)
    duration_sec: float = Field(gt=0, le=380)
    required_free_bytes: int = Field(ge=0, le=9007199254740991)
    free_bytes: int | None = Field(default=None, ge=0, le=9007199254740991)
    can_start: bool
    error_code: ConditioningErrorCode | None = None


MUSIC_CONDITIONING_CLIENT_MODELS: list[type[BaseModel]] = [MusicConditioningCapabilities, MusicConditioningPreview,
    MusicConditioningRecipe, MusicConditioningSourceRequest, MusicConditioningSourceInfo]
