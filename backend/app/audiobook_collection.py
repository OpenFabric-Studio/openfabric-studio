"""Collection ZIP with a plain-text manifest. Pandrator source is not vendored."""
from __future__ import annotations

import zipfile
import hashlib
import uuid
import asyncio
import logging
import re
import threading
from pathlib import Path

from fastapi import HTTPException
from starlette.responses import FileResponse, Response
from starlette.types import Receive, Scope, Send

from . import audiobooks
from .audiobook_cue import render_cue
from .atomic_files import document_lock
from .job_lifecycle import await_cleanup

_LOG = logging.getLogger(__name__)
_SNAPSHOT = re.compile(r"collection-[0-9a-f]{64}\.zip")
_STATE_LOCK = threading.Lock()
_ACTIVE: dict[Path, dict[Path, int]] = {}
_LATEST: dict[Path, Path] = {}


def _remove_snapshot(path: Path) -> None:
    if _SNAPSHOT.fullmatch(path.name) and not path.is_symlink():
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # A cache cleanup failure must not invalidate a published export.
            _LOG.warning("Could not prune audiobook collection cache", exc_info=True)


def _prune_snapshots(root: Path, current: Path) -> None:
    with _STATE_LOCK:
        protected = set(_ACTIVE.get(root, {}))
    try:
        for candidate in root.iterdir():
            if candidate != current and candidate not in protected and candidate.is_file():
                _remove_snapshot(candidate)
    except OSError:
        _LOG.warning("Could not inspect audiobook collection cache", exc_info=True)


def _release_snapshot(path: Path) -> None:
    root = path.parent
    # Use the owned directory even if configuration changed during the response.
    # This is the same lock used by publication and preparation for that book.
    with document_lock(root / ".publication"):
        with _STATE_LOCK:
            active = _ACTIVE.get(root)
            if active is None or path not in active:
                return
            if active[path] > 1:
                active[path] -= 1
                return
            del active[path]
            obsolete = path != _LATEST.get(root)
            if not active:
                del _ACTIVE[root]
                _LATEST.pop(root, None)
        if obsolete:
            _remove_snapshot(path)


class CollectionResponse(Response):
    """Own a snapshot from preparation through the last ASGI body chunk."""
    def __init__(self, book_id: str) -> None:
        super().__init__(media_type="application/zip")
        self.book_id = book_id
        self._snapshot: Path | None = None
        self._file_response: FileResponse | None = None

    def _prepare(self) -> None:
        with audiobooks.publication_lock(self.book_id):
            path = _write_collection(self.book_id)
            root = path.parent.resolve()
            path = root / path.name
            response = FileResponse(path, media_type="application/zip", filename=download_name(self.book_id, "zip"))
            with _STATE_LOCK:
                active = _ACTIVE.setdefault(root, {})
                active[path] = active.get(path, 0) + 1
                _LATEST[root] = path
            self._snapshot = path
            self._file_response = response
            _prune_snapshots(root, path)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Preparing in __call__ avoids a lease left behind by an abandoned
        # handler. Drain the thread before releasing ownership on cancellation.
        try:
            try:
                await await_cleanup(asyncio.to_thread(self._prepare))
            except audiobooks.AudiobookError as error:
                raise HTTPException(status_code=error.status, detail=error.code) from error
            except OSError as error:
                _LOG.exception("Audiobook collection preparation failed")
                raise HTTPException(status_code=503, detail="audiobook_storage_unavailable") from error
            response = self._file_response
            if response is None:
                raise RuntimeError("Collection preparation did not produce a response")
            # pathsend can return before the server opens the file. Retain the
            # Range/HEAD implementation, but send owned bytes before releasing.
            stream_scope: Scope = dict(scope)
            stream_scope["extensions"] = {name: value for name, value in scope.get("extensions", {}).items()
                                          if name != "http.response.pathsend"}
            await response(stream_scope, receive, send)
        finally:
            snapshot = self._snapshot
            self._snapshot = None
            self._file_response = None
            if snapshot is not None:
                await await_cleanup(asyncio.to_thread(_release_snapshot, snapshot))


def speaker_note(spans: list[dict[str, object]]) -> str:
    changes: list[tuple[str, int]] = []
    for span in spans:
        name = str(span.get("speaker") or "").replace("\t", " ").replace("\n", " ")
        start = span.get("start_ms")
        if not name or not isinstance(start, int):
            continue
        if changes and changes[-1][0] == name:
            continue
        changes.append((name, start))
    if len(changes) <= 1:
        return ""
    return "\tspeakers " + ";".join(f"{name} {start}" for name, start in changes)


def _safe(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_ " else "_" for char in value).strip()
    return cleaned or "audiobook"


def write_collection(book_id: str) -> Path:
    with audiobooks.publication_lock(book_id):
        return _write_collection(book_id)


def _write_collection(book_id: str) -> Path:
    with audiobooks._LOCK:
        book = audiobooks.get_book(book_id)
        if book.status != "done":
            raise audiobooks.AudiobookError("export_not_ready", 404)
        jobs = audiobooks.list_jobs(book_id=book_id)
        root = audiobooks.book_dir(book_id)
        chapters: list[tuple[str, Path]] = []
        chapter_speakers: list[list[dict[str, object]]] = []
        lines = [
            "OpenFabric audiobook collection",
            f"title: {book.title}",
            f"author: {book.author}",
            f"language: {book.language}",
            "chapters:",
        ]
        for job in jobs:
            try:
                path = audiobooks.chapter_audio_path(book_id, job.chapter_index)
            except audiobooks.AudiobookError:
                lines.append(f"{job.chapter_index + 1}\t{job.chapter_title}\t{job.language}\tmissing\t")
                continue
            name = path.name
            chapters.append((job.chapter_title, path))
            chapter_speakers.append(audiobooks.cast_spans(job.id))
            spans = audiobooks.pause_spans(job.id)
            span_text = ",".join(f"{item['start_ms']}-{item['end_ms']}" for item in spans)
            ready = "ready" if job.language_ready else "not_ready"
            lines.append(f"{job.chapter_index + 1}\t{job.chapter_title}\t{job.language}\t{ready}\t{name}\t{span_text}{speaker_note(chapter_speakers[-1])}")
        manifest = "\n".join(lines) + "\n"
        cue = render_cue(book.title, book.author, chapters, speakers=chapter_speakers)
    # FileResponse opens its file after the handler returns. A later
    # regeneration must not replace the archive that response references.
    digest = hashlib.sha256((book.model_dump_json() + manifest + cue + "".join(job.model_dump_json() for job in jobs)).encode())
    export = audiobooks.export_path_for(book_id)
    for source in [export, *(path for _title, path in chapters)]:
        with source.open("rb") as handle:
            digest.update(hashlib.file_digest(handle, "sha256").digest())
    snapshot = digest.hexdigest()
    target = root / f"collection-{snapshot}.zip"
    if target.is_symlink():
        raise audiobooks.AudiobookError("audiobook_storage_unavailable", 503)
    if target.is_file():
        return target
    temporary = root / f".collection-{uuid.uuid4().hex}.zip.partial"
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("MANIFEST.txt", manifest)
            archive.writestr("book.cue", cue)
            export = audiobooks.export_path_for(book_id)
            archive.write(export, "export.wav")
            for _title, path in chapters:
                archive.write(path, f"chapters/{path.name}")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def cue_text(book_id: str) -> str:
    book = audiobooks.get_book(book_id)
    if book.status != "done":
        raise audiobooks.AudiobookError("export_not_ready", 404)
    jobs = audiobooks.list_jobs(book_id=book_id)
    chapters = [(job.chapter_title, audiobooks.chapter_audio_path(book_id, job.chapter_index)) for job in jobs]
    return render_cue(book.title, book.author, chapters, speakers=[audiobooks.cast_spans(job.id) for job in jobs])


def download_name(book_id: str, suffix: str) -> str:
    return f"{_safe(audiobooks.get_book(book_id).title)}.{suffix}"
