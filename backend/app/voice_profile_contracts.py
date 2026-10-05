"""Contracts for consent-backed speech voice profiles (talking / audiobook path)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class CloudSpeechConfiguration(Contract):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)
    """A preset sends text only; cloning is a separately permitted reference transfer."""
    model: str = Field(min_length=3, max_length=200, pattern=r"^[A-Za-z0-9._:-]+/[A-Za-z0-9._:-]+$")
    voice: str | None = Field(default=None, min_length=1, max_length=200)
    clone_reference: bool = False
    reference_transfer_confirmed: bool = False
    speed: Literal[1] = 1

    @model_validator(mode="after")
    def voice_mode(self) -> CloudSpeechConfiguration:
        if self.clone_reference == (self.voice is not None):
            raise ValueError("cloud_speech_voice_mode_required")
        return self


class CreateCloudSpeechVoiceProfileRequest(Contract):
    name: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=3, max_length=200, pattern=r"^[A-Za-z0-9._:-]+/[A-Za-z0-9._:-]+$")
    voice: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=2000)


class CloudSpeechApproval(Contract):
    quote_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    transfers_confirmed: bool


class CloudSpeechQuote(Contract):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    estimated_usd: float = Field(ge=0, le=1000000)
    request_count: int = Field(ge=0, le=20000)
    models: list[str] = Field(default_factory=list, max_length=17)
    transfers: list[Literal["text", "reference_audio", "reference_transcript"]] = Field(default_factory=list, max_length=3)
    expires_at: float = Field(ge=0)
    ceiling_is_estimate: Literal[True] = True


class CloudSpeechProvenance(Contract):
    receipt_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    profile_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    model: str = Field(min_length=3,max_length=200)
    model_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    voice: str | None = Field(default=None,max_length=200)
    reference_transferred: bool = False
    generation_id: str | None = Field(default=None,max_length=160)
    actual_cost_usd: float | None = Field(default=None,ge=0,le=1000000)


class CloudSpeechTrial(Contract):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    profile_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    created_at: str = Field(min_length=1,max_length=64)
    audio_url: str = Field(pattern=r"^/api/speech-clone/trials/[0-9a-f]{32}/audio$")
    provenance: CloudSpeechProvenance


class CloudSpeechTrialsResponse(Contract):
    trials: list[CloudSpeechTrial] = Field(default_factory=list,max_length=20)


class SpeechVoiceProfile(Contract):
    id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    name: str = Field(min_length=1, max_length=120)
    consent_confirmed: bool
    reference_audio_path: str = Field(default="", max_length=1024)
    renderer: Literal["local", "openrouter"] = "local"
    cloud: CloudSpeechConfiguration | None = None
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
    renderer: Literal["local", "openrouter"] | None = None
    cloud: CloudSpeechConfiguration | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)
    consent_confirmed: bool | None = None
    reference_transcript: str | None = Field(default=None, max_length=2000)
    reference_language: str | None = Field(default=None, min_length=2, max_length=16, pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}$")


class SpeechCloneTrialRequest(Contract):
    cloud_approval: CloudSpeechApproval | None = None
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
    engine: Literal["speech", "gpt-sovits", "openrouter"]
    profile_id: str
    install_hints: list[str] = Field(default_factory=list)
    trial_id: str | None = None
    output_path: str | None = None
    cloud_receipt_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class SpeechCloneEngineStatus(Contract):
    installed: bool
    mock: bool
    root: str | None = None
    api_base_url: str | None = None
    api_reachable: bool = False
    install_hints: list[str] = Field(default_factory=list)


VOICE_PROFILE_CLIENT_MODELS: list[type[BaseModel]] = [
    CloudSpeechConfiguration,
    CreateCloudSpeechVoiceProfileRequest,
    CloudSpeechApproval,
    CloudSpeechQuote,
    CloudSpeechProvenance,
    CloudSpeechTrial,
    CloudSpeechTrialsResponse,
    SpeechVoiceProfile,
    SpeechVoiceProfilesResponse,
    PatchSpeechVoiceProfileRequest,
    SpeechCloneTrialRequest,
    SpeechCloneTrialResponse,
    SpeechCloneEngineStatus,
    StarterSpeechVoice,
    StarterSpeechVoicesResponse,
]
