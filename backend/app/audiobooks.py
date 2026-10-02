"""SQLite-backed audiobook books and chapter speech jobs (stub synthesis).

Creates queued chapter jobs against a consent-backed speech voice profile.
Actual GPT-SoVITS synthesis and WAV concatenation land in a later pass.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from . import voice_profiles
from .audiobook_contracts import (
    AudiobookBook,
    AudiobookCreateResponse,
    AudiobookJob,
    CreateAudiobookRequest,
)
from .config import DATA_DIR

_ID = re.compile(r"^[0-9a-f]{32}$")
_LOCK = threading.RLock()

# Overridable in tests.
BOOKS_ROOT = DATA_DIR / "audiobooks"


class AudiobookError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def books_root() -> Path:
    root = BOOKS_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def _db_path() -> Path:
    return books_root() / "audiobooks.db"


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(_db_path(), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS audiobook_books (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            profile_id TEXT NOT NULL,
            chapter_count INTEGER NOT NULL,
            status TEXT NOT NULL,
            export_path TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS audiobook_jobs (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            chapter_index INTEGER NOT NULL,
            chapter_title TEXT NOT NULL DEFAULT '',
            chapter_text TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            output_path TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (book_id) REFERENCES audiobook_books(id) ON DELETE CASCADE
        )
        """
    )


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _row_to_book(row: sqlite3.Row) -> AudiobookBook:
    return AudiobookBook(
        id=str(row["id"]),
        title=str(row["title"]),
        profile_id=str(row["profile_id"]),
        chapter_count=int(row["chapter_count"]),
        status=row["status"],  # type: ignore[arg-type]
        export_path=str(row["export_path"]) if row["export_path"] else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_job(row: sqlite3.Row) -> AudiobookJob:
    return AudiobookJob(
        id=str(row["id"]),
        book_id=str(row["book_id"]),
        chapter_index=int(row["chapter_index"]),
        chapter_title=str(row["chapter_title"] or ""),
        status=row["status"],  # type: ignore[arg-type]
        detail=str(row["detail"] or ""),
        output_path=str(row["output_path"]) if row["output_path"] else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def create_book(body: CreateAudiobookRequest) -> AudiobookCreateResponse:
    title = body.title.strip()
    if not title:
        raise AudiobookError("title_required")
    if not body.chapters:
        raise AudiobookError("chapters_required")

    profile = voice_profiles.get_profile(body.profile_id)
    if not profile.consent_confirmed:
        raise AudiobookError("consent_required", 403)

    book_id = uuid.uuid4().hex
    stamp = _now()
    jobs: list[AudiobookJob] = []
    detail = (
        "Queued — GPT-SoVITS chapter synthesis and export concatenation are not "
        "wired yet (stub API)."
    )

    with _LOCK, closing(_connect()) as connection:
        _ensure_schema(connection)
        connection.execute(
            """
            INSERT INTO audiobook_books (
                id, title, profile_id, chapter_count, status, export_path, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'queued', NULL, ?, ?)
            """,
            (book_id, title[:200], body.profile_id, len(body.chapters), stamp, stamp),
        )
        for index, chapter in enumerate(body.chapters):
            text = chapter.text.strip()
            if not text:
                connection.rollback()
                raise AudiobookError("chapter_text_required")
            job_id = uuid.uuid4().hex
            chapter_title = (chapter.title or f"Chapter {index + 1}").strip()[:200]
            connection.execute(
                """
                INSERT INTO audiobook_jobs (
                    id, book_id, chapter_index, chapter_title, chapter_text,
                    status, detail, output_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, NULL, ?, ?)
                """,
                (job_id, book_id, index, chapter_title, text[:100_000], detail, stamp, stamp),
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
            # Ensure book exists.
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
