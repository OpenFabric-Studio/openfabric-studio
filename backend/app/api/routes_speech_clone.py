"""Speech / audiobook clone trials (GPT-SoVITS worker interface)."""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from .. import speech_clone, voice_profiles
from ..voice_profile_contracts import (
    SpeechCloneEngineStatus,
    SpeechCloneTrialRequest,
    SpeechCloneTrialResponse,
)

router = APIRouter(prefix="/api/speech-clone", tags=["speech clone"])
_TRIAL_ID = re.compile(r"^[0-9a-f]{32}$")


@router.get("/engine", response_model=SpeechCloneEngineStatus)
def get_speech_clone_engine() -> SpeechCloneEngineStatus:
    return speech_clone.engine_status()


@router.get("/trials/{trial_id}/audio")
def get_speech_clone_trial_audio(trial_id: str) -> FileResponse:
    if not _TRIAL_ID.fullmatch(trial_id):
        raise HTTPException(status_code=404, detail="trial_audio_not_found")
    try:
        root = speech_clone.TRIALS_ROOT
        path = root / f"{trial_id}.wav"
        if (
            root.is_symlink() or path.is_symlink() or not path.is_file()
            or path.resolve(strict=True).parent != root.resolve(strict=True)
        ):
            raise OSError("unsafe or missing trial audio")
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail="trial_audio_not_found") from exc
    return FileResponse(
        path, media_type="audio/wav", filename=path.name, content_disposition_type="inline"
    )


@router.post("/trials", response_model=SpeechCloneTrialResponse)
def create_speech_clone_trial(body: SpeechCloneTrialRequest, response: Response) -> SpeechCloneTrialResponse:
    try:
        result = speech_clone.start_trial(body)
    except voice_profiles.VoiceProfileError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc
    response.status_code = speech_clone.http_status_for(result)
    return result
