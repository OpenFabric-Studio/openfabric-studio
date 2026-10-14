"""Artist production evidence: declarations are never automated legal clearance."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from .contracts import Contract, JobStatus
from .singer_range_contracts import RangeApproval, RangeAssessment, SingerRangeProfile

Identifier = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]
Sha256 = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
AssetOrigin = Literal['human_recorded', 'licensed_third_party', 'ai_generated', 'unknown']
AssetRole = Literal['guide_vocal', 'accompaniment', 'reference', 'stem', 'voice_converted', 'master', 'export']
ReviewStatus = Literal['approved', 'restricted', 'unknown']
SourceUse = Literal['evaluation', 'production', 'release']


class EvidenceContract(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class SourceDeclaration(EvidenceContract):
    role: Literal['guide_vocal', 'accompaniment', 'reference', 'stem']
    origin: AssetOrigin = 'unknown'
    provider: str = Field(default='unknown', min_length=1, max_length=120)
    rights_holder: str = Field(default='', max_length=300)
    source_artifact: str = Field(default='', max_length=300)
    original_filename: str = Field(min_length=1, max_length=200)
    acquisition_method: str = Field(default='', max_length=300)
    acquired_at: str = Field(default='', max_length=64)
    evidence_ref: str = Field(default='', max_length=500)
    permission_scope: str = Field(default='', max_length=1000)
    provider_terms_version: str = Field(default='', max_length=300)
    permitted_uses: list[SourceUse] = Field(default_factory=list, max_length=3)
    blocked_uses: list[SourceUse] = Field(default_factory=list, max_length=3)

    @model_validator(mode='after')
    def truthful_provider(self) -> SourceDeclaration:
        if self.provider.strip().lower() in {'suno', 'udio', 'ace-step', 'ace_step', 'yue2', 'openrouter'} and self.origin != 'ai_generated':
            raise ValueError('source_origin_conflict')
        if set(self.permitted_uses) & set(self.blocked_uses):
            raise ValueError('conflicting_permission_scope')
        return self


class RightsReviewRequest(EvidenceContract):
    declaration_revision_id: Identifier | None = None
    status: ReviewStatus
    reviewer_label: str = Field(min_length=1, max_length=200)
    evidence_ref: str = Field(min_length=1, max_length=500)
    note: str = Field(default='', max_length=2000)


class RightsReview(RightsReviewRequest):
    id: Identifier
    created_at: str
    authenticated_actor: None = None


class AmendSourceRequest(EvidenceContract):
    declaration: SourceDeclaration
    reviewer_label: str = Field(min_length=1, max_length=200)


class SourceDeclarationRevision(AmendSourceRequest):
    id: Identifier
    created_at: str
    authenticated_actor: None = None


class SourceAsset(EvidenceContract):
    schema_version: Literal[1] = 1
    id: Identifier
    sha256: Sha256
    role: AssetRole
    origin: AssetOrigin
    provider: str
    created_at: str
    declaration: SourceDeclaration | None = None
    classification_basis: Literal['user_declaration', 'app_workflow']
    reference_only: bool
    parent_ids: list[Identifier] = Field(default_factory=list, max_length=32)
    transformation: str = Field(default='', max_length=1000)
    reviews: list[RightsReview] = Field(default_factory=list, max_length=1000)
    declaration_revisions: list[SourceDeclarationRevision] = Field(default_factory=list, max_length=1000)


class SourceAssetsResponse(EvidenceContract):
    assets: list[SourceAsset]


class ModelFileIdentity(EvidenceContract):
    role: Literal['checkpoint', 'configuration', 'voice_reference', 'engine_source', 'installed_auxiliary_candidate']
    name: str = Field(min_length=1, max_length=300)
    sha256: Sha256
    authentication: Literal['observed_local_bytes'] = 'observed_local_bytes'


class VoiceModelSnapshot(EvidenceContract):
    voice_id: Identifier
    model_revision_id: str = Field(min_length=1, max_length=128)
    files: list[ModelFileIdentity] = Field(min_length=3, max_length=512)
    engine: Literal['seed-vc'] = 'seed-vc'
    engine_commit: str = Field(default='unknown', max_length=64)
    software_version: str = Field(default='unknown', max_length=100)
    diffusion_steps: int = Field(ge=1, le=100)
    inference_cfg_rate: float = Field(default=.7, ge=0, le=10)
    f0_condition: Literal[True] = True
    auto_f0_adjust: Literal[False] = False
    seed: int | None = None
    dependency_inventory_complete: Literal[False] = False
    bitwise_determinism_verified: Literal[False] = False
    training_steps: int = Field(default=0, ge=0)
    baseline_recorded_sha256: Sha256 | None = None
    selection_fingerprint: Sha256 | None = None
    preparation_revision: str | None = Field(default=None, max_length=128)


class CleanRenderRequest(EvidenceContract):
    artist_id: Identifier
    consent_version_id: Identifier
    voice_id: Identifier
    model_revision_id: str = Field(min_length=1, max_length=128)
    guide_asset_id: Identifier
    accompaniment_asset_id: Identifier
    title: str = Field(min_length=1, max_length=300)
    guide_singer_confirmed: StrictBool
    guide_isolated: StrictBool
    chosen_key: str = Field(default='', max_length=40)
    range_approval: RangeApproval | None = None

    @model_validator(mode='after')
    def independent_guide(self) -> CleanRenderRequest:
        if not self.guide_singer_confirmed or not self.guide_isolated:
            raise ValueError('isolated_authorized_guide_required')
        if self.guide_asset_id == self.accompaniment_asset_id:
            raise ValueError('independent_accompaniment_required')
        return self


class ReleaseReviewRequest(EvidenceContract):
    reviewer_label: str = Field(min_length=1, max_length=200)
    evidence_ref: str = Field(min_length=1, max_length=500)
    composition_rights: ReviewStatus
    master_rights: ReviewStatus
    voice_rights: ReviewStatus
    dependency_rights: ReviewStatus
    note: str = Field(default='', max_length=2000)


class ReleaseReview(ReleaseReviewRequest):
    id: Identifier
    created_at: str
    authenticated_actor: None = None


class ProductionGate(EvidenceContract):
    technical_eligible: bool = False
    approved_for_release: bool = False
    blockers: list[str] = Field(default_factory=list)
    legal_review_required: Literal[True] = True


class ProductionRun(EvidenceContract):
    schema_version: Literal[1] = 1
    id: Identifier
    project_id: int = Field(gt=0)
    request: CleanRenderRequest
    model: VoiceModelSnapshot
    created_at: str
    status: JobStatus
    track_id: int | None = None
    audio_version_id: Identifier | None = None
    output_asset_id: Identifier | None = None
    error_code: str = ''
    gate: ProductionGate = Field(default_factory=ProductionGate)
    release_reviews: list[ReleaseReview] = Field(default_factory=list)
    range_profile: SingerRangeProfile | None = None
    range_assessment: RangeAssessment | None = None


class ProductionRunsResponse(EvidenceContract):
    runs: list[ProductionRun]


class ProductionProvenance(EvidenceContract):
    schema_version: Literal[1] = 1
    evidence_visibility: Literal['local_full', 'shared_references'] = 'local_full'
    graph_sha256_scope: Literal['local_full_evidence'] = 'local_full_evidence'
    run_id: Identifier
    project_id: int
    artist_id: Identifier
    consent_version_id: Identifier
    audio_version_id: Identifier
    graph_root_id: Identifier
    graph_sha256: Sha256
    assets: list[SourceAsset] = Field(min_length=1, max_length=512)
    model: VoiceModelSnapshot
    gate: ProductionGate
    release_reviews: list[ReleaseReview] = Field(default_factory=list)
    range_assessment: RangeAssessment | None = None


class UsageArtifact(EvidenceContract):
    id: str = Field(min_length=1, max_length=200)
    sha256: Sha256


class UsageEventInput(EvidenceContract):
    idempotency_key: str = Field(min_length=1, max_length=200)
    operation: Literal['train', 'convert', 'render', 'export', 'publish']
    outcome: Literal['requested', 'done', 'failed', 'cancelled', 'interrupted']
    artist_id: Identifier
    consent_version_id: Identifier
    project_id: int | None = None
    track_id: int | None = None
    source_graph_id: Identifier | None = None
    voice_model_revision: str | None = Field(default=None, max_length=128)
    voice_model_sha256: Sha256 | None = None
    inputs: list[UsageArtifact] = Field(default_factory=list, max_length=512)
    outputs: list[UsageArtifact] = Field(default_factory=list, max_length=512)
    release_id: Identifier | None = None
    supersedes_event_id: Identifier | None = None
    error_code: str = Field(default='', max_length=100)


class ModelUsageEvent(UsageEventInput):
    schema_version: Literal[1] = 1
    id: Identifier
    created_at: str
    actor: None = None
    previous_sha256: Sha256
    sha256: Sha256


class ModelUsageLedger(EvidenceContract):
    schema_version: Literal[1] = 1
    events: list[ModelUsageEvent]
    chain_valid: bool
    head_sha256: Sha256
    authenticated_actors: Literal[False] = False


ARTIST_PRODUCTION_CLIENT_MODELS: list[type[BaseModel]] = [SourceDeclaration, RightsReviewRequest,
    SourceAsset, SourceAssetsResponse, CleanRenderRequest, ProductionRun, ProductionRunsResponse, ProductionProvenance,
    ReleaseReviewRequest, ModelUsageLedger, AmendSourceRequest]
