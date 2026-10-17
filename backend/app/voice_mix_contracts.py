"""Opt-in measured conversion mixing; legacy apply requests remain unchanged."""
from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from .artist_production_contracts import VoiceModelSnapshot
from .audio_quality_contracts import AudioMetrics, LoudnessSettings
from .music_analysis_contracts import (MusicAnalysisContract, MusicAnalysisId, MusicAnalysisSha256,
    MusicAnalysisTimestamp, VersionMusicSource)
from .music_dsp_contracts import (CompressionSettings, DspGain, DspMixResult, DspTransformResult,
    DspWarning, EqBand)


class VoiceMixSettings(MusicAnalysisContract):
    schema_version: Literal[1] = 1
    vocal_level: Literal['match_original', 'manual'] = 'match_original'
    manual_gain_db: DspGain = 0.0
    eq_bands: list[EqBand] = Field(default_factory=list, max_length=10)
    compression: CompressionSettings | None = None
    loudness: LoudnessSettings = Field(default_factory=LoudnessSettings)

    @field_validator('loudness', mode='before')
    @classmethod
    def strict_loudness(cls, value: object) -> LoudnessSettings:
        return LoudnessSettings.model_validate(value, strict=True)

    @model_validator(mode='after')
    def explicit_gain(self) -> Self:
        if self.vocal_level != 'manual' and self.manual_gain_db != 0:
            raise ValueError('voice_mix_manual_gain_unused')
        if sum(max(0.0, band.gain_db) for band in self.eq_bands) > 6:
            raise ValueError('voice_mix_eq_boost_budget')
        return self


class VoiceMixArtifact(MusicAnalysisContract):
    id: MusicAnalysisId
    role: Literal['original_vocal', 'converted_dry', 'backing', 'matched_vocal', 'master']
    sha256: MusicAnalysisSha256
    evidence_asset_id: MusicAnalysisId
    sample_rate_hz: int = Field(ge=8000, le=192000)
    channels: Literal[1, 2]
    duration_frames: int = Field(ge=1, le=1382400000)
    audio_url: str = Field(max_length=200,
        pattern=r'^/api/tracks/[1-9][0-9]*/versions/[0-9a-f]{32}/mix/artifacts/[0-9a-f]{32}$')


class VoiceMixReport(MusicAnalysisContract):
    schema_version: Literal[1] = 1
    id: MusicAnalysisId
    audio_version_id: MusicAnalysisId
    source: VersionMusicSource
    source_sha256: MusicAnalysisSha256
    settings: VoiceMixSettings
    conversion: VoiceModelSnapshot
    reference_metrics: AudioMetrics
    converted_metrics: AudioMetrics
    applied_gain_db: DspGain
    residual_lu: float | None = Field(default=None, ge=-200, le=200)
    warnings: list[DspWarning] = Field(default_factory=list, max_length=32)
    transform: DspTransformResult
    mix: DspMixResult
    artifacts: list[VoiceMixArtifact] = Field(min_length=5, max_length=8)
    created_at: MusicAnalysisTimestamp

    @field_validator('conversion', mode='before')
    @classmethod
    def strict_conversion(cls, value: object) -> VoiceModelSnapshot:
        return VoiceModelSnapshot.model_validate(value, strict=True)

    @field_validator('reference_metrics', 'converted_metrics', mode='before')
    @classmethod
    def strict_metrics(cls, value: object) -> AudioMetrics:
        return AudioMetrics.model_validate(value, strict=True)

    @model_validator(mode='after')
    def identities(self) -> Self:
        if len({item.id for item in self.artifacts}) != len(self.artifacts):
            raise ValueError('voice_mix_duplicate_artifact')
        for role in ('original_vocal', 'converted_dry', 'matched_vocal', 'master'):
            if sum(item.role == role for item in self.artifacts) != 1:
                raise ValueError('voice_mix_role_missing')
        by_role = {item.role: item for item in self.artifacts}
        if ('backing' not in by_role or by_role['original_vocal'].sha256 != self.reference_metrics.source_sha256
                or by_role['converted_dry'].sha256 != self.converted_metrics.source_sha256
                or by_role['matched_vocal'].sha256 != self.transform.audio_sha256
                or by_role['master'].sha256 != self.mix.audio_sha256
                or self.transform.applied_gain_db != self.applied_gain_db):
            raise ValueError('voice_mix_measurement_identity_mismatch')
        return self


VOICE_MIX_CLIENT_MODELS: list[type[BaseModel]] = [VoiceMixSettings, VoiceMixArtifact, VoiceMixReport]
