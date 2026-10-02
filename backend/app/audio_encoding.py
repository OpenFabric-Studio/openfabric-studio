"""Validated encoding profiles; defaults affect future exports only."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)
from pydantic_core import PydanticCustomError

from . import db
from .atomic_files import document_lock, write_object
from .audio_version_contracts import AudioVersionId
from .contracts import Contract, JobStatus, JsonObject

logger = logging.getLogger(__name__)
AudioExportFormat = Literal["mp3", "wav", "flac"]
SampleRate = Literal[44100, 48000]
Channels = Literal[1, 2]
_object = TypeAdapter(JsonObject)


class EncodingContract(Contract):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @field_validator(
        "channels",
        "sample_rate",
        "bit_depth",
        "bitrate_kbps",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def integer_choices(cls, value: object) -> object:
        if not isinstance(value, int) or isinstance(value, bool):
            raise PydanticCustomError("integer_choice", "Integer choice required")
        return value


class Mp3EncodingSettings(EncodingContract):
    mode: Literal["cbr", "vbr"] = "cbr"
    bitrate_kbps: Literal[128, 192, 256, 320] = 320
    vbr_quality: int = Field(default=2, ge=0, le=9, strict=True)
    sample_rate: SampleRate = 48000
    channels: Channels = 2


class WavEncodingSettings(EncodingContract):
    bit_depth: Literal[16, 24, 32] = 24
    sample_rate: SampleRate = 48000
    channels: Channels = 2


class FlacEncodingSettings(EncodingContract):
    bit_depth: Literal[16, 24] = 24
    compression_level: int = Field(default=5, ge=0, le=8, strict=True)
    sample_rate: SampleRate = 48000
    channels: Channels = 2


class AudioEncodingSettings(EncodingContract):
    mp3: Mp3EncodingSettings = Field(default_factory=Mp3EncodingSettings)
    wav: WavEncodingSettings = Field(default_factory=WavEncodingSettings)
    flac: FlacEncodingSettings = Field(default_factory=FlacEncodingSettings)


class AudioSettingsResponse(EncodingContract):
    settings: AudioEncodingSettings
    defaults: AudioEncodingSettings


class CreateAudioExportRequest(EncodingContract):
    format: AudioExportFormat
    settings: AudioEncodingSettings | None = None


StemName = Literal["vocals", "drums", "bass", "other"]


class AudioExportResult(EncodingContract):
    id: AudioVersionId
    track_id: int = Field(gt=0)
    format: AudioExportFormat
    status: JobStatus
    error_code: str = ""
    created_at: str
    filename: str | None = None
    audio_url: str | None = None
    settings: AudioEncodingSettings


class AudioExportResponse(AudioExportResult):
    version_id: AudioVersionId


class StemAudioExportResponse(AudioExportResult):
    stem_name: StemName


class StemAudioExportsResponse(EncodingContract):
    exports: list[StemAudioExportResponse] = Field(default_factory=list)


class CreateStemExportRequest(EncodingContract):
    format: AudioExportFormat = 'mp3'


class AudioExportsResponse(EncodingContract):
    exports: list[AudioExportResponse] = Field(default_factory=list)


class AudioEncodingError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def settings_path() -> Path:
    # Existing library migration already moves the complete files subtree.
    root = db.FILES_DIR.resolve()
    path = root / "_audio_settings" / "settings.json"
    if not path.resolve().is_relative_to(root):
        raise AudioEncodingError("settings_invalid")
    return path


def load_settings() -> AudioEncodingSettings:
    path = settings_path()
    with document_lock(path):
        if not path.exists():
            return AudioEncodingSettings()
        try:
            if path.stat().st_size > 16384:
                raise ValueError("oversized settings")
            return AudioEncodingSettings.model_validate_json(path.read_bytes())
        except (OSError, ValueError, ValidationError) as exc:
            logger.warning("Unable to read audio settings", exc_info=True)
            raise AudioEncodingError("settings_invalid") from exc


def get_settings() -> AudioSettingsResponse:
    return AudioSettingsResponse(
        settings=load_settings(), defaults=AudioEncodingSettings()
    )


def save_settings(settings: AudioEncodingSettings) -> AudioSettingsResponse:
    validated = AudioEncodingSettings.model_validate_json(settings.model_dump_json())
    path = settings_path()
    try:
        with document_lock(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            write_object(path, _object.validate_json(validated.model_dump_json()))
    except OSError as exc:
        logger.warning("Unable to save audio settings", exc_info=True)
        raise AudioEncodingError("settings_write_failed") from exc
    return AudioSettingsResponse(settings=validated, defaults=AudioEncodingSettings())


def encoding_args(
    format: AudioExportFormat, settings: AudioEncodingSettings
) -> list[str]:
    if format == "mp3":
        mp3 = settings.mp3
        rate = (
            ["-b:a", f"{mp3.bitrate_kbps}k"]
            if mp3.mode == "cbr"
            else ["-q:a", str(mp3.vbr_quality)]
        )
        return [
            "-c:a",
            "libmp3lame",
            *rate,
            "-ar",
            str(mp3.sample_rate),
            "-ac",
            str(mp3.channels),
            "-f",
            "mp3",
        ]
    if format == "wav":
        wav = settings.wav
        codec = {16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_f32le"}[wav.bit_depth]
        return [
            "-c:a",
            codec,
            "-ar",
            str(wav.sample_rate),
            "-ac",
            str(wav.channels),
            "-f",
            "wav",
        ]
    flac = settings.flac
    return [
        "-c:a",
        "flac",
        "-sample_fmt",
        "s16" if flac.bit_depth == 16 else "s32",
        "-bits_per_raw_sample",
        str(flac.bit_depth),
        "-compression_level",
        str(flac.compression_level),
        "-ar",
        str(flac.sample_rate),
        "-ac",
        str(flac.channels),
        "-f",
        "flac",
    ]


CLIENT_MODELS: list[type[BaseModel]] = [
    Mp3EncodingSettings,
    WavEncodingSettings,
    FlacEncodingSettings,
    AudioEncodingSettings,
    AudioSettingsResponse,
    CreateAudioExportRequest,
    AudioExportResponse,
    AudioExportsResponse,
    StemAudioExportResponse,
    StemAudioExportsResponse,
    CreateStemExportRequest,
]
