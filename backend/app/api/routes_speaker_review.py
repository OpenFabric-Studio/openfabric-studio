"""Read-only audio comparison and explicit opt-in local screening jobs."""
from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter,HTTPException,Query,Path
from .. import speaker_review,audiobooks,voice_profiles
from ..speaker_review_contracts import SpeakerReviewCapability,SpeakerReferencesResponse,SpeakerReviewRequest,SpeakerReview,SpeakerReviewsResponse
from .routes_audiobooks import _BoundedUploadRoute

router=APIRouter(prefix="/api/audiobooks",tags=["speaker-review"],route_class=_BoundedUploadRoute)


@router.get("/speaker-review/capability",response_model=SpeakerReviewCapability)
def capability()->SpeakerReviewCapability:return speaker_review.capability()


@router.get("/{book_id}/chapters/{chapter_index}/passages/{passage_id}/speaker-references",response_model=SpeakerReferencesResponse)
def references(book_id:str,chapter_index:Annotated[int,Path(ge=0,le=99)],passage_id:str,revision:int=Query(ge=1),render_identity:str=Query(pattern=r"^[0-9a-f]{64}$"))->SpeakerReferencesResponse:
    try:return speaker_review.references(book_id,chapter_index,passage_id,revision,render_identity)
    except (speaker_review.SpeakerReviewError,audiobooks.AudiobookError,voice_profiles.VoiceProfileError) as error:raise HTTPException(error.status,error.code) from error


@router.post("/{book_id}/chapters/{chapter_index}/passages/{passage_id}/speaker-review",response_model=SpeakerReview)
async def create(book_id:str,chapter_index:Annotated[int,Path(ge=0,le=99)],passage_id:str,body:SpeakerReviewRequest)->SpeakerReview:
    try:return await speaker_review.create(book_id,chapter_index,passage_id,body)
    except (speaker_review.SpeakerReviewError,audiobooks.AudiobookError,voice_profiles.VoiceProfileError) as error:raise HTTPException(error.status,error.code) from error


@router.get("/speaker-review/{identifier}",response_model=SpeakerReview)
def get(identifier:str)->SpeakerReview:
    try:return speaker_review.get(identifier)
    except speaker_review.SpeakerReviewError as error:raise HTTPException(error.status,error.code) from error


@router.get("/{book_id}/speaker-reviews",response_model=SpeakerReviewsResponse)
def list_reviews(book_id:str)->SpeakerReviewsResponse:
    try:return speaker_review.list_reviews(book_id)
    except speaker_review.SpeakerReviewError as error:raise HTTPException(error.status,error.code) from error


@router.post("/speaker-review/{identifier}/cancel",response_model=SpeakerReview)
async def cancel(identifier:str)->SpeakerReview:
    try:return await speaker_review.cancel(identifier)
    except speaker_review.SpeakerReviewError as error:raise HTTPException(error.status,error.code) from error
