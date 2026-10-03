"""WAV stays the canonical join. MP3 and M4B are separate ffmpeg publishes.

Chapter markers and title/author tags are written by OpenFabric. Missing
encoders are reported; they do not pretend the file exists.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import uuid
from pathlib import Path

from . import audiobooks

_LOG = logging.getLogger(__name__)
_FFMPEG_TIMEOUT = 120


def available_encoders() -> set[str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return set()
    try:
        completed = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
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


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise audiobooks.AudiobookError("export_codec_missing", 503)
    return subprocess.run(
        [ffmpeg, *args],
        capture_output=True,
        text=True,
        timeout=_FFMPEG_TIMEOUT,
        check=False,
    )


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
        temporary.unlink(missing_ok=True)


def publish_formats(
    book_id: str,
    *,
    title: str,
    author: str,
    chapters: list[tuple[str, Path]],
    cover: Path | None,
) -> tuple[Path | None, Path | None, str]:
    """Return mp3 path, m4b path, and a short machine-readable note."""
    root = audiobooks.book_dir(book_id)
    wav = root / "export.wav"
    if not wav.is_file():
        raise audiobooks.AudiobookError("export_not_ready", 404)
    encoders = available_encoders()
    notes: list[str] = []
    mp3_path: Path | None = None
    m4b_path: Path | None = None
    if "libmp3lame" not in encoders:
        notes.append("mp3:export_codec_missing")
    else:
        target = root / "export.mp3"
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
        target = root / "export.m4b"
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
