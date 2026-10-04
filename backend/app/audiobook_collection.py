"""Collection ZIP with a plain-text manifest. Pandrator source is not vendored."""
from __future__ import annotations

import zipfile
from pathlib import Path

from . import audiobooks
from .audiobook_cue import render_cue


def _safe(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_ " else "_" for char in value).strip()
    return cleaned or "audiobook"


def write_collection(book_id: str) -> Path:
    book = audiobooks.get_book(book_id)
    if book.status != "done":
        raise audiobooks.AudiobookError("export_not_ready", 404)
    jobs = audiobooks.list_jobs(book_id=book_id)
    root = audiobooks.book_dir(book_id)
    chapters: list[tuple[str, Path]] = []
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
        spans = audiobooks.pause_spans(job.id)
        span_text = ",".join(f"{item['start_ms']}-{item['end_ms']}" for item in spans)
        ready = "ready" if job.language_ready else "not_ready"
        lines.append(f"{job.chapter_index + 1}\t{job.chapter_title}\t{job.language}\t{ready}\t{name}\t{span_text}")
    manifest = "\n".join(lines) + "\n"
    cue = render_cue(book.title, book.author, chapters)
    target = root / "collection.zip"
    temporary = root / ".collection.zip.partial"
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
    return render_cue(book.title, book.author, chapters)


def download_name(book_id: str, suffix: str) -> str:
    return f"{_safe(audiobooks.get_book(book_id).title)}.{suffix}"
