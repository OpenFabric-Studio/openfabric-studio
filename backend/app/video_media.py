"""Bounded ffprobe/decoder validation before video artifacts become reusable."""

from __future__ import annotations
import math, shutil, sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, ValidationError
from .config import FFMPEG_BIN_DIR
from .video_process import WorkerOutputError, run_owned_command


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


class DisplaySideData(BaseModel):
    side_data_type: str = ''
    rotation: float = Field(default=0, allow_inf_nan=False)


class Stream(BaseModel):
    codec_type: str = ""
    width: int = 0
    height: int = 0
    avg_frame_rate: str = "0/1"
    duration: str = "0"
    sample_aspect_ratio: str = Field(default='1:1', max_length=64)
    side_data_list: list[DisplaySideData] = Field(default_factory=list)


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
    sample_aspect_ratio: float = 1
    rotation: float = 0

    @property
    def display_size(self) -> tuple[int, int]:
        if (not math.isfinite(self.sample_aspect_ratio) or self.sample_aspect_ratio <= 0
                or not math.isfinite(self.rotation) or min(self.width, self.height) < 2):
            raise VideoMediaError('unsupported_geometry')
        turns = self.rotation / 90
        if not math.isclose(turns, round(turns), abs_tol=0.000001):
            raise VideoMediaError('unsupported_geometry')
        display_width = self.width * self.sample_aspect_ratio
        if not math.isfinite(display_width):
            raise VideoMediaError('unsupported_geometry')
        width, height = max(2, round(display_width)), self.height
        return (height, width) if round(turns) % 2 else (width, height)


def duration(value: str) -> float:
    try:
        result = float(value)
    except ValueError:
        return 0
    return result if math.isfinite(result) and result >= 0 else 0


async def probe_media(path: Path, *, input_format: Literal['mov', 'matroska', 'avi'] | None = None) -> MediaInfo:
    if not path.is_file() or path.stat().st_size == 0:
        raise VideoMediaError()
    try:
        result = await run_owned_command([
            tool("ffprobe"),
            "-v",
            "error",
            *(['-f', input_format, '-protocol_whitelist', 'file,pipe'] if input_format is not None else []),
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ], timeout=30)
    except (WorkerOutputError, TimeoutError) as exc:
        raise VideoMediaError() from exc
    if result.returncode != 0:
        raise VideoMediaError()
    try:
        probe = Probe.model_validate_json(result.stdout)
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
    try:
        ratio = 1.0 if video.sample_aspect_ratio in ('N/A', '0:1', '') else float(Fraction(video.sample_aspect_ratio.replace(':', '/')))
    except (ValueError, ZeroDivisionError, OverflowError) as error:
        raise VideoMediaError('unsupported_geometry') from error
    rotation = next((item.rotation for item in video.side_data_list if item.side_data_type == 'Display Matrix'), 0)
    return MediaInfo(
        video.width,
        video.height,
        fps,
        duration(video.duration) or duration(probe.format.duration),
        (duration(audio.duration) or duration(probe.format.duration)) if audio else 0,
        ratio,
        rotation,
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
        try:
            result = await run_owned_command([
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
            ], timeout=120)
        except (WorkerOutputError, TimeoutError) as exc:
            raise VideoMediaError() from exc
        if result.returncode != 0:
            raise VideoMediaError()
    return info
