"""Durable chapter-section cache keyed by narrator and spoken text.

Completed PCM is copied out of the book directory so a later book can reuse it.
This is an OpenFabric reimplementation. No upstream audiobook source is vendored.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import uuid
import wave
from contextlib import closing
from pathlib import Path

from . import audiobooks

_LOG = logging.getLogger(__name__)


def _key(profile_id: str, text: str, render_identity: str) -> str:
    return hashlib.sha256(f"v2\n{profile_id}\n{render_identity}\n{text}".encode()).hexdigest()


def _root() -> Path:
    path = audiobooks.books_root() / "_chapter_cache"
    if path.is_symlink() or not path.resolve().is_relative_to(audiobooks.books_root().resolve()):
        raise audiobooks.AudiobookError("audiobook_storage_unavailable", 503)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _valid_wav(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        with wave.open(str(path), "rb") as handle:
            return handle.getnframes() > 0 and handle.getsampwidth() == 2
    except (OSError, wave.Error):
        return False


def reuse(profile_id: str, text: str, target: Path, *, render_identity: str | None = None) -> bool:
    if render_identity is None:
        return False
    key = _key(profile_id, text, render_identity)
    name = f"{key}.wav"
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        audiobooks._ensure_schema(connection)
        row = connection.execute(
            "SELECT wav_name FROM audiobook_chapter_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
    if row is None or str(row["wav_name"]) != name:
        return False
    source = _root() / name
    if not _valid_wav(source) or not source.resolve().is_relative_to(_root().resolve()):
        return False
    shutil.copyfile(source, target)
    return _valid_wav(target)


def store(profile_id: str, text: str, source: Path, *, render_identity: str | None = None) -> None:
    if render_identity is None:
        return
    if not _valid_wav(source):
        return
    key = _key(profile_id, text, render_identity)
    name = f"{key}.wav"
    root = _root()
    destination = root / name
    temporary = root / f".{key}.{uuid.uuid4().hex}.partial.wav"
    try:
        shutil.copyfile(source, temporary)
        if not _valid_wav(temporary):
            return
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        temporary.replace(destination)
        with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
            audiobooks._ensure_schema(connection)
            connection.execute(
                """INSERT INTO audiobook_chapter_cache
                   (cache_key, profile_id, text_sha256, wav_name, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(cache_key) DO UPDATE SET
                     wav_name = excluded.wav_name,
                     created_at = excluded.created_at""",
                (key, profile_id, hashlib.sha256(text.encode()).hexdigest(), name, audiobooks._now()),
            )
            connection.commit()
    except OSError:
        _LOG.warning("Chapter cache was not stored", exc_info=True)
    finally:
        temporary.unlink(missing_ok=True)
