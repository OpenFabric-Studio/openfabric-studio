"""REST API for audiobook books, chapter speech jobs, and export download."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from pathlib import Path
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from starlette.responses import Response
from starlette.types import Message

from .. import audiobook_workflows, audiobooks, ebook_import, voice_profiles, audiobook_cloud
from ..audiobook_contracts import (
    AcceptAudiobookRepairRequest,
    AudiobookCloudControlRequest,
    AudiobookAudition,
    AudiobookAuditionOptions,
    AudiobookAuditionsResponse,
    AudiobookPassagesResponse,
    AudiobookRepair,
    AudiobookRepairsResponse,
    CreateAudiobookAuditionRequest,
    CreateAudiobookRepairRequest,
    AudiobookBook,
    AudiobookBooksResponse,
    AudiobookCreateResponse,
    AudiobookJobsResponse,
    CreateAudiobookRequest,
    CreateAudiobookFromDraftRequest,
    EbookDraft, EbookDraftsResponse, ImportPastedTextRequest, PatchEbookDraftRequest,
    SetAudiobookLanguagesRequest,
    SetCastRequest,
    SetChapterTextRequest,
    SetPronunciationsRequest,
    SetAudiobookPacingRequest,
    NarrationDurationRequest,
    NarrationDurationGuidance,
)
from ..voice_profile_contracts import CloudSpeechQuote
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
        from ..resource_admission import admission_lock
        async with admission_lock:
            with audiobook_workflows._prepare_admission():
                if audiobooks._sync_worker():
                    return await await_cleanup(asyncio.to_thread(audiobooks.create_book, body))
                return audiobooks.create_book(body)
    except (audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as exc:
        _raise(exc)
        raise  # pragma: no cover


@router.post("/quote",response_model=CloudSpeechQuote)
def quote_audiobook(body: CreateAudiobookRequest) -> CloudSpeechQuote:
    try:
        return audiobook_cloud.quote_creation(body)
    except (audiobooks.AudiobookError,voice_profiles.VoiceProfileError) as exc:
        _raise(exc)
        raise


@router.get("/jobs", response_model=AudiobookJobsResponse)
def list_all_audiobook_jobs() -> AudiobookJobsResponse:
    return AudiobookJobsResponse(jobs=audiobooks.list_jobs())


async def _disconnected(request: Request) -> None:
    while not await request.is_disconnected():
        await asyncio.sleep(0.1)


@router.post("/imports", response_model=EbookDraft)
async def import_ebook(request: Request, file: UploadFile = File(...)) -> EbookDraft:
    filename = file.filename or ""
    if Path(filename).suffix.lower() not in {".mobi", ".epub", ".txt", ".docx", ".srt", ".vtt"}:
        raise HTTPException(400, "unsupported_ebook_format")
    raw = bytearray()
    try:
        while chunk := await file.read(65536):
            raw.extend(chunk)
            if len(raw) > ebook_import.MAX_UPLOAD_BYTES:
                raise HTTPException(413, "ebook_too_large")
    finally:
        await file.close()
    operation = asyncio.create_task(ebook_import.import_document(filename, bytes(raw)))
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


@router.post("/imports/text", response_model=EbookDraft)
async def import_pasted_text(body: ImportPastedTextRequest) -> EbookDraft:
    try:
        return await asyncio.to_thread(ebook_import.import_pasted, body.title, body.text, body.author)
    except ebook_import.EbookImportError as exc:
        _raise(exc)
        raise


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
        media = {".mobi": "application/x-mobipocket-ebook", ".epub": "application/epub+zip", ".txt": "text/plain",
                 ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".srt": "application/x-subrip", ".vtt": "text/vtt"}.get(
            Path(draft.source_filename).suffix.lower(), "application/octet-stream")
        return FileResponse(ebook_import.source_path(draft_id), media_type=media, filename=draft.source_filename)
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


@router.post("/duration-guidance", response_model=NarrationDurationGuidance)
def narration_duration_guidance(body: NarrationDurationRequest) -> NarrationDurationGuidance:
    from ..narration_duration import guidance
    try:
        return guidance(body)
    except (audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as exc:
        _raise(exc)
        raise


@router.put("/{book_id}/pacing", response_model=AudiobookBook)
async def set_audiobook_pacing(book_id: str, body: SetAudiobookPacingRequest) -> AudiobookBook:
    try:
        return await await_cleanup(asyncio.to_thread(audiobooks.set_pacing, book_id, body))
    except (audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as exc:
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
async def resume_audiobook(book_id: str, body: AudiobookCloudControlRequest | None = None) -> AudiobookBook:
    try:
        return await audiobooks.resume_book(book_id,body)
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


@router.put("/{book_id}/cast", response_model=AudiobookBook)
def set_audiobook_cast(book_id: str, body: SetCastRequest) -> AudiobookBook:
    try:
        return audiobooks.set_cast(book_id, body.cast)
    except (audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as exc:
        _raise(exc)
        raise


@router.put("/{book_id}/chapters/{chapter_index}/text", response_model=AudiobookBook)
def set_chapter_text(book_id: str, chapter_index: int, body: SetChapterTextRequest) -> AudiobookBook:
    try:
        return audiobooks.set_chapter_text(book_id, chapter_index, body.text)
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise


@router.put("/{book_id}/pronunciations", response_model=AudiobookBook)
def set_audiobook_pronunciations(book_id: str, body: SetPronunciationsRequest) -> AudiobookBook:
    try:
        return audiobooks.set_pronunciations(book_id, body.pronunciations)
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise


@router.post("/{book_id}/chapters/{chapter_index}/regenerate", response_model=AudiobookBook)
async def regenerate_chapter(book_id: str, chapter_index: int, body: AudiobookCloudControlRequest | None = None) -> AudiobookBook:
    try:
        await audiobooks.wait_for_book(book_id)
        if audiobooks._sync_worker():
            return await await_cleanup(asyncio.to_thread(audiobooks.regenerate_chapter, book_id, chapter_index, body))
        return audiobooks.regenerate_chapter(book_id, chapter_index, body)
    except (audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as exc:
        _raise(exc)
        raise


@router.post("/{book_id}/cover", response_model=AudiobookBook)
async def upload_audiobook_cover(book_id: str, file: UploadFile = File(...)) -> AudiobookBook:
    raw = bytearray()
    try:
        while chunk := await file.read(65536):
            raw.extend(chunk)
            if len(raw) > 2_000_000:
                raise HTTPException(413, "cover_too_large")
    finally:
        await file.close()
    try:
        book = await asyncio.to_thread(audiobooks.save_cover, book_id, bytes(raw))
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise
    return book


@router.put("/{book_id}/languages", response_model=AudiobookBook)
def set_audiobook_languages(book_id: str, body: SetAudiobookLanguagesRequest) -> AudiobookBook:
    try:
        return audiobooks.set_languages(
            book_id, body.language, [(item.chapter_index, item.language) for item in body.chapters],
        )
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise


@router.get("/{book_id}/exports/cue")
def download_audiobook_cue(book_id: str) -> Response:
    from ..audiobook_collection import cue_text, download_name
    try:
        body = cue_text(book_id)
        name = download_name(book_id, "cue")
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise
    return Response(body, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/{book_id}/exports/collection")
def download_audiobook_collection(book_id: str) -> Response:
    from ..audiobook_collection import CollectionResponse
    return CollectionResponse(book_id)


def _book_manifest_path(book_id: str, fmt: str, kind: str) -> Path:
    from .. import export_provenance
    from ..audiobook_cast import require_cast
    try:
        with audiobooks.publication_lock(book_id):
            book=audiobooks.get_book(book_id)
            if not voice_profiles.get_profile(book.profile_id).consent_confirmed:
                raise audiobooks.AudiobookError('consent_required',403)
            require_cast(book.cast)
            # Cast edits preserve accepted PCM. Check the voices captured in
            # those completed passages, rather than only today's cast settings.
            for job in audiobooks.list_jobs(book_id=book_id):
                if job.status!='done':continue
                accepted=audiobook_workflows.get_passages(book_id,job.chapter_index)
                for passage in accepted.passages:
                    if passage.status!='done':continue
                    if not voice_profiles.get_profile(passage.profile_id).consent_confirmed:
                        raise audiobooks.AudiobookError('consent_required',403)
                    try:
                        audiobook_workflows.passage_render_snapshot(book_id,job.chapter_index,passage.id,accepted.revision)
                    except audiobooks.AudiobookError as exc:
                        if exc.code!='passage_snapshot_unavailable':raise
            media=audiobooks.export_format_path(book_id,fmt)
            value=export_provenance.read(media)
            # Regenerate the readable copy only from the exact retained receipt.
            export_provenance.write(media,value)
            return export_provenance.path_for(media,'json' if kind=='json' else 'txt')
    except (audiobooks.AudiobookError,voice_profiles.VoiceProfileError) as exc:
        _raise(exc)
        raise
    except (OSError,export_provenance.ProvenanceError) as exc:
        raise HTTPException(404,'manifest_unavailable') from exc


@router.get('/{book_id}/exports/{fmt}/provenance')
def download_audiobook_provenance(book_id: str,fmt: str) -> FileResponse:
    return FileResponse(_book_manifest_path(book_id,fmt,'json'),media_type='application/json')


@router.get('/{book_id}/exports/{fmt}/manifest')
def download_audiobook_manifest(book_id: str,fmt: str) -> FileResponse:
    return FileResponse(_book_manifest_path(book_id,fmt,'txt'),media_type='text/plain',filename=f'{fmt}-provenance.txt')


@router.get("/{book_id}/exports/{fmt}")
def download_audiobook_format(book_id: str, fmt: str) -> FileResponse:
    try:
        path = audiobooks.export_format_path(book_id, fmt)
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise
    book = audiobooks.get_book(book_id)
    safe = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in book.title).strip() or "audiobook"
    media = {"wav": "audio/wav", "mp3": "audio/mpeg", "m4b": "audio/mp4"}[fmt]
    return FileResponse(path, media_type=media, filename=f"{safe}.{fmt}")


@router.post("/{book_id}/retry", response_model=AudiobookBook)
async def retry_audiobook(book_id: str, body: AudiobookCloudControlRequest | None = None) -> AudiobookBook:
    try:
        await audiobooks.wait_for_book(book_id)
        if audiobooks._sync_worker():
            return await await_cleanup(asyncio.to_thread(audiobooks.retry_failed, book_id, body))
        return audiobooks.retry_failed(book_id,body)
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


@router.post("/auditions", response_model=AudiobookAudition)
async def create_draft_audition(body: CreateAudiobookAuditionRequest) -> AudiobookAudition:
    try:
        from ..resource_admission import admission_lock
        async with admission_lock:
            if audiobooks._sync_worker():
                return await await_cleanup(asyncio.to_thread(audiobook_workflows.start_audition, body))
            return audiobook_workflows.start_audition(body)
    except (audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as exc:
        _raise(exc)
        raise


@router.post("/{book_id}/auditions", response_model=AudiobookAudition)
async def create_saved_audition(book_id: str, body: AudiobookAuditionOptions) -> AudiobookAudition:
    try:
        from ..resource_admission import admission_lock
        async with admission_lock:
            if audiobooks._sync_worker():
                return await await_cleanup(asyncio.to_thread(audiobook_workflows.start_book_audition, book_id, body))
            return audiobook_workflows.start_book_audition(book_id, body)
    except (audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as exc:
        _raise(exc)
        raise


@router.get("/{book_id}/auditions", response_model=AudiobookAuditionsResponse)
def list_saved_auditions(book_id: str, chapter_index: int | None = None) -> AudiobookAuditionsResponse:
    try:
        return AudiobookAuditionsResponse(auditions=audiobook_workflows.list_auditions(book_id, chapter_index))
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise


@router.get("/auditions/{identifier}", response_model=AudiobookAudition)
def get_cast_audition(identifier: str) -> AudiobookAudition:
    try:
        return audiobook_workflows.get_audition(identifier)
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise


@router.post("/auditions/{identifier}/cancel", response_model=AudiobookAudition)
def cancel_cast_audition(identifier: str) -> AudiobookAudition:
    try:
        audiobook_workflows.cancel(identifier, "audition")
        return audiobook_workflows.get_audition(identifier)
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise


@router.get("/auditions/{identifier}/clips/{index}/audio")
def download_audition_clip(identifier: str, index: int) -> FileResponse:
    try:
        return FileResponse(audiobook_workflows.audition_audio_path(identifier, index), media_type="audio/wav")
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise


@router.get("/auditions/{identifier}/audio")
def download_audition_scene(identifier: str) -> FileResponse:
    try:
        return FileResponse(audiobook_workflows.audition_audio_path(identifier), media_type="audio/wav")
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise


@router.get("/{book_id}/chapters/{chapter_index}/passages", response_model=AudiobookPassagesResponse)
def list_chapter_passages(book_id: str, chapter_index: int) -> AudiobookPassagesResponse:
    try:
        return audiobook_workflows.get_passages(book_id, chapter_index)
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise


@router.get("/{book_id}/passages/{passage_id}/audio")
def download_passage(book_id: str, passage_id: str, revision: int | None = None) -> FileResponse:
    try:
        return FileResponse(audiobook_workflows.passage_audio_path(book_id, passage_id, revision), media_type="audio/wav")
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise


@router.post("/{book_id}/chapters/{chapter_index}/passages/{passage_id}/repairs", response_model=AudiobookRepair)
async def generate_passage_repair(book_id: str, chapter_index: int, passage_id: str, body: CreateAudiobookRepairRequest) -> AudiobookRepair:
    try:
        from ..resource_admission import admission_lock
        async with admission_lock:
            if audiobooks._sync_worker():
                return await await_cleanup(asyncio.to_thread(audiobook_workflows.start_repair, book_id, chapter_index, passage_id, body))
            return audiobook_workflows.start_repair(book_id, chapter_index, passage_id, body)
    except (audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as exc:
        _raise(exc)
        raise


@router.get("/{book_id}/chapters/{chapter_index}/passages/{passage_id}/repairs", response_model=AudiobookRepairsResponse)
def list_passage_repairs(book_id: str, chapter_index: int, passage_id: str, revision: int | None = None) -> AudiobookRepairsResponse:
    try:
        return AudiobookRepairsResponse(repairs=audiobook_workflows.list_repairs(book_id, chapter_index, passage_id, revision))
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise


@router.get("/repairs/{identifier}", response_model=AudiobookRepair)
def get_passage_repair(identifier: str) -> AudiobookRepair:
    try:
        return audiobook_workflows.get_repair(identifier)
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise


@router.get("/repairs/{identifier}/audio")
def download_passage_repair(identifier: str) -> FileResponse:
    try:
        return FileResponse(audiobook_workflows.repair_audio_path(identifier), media_type="audio/wav")
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise


@router.post("/repairs/{identifier}/cancel", response_model=AudiobookRepair)
def cancel_passage_repair(identifier: str) -> AudiobookRepair:
    try:
        audiobook_workflows.cancel(identifier, "repair")
        return audiobook_workflows.get_repair(identifier)
    except audiobooks.AudiobookError as exc:
        _raise(exc)
        raise


@router.post("/repairs/{identifier}/accept", response_model=AudiobookPassagesResponse)
async def accept_passage_repair(identifier: str, body: AcceptAudiobookRepairRequest) -> AudiobookPassagesResponse:
    try:
        return await await_cleanup(asyncio.to_thread(audiobook_workflows.accept_repair, identifier, body))
    except (audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as exc:
        _raise(exc)
        raise


@router.post("/auditions/quote",response_model=CloudSpeechQuote)
def quote_draft_audition(body: CreateAudiobookAuditionRequest) -> CloudSpeechQuote:
    try:
        return audiobook_workflows.quote_audition(body)
    except (audiobooks.AudiobookError,voice_profiles.VoiceProfileError) as exc:
        _raise(exc)
        raise


@router.post("/{book_id}/auditions/quote",response_model=CloudSpeechQuote)
def quote_saved_audition(book_id: str, body: AudiobookAuditionOptions) -> CloudSpeechQuote:
    try:
        return audiobook_workflows.quote_book_audition(book_id,body)
    except (audiobooks.AudiobookError,voice_profiles.VoiceProfileError) as exc:
        _raise(exc)
        raise


@router.post("/{book_id}/cloud-quote",response_model=CloudSpeechQuote)
def quote_cloud_control(book_id: str,body: AudiobookCloudControlRequest) -> CloudSpeechQuote:
    try:
        return audiobook_cloud.quote_control(book_id,body)
    except (audiobooks.AudiobookError,voice_profiles.VoiceProfileError) as exc:
        _raise(exc)
        raise


@router.post("/{book_id}/chapters/{chapter_index}/passages/{passage_id}/repairs/quote",response_model=CloudSpeechQuote)
def quote_passage_repair(book_id: str,chapter_index: int,passage_id: str,body: CreateAudiobookRepairRequest) -> CloudSpeechQuote:
    try:
        return audiobook_workflows.quote_repair(book_id,chapter_index,passage_id,body)
    except (audiobooks.AudiobookError,voice_profiles.VoiceProfileError) as exc:
        _raise(exc)
        raise
