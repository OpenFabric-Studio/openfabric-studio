"""Matched native comparisons; measurements, declarations and judgments stay distinct."""
from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from .export_provenance_contracts import MusicEvidence
from .local_music_contracts import LocalMusicJob, LocalMusicRequest
from .music_analysis_contracts import MusicAnalysisId, MusicAnalysisSha256, MusicAnalysisTimestamp
from .music_lora_contracts import MusicAdapter
from .module_contracts import LicenseDate
from .regional_dataset_contracts import RegionalContract, RegionalPreparedSelection
from .stable_lora_contracts import StableLoraTrainingRequest

RegionalComparisonArmId = Literal['base', 'adapter']
RegionalUnavailableMetric = Literal['fad', 'clap', 'instrument_classifier', 'cultural_authenticity']


def _unavailable_metrics() -> list[RegionalUnavailableMetric]:
    return ['fad', 'clap', 'instrument_classifier', 'cultural_authenticity']


class RegionalMeasuredModel(RegionalContract):
    file_name: str = Field(min_length=1, max_length=150, pattern=r'^[A-Za-z0-9_.-]+$')
    sha256: MusicAnalysisSha256
    bytes: int = Field(ge=1, le=100_000_000_000)


class RegionalMeasuredRuntime(RegionalContract):
    models: list[RegionalMeasuredModel] = Field(min_length=1, max_length=6)
    source_sha256: MusicAnalysisSha256
    source_file_count: int = Field(ge=1, le=500)
    python_executable_sha256: MusicAnalysisSha256
    python_version: str = Field(min_length=1, max_length=80)
    platform: str = Field(min_length=1, max_length=40)
    machine: str = Field(max_length=80)
    mlx_version: str = Field(min_length=1, max_length=120)
    numpy_version: str = Field(min_length=1, max_length=120)


class RegionalTrainingReceipt(RegionalContract):
    job_id: MusicAnalysisId
    request_sha256: MusicAnalysisSha256
    dataset_sha256: MusicAnalysisSha256
    runtime: RegionalMeasuredRuntime | None = None
    identity_status: Literal['worker_measured', 'unavailable']

    @model_validator(mode='after')
    def measured_is_present(self) -> Self:
        if (self.identity_status == 'worker_measured') != (self.runtime is not None):
            raise ValueError('regional_dataset_invalid')
        return self


class RegionalEvaluationRequest(RegionalPreparedSelection):
    submission_key: MusicAnalysisId
    adapter_id: MusicAnalysisId
    adapter_sha256: MusicAnalysisSha256
    request: LocalMusicRequest

    @model_validator(mode='after')
    def matched_generation_supported(self) -> Self:
        if self.request.adapter is None or self.request.adapter.id != self.adapter_id or self.request.conditioning is not None:
            raise ValueError('regional_dataset_invalid')
        return self


class RegionalComparisonSpec(RegionalContract):
    id: MusicAnalysisId
    created_at: MusicAnalysisTimestamp
    request: RegionalEvaluationRequest
    adapter: MusicAdapter
    training_job_id: MusicAnalysisId
    training_request: StableLoraTrainingRequest
    training_receipt: RegionalTrainingReceipt
    held_out_excerpt_ids: list[MusicAnalysisId] = Field(min_length=1, max_length=100)
    publisher_declarations: MusicEvidence
    quality_status: Literal['unverified'] = 'unverified'
    generation_runtime_identity: Literal['unverified'] = 'unverified'
    unavailable_metrics: list[RegionalUnavailableMetric] = Field(default_factory=_unavailable_metrics, max_length=4)

    @model_validator(mode='after')
    def bound_training_and_holdout(self) -> Self:
        selected = [sample.regional_manifest for sample in self.training_request.samples]
        if (self.adapter.id != self.request.adapter_id or self.adapter.sha256 != self.request.adapter_sha256
                or self.adapter.source_job_id != self.training_job_id or self.training_receipt.job_id != self.training_job_id
                or self.training_request.variant != self.request.request.variant or self.adapter.target != self.request.request.variant
                or len(set(self.held_out_excerpt_ids)) != len(self.held_out_excerpt_ids)
                or any(item is None or item.manifest_id != self.request.manifest_id
                    or item.manifest_sha256 != self.request.manifest_sha256 or item.excerpt_id in self.held_out_excerpt_ids for item in selected)):
            raise ValueError('regional_dataset_holdout')
        return self


class RegionalComparisonPlan(RegionalContract):
    sha256: MusicAnalysisSha256
    spec: RegionalComparisonSpec


class RegionalComparisonArm(RegionalContract):
    arm: RegionalComparisonArmId
    job: LocalMusicJob | None = None
    output_sha256: MusicAnalysisSha256 | None = None
    audio_url: str | None = Field(default=None, pattern=r'^/api/music/regional-datasets/comparisons/[0-9a-f]{32}/audio/(base|adapter)$', max_length=160)
    submission_unverified: bool = False


class RegionalComparisonState(RegionalContract):
    plan: RegionalComparisonPlan
    arms: list[RegionalComparisonArm] = Field(min_length=2, max_length=2)
    listener_reviews: int = Field(ge=0, le=10000)


class RegionalComparisons(RegionalContract):
    comparisons: list[RegionalComparisonState] = Field(max_length=100)


class RegionalListeningReview(RegionalContract):
    comparison_sha256: MusicAnalysisSha256
    base_output_sha256: MusicAnalysisSha256
    adapter_output_sha256: MusicAnalysisSha256
    reviewer_reference: str = Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]*$')
    reviewed_at: LicenseDate
    tradition_scope: str = Field(min_length=1, max_length=200, pattern=r'\S')
    preference: Literal['base', 'adapter', 'tie', 'neither']
    timbre: int = Field(ge=1, le=5)
    phrasing: int = Field(ge=1, le=5)
    rhythm: int = Field(ge=1, le=5)
    coherence: int = Field(ge=1, le=5)
    artifacts: int = Field(ge=1, le=5)
    cultural_appropriateness: int = Field(ge=1, le=5)
    notes: str = Field(default='', max_length=4000)
    public_attribution: str = Field(default='', max_length=200)
    attribution_consented: bool = False

    @model_validator(mode='after')
    def attribution_has_consent(self) -> Self:
        if self.public_attribution and not self.attribution_consented:
            raise ValueError('regional_dataset_invalid')
        return self


REGIONAL_EVALUATION_CLIENT_MODELS: list[type[BaseModel]] = [RegionalTrainingReceipt, RegionalEvaluationRequest,
    RegionalComparisonPlan, RegionalComparisonState, RegionalComparisons, RegionalListeningReview]
