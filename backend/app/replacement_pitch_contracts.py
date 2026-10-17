"""Bounded sampled pitch evidence. Recommendations never change render settings."""
from __future__ import annotations

import math
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from .music_analysis_contracts import MusicAnalysisContract, MusicAnalysisId, MusicAnalysisSha256
from .music_dsp_contracts import WarpAnchor, validate_anchors

PitchRole = Literal['reference', 'candidate', 'converted', 'matched']
PitchErrorCode = Literal['pitch_input_invalid', 'pitch_artifact_changed', 'pitch_budget_exceeded',
    'pitch_runtime_unavailable', 'pitch_worker_failed', 'pitch_worker_output_invalid']
PitchUnknown = Literal['analysis_unavailable', 'analysis_failed', 'no_reliable_pitch',
    'insufficient_voiced_support', 'alignment_unreviewed', 'insufficient_paired_windows',
    'inconsistent_pitch_difference', 'shift_outside_advisory_bound', 'already_matches',
    'pitch_near_analysis_boundary']
PitchWarning = Literal['sampled_passages_only', 'observed_pitch_not_full_range',
    'voicing_probability_not_pitch_confidence', 'monophonic_estimator', 'melody_correspondence_unverified',
    'user_review_required', 'key_not_inferred', 'analysis_unavailable']


class DiagnosticArtifactIdentity(MusicAnalysisContract):
    artifact_id: MusicAnalysisId
    sha256: MusicAnalysisSha256
    sample_rate_hz: int = Field(ge=8000, le=192000)
    channels: Literal[1, 2]
    duration_frames: int = Field(ge=1, le=1382400000)

    @field_validator('channels', mode='before')
    @classmethod
    def integer_channels(cls, value: object) -> int:
        if type(value) is not int:
            raise ValueError('diagnostic_channels_invalid')
        return value

    @property
    def duration_sec(self) -> float:
        return self.duration_frames / self.sample_rate_hz

    @model_validator(mode='after')
    def duration_bound(self) -> Self:
        if self.duration_frames > self.sample_rate_hz * 7200:
            raise ValueError('diagnostic_duration_exceeded')
        return self


class DiagnosticWindow(MusicAnalysisContract):
    """Half-open native-frame interval, not an inferred note or phrase."""
    start_frame: int = Field(ge=0, le=1382400000)
    end_frame: int = Field(ge=1, le=1382400000)

    @model_validator(mode='after')
    def ordered(self) -> Self:
        if self.start_frame >= self.end_frame:
            raise ValueError('diagnostic_window_invalid')
        return self


class PitchWorkerArtifact(MusicAnalysisContract):
    identity: DiagnosticArtifactIdentity
    role: PitchRole
    timeline: Literal['reference', 'prepared'] = 'reference'
    applied_pitch_semitones: float = Field(default=0.0, ge=-12, le=12)

    @model_validator(mode='after')
    def prepared_role(self) -> Self:
        if self.timeline == 'prepared' and self.role != 'candidate':
            raise ValueError('diagnostic_timeline_invalid')
        return self


class PitchWorkerInput(MusicAnalysisContract):
    """Private owner manifest; files are artifact_root/<artifact_id>.wav only."""
    schema_version: Literal[1] = 1
    artifact_root: str = Field(min_length=1, max_length=4096)
    artifacts: list[PitchWorkerArtifact] = Field(min_length=1, max_length=4)
    alignment_anchors: list[WarpAnchor] = Field(default_factory=list, max_length=1000)
    alignment_reviewed: bool = False
    current_pitch_semitones: float = Field(default=0.0, ge=-12, le=12)
    prepared_recommendation: bool = True

    @model_validator(mode='after')
    def identities(self) -> Self:
        roles = {item.role for item in self.artifacts}
        if len(roles) != len(self.artifacts) or 'reference' not in roles:
            raise ValueError('diagnostic_roles_invalid')
        if len({item.identity.artifact_id for item in self.artifacts}) != len(self.artifacts):
            raise ValueError('diagnostic_artifacts_not_distinct')
        reference = next(item for item in self.artifacts if item.role == 'reference')
        candidate = next((item for item in self.artifacts if item.role == 'candidate'), None)
        if self.alignment_anchors:
            validate_anchors(self.alignment_anchors, 2 / 3, 1.5)
            if candidate is None or (self.alignment_anchors[-1].source_sec > candidate.identity.duration_sec
                    or self.alignment_anchors[-1].target_sec > reference.identity.duration_sec):
                raise ValueError('diagnostic_alignment_outside_audio')
        if self.alignment_reviewed and candidate is not None and candidate.timeline == 'prepared' and not self.alignment_anchors:
            raise ValueError('diagnostic_alignment_missing')
        return self


class PitchWindowObservation(MusicAnalysisContract):
    window: DiagnosticWindow
    analyzed_sec: float = Field(ge=0, le=5)
    voiced_sec: float = Field(ge=0, le=5)
    reliable_voiced_sec: float = Field(ge=0, le=5)
    pitch_p05_hz: float | None = Field(default=None, ge=50, le=2000)
    pitch_median_hz: float | None = Field(default=None, ge=50, le=2000)
    pitch_p95_hz: float | None = Field(default=None, ge=50, le=2000)
    median_voicing_probability: float | None = Field(default=None, ge=.8, le=1)

    @model_validator(mode='after')
    def support(self) -> Self:
        if not self.reliable_voiced_sec <= self.voiced_sec + 1e-9 <= self.analyzed_sec + 2e-9:
            raise ValueError('diagnostic_voiced_support_invalid')
        pitches = (self.pitch_p05_hz, self.pitch_median_hz, self.pitch_p95_hz)
        if any(value is None for value in pitches) != all(value is None for value in pitches):
            raise ValueError('diagnostic_partial_pitch')
        available = [value for value in pitches if value is not None]
        if available != sorted(available) or bool(available) != (self.reliable_voiced_sec > 0):
            raise ValueError('diagnostic_pitch_support_invalid')
        if (self.median_voicing_probability is not None) != bool(available):
            raise ValueError('diagnostic_probability_support_invalid')
        return self


class PitchArtifactReport(MusicAnalysisContract):
    identity: DiagnosticArtifactIdentity
    role: PitchRole
    applied_pitch_semitones: float = Field(ge=-12, le=12)
    status: Literal['observed', 'unknown']
    unknown_reason: PitchUnknown | None = None
    windows: list[PitchWindowObservation] = Field(default_factory=list, max_length=12)
    sampled_sec: float = Field(ge=0, le=60)
    analyzed_sec: float = Field(ge=0, le=60)
    voiced_sec: float = Field(ge=0, le=60)
    reliable_voiced_sec: float = Field(ge=0, le=60)
    # Aggregate pYIN histogram quantiles use existing one-semitone MIDI bins.
    pitch_p05_hz: float | None = Field(default=None, gt=0, le=2100)
    pitch_median_hz: float | None = Field(default=None, gt=0, le=2100)
    pitch_p95_hz: float | None = Field(default=None, gt=0, le=2100)

    @model_validator(mode='after')
    def bound_windows(self) -> Self:
        if (self.status == 'unknown') != (self.unknown_reason is not None):
            raise ValueError('diagnostic_unknown_reason_invalid')
        previous = 0
        for item in self.windows:
            if (item.window.start_frame < previous or item.window.end_frame > self.identity.duration_frames
                    or item.window.end_frame - item.window.start_frame > 5 * self.identity.sample_rate_hz
                    or item.analyzed_sec > (item.window.end_frame - item.window.start_frame) / self.identity.sample_rate_hz + 1e-8):
                raise ValueError('diagnostic_window_outside_audio')
            previous = item.window.end_frame
        expected = sum((item.window.end_frame - item.window.start_frame) / self.identity.sample_rate_hz for item in self.windows)
        if not math.isclose(self.sampled_sec, expected, rel_tol=0, abs_tol=1e-7):
            raise ValueError('diagnostic_sampled_duration_invalid')
        for name in ('analyzed_sec', 'voiced_sec', 'reliable_voiced_sec'):
            if not math.isclose(getattr(self, name), sum(getattr(item, name) for item in self.windows), rel_tol=0, abs_tol=1e-7):
                raise ValueError('diagnostic_support_sum_invalid')
        if self.analyzed_sec > self.sampled_sec + 1e-7:
            raise ValueError('diagnostic_analyzed_duration_invalid')
        pitches = [self.pitch_p05_hz, self.pitch_median_hz, self.pitch_p95_hz]
        available = [value for value in pitches if value is not None]
        if available != sorted(available):
            raise ValueError('diagnostic_pitch_quantiles_unordered')
        if self.status == 'observed' and (any(value is None for value in pitches) or self.reliable_voiced_sec < 1):
            raise ValueError('diagnostic_observation_unsupported')
        if self.status == 'unknown' and any(value is not None for value in pitches):
            raise ValueError('diagnostic_unknown_pitch')
        return self


class PitchRecommendation(MusicAnalysisContract):
    """Proposed TOTAL setting, not an increment to the current pitch setting."""
    pitch_shift_semitones: int = Field(ge=-2, le=2)
    pitch_nudge_semitones: float = Field(ge=-.5, le=.5)
    requires_review: Literal[True] = True

    @property
    def total_semitones(self) -> float:
        return self.pitch_shift_semitones + self.pitch_nudge_semitones

    @model_validator(mode='after')
    def total_bound(self) -> Self:
        if abs(self.total_semitones) > 2:
            raise ValueError('diagnostic_pitch_advisory_bound')
        return self


class PitchComparison(MusicAnalysisContract):
    kind: Literal['prepared_fit', 'conversion_drift']
    reference_artifact_id: MusicAnalysisId
    candidate_artifact_id: MusicAnalysisId
    status: Literal['observed', 'unknown', 'no_change']
    reason: PitchUnknown | None = None
    paired_windows: int = Field(ge=0, le=12)
    supported_windows: int = Field(ge=0, le=12)
    median_delta_semitones: float | None = Field(default=None, ge=-96, le=96)
    spread_semitones: float | None = Field(default=None, ge=0, le=192)
    recommendation: PitchRecommendation | None = None

    @model_validator(mode='after')
    def recommendation_support(self) -> Self:
        if self.supported_windows > self.paired_windows:
            raise ValueError('diagnostic_pair_support_invalid')
        if self.status == 'unknown' and (self.reason is None or self.recommendation is not None):
            raise ValueError('diagnostic_comparison_unknown_invalid')
        if self.recommendation is not None and (self.kind != 'prepared_fit' or self.status != 'observed'
                or self.supported_windows < 3 or self.median_delta_semitones is None or self.spread_semitones is None):
            raise ValueError('diagnostic_recommendation_unsupported')
        return self


class PitchRuntimeVersions(MusicAnalysisContract):
    python: str = Field(min_length=3, max_length=60)
    librosa: str | None = Field(default=None, min_length=1, max_length=60)
    numpy: str | None = Field(default=None, min_length=1, max_length=60)
    scipy: str | None = Field(default=None, min_length=1, max_length=60)


class ReplacementPitchReport(MusicAnalysisContract):
    schema_version: Literal[1] = 1
    method: Literal['pyin_50_2000_hz_20ms_probability_0_8'] = 'pyin_50_2000_hz_20ms_probability_0_8'
    sampling_method: Literal['uniform-disjoint-5s-max60s-v1'] = 'uniform-disjoint-5s-max60s-v1'
    configuration_sha256: MusicAnalysisSha256
    runtime: PitchRuntimeVersions
    artifacts: list[PitchArtifactReport] = Field(min_length=1, max_length=4)
    comparisons: list[PitchComparison] = Field(default_factory=list, max_length=2)
    confidence: None = None
    confidence_method: Literal['uncalibrated'] = 'uncalibrated'
    warnings: list[PitchWarning] = Field(default_factory=list, max_length=12)

    @model_validator(mode='after')
    def comparison_membership(self) -> Self:
        ids = {item.identity.artifact_id for item in self.artifacts}
        if len(ids) != len(self.artifacts) or len({item.role for item in self.artifacts}) != len(self.artifacts):
            raise ValueError('diagnostic_report_identity_invalid')
        if any(item.reference_artifact_id not in ids or item.candidate_artifact_id not in ids
                or item.reference_artifact_id == item.candidate_artifact_id for item in self.comparisons):
            raise ValueError('diagnostic_comparison_identity_invalid')
        if len({item.kind for item in self.comparisons}) != len(self.comparisons):
            raise ValueError('diagnostic_duplicate_comparison')
        by_id = {item.identity.artifact_id: item for item in self.artifacts}
        for comparison in self.comparisons:
            reference, candidate = by_id[comparison.reference_artifact_id], by_id[comparison.candidate_artifact_id]
            if ((comparison.kind == 'prepared_fit' and (reference.role != 'reference' or candidate.role != 'candidate'))
                    or (comparison.kind == 'conversion_drift' and (reference.role not in {'reference', 'candidate'}
                        or candidate.role != 'converted'))
                    or comparison.paired_windows != min(len(reference.windows), len(candidate.windows))):
                raise ValueError('diagnostic_comparison_basis_invalid')
            supported = [(a, b) for a, b in zip(reference.windows, candidate.windows)
                if a.pitch_median_hz is not None and b.pitch_median_hz is not None
                and min(a.reliable_voiced_sec, b.reliable_voiced_sec) >= 1
                and a.reliable_voiced_sec >= .5 * a.analyzed_sec and b.reliable_voiced_sec >= .5 * b.analyzed_sec]
            if comparison.supported_windows > len(supported):
                raise ValueError('diagnostic_comparison_support_forged')
            if comparison.status != 'unknown':
                if (reference.status != 'observed' or candidate.status != 'observed'
                        or comparison.supported_windows != len(supported) or len(supported) < 3
                        or sum(min(a.reliable_voiced_sec, b.reliable_voiced_sec) for a, b in supported) < 5
                        or comparison.median_delta_semitones is None or comparison.spread_semitones is None):
                    raise ValueError('diagnostic_comparison_unobserved')
                differences = sorted(12 * math.log2(a.pitch_median_hz / b.pitch_median_hz)
                    for a, b in supported if a.pitch_median_hz is not None and b.pitch_median_hz is not None)
                median = (differences[len(differences) // 2] + differences[(len(differences) - 1) // 2]) / 2
                if (not math.isclose(comparison.median_delta_semitones, median, rel_tol=0, abs_tol=1e-7)
                        or not math.isclose(comparison.spread_semitones, differences[-1] - differences[0], rel_tol=0, abs_tol=1e-7)):
                    raise ValueError('diagnostic_comparison_summary_forged')
                if comparison.recommendation is not None:
                    total = candidate.applied_pitch_semitones + median
                    if (abs(total) > 2.000001 or any(abs(value - median) > .35 for value in differences)
                            or not math.isclose(comparison.recommendation.total_semitones, round(total / .2) * .2,
                                rel_tol=0, abs_tol=1e-7)):
                        raise ValueError('diagnostic_recommendation_basis_invalid')
        return self


class ReplacementPitchWorkerOutcome(MusicAnalysisContract):
    input_sha256: MusicAnalysisSha256
    result: ReplacementPitchReport | None = None
    error_code: PitchErrorCode | None = None

    @model_validator(mode='after')
    def one_outcome(self) -> Self:
        if (self.result is None) == (self.error_code is None):
            raise ValueError('pitch_worker_outcome_invalid')
        return self
