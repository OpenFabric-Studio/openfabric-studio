"""Measurements belong to an exact replacement request and reviewed timing."""
from __future__ import annotations

import hashlib
import json
from typing import Literal, Self

from pydantic import Field, model_validator

from .music_analysis_contracts import MusicAnalysisContract, MusicAnalysisId, MusicAnalysisSha256, MusicAnalysisTimestamp
from .replacement_pitch_contracts import ReplacementPitchReport
from .replacement_tonal_contracts import ReplacementTonalReport


def diagnostic_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class ReplacementDiagnostics(MusicAnalysisContract):
    revision: int = Field(ge=1, le=9007199254740991)
    request_sha256: MusicAnalysisSha256
    alignment_sha256: MusicAnalysisSha256 | None = None
    attempt_id: MusicAnalysisId
    created_at: MusicAnalysisTimestamp
    pitch: ReplacementPitchReport | None = None
    tonal: ReplacementTonalReport | None = None
    unavailable: list[Literal['pitch', 'tonal']] = Field(default_factory=list, max_length=2)

    @model_validator(mode='after')
    def explicit_outcomes(self) -> Self:
        if len(set(self.unavailable)) != len(self.unavailable):
            raise ValueError('replacement_duplicate_diagnostic_outcome')
        if (('pitch' in self.unavailable) != (self.pitch is None)
                or ('tonal' in self.unavailable) != (self.tonal is None)):
            raise ValueError('replacement_diagnostic_outcome_missing')
        if self.tonal is not None and self.tonal.revision != self.revision:
            raise ValueError('replacement_diagnostic_revision_mismatch')
        return self
