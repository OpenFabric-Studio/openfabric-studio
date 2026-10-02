"""REST API for audiobook books and chapter speech jobs (stub synthesis)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import audiobooks, voice_profiles
from ..audiobook_contracts import (
    AudiobookBook,
    AudiobookBooksResponse,
    AudiobookCreateResponse,
    AudiobookJobsResponse,
    CreateAudiobookRequest,
)

router = APIRouter(prefix="/api/audiobooks", tags=["audiobooks"])


def _raise(exc: audiobooks.AudiobookError | voice_profiles.VoiceProfileError) -> None:
    raise HTTPException(status_code=exc.status, detail=exc.code) from exc


@router.get("", response_model=AudiobookBooksResponse)
def list_audiobooks() -> AudiobookBooksResponse:
    return AudiobookBooksResponse(books=audiobooks.list_books())


@router.post("", response_model=AudiobookCreateResponse)
def create_audiobook(body: CreateAudiobookRequest) -> AudiobookCreateResponse:
    try:
        return audiobooks.create_book(body)
    except (audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as exc:
        _raise(exc)
        raise  # pragma: no cover


@router.get("/jobs", response_model=AudiobookJobsResponse)
def list_all_audiobook_jobs() -> AudiobookJobsResponse:
    return AudiobookJobsResponse(jobs=audiobooks.list_jobs())


@router.get("/{book_id}", response_model=AudiobookBook)
def get_audiobook(book_id: str) -> AudiobookBook:
    try:
        return audiobooks.get_book(book_id)
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise  # pragma: no cover


@router.get("/{book_id}/jobs", response_model=AudiobookJobsResponse)
def list_audiobook_jobs(book_id: str) -> AudiobookJobsResponse:
    try:
        return AudiobookJobsResponse(jobs=audiobooks.list_jobs(book_id=book_id))
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise  # pragma: no cover
