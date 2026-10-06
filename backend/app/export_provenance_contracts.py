"""Informative export provenance, not a signature or legal certification."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .contracts import Contract
from .audio_quality_contracts import AudioMetrics,LoudnessSettings,TargetResult

ContentOrigin=Literal['generated','mixed','recorded','unknown']


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


EXPORT_PROVENANCE_CLIENT_MODELS: list[type[BaseModel]]=[ExportProvenance,ProvenanceComponent]
