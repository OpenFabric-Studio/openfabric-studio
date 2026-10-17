"""Descriptive sampled spectra and accompaniment similarity, never singer clearance."""
from __future__ import annotations

import math
from statistics import median
from typing import Literal, Self

from pydantic import Field, model_validator

from .music_analysis_contracts import MusicAnalysisContract, MusicAnalysisId, MusicAnalysisSha256
from .music_dsp_contracts import EqBand
from .replacement_pitch_contracts import DiagnosticArtifactIdentity, DiagnosticWindow

TonalRole = Literal['source_vocal', 'backing', 'new_vocal', 'matched_vocal']
TonalFrequency = Literal[125, 250, 500, 1000, 2000, 4000]
TonalUnknown = Literal['missing_new_vocal', 'insufficient_active_audio', 'insufficient_bands',
    'bandwidth_unavailable', 'timeline_unmapped', 'no_variable_signal']
TonalWarning = Literal['sampled_passages_only', 'amplitude_activity_not_voicing',
    'spectral_difference_not_quality', 'vowels_and_timbre_affect_spectra',
    'correlation_not_singer_detection', 'shared_instrumentation_can_correlate',
    'low_correlation_not_absence_of_bleed', 'correlation_thresholds_uncalibrated',
    'lag_ambiguous', 'user_review_required', 'overlapping_timeline_only']
TonalErrorCode = Literal['replacement_tonal_invalid_audio', 'replacement_tonal_source_changed',
    'replacement_tonal_unavailable', 'replacement_tonal_budget_exceeded']


class ReplacementTonalWorkerRequest(MusicAnalysisContract):
    """Private owner manifest. Paths never appear in the public report."""
    plan_id: MusicAnalysisId
    revision: int = Field(ge=1, le=2147483647)
    artifact_root: str = Field(min_length=1, max_length=4096)
    source_vocal: DiagnosticArtifactIdentity
    backing: DiagnosticArtifactIdentity
    new_vocal: DiagnosticArtifactIdentity | None = None
    new_vocal_aligned: bool = False
    matched_vocal: DiagnosticArtifactIdentity | None = None

    @model_validator(mode='after')
    def matched_timeline(self) -> Self:
        if self.matched_vocal is not None and (self.new_vocal is None or not self.new_vocal_aligned):
            raise ValueError('tonal_matched_timeline_missing')
        return self


class TonalArtifactBasis(MusicAnalysisContract):
    role: TonalRole
    identity: DiagnosticArtifactIdentity
    windows: list[DiagnosticWindow] = Field(min_length=1, max_length=12)
    active_samples: int = Field(ge=0, le=1323000)

    @model_validator(mode='after')
    def windows_bounded(self) -> Self:
        previous = 0
        frames = 0
        for window in self.windows:
            if (window.start_frame < previous or window.end_frame > self.identity.duration_frames
                    or window.end_frame - window.start_frame > 5 * self.identity.sample_rate_hz):
                raise ValueError('tonal_window_outside_audio')
            previous = window.end_frame
            frames += window.end_frame - window.start_frame
        if frames > 60 * self.identity.sample_rate_hz or self.active_samples > math.ceil(frames / self.identity.sample_rate_hz * 22050):
            raise ValueError('tonal_sample_budget_exceeded')
        return self


class TonalBand(MusicAnalysisContract):
    frequency_hz: TonalFrequency
    status: Literal['supported', 'unsupported']
    reason: TonalUnknown | None = None
    reference_db: float | None = Field(default=None, ge=-160, le=0)
    new_db: float | None = Field(default=None, ge=-160, le=0)
    matched_db: float | None = Field(default=None, ge=-160, le=0)
    difference_db: float | None = Field(default=None, ge=-160, le=160)
    suggested_gain_db: float = Field(default=0.0, ge=-6, le=6)

    @model_validator(mode='after')
    def supported_measurement(self) -> Self:
        measurements = (self.reference_db, self.new_db, self.matched_db, self.difference_db)
        if self.status == 'unsupported' and (self.reason is None or any(value is not None for value in measurements)
                or self.suggested_gain_db != 0):
            raise ValueError('tonal_unsupported_measurement')
        if self.status == 'supported' and (self.reason is not None or self.reference_db is None
                or self.new_db is None or self.difference_db is None
                or not math.isclose(self.reference_db - self.new_db, self.difference_db, abs_tol=1e-8, rel_tol=0)):
            raise ValueError('tonal_band_support_missing')
        return self


class MatchingEqDiagnostic(MusicAnalysisContract):
    status: Literal['available', 'inconclusive']
    reason: TonalUnknown | None = None
    bands: list[TonalBand] = Field(min_length=6, max_length=6)
    suggested_eq_bands: list[EqBand] = Field(default_factory=list, max_length=6)
    before_difference_db: float | None = Field(default=None, ge=0, le=160)
    after_difference_db: float | None = Field(default=None, ge=0, le=160)
    requires_review: Literal[True] = True

    @model_validator(mode='after')
    def bounded_suggestion(self) -> Self:
        if [band.frequency_hz for band in self.bands] != [125, 250, 500, 1000, 2000, 4000]:
            raise ValueError('tonal_band_order_invalid')
        frequencies = [band.frequency_hz for band in self.suggested_eq_bands]
        supported = {band.frequency_hz for band in self.bands if band.status == 'supported'}
        if len(set(frequencies)) != len(frequencies) or not set(frequencies).issubset(supported):
            raise ValueError('tonal_suggestion_band_invalid')
        if sum(max(0.0, band.gain_db) for band in self.suggested_eq_bands) > 6 + 1e-9:
            raise ValueError('tonal_eq_boost_budget')
        suggestions = {band.frequency_hz: band.gain_db for band in self.suggested_eq_bands}
        if any(not math.isclose(band.suggested_gain_db, suggestions.get(band.frequency_hz, 0.0),
                abs_tol=1e-9, rel_tol=0) for band in self.bands):
            raise ValueError('tonal_suggestion_measurement_mismatch')
        if self.status == 'inconclusive' and (self.reason is None or self.suggested_eq_bands
                or self.before_difference_db is not None or self.after_difference_db is not None):
            raise ValueError('tonal_inconclusive_suggestion')
        if self.status == 'available' and (self.reason is not None or len(supported) < 3 or self.before_difference_db is None):
            raise ValueError('tonal_suggestion_support_missing')
        return self


class BleedWindow(MusicAnalysisContract):
    window_index: int = Field(ge=0, le=11)
    active_samples: int = Field(ge=22050, le=110250)
    vocal_channel: int = Field(ge=0, le=1)
    backing_channel: int = Field(ge=0, le=1)
    signed_correlation: float = Field(ge=-1, le=1)
    absolute_correlation: float = Field(ge=0, le=1)
    lag_frames: int = Field(ge=-5512, le=5512)
    lag_ms: float = Field(ge=-250, le=250)
    polarity: Literal['positive', 'inverted']
    lag_ambiguous: bool = False

    @model_validator(mode='after')
    def correlation_identity(self) -> Self:
        if (not math.isclose(abs(self.signed_correlation), self.absolute_correlation, abs_tol=1e-8, rel_tol=0)
                or not math.isclose(self.lag_frames / 22050 * 1000, self.lag_ms, abs_tol=1e-8, rel_tol=0)
                or (self.signed_correlation < 0) != (self.polarity == 'inverted')):
            raise ValueError('tonal_correlation_identity_invalid')
        return self


class BleedDiagnostic(MusicAnalysisContract):
    vocal_artifact_id: MusicAnalysisId
    backing_artifact_id: MusicAnalysisId
    status: Literal['measured', 'inconclusive']
    reason: TonalUnknown | None = None
    correlation_strength: Literal['weak', 'medium', 'high', 'inconclusive']
    median_absolute_correlation: float | None = Field(default=None, ge=0, le=1)
    medium_threshold: float = Field(default=.3, ge=.3, le=.3)
    high_threshold: float = Field(default=.6, ge=.6, le=.6)
    windows: list[BleedWindow] = Field(default_factory=list, max_length=12)
    confidence: None = None
    interpretation: Literal['accompaniment_similarity_only'] = 'accompaniment_similarity_only'

    @model_validator(mode='after')
    def measured_support(self) -> Self:
        value = self.median_absolute_correlation
        if self.status == 'inconclusive' and (self.reason is None or value is not None or self.windows
                or self.correlation_strength != 'inconclusive'):
            raise ValueError('tonal_inconclusive_correlation')
        if self.status == 'measured':
            if self.reason is not None or value is None or not self.windows:
                raise ValueError('tonal_correlation_support_missing')
            expected = 'high' if value >= self.high_threshold else 'medium' if value >= self.medium_threshold else 'weak'
            if self.correlation_strength != expected:
                raise ValueError('tonal_correlation_strength_invalid')
            if not math.isclose(value, median(window.absolute_correlation for window in self.windows), abs_tol=1e-8, rel_tol=0):
                raise ValueError('tonal_correlation_summary_mismatch')
        if len({window.window_index for window in self.windows}) != len(self.windows):
            raise ValueError('tonal_duplicate_bleed_window')
        return self


class ReplacementTonalReport(MusicAnalysisContract):
    schema_version: Literal[1] = 1
    plan_id: MusicAnalysisId
    revision: int = Field(ge=1, le=2147483647)
    method: Literal['masked-octave-median-lagged-correlation-v1'] = 'masked-octave-median-lagged-correlation-v1'
    sampling_method: Literal['uniform-disjoint-5s-max60s-v1'] = 'uniform-disjoint-5s-max60s-v1'
    configuration_sha256: MusicAnalysisSha256
    analysis_sample_rate_hz: Literal[22050] = 22050
    ffmpeg_version: str = Field(min_length=1, max_length=512)
    numpy_version: str = Field(min_length=1, max_length=60)
    artifacts: list[TonalArtifactBasis] = Field(min_length=2, max_length=4)
    matching_eq: MatchingEqDiagnostic
    bleed: list[BleedDiagnostic] = Field(min_length=1, max_length=2)
    warnings: list[TonalWarning] = Field(default_factory=list, max_length=12)

    @model_validator(mode='after')
    def artifact_membership(self) -> Self:
        roles = {item.role: item for item in self.artifacts}
        if len(roles) != len(self.artifacts) or not {'source_vocal', 'backing'}.issubset(roles):
            raise ValueError('tonal_report_roles_invalid')
        for item in self.bleed:
            if (item.backing_artifact_id != roles['backing'].identity.artifact_id
                    or item.vocal_artifact_id not in {basis.identity.artifact_id for role, basis in roles.items()
                        if role in {'source_vocal', 'new_vocal'}}):
                raise ValueError('tonal_bleed_identity_invalid')
        return self


class ReplacementTonalWorkerOutcome(MusicAnalysisContract):
    input_sha256: MusicAnalysisSha256
    result: ReplacementTonalReport | None = None
    error_code: TonalErrorCode | None = None

    @model_validator(mode='after')
    def one_outcome(self) -> Self:
        if (self.result is None) == (self.error_code is None):
            raise ValueError('tonal_worker_outcome_invalid')
        return self


TONAL_CLIENT_MODELS = [ReplacementTonalReport]
