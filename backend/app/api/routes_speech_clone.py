"""Speech / audiobook clone trials (GPT-SoVITS worker interface)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from .. import speech_clone, voice_profiles
from ..voice_profile_contracts import (
    SpeechCloneEngineStatus,
    SpeechCloneTrialRequest,
    SpeechCloneTrialResponse,
)

router = APIRouter(prefix="/api/speech-clone", tags=["speech clone"])


@router.get("/engine", response_model=SpeechCloneEngineStatus)
def get_speech_clone_engine() -> SpeechCloneEngineStatus:
    return speech_clone.engine_status()


@router.post("/trials", response_model=SpeechCloneTrialResponse)
def create_speech_clone_trial(body: SpeechCloneTrialRequest, response: Response) -> SpeechCloneTrialResponse:
    try:
        result = speech_clone.start_trial(body)
    except voice_profiles.VoiceProfileError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc
    response.status_code = speech_clone.http_status_for(result)
    return result
