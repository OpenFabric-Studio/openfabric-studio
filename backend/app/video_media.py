"""Bounded ffprobe/decoder validation before video artifacts become reusable."""

from __future__ import annotations
import math, shutil, sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
import asyncio
from pydantic import BaseModel, Field, ValidationError
from .config import FFMPEG_BIN_DIR
from .job_lifecycle import spawn_process, communicate_process


class VideoMediaError(Exception):
    def __init__(self, code: str = "invalid_media") -> None:
        super().__init__(code)
        self.code = code


def tool(name: str) -> str:
    local = FFMPEG_BIN_DIR / (name + ".exe" if sys.platform == "win32" else name)
    found = str(local) if local.is_file() else shutil.which(name)
    if found is None:
        raise VideoMediaError("ffmpeg_missing")
    return found


class Stream(BaseModel):
    codec_type: str = ""
    width: int = 0
    height: int = 0
    avg_frame_rate: str = "0/1"
    duration: str = "0"


class Format(BaseModel):
    duration: str = "0"


class Probe(BaseModel):
    streams: list[Stream] = Field(default_factory=list)
    format: Format = Field(default_factory=Format)


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    fps: float
    video_duration: float
    audio_duration: float


def duration(value: str) -> float:
    try:
        result = float(value)
    except ValueError:
        return 0
    return result if math.isfinite(result) and result >= 0 else 0


async def probe_media(path: Path) -> MediaInfo:
    if not path.is_file() or path.stat().st_size == 0:
        raise VideoMediaError()
    proc = await spawn_process(
        tool("ffprobe"),
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await communicate_process(proc, 30)
    if proc.returncode != 0:
        raise VideoMediaError()
    try:
        probe = Probe.model_validate_json(out)
    except ValidationError as exc:
        raise VideoMediaError() from exc
    video = next((item for item in probe.streams if item.codec_type == "video"), None)
    audio = next((item for item in probe.streams if item.codec_type == "audio"), None)
    if video is None:
        raise VideoMediaError()
    try:
        fps = float(Fraction(video.avg_frame_rate))
    except (ValueError, ZeroDivisionError):
        fps = 0
    return MediaInfo(
        video.width,
        video.height,
        fps,
        duration(video.duration),
        duration(audio.duration) if audio else 0,
    )


async def validate_media(
    path: Path,
    expected_seconds: float,
    size: tuple[int, int],
    *,
    require_audio: bool = False,
    decode: bool = True,
) -> MediaInfo:
    info = await probe_media(path)
    tolerance = max(2 / 24, 0.1)
    if (
        not math.isfinite(expected_seconds)
        or expected_seconds <= 0
        or (info.width, info.height) != size
        or abs(info.fps - 24) > 0.01
        or abs(info.video_duration - expected_seconds) > tolerance
    ):
        raise VideoMediaError()
    if require_audio and abs(info.audio_duration - expected_seconds) > tolerance:
        raise VideoMediaError()
    if decode:
        maps = ["-map", "0:v:0"] + (["-map", "0:a:0"] if require_audio else [])
        proc = await spawn_process(
            tool("ffmpeg"),
            "-v",
            "error",
            "-xerror",
            "-i",
            str(path),
            *maps,
            "-f",
            "null",
            "-",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, _ = await communicate_process(proc, 120)
        if proc.returncode != 0:
            raise VideoMediaError()
    return info
