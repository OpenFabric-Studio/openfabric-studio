"""SQLite-backed audiobooks: chapter speech jobs + ffmpeg export.

Creates consent-backed chapter jobs, synthesizes them serially via the shared
GPT-SoVITS speech-clone worker, then concatenates chapter WAVs into one export.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from . import speech_clone, voice_profiles
from .audiobook_contracts import (
    AudiobookBook,
    AudiobookCreateResponse,
    AudiobookJob,
    CreateAudiobookRequest,
)
from .config import DATA_DIR, FFMPEG_BIN_DIR

logger = logging.getLogger(__name__)

_ID = re.compile(r"^[0-9a-f]{32}$")
_LOCK = threading.RLock()
_WORKERS: dict[str, threading.Thread] = {}

# Overridable in tests.
BOOKS_ROOT = DATA_DIR / "audiobooks"

# v1 caps — keep chapter text within GPT-SoVITS comfort; books stay modest.
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
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def books_root() -> Path:
    root = BOOKS_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def book_dir(book_id: str) -> Path:
    if not _ID.fullmatch(book_id):
        raise AudiobookError("invalid_book_id", 404)
    path = books_root() / book_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def chapters_dir(book_id: str) -> Path:
    path = book_dir(book_id) / "chapters"
    path.mkdir(parents=True, exist_ok=True)
    return path


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


def _ffmpeg_bin() -> str | None:
    import sys

    local = FFMPEG_BIN_DIR / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if local.is_file():
        return str(local)
    return shutil.which("ffmpeg")


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


def create_book(body: CreateAudiobookRequest) -> AudiobookCreateResponse:
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
    """Re-queue failed chapter jobs and restart the serial worker."""
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
        if not failed:
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
    if _sync_worker():
        _run_book(book_id)
        return
    with _LOCK:
        existing = _WORKERS.get(book_id)
        if existing is not None and existing.is_alive():
            return
        thread = threading.Thread(
            target=_run_book,
            args=(book_id,),
            daemon=True,
            name=f"audiobook:{book_id}",
        )
        _WORKERS[book_id] = thread
        thread.start()


def _run_book(book_id: str) -> None:
    try:
        _process_book(book_id)
    except Exception:  # noqa: BLE001 — never kill the process from a worker thread
        logger.exception("audiobook worker crashed for %s", book_id)
        try:
            with _LOCK, closing(_connect()) as connection:
                _ensure_schema(connection)
                _update_book(connection, book_id, status="failed")
                connection.commit()
        except Exception:  # noqa: BLE001
            logger.exception("failed to mark audiobook %s failed", book_id)


def _process_book(book_id: str) -> None:
    with _LOCK, closing(_connect()) as connection:
        _ensure_schema(connection)
        book_row = connection.execute(
            "SELECT * FROM audiobook_books WHERE id = ?", (book_id,)
        ).fetchone()
        if book_row is None:
            return
        profile_id = str(book_row["profile_id"])
        _update_book(connection, book_id, status="running", clear_export=True)
        connection.commit()
        job_rows = connection.execute(
            """
            SELECT * FROM audiobook_jobs
            WHERE book_id = ? AND status IN ('queued', 'running')
            ORDER BY chapter_index ASC
            """,
            (book_id,),
        ).fetchall()
        pending = [dict(row) for row in job_rows]

    for job in pending:
        _process_chapter(book_id, profile_id, job)

    _finalize_book(book_id)


def _process_chapter(book_id: str, profile_id: str, job: dict) -> None:
    job_id = str(job["id"])
    index = int(job["chapter_index"])
    text = str(job["chapter_text"])
    out = chapters_dir(book_id) / f"{index:04d}.wav"

    with _LOCK, closing(_connect()) as connection:
        _ensure_schema(connection)
        _update_job(
            connection,
            job_id,
            status="running",
            detail="Synthesizing chapter via GPT-SoVITS…",
        )
        connection.commit()

    try:
        # Consent re-check on every chapter (Phase D hardening, done early).
        profile = voice_profiles.get_profile(profile_id)
        if not profile.consent_confirmed:
            raise voice_profiles.VoiceProfileError("consent_required", 403)
        outcome = speech_clone.synthesize_to_path(
            profile_id=profile_id,
            text=text,
            output_path=out,
            require_consent=True,
        )
    except voice_profiles.VoiceProfileError as exc:
        with _LOCK, closing(_connect()) as connection:
            _ensure_schema(connection)
            _update_job(
                connection,
                job_id,
                status="failed",
                detail=exc.code,
            )
            connection.commit()
        return
    except Exception as exc:  # noqa: BLE001
        with _LOCK, closing(_connect()) as connection:
            _ensure_schema(connection)
            _update_job(
                connection,
                job_id,
                status="failed",
                detail=f"chapter_synth_error: {exc}"[:2000],
            )
            connection.commit()
        return

    if outcome.status in {"mock_completed", "completed"} and outcome.output_path is not None:
        with _LOCK, closing(_connect()) as connection:
            _ensure_schema(connection)
            _update_job(
                connection,
                job_id,
                status="done",
                detail=outcome.detail,
                output_path=str(outcome.output_path),
            )
            connection.commit()
        return

    with _LOCK, closing(_connect()) as connection:
        _ensure_schema(connection)
        _update_job(
            connection,
            job_id,
            status="failed",
            detail=f"{outcome.status}: {outcome.detail}"[:2000],
        )
        connection.commit()


def _concat_chapters(book_id: str, chapter_wavs: list[Path]) -> Path:
    ffmpeg = _ffmpeg_bin()
    export = book_dir(book_id) / "export.wav"
    if ffmpeg is None:
        raise AudiobookError("ffmpeg_missing", 500)

    list_file = book_dir(book_id) / "concat.txt"
    lines: list[str] = []
    for wav in chapter_wavs:
        # ffmpeg concat demuxer: escape single quotes in paths
        escaped = str(wav.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c:a",
        "pcm_s16le",
        str(export),
    ]
    env = os.environ.copy()
    if FFMPEG_BIN_DIR.is_dir():
        env["PATH"] = f"{FFMPEG_BIN_DIR}{os.pathsep}{env.get('PATH', '')}"
    completed = subprocess.run(
        command,
        capture_output=True,
        env=env,
        check=False,
        timeout=600,
    )
    if completed.returncode != 0 or not export.is_file():
        err = (completed.stderr or completed.stdout or b"").decode("utf-8", "replace")[:500]
        raise RuntimeError(f"ffmpeg concat failed: {err or 'no output'}")
    return export


def _finalize_book(book_id: str) -> None:
    with _LOCK, closing(_connect()) as connection:
        _ensure_schema(connection)
        jobs = connection.execute(
            """
            SELECT status, output_path, chapter_index FROM audiobook_jobs
            WHERE book_id = ?
            ORDER BY chapter_index ASC
            """,
            (book_id,),
        ).fetchall()
        statuses = [str(row["status"]) for row in jobs]
        if any(status in {"queued", "running"} for status in statuses):
            # Another worker pass still outstanding.
            return
        if any(status == "failed" for status in statuses) or not jobs:
            _update_book(connection, book_id, status="failed", clear_export=True)
            connection.commit()
            return
        wavs = []
        for row in jobs:
            if not row["output_path"]:
                _update_book(connection, book_id, status="failed", clear_export=True)
                connection.commit()
                return
            wavs.append(Path(str(row["output_path"])))

    try:
        export = _concat_chapters(book_id, wavs)
    except Exception as exc:  # noqa: BLE001
        logger.exception("audiobook export failed for %s", book_id)
        with _LOCK, closing(_connect()) as connection:
            _ensure_schema(connection)
            _update_book(connection, book_id, status="failed", clear_export=True)
            # Attach export error onto the last job detail for visibility.
            last = connection.execute(
                """
                SELECT id FROM audiobook_jobs
                WHERE book_id = ?
                ORDER BY chapter_index DESC LIMIT 1
                """,
                (book_id,),
            ).fetchone()
            if last is not None:
                connection.execute(
                    """
                    UPDATE audiobook_jobs
                    SET detail = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (f"export_failed: {exc}"[:2000], _now(), last["id"]),
                )
            connection.commit()
        return

    with _LOCK, closing(_connect()) as connection:
        _ensure_schema(connection)
        _update_book(connection, book_id, status="done", export_path=str(export))
        connection.commit()
