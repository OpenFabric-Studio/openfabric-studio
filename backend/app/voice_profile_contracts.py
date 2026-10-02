"""Contracts for consent-backed speech voice profiles (talking / audiobook path)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .contracts import Contract, JsonValue

EngineHintMap = dict[str, JsonValue]


class SpeechVoiceProfile(Contract):
    id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    name: str = Field(min_length=1, max_length=120)
    consent_confirmed: bool
    reference_audio_path: str = Field(min_length=1, max_length=1024)
    notes: str = Field(default="", max_length=2000)
    created_at: str = Field(min_length=1, max_length=64)
    updated_at: str = Field(min_length=1, max_length=64)
    engine_hints: EngineHintMap | None = None


class SpeechVoiceProfilesResponse(Contract):
    profiles: list[SpeechVoiceProfile]


class PatchSpeechVoiceProfileRequest(Contract):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)
    consent_confirmed: bool | None = None


class SpeechCloneTrialRequest(Contract):
    profile_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    text: str = Field(min_length=1, max_length=8000)
    engine: Literal["speech", "gpt-sovits"] = "gpt-sovits"


class SpeechCloneTrialResponse(Contract):
    status: Literal["engine_not_installed"]
    detail: str
    engine: Literal["speech", "gpt-sovits"]
    profile_id: str
    install_hints: list[str]


VOICE_PROFILE_CLIENT_MODELS: list[type[BaseModel]] = [
    SpeechVoiceProfile,
    SpeechVoiceProfilesResponse,
    PatchSpeechVoiceProfileRequest,
    SpeechCloneTrialRequest,
    SpeechCloneTrialResponse,
]
