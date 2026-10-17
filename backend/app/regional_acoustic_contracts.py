"""Path-free, source-bound candidate evidence, never recording identity claims."""
from __future__ import annotations

from typing import Literal, Self
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .contracts import Contract
from .music_analysis_contracts import MusicAnalysisId, MusicAnalysisSha256, MusicAnalysisTimestamp
from .module_contracts import LicenseDate

AcousticErrorCode = Literal['regional_dataset_invalid', 'regional_dataset_source_changed', 'regional_dataset_source_missing', 'regional_dataset_limit',
    'regional_dataset_storage_unavailable', 'regional_dataset_cancelled', 'regional_dataset_interrupted',
    'regional_dataset_worker_unverified', 'regional_dataset_revision_conflict', 'insufficient_disk']
AcousticLimitation = Literal['heuristic_uncalibrated', 'selected_excerpts_only', 'pitch_and_time_changes_may_be_missed',
    'window_boundaries_limited', 'insufficient_information']


def default_limitations() -> list[AcousticLimitation]:
    return ['heuristic_uncalibrated', 'selected_excerpts_only', 'pitch_and_time_changes_may_be_missed', 'window_boundaries_limited']


class AcousticContract(Contract):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)


class RegionalAcousticScanRequest(AcousticContract):
    dataset_id: MusicAnalysisId
    review_revision: int = Field(ge=1, le=9007199254740991)
    review_sha256: MusicAnalysisSha256
    submission_key: MusicAnalysisId


class RegionalAcousticExcerpt(AcousticContract):
    excerpt_id: MusicAnalysisId
    recording_id: MusicAnalysisId
    source_sha256: MusicAnalysisSha256
    decoded_pcm_sha256: MusicAnalysisSha256
    sample_rate_hz: int = Field(ge=8000, le=192000)
    channels: Literal[1, 2]
    start_frame: int = Field(ge=0, le=1382400000)
    end_frame: int = Field(ge=1, le=1382400000)
    coverage: Literal['screened', 'insufficient_information']
    landmark_count: int = Field(ge=0, le=2000000)

    @model_validator(mode='after')
    def valid_span(self) -> Self:
        if not 0 < self.end_frame - self.start_frame <= self.sample_rate_hz * 600:
            raise ValueError('regional_dataset_invalid')
        return self


class RegionalAcousticAudition(AcousticContract):
    sha256: MusicAnalysisSha256
    bytes: int = Field(ge=1, le=32 * 1024**2)
    sample_rate_hz: Literal[8000] = 8000
    channels: Literal[1, 2]
    frames: int = Field(ge=1, le=96000)
    source_start_frame: int = Field(ge=0, le=1382400000)
    source_end_frame: int = Field(ge=1, le=1382400000)


class RegionalAcousticCandidate(AcousticContract):
    id: MusicAnalysisSha256
    left_excerpt_id: MusicAnalysisId
    right_excerpt_id: MusicAnalysisId
    left_channel: int = Field(ge=0, le=1)
    right_channel: int = Field(ge=0, le=1)
    left_start_frame: int = Field(ge=0, le=1382400000)
    left_end_frame: int = Field(ge=1, le=1382400000)
    right_start_frame: int = Field(ge=0, le=1382400000)
    right_end_frame: int = Field(ge=1, le=1382400000)
    offset_tolerance_sec: float = Field(default=.256, ge=0, le=1)
    aligned_seconds: float = Field(ge=3, le=600)
    matched_landmarks: int = Field(ge=12, le=100000)
    distinct_anchors: int = Field(ge=6, le=20000)
    spectral_agreement: float = Field(ge=-1, le=1)
    left_audition: RegionalAcousticAudition | None = None
    right_audition: RegionalAcousticAudition | None = None
    audition_unavailable_reason: Literal['not_retained', 'capacity'] | None = 'not_retained'

    @model_validator(mode='after')
    def paired_auditions(self) -> Self:
        if (self.left_audition is None) != (self.right_audition is None):
            raise ValueError('regional_dataset_invalid')
        if self.left_audition is not None and self.right_audition is not None:
            if (self.audition_unavailable_reason is not None
                    or not self.left_start_frame <= self.left_audition.source_start_frame < self.left_audition.source_end_frame <= self.left_end_frame
                    or not self.right_start_frame <= self.right_audition.source_start_frame < self.right_audition.source_end_frame <= self.right_end_frame):
                raise ValueError('regional_dataset_invalid')
        return self


class RegionalAcousticReport(AcousticContract):
    schema_version: Literal[1] = 1
    method_revision: Literal['regional-landmarks-v1'] = 'regional-landmarks-v1'
    configuration_sha256: MusicAnalysisSha256
    selection_sha256: MusicAnalysisSha256
    excerpts: list[RegionalAcousticExcerpt] = Field(min_length=1, max_length=100)
    candidates: list[RegionalAcousticCandidate] = Field(default_factory=list, max_length=4950)
    limitations: list[AcousticLimitation] = Field(default_factory=default_limitations, max_length=10)

    @model_validator(mode='after')
    def bound_candidates(self) -> Self:
        excerpts = {item.excerpt_id: item for item in self.excerpts}
        pairs: set[tuple[str, str]] = set()
        if len(excerpts) != len(self.excerpts) or len({item.id for item in self.candidates}) != len(self.candidates):
            raise ValueError('regional_dataset_invalid')
        for item in self.candidates:
            left, right = excerpts.get(item.left_excerpt_id), excerpts.get(item.right_excerpt_id)
            pair = (min(item.left_excerpt_id, item.right_excerpt_id), max(item.left_excerpt_id, item.right_excerpt_id))
            if (left is None or right is None or left.excerpt_id == right.excerpt_id or pair in pairs
                    or left.recording_id == right.recording_id or item.left_channel >= left.channels
                    or item.right_channel >= right.channels
                    or left.coverage != 'screened' or right.coverage != 'screened'
                    or not left.start_frame <= item.left_start_frame < item.left_end_frame <= left.end_frame
                    or not right.start_frame <= item.right_start_frame < item.right_end_frame <= right.end_frame):
                raise ValueError('regional_dataset_invalid')
            if ((item.left_audition is not None and item.left_audition.channels != left.channels)
                    or (item.right_audition is not None and item.right_audition.channels != right.channels)):
                raise ValueError('regional_dataset_invalid')
            pairs.add(pair)
        return self


class RegionalAcousticResult(AcousticContract):
    sha256: MusicAnalysisSha256
    report: RegionalAcousticReport


class RegionalAcousticDecision(AcousticContract):
    candidate_id: MusicAnalysisSha256
    decision: Literal['keep_together', 'distinct_after_review']
    reviewed_by: str = Field(min_length=1, max_length=120, pattern=r'\S')
    reviewed_at: LicenseDate
    note: str = Field(min_length=1, max_length=2000, pattern=r'\S')


class RegionalAcousticReview(AcousticContract):
    scan_id: MusicAnalysisId
    scan_sha256: MusicAnalysisSha256
    decisions: list[RegionalAcousticDecision] = Field(default_factory=list, max_length=4950)

    @model_validator(mode='after')
    def unique_decisions(self) -> Self:
        if len({item.candidate_id for item in self.decisions}) != len(self.decisions):
            raise ValueError('regional_dataset_invalid')
        return self


class RegionalAcousticJob(AcousticContract):
    id: MusicAnalysisId
    request: RegionalAcousticScanRequest
    status: Literal['queued', 'running', 'done', 'failed', 'cancelled', 'interrupted']
    created_at: MusicAnalysisTimestamp
    result: RegionalAcousticResult | None = None
    error_code: AcousticErrorCode | None = None

    @model_validator(mode='after')
    def completed_evidence(self) -> Self:
        if (self.status == 'done') != (self.result is not None) or self.status == 'done' and self.error_code is not None:
            raise ValueError('regional_dataset_invalid')
        return self


class RegionalAcousticJobs(AcousticContract):
    jobs: list[RegionalAcousticJob] = Field(max_length=100)


REGIONAL_ACOUSTIC_CLIENT_MODELS: list[type[BaseModel]] = [RegionalAcousticScanRequest, RegionalAcousticJob,
    RegionalAcousticJobs, RegionalAcousticResult, RegionalAcousticReport, RegionalAcousticReview]
