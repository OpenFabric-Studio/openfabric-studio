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
    blind_reviews: int = Field(default=0, ge=0, le=10000)


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


RegionalBlindLabel = Literal['a', 'b']
RegionalBlindPreference = Literal['a', 'b', 'tie', 'neither']


class RegionalBlindSessionRequest(RegionalContract):
    reviewer_reference: str = Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]*$')


class RegionalBlindClip(RegionalContract):
    label: RegionalBlindLabel
    output_sha256: MusicAnalysisSha256
    audio_url: str = Field(pattern=r'^/api/music/regional-datasets/comparisons/[0-9a-f]{32}/blind/[A-Za-z0-9][A-Za-z0-9_-]{0,119}/audio/[ab]$', max_length=220)


class RegionalBlindSession(RegionalContract):
    comparison_id: MusicAnalysisId
    comparison_sha256: MusicAnalysisSha256
    reviewer_reference: str = Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]*$')
    assignment_sha256: MusicAnalysisSha256
    clips: list[RegionalBlindClip] = Field(min_length=2, max_length=2)

    @model_validator(mode='after')
    def unlabeled_pair(self) -> Self:
        labels = [clip.label for clip in self.clips]
        if labels != ['a', 'b'] or len({clip.audio_url for clip in self.clips}) != 2:
            raise ValueError('regional_dataset_invalid')
        if any('/audio/base' in clip.audio_url or '/audio/adapter' in clip.audio_url for clip in self.clips):
            raise ValueError('regional_dataset_invalid')
        return self


class RegionalBlindReview(RegionalContract):
    comparison_sha256: MusicAnalysisSha256
    assignment_sha256: MusicAnalysisSha256
    label_a_sha256: MusicAnalysisSha256
    label_b_sha256: MusicAnalysisSha256
    reviewer_reference: str = Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]*$')
    reviewed_at: LicenseDate
    tradition_scope: str = Field(min_length=1, max_length=200, pattern=r'\S')
    preference: RegionalBlindPreference
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


class RegionalBlindReveal(RegionalContract):
    comparison_id: MusicAnalysisId
    reviewer_reference: str = Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]*$')
    label_a_arm: RegionalComparisonArmId
    label_b_arm: RegionalComparisonArmId
    decoded_preference: Literal['base', 'adapter', 'tie', 'neither']
    quality_status: Literal['unverified'] = 'unverified'
    blind_reviews: int = Field(ge=1, le=10000)

    @model_validator(mode='after')
    def opposite_labels(self) -> Self:
        if {self.label_a_arm, self.label_b_arm} != {'base', 'adapter'}:
            raise ValueError('regional_dataset_invalid')
        return self


RegionalModelCardNoticeCode = Literal['quality_unverified', 'metrics_unavailable', 'listening_not_a_panel',
    'generation_runtime_unverified', 'distribution_not_approved']
_MODEL_CARD_NOTICES: dict[RegionalModelCardNoticeCode, str] = {
    'quality_unverified': 'Quality is unverified.',
    'metrics_unavailable': 'FAD, CLAP, instrument classification and cultural authenticity were not measured.',
    'listening_not_a_panel': 'Saved listening reviews are local records. They are not a recruited panel or a recommendation.',
    'generation_runtime_unverified': 'The generation runtime identity was not measured.',
    'distribution_not_approved': 'This card does not approve distribution, commercial use, or release.',
}


class RegionalModelCardNotice(RegionalContract):
    code: RegionalModelCardNoticeCode
    text: str = Field(min_length=1, max_length=240)


class RegionalAttributionNotice(RegionalContract):
    source: Literal['rights_record', 'listening_review', 'publisher_declaration']
    text: str = Field(min_length=1, max_length=2000)


class RegionalHashNotice(RegionalContract):
    role: Literal['source_recording', 'rights_evidence', 'prepared_excerpt', 'comparison_output']
    sha256: MusicAnalysisSha256


def regional_model_card_notices() -> list[RegionalModelCardNotice]:
    return [RegionalModelCardNotice(code=code, text=text) for code, text in _MODEL_CARD_NOTICES.items()]


class RegionalModelCard(RegionalContract):
    comparison_id: MusicAnalysisId
    comparison_sha256: MusicAnalysisSha256
    quality_status: Literal['unverified'] = 'unverified'
    distribution_status: Literal['not_approved'] = 'not_approved'
    notices: list[RegionalModelCardNotice] = Field(min_length=5, max_length=5)
    tradition: str = Field(min_length=1, max_length=120)
    manifest_id: MusicAnalysisId
    manifest_sha256: MusicAnalysisSha256
    training_recordings: int = Field(ge=0, le=100)
    holdout_recordings: int = Field(ge=0, le=100)
    excluded_recordings: int = Field(ge=0, le=100)
    training_excerpts: int = Field(ge=0, le=100)
    held_out_excerpts: int = Field(ge=1, le=100)
    declared_adapter_distribution_permitted: bool
    declared_source_redistribution_permitted: bool
    adapter_id: MusicAnalysisId
    adapter_name: str = Field(min_length=1, max_length=120)
    adapter_sha256: MusicAnalysisSha256
    adapter_target: Literal['small-music', 'medium']
    training_rank: Literal[8, 16, 32]
    training_max_steps: int = Field(ge=1, le=100000)
    training_learning_rate: float = Field(ge=0.0000001, le=0.01)
    training_seed: int = Field(ge=0, le=2147483647)
    training_request_sha256: MusicAnalysisSha256
    training_dataset_sha256: MusicAnalysisSha256
    training_identity_status: Literal['worker_measured', 'unavailable']
    runtime: RegionalMeasuredRuntime | None = None
    prompt: str = Field(min_length=1, max_length=4000)
    comparison_seed: int = Field(ge=0, le=2147483647)
    duration_seconds: int = Field(ge=5, le=380)
    base_output_sha256: MusicAnalysisSha256 | None = None
    adapter_output_sha256: MusicAnalysisSha256 | None = None
    generation_runtime_identity: Literal['unverified'] = 'unverified'
    unavailable_metrics: list[RegionalUnavailableMetric] = Field(default_factory=_unavailable_metrics, min_length=4, max_length=4)
    listener_reviews: int = Field(ge=0, le=10000)
    blind_reviews: int = Field(ge=0, le=10000)
    attributions: list[RegionalAttributionNotice] = Field(max_length=10000)
    hashes: list[RegionalHashNotice] = Field(max_length=2500)

    @model_validator(mode='after')
    def honest_card(self) -> Self:
        expected = [(item.code, item.text) for item in regional_model_card_notices()]
        if ([(item.code, item.text) for item in self.notices] != expected or self.unavailable_metrics != _unavailable_metrics()
                or (self.training_identity_status == 'worker_measured') != (self.runtime is not None)
                or self.quality_status != 'unverified' or self.distribution_status != 'not_approved'):
            raise ValueError('regional_dataset_invalid')
        return self


REGIONAL_EVALUATION_CLIENT_MODELS: list[type[BaseModel]] = [RegionalTrainingReceipt, RegionalEvaluationRequest,
    RegionalComparisonPlan, RegionalComparisonState, RegionalComparisons, RegionalListeningReview,
    RegionalBlindSessionRequest, RegionalBlindSession, RegionalBlindReview, RegionalBlindReveal, RegionalModelCard]
