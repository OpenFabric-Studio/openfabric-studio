"""Opt-in review actions; no transcript hint automatically changes narration."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from .. import audiobook_review, audiobooks, narration_pauses
from ..audiobook_review_contracts import AsrCapability, AsrReview, AsrReviewRequest, AsrReviewsResponse, PauseAnalysisSettings
from .routes_audiobooks import _BoundedUploadRoute

router = APIRouter(prefix="/api/audiobooks", tags=["audiobook-review"], route_class=_BoundedUploadRoute)


@router.get("/analysis/settings", response_model=PauseAnalysisSettings)
def pause_settings() -> PauseAnalysisSettings:
    try:
        return narration_pauses.load_settings()
    except (OSError, ValueError) as error:
        raise HTTPException(503, "pause_settings_storage_unavailable") from error


@router.put("/analysis/settings", response_model=PauseAnalysisSettings)
def set_pause_settings(body: PauseAnalysisSettings) -> PauseAnalysisSettings:
    try:
        return narration_pauses.save_settings(body)
    except (OSError, ValueError) as error:
        raise HTTPException(503, "pause_settings_storage_unavailable") from error


@router.get("/qa/capability", response_model=AsrCapability)
def asr_capability() -> AsrCapability:
    return audiobook_review.capability()


@router.get("/qa/{identifier}", response_model=AsrReview)
def get_review(identifier: str) -> AsrReview:
    try:
        return audiobook_review.get(identifier)
    except audiobook_review.AudiobookReviewError as error:
        raise HTTPException(error.status, error.code) from error


@router.get("/{book_id}/qa", response_model=AsrReviewsResponse)
def reviews(book_id: str) -> AsrReviewsResponse:
    try:
        return audiobook_review.list_reviews(book_id)
    except audiobook_review.AudiobookReviewError as error:
        raise HTTPException(error.status, error.code) from error


@router.post("/{book_id}/chapters/{chapter_index}/passages/{passage_id}/qa", response_model=AsrReview)
async def create_review(book_id: str, chapter_index: int, passage_id: str, body: AsrReviewRequest) -> AsrReview:
    try:
        return await audiobook_review.create(book_id, chapter_index, passage_id, body)
    except (audiobook_review.AudiobookReviewError, audiobooks.AudiobookError) as error:
        raise HTTPException(error.status, error.code) from error


@router.post("/qa/{identifier}/cancel", response_model=AsrReview)
async def cancel_review(identifier: str) -> AsrReview:
    try:
        return await audiobook_review.cancel(identifier)
    except audiobook_review.AudiobookReviewError as error:
        raise HTTPException(error.status, error.code) from error
