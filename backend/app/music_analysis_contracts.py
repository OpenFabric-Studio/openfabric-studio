"""Strict source-bound music analysis; unknown musical evidence stays unknown."""
from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from datetime import datetime, timedelta
import math
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from .audio_quality_contracts import AudioMetrics
from .contracts import Contract

MusicAnalysisId = Annotated[str, Field(min_length=32, max_length=32, pattern=r'^[0-9a-f]{32}$')]
MusicAnalysisSha256 = Annotated[str, Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')]
MusicAnalysisSeconds = Annotated[float, Field(ge=0, le=7200)]
MusicAnalysisMethod = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$')]
MusicAnalysisWarning = Literal['silence', 'short_audio', 'beat_grid_uncertain', 'tempo_half_double',
    'meter_unknown', 'key_unknown', 'key_changes', 'generic_sections', 'vocal_activity_unavailable',
    'channel_disagreement', 'window_boundary_uncertain', 'analysis_partial']
MusicAnalysisErrorCode = Literal['analysis_unavailable', 'analysis_weights_missing', 'analysis_runtime_unverified',
    'analysis_failed', 'invalid_audio', 'source_missing', 'source_changed', 'decode_budget_exceeded',
    'analysis_budget_exceeded', 'insufficient_disk', 'analysis_timeout', 'worker_output_limit', 'worker_unverified',
    'analysis_cancelled', 'analysis_interrupted', 'analysis_revision_conflict', 'analysis_storage_unavailable',
    'setup_busy', 'analysis_busy', 'unsupported_audio', 'analysis_job_not_found']


def _utc(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() != timedelta(0):
        raise ValueError('analysis_timestamp_not_utc')
    return value


MusicAnalysisTimestamp = Annotated[str, Field(min_length=20, max_length=40,
    pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$'), AfterValidator(_utc)]


def _ordered(values: list[float]) -> list[float]:
    if any(right <= left for left, right in zip(values, values[1:])):
        raise ValueError('analysis_times_not_strictly_increasing')
    return values


MusicAnalysisTimes = Annotated[list[MusicAnalysisSeconds], Field(max_length=50000), AfterValidator(_ordered)]


class MusicAnalysisContract(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True, frozen=True)


class VersionMusicSource(MusicAnalysisContract):
    kind: Literal['version'] = 'version'
    track_id: int = Field(ge=1, le=9007199254740991)
    version_id: MusicAnalysisId


class StemMusicSource(MusicAnalysisContract):
    kind: Literal['stem'] = 'stem'
    track_id: int = Field(ge=1, le=9007199254740991)
    stem: Literal['vocals', 'drums', 'bass', 'other', 'accompaniment']
    sha256: MusicAnalysisSha256


class ProductionMusicSource(MusicAnalysisContract):
    kind: Literal['production_asset'] = 'production_asset'
    asset_id: MusicAnalysisId
    sha256: MusicAnalysisSha256


MusicSourceRef = Annotated[VersionMusicSource | StemMusicSource | ProductionMusicSource, Field(discriminator='kind')]


class TempoPoint(MusicAnalysisContract):
    start_sec: MusicAnalysisSeconds
    bpm: float = Field(ge=20, le=400)


class TempoEstimate(MusicAnalysisContract):
    status: Literal['known', 'unknown'] = 'unknown'
    bpm: float | None = Field(default=None, ge=20, le=400)
    confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_method: MusicAnalysisMethod = 'unavailable'
    half_double_ambiguous: bool = False
    map: list[TempoPoint] = Field(default_factory=list, max_length=20000)

    @model_validator(mode='after')
    def coherent(self) -> Self:
        if self.status == 'unknown' and (self.bpm is not None or self.confidence is not None or self.map):
            raise ValueError('unknown_tempo_has_no_estimate')
        if self.status == 'known' and self.bpm is None:
            raise ValueError('known_tempo_requires_bpm')
        _ordered([point.start_sec for point in self.map])
        return self


class MeterEstimate(MusicAnalysisContract):
    status: Literal['known', 'unknown'] = 'unknown'
    beats_per_bar: int | None = Field(default=None, ge=1, le=32)
    confidence: float | None = Field(default=None, ge=0, le=1)
    method: MusicAnalysisMethod = 'unavailable'

    @model_validator(mode='after')
    def coherent(self) -> Self:
        if self.status == 'unknown' and (self.beats_per_bar is not None or self.confidence is not None):
            raise ValueError('unknown_meter_has_no_estimate')
        if self.status == 'known' and self.beats_per_bar is None:
            raise ValueError('known_meter_requires_count')
        return self


class KeyCandidate(MusicAnalysisContract):
    tonic_pc: int = Field(ge=0, le=11)
    mode: Literal['major', 'minor']
    score: float = Field(ge=-1, le=1)


class KeyEstimate(MusicAnalysisContract):
    status: Literal['known', 'unknown'] = 'unknown'
    tonic_pc: int | None = Field(default=None, ge=0, le=11)
    mode: Literal['major', 'minor'] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    method: MusicAnalysisMethod = 'unavailable'
    candidates: list[KeyCandidate] = Field(default_factory=list, max_length=24)
    unknown_reason: Literal['insufficient_evidence', 'silence', 'short_audio', 'ambiguous',
        'tuning_unsupported', 'channel_disagreement', 'unstable'] | None = 'insufficient_evidence'

    @model_validator(mode='after')
    def coherent(self) -> Self:
        if self.status == 'unknown' and (self.tonic_pc is not None or self.mode is not None or self.confidence is not None or self.unknown_reason is None):
            raise ValueError('unknown_key_has_no_estimate')
        if self.status == 'known' and (self.tonic_pc is None or self.mode is None or self.unknown_reason is not None):
            raise ValueError('known_key_requires_tonic_and_mode')
        if len({(item.tonic_pc, item.mode) for item in self.candidates}) != len(self.candidates):
            raise ValueError('duplicate_key_candidate')
        return self


class AnalysisInterval(MusicAnalysisContract):
    start_sec: MusicAnalysisSeconds
    end_sec: float = Field(gt=0, le=7200)

    @model_validator(mode='after')
    def forward(self) -> Self:
        if self.end_sec <= self.start_sec:
            raise ValueError('analysis_interval_not_forward')
        return self


class KeyChange(AnalysisInterval):
    key: KeyEstimate


class AnalysisSection(AnalysisInterval):
    id: MusicAnalysisId
    group: str = Field(min_length=1, max_length=32, pattern=r'^[A-Z][0-9]*$')
    label: str = Field(min_length=1, max_length=32, pattern=r'^[A-Z][0-9]*$')
    musical_label: Literal['intro', 'verse', 'chorus', 'bridge', 'outro', 'instrumental'] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    bars: int | None = Field(default=None, ge=1, le=10000)


class VocalInterval(AnalysisInterval):
    confidence: float | None = Field(default=None, ge=0, le=1)


def _intervals(values: Sequence[AnalysisInterval], duration: float = 7200) -> None:
    previous = 0.0
    for value in values:
        if value.start_sec < previous or value.end_sec > duration:
            raise ValueError('analysis_intervals_overlap_or_exceed_duration')
        previous = value.end_sec


class VocalActivity(MusicAnalysisContract):
    status: Literal['known', 'unknown'] = 'unknown'
    method: MusicAnalysisMethod = 'unavailable'
    intervals: list[VocalInterval] = Field(default_factory=list, max_length=10000)

    @model_validator(mode='after')
    def coherent(self) -> Self:
        if self.status == 'unknown' and self.intervals:
            raise ValueError('unknown_vocal_activity_has_no_intervals')
        _intervals(self.intervals)
        return self


class AnalysisEnergyPoint(MusicAnalysisContract):
    time_sec: MusicAnalysisSeconds
    rms_dbfs: float = Field(ge=-200, le=25)
    centroid_hz: float = Field(ge=0, le=96000)
    overlap_band_power: float = Field(ge=0, le=1)


class MusicAnalysisFeatures(MusicAnalysisContract):
    duration_sec: float = Field(gt=0, le=7200)
    analysis_sample_rate_hz: int = Field(ge=8000, le=22050)
    hop_sec: float = Field(gt=0, le=1)
    method: Literal['numpy-summary-v1'] = 'numpy-summary-v1'
    tempo: TempoEstimate = Field(default_factory=TempoEstimate)
    beats_sec: MusicAnalysisTimes = Field(default_factory=list)
    downbeats_sec: MusicAnalysisTimes = Field(default_factory=list)
    meter: MeterEstimate = Field(default_factory=MeterEstimate)
    key: KeyEstimate = Field(default_factory=KeyEstimate)
    key_changes: list[KeyChange] = Field(default_factory=list, max_length=1024)
    sections: list[AnalysisSection] = Field(default_factory=list, max_length=1024)
    vocal_activity: VocalActivity = Field(default_factory=VocalActivity)
    energy: list[AnalysisEnergyPoint] = Field(default_factory=list, max_length=4000)
    warnings: list[MusicAnalysisWarning] = Field(default_factory=list, max_length=32)

    @model_validator(mode='after')
    def timelines(self) -> Self:
        for values in (self.beats_sec, self.downbeats_sec, [point.start_sec for point in self.tempo.map], [point.time_sec for point in self.energy]):
            _ordered(values)
            if values and values[-1] >= self.duration_sec:
                raise ValueError('analysis_time_outside_duration')
        for downbeat in self.downbeats_sec:
            index = bisect_left(self.beats_sec, downbeat)
            if not any(abs(beat - downbeat) <= self.hop_sec for beat in self.beats_sec[max(0, index - 1):index + 1]):
                raise ValueError('downbeat_requires_nearby_beat')
        for intervals in (self.sections, self.key_changes, self.vocal_activity.intervals):
            _intervals(intervals, self.duration_sec)
        if len({section.id for section in self.sections}) != len(self.sections):
            raise ValueError('duplicate_section_id')
        if any(point.centroid_hz > self.analysis_sample_rate_hz / 2 for point in self.energy):
            raise ValueError('centroid_above_analysis_nyquist')
        if 'silence' in self.warnings and (self.beats_sec or self.downbeats_sec or self.tempo.status == 'known' or self.key.status == 'known' or self.meter.status == 'known'):
            raise ValueError('silence_has_no_musical_grid')
        return self


class AnalysisEngine(MusicAnalysisContract):
    task: Literal['features', 'key', 'sections', 'beats', 'loudness', 'vocal_activity']
    name: MusicAnalysisMethod
    revision: MusicAnalysisMethod
    configuration_sha256: MusicAnalysisSha256
    weights_sha256: MusicAnalysisSha256 | None = None
    device: Literal['cpu'] = 'cpu'
    precision: Literal['float32', 'float64'] = 'float64'


class AnalysisResult(MusicAnalysisFeatures):
    schema_version: Literal[1] = 1
    id: MusicAnalysisId
    source: MusicSourceRef
    audio_sha256: MusicAnalysisSha256
    sample_rate_hz: int = Field(ge=8000, le=192000)
    channels: int = Field(ge=1, le=2)
    duration_frames: int = Field(ge=1, le=1382400000)
    analyzer_revision: MusicAnalysisMethod
    configuration_sha256: MusicAnalysisSha256
    weights_sha256: MusicAnalysisSha256 | None = None
    loudness: AudioMetrics
    engines: list[AnalysisEngine] = Field(default_factory=list, max_length=16)
    license_evidence_ids: list[Annotated[str, Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9_.:-]+$')]] = Field(default_factory=list, max_length=64)
    created_at: MusicAnalysisTimestamp
    override_revision: int = Field(default=0, ge=0, le=9007199254740991)

    @field_validator('loudness', mode='before')
    @classmethod
    def strict_loudness(cls, value: object) -> AudioMetrics:
        return AudioMetrics.model_validate(value, strict=True)

    @model_validator(mode='after')
    def source_identity(self) -> Self:
        if not math.isclose(self.duration_sec, self.duration_frames / self.sample_rate_hz, rel_tol=1e-12, abs_tol=1e-9):
            raise ValueError('analysis_duration_frames_mismatch')
        if self.loudness.source_sha256 != self.audio_sha256 or not math.isclose(self.loudness.duration_sec, self.duration_sec, abs_tol=1 / self.sample_rate_hz, rel_tol=0):
            raise ValueError('analysis_loudness_source_mismatch')
        if self.source.kind != 'version' and self.source.sha256 != self.audio_sha256:
            raise ValueError('analysis_reference_hash_mismatch')
        return self


class MusicAnalysisRequest(MusicAnalysisContract):
    source: MusicSourceRef
    profile: Literal['music-v1', 'music-beats-v1'] = 'music-v1'


class MusicAnalysisStoragePreview(MusicAnalysisContract):
    source_bytes: int = Field(ge=0, le=9223372036854775807)
    retained_bytes: int = Field(ge=0, le=9223372036854775807)
    temporary_bytes: int = Field(ge=0, le=9223372036854775807)
    required_free_bytes: int = Field(ge=0, le=9223372036854775807)
    free_bytes: int | None = Field(default=None, ge=0, le=9223372036854775807)
    can_start: bool


class MusicAnalysisProgress(MusicAnalysisContract):
    current: int = Field(ge=0, le=9007199254740991)
    total: int = Field(ge=1, le=9007199254740991)
    unit: Literal['windows', 'frames', 'bytes']

    @model_validator(mode='after')
    def bounded(self) -> Self:
        if self.current > self.total:
            raise ValueError('analysis_progress_exceeds_total')
        return self


class MusicAnalysisJob(MusicAnalysisContract):
    id: MusicAnalysisId
    source: MusicSourceRef
    status: Literal['queued', 'running', 'done', 'failed', 'cancelled', 'interrupted'] = 'queued'
    phase: Literal['waiting', 'snapshot', 'decode', 'beats', 'features', 'loudness', 'publishing', 'complete'] = 'waiting'
    attempt: int = Field(default=1, ge=1, le=9007199254740991)
    cancellation_requested: bool = False
    progress: MusicAnalysisProgress | None = None
    created_at: MusicAnalysisTimestamp
    started_at: MusicAnalysisTimestamp | None = None
    finished_at: MusicAnalysisTimestamp | None = None
    error_code: MusicAnalysisErrorCode | None = None
    result: AnalysisResult | None = None

    @model_validator(mode='after')
    def state(self) -> Self:
        if self.status == 'done' and (self.result is None or self.phase != 'complete' or self.error_code is not None):
            raise ValueError('completed_analysis_requires_result')
        if self.status != 'done' and self.result is not None:
            raise ValueError('analysis_result_requires_completed_job')
        if self.status == 'failed' and self.error_code is None:
            raise ValueError('failed_analysis_requires_error')
        if self.result is not None and self.result.source != self.source:
            raise ValueError('analysis_job_source_mismatch')
        return self


class MusicAnalysisOverrides(MusicAnalysisContract):
    analysis_id: MusicAnalysisId
    expected_revision: int = Field(ge=0, le=9007199254740991)
    beats_sec: MusicAnalysisTimes | None = None
    downbeats_sec: MusicAnalysisTimes | None = None
    tempo: TempoEstimate | None = None
    meter: MeterEstimate | None = None
    key: KeyEstimate | None = None
    sections: list[AnalysisSection] | None = Field(default=None, max_length=1024)

    @model_validator(mode='after')
    def changes(self) -> Self:
        if all(value is None for value in (self.beats_sec, self.downbeats_sec, self.tempo, self.meter, self.key, self.sections)):
            raise ValueError('analysis_override_has_no_changes')
        if self.sections is not None:
            _intervals(self.sections)
        return self


MUSIC_ANALYSIS_CLIENT_MODELS: list[type[BaseModel]] = [VersionMusicSource, StemMusicSource, ProductionMusicSource,
    TempoPoint, TempoEstimate, MeterEstimate, KeyCandidate, KeyEstimate, KeyChange, AnalysisSection,
    VocalInterval, VocalActivity, AnalysisEnergyPoint, MusicAnalysisFeatures, AnalysisEngine, AnalysisResult,
    MusicAnalysisRequest, MusicAnalysisStoragePreview, MusicAnalysisProgress, MusicAnalysisJob, MusicAnalysisOverrides]
