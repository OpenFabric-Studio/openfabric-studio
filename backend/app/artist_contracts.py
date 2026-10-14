"""Artist permission evidence records local declarations, never authenticated identity."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal
from unicodedata import normalize

from pydantic import ConfigDict, Field, field_validator, model_validator

from .contracts import Contract

ArtistId = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$', strict=True)]
ArtistOperation = Literal['train', 'convert', 'export']
ArtistUse = Literal['evaluation', 'production', 'commercial']
ArtistConsentStatus = Literal['needs_review', 'active', 'revoked']
Territory = Annotated[str, Field(min_length=1, max_length=120, strict=True)]
AuthorizedOperator = Annotated[str, Field(min_length=1, max_length=120, strict=True, pattern=r'^[^\x00-\x1f\x7f]+$')]


class ArtistContract(Contract):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)


class CreateArtistRequest(ArtistContract):
    name: str = Field(min_length=1, max_length=120, strict=True, pattern=r'^[^\x00-\x1f\x7f]+$')
    voice_id: ArtistId | None = None

    @field_validator('name')
    @classmethod
    def trimmed_name(cls, value: str) -> str:
        result = value.strip()
        if not result:
            raise ValueError('artist_name_required')
        return result


class BindArtistVoiceRequest(ArtistContract):
    voice_id: ArtistId


class ArtistProfile(CreateArtistRequest):
    id: ArtistId
    created_at: str
    current_consent_version_id: ArtistId | None = None


class ArtistsResponse(ArtistContract):
    artists: list[ArtistProfile] = Field(default_factory=list)


class ArtistConsentRequest(ArtistContract):
    status: ArtistConsentStatus = 'needs_review'
    signed_evidence_ref: str | None = Field(default=None, min_length=1, max_length=2048, strict=True)
    signed_evidence_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$', strict=True)
    operator_label: str = Field(default='', max_length=120, strict=True, pattern=r'^[^\x00-\x1f\x7f]*$')
    authorized_operators: list[AuthorizedOperator] = Field(default_factory=list, max_length=100, strict=True)
    human_declaration: str = Field(default='', max_length=2000, strict=True)
    allowed_operations: list[ArtistOperation] = Field(default_factory=list, max_length=3)
    allowed_uses: list[ArtistUse] = Field(default_factory=list, max_length=3)
    territories: list[Territory] = Field(default_factory=list, max_length=100)
    starts_at: str | None = Field(default=None, max_length=50, strict=True)
    ends_at: str | None = Field(default=None, max_length=50, strict=True)

    @field_validator('signed_evidence_ref')
    @classmethod
    def evidence_reference(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError('artist_signed_evidence_reference_required')
        return value.strip() if value is not None else None

    @field_validator('operator_label', 'human_declaration')
    @classmethod
    def trimmed_evidence(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator('starts_at', 'ends_at')
    @classmethod
    def aware_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError('artist_consent_timezone_required')
        return timestamp.astimezone(timezone.utc).isoformat()

    @field_validator('territories')
    @classmethod
    def normalized_territories(cls, values: list[str]) -> list[str]:
        result = [value.strip().upper() for value in values]
        if any(not value or len(value) > 120 or not value.isprintable() for value in result):
            raise ValueError('artist_territory_invalid')
        return result

    @field_validator('authorized_operators')
    @classmethod
    def normalized_operators(cls, values: list[str]) -> list[str]:
        result = [' '.join(normalize('NFKC', value).split()) for value in values]
        if any(not value or len(value) > 120 or not value.isprintable() for value in result):
            raise ValueError('artist_operator_invalid')
        if len(result) != len({value.casefold() for value in result}):
            raise ValueError('artist_consent_duplicate_operator')
        return result

    @model_validator(mode='after')
    def permission_evidence(self) -> ArtistConsentRequest:
        for values in (self.allowed_operations, self.allowed_uses, self.territories):
            if len(values) != len(set(values)):
                raise ValueError('artist_consent_duplicate_scope')
        if self.starts_at is not None and self.ends_at is not None:
            if datetime.fromisoformat(self.starts_at) >= datetime.fromisoformat(self.ends_at):
                raise ValueError('artist_consent_invalid_interval')
        if self.signed_evidence_sha256 is not None and not self.signed_evidence_ref:
            raise ValueError('artist_signed_evidence_reference_required')
        if self.status in {'active', 'revoked'} and (not self.operator_label or len(self.human_declaration) < 20):
            raise ValueError('artist_human_declaration_required')
        if self.status == 'active' and (not self.signed_evidence_ref or not self.allowed_operations or not self.allowed_uses):
            raise ValueError('artist_signed_permission_evidence_required')
        return self


class ArtistConsentVersion(ArtistConsentRequest):
    id: ArtistId
    artist_id: ArtistId
    revision: int = Field(ge=1)
    created_at: str
    actor_authenticated: Literal[False] = False
    authenticated_actor_id: None = None


class ArtistConsentsResponse(ArtistContract):
    artist_id: ArtistId
    versions: list[ArtistConsentVersion] = Field(default_factory=list)
