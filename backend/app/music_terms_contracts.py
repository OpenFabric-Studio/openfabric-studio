"""Local acknowledgements describe operator choices, never legal clearance."""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, StrictBool, model_validator

from .contracts import Contract
from .module_contracts import LicenseScope, LicenseSourceUrl

MusicTermsEngine = Literal['ace_step', 'yue2', 'stable_audio3']
MusicTermsOperation = Literal['setup', 'generation', 'training', 'transcription']
TermsDigest = Annotated[str, Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')]
TermsRevision = Annotated[int, Field(ge=0, le=9_007_199_254_740_991, strict=True)]


class TermsContract(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class MusicTermsRequirement(TermsContract):
    component_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    scope: LicenseScope
    restriction: Literal['noncommercial', 'conditional']
    source_url: LicenseSourceUrl
    terms_digest: TermsDigest
    acknowledged: bool


class MusicTermsSnapshot(TermsContract):
    engine: MusicTermsEngine
    operation: MusicTermsOperation
    revision: TermsRevision
    noncommercial_enabled: bool
    requirements: list[MusicTermsRequirement] = Field(max_length=20)
    allowed: bool


class MusicTermsAcceptRequest(TermsContract):
    engine: MusicTermsEngine
    operation: MusicTermsOperation
    expected_revision: TermsRevision
    terms_digests: list[TermsDigest] = Field(max_length=20)
    allow_noncommercial: StrictBool = False

    @model_validator(mode='after')
    def unique_digests(self) -> Self:
        if len(set(self.terms_digests)) != len(self.terms_digests):
            raise ValueError('duplicate_terms_digest')
        return self


class MusicTermsSettingsRequest(TermsContract):
    expected_revision: TermsRevision
    noncommercial_enabled: StrictBool


class MusicTermsSettingsResponse(TermsContract):
    revision: TermsRevision
    noncommercial_enabled: bool


MUSIC_TERMS_CLIENT_MODELS: tuple[type[Contract], ...] = (
    MusicTermsRequirement, MusicTermsSnapshot, MusicTermsAcceptRequest,
    MusicTermsSettingsRequest, MusicTermsSettingsResponse,
)
