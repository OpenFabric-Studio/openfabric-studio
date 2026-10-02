"""Validated, offline speech reference catalog and explicit library imports."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import voice_profiles
from .voice_profile_contracts import SpeechVoiceProfile, StarterSpeechVoice
from .voice_profiles import VoiceProfileError

ASSETS_ROOT = Path(__file__).parent.parent / "assets" / "starter-voices"
_LOG = logging.getLogger(__name__)
_MAX_CATALOG_BYTES = 1024 * 1024
_MAX_AUDIO_BYTES = 2 * 1024 * 1024


class _StarterAsset(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(pattern=r"^vctk-p[0-9]{3}$")
    name: str = Field(min_length=1, max_length=120)
    language: Literal["en"]
    accent: str = Field(min_length=1, max_length=120)
    transcript: str = Field(min_length=1, max_length=2000)
    file_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*\.wav$", max_length=80)
    duration_seconds: float = Field(ge=3, le=10)
    sample_rate_hz: int = Field(ge=16000, le=96000)
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _StarterCatalog(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: Literal[1]
    source_url: Literal["https://datashare.ed.ac.uk/handle/10283/3443"]
    license_name: Literal["CC BY 4.0"]
    license_url: Literal["https://creativecommons.org/licenses/by/4.0/"]
    attribution: str = Field(min_length=1, max_length=1000)
    voices: list[_StarterAsset] = Field(min_length=1, max_length=32)


def _catalog() -> _StarterCatalog:
    try:
        root = ASSETS_ROOT.resolve(strict=True)
        manifest = ASSETS_ROOT / "catalog.json"
        if ASSETS_ROOT.is_symlink() or manifest.is_symlink() or manifest.resolve(strict=True).parent != root:
            raise ValueError("unsafe catalog path")
        with manifest.open("rb") as stream:
            contents = stream.read(_MAX_CATALOG_BYTES + 1)
        if len(contents) > _MAX_CATALOG_BYTES:
            raise ValueError("oversized catalog")
        catalog = _StarterCatalog.model_validate_json(contents)
        if len({asset.id for asset in catalog.voices}) != len(catalog.voices):
            raise ValueError("duplicate starter identifiers")
        return catalog
    except (OSError, ValueError, ValidationError, RuntimeError) as exc:
        _LOG.warning("Speech starter catalog unavailable: %s", exc)
        raise VoiceProfileError("starter_catalog_unavailable", 503) from exc


def _public_voice(asset: _StarterAsset, catalog: _StarterCatalog) -> StarterSpeechVoice:
    return StarterSpeechVoice(
        id=asset.id,
        name=asset.name,
        language=asset.language,
        accent=asset.accent,
        transcript=asset.transcript,
        duration_seconds=asset.duration_seconds,
        sample_rate_hz=asset.sample_rate_hz,
        audio_url=f"/api/voice-profiles/starter-voices/{asset.id}/audio",
        source_url=catalog.source_url,
        license_name=catalog.license_name,
        license_url=catalog.license_url,
        attribution=catalog.attribution,
    )


def list_starter_voices() -> list[StarterSpeechVoice]:
    """List licensed references without opening or changing the user's library."""
    catalog = _catalog()
    return [_public_voice(asset, catalog) for asset in catalog.voices]


def _get_asset(starter_id: str) -> _StarterAsset:
    for asset in _catalog().voices:
        if asset.id == starter_id:
            return asset
    raise VoiceProfileError("starter_voice_not_found", 404)


def _checked_audio(asset: _StarterAsset) -> tuple[Path, bytes]:
    try:
        root = ASSETS_ROOT.resolve(strict=True)
        path = ASSETS_ROOT / asset.file_name
        if ASSETS_ROOT.is_symlink() or path.is_symlink() or path.resolve(strict=True).parent != root:
            raise ValueError("unsafe starter audio path")
        with path.open("rb") as stream:
            audio = stream.read(_MAX_AUDIO_BYTES + 1)
        if len(audio) > _MAX_AUDIO_BYTES or hashlib.sha256(audio).hexdigest() != asset.audio_sha256:
            raise ValueError("starter audio checksum mismatch")
        return path, audio
    except (OSError, ValueError, RuntimeError) as exc:
        _LOG.warning("Speech starter audio unavailable: %s", exc)
        raise VoiceProfileError("starter_audio_unavailable", 503) from exc


def starter_audio_path(starter_id: str) -> Path:
    """Resolve only an allowlisted, checksum-verified bundled recording."""
    path, _ = _checked_audio(_get_asset(starter_id))
    return path


def import_starter_voice(starter_id: str) -> SpeechVoiceProfile:
    """Copy a licensed reference once, preserving an existing import's user edits."""
    asset = _get_asset(starter_id)
    path, audio = _checked_audio(asset)
    return voice_profiles.create_profile(
        name=asset.name,
        consent_confirmed=True,
        audio_bytes=audio,
        filename=path.name,
        notes=asset.transcript,
        starter_voice_id=asset.id,
    )
