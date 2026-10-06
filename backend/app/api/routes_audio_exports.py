"""Version-specific, durable export and download endpoints."""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Path
from fastapi.responses import FileResponse

from .. import audio_exports
from ..audio_encoding import (
    AudioEncodingError,
    AudioExportResponse,
    AudioExportsResponse,
    CreateAudioExportRequest,
)
from ..tagging import TaggedDownloadOptions, TaggedFileResponse
from .tagged_download_response import download_options, tagged_download_response

router = APIRouter(prefix="/api/tracks", tags=["audio-exports"])
TrackId = Annotated[int, Path(gt=0)]
VersionId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]


def _raise(exc: audio_exports.AudioExportError | AudioEncodingError) -> NoReturn:
    status = 404 if exc.code in {"track_not_found", "export_not_found"} else 409
    if exc.code in {"export_write_failed", "settings_invalid", "settings_write_failed"}:
        status = 503
    raise HTTPException(status, detail=exc.code) from exc


@router.get(
    "/{track_id}/versions/{version_id}/exports", response_model=AudioExportsResponse
)
async def list_exports(
    track_id: TrackId, version_id: VersionId
) -> AudioExportsResponse:
    try:
        return audio_exports.list_exports(track_id, version_id)
    except audio_exports.AudioExportError as exc:
        _raise(exc)


@router.post(
    "/{track_id}/versions/{version_id}/exports", response_model=AudioExportResponse
)
async def create_export(
    track_id: TrackId, version_id: VersionId, body: CreateAudioExportRequest
) -> AudioExportResponse:
    try:
        return await audio_exports.create_export(
            track_id, version_id, body.format, body.settings, operation=body.operation,source_origin=body.source_origin
        )
    except (audio_exports.AudioExportError, AudioEncodingError) as exc:
        _raise(exc)


@router.get(
    "/{track_id}/versions/{version_id}/exports/{export_id}",
    response_model=AudioExportResponse,
)
async def get_export(
    track_id: TrackId, version_id: VersionId, export_id: VersionId
) -> AudioExportResponse:
    try:
        return audio_exports.get_export(track_id, version_id, export_id)
    except audio_exports.AudioExportError as exc:
        _raise(exc)


@router.post(
    "/{track_id}/versions/{version_id}/exports/{export_id}/cancel",
    response_model=AudioExportResponse,
)
async def cancel_export(
    track_id: TrackId, version_id: VersionId, export_id: VersionId
) -> AudioExportResponse:
    try:
        return await audio_exports.cancel_export(track_id, version_id, export_id)
    except audio_exports.AudioExportError as exc:
        _raise(exc)


@router.post(
    "/{track_id}/versions/{version_id}/exports/{export_id}/retry",
    response_model=AudioExportResponse,
)
async def retry_export(
    track_id: TrackId, version_id: VersionId, export_id: VersionId
) -> AudioExportResponse:
    try:
        return await audio_exports.retry_export(track_id, version_id, export_id)
    except (audio_exports.AudioExportError, AudioEncodingError) as exc:
        _raise(exc)


@router.get("/{track_id}/versions/{version_id}/exports/{export_id}/audio")
async def export_audio(
    track_id: TrackId, version_id: VersionId, export_id: VersionId
) -> FileResponse:
    try:
        path = audio_exports.export_file(track_id, version_id, export_id)
        return FileResponse(path, filename=f"{version_id}{path.suffix}")
    except audio_exports.AudioExportError as exc:
        _raise(exc)


@router.get("/{track_id}/versions/{version_id}/exports/{export_id}/download")
async def export_download(track_id: TrackId, version_id: VersionId, export_id: VersionId,
                          options: Annotated[TaggedDownloadOptions, Depends(download_options)], request: Request) -> TaggedFileResponse:
    return await tagged_download_response(track_id, version_id, export_id, options, request)


@router.get('/{track_id}/versions/{version_id}/exports/{export_id}/provenance')
def export_provenance(track_id: TrackId,version_id: VersionId,export_id: VersionId) -> FileResponse:
    try:return FileResponse(audio_exports.manifest_file(track_id,version_id,export_id,'json'),media_type='application/json')
    except audio_exports.AudioExportError as exc:_raise(exc)


@router.get('/{track_id}/versions/{version_id}/exports/{export_id}/manifest')
def export_manifest(track_id: TrackId,version_id: VersionId,export_id: VersionId) -> FileResponse:
    try:return FileResponse(audio_exports.manifest_file(track_id,version_id,export_id,'txt'),media_type='text/plain',filename=f'{export_id}-provenance.txt')
    except audio_exports.AudioExportError as exc:_raise(exc)
