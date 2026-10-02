"""Speech / audiobook clone trial scaffold (GPT-SoVITS)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import speech_clone, voice_profiles
from ..voice_profile_contracts import SpeechCloneTrialRequest, SpeechCloneTrialResponse

router = APIRouter(prefix="/api/speech-clone", tags=["speech clone"])


@router.post("/trials", response_model=SpeechCloneTrialResponse, status_code=501)
def create_speech_clone_trial(body: SpeechCloneTrialRequest) -> SpeechCloneTrialResponse:
    try:
        return speech_clone.start_trial(body)
    except voice_profiles.VoiceProfileError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc
