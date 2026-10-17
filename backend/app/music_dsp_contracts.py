"""Source-bound CPU DSP plans. Anchors always map source into target time."""
from __future__ import annotations

import math
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from .audio_quality_contracts import AudioMetrics, LoudnessSettings, TargetResult
from .music_analysis_contracts import (MusicAnalysisContract, MusicAnalysisId, MusicAnalysisMethod,
    MusicAnalysisSeconds, MusicAnalysisSha256, MusicAnalysisTimestamp, MusicSourceRef,
    ProductionMusicSource, StemMusicSource, VersionMusicSource)

DspRevision = Annotated[int, Field(ge=0, le=9007199254740991)]
DspGain = Annotated[float, Field(ge=-12.0412, le=12.0412)]
DspWarning = Literal['pitch_artifact_risk', 'level_match_unavailable', 'gain_capped', 'loudness_target_unmet',
    'loudness_inconclusive', 'true_peak_over', 'hard_clip_risk', 'unmatched_phrases', 'phrase_ambiguous',
    'alignment_unverified', 'alignment_budget_exceeded', 'stretch_unavailable', 'anchor_boundary_uncertain']
DspErrorCode = Literal['dsp_unavailable', 'dsp_stretch_unavailable', 'dsp_runtime_unverified', 'dsp_invalid_source',
    'dsp_source_changed', 'dsp_invalid_anchors', 'dsp_budget_exceeded', 'dsp_insufficient_disk', 'dsp_output_invalid',
    'dsp_timeout', 'dsp_worker_unverified', 'dsp_failed', 'alignment_unavailable', 'alignment_budget_exceeded']


class WarpAnchor(MusicAnalysisContract):
    source_sec: MusicAnalysisSeconds
    target_sec: MusicAnalysisSeconds


def validate_anchors(anchors: list[WarpAnchor], minimum: float = .75, maximum: float = 1.5) -> None:
    if len(anchors) < 2 or minimum > maximum:
        raise ValueError('dsp_invalid_anchors')
    for previous, current in zip(anchors, anchors[1:]):
        source = current.source_sec - previous.source_sec
        target = current.target_sec - previous.target_sec
        if source <= 0 or target <= 0 or not minimum - 1e-12 <= target / source <= maximum + 1e-12:
            raise ValueError('dsp_invalid_anchors')


class EqBand(MusicAnalysisContract):
    frequency_hz: float = Field(ge=20, le=20000)
    q: float = Field(default=1.0, ge=.1, le=12)
    gain_db: float = Field(ge=-6, le=6)


class CompressionSettings(MusicAnalysisContract):
    threshold_dbfs: float = Field(default=-18.0, ge=-60, le=0)
    ratio: float = Field(default=2.0, ge=1, le=4)
    knee_db: float = Field(default=6.0, ge=0, le=12)
    attack_ms: float = Field(default=10.0, ge=1, le=100)
    release_ms: float = Field(default=250.0, ge=10, le=2000)
    makeup_db: float = Field(default=0.0, ge=0, le=6)


class FadeSettings(MusicAnalysisContract):
    fade_in_sec: float = Field(default=0.0, ge=0, le=10)
    fade_out_sec: float = Field(default=0.0, ge=0, le=10)
    curve: Literal['equal_power', 'linear'] = 'linear'


class DspTransformRequest(MusicAnalysisContract):
    schema_version: Literal[1] = 1
    source: MusicSourceRef
    source_sha256: MusicAnalysisSha256
    revision: DspRevision
    sample_rate_hz: Literal[44100, 48000] | None = None
    channels: Literal[1, 2] | None = None
    anchors: list[WarpAnchor] = Field(min_length=2, max_length=4096)
    output_duration_sec: float = Field(gt=0, le=7200)
    pitch_shift_semitones: float = Field(default=0.0, ge=-12, le=12)
    min_duration_ratio: float = Field(default=.75, ge=.75, le=1.5)
    max_duration_ratio: float = Field(default=1.5, ge=.75, le=1.5)
    gain_db: DspGain = 0.0
    # FFmpeg two-pole Butterworth high-pass: nominal 12 dB/octave roll-off.
    highpass_hz: float | None = Field(default=None, ge=20, le=500)
    eq_bands: list[EqBand] = Field(default_factory=list, max_length=10)
    compression: CompressionSettings | None = None
    fades: FadeSettings = Field(default_factory=FadeSettings)
    loudness: LoudnessSettings = Field(default_factory=LoudnessSettings)

    @field_validator('loudness', mode='before')
    @classmethod
    def strict_loudness(cls, value: object) -> LoudnessSettings:
        return LoudnessSettings.model_validate(value, strict=True)

    @model_validator(mode='after')
    def coherent(self) -> Self:
        validate_anchors(self.anchors, self.min_duration_ratio, self.max_duration_ratio)
        duration = self.anchors[-1].target_sec - self.anchors[0].target_sec
        if not math.isclose(duration, self.output_duration_sec, abs_tol=1e-9, rel_tol=0):
            raise ValueError('dsp_target_span_mismatch')
        if self.fades.fade_in_sec + self.fades.fade_out_sec > duration:
            raise ValueError('dsp_fades_overlap')
        if sum(max(0.0, band.gain_db) for band in self.eq_bands) > 6:
            raise ValueError('dsp_eq_boost_budget')
        if self.sample_rate_hz is not None and any(band.frequency_hz > .45 * self.sample_rate_hz for band in self.eq_bands):
            raise ValueError('dsp_eq_above_nyquist_margin')
        if self.source.kind != 'version' and self.source.sha256 != self.source_sha256:
            raise ValueError('dsp_source_hash_mismatch')
        return self


class DspArtifactRef(MusicAnalysisContract):
    kind: Literal['transform'] = 'transform'
    id: MusicAnalysisId
    revision: DspRevision
    sha256: MusicAnalysisSha256


DspInputSource = Annotated[VersionMusicSource | StemMusicSource | ProductionMusicSource | DspArtifactRef,
    Field(discriminator='kind')]


class DuckSettings(MusicAnalysisContract):
    sidechain_input_id: MusicAnalysisId
    threshold_dbfs: float = Field(default=-24.0, ge=-60, le=0)
    ratio: float = Field(default=4.0, ge=1, le=8)
    knee_db: float = Field(default=6.0, ge=0, le=12)
    attack_ms: float = Field(default=10.0, ge=1, le=100)
    release_ms: float = Field(default=250.0, ge=20, le=2000)
    max_reduction_db: float = Field(default=6.0, ge=0, le=12)


class DspMixInput(MusicAnalysisContract):
    id: MusicAnalysisId
    source: DspInputSource
    source_sha256: MusicAnalysisSha256
    source_start_sec: MusicAnalysisSeconds = 0.0
    start_sec: MusicAnalysisSeconds = 0.0
    duration_sec: float = Field(gt=0, le=7200)
    gain_db: DspGain = 0.0
    pan: float = Field(default=0.0, ge=-1, le=1)
    muted: bool = False
    duck: DuckSettings | None = None

    @model_validator(mode='after')
    def coherent(self) -> Self:
        if self.source_start_sec + self.duration_sec > 7200 or self.start_sec + self.duration_sec > 7200:
            raise ValueError('dsp_mix_timeline_exceeded')
        if self.source.kind != 'version' and self.source.sha256 != self.source_sha256:
            raise ValueError('dsp_source_hash_mismatch')
        return self


class DspMixRequest(MusicAnalysisContract):
    schema_version: Literal[1] = 1
    revision: DspRevision
    sample_rate_hz: Literal[44100, 48000] = 48000
    channels: Literal[1, 2] = 2
    duration_sec: float = Field(gt=0, le=7200)
    inputs: list[DspMixInput] = Field(min_length=1, max_length=32)
    loudness: LoudnessSettings = Field(default_factory=LoudnessSettings)

    @field_validator('loudness', mode='before')
    @classmethod
    def strict_loudness(cls, value: object) -> LoudnessSettings:
        return LoudnessSettings.model_validate(value, strict=True)

    @model_validator(mode='after')
    def graph(self) -> Self:
        by_id = {item.id: item for item in self.inputs}
        if len(by_id) != len(self.inputs) or any(item.start_sec + item.duration_sec > self.duration_sec for item in self.inputs):
            raise ValueError('dsp_mix_timeline_or_identity_invalid')
        if self.channels == 1 and any(item.pan != 0 for item in self.inputs):
            raise ValueError('dsp_pan_requires_stereo')
        for item in self.inputs:
            visited = {item.id}
            current = item
            while current.duck is not None:
                sidechain = current.duck.sidechain_input_id
                if sidechain not in by_id or sidechain in visited:
                    raise ValueError('dsp_sidechain_cycle_or_missing')
                visited.add(sidechain)
                current = by_id[sidechain]
        return self


class DspEngineIdentity(MusicAnalysisContract):
    name: MusicAnalysisMethod
    revision: MusicAnalysisMethod
    configuration_sha256: MusicAnalysisSha256
    executable_sha256: MusicAnalysisSha256 | None = None
    recipe: MusicAnalysisMethod = 'direct-v1'
    input_gain_power_of_two: int = Field(default=0, ge=-32, le=0)
    output_gain_power_of_two: int = Field(default=0, ge=0, le=32)
    license_evidence_ids: list[Annotated[str, Field(min_length=1, max_length=120,
        pattern=r'^[A-Za-z0-9_.:-]+$')]] = Field(default_factory=list, max_length=64)

    @model_validator(mode='after')
    def compensated_headroom(self) -> Self:
        if self.input_gain_power_of_two + self.output_gain_power_of_two != 0:
            raise ValueError('dsp_uncompensated_native_headroom')
        return self


class DspMeasuredInput(MusicAnalysisContract):
    id: MusicAnalysisId
    source: DspInputSource
    source_sha256: MusicAnalysisSha256
    metrics: AudioMetrics

    @field_validator('metrics', mode='before')
    @classmethod
    def strict_metrics(cls, value: object) -> AudioMetrics:
        return AudioMetrics.model_validate(value, strict=True)

    @model_validator(mode='after')
    def identity(self) -> Self:
        if self.metrics.source_sha256 != self.source_sha256:
            raise ValueError('dsp_input_measurement_mismatch')
        return self


class DspArtifactResult(MusicAnalysisContract):
    schema_version: Literal[1] = 1
    id: MusicAnalysisId
    revision: DspRevision
    artifact_name: str = Field(min_length=36, max_length=36, pattern=r'^[0-9a-f]{32}\.wav$')
    audio_sha256: MusicAnalysisSha256
    sample_rate_hz: int = Field(ge=8000, le=192000)
    channels: Literal[1, 2]
    duration_frames: int = Field(ge=1, le=1382400000)
    output_metrics: AudioMetrics
    target_result: TargetResult
    engines: list[DspEngineIdentity] = Field(min_length=1, max_length=8)
    configuration_sha256: MusicAnalysisSha256
    warnings: list[DspWarning] = Field(default_factory=list, max_length=32)
    created_at: MusicAnalysisTimestamp

    @field_validator('output_metrics', mode='before')
    @classmethod
    def strict_metrics(cls, value: object) -> AudioMetrics:
        return AudioMetrics.model_validate(value, strict=True)

    @model_validator(mode='after')
    def output_identity(self) -> Self:
        if (self.artifact_name != self.id + '.wav' or self.output_metrics.source_sha256 != self.audio_sha256
                or self.output_metrics.samples_analyzed != self.duration_frames * self.channels
                or not math.isclose(self.output_metrics.duration_sec, self.duration_frames / self.sample_rate_hz,
                    rel_tol=0, abs_tol=1e-9)):
            raise ValueError('dsp_output_identity_mismatch')
        return self


class DspTransformResult(DspArtifactResult):
    request: DspTransformRequest
    input_metrics: AudioMetrics
    target_start_frame: int = Field(ge=0, le=1382400000)
    applied_gain_db: DspGain

    @field_validator('input_metrics', mode='before')
    @classmethod
    def strict_input_metrics(cls, value: object) -> AudioMetrics:
        return AudioMetrics.model_validate(value, strict=True)

    @model_validator(mode='after')
    def transform_identity(self) -> Self:
        start = round(self.request.anchors[0].target_sec * self.sample_rate_hz)
        end = round(self.request.anchors[-1].target_sec * self.sample_rate_hz)
        if (self.revision != self.request.revision or self.input_metrics.source_sha256 != self.request.source_sha256
                or self.target_start_frame != start or self.duration_frames != end - start
                or self.request.sample_rate_hz is not None and self.request.sample_rate_hz != self.sample_rate_hz
                or self.request.channels is not None and self.request.channels != self.channels):
            raise ValueError('dsp_transform_identity_mismatch')
        return self


class DspMixResult(DspArtifactResult):
    request: DspMixRequest
    input_metrics: list[DspMeasuredInput] = Field(min_length=1, max_length=32)

    @model_validator(mode='after')
    def mix_identity(self) -> Self:
        if (self.revision != self.request.revision or self.sample_rate_hz != self.request.sample_rate_hz
                or self.channels != self.request.channels or self.duration_frames != round(self.request.duration_sec * self.sample_rate_hz)
                or len(self.input_metrics) != len(self.request.inputs)):
            raise ValueError('dsp_mix_identity_mismatch')
        for measured, requested in zip(self.input_metrics, self.request.inputs):
            if measured.id != requested.id or measured.source != requested.source or measured.source_sha256 != requested.source_sha256:
                raise ValueError('dsp_mix_input_identity_mismatch')
        return self


class AlignmentRequest(MusicAnalysisContract):
    schema_version: Literal[1] = 1
    reference: MusicSourceRef
    reference_sha256: MusicAnalysisSha256
    prepared: MusicSourceRef
    prepared_sha256: MusicAnalysisSha256
    revision: DspRevision
    manual_anchors: list[WarpAnchor] = Field(default_factory=list, max_length=4096)
    min_duration_ratio: float = Field(default=.75, ge=.75, le=1.5)
    max_duration_ratio: float = Field(default=1.5, ge=.75, le=1.5)

    @model_validator(mode='after')
    def coherent(self) -> Self:
        if self.min_duration_ratio > self.max_duration_ratio:
            raise ValueError('dsp_invalid_anchors')
        if self.manual_anchors:
            validate_anchors(self.manual_anchors, self.min_duration_ratio, self.max_duration_ratio)
        for source, digest in ((self.reference, self.reference_sha256), (self.prepared, self.prepared_sha256)):
            if source.kind != 'version' and source.sha256 != digest:
                raise ValueError('dsp_source_hash_mismatch')
        return self


class AlignmentPhraseDiagnostic(MusicAnalysisContract):
    phrase_id: MusicAnalysisId
    reference_start_sec: MusicAnalysisSeconds | None = None
    reference_end_sec: MusicAnalysisSeconds | None = None
    prepared_start_sec: MusicAnalysisSeconds | None = None
    prepared_end_sec: MusicAnalysisSeconds | None = None
    mean_cost: float | None = Field(default=None, ge=0, le=1000000)
    onset_error_sec: float | None = Field(default=None, ge=0, le=7200)
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason: Literal['aligned', 'manual', 'unmatched_reference', 'unmatched_prepared', 'ambiguous',
        'budget_exceeded', 'insufficient_activity', 'unsafe_slope']

    @model_validator(mode='after')
    def intervals(self) -> Self:
        for start, end in ((self.reference_start_sec, self.reference_end_sec), (self.prepared_start_sec, self.prepared_end_sec)):
            if (start is None) != (end is None) or start is not None and end is not None and end <= start:
                raise ValueError('alignment_invalid_phrase_interval')
        if self.reference_start_sec is None and self.prepared_start_sec is None:
            raise ValueError('alignment_empty_phrase_diagnostic')
        return self


class AlignmentResult(MusicAnalysisContract):
    schema_version: Literal[1] = 1
    id: MusicAnalysisId
    request: AlignmentRequest
    status: Literal['accepted', 'needs_review', 'unavailable']
    anchors: list[WarpAnchor] = Field(default_factory=list, max_length=4096)
    phrases: list[AlignmentPhraseDiagnostic] = Field(default_factory=list, max_length=1024)
    feature_method: MusicAnalysisMethod
    matcher_method: MusicAnalysisMethod
    configuration_sha256: MusicAnalysisSha256
    warnings: list[DspWarning] = Field(default_factory=list, max_length=32)
    created_at: MusicAnalysisTimestamp

    @model_validator(mode='after')
    def coherent(self) -> Self:
        if self.anchors:
            validate_anchors(self.anchors, self.request.min_duration_ratio, self.request.max_duration_ratio)
        if self.status == 'accepted' and (len(self.anchors) < 2 or self.warnings):
            raise ValueError('alignment_acceptance_requires_validated_evidence')
        if len({phrase.phrase_id for phrase in self.phrases}) != len(self.phrases):
            raise ValueError('alignment_duplicate_phrase_id')
        return self


MUSIC_DSP_CLIENT_MODELS: list[type[BaseModel]] = [WarpAnchor, EqBand, CompressionSettings, FadeSettings,
    DspTransformRequest, DspArtifactRef, DuckSettings, DspMixInput, DspMixRequest, DspEngineIdentity,
    DspMeasuredInput, DspTransformResult, DspMixResult, AlignmentRequest, AlignmentPhraseDiagnostic, AlignmentResult]
