"""Source-scoped rights declarations and immutable, explicitly separated datasets."""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import Contract
from .music_analysis_contracts import MusicAnalysisId, MusicAnalysisSha256, MusicAnalysisTimestamp
from .music_training_source_contracts import MusicLoraDatasetSource
from .module_contracts import LicenseDate, LicenseSourceUrl
from .stable_lora_contracts import StableLoraSample, StableLoraTrainingRequest
from .regional_acoustic_contracts import RegionalAcousticReview, RegionalAcousticResult

MAX_WIRE_BYTES = 9007199254740991


def _absent_acoustic(value: object) -> bool:
    """New optional evidence must not change pre-existing canonical hashes."""
    return value is None


def _legacy_acoustic(value: object) -> bool:
    return value == 'legacy_unscanned'


RegionalPermission = Literal['allowed', 'blocked', 'unknown']
RegionalDatasetErrorCode = Literal['regional_dataset_not_found', 'regional_dataset_invalid',
    'regional_dataset_revision_conflict', 'regional_dataset_source_changed', 'regional_dataset_source_missing',
    'regional_dataset_rights_required', 'regional_dataset_split_insufficient', 'regional_dataset_holdout',
    'regional_dataset_busy', 'regional_dataset_storage_unavailable', 'regional_dataset_interrupted',
    'regional_dataset_cancelled', 'regional_dataset_worker_unverified', 'regional_dataset_limit', 'insufficient_disk']
RegionalDatasetWarning = Literal['near_copy_detection_unavailable', 'provenance_incomplete',
    'independent_groups_insufficient', 'distribution_permission_missing', 'source_redistribution_not_permitted',
    'cultural_labels_user_declared', 'rights_declaration_unverified', 'holdout_fraction_approximate', 'stratification_incomplete',
    'acoustic_candidates_grouped', 'acoustic_screening_limited', 'acoustic_information_insufficient']


class RegionalDatasetError(Exception):
    def __init__(self, code: RegionalDatasetErrorCode, status: int = 409) -> None:
        self.code, self.status = code, status
        super().__init__(code)


class RegionalContract(Contract):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)


class RegionalPermissions(RegionalContract):
    reference: RegionalPermission = 'unknown'
    training: RegionalPermission = 'unknown'
    source_redistribution: RegionalPermission = 'unknown'
    adapter_distribution: RegionalPermission = 'unknown'


class RegionalRightsEvidence(RegionalContract):
    id: MusicAnalysisId
    source_sha256: MusicAnalysisSha256
    evidence_sha256: MusicAnalysisSha256
    restriction: Literal['conditional', 'unrestricted_declared', 'noncommercial', 'no_derivatives', 'unknown']
    reviewed_by: str = Field(min_length=1, max_length=120, pattern=r'\S')
    reviewed_at: LicenseDate
    permissions: RegionalPermissions
    public_url: LicenseSourceUrl | None = None
    private_reference: str | None = Field(default=None, min_length=1, max_length=120, pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]*$')
    public_attribution: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def evidence_reference(self) -> Self:
        if self.public_url is None and self.private_reference is None:
            raise ValueError('regional_dataset_rights_required')
        return self


class RegionalRecordingReview(RegionalContract):
    id: MusicAnalysisId
    source: MusicLoraDatasetSource
    source_sha256: MusicAnalysisSha256
    author: str = Field(default='', max_length=200)
    performer: str = Field(default='', max_length=200)
    acquired_at: LicenseDate | None = None
    original_filename: str = Field(default='', max_length=255, pattern=r'^[^/\\\x00-\x1f]*$')
    original_recording_sha256: MusicAnalysisSha256 | None = None
    performer_group_sha256: MusicAnalysisSha256 | None = None
    session_group_sha256: MusicAnalysisSha256 | None = None
    declared_near_copy_group_sha256: MusicAnalysisSha256 | None = None
    evaluation_only: bool = False
    caption: str = Field(min_length=1, max_length=4000, pattern=r'\S')
    captions_reviewed: bool = False
    instrument: str = Field(default='', max_length=120)
    technique: str = Field(default='', max_length=120)
    tuning: str = Field(default='', max_length=200)
    tags: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default_factory=list, max_length=20)
    rights: list[RegionalRightsEvidence] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def source_scoped_evidence(self) -> Self:
        if any(item.source_sha256 != self.source_sha256 for item in self.rights) or len({item.id for item in self.rights}) != len(self.rights):
            raise ValueError('regional_dataset_rights_required')
        return self


class RegionalExcerpt(RegionalContract):
    id: MusicAnalysisId
    recording_id: MusicAnalysisId
    start_frame: int = Field(ge=0, le=1382400000)
    end_frame: int = Field(ge=1, le=1382400000)

    @model_validator(mode='after')
    def nonempty(self) -> Self:
        if self.start_frame >= self.end_frame:
            raise ValueError('regional_dataset_invalid')
        return self


class RegionalDatasetReviewRequest(RegionalContract):
    dataset_id: MusicAnalysisId
    dataset_revision: int = Field(ge=0, le=9007199254740991)
    expected_revision: int = Field(ge=0, le=9007199254740991)
    tradition: str = Field(min_length=1, max_length=120, pattern=r'\S')
    split_seed: int = Field(default=42, ge=0, le=2147483647)
    recordings: list[RegionalRecordingReview] = Field(min_length=1, max_length=100)
    excerpts: list[RegionalExcerpt] = Field(min_length=1, max_length=100)
    acoustic_review: RegionalAcousticReview | None = Field(default=None, exclude_if=_absent_acoustic)

    @model_validator(mode='after')
    def exact_recording_selection(self) -> Self:
        identifiers = {item.id for item in self.recordings}
        if (len(identifiers) != len(self.recordings) or len({item.id for item in self.excerpts}) != len(self.excerpts)
                or any(item.recording_id not in identifiers for item in self.excerpts)
                or {item.recording_id for item in self.excerpts} != identifiers
                or any(item.source.dataset_id != self.dataset_id or item.source.revision != self.dataset_revision for item in self.recordings)):
            raise ValueError('regional_dataset_invalid')
        return self


class RegionalDatasetReview(RegionalContract):
    revision: int = Field(ge=1, le=9007199254740991)
    sha256: MusicAnalysisSha256
    created_at: MusicAnalysisTimestamp
    request: RegionalDatasetReviewRequest


class RegionalRecordingInspection(RegionalContract):
    recording_id: MusicAnalysisId
    source_sha256: MusicAnalysisSha256
    decoded_pcm_sha256: MusicAnalysisSha256
    source_bytes: int = Field(ge=1, le=512 * 1024**2)
    sample_rate_hz: int = Field(ge=8000, le=192000)
    channels: Literal[1, 2]
    frames: int = Field(ge=1, le=1382400000)


class RegionalSplitRecording(RegionalContract):
    recording_id: MusicAnalysisId
    cluster_sha256: MusicAnalysisSha256
    split: Literal['train', 'holdout', 'excluded']
    exclusion_reason: Literal['rights_unknown', 'rights_conflict', 'restricted_license', 'caption_unreviewed'] | None = None
    adapter_distribution_permitted: bool
    source_redistribution_permitted: bool


class RegionalSplitPlan(RegionalContract):
    recordings: list[RegionalSplitRecording] = Field(min_length=1, max_length=100)
    independent_groups: int = Field(ge=0, le=100)
    training_recordings: int = Field(ge=0, le=100)
    holdout_recordings: int = Field(ge=0, le=100)
    evaluation_sufficient: bool
    warnings: list[RegionalDatasetWarning] = Field(max_length=20)
    acoustic_status: Literal['legacy_unscanned', 'screened', 'insufficient_information'] = Field(
        default='legacy_unscanned', exclude_if=_legacy_acoustic)


class RegionalDatasetInspectionRequest(RegionalContract):
    recording_id: MusicAnalysisId
    source: MusicLoraDatasetSource


class RegionalDatasetPreview(RegionalContract):
    review_revision: int = Field(ge=1, le=9007199254740991)
    review_sha256: MusicAnalysisSha256
    split: RegionalSplitPlan
    source_bytes: int = Field(ge=0, le=MAX_WIRE_BYTES)
    excerpt_bytes: int = Field(ge=0, le=MAX_WIRE_BYTES)
    required_free_bytes: int = Field(ge=0, le=MAX_WIRE_BYTES)
    free_bytes: int | None = Field(default=None, ge=0, le=MAX_WIRE_BYTES)
    can_prepare: bool
    error_code: RegionalDatasetErrorCode | None = None


class RegionalManifestSelection(RegionalContract):
    dataset_id: MusicAnalysisId
    review_revision: int = Field(ge=1, le=9007199254740991)
    review_sha256: MusicAnalysisSha256


class RegionalPreparationRequest(RegionalManifestSelection):
    submission_key: MusicAnalysisId


class RegionalPreparedExcerpt(RegionalContract):
    id: MusicAnalysisId
    recording_id: MusicAnalysisId
    split: Literal['train', 'holdout']
    source_start_frame: int = Field(ge=0, le=1382400000)
    source_end_frame: int = Field(ge=1, le=1382400000)
    sample_rate_hz: int = Field(ge=8000, le=192000)
    channels: Literal[1, 2]
    frames: int = Field(ge=1, le=115200000)
    sha256: MusicAnalysisSha256
    preprocessing: Literal['decoded_float32_excerpt_v1'] = 'decoded_float32_excerpt_v1'


class RegionalManifestRecording(RegionalContract):
    review: RegionalRecordingReview
    inspection: RegionalRecordingInspection


class RegionalDatasetManifest(RegionalContract):
    schema_version: Literal[1] = 1
    id: MusicAnalysisId
    dataset_id: MusicAnalysisId
    review_revision: int = Field(ge=1, le=9007199254740991)
    review_sha256: MusicAnalysisSha256
    created_at: MusicAnalysisTimestamp
    tradition: str = Field(min_length=1, max_length=120)
    split_seed: int = Field(ge=0, le=2147483647)
    split: RegionalSplitPlan
    recordings: list[RegionalManifestRecording] = Field(min_length=1, max_length=100)
    excerpts: list[RegionalPreparedExcerpt] = Field(min_length=1, max_length=100)
    acoustic_report: RegionalAcousticResult | None = Field(default=None, exclude_if=_absent_acoustic)
    acoustic_review: RegionalAcousticReview | None = Field(default=None, exclude_if=_absent_acoustic)

    @model_validator(mode='after')
    def bound_excerpts(self) -> Self:
        recordings = {item.review.id: item for item in self.recordings}
        splits = {item.recording_id: item.split for item in self.split.recordings}
        if (len(recordings) != len(self.recordings) or len({item.id for item in self.excerpts}) != len(self.excerpts)
                or set(recordings) != set(splits)):
            raise ValueError('regional_dataset_invalid')
        for recording in self.recordings:
            if recording.review.id != recording.inspection.recording_id or recording.review.source_sha256 != recording.inspection.source_sha256:
                raise ValueError('regional_dataset_source_changed')
        for item in self.excerpts:
            source = recordings.get(item.recording_id)
            if (source is None or splits[item.recording_id] != item.split
                    or not 0 <= item.source_start_frame < item.source_end_frame <= source.inspection.frames
                    or item.frames != item.source_end_frame - item.source_start_frame
                    or item.sample_rate_hz != source.inspection.sample_rate_hz or item.channels != source.inspection.channels):
                raise ValueError('regional_dataset_invalid')
        if (self.acoustic_report is None) != (self.acoustic_review is None):
            raise ValueError('regional_dataset_source_changed')
        if self.acoustic_report is not None and self.acoustic_review is not None:
            if self.acoustic_review.scan_sha256 != self.acoustic_report.sha256:
                raise ValueError('regional_dataset_source_changed')
            candidates = {item.id: item for item in self.acoustic_report.report.candidates}
            for decision in self.acoustic_review.decisions:
                candidate = candidates.get(decision.candidate_id)
                if (candidate is None or decision.decision == 'distinct_after_review' and (
                        candidate.left_audition is None or candidate.right_audition is None)):
                    raise ValueError('regional_dataset_source_changed')
            evidence = {item.excerpt_id: item for item in self.acoustic_report.report.excerpts}
            for item in self.excerpts:
                selected = evidence.get(item.id)
                facts = recordings[item.recording_id].inspection
                if (selected is None or selected.recording_id != item.recording_id
                        or (selected.source_sha256, selected.decoded_pcm_sha256, selected.sample_rate_hz, selected.channels,
                            selected.start_frame, selected.end_frame) != (facts.source_sha256, facts.decoded_pcm_sha256,
                            facts.sample_rate_hz, facts.channels, item.source_start_frame, item.source_end_frame)):
                    raise ValueError('regional_dataset_source_changed')
        return self


class RegionalPreparedManifest(RegionalContract):
    sha256: MusicAnalysisSha256
    manifest: RegionalDatasetManifest


class RegionalPreparedSelection(RegionalContract):
    manifest_id: MusicAnalysisId
    manifest_sha256: MusicAnalysisSha256


class RegionalTrainingSelection(RegionalPreparedSelection):
    samples: list[StableLoraSample] = Field(min_length=1, max_length=100)
    held_out_excerpts: int = Field(ge=0, le=100)
    evaluation_sufficient: bool
    adapter_distribution_permitted: bool
    quality_status: Literal['unverified'] = 'unverified'


class RegionalTrainingSubmission(RegionalPreparedSelection):
    request: StableLoraTrainingRequest


class RegionalPreparationJob(RegionalContract):
    id: MusicAnalysisId
    request: RegionalPreparationRequest
    status: Literal['queued', 'running', 'done', 'failed', 'cancelled', 'interrupted']
    phase: Literal['waiting', 'snapshot', 'cropping', 'publishing', 'complete'] = 'waiting'
    created_at: MusicAnalysisTimestamp
    completed_items: int = Field(default=0, ge=0, le=200)
    total_items: int = Field(default=0, ge=0, le=200)
    result: RegionalPreparedManifest | None = None
    prepared_manifest_sha256: MusicAnalysisSha256 | None = None
    can_retry_save: bool = False
    error_code: RegionalDatasetErrorCode | None = None

    @model_validator(mode='after')
    def truthful_completion(self) -> Self:
        if self.completed_items > self.total_items or (self.status == 'done') != (self.result is not None):
            raise ValueError('regional_dataset_invalid')
        if self.status == 'done' and (self.phase != 'complete' or self.completed_items != self.total_items):
            raise ValueError('regional_dataset_invalid')
        return self


class RegionalPreparationJobs(RegionalContract):
    jobs: list[RegionalPreparationJob] = Field(max_length=100)


REGIONAL_DATASET_CLIENT_MODELS: list[type[BaseModel]] = [RegionalDatasetReviewRequest, RegionalDatasetReview,
    RegionalRecordingInspection, RegionalSplitPlan, RegionalDatasetInspectionRequest, RegionalDatasetPreview,
    RegionalManifestSelection, RegionalPreparationRequest, RegionalPreparationJob, RegionalPreparationJobs, RegionalPreparedManifest,
    RegionalPreparedSelection, RegionalTrainingSelection, RegionalTrainingSubmission]
