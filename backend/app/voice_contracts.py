"""Validated contracts for reviewable voice preparation and listening trials."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract

SeparationQuality = Literal["fast", "high", "roformer"]
InputKind = Literal["song", "vocal"]
VoiceWorkStatus = Literal["idle", "queued", "running", "done", "failed", "cancelled"]
VoiceProgressPhase = Literal['queued', 'inspecting', 'separating', 'normalizing', 'screening', 'cleaning', 'slicing', 'references', 'coverage', 'assembling', 'merging', 'base_model', 'waiting_gpu', 'training', 'publishing', 'complete', 'preparing', 'loading', 'analyzing', 'converting', 'mixing']
VoiceProgressUnit = Literal['files', 'samples', 'steps', 'tasks', 'chunks']
VoiceQueueReason = Literal['', 'voice_training', 'voice_conversion', 'voice_preparation', 'stem_separation', 'video_generation', 'gpu_busy']


class VoiceJobProgress(Contract):
    """Persisted wall-clock timing; estimates describe only the current phase."""
    job_id: str = Field(min_length=1, max_length=128)
    kind: Literal['preparation', 'coverage', 'build', 'apply']
    preparation_revision: str = Field(default='', max_length=128)
    status: Literal['queued', 'running', 'done', 'failed', 'cancelled'] = 'queued'
    queued_at: float = Field(ge=0, allow_inf_nan=False)
    started_at: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    finished_at: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    phase_started_at: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    observed_at: float = Field(ge=0, allow_inf_nan=False)
    phase: VoiceProgressPhase = 'queued'
    phase_current: int = Field(default=0, ge=0)
    phase_total: int = Field(default=0, ge=0)
    phase_unit: VoiceProgressUnit = 'tasks'
    files_completed: int = Field(default=0, ge=0)
    files_total: int = Field(default=0, ge=0)
    current_file: str = Field(default='', max_length=255)
    estimated_phase_remaining_sec: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    queue_reason: VoiceQueueReason = ''
    queue_label: str = Field(default='', max_length=200)

    @model_validator(mode='after')
    def valid_progress(self) -> VoiceJobProgress:
        if self.phase_current > self.phase_total or self.files_completed > self.files_total:
            raise ValueError('invalid_progress_counts')
        if self.started_at is not None and self.started_at < self.queued_at:
            raise ValueError('invalid_progress_timestamps')
        if self.finished_at is not None and self.finished_at < (self.started_at or self.queued_at):
            raise ValueError('invalid_progress_timestamps')
        return self


class VoiceSourceSelection(Contract):
    filename: str = Field(min_length=1, max_length=255)
    enabled: bool = True
    kind: InputKind = "song"


class VoiceSeparationOption(Contract):
    id: SeparationQuality
    available: bool
    reason: str = ""


class VoiceSeparationOptionsResponse(Contract):
    options: list[VoiceSeparationOption]


class PrepareVoiceRequest(Contract):
    sources: list[VoiceSourceSelection] = Field(default_factory=list, max_length=100)
    separation_quality: SeparationQuality = "fast"
    clean: bool = False
    singer_confirmed: bool = False
    max_selected_seconds: float = Field(default=900, ge=60, le=3600)


class AnalyzeVoiceCoverageRequest(Contract):
    revision: str = Field(min_length=1, max_length=128)


class VoicePitchBin(Contract):
    midi_note: int = Field(ge=0, le=127)
    seconds: float = Field(ge=0)


class VoiceCoverageMeasurement(Contract):
    duration_sec: float = Field(ge=0)
    analyzed_sec: float = Field(ge=0)
    voiced_sec: float = Field(ge=0)
    reliable_voiced_sec: float = Field(ge=0)
    pitch_p05_hz: float | None = Field(default=None, gt=0)
    pitch_median_hz: float | None = Field(default=None, gt=0)
    pitch_p95_hz: float | None = Field(default=None, gt=0)
    median_voicing_probability: float | None = Field(default=None, ge=0, le=1)
    pitch_bins: list[VoicePitchBin] = Field(default_factory=list)
    spectral_rolloff95_hz: float | None = Field(default=None, ge=0)
    high_band_energy_fraction: float | None = Field(default=None, ge=0, le=1)
    source_sample_rate: int = Field(gt=0)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def consistent_measurements(self) -> VoiceCoverageMeasurement:
        spans = (self.duration_sec, self.analyzed_sec, self.voiced_sec, self.reliable_voiced_sec)
        if any(right > left + .001 for left, right in zip(spans, spans[1:])):
            raise ValueError('invalid_coverage_durations')
        if sum(item.seconds for item in self.pitch_bins) > self.reliable_voiced_sec + .001:
            raise ValueError('invalid_pitch_duration')
        if len({item.midi_note for item in self.pitch_bins}) != len(self.pitch_bins):
            raise ValueError('duplicate_pitch_bin')
        percentiles = [value for value in (self.pitch_p05_hz, self.pitch_median_hz, self.pitch_p95_hz) if value is not None]
        if percentiles != sorted(percentiles):
            raise ValueError('invalid_pitch_percentiles')
        return self


class VoiceCoverageSummary(Contract):
    analysis_method: Literal['pyin_50_2000_hz_20ms_probability_0_8'] = 'pyin_50_2000_hz_20ms_probability_0_8'
    duration_sec: float = Field(ge=0)
    analyzed_sec: float = Field(ge=0)
    voiced_sec: float = Field(ge=0)
    reliable_voiced_sec: float = Field(ge=0)
    pitch_p05_hz: float | None = Field(default=None, gt=0)
    pitch_median_hz: float | None = Field(default=None, gt=0)
    pitch_p95_hz: float | None = Field(default=None, gt=0)
    pitch_bins: list[VoicePitchBin] = Field(default_factory=list)
    measurements: int = Field(ge=0)
    unavailable_segments: int = Field(ge=0)
    spectral_rolloff95_hz: float | None = Field(default=None, ge=0)
    high_band_energy_fraction: float | None = Field(default=None, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)


class VoiceSegment(Contract):
    id: str
    source_filename: str
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)
    duration_sec: float = Field(gt=0)
    score: float = Field(ge=0, le=1)
    periodicity: float = Field(ge=0, le=1)
    level_db: float
    peak: float = Field(ge=0)
    clipped_fraction: float = Field(ge=0, le=1)
    accepted: bool
    reasons: list[str]
    has_cleaned: bool = False
    original_coverage: VoiceCoverageMeasurement | None = None
    cleaned_coverage: VoiceCoverageMeasurement | None = None


class VoiceSourceReport(Contract):
    filename: str
    duration_sec: float = Field(ge=0)
    accepted_sec: float = Field(ge=0)
    rejected_sec: float = Field(ge=0)
    error_code: str = ""


class VoiceReferenceCandidate(Contract):
    id: str
    segment_id: str
    source_filename: str
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)
    duration_sec: float = Field(gt=0)
    score: float = Field(ge=0, le=1)


class VoicePreparationResponse(Contract):
    progress: VoiceJobProgress | None = None
    revision: str = ""
    status: VoiceWorkStatus = "idle"
    operation: Literal['preparation', 'coverage'] = 'preparation'
    error_code: str = ""
    options: PrepareVoiceRequest = Field(default_factory=PrepareVoiceRequest)
    segments: list[VoiceSegment] = Field(default_factory=list)
    sources: list[VoiceSourceReport] = Field(default_factory=list)
    selected_segment_ids: list[str] = Field(default_factory=list)
    cleaned_segment_ids: list[str] = Field(default_factory=list)
    references: list[VoiceReferenceCandidate] = Field(default_factory=list)
    reference_id: str | None = None
    accepted_seconds: float = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
    coverage: VoiceCoverageSummary | None = None


class SelectVoiceSamplesRequest(Contract):
    revision: str = Field(min_length=1, max_length=128)
    segment_ids: list[str] = Field(min_length=1, max_length=1000)
    reference_id: str = Field(min_length=1, max_length=128)
    cleaned_segment_ids: list[str] = Field(default_factory=list, max_length=1000)
    max_selected_seconds: float | None = Field(default=None, ge=60, le=3600)


class VoiceModelChoice(Contract):
    id: str
    steps: int = Field(ge=0)
    kind: Literal["base", "trained"]
    resume_available: bool = False


class SelectVoiceModelRequest(Contract):
    model_id: str = Field(min_length=1, max_length=128)


class VoiceTrialSource(Contract):
    id: str
    filename: str
    duration_sec: float = Field(gt=0)


class VoiceTrialSourcesResponse(Contract):
    sources: list[VoiceTrialSource]


def default_diffusion_steps() -> list[Literal[30, 50]]:
    return [30]


class VoiceComparisonRequest(Contract):
    source_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    start_sec: float = Field(default=0, ge=0)
    duration_sec: float = Field(default=10, ge=2, le=30)
    input_kind: InputKind = "song"
    separation_quality: SeparationQuality = "fast"
    model_ids: list[str] = Field(min_length=1, max_length=4)
    reference_ids: list[str] = Field(default_factory=list, max_length=3)
    diffusion_steps: list[Literal[30, 50]] = Field(default_factory=default_diffusion_steps, min_length=1, max_length=2)
    seed: int = Field(default=42, ge=0, le=2**32 - 1)

    @model_validator(mode="after")
    def bounded_trials(self) -> VoiceComparisonRequest:
        dimensions = (self.model_ids, self.reference_ids, self.diffusion_steps)
        if any(len(values) != len(set(values)) for values in dimensions):
            raise ValueError("duplicate_trial_option")
        if len(self.model_ids) * max(1, len(self.reference_ids)) * len(self.diffusion_steps) > 12:
            raise ValueError("too_many_trials")
        return self


class VoiceTrialMetrics(Contract):
    duration_sec: float = Field(ge=0)
    duration_delta_sec: float
    level_db: float
    peak: float = Field(ge=0)
    clipped_fraction: float = Field(ge=0, le=1)


class VoiceTrialRating(Contract):
    identity: int = Field(ge=1, le=5)
    pitch: int = Field(ge=1, le=5)
    intelligibility: int = Field(ge=1, le=5)
    artifacts: int = Field(ge=1, le=5)
    notes: str = Field(default="", max_length=2000)


class VoiceComparisonTrial(Contract):
    id: str
    model_id: str
    reference_id: str
    diffusion_steps: Literal[30, 50]
    status: VoiceWorkStatus = "queued"
    error_code: str = ""
    audio_url: str = ""
    metrics: VoiceTrialMetrics | None = None
    rating: VoiceTrialRating | None = None


class VoiceComparisonResponse(Contract):
    id: str
    voice_id: str
    status: VoiceWorkStatus
    request: VoiceComparisonRequest
    source_filename: str
    trials: list[VoiceComparisonTrial]
    error_code: str = ""


class VoiceComparisonsResponse(Contract):
    comparisons: list[VoiceComparisonResponse]
