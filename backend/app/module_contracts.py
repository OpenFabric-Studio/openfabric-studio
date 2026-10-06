"""Fixed catalog and setup contracts shared with the browser."""
from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, StrictBool, model_validator

from .contracts import Contract

ModuleId = Literal['ace_step', 'yue2', 'speech', 'singing', 'separation', 'video', 'media', 'transcription', 'source_import', 'ebooks', 'kokoro', 'chatterbox', 'wan22', 'rvc', 'speaker_review']
ModuleState = Literal['unsupported', 'missing', 'partial', 'installed', 'ready']
ModuleActionKind = Literal['install', 'download', 'manual', 'verify', 'restart']
ModuleJobState = Literal['queued', 'running', 'completed', 'awaiting_manual', 'failed', 'cancelled', 'interrupted']
ModuleStepState = Literal['queued', 'running', 'verified', 'manual', 'skipped', 'failed']
LicenseScope = Literal['code', 'model', 'tool', 'auxiliary', 'source_data']


class ModuleEvidence(Contract):
    code: str = Field(max_length=80)
    detail: str = Field(max_length=500)
    verified: bool


class ModuleAction(Contract):
    kind: ModuleActionKind
    label: str = Field(max_length=120)
    detail: str = Field(max_length=1000)
    url: str | None = Field(default=None, max_length=500)


class ModuleLicenseDeclaration(Contract):
    model_config = ConfigDict(extra='forbid')
    component_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    scope: LicenseScope
    status: Literal['declared', 'unknown']
    declared_license: str | None = Field(default=None, min_length=1, max_length=120)
    source_url: str | None = Field(default=None, max_length=500, pattern=r'^https://[^\s]+$')
    notes: str = Field(max_length=1000)
    reviewed_at: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')

    @model_validator(mode='after')
    def declaration_source(self) -> ModuleLicenseDeclaration:
        if self.status == 'declared' and (self.declared_license is None or self.source_url is None):
            raise ValueError('license_source_required')
        if self.status == 'unknown' and self.declared_license is not None:
            raise ValueError('unknown_license_cannot_be_declared')
        return self


class ModuleInfo(Contract):
    id: ModuleId
    name: str = Field(max_length=80)
    description: str = Field(max_length=300)
    state: ModuleState
    supported: bool
    managed: bool
    automation: Literal['automatic', 'manual', 'unsupported']
    dependencies: list[ModuleId]
    capabilities: list[str]
    evidence: list[ModuleEvidence]
    actions: list[ModuleAction]
    estimated_download_bytes: int | None = Field(default=None, ge=0)
    restart_required: bool = False
    licenses: list[ModuleLicenseDeclaration] = Field(default_factory=list, max_length=20)


class ModuleInventory(Contract):
    platform: str
    architecture: str
    acceleration: Literal['apple_silicon', 'nvidia_unverified', 'cpu', 'unknown']
    managed_root: str
    free_bytes: int | None = Field(default=None, ge=0)
    checked_at: str
    modules: list[ModuleInfo]


class ModulePlanRequest(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    features: list[ModuleId] = Field(min_length=1, max_length=16)
    download_models: StrictBool = False

    @model_validator(mode='after')
    def unique_features(self) -> ModulePlanRequest:
        if len(set(self.features)) != len(self.features):
            raise ValueError('duplicate_module')
        return self


class ModulePlanStep(Contract):
    module_id: ModuleId
    name: str
    operation: Literal['install', 'verify', 'manual', 'unsupported']
    estimated_download_bytes: int | None = Field(default=None, ge=0)
    detail: str
    actions: list[ModuleAction]
    licenses: list[ModuleLicenseDeclaration] = Field(default_factory=list, max_length=20)


class ModulePlan(Contract):
    features: list[ModuleId]
    download_models: bool
    plan_token: str
    steps: list[ModulePlanStep]
    estimated_download_bytes: int = Field(ge=0)
    download_size_unknown: bool
    required_free_bytes: int = Field(ge=0)
    free_bytes: int | None = Field(default=None, ge=0)
    can_install: bool
    warnings: list[str]


class ModuleInstallRequest(ModulePlanRequest):
    plan_token: str = Field(pattern=r'^[0-9a-f]{64}$')


class ModuleDownloadProgress(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    artifact_name: str = Field(min_length=1, max_length=120)
    bytes_received: int = Field(ge=0, le=100_000_000_000)
    total_bytes: int | None = Field(default=None, ge=1, le=100_000_000_000)
    attempt: int = Field(ge=1, le=3)
    max_attempts: int = Field(default=3, ge=1, le=3)
    phase: Literal['downloading', 'retrying', 'verifying', 'complete']
    retry_after_sec: float | None = Field(default=None, ge=0, le=30)

    @model_validator(mode='after')
    def bounded_progress(self) -> ModuleDownloadProgress:
        if self.attempt > self.max_attempts or self.total_bytes is not None and self.bytes_received > self.total_bytes:
            raise ValueError('download_progress_invalid')
        return self


class ModuleJobStep(Contract):
    module_id: ModuleId
    name: str
    state: ModuleStepState
    detail: str
    error_code: str | None = None
    download: ModuleDownloadProgress | None = None


class ModuleInstallJob(Contract):
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    state: ModuleJobState
    created_at: str
    updated_at: str
    features: list[ModuleId]
    download_models: bool
    steps: list[ModuleJobStep]
    current_step: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    restart_required: bool = False


class ModuleJobsResponse(Contract):
    jobs: list[ModuleInstallJob]


MODULE_CLIENT_MODELS: tuple[type[Contract], ...] = (
    ModuleEvidence, ModuleAction, ModuleLicenseDeclaration, ModuleInfo, ModuleInventory, ModulePlanRequest,
    ModulePlanStep, ModulePlan, ModuleInstallRequest, ModuleDownloadProgress, ModuleJobStep, ModuleInstallJob, ModuleJobsResponse,
)
