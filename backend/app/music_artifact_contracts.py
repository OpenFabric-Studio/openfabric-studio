"""Bounded storage review and explicit cleanup; no caller supplies a path."""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import Contract
from .music_analysis_contracts import MusicAnalysisId, MusicAnalysisSha256, MusicAnalysisTimestamp, MusicSourceRef

ArtifactBytes = Annotated[int, Field(ge=0, le=9007199254740991)]
ArtifactName = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[a-zA-Z0-9_.:-]+$')]
ArtifactReason = Literal['active_job', 'owner_busy', 'worker_unverified', 'protected_media', 'protected_dataset',
    'protected_adapter', 'protected_model', 'protected_checkpoint', 'ar_training_parent', 'source_missing',
    'source_changed', 'unsafe_path', 'unrecognized_files', 'referenced', 'incomplete_inventory', 'metadata_invalid',
    'cleanup_pending']
ArtifactErrorCode = Literal['music_artifact_not_found', 'music_artifact_invalid', 'music_artifact_busy',
    'music_artifact_limit', 'music_artifact_plan_changed', 'music_artifact_confirmation_required',
    'music_artifact_source_missing', 'music_artifact_source_changed', 'music_artifact_worker_unverified',
    'music_artifact_unsafe_path', 'music_artifact_referenced', 'music_artifact_storage_unavailable']


class ArtifactContract(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True, frozen=True)


class MusicArtifactDependency(ArtifactContract):
    kind: Literal['analysis_job', 'analysis_result', 'track', 'production_asset', 'training_job', 'dataset', 'adapter', 'module', 'cleanup_plan']
    id: ArtifactName


class MusicArtifactGroup(ArtifactContract):
    id: ArtifactName
    kind: Literal['analysis_workspace', 'generation_workspace', 'training_workspace', 'dataset', 'registered_adapter',
        'model', 'installer_cache', 'original_media', 'cleanup_staging']
    root_alias: Literal['music_analysis', 'local_music', 'music_training', 'stable_training', 'datasets',
        'registered_adapters', 'library_files', 'library_models', 'module_runtime', 'cleanup_staging']
    relative_path: str | None = Field(default=None, max_length=256)
    size_bytes: ArtifactBytes | None = None
    size_complete: bool = False
    disposition: Literal['review', 'retained', 'blocked']
    reasons: list[ArtifactReason] = Field(default_factory=list, max_length=32)
    dependencies: list[MusicArtifactDependency] = Field(default_factory=list, max_length=128)

    @field_validator('relative_path')
    @classmethod
    def relative_only(cls, value: str | None) -> str | None:
        if value is not None and (not value or value.startswith(('/', '\\')) or '\\' in value
                or ':' in value.split('/')[0] or any(part in {'', '.', '..'} for part in value.split('/'))):
            raise ValueError('artifact_relative_path_required')
        return value


class MusicArtifactInventory(ArtifactContract):
    schema_version: Literal[1] = 1
    generated_at: MusicAnalysisTimestamp
    groups: list[MusicArtifactGroup] = Field(max_length=1100)
    scan_complete: bool


class MusicArtifactCleanupRequest(ArtifactContract):
    analysis_job_ids: list[MusicAnalysisId] = Field(min_length=1, max_length=20)

    @model_validator(mode='after')
    def unique(self) -> Self:
        if len(set(self.analysis_job_ids)) != len(self.analysis_job_ids):
            raise ValueError('artifact_selection_not_unique')
        return self


class MusicArtifactCleanupItem(ArtifactContract):
    job_id: MusicAnalysisId
    source: MusicSourceRef | None = None
    result_id: MusicAnalysisId | None = None
    source_sha256: MusicAnalysisSha256 | None = None
    bytes: ArtifactBytes | None = None
    file_count: int = Field(default=0, ge=0, le=128)
    disposition: Literal['eligible', 'blocked']
    reasons: list[ArtifactReason] = Field(default_factory=list, max_length=32)


class MusicArtifactCleanupPlan(ArtifactContract):
    id: MusicAnalysisId
    created_at: MusicAnalysisTimestamp
    plan_sha256: MusicAnalysisSha256
    status: Literal['pending', 'executing', 'completed', 'partial'] = 'pending'
    items: list[MusicArtifactCleanupItem] = Field(min_length=1, max_length=20)


class ConfirmMusicArtifactCleanup(ArtifactContract):
    plan_sha256: MusicAnalysisSha256
    confirm_cleanup: Literal[True]

    @field_validator('confirm_cleanup', mode='before')
    @classmethod
    def explicit_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError('artifact_confirmation_required')
        return value


class MusicArtifactCleanupOutcome(ArtifactContract):
    job_id: MusicAnalysisId
    status: Literal['removed', 'pending', 'retained']
    removed_bytes: ArtifactBytes = 0
    error_code: ArtifactErrorCode | None = None


class MusicArtifactCleanupReceipt(ArtifactContract):
    plan_id: MusicAnalysisId
    plan_sha256: MusicAnalysisSha256
    status: Literal['completed', 'partial', 'blocked']
    items: list[MusicArtifactCleanupOutcome] = Field(min_length=1, max_length=20)


MUSIC_ARTIFACT_CLIENT_MODELS: list[type[BaseModel]] = [MusicArtifactDependency, MusicArtifactGroup,
    MusicArtifactInventory, MusicArtifactCleanupRequest, MusicArtifactCleanupItem, MusicArtifactCleanupPlan,
    ConfirmMusicArtifactCleanup, MusicArtifactCleanupOutcome, MusicArtifactCleanupReceipt]
