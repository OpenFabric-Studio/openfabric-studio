"""Public deletion review and receipts, without machine-specific absolute paths."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StrictBool, field_validator

from .contracts import Contract

RetentionId = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]
RetentionDigest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
DeletionStatus = Literal['pending', 'executing', 'deleted']


class VoiceDeletionItem(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=8192)
    kind: Literal['recording', 'sample', 'voice_model', 'trial', 'metadata', 'directory', 'derived_track', 'audio_version', 'audio_export', 'shared_model', 'log', 'backup', 'external_copy', 'working_copy', 'source_asset']
    root_alias: Literal['library_voices', 'library_voice_work', 'library_models', 'library_files', 'catalog', 'logs', 'external']
    relative_path: str | None = Field(default=None, max_length=4096)
    track_id: int | None = Field(default=None, gt=0)
    version_id: RetentionId | None = None
    bytes: int | None = Field(default=None, ge=0)
    sha256: RetentionDigest | None = None
    action: Literal['delete', 'retain', 'unknown']
    status: Literal['planned', 'completed', 'retained', 'pending', 'external']
    error_code: str = Field(default='', max_length=100)

    @field_validator('relative_path')
    @classmethod
    def relative_only(cls, value: str | None) -> str | None:
        if value is not None and (not value or value.startswith(('/', '\\')) or '\\' in value or ':' in value.split('/')[0] or '..' in value.split('/')):
            raise ValueError('relative path required')
        return value


class VoiceDeletionPlan(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: RetentionId
    voice_id: RetentionId
    created_at: str
    status: DeletionStatus
    plan_sha256: RetentionDigest
    items: list[VoiceDeletionItem]


class ConfirmVoiceDeletionRequest(Contract):
    model_config = ConfigDict(extra='forbid')
    plan_sha256: RetentionDigest
    confirm_voice_id: RetentionId
    delete_voice_assets: StrictBool


class VoiceDeletionReceipt(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    plan_id: RetentionId
    voice_id: RetentionId
    plan_sha256: RetentionDigest
    status: DeletionStatus
    created_at: str
    sequence: int = Field(ge=1)
    items: list[VoiceDeletionItem]
    voice_assets_deleted: bool
    full_erasure_verified: bool = False
    previous_receipt_sha256: RetentionDigest | None = None
    receipt_sha256: RetentionDigest
