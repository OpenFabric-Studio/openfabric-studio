"""Informative export provenance, not a signature or legal certification."""
from __future__ import annotations
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .contracts import Contract
from .audio_quality_contracts import AudioMetrics,LoudnessSettings,TargetResult
from .artist_production_contracts import ProductionProvenance
from .module_contracts import (LicenseCopyleft, LicenseRedistribution, LicenseRestriction, LicenseScope,
    LicenseSourceUrl, ModuleComponentIdentity, ModuleLicenseEvidence)
from .music_lora_contracts import AdapterEngine, AdapterTarget

ContentOrigin=Literal['generated','mixed','recorded','unknown']


class MusicComponentEvidence(Contract):
    """Public declarations only; private source notes are deliberately absent."""
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)
    component_id: str = Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]*$')
    scope: LicenseScope
    restriction: LicenseRestriction = 'unknown'
    redistribution: LicenseRedistribution = 'unknown'
    copyleft: LicenseCopyleft = 'unknown'
    declared_license: str | None = Field(default=None, max_length=120)
    source_url: LicenseSourceUrl | None = None
    evidence: list[ModuleLicenseEvidence] = Field(default_factory=list, max_length=20)
    identities: list[ModuleComponentIdentity] = Field(default_factory=list, max_length=40)


class MusicAdapterIdentity(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    engine: AdapterEngine
    target: AdapterTarget
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    strength: float = Field(ge=0, le=2)
    source_job_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')


class MusicEvidence(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)
    restriction: Literal['noncommercial', 'conditional', 'unknown'] = 'unknown'
    identity_basis: Literal['publisher_declared'] = 'publisher_declared'
    components: list[MusicComponentEvidence] = Field(default_factory=list, max_length=1000)
    adapters: list[MusicAdapterIdentity] = Field(default_factory=list, max_length=1000)
    lineage_complete: bool = False
    public_attributions: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(default_factory=list, max_length=1000)

    @model_validator(mode='after')
    def retain_restrictions(self) -> MusicEvidence:
        restrictions = {item.restriction for item in self.components} | {self.restriction}
        if 'noncommercial' in restrictions:
            self.restriction = 'noncommercial'
        elif 'conditional' in restrictions:
            self.restriction = 'conditional'
        return self


class GenerationIdentity(Contract):
    engine: str=Field(min_length=1,max_length=200)
    model_id: str | None=Field(default=None,max_length=200)
    engine_identity: str | None=Field(default=None,max_length=200)


class ProvenanceComponent(Contract):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    role: Literal['audio','video','conditioning_reference']
    content_origin: ContentOrigin
    source_id: str=Field(min_length=1,max_length=200)
    source_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    hash_scope: Literal['file','pcm']='file'
    classification_basis: Literal['app_workflow','user_declared','unverified']='app_workflow'
    generation_identities: list[GenerationIdentity]=Field(default_factory=list,max_length=32)
    source_records_sha256: str | None=Field(default=None,pattern=r'^[0-9a-f]{64}$')
    source_record_count: int=Field(default=0,ge=0,le=20000)
    engine: str | None=Field(default=None,max_length=200)
    model_id: str | None=Field(default=None,max_length=200)
    engine_fingerprint: str | None=Field(default=None,max_length=200)
    provider_receipt_id: str | None=Field(default=None,pattern=r'^[0-9a-f]{32}$')
    provider_job_id: str | None=Field(default=None,max_length=200)
    music_evidence: MusicEvidence | None = None


class ExportProvenance(Contract):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    schema_version: Literal[1]=1
    export_id: str=Field(min_length=1,max_length=200)
    subject: Literal['track_audio','stem','speech_trial','audiobook','video']
    created_at: str=Field(min_length=1,max_length=64)
    artifact_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    content_origin: ContentOrigin
    classification_basis: Literal['app_workflow','contains_user_declaration']='app_workflow'
    components: list[ProvenanceComponent]=Field(min_length=1,max_length=1000)
    transformations: list[str]=Field(default_factory=list,max_length=20)
    measured_audio: AudioMetrics | None=None
    audio_target: LoudnessSettings | None=None
    audio_target_result: TargetResult | None=None
    visible_ai_label: bool=False
    informational_only: Literal[True]=True
    production: ProductionProvenance | None = None
    music_evidence: MusicEvidence | None = None


EXPORT_PROVENANCE_CLIENT_MODELS: list[type[BaseModel]]=[ExportProvenance,ProvenanceComponent]
