"""Speech / audiobook clone trials (GPT-SoVITS worker interface)."""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Response, Request
from fastapi.responses import FileResponse

from .. import speech_clone, voice_profiles, cloud_speech
from ..voice_profile_contracts import (
    SpeechCloneEngineStatus,
    SpeechCloneTrialRequest,
    SpeechCloneTrialResponse,
    CloudSpeechQuote,
    CloudSpeechTrialsResponse,
)

from ..module_security import require_local_origin

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
    try:
        provenance = cloud_speech.read_provenance(path)
        if provenance is not None:
            profile = voice_profiles.get_profile(provenance.profile_id)
            if not profile.consent_confirmed:
                raise HTTPException(403,"consent_required")
    except voice_profiles.VoiceProfileError as exc:
        raise HTTPException(exc.status,exc.code) from exc
    return FileResponse(
        path, media_type="audio/wav", filename=path.name, content_disposition_type="inline"
    )


@router.post("/trials", response_model=SpeechCloneTrialResponse)
def create_speech_clone_trial(body: SpeechCloneTrialRequest, response: Response, request: Request) -> SpeechCloneTrialResponse:
    try:
        if voice_profiles.get_profile(body.profile_id).renderer == "openrouter":
            require_local_origin(request)
        result = speech_clone.start_trial(body)
    except voice_profiles.VoiceProfileError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc
    response.status_code = speech_clone.http_status_for(result)
    return result


@router.post("/quote",response_model=CloudSpeechQuote)
def quote_speech_trial(body: SpeechCloneTrialRequest, request: Request) -> CloudSpeechQuote:
    require_local_origin(request)
    try:
        return cloud_speech.quote_inputs([(body.text.strip(),body.profile_id,body.text_language or "en")])
    except voice_profiles.VoiceProfileError as exc:
        raise HTTPException(exc.status,exc.code) from exc


@router.get("/profiles/{profile_id}/trials",response_model=CloudSpeechTrialsResponse)
def list_cloud_speech_trials(profile_id: str) -> CloudSpeechTrialsResponse:
    try:
        return CloudSpeechTrialsResponse(trials=cloud_speech.list_trials(profile_id))
    except voice_profiles.VoiceProfileError as exc:
        raise HTTPException(exc.status,exc.code) from exc
