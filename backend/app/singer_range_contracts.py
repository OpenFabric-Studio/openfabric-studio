"""Artist-confirmed range declarations and explicitly fallible pitch screening."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from .contracts import Contract

RangeId = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$', strict=True)]
MidiNote = Annotated[int, Field(ge=0, le=127, strict=True)]
RangePolicy = Literal['warn', 'block', 'needs_approval']
RangeAnalysisStatus = Literal['observed', 'unknown']
RangeDecision = Literal['allow', 'blocked', 'needs_approval']


class RangeContract(Contract):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)


class SingerRangeRequest(RangeContract):
    comfortable_low_midi: MidiNote
    comfortable_high_midi: MidiNote
    extended_low_midi: MidiNote | None = None
    extended_high_midi: MidiNote | None = None
    policy: RangePolicy = 'warn'
    chosen_key: str = Field(default='', max_length=60, strict=True)
    operator_label: str = Field(min_length=1, max_length=120, strict=True)
    operator_declaration: str = Field(min_length=20, max_length=2000, strict=True)
    artist_confirmation_declaration: str = Field(min_length=20, max_length=2000, strict=True)

    @field_validator('operator_label', 'operator_declaration', 'artist_confirmation_declaration')
    @classmethod
    def explicit_declaration(cls, value: str) -> str:
        result = value.strip()
        if not result or '\0' in result:
            raise ValueError('range_confirmation_required')
        return result

    @model_validator(mode='after')
    def ordered_bounds(self) -> SingerRangeRequest:
        if self.comfortable_low_midi > self.comfortable_high_midi:
            raise ValueError('invalid_comfortable_range')
        if (self.extended_low_midi is None) != (self.extended_high_midi is None):
            raise ValueError('complete_extended_range_required')
        if self.extended_low_midi is not None and self.extended_high_midi is not None:
            if self.extended_low_midi > self.comfortable_low_midi or self.extended_high_midi < self.comfortable_high_midi:
                raise ValueError('extended_range_must_contain_comfortable_range')
        if len(self.operator_declaration) < 20 or len(self.artist_confirmation_declaration) < 20:
            raise ValueError('range_confirmation_required')
        return self


class SingerRangeProfile(SingerRangeRequest):
    id: RangeId
    artist_id: RangeId
    revision: int = Field(ge=1, le=9_007_199_254_740_991)
    created_at: str = Field(min_length=1, max_length=50)
    authenticated_actor: None = None
    inferred_from_pitch: Literal[False] = False


class PlannedNote(RangeContract):
    start_sec: float = Field(ge=0, le=7200)
    end_sec: float = Field(gt=0, le=7200)
    midi_note: MidiNote
    timing_basis: Literal['planned', 'observed_window'] = 'planned'
    observed_seconds: float | None = Field(default=None, ge=0, le=7200)

    @model_validator(mode='after')
    def note_interval(self) -> PlannedNote:
        if self.start_sec >= self.end_sec:
            raise ValueError('invalid_note_interval')
        if self.timing_basis == 'observed_window' and self.observed_seconds is None:
            raise ValueError('observed_occupancy_required')
        if self.observed_seconds is not None and self.observed_seconds > self.end_sec - self.start_sec + 1e-6:
            raise ValueError('invalid_observed_occupancy')
        return self


class OutOfRangeNote(PlannedNote):
    distance_semitones: int = Field(ge=1, le=127)
    outside_extended_range: bool | None = None


class RangeApproval(RangeContract):
    profile_version_id: RangeId
    assessment_sha256: str = Field(pattern=r'^[0-9a-f]{64}$', strict=True)
    evidence_ref: str = Field(min_length=1, max_length=500, strict=True)
    operator_label: str = Field(min_length=1, max_length=120, strict=True)
    artist_confirmation_declaration: str = Field(min_length=20, max_length=2000, strict=True)
    authenticated_actor: None = None

    @field_validator('evidence_ref', 'operator_label', 'artist_confirmation_declaration')
    @classmethod
    def explicit_declaration(cls, value: str) -> str:
        return SingerRangeRequest.explicit_declaration(value)

    @model_validator(mode='after')
    def confirmation_length(self) -> RangeApproval:
        if len(self.artist_confirmation_declaration) < 20:
            raise ValueError('range_confirmation_required')
        return self


class RangeAssessment(RangeContract):
    artist_id: RangeId
    profile_version_id: RangeId | None
    policy: RangePolicy
    analysis_status: RangeAnalysisStatus
    decision: RangeDecision
    assessment_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    chosen_key: str = Field(default='', max_length=60)
    audio_transposed: Literal[False] = False
    notes_evaluated: int = Field(ge=0, le=100000)
    out_of_range_notes: list[OutOfRangeNote] = Field(default_factory=list, max_length=100000)
    observed_low_midi: MidiNote | None = None
    observed_high_midi: MidiNote | None = None
    observed_reliable_voiced_sec: float = Field(default=0, ge=0, le=7200)
    approval_applied: bool = False
    warnings: list[str] = Field(default_factory=list, max_length=100)
    error_code: str = Field(default='', max_length=200)


class GuideRangeInput(RangeContract):
    artist_id: RangeId
    profile: SingerRangeProfile | None = None
    guide_path: str = Field(min_length=1, max_length=8192, strict=True)
    guide_sha256: str = Field(pattern=r'^[0-9a-f]{64}$', strict=True)
    chosen_key: str = Field(default='', max_length=60, strict=True)
    approval: RangeApproval | None = None

    @model_validator(mode='after')
    def matching_artist(self) -> GuideRangeInput:
        if self.profile is not None and self.profile.artist_id != self.artist_id:
            raise ValueError('range_artist_mismatch')
        return self


class PlannedRangeRequest(RangeContract):
    notes: list[PlannedNote] = Field(min_length=1, max_length=100000)
    approval: RangeApproval | None = None
