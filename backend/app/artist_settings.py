"""Validated artist metadata persisted atomically with the library."""
from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from . import db
from .atomic_files import document_lock, write_object
from .contracts import Contract, JsonObject

logger = logging.getLogger(__name__)
_json = TypeAdapter(JsonObject)


class ArtistSettings(Contract):
    model_config = ConfigDict(extra="forbid")
    artist: str = Field(max_length=120, strict=True, pattern=r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*$")

    @field_validator("artist")
    @classmethod
    def normalize_artist(cls, value: str) -> str:
        return " ".join(value.split())


class ArtistSettingsError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def settings_path() -> Path:
    root = db.FILES_DIR.resolve()
    path = root / "_artist_settings" / "settings.json"
    if path.resolve() != path:
        raise ArtistSettingsError("artist_settings_invalid")
    return path


def get_settings() -> ArtistSettings:
    path = settings_path()
    with document_lock(path):
        if not path.exists():
            return ArtistSettings(artist="")
        try:
            if path.stat().st_size > 4096:
                raise ValueError("oversized artist settings")
            return ArtistSettings.model_validate_json(path.read_bytes())
        except (OSError, ValueError, ValidationError) as exc:
            logger.warning("Unable to read artist settings", exc_info=True)
            raise ArtistSettingsError("artist_settings_invalid") from exc


def save_settings(settings: ArtistSettings) -> ArtistSettings:
    validated = ArtistSettings.model_validate_json(settings.model_dump_json())
    path = settings_path()
    try:
        with document_lock(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            write_object(path, _json.validate_json(validated.model_dump_json()))
    except OSError as exc:
        logger.warning("Unable to save artist settings", exc_info=True)
        raise ArtistSettingsError("artist_settings_write_failed") from exc
    return validated
