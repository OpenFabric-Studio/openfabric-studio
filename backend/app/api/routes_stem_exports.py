"""Owned stem exports using the user's shared audio encoding profile."""
from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import FileResponse

from .. import audio_exports
from ..audio_encoding import (AudioEncodingError, CreateStemExportRequest,
                              StemAudioExportResponse, StemAudioExportsResponse, StemName)

router = APIRouter(prefix='/api/tracks', tags=['stem-exports'])
TrackId = Annotated[int, Path(gt=0)]
ExportId = Annotated[str, Path(pattern=r'^[0-9a-f]{32}$')]


def _raise(exc: audio_exports.AudioExportError | AudioEncodingError) -> NoReturn:
    status = 404 if exc.code in {'track_not_found', 'stem_not_found', 'export_not_found'} else 409
    if exc.code in {'export_write_failed', 'settings_invalid', 'settings_write_failed'}:
        status = 503
    raise HTTPException(status, detail=exc.code) from exc


@router.get('/{track_id}/stems/{stem_name}/exports', response_model=StemAudioExportsResponse)
async def list_exports(track_id: TrackId, stem_name: StemName) -> StemAudioExportsResponse:
    try:
        return audio_exports.list_stem_exports(track_id, stem_name)
    except audio_exports.AudioExportError as exc:
        _raise(exc)


@router.post('/{track_id}/stems/{stem_name}/exports', response_model=StemAudioExportResponse)
async def create_export(track_id: TrackId, stem_name: StemName, body: CreateStemExportRequest) -> StemAudioExportResponse:
    try:
        return await audio_exports.create_stem_export(track_id, stem_name, body.format)
    except (audio_exports.AudioExportError, AudioEncodingError) as exc:
        _raise(exc)


@router.get('/{track_id}/stems/{stem_name}/exports/{export_id}', response_model=StemAudioExportResponse)
async def get_export(track_id: TrackId, stem_name: StemName, export_id: ExportId) -> StemAudioExportResponse:
    try:
        return audio_exports.get_stem_export(track_id, stem_name, export_id)
    except audio_exports.AudioExportError as exc:
        _raise(exc)


@router.post('/{track_id}/stems/{stem_name}/exports/{export_id}/cancel', response_model=StemAudioExportResponse)
async def cancel_export(track_id: TrackId, stem_name: StemName, export_id: ExportId) -> StemAudioExportResponse:
    try:
        return await audio_exports.cancel_stem_export(track_id, stem_name, export_id)
    except audio_exports.AudioExportError as exc:
        _raise(exc)


@router.post('/{track_id}/stems/{stem_name}/exports/{export_id}/retry', response_model=StemAudioExportResponse)
async def retry_export(track_id: TrackId, stem_name: StemName, export_id: ExportId) -> StemAudioExportResponse:
    try:
        return await audio_exports.retry_stem_export(track_id, stem_name, export_id)
    except (audio_exports.AudioExportError, AudioEncodingError) as exc:
        _raise(exc)


@router.get('/{track_id}/stems/{stem_name}/exports/{export_id}/audio')
async def export_audio(track_id: TrackId, stem_name: StemName, export_id: ExportId) -> FileResponse:
    try:
        path = audio_exports.stem_export_file(track_id, stem_name, export_id)
        return FileResponse(path, filename=f'{stem_name}.{path.suffix.lstrip(".")}')
    except audio_exports.AudioExportError as exc:
        _raise(exc)
