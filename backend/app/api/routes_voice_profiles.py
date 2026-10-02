"""REST API for consent-backed speech voice profiles."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import voice_profiles
from ..voice_profile_contracts import (
    PatchSpeechVoiceProfileRequest,
    SpeechVoiceProfile,
    SpeechVoiceProfilesResponse,
)

router = APIRouter(prefix="/api/voice-profiles", tags=["voice profiles"])


def _raise(exc: voice_profiles.VoiceProfileError) -> None:
    raise HTTPException(status_code=exc.status, detail=exc.code) from exc


@router.get("", response_model=SpeechVoiceProfilesResponse)
def list_voice_profiles() -> SpeechVoiceProfilesResponse:
    return SpeechVoiceProfilesResponse(profiles=voice_profiles.list_profiles())


@router.post("", response_model=SpeechVoiceProfile)
async def create_voice_profile(
    name: Annotated[str, Form(min_length=1, max_length=120)],
    consent_confirmed: Annotated[bool, Form()],
    audio: Annotated[UploadFile, File()],
    notes: Annotated[str, Form(max_length=2000)] = "",
) -> SpeechVoiceProfile:
    raw = await audio.read()
    try:
        return voice_profiles.create_profile(
            name=name,
            consent_confirmed=consent_confirmed,
            audio_bytes=raw,
            filename=audio.filename or "reference.wav",
            notes=notes,
        )
    except voice_profiles.VoiceProfileError as exc:
        _raise(exc)
        raise  # pragma: no cover


@router.get("/{profile_id}", response_model=SpeechVoiceProfile)
def get_voice_profile(profile_id: str) -> SpeechVoiceProfile:
    try:
        return voice_profiles.get_profile(profile_id)
    except voice_profiles.VoiceProfileError as exc:
        _raise(exc)
        raise  # pragma: no cover


@router.patch("/{profile_id}", response_model=SpeechVoiceProfile)
def patch_voice_profile(profile_id: str, body: PatchSpeechVoiceProfileRequest) -> SpeechVoiceProfile:
    try:
        return voice_profiles.patch_profile(profile_id, body)
    except voice_profiles.VoiceProfileError as exc:
        _raise(exc)
        raise  # pragma: no cover


@router.delete("/{profile_id}", status_code=204)
def delete_voice_profile(profile_id: str) -> None:
    try:
        voice_profiles.delete_profile(profile_id)
    except voice_profiles.VoiceProfileError as exc:
        _raise(exc)
        raise  # pragma: no cover
