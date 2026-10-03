"""Owned audiobook workers, durable short sections and atomic PCM WAV joins.

The upstream speech API is blocking and has no cancellation contract. Control
requests stop at a section boundary; shutdown waits for that call to finish.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid
import wave
from typing import Literal
from contextlib import closing
from pathlib import Path

from pydantic import BaseModel, Field

from . import audiobooks, speech_clone, voice_profiles
from .audiobook_contracts import AudiobookBook
from .job_lifecycle import await_cleanup

SECTION_CHARS = 1200
_TASKS: dict[str, asyncio.Task[None]] = {}
_SHUTTING_DOWN = False
_LOG = logging.getLogger(__name__)


class _Chapter(BaseModel):
    id: str
    chapter_index: int
    chapter_text: str


class _Section(BaseModel):
    section_index: int = Field(ge=0)
    section_text: str = Field(min_length=1, max_length=SECTION_CHARS)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["queued", "running", "done"]
    output_path: str | None = None


class _Stopped(Exception):
    pass


def split_sections(text: str) -> list[str]:
    result: list[str] = []
    remainder = text
    while len(remainder) > SECTION_CHARS:
        window = remainder[:SECTION_CHARS]
        boundary = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("? "), window.rfind("! "))
        if boundary < SECTION_CHARS // 3:
            boundary = window.rfind(" ")
        if boundary < SECTION_CHARS // 3:
            boundary = SECTION_CHARS
        else:
            boundary += 1
        result.append(remainder[:boundary])
        remainder = remainder[boundary:]
    if remainder:
        result.append(remainder)
    return result


def _active(identifier: str) -> bool:
    return audiobooks.get_book(identifier).status in {"queued", "running"}


def _contained(path: Path, root: Path) -> Path:
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise audiobooks.AudiobookError("audiobook_storage_unavailable", 503)
    return path


def concat_wavs(identifier: str, paths: list[Path], target: Path, *, controlled: bool = True) -> Path:
    """Stream matching PCM sections without an unmanaged ffmpeg subprocess."""
    if not paths:
        raise audiobooks.AudiobookError("audio_missing")
    root = audiobooks.book_dir(identifier)
    _contained(target, root)
    temporary = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.tmp.wav")
    try:
        parameters: tuple[int, int, int] | None = None
        with wave.open(str(temporary), "wb") as destination:
            for path in paths:
                _contained(path, root)
                with wave.open(str(path), "rb") as source:
                    actual = (source.getnchannels(), source.getsampwidth(), source.getframerate())
                    if parameters is None:
                        parameters = actual
                        destination.setnchannels(actual[0])
                        destination.setsampwidth(actual[1])
                        destination.setframerate(actual[2])
                    elif actual != parameters:
                        raise audiobooks.AudiobookError("audio_format_mismatch")
                    while frames := source.readframes(65536):
                        if controlled and not _active(identifier):
                            raise _Stopped()
                        destination.writeframesraw(frames)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        temporary.replace(target)
        return target
    finally:
        temporary.unlink(missing_ok=True)


def _sections(chapter: _Chapter) -> list[_Section]:
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        audiobooks._ensure_schema(connection)
        expected = split_sections(chapter.chapter_text)
        rows = connection.execute("SELECT section_index, section_text, text_sha256, status, output_path FROM audiobook_sections WHERE job_id = ? ORDER BY section_index", (chapter.id,)).fetchall()
        if rows:
            sections = [_Section.model_validate(dict(row)) for row in rows]
            if (len(sections) != len(expected) or any(section.section_index != index or section.section_text != text
                    or section.text_sha256 != hashlib.sha256(text.encode()).hexdigest()
                    for index, (section, text) in enumerate(zip(sections, expected, strict=True)))):
                raise audiobooks.AudiobookError("narration_sections_changed")
            return sections
        for index, text in enumerate(expected):
            connection.execute("""INSERT INTO audiobook_sections
                (job_id, section_index, section_text, text_sha256, status, output_path)
                VALUES (?, ?, ?, ?, 'queued', NULL)""",
                (chapter.id, index, text, hashlib.sha256(text.encode()).hexdigest()))
        connection.commit()
        rows = connection.execute("SELECT section_index, section_text, text_sha256, status, output_path FROM audiobook_sections WHERE job_id = ? ORDER BY section_index", (chapter.id,)).fetchall()
        return [_Section.model_validate(dict(row)) for row in rows]


def _section_status(job_id: str, index: int, status: str, path: Path | None = None) -> None:
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        connection.execute("UPDATE audiobook_sections SET status = ?, output_path = ? WHERE job_id = ? AND section_index = ?", (status, str(path) if path else None, job_id, index))
        connection.commit()


def _job_status(job_id: str, status: str, detail: str = "", path: Path | None = None) -> None:
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        row = connection.execute("SELECT b.status, j.status FROM audiobook_books b JOIN audiobook_jobs j ON j.book_id = b.id WHERE j.id = ?", (job_id,)).fetchone()
        if row is None:
            raise audiobooks.AudiobookError("book_not_found", 404)
        if row[0] in {"paused", "cancelled"}:
            if row[1] == "done":
                return
            status = "cancelled" if row[0] == "cancelled" else "queued"
            detail, path = "stopped_at_section_boundary", None
        audiobooks._update_job(connection, job_id, status=status, detail=detail, output_path=str(path) if path else None)
        connection.commit()


def _synthesize(identifier: str, profile_id: str, text: str, target: Path) -> None:
    # Check the control flag after acquiring the shared lock: a waiting book
    # may have been paused while another book or speech trial used the engine.
    with speech_clone.SYNTHESIS_LOCK:
        if not _active(identifier):
            raise _Stopped()
        outcome = speech_clone.synthesize_to_path(profile_id=profile_id, text=text, output_path=target, require_consent=True)
        if outcome.status not in {"completed", "mock_completed"} or outcome.output_path is None:
            _LOG.warning("Audiobook section failed: %s", outcome.status)
            code = outcome.status if outcome.status in {"engine_not_installed", "api_unavailable"} else "setup_busy" if outcome.detail == "setup_busy" else "chapter_synthesis_failed"
            raise audiobooks.AudiobookError(code)


def _process_chapter(identifier: str, profile_id: str, chapter: _Chapter) -> None:
    output_root = audiobooks.chapters_dir(identifier)
    sections_root = _contained(output_root / "sections", output_root)
    sections_root.mkdir(exist_ok=True)
    paths: list[Path] = []
    _job_status(chapter.id, "running", "narrating_sections")
    try:
        for section in _sections(chapter):
            if not _active(identifier):
                raise _Stopped()
            target = _contained(sections_root / f"{chapter.id}-{section.section_index:04d}.wav", output_root)
            if section.status == "done" and section.output_path == str(target) and target.is_file():
                paths.append(target)
                continue
            _section_status(chapter.id, section.section_index, "running")
            temporary = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.partial.wav")
            try:
                _synthesize(identifier, profile_id, section.section_text, temporary)
                # The upstream reply must be real PCM WAV before publication.
                with wave.open(str(temporary), "rb") as handle:
                    if handle.getnframes() <= 0:
                        raise audiobooks.AudiobookError("invalid_speech_audio")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            _section_status(chapter.id, section.section_index, "done", target)
            paths.append(target)
        target = concat_wavs(identifier, paths, output_root / f"{chapter.chapter_index:04d}.wav")
        _job_status(chapter.id, "done", "narration_completed", target)
    except _Stopped:
        status = "cancelled" if audiobooks.get_book(identifier).status == "cancelled" else "queued"
        _job_status(chapter.id, status, "stopped_at_section_boundary")
        raise
    except (audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as exc:
        _job_status(chapter.id, "failed", exc.code)
    except (OSError, EOFError, wave.Error):
        _LOG.exception("Audiobook audio publication failed")
        _job_status(chapter.id, "failed", "invalid_speech_audio")


def _process_book(identifier: str) -> None:
    if not _active(identifier):
        return
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        transition = connection.execute("UPDATE audiobook_books SET status = 'running', export_path = NULL, updated_at = ? WHERE id = ? AND status IN ('queued', 'running')", (audiobooks._now(), identifier))
        if transition.rowcount == 0:
            return
        connection.commit()
        rows = connection.execute("SELECT id, chapter_index, chapter_text FROM audiobook_jobs WHERE book_id = ? AND status != 'done' ORDER BY chapter_index", (identifier,)).fetchall()
        chapters = [_Chapter.model_validate(dict(row)) for row in rows]
    profile_id = audiobooks.get_book(identifier).profile_id
    for chapter in chapters:
        if not _active(identifier):
            return
        _process_chapter(identifier, profile_id, chapter)
    if not _active(identifier):
        return
    jobs = audiobooks.list_jobs(book_id=identifier)
    if any(job.status != "done" or job.output_path is None for job in jobs):
        with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
            if not _active(identifier):
                return
            audiobooks._update_book(connection, identifier, status="failed", clear_export=True)
            connection.commit()
        return
    paths = [audiobooks.chapter_audio_path(identifier, job.chapter_index) for job in jobs]
    exported = concat_wavs(identifier, paths, audiobooks.book_dir(identifier) / "export.wav")
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        if _active(identifier):
            audiobooks._update_book(connection, identifier, status="done", export_path=str(exported))
            connection.commit()


def run_sync(identifier: str) -> None:
    try:
        _process_book(identifier)
    except _Stopped:
        pass
    except Exception:
        _LOG.exception("Audiobook narration failed")
        with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
            if _active(identifier):
                audiobooks._update_book(connection, identifier, status="failed", clear_export=True)
                connection.commit()


async def _run(identifier: str) -> None:
    try:
        # Cancellation cannot discard ownership of the blocking speech call.
        await await_cleanup(asyncio.to_thread(run_sync, identifier))
    finally:
        if _TASKS.get(identifier) is asyncio.current_task():
            _TASKS.pop(identifier, None)


def schedule(identifier: str) -> None:
    if _SHUTTING_DOWN:
        raise audiobooks.AudiobookError("audiobook_backend_stopping", 503)
    previous = _TASKS.get(identifier)
    if previous is None or previous.done():
        _TASKS[identifier] = asyncio.create_task(_run(identifier), name=f"audiobook:{identifier}")


def work_busy() -> bool:
    # A paused/cancelled worker can still own its current upstream section.
    if any(not task.done() for task in tuple(_TASKS.values())):
        return True
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        audiobooks._ensure_schema(connection)
        return connection.execute("SELECT 1 FROM audiobook_books WHERE status IN ('queued', 'running') LIMIT 1").fetchone() is not None


async def wait_for_book(identifier: str) -> None:
    task = _TASKS.get(identifier)
    if task is not None:
        await await_cleanup(asyncio.shield(task))


def pause_book(identifier: str) -> AudiobookBook:
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        book = audiobooks.get_book(identifier)
        if book.status not in {"queued", "running", "paused"}:
            raise audiobooks.AudiobookError("audiobook_not_active", 409)
        audiobooks._update_book(connection, identifier, status="paused")
        connection.commit()
    return audiobooks.get_book(identifier)


def cancel_book(identifier: str) -> AudiobookBook:
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        book = audiobooks.get_book(identifier)
        if book.status == "done":
            raise audiobooks.AudiobookError("audiobook_already_completed", 409)
        audiobooks._update_book(connection, identifier, status="cancelled", clear_export=True)
        connection.execute("UPDATE audiobook_jobs SET status = 'cancelled', detail = 'stopped_at_section_boundary' WHERE book_id = ? AND status != 'done'", (identifier,))
        connection.commit()
    return audiobooks.get_book(identifier)


async def resume_book(identifier: str) -> AudiobookBook:
    await wait_for_book(identifier)
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        book = audiobooks.get_book(identifier)
        if book.status not in {"paused", "cancelled", "failed"}:
            raise audiobooks.AudiobookError("audiobook_not_resumable", 409)
        profile = voice_profiles.get_profile(book.profile_id)
        if not profile.consent_confirmed:
            raise audiobooks.AudiobookError("consent_required", 403)
        audiobooks._update_book(connection, identifier, status="queued", clear_export=True)
        connection.execute("UPDATE audiobook_jobs SET status = 'queued', detail = 'resuming_sections' WHERE book_id = ? AND status != 'done'", (identifier,))
        connection.commit()
    schedule(identifier)
    return audiobooks.get_book(identifier)


async def start() -> None:
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = False
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        audiobooks._ensure_schema(connection)
        connection.execute("UPDATE audiobook_books SET status = 'paused', updated_at = ? WHERE status IN ('queued', 'running')", (audiobooks._now(),))
        connection.execute("UPDATE audiobook_jobs SET status = 'queued', detail = 'narration_interrupted' WHERE status = 'running'")
        connection.execute("UPDATE audiobook_sections SET status = 'queued', output_path = NULL WHERE status = 'running'")
        connection.commit()


async def shutdown() -> None:
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = True
    for identifier in tuple(_TASKS):
        if _active(identifier):
            pause_book(identifier)
    await await_cleanup(asyncio.gather(*tuple(_TASKS.values()), return_exceptions=True))
