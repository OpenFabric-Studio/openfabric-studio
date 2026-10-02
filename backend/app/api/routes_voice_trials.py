"""Voice quality comparisons are stored independently of training recordings."""
from __future__ import annotations

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse

from .. import voice_comparisons as comparisons
from ..voice_contracts import (
    VoiceComparisonRequest, VoiceComparisonResponse, VoiceComparisonsResponse,
    VoiceSeparationOptionsResponse, VoiceTrialRating, VoiceTrialSource, VoiceTrialSourcesResponse,
)
from ..voice_separation import separation_options

router = APIRouter(prefix="/api/voices", tags=["voice trials"])


@router.get("/separation-options", response_model=VoiceSeparationOptionsResponse)
async def get_separation_options() -> VoiceSeparationOptionsResponse:
    return separation_options()


@router.get("/{voice_id}/trial-sources", response_model=VoiceTrialSourcesResponse)
async def get_trial_sources(voice_id: str) -> VoiceTrialSourcesResponse:
    return comparisons.list_sources(voice_id)


@router.post("/{voice_id}/trial-sources", response_model=VoiceTrialSource)
async def upload_trial_source(voice_id: str, file: UploadFile = File(...)) -> VoiceTrialSource:
    return await comparisons.add_source(voice_id, file)


@router.post("/{voice_id}/comparisons", response_model=VoiceComparisonResponse)
async def start_comparison(voice_id: str, body: VoiceComparisonRequest) -> VoiceComparisonResponse:
    return comparisons.start_comparison(voice_id, body)


@router.get("/{voice_id}/comparisons", response_model=VoiceComparisonsResponse)
async def list_comparisons(voice_id: str) -> VoiceComparisonsResponse:
    return comparisons.list_comparisons(voice_id)


@router.get("/{voice_id}/comparisons/{comparison_id}", response_model=VoiceComparisonResponse)
async def get_comparison(voice_id: str, comparison_id: str) -> VoiceComparisonResponse:
    return comparisons.get_comparison(voice_id, comparison_id)


@router.post("/{voice_id}/comparisons/{comparison_id}/cancel", response_model=VoiceComparisonResponse)
async def cancel_comparison(voice_id: str, comparison_id: str) -> VoiceComparisonResponse:
    return await comparisons.cancel_comparison(voice_id, comparison_id)


@router.patch("/{voice_id}/comparisons/{comparison_id}/trials/{trial_id}/rating", response_model=VoiceComparisonResponse)
async def rate_trial(voice_id: str, comparison_id: str, trial_id: str, body: VoiceTrialRating) -> VoiceComparisonResponse:
    return comparisons.rate_trial(voice_id, comparison_id, trial_id, body)


@router.get("/{voice_id}/comparisons/{comparison_id}/trials/{trial_id}/audio")
async def trial_audio(voice_id: str, comparison_id: str, trial_id: str) -> FileResponse:
    return FileResponse(comparisons.audio_path(voice_id, comparison_id, trial_id))
