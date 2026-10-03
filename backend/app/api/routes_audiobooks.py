"""REST API for audiobook books, chapter speech jobs, and export download."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from starlette.responses import Response
from starlette.types import Message

from .. import audiobooks, ebook_import, voice_profiles
from ..audiobook_contracts import (
    AudiobookBook,
    AudiobookBooksResponse,
    AudiobookCreateResponse,
    AudiobookJobsResponse,
    CreateAudiobookRequest,
    CreateAudiobookFromDraftRequest,
    EbookDraft, EbookDraftsResponse, PatchEbookDraftRequest,
)
from ..job_lifecycle import await_cleanup
from ..module_security import require_local_origin

MAX_JSON_REQUEST_BYTES = 16 * 1024 * 1024


class _BoundedUploadRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()

        async def bounded(request: Request) -> Response:
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                require_local_origin(request)
            if request.method in {"GET", "HEAD", "OPTIONS"}:
                return await handler(request)
            limit = (ebook_import.MAX_UPLOAD_BYTES + 1024 * 1024
                     if request.method == "POST" and request.url.path == "/api/audiobooks/imports"
                     else MAX_JSON_REQUEST_BYTES)
            count = 0

            async def receive() -> Message:
                nonlocal count
                message = await request.receive()
                if message["type"] == "http.request":
                    body: object = message.get("body", b"")
                    if isinstance(body, bytes):
                        count += len(body)
                    if count > limit:
                        raise HTTPException(413, "ebook_too_large")
                return message

            return await handler(Request(request.scope, receive))

        return bounded


router = APIRouter(prefix="/api/audiobooks", tags=["audiobooks"], route_class=_BoundedUploadRoute)


def _raise(exc: audiobooks.AudiobookError | voice_profiles.VoiceProfileError | ebook_import.EbookImportError) -> None:
    raise HTTPException(status_code=exc.status, detail=exc.code) from exc


@router.get("", response_model=AudiobookBooksResponse)
def list_audiobooks() -> AudiobookBooksResponse:
    return AudiobookBooksResponse(books=audiobooks.list_books())


@router.post("", response_model=AudiobookCreateResponse)
async def create_audiobook(body: CreateAudiobookRequest) -> AudiobookCreateResponse:
    try:
        if audiobooks._sync_worker():
            return await await_cleanup(asyncio.to_thread(audiobooks.create_book, body))
        return audiobooks.create_book(body)
    except (audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as exc:
        _raise(exc)
        raise  # pragma: no cover


@router.get("/jobs", response_model=AudiobookJobsResponse)
def list_all_audiobook_jobs() -> AudiobookJobsResponse:
    return AudiobookJobsResponse(jobs=audiobooks.list_jobs())


async def _disconnected(request: Request) -> None:
    while not await request.is_disconnected():
        await asyncio.sleep(0.1)


@router.post("/imports", response_model=EbookDraft)
async def import_ebook(request: Request, file: UploadFile = File(...)) -> EbookDraft:
    filename = file.filename or "book.mobi"
    if not filename.lower().endswith(".mobi"):
        raise HTTPException(400, "unsupported_ebook_format")
    raw = bytearray()
    try:
        while chunk := await file.read(65536):
            raw.extend(chunk)
            if len(raw) > ebook_import.MAX_UPLOAD_BYTES:
                raise HTTPException(413, "ebook_too_large")
    finally:
        await file.close()
    operation = asyncio.create_task(ebook_import.import_mobi(filename, bytes(raw)))
    disconnect = asyncio.create_task(_disconnected(request))
    try:
        completed, _ = await asyncio.wait((operation, disconnect), return_when=asyncio.FIRST_COMPLETED)
        if operation in completed:
            return await operation
        raise ebook_import.EbookImportError("ebook_import_cancelled", 499)
    except ebook_import.EbookImportError as exc:
        _raise(exc)
        raise
    finally:
        for task in (operation, disconnect):
            if not task.done():
                task.cancel()
        await await_cleanup(asyncio.gather(operation, disconnect, return_exceptions=True))


@router.get("/imports", response_model=EbookDraftsResponse)
def list_ebook_drafts() -> EbookDraftsResponse:
    try:
        return EbookDraftsResponse(drafts=ebook_import.list_drafts())
    except (ebook_import.EbookImportError, audiobooks.AudiobookError) as exc:
        _raise(exc)
        raise


@router.get("/imports/{draft_id}", response_model=EbookDraft)
def get_ebook_draft(draft_id: str) -> EbookDraft:
    try:
        return ebook_import.get_draft(draft_id)
    except (ebook_import.EbookImportError, audiobooks.AudiobookError) as exc:
        _raise(exc)
        raise


@router.patch("/imports/{draft_id}", response_model=EbookDraft)
def update_ebook_draft(draft_id: str, body: PatchEbookDraftRequest) -> EbookDraft:
    try:
        return ebook_import.patch_draft(draft_id, body)
    except (ebook_import.EbookImportError, audiobooks.AudiobookError) as exc:
        _raise(exc)
        raise


@router.post("/imports/{draft_id}/create", response_model=AudiobookCreateResponse)
async def narrate_ebook_draft(draft_id: str, body: CreateAudiobookFromDraftRequest) -> AudiobookCreateResponse:
    try:
        if audiobooks._sync_worker():
            return await await_cleanup(asyncio.to_thread(ebook_import.create_from_draft, draft_id, body))
        return ebook_import.create_from_draft(draft_id, body)
    except (ebook_import.EbookImportError, audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as exc:
        _raise(exc)
        raise


@router.get("/imports/{draft_id}/source")
def download_ebook_source(draft_id: str) -> FileResponse:
    try:
        draft = ebook_import.get_draft(draft_id)
        return FileResponse(ebook_import.source_path(draft_id), media_type="application/x-mobipocket-ebook", filename=draft.source_filename)
    except (ebook_import.EbookImportError, audiobooks.AudiobookError) as exc:
        _raise(exc)
        raise


@router.delete("/imports/{draft_id}", status_code=204)
def delete_ebook_draft(draft_id: str) -> None:
    try:
        ebook_import.delete_draft(draft_id)
    except (ebook_import.EbookImportError, audiobooks.AudiobookError) as exc:
        _raise(exc)
        raise


@router.post("/{book_id}/pause", response_model=AudiobookBook)
def pause_audiobook(book_id: str) -> AudiobookBook:
    try:
        return audiobooks.pause_book(book_id)
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise


@router.post("/{book_id}/resume", response_model=AudiobookBook)
async def resume_audiobook(book_id: str) -> AudiobookBook:
    try:
        return await audiobooks.resume_book(book_id)
    except (audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as exc:
        _raise(exc)
        raise


@router.post("/{book_id}/cancel", response_model=AudiobookBook)
def cancel_audiobook(book_id: str) -> AudiobookBook:
    try:
        return audiobooks.cancel_book(book_id)
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise


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
async def retry_audiobook(book_id: str) -> AudiobookBook:
    try:
        await audiobooks.wait_for_book(book_id)
        if audiobooks._sync_worker():
            return await await_cleanup(asyncio.to_thread(audiobooks.retry_failed, book_id))
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
