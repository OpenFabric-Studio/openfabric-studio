"""Bounded audio-version APIs; all paths and ownership stay on the backend."""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Path
from fastapi.responses import FileResponse

from .. import audio_versions
from ..audio_version_contracts import AudioVersion, CreateAudioVersionRequest, TrackAudioVersionsResponse
from ..tagging import TaggedDownloadOptions, TaggedFileResponse
from .tagged_download_response import download_options, tagged_download_response

router = APIRouter(prefix='/api/tracks', tags=['audio-versions'])
VersionId = Annotated[str, Path(pattern=r'^[0-9a-f]{32}$')]
TrackId = Annotated[int, Path(gt=0)]
logger = logging.getLogger(__name__)


@contextmanager
def _storage_errors() -> Iterator[None]:
    try:
        yield
    except (OSError, sqlite3.Error, ValueError) as exc:
        logger.exception('Audio version storage operation failed')
        raise HTTPException(status_code=500, detail='audio_version_storage_failed') from exc


@router.get('/{track_id}/versions', response_model=TrackAudioVersionsResponse)
async def list_versions(track_id: TrackId) -> TrackAudioVersionsResponse:
    with _storage_errors():
        return audio_versions.list_versions(track_id)


@router.post('/{track_id}/versions', response_model=AudioVersion)
async def create_version(track_id: TrackId, request: CreateAudioVersionRequest) -> AudioVersion:
    with _storage_errors():
        return audio_versions.start_version(track_id, request.voice_id)


@router.get('/{track_id}/versions/{version_id}/audio')
async def version_audio(track_id: TrackId, version_id: VersionId) -> FileResponse:
    with _storage_errors():
        source = audio_versions.resolve_source(track_id, version_id)
    return FileResponse(source, filename=source.name)


@router.get('/{track_id}/versions/{version_id}/download')
async def version_download(track_id: TrackId, version_id: VersionId,
                           options: Annotated[TaggedDownloadOptions, Depends(download_options)], request: Request) -> TaggedFileResponse:
    return await tagged_download_response(track_id, version_id, None, options, request)


@router.post('/{track_id}/versions/{version_id}/cancel', response_model=AudioVersion)
async def cancel_version(track_id: TrackId, version_id: VersionId) -> AudioVersion:
    with _storage_errors():
        return await audio_versions.cancel_version(track_id, version_id)


@router.post('/{track_id}/versions/{version_id}/retry', response_model=AudioVersion)
async def retry_version(track_id: TrackId, version_id: VersionId) -> AudioVersion:
    with _storage_errors():
        return await audio_versions.retry_version(track_id, version_id)
