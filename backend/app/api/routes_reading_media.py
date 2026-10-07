"""Chapter presentation and retained-source handoff APIs."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from .. import reading_media, retained_audio, audiobooks, video_projects, voice_profiles
from ..reading_media_contracts import (ReadAlongRequest, ReadAlongExport, ReadAlongExportsResponse,
    RetainedAudioSource, RetainedAudioInfo, RetainedAudioVideoRequest)
from ..video_contracts import VideoProject

router = APIRouter(prefix='/api/reading-media', tags=['reading-media'])


def _error(error: reading_media.ReadingMediaError | audiobooks.AudiobookError | voice_profiles.VoiceProfileError | video_projects.VideoProjectError) -> HTTPException:
    return HTTPException(409 if isinstance(error, video_projects.VideoProjectError) else error.status, error.code)


@router.post('/books/{book_id}/chapters/{chapter_index}/exports', response_model=ReadAlongExport)
async def create_export(book_id: str, chapter_index: int, body: ReadAlongRequest) -> ReadAlongExport:
    try:
        return await reading_media.create(book_id, chapter_index, body)
    except (reading_media.ReadingMediaError, audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as error:
        raise _error(error) from error


@router.get('/books/{book_id}/chapters/{chapter_index}/exports', response_model=ReadAlongExportsResponse)
def list_exports(book_id: str, chapter_index: int) -> ReadAlongExportsResponse:
    try:
        return reading_media.list_exports(book_id, chapter_index)
    except reading_media.ReadingMediaError as error:
        raise _error(error) from error


@router.get('/exports/{identifier}', response_model=ReadAlongExport)
def get_export(identifier: str) -> ReadAlongExport:
    try:
        return reading_media.get(identifier)
    except reading_media.ReadingMediaError as error:
        raise _error(error) from error


@router.post('/exports/{identifier}/cancel', response_model=ReadAlongExport)
async def cancel_export(identifier: str) -> ReadAlongExport:
    try:
        return await reading_media.cancel(identifier)
    except reading_media.ReadingMediaError as error:
        raise _error(error) from error


@router.post('/exports/{identifier}/resume', response_model=ReadAlongExport)
async def resume_export(identifier: str) -> ReadAlongExport:
    try:
        return await reading_media.resume(identifier)
    except (reading_media.ReadingMediaError, audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as error:
        raise _error(error) from error


@router.get('/exports/{identifier}/{name}')
def export_file(identifier: str, name: str) -> FileResponse:
    try:
        path = reading_media.serve(identifier, name)
    except (reading_media.ReadingMediaError, voice_profiles.VoiceProfileError) as error:
        raise _error(error) from error
    types = {'movie.mp4': 'video/mp4', 'captions.srt': 'application/x-subrip',
             'captions.vtt': 'text/vtt', 'manifest.json': 'application/json'}
    return FileResponse(path, media_type=types[name], filename=f'{identifier}-{name}')


@router.post('/audio/info', response_model=RetainedAudioInfo)
def audio_info(body: RetainedAudioSource) -> RetainedAudioInfo:
    try:
        return retained_audio.info(body)
    except (reading_media.ReadingMediaError, audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as error:
        raise _error(error) from error


@router.post('/audio/video', response_model=VideoProject)
async def audio_video(body: RetainedAudioVideoRequest) -> VideoProject:
    try:
        return await video_projects.create_retained_audio_project(body)
    except (reading_media.ReadingMediaError, audiobooks.AudiobookError,
            voice_profiles.VoiceProfileError, video_projects.VideoProjectError) as error:
        raise _error(error) from error
