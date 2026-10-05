"""Contracts for consent-backed speech voice profiles (talking / audiobook path)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .contracts import Contract, JsonValue

EngineHintMap = dict[str, JsonValue]

SpeechCloneStatus = Literal[
    "engine_not_installed",
    "engine_ready",
    "api_unavailable",
    "mock_completed",
    "completed",
    "failed",
]


class SpeechVoiceProfile(Contract):
    id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    name: str = Field(min_length=1, max_length=120)
    consent_confirmed: bool
    reference_audio_path: str = Field(min_length=1, max_length=1024)
    notes: str = Field(default="", max_length=2000)
    reference_transcript: str = Field(default="", max_length=2000)
    reference_language: str = Field(default="en", min_length=2, max_length=16, pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}$")
    created_at: str = Field(min_length=1, max_length=64)
    updated_at: str = Field(min_length=1, max_length=64)
    engine_hints: EngineHintMap | None = None
    starter_voice_id: str | None = Field(default=None, pattern=r"^vctk-p[0-9]{3}$")


class StarterSpeechVoice(Contract):
    id: str = Field(pattern=r"^vctk-p[0-9]{3}$")
    name: str = Field(min_length=1, max_length=120)
    language: Literal["en"] = "en"
    accent: str = Field(min_length=1, max_length=120)
    transcript: str = Field(min_length=1, max_length=2000)
    duration_seconds: float = Field(ge=3, le=10)
    sample_rate_hz: int = Field(ge=16000, le=96000)
    audio_url: str = Field(pattern=r"^/api/voice-profiles/starter-voices/vctk-p[0-9]{3}/audio$")
    source_url: Literal["https://datashare.ed.ac.uk/handle/10283/3443"]
    license_name: Literal["CC BY 4.0"]
    license_url: Literal["https://creativecommons.org/licenses/by/4.0/"]
    attribution: str = Field(min_length=1, max_length=1000)


class StarterSpeechVoicesResponse(Contract):
    voices: list[StarterSpeechVoice]


class SpeechVoiceProfilesResponse(Contract):
    profiles: list[SpeechVoiceProfile]


class PatchSpeechVoiceProfileRequest(Contract):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)
    consent_confirmed: bool | None = None
    reference_transcript: str | None = Field(default=None, max_length=2000)
    reference_language: str | None = Field(default=None, min_length=2, max_length=16, pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}$")


class SpeechCloneTrialRequest(Contract):
    profile_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    text: str = Field(min_length=1, max_length=8000)
    engine: Literal["speech", "gpt-sovits"] = "gpt-sovits"
    # Transcript of the reference clip for GPT-SoVITS; falls back to profile notes.
    prompt_text: str | None = Field(default=None, max_length=2000)
    prompt_language: str | None = Field(default=None, min_length=2, max_length=16)
    text_language: str | None = Field(default=None, min_length=2, max_length=16)


class SpeechCloneTrialResponse(Contract):
    status: SpeechCloneStatus
    detail: str
    engine: Literal["speech", "gpt-sovits"]
    profile_id: str
    install_hints: list[str] = Field(default_factory=list)
    trial_id: str | None = None
    output_path: str | None = None


class SpeechCloneEngineStatus(Contract):
    installed: bool
    mock: bool
    root: str | None = None
    api_base_url: str | None = None
    api_reachable: bool = False
    install_hints: list[str] = Field(default_factory=list)


VOICE_PROFILE_CLIENT_MODELS: list[type[BaseModel]] = [
    SpeechVoiceProfile,
    SpeechVoiceProfilesResponse,
    PatchSpeechVoiceProfileRequest,
    SpeechCloneTrialRequest,
    SpeechCloneTrialResponse,
    SpeechCloneEngineStatus,
    StarterSpeechVoice,
    StarterSpeechVoicesResponse,
]
