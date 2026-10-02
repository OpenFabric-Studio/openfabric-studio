"""REST API for audiobook books, chapter speech jobs, and export download."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

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


@router.post("/{book_id}/retry", response_model=AudiobookBook)
def retry_audiobook(book_id: str) -> AudiobookBook:
    try:
        return audiobooks.retry_failed(book_id)
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise  # pragma: no cover


@router.get("/{book_id}/export")
def download_audiobook_export(book_id: str) -> FileResponse:
    try:
        path = audiobooks.export_path_for(book_id)
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise  # pragma: no cover
    book = audiobooks.get_book(book_id)
    safe = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in book.title).strip() or "audiobook"
    return FileResponse(path, media_type="audio/wav", filename=f"{safe}.wav")


@router.get("/{book_id}/chapters/{chapter_index}/audio")
def download_chapter_audio(book_id: str, chapter_index: int) -> FileResponse:
    try:
        path = audiobooks.chapter_audio_path(book_id, chapter_index)
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise  # pragma: no cover
    return FileResponse(path, media_type="audio/wav", filename=f"chapter-{chapter_index:04d}.wav")
