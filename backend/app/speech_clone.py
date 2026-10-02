"""Talking / audiobook speech-clone scaffold (GPT-SoVITS-oriented).

No model weights are downloaded here. Trials return a clear not-installed status
until the optional engine is installed separately.
"""
from __future__ import annotations

from typing import Literal

from . import voice_profiles
from .voice_profile_contracts import SpeechCloneTrialRequest, SpeechCloneTrialResponse

GPT_SOVITS_URL = "https://github.com/RVC-Boss/GPT-SoVITS"
INSTALL_HINTS = [
    "Install GPT-SoVITS separately (MIT): " + GPT_SOVITS_URL,
    "Point OpenFabric at the engine checkout once ready; singing voice clone remains Seed-VC.",
    "OpenFabric does not vendor or auto-download GPT-SoVITS weights.",
]


def engine_installed(_engine: Literal["speech", "gpt-sovits"] = "gpt-sovits") -> bool:
    """Scaffold: engine integration is not shipped yet."""
    return False


def start_trial(body: SpeechCloneTrialRequest) -> SpeechCloneTrialResponse:
    # Ensure the profile exists and consent was recorded before advertising install hints.
    profile = voice_profiles.get_profile(body.profile_id)
    if not profile.consent_confirmed:
        raise voice_profiles.VoiceProfileError("consent_required", 403)
    text = body.text.strip()
    if not text:
        raise voice_profiles.VoiceProfileError("text_required")
    return SpeechCloneTrialResponse(
        status="engine_not_installed",
        detail="GPT-SoVITS speech engine is not installed in this OpenFabric Studio checkout.",
        engine=body.engine,
        profile_id=body.profile_id,
        install_hints=list(INSTALL_HINTS),
    )
