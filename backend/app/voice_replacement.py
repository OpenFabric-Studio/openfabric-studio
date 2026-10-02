"""Bounded uploaded-song replacement with independent original/result tracks."""
from __future__ import annotations

import asyncio
import logging
import math
import threading
import uuid
from collections.abc import Callable, Coroutine
from pathlib import Path

from fastapi import HTTPException, Request, UploadFile
from fastapi.routing import APIRoute
from starlette.responses import Response
from starlette.types import Message

from . import db, voice_build
from .client_contracts import ApplyStatusResponse, VoiceReplacementResponse
from .job_lifecycle import await_cleanup, communicate_process, spawn_process
from .track_view import track_response

logger = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_DURATION_SECONDS = 2 * 60 * 60
MAX_MULTIPART_OVERHEAD_BYTES = 64 * 1024
ALLOWED_AUDIO_EXTENSIONS = frozenset({'wav', 'mp3', 'flac', 'ogg', 'opus', 'm4a'})
_INPUT_OPTIONS = ['-protocol_whitelist', 'file,pipe', '-format_whitelist', 'wav,mp3,flac,ogg,mov']


class ReplacementRoute(APIRoute):
    """Cap only this multipart endpoint before each chunk reaches its parser."""
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()
        if self.path != '/api/voices/replace':
            return handler
        async def bounded(request: Request) -> Response:
            limit = MAX_UPLOAD_BYTES + MAX_MULTIPART_OVERHEAD_BYTES
            raw_length = request.headers.get('content-length')
            if raw_length is not None:
                try:
                    declared = int(raw_length)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail='invalid_request') from exc
                if declared < 0:
                    raise HTTPException(status_code=400, detail='invalid_request')
                if declared > limit:
                    raise HTTPException(status_code=413, detail='upload_too_large')
            received = 0
            async def receive() -> Message:
                nonlocal received
                message = await request.receive()
                if message.get('type') == 'http.request':
                    body = message.get('body', b'')
                    if not isinstance(body, bytes):
                        raise HTTPException(status_code=400, detail='invalid_request')
                    received += len(body)
                    if received > limit:
                        raise HTTPException(status_code=413, detail='upload_too_large')
                return message
            return await handler(Request(request.scope, receive=receive))
        return bounded


def _filename(raw: str | None) -> tuple[str, str]:
    filename = (raw or '').replace('\\', '/').rsplit('/', 1)[-1]
    filename = ''.join(character for character in filename if character.isprintable()).strip()
    extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if not filename or extension not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(status_code=400, detail='unsupported_audio_format')
    stem = filename.rsplit('.', 1)[0][:180].strip() or 'song'
    return f'{stem}.{extension}', extension


async def _copy_upload(upload: UploadFile, destination: Path) -> None:
    stop = threading.Event()
    def copy() -> None:
        total = 0
        with destination.open('xb') as handle:
            while not stop.is_set():
                chunk = upload.file.read(65536)
                if stop.is_set():
                    return
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail='upload_too_large')
                handle.write(chunk)
        if total == 0 and not stop.is_set():
            raise HTTPException(status_code=400, detail='invalid_audio')
    task = asyncio.create_task(asyncio.to_thread(copy))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.set()
        await await_cleanup(asyncio.gather(task, return_exceptions=True))
        raise


async def _duration(path: Path) -> float:
    proc = await spawn_process(voice_build._tool('ffprobe'), '-v', 'error', *_INPUT_OPTIONS, '-show_entries', 'format=duration',
                               '-of', 'default=noprint_wrappers=1:nokey=1', str(path),
                               stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await communicate_process(proc, 30)
    try:
        seconds = float(out.decode().strip())
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail='invalid_audio') from exc
    if proc.returncode != 0 or not math.isfinite(seconds) or seconds <= 0:
        raise HTTPException(status_code=400, detail='invalid_audio')
    if seconds > MAX_DURATION_SECONDS:
        raise HTTPException(status_code=400, detail='audio_too_long')
    return seconds


async def _decode(source: Path, destination: Path) -> float:
    await _duration(source)
    proc = await spawn_process(voice_build._tool('ffmpeg'), '-v', 'error', '-xerror', '-n', *_INPUT_OPTIONS, '-i', str(source),
                               '-map', '0:a:0', '-vn', '-sn', '-dn', '-t', str(MAX_DURATION_SECONDS + 1),
                               '-ar', '44100', '-ac', '2', '-c:a', 'pcm_s16le', '-f', 'wav', str(destination),
                               stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    await communicate_process(proc, 300)
    if proc.returncode != 0:
        raise HTTPException(status_code=400, detail='invalid_audio')
    return await _duration(destination)


async def _rollback(track_ids: list[int], files: list[Path]) -> None:
    try:
        if track_ids:
            await voice_build.cancel_apply(track_ids[-1])
    finally:
        for track_id in reversed(track_ids):
            try:
                db.delete_track(track_id)
            except Exception:
                logger.exception('Could not roll back replacement track %s', track_id)
        for path in files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.exception('Could not remove a replacement upload')


async def replace_voice(upload: UploadFile, voice_id: str) -> VoiceReplacementResponse:
    """Accept only after both independent rows and the owned apply task exist."""
    tracks: list[int] = []
    files: list[Path] = []
    accepted = False
    closed = False
    try:
        voice_build.require_ready_voice(voice_id)
        filename, extension = _filename(upload.filename)
        root = db.FILES_DIR.resolve()
        directory = root / 'upload'
        if not directory.resolve().is_relative_to(root):
            raise HTTPException(status_code=400, detail='invalid_storage')
        directory.mkdir(parents=True, exist_ok=True)
        identifier = uuid.uuid4().hex
        source = directory / f'{identifier}.source.{extension}'
        result = directory / f'{identifier}.voiced.wav'
        files.extend([source, result])
        await _copy_upload(upload, source)
        seconds = await _decode(source, result)
        await await_cleanup(upload.close())
        closed = True
        # The voice may have been removed or rebuilt during validation.
        voice_build.require_ready_voice(voice_id)
        title = filename.rsplit('.', 1)[0]
        source_id = db.insert_track(model='upload', title=title, lyrics='', seed=None, duration_ms=seconds * 1000,
            wall_ms=None, params={'source': 'voice_replacement_source', 'source_filename': filename}, audio_path=source, abc_path=None)
        tracks.append(source_id)
        track_id = db.insert_track(model='upload', title=title, lyrics='', seed=None, duration_ms=seconds * 1000,
            wall_ms=None, params={'source': 'voice_replacement', 'source_track_id': source_id, 'source_filename': filename,
                'voice_id': voice_id, 'audio_format': 'wav', 'task_type': 'voice_replacement'}, audio_path=result, abc_path=None)
        tracks.append(track_id)
        application = ApplyStatusResponse.model_validate(voice_build.start_apply(voice_id, track_id))
        source_row, result_row = db.get_track(source_id), db.get_track(track_id)
        if source_row is None or result_row is None:
            raise HTTPException(status_code=409, detail='track_missing')
        response = VoiceReplacementResponse(track=track_response(result_row), source_track=track_response(source_row), application=application)
        accepted = True
        return response
    except HTTPException:
        raise
    except TimeoutError as exc:
        raise HTTPException(status_code=400, detail='audio_validation_timeout') from exc
    except voice_build.VoiceBuildError as exc:
        logger.warning('Replacement audio engine unavailable: %s', exc.code)
        raise HTTPException(status_code=409, detail='audio_engine_missing') from exc
    except Exception as exc:
        logger.exception('Could not accept a voice replacement upload')
        raise HTTPException(status_code=500, detail='replacement_storage_failed') from exc
    finally:
        try:
            if not accepted:
                if tracks:
                    voice_build.request_apply_cancellation(tracks[-1])
                await await_cleanup(_rollback(tracks, files))
        finally:
            if not closed:
                await await_cleanup(upload.close())
