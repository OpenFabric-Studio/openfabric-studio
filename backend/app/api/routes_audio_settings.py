"""Encoding defaults are independent of data-folder/restart configuration."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..audio_encoding import (
    AudioEncodingError,
    AudioEncodingSettings,
    AudioSettingsResponse,
    get_settings,
    save_settings,
)

router = APIRouter(prefix="/api/settings/audio", tags=["settings"])


@router.get("", response_model=AudioSettingsResponse)
def audio_settings() -> AudioSettingsResponse:
    try:
        return get_settings()
    except AudioEncodingError as exc:
        raise HTTPException(503, detail=exc.code) from exc


@router.put("", response_model=AudioSettingsResponse)
def update_audio_settings(body: AudioEncodingSettings) -> AudioSettingsResponse:
    try:
        return save_settings(body)
    except AudioEncodingError as exc:
        raise HTTPException(503, detail=exc.code) from exc
