"""Cue sheet for a finished audiobook. Reimplemented; no upstream cue code is copied."""
from __future__ import annotations

import wave
from pathlib import Path


def _index(seconds: float) -> str:
    frames = int(round(max(0.0, seconds) * 75))
    minutes, remainder = divmod(frames, 75 * 60)
    secs, frame = divmod(remainder, 75)
    return f"{minutes:02d}:{secs:02d}:{frame:02d}"


def _duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        if rate <= 0:
            return 0.0
        return handle.getnframes() / rate


def _quoted(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', "'") + '"'


def render_cue(
    title: str,
    performer: str,
    chapters: list[tuple[str, Path]],
    file_name: str = "export.wav",
    speakers: list[list[dict[str, object]]] | None = None,
) -> str:
    lines = [
        f"PERFORMER {_quoted(performer or 'OpenFabric')}",
        f"TITLE {_quoted(title or 'Audiobook')}",
        f"FILE {_quoted(file_name)} WAVE",
    ]
    cursor = 0.0
    for index, (chapter_title, path) in enumerate(chapters, start=1):
        lines.append(f"  TRACK {index:02d} AUDIO")
        lines.append(f"    TITLE {_quoted(chapter_title or f'Chapter {index}')}")
        chapter_speakers = speakers[index - 1] if speakers and index - 1 < len(speakers) else []
        names = {str(item.get("speaker") or "") for item in chapter_speakers}
        if len(names) > 1:
            for item in chapter_speakers:
                start = item.get("start_ms")
                speaker = str(item.get("speaker") or "")
                if not isinstance(start, int) or not speaker:
                    continue
                lines.append(f"    REM SPEAKER {_quoted(speaker)} {_index(cursor + start / 1000)}")
        lines.append(f"    INDEX 01 {_index(cursor)}")
        cursor += _duration(path)
    return "\n".join(lines) + "\n"
