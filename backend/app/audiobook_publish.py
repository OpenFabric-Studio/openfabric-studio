"""WAV stays the canonical join. MP3 and M4B are separate ffmpeg publishes.

Chapter markers and title/author tags are written by OpenFabric. Missing
encoders are reported; they do not pretend the file exists.
"""
from __future__ import annotations

import logging
import asyncio
import os
import re
import shutil
import subprocess
import threading
import sys
import uuid
from pathlib import Path

from . import audiobooks
from .video_process import WorkerIdentity, WorkerReceipt, WorkerOutputError, spawn_owned, read_owned_output, terminate_verified
from .job_lifecycle import await_cleanup, kill_process_tree
from pydantic import ValidationError

_LOG = logging.getLogger(__name__)
_FFMPEG_TIMEOUT = 120
_OWNED: dict[str, WorkerIdentity] = {}
_QUARANTINE: dict[str, WorkerIdentity] = {}
_DEFERRED_FILES: set[Path] = set()
_RECOVERY_BLOCKED: set[Path] = set()
_OWNER_LOCK = threading.RLock()

def cleanup_pending() -> bool:
    with _OWNER_LOCK:
        return bool(_QUARANTINE or _RECOVERY_BLOCKED)


def _group_exited(identity: WorkerIdentity) -> bool:
    """A gone POSIX leader alone does not prove its descendants are gone."""
    if sys.platform == "win32":
        return True  # terminate_verified checks the exact named Job Object.
    try:
        os.killpg(identity.pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


async def start() -> None:
    """Recover bounded, contained encoder receipts before admitting any new work."""
    root = audiobooks.books_root() / "_codec_workers"
    with _OWNER_LOCK:
        _RECOVERY_BLOCKED.clear()
    if root.is_symlink() or not root.resolve().is_relative_to(audiobooks.books_root().resolve()):
        with _OWNER_LOCK:
            _RECOVERY_BLOCKED.add(root)
        return
    if not root.exists():
        return
    try:
        for index, path in enumerate(root.iterdir()):
            if index >= 2000:
                with _OWNER_LOCK:
                    _RECOVERY_BLOCKED.add(root)
                break
            if path.suffix != ".json":
                continue
            if re.fullmatch(r"[0-9a-f]{32}\.json", path.name) is None or path.is_symlink() or not path.is_file():
                with _OWNER_LOCK:
                    _RECOVERY_BLOCKED.add(path)
                continue
            try:
                with path.open("rb") as handle:
                    raw = handle.read(65537)
                if len(raw) > 65536:
                    raise ValueError("codec_receipt_too_large")
                receipt = WorkerReceipt.model_validate_json(raw)
                identity = WorkerIdentity(pid=receipt.pid, token=receipt.token, receipt=str(path))
                with _OWNER_LOCK:
                    _QUARANTINE[path.stem] = identity
            except (OSError, ValidationError, ValueError):
                with _OWNER_LOCK:
                    _RECOVERY_BLOCKED.add(path)
    except OSError:
        with _OWNER_LOCK:
            _RECOVERY_BLOCKED.add(root)
    await shutdown()


def defer_cleanup(paths: list[Path]) -> None:
    """Keep partial bytes until every unverified encoder has actually exited."""
    from .audiobook_narration import _contained
    root = audiobooks.books_root()
    for path in paths:
        _contained(path, root)
    with _OWNER_LOCK:
        _DEFERRED_FILES.update(paths)


async def shutdown() -> None:
    """Retry exact owned identities when a prior process drain could not verify exit."""
    with _OWNER_LOCK:
        pending = tuple(_QUARANTINE.items())
    for key, identity in pending:
        try:
            verified = await await_cleanup(terminate_verified(identity)) and _group_exited(identity)
        except (OSError, TimeoutError):
            verified = False
        if verified:
            Path(identity.receipt).unlink(missing_ok=True)
            with _OWNER_LOCK:
                _QUARANTINE.pop(key, None)
    with _OWNER_LOCK:
        if not _QUARANTINE and not _RECOVERY_BLOCKED:
            files = tuple(_DEFERRED_FILES)
            _DEFERRED_FILES.clear()
        else:
            files = ()
    for path in files:
        path.unlink(missing_ok=True)


async def _run_owned(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    if cleanup_pending():
        raise audiobooks.AudiobookError("audiobook_export_cleanup_failed", 503)
    root = audiobooks.books_root() / "_codec_workers"
    from .audiobook_narration import _contained
    _contained(root, audiobooks.books_root())
    root.mkdir(exist_ok=True)
    key = uuid.uuid4().hex
    receipt = root / f"{key}.json"

    def identity_ready(identity: WorkerIdentity) -> None:
        with _OWNER_LOCK:
            _OWNED[key] = identity

    proc = await spawn_owned(command, receipt_path=receipt, stdout=asyncio.subprocess.PIPE, on_identity=identity_ready)
    try:
        output = await read_owned_output(proc, max_bytes=1_000_000, timeout=timeout)
        text = output.decode("utf-8", errors="replace")
        return subprocess.CompletedProcess(command, proc.returncode if proc.returncode is not None else 1, text, text)
    except (WorkerOutputError, TimeoutError):
        raise audiobooks.AudiobookError("export_failed", 503) from None
    finally:
        try:
            await await_cleanup(kill_process_tree(proc))
        except BaseException:
            with _OWNER_LOCK:
                identity = _OWNED.pop(key)
                _QUARANTINE[key] = identity
            _LOG.exception("Audiobook encoder cleanup could not verify exit")
            raise audiobooks.AudiobookError("audiobook_export_cleanup_failed", 503) from None
        else:
            with _OWNER_LOCK:
                _OWNED.pop(key, None)
            receipt.unlink(missing_ok=True)


def available_encoders() -> set[str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return set()
    try:
        completed = _run(["-hide_banner", "-encoders"], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    names: set[str] = set()
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0][:1] in {"A", "V"}:
            names.add(parts[1])
    return names


def _escape(value: str) -> str:
    return "".join("\\" + char if char in "\\#;=\n\r" else char for char in value)


def _wav_ms(path: Path) -> int:
    import wave
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        frames = handle.getnframes()
    if rate <= 0 or frames <= 0:
        raise audiobooks.AudiobookError("invalid_speech_audio")
    return max(1, int(frames * 1000 / rate))


def _metadata(title: str, author: str, chapters: list[tuple[str, Path]]) -> str:
    lines = [";FFMETADATA1", f"title={_escape(title)}", f"album={_escape(title)}"]
    if author:
        lines.append(f"artist={_escape(author)}")
    cursor = 0
    for index, (chapter_title, path) in enumerate(chapters, start=1):
        duration = _wav_ms(path)
        label = chapter_title.strip() or f"Chapter {index}"
        lines.extend([
            "",
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={cursor}",
            f"END={cursor + duration}",
            f"title={_escape(label)}",
        ])
        cursor += duration
    return "\n".join(lines) + "\n"


def _run(args: list[str], *, timeout: float = _FFMPEG_TIMEOUT) -> subprocess.CompletedProcess[str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise audiobooks.AudiobookError("export_codec_missing", 503)
    return asyncio.run(_run_owned([ffmpeg, *args], timeout))


def _publish_one(args: list[str], target: Path) -> bool:
    temporary = target.with_name(f"{target.stem}-{uuid.uuid4().hex}{target.suffix}")
    try:
        completed = _run([*args, str(temporary)])
        if completed.returncode != 0 or not temporary.is_file() or temporary.stat().st_size <= 0:
            _LOG.warning("Audiobook %s export failed: %s", target.suffix, (completed.stderr or "")[-400:])
            return False
        temporary.replace(target)
        return True
    except (OSError, subprocess.TimeoutExpired):
        _LOG.exception("Audiobook %s export failed", target.suffix)
        return False
    finally:
        if cleanup_pending():
            defer_cleanup([temporary])
        else:
            temporary.unlink(missing_ok=True)


def publish_formats(
    book_id: str,
    *,
    title: str,
    author: str,
    chapters: list[tuple[str, Path]],
    cover: Path | None,
    canonical_wav: Path | None = None,
    destination_stem: str = "export",
) -> tuple[Path | None, Path | None, str]:
    """Return mp3 path, m4b path, and a short machine-readable note."""
    root = audiobooks.book_dir(book_id)
    from .audiobook_narration import _contained
    if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", destination_stem) is None:
        raise audiobooks.AudiobookError("audiobook_storage_unavailable", 503)
    wav = _contained(canonical_wav if canonical_wav is not None else root / "export.wav", root)
    for _, chapter in chapters:
        _contained(chapter, root)
    if cover is not None:
        _contained(cover, root)
    if not wav.is_file():
        raise audiobooks.AudiobookError("export_not_ready", 404)
    encoders = available_encoders()
    notes: list[str] = []
    mp3_path: Path | None = None
    m4b_path: Path | None = None
    if "libmp3lame" not in encoders:
        notes.append("mp3:export_codec_missing")
    else:
        target = _contained(root / f"{destination_stem}.mp3", root)
        metadata = ["-metadata", f"title={title}", "-metadata", f"album={title}"]
        if author:
            metadata.extend(["-metadata", f"artist={author}"])
        if _publish_one(["-y", "-i", str(wav), "-vn", "-c:a", "libmp3lame", "-q:a", "4", *metadata, "-f", "mp3"], target):
            mp3_path = target
        else:
            notes.append("mp3:export_failed")
    if "aac" not in encoders:
        notes.append("m4b:export_codec_missing")
    else:
        meta = root / f".chapters.{uuid.uuid4().hex}.txt"
        target = _contained(root / f"{destination_stem}.m4b", root)
        try:
            meta.write_text(_metadata(title, author, chapters), encoding="utf-8")
            args = ["-y", "-i", str(wav)]
            meta_index = 1
            if cover is not None and cover.is_file():
                args.extend(["-i", str(cover)])
                meta_index = 2
            args.extend(["-i", str(meta), "-map", "0:a"])
            if meta_index == 2:
                args.extend(["-map", "1:v", "-c:v", "mjpeg", "-disposition:v:0", "attached_pic"])
            args.extend(["-map_metadata", str(meta_index), "-c:a", "aac", "-b:a", "96k", "-f", "mp4"])
            if _publish_one(args, target):
                m4b_path = target
            else:
                notes.append("m4b:export_failed")
        finally:
            meta.unlink(missing_ok=True)
    return mp3_path, m4b_path, ";".join(notes)[:500]
