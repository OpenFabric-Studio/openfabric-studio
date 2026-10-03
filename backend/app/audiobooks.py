"""SQLite-backed books with durable short speech sections and PCM WAV exports.

Creates consent-backed chapter jobs, synthesizes them serially via the shared
GPT-SoVITS speech-clone worker, then concatenates chapter WAVs into one export.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import uuid

from pydantic import TypeAdapter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from . import voice_profiles
from .audiobook_contracts import (
    AudiobookBook,
    AudiobookBookStatus,
    AudiobookCreateResponse,
    AudiobookJob,
    CreateAudiobookRequest,
)
from .config import DATA_DIR
from .contracts import JobStatus

_ID = re.compile(r"^[0-9a-f]{32}$")
_LOCK = threading.RLock()
_BOOK_STATUS: TypeAdapter[AudiobookBookStatus] = TypeAdapter(AudiobookBookStatus)
_JOB_STATUS: TypeAdapter[JobStatus] = TypeAdapter(JobStatus)

# Overridable in tests.
BOOKS_ROOT = DATA_DIR / "audiobooks"

# Review limits; narration sends short sections rather than whole chapters.
MAX_CHAPTER_CHARS = 20_000
MAX_CHAPTERS = 100

# Set OPENFABRIC_AUDIOBOOK_SYNC=1 in tests to run the worker on the caller thread.
def _sync_worker() -> bool:
    return os.getenv("OPENFABRIC_AUDIOBOOK_SYNC", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class AudiobookError(Exception):
    def __init__(self, code: str, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def books_root() -> Path:
    root = BOOKS_ROOT
    if root.is_symlink():
        raise AudiobookError("audiobook_storage_unavailable", 503)
    root.mkdir(parents=True, exist_ok=True)
    return root


def book_dir(book_id: str) -> Path:
    if not _ID.fullmatch(book_id):
        raise AudiobookError("invalid_book_id", 404)
    path = books_root() / book_id
    if path.is_symlink() or not path.resolve().is_relative_to(books_root().resolve()):
        raise AudiobookError("audiobook_storage_unavailable", 503)
    path.mkdir(parents=True, exist_ok=True)
    return path


def chapters_dir(book_id: str) -> Path:
    path = book_dir(book_id) / "chapters"
    if path.is_symlink() or not path.resolve().is_relative_to(books_root().resolve()):
        raise AudiobookError("audiobook_storage_unavailable", 503)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _db_path() -> Path:
    path = books_root() / "audiobooks.db"
    if path.is_symlink():
        raise AudiobookError("audiobook_storage_unavailable", 503)
    return path


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(_db_path(), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == 1:
        return
    if version != 0:
        raise AudiobookError("audiobook_storage_unavailable", 503)
    connection.execute("BEGIN IMMEDIATE")
    try:
        if connection.execute("PRAGMA user_version").fetchone()[0] == 1:
            connection.commit()
            return
        connection.execute("""CREATE TABLE IF NOT EXISTS audiobook_books (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, profile_id TEXT NOT NULL,
            chapter_count INTEGER NOT NULL, status TEXT NOT NULL, export_path TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS audiobook_jobs (
            id TEXT PRIMARY KEY, book_id TEXT NOT NULL, chapter_index INTEGER NOT NULL,
            chapter_title TEXT NOT NULL DEFAULT '', chapter_text TEXT NOT NULL,
            status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', output_path TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY (book_id) REFERENCES audiobook_books(id) ON DELETE CASCADE)""")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(audiobook_books)")}
        if "source_import_id" not in columns:
            connection.execute("ALTER TABLE audiobook_books ADD COLUMN source_import_id TEXT")
        connection.execute("""CREATE TABLE IF NOT EXISTS audiobook_sections (
            job_id TEXT NOT NULL, section_index INTEGER NOT NULL CHECK(section_index >= 0),
            section_text TEXT NOT NULL, text_sha256 TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('queued','running','done')),
            output_path TEXT, PRIMARY KEY(job_id, section_index),
            FOREIGN KEY(job_id) REFERENCES audiobook_jobs(id) ON DELETE CASCADE)""")
        connection.execute("CREATE INDEX IF NOT EXISTS audiobook_jobs_book ON audiobook_jobs(book_id, chapter_index)")
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _row_to_book(row: sqlite3.Row) -> AudiobookBook:
    return AudiobookBook(
        id=str(row["id"]),
        title=str(row["title"]),
        profile_id=str(row["profile_id"]),
        chapter_count=int(row["chapter_count"]),
        status=_BOOK_STATUS.validate_python(row["status"]),
        export_path=str(row["export_path"]) if row["export_path"] else None,
        source_import_id=str(row["source_import_id"]) if row["source_import_id"] else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_job(row: sqlite3.Row) -> AudiobookJob:
    with closing(_connect()) as connection:
        counts = connection.execute("SELECT COUNT(*), SUM(status = 'done') FROM audiobook_sections WHERE job_id = ?", (row["id"],)).fetchone()
    return AudiobookJob(
        id=str(row["id"]),
        book_id=str(row["book_id"]),
        chapter_index=int(row["chapter_index"]),
        chapter_title=str(row["chapter_title"] or ""),
        completed_sections=int(counts[1] or 0), total_sections=int(counts[0]),
        status=_JOB_STATUS.validate_python(row["status"]),
        detail=str(row["detail"] or ""),
        output_path=str(row["output_path"]) if row["output_path"] else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _update_book(
    connection: sqlite3.Connection,
    book_id: str,
    *,
    status: str | None = None,
    export_path: str | None = None,
    clear_export: bool = False,
) -> None:
    stamp = _now()
    if status is not None and (export_path is not None or clear_export):
        connection.execute(
            """
            UPDATE audiobook_books
            SET status = ?, export_path = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, None if clear_export else export_path, stamp, book_id),
        )
    elif status is not None:
        connection.execute(
            "UPDATE audiobook_books SET status = ?, updated_at = ? WHERE id = ?",
            (status, stamp, book_id),
        )
    elif export_path is not None or clear_export:
        connection.execute(
            "UPDATE audiobook_books SET export_path = ?, updated_at = ? WHERE id = ?",
            (None if clear_export else export_path, stamp, book_id),
        )


def _update_job(
    connection: sqlite3.Connection,
    job_id: str,
    *,
    status: str,
    detail: str = "",
    output_path: str | None = None,
) -> None:
    stamp = _now()
    connection.execute(
        """
        UPDATE audiobook_jobs
        SET status = ?, detail = ?, output_path = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, detail[:2000], output_path, stamp, job_id),
    )


def create_book(body: CreateAudiobookRequest, *, source_import_id: str | None = None,
                source_import_revision: int | None = None) -> AudiobookCreateResponse:
    title = body.title.strip()
    if not title:
        raise AudiobookError("title_required")
    if not body.chapters:
        raise AudiobookError("chapters_required")
    if len(body.chapters) > MAX_CHAPTERS:
        raise AudiobookError("too_many_chapters")

    profile = voice_profiles.get_profile(body.profile_id)
    if not profile.consent_confirmed:
        raise AudiobookError("consent_required", 403)

    book_id = uuid.uuid4().hex
    stamp = _now()
    jobs: list[AudiobookJob] = []
    detail = "Queued for serial GPT-SoVITS chapter synthesis."

    with _LOCK, closing(_connect()) as connection:
        _ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        if source_import_id is not None:
            source = connection.execute("SELECT json_extract(payload, '$.revision') FROM ebook_drafts WHERE id = ?", (source_import_id,)).fetchone()
            if source is None or source_import_revision is None or source[0] != source_import_revision:
                raise AudiobookError("ebook_draft_conflict", 409)
        connection.execute(
            """
            INSERT INTO audiobook_books (
                id, title, profile_id, chapter_count, status, export_path, created_at, updated_at, source_import_id
            ) VALUES (?, ?, ?, ?, 'queued', NULL, ?, ?, ?)
            """,
            (book_id, title[:200], body.profile_id, len(body.chapters), stamp, stamp, source_import_id),
        )
        for index, chapter in enumerate(body.chapters):
            text = chapter.text.strip()
            if not text:
                connection.rollback()
                raise AudiobookError("chapter_text_required")
            if len(text) > MAX_CHAPTER_CHARS:
                connection.rollback()
                raise AudiobookError("chapter_text_too_long")
            job_id = uuid.uuid4().hex
            chapter_title = (chapter.title or f"Chapter {index + 1}").strip()[:200]
            connection.execute(
                """
                INSERT INTO audiobook_jobs (
                    id, book_id, chapter_index, chapter_title, chapter_text,
                    status, detail, output_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, NULL, ?, ?)
                """,
                (job_id, book_id, index, chapter_title, text[:MAX_CHAPTER_CHARS], detail, stamp, stamp),
            )
            jobs.append(
                AudiobookJob(
                    id=job_id,
                    book_id=book_id,
                    chapter_index=index,
                    chapter_title=chapter_title,
                    status="queued",
                    detail=detail,
                    output_path=None,
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
        connection.commit()
        book_row = connection.execute(
            "SELECT * FROM audiobook_books WHERE id = ?", (book_id,)
        ).fetchone()
        assert book_row is not None
        book = _row_to_book(book_row)

    book_dir(book_id)
    _schedule_book(book_id)
    return AudiobookCreateResponse(book=book, jobs=jobs)


def list_books() -> list[AudiobookBook]:
    with _LOCK, closing(_connect()) as connection:
        _ensure_schema(connection)
        rows = connection.execute(
            "SELECT * FROM audiobook_books ORDER BY created_at DESC, id DESC"
        ).fetchall()
        return [_row_to_book(row) for row in rows]


def get_book(book_id: str) -> AudiobookBook:
    if not _ID.fullmatch(book_id):
        raise AudiobookError("invalid_book_id", 404)
    with _LOCK, closing(_connect()) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            "SELECT * FROM audiobook_books WHERE id = ?", (book_id,)
        ).fetchone()
        if row is None:
            raise AudiobookError("book_not_found", 404)
        return _row_to_book(row)


def list_jobs(*, book_id: str | None = None) -> list[AudiobookJob]:
    with _LOCK, closing(_connect()) as connection:
        _ensure_schema(connection)
        if book_id is not None:
            if not _ID.fullmatch(book_id):
                raise AudiobookError("invalid_book_id", 404)
            exists = connection.execute(
                "SELECT 1 FROM audiobook_books WHERE id = ?", (book_id,)
            ).fetchone()
            if exists is None:
                raise AudiobookError("book_not_found", 404)
            rows = connection.execute(
                """
                SELECT * FROM audiobook_jobs
                WHERE book_id = ?
                ORDER BY chapter_index ASC, id ASC
                """,
                (book_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM audiobook_jobs ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [_row_to_job(row) for row in rows]


def export_path_for(book_id: str) -> Path:
    book = get_book(book_id)
    if not book.export_path:
        raise AudiobookError("export_not_ready", 404)
    path = Path(book.export_path)
    root = books_root().resolve()
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise AudiobookError("export_not_ready", 404) from exc
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise AudiobookError("export_not_ready", 404)
    return resolved


def chapter_audio_path(book_id: str, chapter_index: int) -> Path:
    if chapter_index < 0:
        raise AudiobookError("chapter_not_found", 404)
    jobs = list_jobs(book_id=book_id)
    match = next((job for job in jobs if job.chapter_index == chapter_index), None)
    if match is None or not match.output_path:
        raise AudiobookError("chapter_not_found", 404)
    path = Path(match.output_path)
    root = books_root().resolve()
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise AudiobookError("chapter_not_found", 404) from exc
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise AudiobookError("chapter_not_found", 404)
    return resolved


def retry_failed(book_id: str) -> AudiobookBook:
    """Retry failed chapters or an export, retaining completed section audio."""
    if not _ID.fullmatch(book_id):
        raise AudiobookError("invalid_book_id", 404)
    with _LOCK, closing(_connect()) as connection:
        _ensure_schema(connection)
        book_row = connection.execute(
            "SELECT * FROM audiobook_books WHERE id = ?", (book_id,)
        ).fetchone()
        if book_row is None:
            raise AudiobookError("book_not_found", 404)
        failed = connection.execute(
            """
            SELECT id FROM audiobook_jobs
            WHERE book_id = ? AND status = 'failed'
            """,
            (book_id,),
        ).fetchall()
        if not failed and book_row["status"] != "failed":
            raise AudiobookError("nothing_to_retry")
        stamp = _now()
        for row in failed:
            connection.execute(
                """
                UPDATE audiobook_jobs
                SET status = 'queued', detail = ?, output_path = NULL, updated_at = ?
                WHERE id = ?
                """,
                ("Re-queued after failure.", stamp, row["id"]),
            )
        connection.execute(
            """
            UPDATE audiobook_books
            SET status = 'queued', export_path = NULL, updated_at = ?
            WHERE id = ?
            """,
            (stamp, book_id),
        )
        connection.commit()
        book = _row_to_book(
            connection.execute(
                "SELECT * FROM audiobook_books WHERE id = ?", (book_id,)
            ).fetchone()
        )
    _schedule_book(book_id)
    return book


def _schedule_book(book_id: str) -> None:
    from . import audiobook_narration
    if _sync_worker():
        audiobook_narration.run_sync(book_id)
    else:
        audiobook_narration.schedule(book_id)


def _concat_chapters(book_id: str, chapter_wavs: list[Path]) -> Path:
    from .audiobook_narration import concat_wavs
    return concat_wavs(book_id, chapter_wavs, book_dir(book_id) / "export.wav", controlled=False)


async def start() -> None:
    from . import audiobook_narration, ebook_import
    ebook_import.start()
    await audiobook_narration.start()


async def shutdown() -> None:
    from . import audiobook_narration
    await audiobook_narration.shutdown()


def pause_book(book_id: str) -> AudiobookBook:
    from . import audiobook_narration
    return audiobook_narration.pause_book(book_id)


def cancel_book(book_id: str) -> AudiobookBook:
    from . import audiobook_narration
    return audiobook_narration.cancel_book(book_id)


async def resume_book(book_id: str) -> AudiobookBook:
    from . import audiobook_narration
    return await audiobook_narration.resume_book(book_id)


async def wait_for_book(book_id: str) -> None:
    from . import audiobook_narration
    await audiobook_narration.wait_for_book(book_id)
