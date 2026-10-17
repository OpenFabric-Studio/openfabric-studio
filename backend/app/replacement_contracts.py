"""Revision-bound replacement plans. Public identities never contain paths."""
from __future__ import annotations

import math
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

from .audio_quality_contracts import AudioMetrics
from .music_analysis_contracts import (MusicAnalysisContract, MusicAnalysisId, MusicAnalysisSha256,
    MusicAnalysisStoragePreview, MusicAnalysisTimestamp, MusicSourceRef)
from .music_dsp_contracts import AlignmentResult, DspWarning, DspGain
from .music_analysis_contracts import MusicAnalysisMethod
from .voice_contracts import SeparationQuality, VoiceSeparationOptionsResponse
from .voice_mix_contracts import VoiceMixSettings
from .module_contracts import LicenseSourceUrl, ModuleLicenseDeclaration
from .artist_production_contracts import SourceDeclaration
from .replacement_diagnostics_contracts import ReplacementDiagnostics, diagnostic_digest

ReplacementRevision = Annotated[int, Field(ge=1, le=9007199254740991)]
ReplacementMode = Literal['convert', 'prepared', 'hybrid']
ReplacementStatus = Literal['draft', 'queued', 'running', 'needs_review', 'done', 'failed', 'cancelled', 'interrupted']
ReplacementPhase = Literal['inspecting', 'separating', 'analyzing', 'cleaning', 'aligning', 'converting',
    'mixing', 'measuring', 'publishing', 'complete']
ReplacementErrorCode = Literal['replacement_invalid_request', 'upload_too_large', 'invalid_audio', 'audio_too_long',
    'unsupported_audio_format', 'replacement_plan_missing', 'replacement_source_changed', 'replacement_artifact_changed',
    'replacement_model_changed', 'replacement_source_missing', 'replacement_artifact_missing', 'replacement_revision_conflict',
    'replacement_request_conflict', 'replacement_busy', 'replacement_cleanup_pending', 'replacement_engine_unavailable',
    'replacement_separator_unavailable', 'replacement_engine_incompatible', 'replacement_alignment_capacity',
    'replacement_alignment_unavailable', 'replacement_alignment_invalid', 'replacement_loudness_unavailable',
    'replacement_target_unattainable', 'replacement_output_duration_mismatch', 'replacement_output_invalid',
    'replacement_disk_full', 'replacement_storage_failed', 'replacement_storage_incompatible', 'replacement_source_in_use',
    'replacement_model_in_use', 'replacement_model_untrusted', 'replacement_evidence_required', 'replacement_interrupted',
    'replacement_timeout', 'replacement_cancelled', 'replacement_failed']
ReplacementWarning = DspWarning | Literal['replacement_loudness_unavailable', 'replacement_target_unattainable',
    'replacement_alignment_capacity', 'replacement_alignment_unavailable']
ReplacementArtifactRole = Literal['source_vocal', 'backing', 'prepared_dry', 'prepared_clean', 'aligned_dry',
    'converted_dry', 'matched_vocal', 'preview', 'master', 'impulse_response']


class SelectedSource(MusicAnalysisContract):
    source: MusicSourceRef
    audio_sha256: MusicAnalysisSha256

    @model_validator(mode='after')
    def hash_identity(self) -> Self:
        if self.source.kind != 'version' and self.source.sha256 != self.audio_sha256:
            raise ValueError('replacement_source_changed')
        return self


class ReplacementAssetImport(MusicAnalysisContract):
    request_id: MusicAnalysisId
    role: Literal['source', 'prepared', 'impulse_response']
    title: str = Field(min_length=1, max_length=300, pattern=r'\S')
    declaration: SourceDeclaration

    @field_validator('declaration', mode='before')
    @classmethod
    def reference_only(cls, value: object) -> SourceDeclaration:
        result = SourceDeclaration.model_validate(value, strict=True)
        if result.role != 'reference':
            raise ValueError('replacement_evidence_required')
        return result


class ReplacementAssetResponse(MusicAnalysisContract):
    request_id: MusicAnalysisId
    role: Literal['source', 'prepared', 'impulse_response']
    selection: SelectedSource
    evidence_asset_id: MusicAnalysisId
    track_id: int | None = Field(default=None, gt=0, le=9007199254740991)

    @model_validator(mode='after')
    def source_role(self) -> Self:
        if self.role == 'impulse_response':
            if self.track_id is not None or self.selection.source.kind != 'production_asset' or self.selection.source.asset_id != self.evidence_asset_id:
                raise ValueError('replacement_invalid_request')
        elif self.selection.source.kind != 'version' or self.selection.source.track_id != self.track_id:
            raise ValueError('replacement_invalid_request')
        return self


class SeedTarget(MusicAnalysisContract):
    engine: Literal['seed-vc']
    voice_id: MusicAnalysisId
    model_revision_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')


class RvcTarget(MusicAnalysisContract):
    engine: Literal['rvc']
    registered_model_id: MusicAnalysisId


VoiceTarget = Annotated[SeedTarget | RvcTarget, Field(discriminator='engine')]


class ReplacementFitSettings(MusicAnalysisContract):
    max_stretch: float = Field(default=1.25, ge=1, le=1.5)
    pitch_shift_semitones: int = Field(default=0, ge=-12, le=12)
    pitch_nudge_semitones: float = Field(default=0.0, ge=-1, le=1)
    cleanup: Literal['off', 'light'] = 'off'
    keep_breaths: bool = True

    @property
    def pitch_semitones(self) -> float:
        return self.pitch_shift_semitones + self.pitch_nudge_semitones

    @model_validator(mode='after')
    def native_pitch_bound(self) -> Self:
        if not -12 <= self.pitch_semitones <= 12:
            raise ValueError('replacement_pitch_exceeds_native_limit')
        return self


class ReplacementBase(MusicAnalysisContract):
    schema_version: Literal[1] = 1
    title: str = Field(min_length=1, max_length=300, pattern=r'\S')
    source: SelectedSource
    separator: SeparationQuality = 'high'
    fit: ReplacementFitSettings = Field(default_factory=ReplacementFitSettings)


class ConvertReplacementRequest(ReplacementBase):
    mode: Literal['convert']
    target: VoiceTarget


class PreparedReplacementBase(ReplacementBase):
    prepared: SelectedSource

    @model_validator(mode='after')
    def distinct_take(self) -> Self:
        if self.source.audio_sha256 == self.prepared.audio_sha256:
            raise ValueError('replacement_prepared_matches_source')
        return self


class PreparedReplacementRequest(PreparedReplacementBase):
    mode: Literal['prepared']


class HybridReplacementRequest(PreparedReplacementBase):
    mode: Literal['hybrid']
    target: VoiceTarget


ReplacementRequest = Annotated[ConvertReplacementRequest | PreparedReplacementRequest | HybridReplacementRequest,
    Field(discriminator='mode')]
replacement_request_adapter: TypeAdapter[ReplacementRequest] = TypeAdapter(ReplacementRequest)


class ReplacementMixSettings(VoiceMixSettings):
    master_level: Literal['match_original', 'custom', 'off'] = 'off'
    space: Literal['off', 'owned_ir'] = 'off'
    ir_asset_id: MusicAnalysisId | None = None
    wet: float = Field(default=0.0, ge=0, le=.25)

    @model_validator(mode='after')
    def explicit_processing(self) -> Self:
        if (self.space == 'off' and (self.ir_asset_id is not None or self.wet != 0)
                or self.space == 'owned_ir' and (self.ir_asset_id is None or self.wet == 0)):
            raise ValueError('replacement_ir_required')
        if (self.master_level == 'custom' and self.loudness.profile == 'off'
                or self.master_level != 'custom' and self.loudness.profile != 'off'):
            raise ValueError('replacement_master_level_conflict')
        if self.compression is not None and self.compression.makeup_db > 3:
            raise ValueError('replacement_makeup_gain_exceeded')
        return self


class ReplacementProcessing(MusicAnalysisContract):
    cleanup_recipe: MusicAnalysisMethod | None = None
    low_level_attenuation_db: float | None = Field(default=None, ge=0, le=3)
    applied_gain_db: DspGain | None = None
    residual_lu: float | None = Field(default=None, ge=-300, le=300)
    ir_sha256: MusicAnalysisSha256 | None = None
    ir_gain: float | None = Field(default=None, gt=0, le=1)
    wet: float | None = Field(default=None, gt=0, le=.25)
    stitch_recipe: MusicAnalysisMethod | None = None
    stitched_phrases: int = Field(default=0, ge=0, le=1000)
    native_recipes: list[MusicAnalysisMethod] = Field(default_factory=list, max_length=8)
    native_executable_sha256s: list[MusicAnalysisSha256] = Field(default_factory=list, max_length=4)
    quality_verified: Literal[False] = False

    @model_validator(mode='after')
    def complete_ir_identity(self) -> Self:
        if len({value is None for value in (self.ir_sha256, self.ir_gain, self.wet)}) > 1:
            raise ValueError('replacement_output_invalid')
        return self


class ReplacementArtifact(MusicAnalysisContract):
    id: MusicAnalysisId
    evidence_asset_id: MusicAnalysisId
    role: ReplacementArtifactRole
    sha256: MusicAnalysisSha256
    source_sha256s: list[MusicAnalysisSha256] = Field(min_length=1, max_length=8)
    settings_sha256: MusicAnalysisSha256
    duration_sec: float = Field(gt=0, le=7200)
    sample_rate_hz: int = Field(ge=8000, le=192000)
    channels: Literal[1, 2]
    alignment_revision: ReplacementRevision | None = None
    audio_url: str | None = Field(default=None, max_length=200,
        pattern=r'^/api/voices/replace/plans/[0-9a-f]{32}/artifacts/[0-9a-f]{32}/audio$')
    measurement: AudioMetrics | None = None
    processing: ReplacementProcessing | None = None

    @field_validator('measurement', mode='before')
    @classmethod
    def strict_measurement(cls, value: object) -> AudioMetrics | None:
        return None if value is None else AudioMetrics.model_validate(value, strict=True)

    @model_validator(mode='after')
    def measurement_identity(self) -> Self:
        if len(set(self.source_sha256s)) != len(self.source_sha256s):
            raise ValueError('replacement_duplicate_source_hash')
        metrics = self.measurement
        if metrics is not None and (metrics.source_sha256 != self.sha256
                or not math.isclose(metrics.duration_sec, self.duration_sec, rel_tol=0, abs_tol=1 / self.sample_rate_hz)
                or metrics.samples_analyzed != round(self.duration_sec * self.sample_rate_hz) * self.channels):
            raise ValueError('replacement_measurement_identity_mismatch')
        return self


class ReplacementRender(MusicAnalysisContract):
    id: MusicAnalysisId
    plan_id: MusicAnalysisId
    revision: ReplacementRevision
    request_id: MusicAnalysisId
    status: Literal['queued', 'running', 'done', 'failed', 'cancelled', 'interrupted'] = 'queued'
    master_artifact_id: MusicAnalysisId | None = None
    track_id: int | None = Field(default=None, gt=0, le=9007199254740991)
    track_deleted: bool = False
    settings_sha256: MusicAnalysisSha256
    created_at: MusicAnalysisTimestamp
    error_code: ReplacementErrorCode | None = None

    @model_validator(mode='after')
    def publication_identity(self) -> Self:
        if self.status == 'done' and (self.master_artifact_id is None or self.error_code is not None
                or self.track_id is None and not self.track_deleted):
            raise ValueError('replacement_render_not_published')
        if self.track_deleted and (self.status != 'done' or self.track_id is not None):
            raise ValueError('replacement_invalid_track_tombstone')
        if self.status in {'failed', 'interrupted'} and self.error_code is None:
            raise ValueError('replacement_render_error_required')
        return self


class ReplacementActionRequest(MusicAnalysisContract):
    expected_revision: ReplacementRevision
    request_id: MusicAnalysisId


class ReplacementUpdateRequest(MusicAnalysisContract):
    expected_revision: ReplacementRevision
    request: ReplacementRequest


class ReplacementMixEditRequest(MusicAnalysisContract):
    expected_revision: ReplacementRevision
    mix: ReplacementMixSettings


class AlignmentEditRequest(MusicAnalysisContract):
    expected_revision: ReplacementRevision
    alignment: AlignmentResult
    reviewed_phrase_ids: list[MusicAnalysisId] = Field(default_factory=list, max_length=1000)

    @model_validator(mode='after')
    def bounded_manual_edit(self) -> Self:
        if (len(self.alignment.anchors) > 1000 or len(self.alignment.phrases) > 1000
                or len(set(self.reviewed_phrase_ids)) != len(self.reviewed_phrase_ids)
                or not set(self.reviewed_phrase_ids).issubset({phrase.phrase_id for phrase in self.alignment.phrases})):
            raise ValueError('replacement_alignment_invalid')
        return self


class ReplacementPreviewWindow(MusicAnalysisContract):
    start_sec: float = Field(ge=0, le=7200)
    duration_sec: float = Field(gt=0, le=30)

    @model_validator(mode='after')
    def timeline_bound(self) -> Self:
        if self.start_sec + self.duration_sec > 7200:
            raise ValueError('replacement_preview_window_invalid')
        return self


class ReplacementRenderRequest(ReplacementActionRequest):
    mix: ReplacementMixSettings
    preview_window: ReplacementPreviewWindow | None = None


class ReplacementPlanResponse(MusicAnalysisContract):
    id: MusicAnalysisId
    revision: ReplacementRevision = 1
    request: ReplacementRequest
    mix: ReplacementMixSettings = Field(default_factory=ReplacementMixSettings)
    status: ReplacementStatus = 'draft'
    phase: ReplacementPhase = 'inspecting'
    active_attempt_id: MusicAnalysisId | None = None
    alignment: AlignmentResult | None = None
    reviewed_phrase_ids: list[MusicAnalysisId] = Field(default_factory=list, max_length=1000)
    alignment_reviewed: bool = False
    artifacts: list[ReplacementArtifact] = Field(default_factory=list, max_length=64)
    renders: list[ReplacementRender] = Field(default_factory=list, max_length=100)
    error_code: ReplacementErrorCode | None = None
    warnings: list[ReplacementWarning] = Field(default_factory=list, max_length=64)
    diagnostics: ReplacementDiagnostics | None = None
    updated_at: MusicAnalysisTimestamp

    @model_validator(mode='after')
    def owned_state(self) -> Self:
        if len({item.id for item in self.artifacts}) != len(self.artifacts) or len({item.id for item in self.renders}) != len(self.renders):
            raise ValueError('replacement_duplicate_artifact_or_render')
        if any(render.plan_id != self.id or render.revision > self.revision for render in self.renders):
            raise ValueError('replacement_render_owner_mismatch')
        if self.alignment_reviewed and (self.alignment is None or len(self.alignment.anchors) < 2):
            raise ValueError('replacement_alignment_review_missing')
        if self.status == 'done' and (self.phase != 'complete' or not any(render.status == 'done' for render in self.renders)):
            raise ValueError('replacement_plan_not_published')
        if self.status in {'failed', 'interrupted'} and self.error_code is None:
            raise ValueError('replacement_plan_error_required')
        report = self.diagnostics
        if report is not None:
            alignment = diagnostic_digest({'alignment': self.alignment.model_dump(mode='json'),
                'reviewed_phrase_ids': self.reviewed_phrase_ids, 'alignment_reviewed': self.alignment_reviewed}) if self.alignment else None
            if (report.revision != self.revision or report.request_sha256 != diagnostic_digest(self.request.model_dump(mode='json'))
                    or report.alignment_sha256 != alignment or report.tonal is not None and report.tonal.plan_id != self.id):
                raise ValueError('replacement_diagnostic_owner_mismatch')
            artifacts = {item.id: item for item in self.artifacts}
            identities = [item.identity for item in report.pitch.artifacts] if report.pitch else []
            if report.tonal:
                identities.extend(item.identity for item in report.tonal.artifacts)
            for identity in identities:
                artifact = artifacts.get(identity.artifact_id)
                if (artifact is None or artifact.sha256 != identity.sha256 or artifact.sample_rate_hz != identity.sample_rate_hz
                        or artifact.channels != identity.channels
                        or round(artifact.duration_sec * artifact.sample_rate_hz) != identity.duration_frames):
                    raise ValueError('replacement_diagnostic_artifact_mismatch')
        return self


class ReplacementPlanSummary(MusicAnalysisContract):
    id: MusicAnalysisId
    revision: ReplacementRevision
    title: str = Field(min_length=1, max_length=300)
    mode: ReplacementMode
    status: ReplacementStatus
    phase: ReplacementPhase
    error_code: ReplacementErrorCode | None = None
    updated_at: MusicAnalysisTimestamp


class ReplacementPlansResponse(MusicAnalysisContract):
    plans: list[ReplacementPlanSummary] = Field(default_factory=list, max_length=100)
    next_cursor: MusicAnalysisId | None = None


class ReplacementCapabilities(MusicAnalysisContract):
    native_stretch_available: bool = False
    native_uniform_max_input_sec: float = Field(default=0.0, ge=0, le=7200)
    native_mapped_max_input_sec: float = Field(default=0.0, ge=0, le=7200)
    min_duration_ratio: float = Field(default=.75, ge=.75, le=1)
    max_duration_ratio: float = Field(default=1.5, ge=1, le=1.5)
    max_pitch_shift_semitones: float = Field(default=12.0, ge=0, le=12)
    max_source_sec: float = Field(default=7200.0, gt=0, le=7200)
    alignment_max_phrase_sec: float = Field(default=30.0, gt=0, le=40)
    alignment_max_dtw_cells: int = Field(default=2000000, ge=1, le=4000000)
    alignment_requires_review: Literal[True] = True
    rvc_inference_ready: bool = False
    owned_ir_supported: bool = False
    cleanup_supported: bool = False
    separation: VoiceSeparationOptionsResponse


class ReplacementStoragePreview(MusicAnalysisStoragePreview):
    """Current source/retained/temporary budget using the shared storage shape."""


class RvcRuntimeFile(MusicAnalysisContract):
    role: Literal['engine_source', 'hubert_configuration', 'hubert_weights', 'rmvpe_weights']
    filename: str = Field(min_length=1, max_length=160, pattern=r'^[A-Za-z0-9][A-Za-z0-9._/-]*$')
    sha256: MusicAnalysisSha256

    @field_validator('filename')
    @classmethod
    def contained_name(cls, value: str) -> str:
        if any(part in {'', '.', '..'} for part in value.split('/')):
            raise ValueError('replacement_engine_incompatible')
        return value


class RvcRuntimeIdentity(MusicAnalysisContract):
    engine: Literal['rvc'] = 'rvc'
    source_revision: Literal['81eed5e8f68b6bed1789f682fe78cdd324495afc'] = '81eed5e8f68b6bed1789f682fe78cdd324495afc'
    files: list[RvcRuntimeFile] = Field(min_length=9, max_length=20)
    configuration_sha256: MusicAnalysisSha256
    environment_sha256: MusicAnalysisSha256
    runtime: str = Field(min_length=1, max_length=300)


class RvcModelImport(MusicAnalysisContract):
    request_id: MusicAnalysisId | None = None
    voice_id: MusicAnalysisId
    label: str = Field(min_length=1, max_length=160, pattern=r'\S')
    source_url: LicenseSourceUrl | None = None
    evidence_ref: str = Field(min_length=1, max_length=1000, pattern=r'\S')
    weights_license: ModuleLicenseDeclaration
    rights_declaration: str = Field(min_length=16, max_length=2000, pattern=r'\S')
    trusted_local_checkpoint: Literal[True]
    pickle_execution_acknowledged: Literal[True]
    declared_terms_acknowledged: Literal[True]
    speaker_id: int = Field(default=0, ge=0, le=1023)
    index_rate: float = Field(default=0.0, ge=0, le=1)

    @field_validator('trusted_local_checkpoint', 'pickle_execution_acknowledged', 'declared_terms_acknowledged', mode='before')
    @classmethod
    def real_acknowledgement(cls, value: object) -> bool:
        if value is not True:
            raise ValueError('replacement_model_untrusted')
        return True

    @field_validator('weights_license', mode='before')
    @classmethod
    def strict_license(cls, value: object) -> ModuleLicenseDeclaration:
        result = ModuleLicenseDeclaration.model_validate(value, strict=True)
        if result.scope != 'model':
            raise ValueError('replacement_evidence_required')
        return result


class RvcRegisteredModel(RvcModelImport):
    id: MusicAnalysisId
    engine: Literal['rvc'] = 'rvc'
    checkpoint_sha256: MusicAnalysisSha256
    index_sha256: MusicAnalysisSha256 | None = None
    observed_runtime: RvcRuntimeIdentity | None = None
    ready: bool = False
    blocked_reasons: list[ReplacementErrorCode] = Field(default_factory=list, max_length=8)
    created_at: MusicAnalysisTimestamp

    @model_validator(mode='after')
    def explicit_index(self) -> Self:
        if self.index_rate > 0 and self.index_sha256 is None:
            raise ValueError('replacement_model_changed')
        return self


class RvcModelsResponse(MusicAnalysisContract):
    models: list[RvcRegisteredModel] = Field(default_factory=list, max_length=100)


REPLACEMENT_CLIENT_MODELS: list[type[BaseModel]] = [SelectedSource, ReplacementAssetImport, ReplacementAssetResponse, SeedTarget, RvcTarget, ReplacementFitSettings,
    ReplacementDiagnostics,
    ConvertReplacementRequest, PreparedReplacementRequest, HybridReplacementRequest, ReplacementMixSettings,
    ReplacementProcessing, ReplacementArtifact, ReplacementRender, ReplacementActionRequest, ReplacementUpdateRequest, ReplacementMixEditRequest, AlignmentEditRequest,
    ReplacementPreviewWindow, ReplacementRenderRequest, ReplacementPlanResponse, ReplacementPlanSummary,
    ReplacementPlansResponse, ReplacementCapabilities, ReplacementStoragePreview,
    RvcRuntimeFile, RvcRuntimeIdentity, RvcModelImport, RvcRegisteredModel, RvcModelsResponse]
