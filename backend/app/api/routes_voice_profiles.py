"""REST API for consent-backed speech voice profiles."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse

from .. import speech_starter_voices, voice_profiles
from ..voice_profile_contracts import (
    PatchSpeechVoiceProfileRequest,
    SpeechVoiceProfile,
    SpeechVoiceProfilesResponse,
    StarterSpeechVoicesResponse,
    CreateCloudSpeechVoiceProfileRequest,
)

from ..module_security import require_local_origin

router = APIRouter(prefix="/api/voice-profiles", tags=["voice profiles"])


def _raise(exc: voice_profiles.VoiceProfileError) -> None:
    raise HTTPException(status_code=exc.status, detail=exc.code) from exc


@router.get("", response_model=SpeechVoiceProfilesResponse)
def list_voice_profiles() -> SpeechVoiceProfilesResponse:
    try:
        return SpeechVoiceProfilesResponse(profiles=voice_profiles.list_profiles())
    except voice_profiles.VoiceProfileError as exc:
        _raise(exc)
        raise  # pragma: no cover


@router.post("", response_model=SpeechVoiceProfile)
async def create_voice_profile(
    name: Annotated[str, Form(min_length=1, max_length=120)],
    consent_confirmed: Annotated[bool, Form()],
    audio: Annotated[UploadFile, File()],
    notes: Annotated[str, Form(max_length=2000)] = "",
    reference_transcript: Annotated[str, Form(max_length=2000)] = "",
    reference_language: Annotated[str, Form(min_length=2, max_length=16, pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}$")] = "en",
) -> SpeechVoiceProfile:
    raw = await audio.read()
    try:
        return voice_profiles.create_profile(
            name=name,
            consent_confirmed=consent_confirmed,
            audio_bytes=raw,
            filename=audio.filename or "reference.wav",
            notes=notes,
            reference_transcript=reference_transcript,
            reference_language=reference_language,
        )
    except voice_profiles.VoiceProfileError as exc:
        _raise(exc)
        raise  # pragma: no cover


@router.get("/starter-voices", response_model=StarterSpeechVoicesResponse)
def list_starter_voice_catalog() -> StarterSpeechVoicesResponse:
    try:
        return StarterSpeechVoicesResponse(voices=speech_starter_voices.list_starter_voices())
    except voice_profiles.VoiceProfileError as exc:
        _raise(exc)
        raise  # pragma: no cover


@router.post("/cloud", response_model=SpeechVoiceProfile)
def create_cloud_voice_profile(body: CreateCloudSpeechVoiceProfileRequest, request: Request) -> SpeechVoiceProfile:
    require_local_origin(request)
    try:
        return voice_profiles.create_cloud_profile(body)
    except voice_profiles.VoiceProfileError as exc:
        _raise(exc)
        raise  # pragma: no cover


@router.get("/starter-voices/{starter_id}/audio")
def preview_starter_voice(starter_id: str) -> FileResponse:
    try:
        path = speech_starter_voices.starter_audio_path(starter_id)
        return FileResponse(
            path, media_type="audio/wav", filename=path.name, content_disposition_type="inline"
        )
    except voice_profiles.VoiceProfileError as exc:
        _raise(exc)
        raise  # pragma: no cover


@router.post("/starter-voices/{starter_id}/import", response_model=SpeechVoiceProfile)
def import_starter_voice(starter_id: str) -> SpeechVoiceProfile:
    try:
        return speech_starter_voices.import_starter_voice(starter_id)
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
def patch_voice_profile(profile_id: str, body: PatchSpeechVoiceProfileRequest, request: Request) -> SpeechVoiceProfile:
    try:
        if body.renderer is not None or body.cloud is not None:
            require_local_origin(request)
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
