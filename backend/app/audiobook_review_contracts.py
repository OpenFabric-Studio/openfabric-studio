"""Typed, opt-in local narration analysis and passage QA contracts."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .contracts import Contract


class PauseAnalysisSettings(Contract):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    energy_ratio: float = Field(default=0.12, ge=0.01, le=0.5)
    min_silence_ms: int = Field(default=280, ge=40, le=2000)
    padding_ms: int = Field(default=0, ge=0, le=1000)


class AsrReviewFlag(Contract):
    code: Literal["omission", "repeat", "duration"]
    message: str = Field(min_length=1, max_length=400)


class AsrCapability(Contract):
    available: bool
    reason: str = Field(default="", max_length=200)
    setup_hint: str = Field(default="", max_length=500)


class AsrReviewRequest(Contract):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    render_identity: str = Field(pattern=r"^[0-9a-f]{64}$")


class AsrReview(Contract):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    book_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    chapter_index: int = Field(ge=0, le=99)
    passage_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    revision: int = Field(ge=1)
    render_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal["queued", "running", "completed", "unavailable", "skipped", "failed", "canceled"]
    reason: str = Field(default="", max_length=200)
    expected_text: str = Field(min_length=1, max_length=20000)
    transcript: str = Field(default="", max_length=20000)
    duration_ms: int = Field(ge=0)
    flags: list[AsrReviewFlag] = Field(default_factory=list, max_length=3)
    created_at: str = Field(min_length=1, max_length=64)
    updated_at: str = Field(min_length=1, max_length=64)


class AsrReviewsResponse(Contract):
    reviews: list[AsrReview]


AUDIOBOOK_REVIEW_CLIENT_MODELS: list[type[BaseModel]] = [PauseAnalysisSettings, AsrReviewFlag, AsrCapability,
    AsrReviewRequest, AsrReview, AsrReviewsResponse]
