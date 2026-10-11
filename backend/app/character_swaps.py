"""Retained short video edits: one reviewed POST, durable GET-only recovery."""
from __future__ import annotations

import asyncio
import logging
import base64
import hashlib
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import UploadFile
from pydantic import Field, TypeAdapter, ValidationError

from .atomic_files import document_lock, write_object
from .character_swap_contracts import CharacterSwapJob, CharacterSwapReadiness, CharacterSwapRevisionRequest, CharacterSwapsResponse, SubmitCharacterSwapRequest
from .config import DATA_DIR
from .contracts import JsonObject
from . import openrouter_catalog as catalog, openrouter_requests as ledger, openrouter_settings as settings
from .openrouter_client import OpenRouterClient
from .openrouter_contracts import OpenRouterImageEditRequest, OpenRouterVideoEditRequest, OpenRouterQuoteRequest, OpenRouterReceipt
from .openrouter_errors import OpenRouterError
from .image_normalization import normalization_argv
from .job_lifecycle import await_cleanup, cancel_and_wait, kill_process_tree
from .resource_admission import admission_lock, require_setup_idle
from .video_contracts import VideoContract, VideoId
from .video_io import hash_file
from .video_media import VideoMediaError, probe_media, tool, validate_media
from .video_process import WorkerIdentity, read_owned_output, spawn_owned, terminate_verified
from .video_projects import VideoProjectError

logger = logging.getLogger(__name__)
_ID = re.compile(r'^[0-9a-f]{32}$')
_ASPECTS = ((16, 9), (4, 3), (3, 2), (1, 1), (2, 3), (3, 4), (9, 16), (21, 9))
_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_tasks: dict[str, asyncio.Task[None]] = {}
_preparations: dict[str, asyncio.Task[CharacterSwapJob]] = {}
_quotations: dict[str, asyncio.Task[CharacterSwapJob]] = {}
_stopping: set[str] = set()
_unresolved: set[str] = set()
_closing = False
_media_lock = asyncio.Lock()
POLL_INTERVAL = 3.0
MAX_VIDEO_BYTES = 100 * 1024 * 1024
MAX_IMAGE_BYTES = 15 * 1024 * 1024
_FILES = {'source.video', 'image.original', 'reference.png', 'selected.mp4', 'input.mp4', 'output.mp4', 'delivered.mp4', 'output.pending.mp4', 'job.json', 'worker.json', 'worker-receipt.json', 'normalized.png', 'normalized.normalization.json', 'source-frame.png', 'edited-frame.png', 'frame.pending.png', 'generated-frame.original', 'generated-frame.png', 'generated-frame.normalization.json'}


class CharacterSwapError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SwapInputs(VideoContract):
    name: str = Field(min_length=1, max_length=120)
    start_sec: float = Field(ge=0, le=21600)
    seconds: int = Field(ge=2, le=8, strict=True)
    seed: int = Field(ge=0, le=2147483647, strict=True)


class SceneSwapInputs(SwapInputs):
    scene_id: VideoId
    cut_id: VideoId


class StoredSwap(VideoContract):
    job: CharacterSwapJob
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    image_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    selected_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    input_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    pending_sha256: str = Field(default='', pattern=r'^(?:[0-9a-f]{64})?$')
    source_frame_sha256: str = Field(default='', pattern=r'^(?:[0-9a-f]{64})?$')
    edited_frame_sha256: str = Field(default='', pattern=r'^(?:[0-9a-f]{64})?$')
    raw_frame_sha256: str = Field(default='', pattern=r'^(?:[0-9a-f]{64})?$')
    pending_frame_sha256: str = Field(default='', pattern=r'^(?:[0-9a-f]{64})?$')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def root() -> Path:
    path = DATA_DIR / 'videos' / 'character-swaps'
    if any(item.is_symlink() for item in (DATA_DIR, DATA_DIR / 'videos', path)):
        raise CharacterSwapError('storage_unavailable')
    return path


def directory(identifier: str) -> Path:
    if _ID.fullmatch(identifier) is None:
        raise CharacterSwapError('not_found')
    path = root() / identifier
    if path.is_symlink() or not path.resolve().is_relative_to(root().resolve()):
        raise CharacterSwapError('storage_unavailable')
    return path


def artifact(identifier: str, name: str) -> Path:
    if name not in _FILES:
        raise CharacterSwapError('not_found')
    path = directory(identifier) / name
    if path.is_symlink() or not path.resolve().is_relative_to(directory(identifier).resolve()):
        raise CharacterSwapError('storage_unavailable')
    return path


def _write(path: Path, model: VideoContract | WorkerIdentity) -> None:
    write_object(path, _JSON.validate_json(model.model_dump_json()))


def save(document: StoredSwap) -> None:
    checked = StoredSwap.model_validate(document.model_dump())
    _write(artifact(checked.job.id, 'job.json'), checked)


def load(identifier: str) -> StoredSwap:
    path = artifact(identifier, 'job.json')
    try:
        if path.stat().st_size > 65536:
            raise ValueError('metadata_size')
        document = StoredSwap.model_validate_json(path.read_bytes())
        if document.job.id != identifier:
            raise ValueError('metadata_id')
        if document.job.quote_expires_at and datetime.fromisoformat(document.job.quote_expires_at).tzinfo is None:
            raise ValueError('metadata_time')
        return document
    except FileNotFoundError as error:
        raise CharacterSwapError('not_found') from error
    except (OSError, ValueError) as error:
        raise CharacterSwapError('storage_unavailable') from error


def get(identifier: str) -> CharacterSwapJob:
    with document_lock(artifact(identifier, 'job.json')):
        document = load(identifier)
        job = document.job
        job.resume_available = job.provider == 'openrouter' and job.status in {'stopped', 'failed'} and job.error_code not in {'provider_video_failed', 'provider_video_cancelled', 'provider_video_expired', 'request_mismatch'} and bool(
            document.pending_sha256 or document.pending_frame_sha256 or document.raw_frame_sha256
            if job.stage == 'frame' else document.pending_sha256 or job.remote_id or job.video_receipt_id)
        return job


def list_jobs() -> CharacterSwapsResponse:
    path = root()
    jobs: list[CharacterSwapJob] = []
    if path.exists():
        for child in path.iterdir():
            if _ID.fullmatch(child.name) is None:
                continue
            # Unreadable evidence remains on disk; do not hide corruption as an empty list.
            jobs.append(get(child.name))
    return CharacterSwapsResponse(jobs=sorted(jobs, key=lambda job: (job.created_at, job.id), reverse=True)[:1000])


def readiness() -> CharacterSwapReadiness:
    try:
        tool('ffmpeg'); tool('ffprobe')
        available = True
    except VideoMediaError:
        available = False
    status = settings.status()
    return CharacterSwapReadiness(configured=status.enabled and status.credential_configured,
        enabled=status.enabled, credential_configured=status.credential_configured, ffmpeg_ready=available)


async def _command(path: Path, argv: list[str]) -> None:
    ownership = path / 'worker.json'
    receipt = path / 'worker-receipt.json'
    proc = await spawn_owned(argv, receipt_path=receipt, stdout=asyncio.subprocess.PIPE,
                             on_identity=lambda identity: _write(ownership, WorkerIdentity(
                                 pid=identity.pid, token=identity.token, receipt=receipt.name)))
    try:
        await read_owned_output(proc, max_bytes=16384, timeout=180)
        if proc.returncode != 0:
            raise CharacterSwapError('invalid_media')
    finally:
        await await_cleanup(kill_process_tree(proc))
        ownership.unlink(missing_ok=True)
        receipt.unlink(missing_ok=True)


async def _upload(upload: UploadFile, path: Path, limit: int) -> None:
    total = 0
    with path.open('xb') as stream:
        while chunk := await upload.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                raise CharacterSwapError('upload_too_large')
            stream.write(chunk)
    if total == 0:
        raise CharacterSwapError('invalid_media')


def _name(upload: UploadFile, extensions: set[str]) -> str:
    name = (upload.filename or '').replace('\\', '/').rsplit('/', 1)[-1]
    if not 1 <= len(name) <= 255 or Path(name).suffix.lower() not in extensions:
        raise CharacterSwapError('unsupported_format')
    return name


async def create_from_scene(scene_id: str, cut_id: str, image: UploadFile, *, name: str,
                            start_sec: float, seconds: int, seed: int = 42) -> CharacterSwapJob:
    from .video_scenes import resolve_cut
    video: UploadFile | None = None
    try:
        inputs = SceneSwapInputs(scene_id=scene_id, cut_id=cut_id, name=name.strip(),
                                 start_sec=start_sec, seconds=seconds, seed=seed)
        path, cut = await resolve_cut(inputs.scene_id, inputs.cut_id)
        if inputs.start_sec + inputs.seconds > cut.seconds + 1 / 24:
            raise CharacterSwapError('invalid_interval')
        if path.stat().st_size > MAX_VIDEO_BYTES:
            raise CharacterSwapError('upload_too_large')
        video = UploadFile(path.open('rb'), filename='scene.mp4')
        return await create(video, image, name=inputs.name, start_sec=inputs.start_sec,
                            seconds=inputs.seconds, seed=inputs.seed, expected_source_sha256=cut.output_sha256)
    except ValidationError as error:
        raise CharacterSwapError('bad_settings') from error
    finally:
        if video is None:
            await await_cleanup(image.close())
        else:
            await await_cleanup(asyncio.gather(video.close(), image.close()))


async def create(video: UploadFile, image: UploadFile, *, name: str, start_sec: float, seconds: int, seed: int = 42,
                 expected_source_sha256: str | None = None) -> CharacterSwapJob:
    task: asyncio.Task[CharacterSwapJob] | None = None
    try:
        settings = SwapInputs(name=name.strip(), start_sec=start_sec, seconds=seconds, seed=seed)
        source_name = _name(video, {'.mp4', '.mov', '.webm', '.avi', '.mkv'})
        image_name = _name(image, {'.png', '.jpg', '.jpeg', '.webp'})
        async with admission_lock:
            require_setup_idle()
            if _closing or _preparations or _unresolved:
                raise CharacterSwapError('busy')
            identifier = uuid4().hex
            path = directory(identifier)
            path.parent.mkdir(parents=True, exist_ok=True)
            # A hidden staging folder never appears as a completed preparation.
            staging = path.with_name('.' + identifier + '.preparing')
            staging.mkdir()
            write_object(staging / 'preparation.json', {'id': identifier, 'kind': 'character_swap_preparation'})
            task = asyncio.create_task(_prepare(identifier, staging, video, image, source_name, image_name, settings,
                                                expected_source_sha256))
            _preparations[identifier] = task
            task.add_done_callback(lambda finished: _preparations.pop(identifier, None))
        return await task
    except ValidationError as error:
        raise CharacterSwapError('bad_settings') from error
    finally:
        if task is not None and not task.done():
            task.cancel()
            await await_cleanup(asyncio.gather(task, return_exceptions=True))
        await await_cleanup(asyncio.gather(video.close(), image.close()))


async def _prepare(identifier: str, staging: Path, video: UploadFile, image: UploadFile,
                   source_name: str, image_name: str, settings: SwapInputs,
                   expected_source_sha256: str | None = None) -> CharacterSwapJob:
    published = False
    try:
        async with _media_lock:
            await _upload(video, staging / 'source.video', MAX_VIDEO_BYTES)
            if expected_source_sha256 is not None and await hash_file(staging / 'source.video') != expected_source_sha256:
                raise CharacterSwapError('source_changed')
            await _upload(image, staging / 'image.original', MAX_IMAGE_BYTES)
            format_name: Literal['mov', 'matroska', 'avi'] = 'matroska' if source_name.lower().endswith(('.webm', '.mkv')) else 'avi' if source_name.lower().endswith('.avi') else 'mov'
            info = await probe_media(staging / 'source.video', input_format=format_name)
            display_width, display_height = info.display_size
            if (not 0 < info.video_duration <= 21600 or not 1 <= info.fps <= 120
                    or not 2 <= min(info.width, info.height) or max(info.width, info.height) > 4096
                    or max(display_width, display_height) > 4096
                    or max(display_width, display_height) / min(display_width, display_height) > 3
                    or settings.start_sec + settings.seconds > info.video_duration + 1 / 24):
                raise CharacterSwapError('invalid_interval')
            ffmpeg = tool('ffmpeg')
            width, height = _prepared_size(display_width, display_height)
            picture_width = max(2, min(width, display_width * height // display_height) // 2 * 2)
            picture_height = max(2, min(height, display_height * width // display_width) // 2 * 2)
            scaling = f'scale={picture_width}:{picture_height},setsar=1,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=24'
            await _command(staging, [ffmpeg, '-v', 'error', '-xerror', '-f', format_name, '-protocol_whitelist', 'file,pipe',
                '-ss', str(settings.start_sec), '-i', str(staging / 'source.video'), '-t', str(settings.seconds),
                '-map', '0:v:0', '-map', '0:a:0?', '-vf', scaling, '-frames:v', str(settings.seconds * 24),
                '-map_metadata', '-1', '-map_chapters', '-1',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', str(staging / 'selected.mp4')])
            selected = await probe_media(staging / 'selected.mp4')
            await validate_media(staging / 'selected.mp4', settings.seconds, (selected.width, selected.height), require_audio=info.audio_duration > 0)
            await _command(staging, [ffmpeg, '-v', 'error', '-xerror', '-i', str(staging / 'selected.mp4'),
                '-map', '0:v:0', '-an', '-c:v', 'copy', '-movflags', '+faststart', str(staging / 'input.mp4')])
            if 4 * (((staging / 'input.mp4').stat().st_size + 2) // 3) + len('data:video/mp4;base64,') > 5000000:
                raise CharacterSwapError('upload_too_large')
            await _command(staging, normalization_argv(staging / 'image.original', staging / 'normalized.png'))
            await _command(staging, [ffmpeg, '-v', 'error', '-i', str(staging / 'normalized.png'), '-frames:v', '1',
                '-vf', "scale=w='min(1024,iw)':h='min(1024,ih)':force_original_aspect_ratio=decrease,format=rgb24", str(staging / 'reference.png')])
            reference = await probe_media(staging / 'reference.png')
            if min(reference.width, reference.height) < 14 or max(reference.width, reference.height) / min(reference.width, reference.height) > 16:
                raise CharacterSwapError('invalid_media')
            await _command(staging, [ffmpeg, '-v', 'error', '-i', str(staging / 'input.mp4'), '-frames:v', '1', str(staging / 'source-frame.png')])
            _aspect(selected.width, selected.height)
            base = f'/api/videos/character-swaps/{identifier}'
            timestamp = now()
            job = CharacterSwapJob(id=identifier, name=settings.name, created_at=timestamp, updated_at=timestamp,
                start_sec=settings.start_sec, seconds=settings.seconds, source_name=source_name, image_name=image_name,
                source_duration_sec=info.video_duration, width=selected.width, height=selected.height,
                frames=settings.seconds * 24, has_audio=selected.audio_duration > 0,
                seed=settings.seed, source_frame_url=base + '/frame-original',
                source_url=base + '/source', clip_url=base + '/clip', image_url=base + '/image')
            document = StoredSwap(job=job, source_sha256=await hash_file(staging / 'source.video'),
                image_sha256=await hash_file(staging / 'reference.png'), selected_sha256=await hash_file(staging / 'selected.mp4'),
                input_sha256=await hash_file(staging / 'input.mp4'), source_frame_sha256=await hash_file(staging / 'source-frame.png'))
            _write(staging / 'job.json', document)
            staging.rename(directory(identifier))
            published = True
            return job
    finally:
        if not published:
            # Failed process drains retain ownership and files for recovery.
            if (staging / 'worker.json').exists():
                _unresolved.add(identifier)
            else:
                shutil.rmtree(staging)


def _change(identifier: str, *, status: Literal['prepared', 'submitting', 'submission_unknown', 'queued', 'running', 'ready', 'failed', 'stopped'],
            phase: str, error_code: str = '', remote_id: str | None = None, revise: bool = False) -> None:
    with document_lock(artifact(identifier, 'job.json')):
        document = load(identifier)
        document.job.status = status
        document.job.phase = phase
        document.job.error_code = error_code
        document.job.updated_at = now()
        if revise:
            document.job.revision += 1
        if remote_id is not None:
            if document.job.remote_id not in (None, remote_id):
                raise CharacterSwapError('request_mismatch')
            document.job.remote_id = remote_id
        save(document)


def _start(identifier: str, *, submit_request: bool) -> None:
    task = asyncio.create_task(_run(identifier, submit_request=submit_request))
    _tasks[identifier] = task
    task.add_done_callback(lambda finished: _tasks.pop(identifier, None))


def _require_provider() -> None:
    status = settings.status()
    if not status.enabled:
        raise CharacterSwapError('provider_disabled')
    if not status.credential_configured:
        raise CharacterSwapError('provider_key_missing')


def _prepared_size(width: int, height: int) -> tuple[int, int]:
    left, right = min(_ASPECTS, key=lambda pair: abs(width / height - pair[0] / pair[1]) / (pair[0] / pair[1]))
    # Fit ordinary movie crops with at most 3% padding; keep provider checks strict.
    if abs(width / height - left / right) / (left / right) > .03:
        raise CharacterSwapError('edit_parameters_unsupported')
    return (480, round(480 * right / left / 2) * 2) if left < right else (round(480 * left / right / 2) * 2, 480)


def _aspect(width: int, height: int) -> str:
    for left, right in _ASPECTS:
        if abs(width / height - left / right) < .002:
            return f'{left}:{right}'
    raise CharacterSwapError('edit_parameters_unsupported')


def _uri(identifier: str, name: str, media_type: str) -> str:
    path = artifact(identifier, name)
    if 4 * ((path.stat().st_size + 2) // 3) + len(f'data:{media_type};base64,') > 5000000:
        raise CharacterSwapError('upload_too_large')
    return f'data:{media_type};base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


def _request(document: StoredSwap) -> OpenRouterImageEditRequest | OpenRouterVideoEditRequest:
    job = document.job
    if job.provider != 'openrouter':
        raise CharacterSwapError('legacy_provider_readonly')
    if job.stage == 'frame':
        return OpenRouterImageEditRequest(prompt='Edit image 1: replace its sole person with the full character from image 2. Preserve the original pose, framing, lighting, and background.',
            source_frame=_uri(job.id, 'source-frame.png', 'image/png'), reference_image=_uri(job.id, 'reference.png', 'image/png'),
            aspect_ratio=_aspect(job.width, job.height), seed=job.seed)
    if not document.edited_frame_sha256:
        raise CharacterSwapError('frame_review_required')
    return _video_request(document, 'edited-frame.png')


def _video_request(document: StoredSwap, frame_name: str) -> OpenRouterVideoEditRequest:
    job = document.job
    return OpenRouterVideoEditRequest(prompt='Replace the sole person throughout the video with the character shown in the edited first frame. Preserve the original movement, camera, lighting, and background.',
        reference_video=_uri(job.id, 'input.mp4', 'video/mp4'), reference_image=_uri(job.id, frame_name, 'image/png'),
        duration=job.seconds, width=job.width, height=job.height, seed=job.seed)


def _parameters(body: OpenRouterImageEditRequest | OpenRouterVideoEditRequest) -> OpenRouterQuoteRequest:
    return catalog.image_parameters(body) if isinstance(body, OpenRouterImageEditRequest) else catalog.video_edit_parameters(body)


def _receipt(document: StoredSwap) -> OpenRouterReceipt:
    job = document.job
    identifier = job.frame_receipt_id if job.stage == 'frame' else job.video_receipt_id
    if identifier is None:
        raise CharacterSwapError('submission_unknown')
    record = ledger.get(identifier)
    if (record.owner_id != f'character-{job.stage}-{job.id}' or record.model_id != _request(document).model
            or record.kind != ('image' if job.stage == 'frame' else 'video')
            or record.request_fingerprint != catalog.digest(_parameters(_request(document)))
            or job.remote_id is not None and job.stage == 'video' and record.remote_id != job.remote_id):
        raise CharacterSwapError('request_mismatch')
    return record


async def quote(identifier: str, body: CharacterSwapRevisionRequest) -> CharacterSwapJob:
    async with admission_lock:
        require_setup_idle()
        if _closing or _tasks or _quotations or _unresolved or identifier in _stopping:
            raise CharacterSwapError('busy')
        _require_provider()
        job = get(identifier)
        if job.revision != body.revision:
            raise CharacterSwapError('revision_conflict')
        if job.status != 'prepared':
            raise CharacterSwapError('already_submitted')
        if job.provider != 'openrouter':
            raise CharacterSwapError('legacy_provider_readonly')
        task = asyncio.create_task(_quote(identifier, body.revision))
        _quotations[identifier] = task
        task.add_done_callback(lambda finished: _quotations.pop(identifier, None))
    return await task


async def _quote(identifier: str, revision: int) -> CharacterSwapJob:
    try:
        document = load(identifier)
        await _check_inputs(document)
        request = _request(document)
        await OpenRouterClient().refresh_catalog()
        if document.job.stage == 'frame':
            catalog.estimate_video_edit(_video_request(document, 'source-frame.png'))
        result = catalog.quote(_parameters(request))
        with document_lock(artifact(identifier, 'job.json')):
            document = load(identifier)
            if document.job.revision != revision or document.job.status != 'prepared':
                raise CharacterSwapError('revision_conflict')
            document.job.quote = result
            document.job.revision += 1
            document.job.updated_at = now()
            save(document)
            return document.job
    except OpenRouterError as error:
        raise CharacterSwapError(error.code) from error


async def submit(identifier: str, body: SubmitCharacterSwapRequest) -> CharacterSwapJob:
    try:
        async with admission_lock:
            require_setup_idle()
            if _closing or _tasks or _quotations or _unresolved or identifier in _stopping:
                raise CharacterSwapError('busy')
            if not body.transfers_confirmed or not body.consent_confirmed:
                raise CharacterSwapError('confirmation_required')
            _require_provider()
            document = load(identifier)
            await _check_inputs(document)
            with document_lock(artifact(identifier, 'job.json')):
                document = load(identifier)
                job = document.job
                if job.revision != body.revision:
                    raise CharacterSwapError('revision_conflict')
                if job.status != 'prepared':
                    raise CharacterSwapError('already_submitted')
                request = _request(document)
                if job.quote is None or job.quote.id != body.quote_id:
                    raise CharacterSwapError('quote_mismatch')
                if job.stage == 'frame':
                    catalog.estimate_video_edit(_video_request(document, 'source-frame.png'))
                checked = catalog.validate_quote(body.quote_id, _parameters(request))
                if checked != job.quote:
                    raise CharacterSwapError('quote_mismatch')
                receipt = ledger.prepare(f'character-{job.stage}-{job.id}', checked)
                if job.stage == 'frame': job.frame_receipt_id = receipt.id
                else: job.video_receipt_id = receipt.id
                job.status = 'submitting'; job.phase = 'checking_inputs'
                job.revision += 1; job.updated_at = now()
                save(document)
                _start(identifier, submit_request=True)
                return get(identifier)
    except OpenRouterError as error:
        raise CharacterSwapError(error.code) from error


async def _check_inputs(document: StoredSwap) -> None:
    pairs = [('source.video', document.source_sha256), ('reference.png', document.image_sha256),
                           ('selected.mp4', document.selected_sha256), ('input.mp4', document.input_sha256)]
    if document.job.provider == 'openrouter':
        pairs.append(('source-frame.png', document.source_frame_sha256))
        if document.job.stage == 'video': pairs.append(('edited-frame.png', document.edited_frame_sha256))
    try:
        for name, expected in pairs:
            if await hash_file(artifact(document.job.id, name)) != expected:
                raise CharacterSwapError('source_changed')
    except (OSError, VideoProjectError) as error:
        raise CharacterSwapError('source_changed') from error


async def _publish(identifier: str, client: OpenRouterClient | None, remote_id: str) -> None:
    document = load(identifier)
    job = document.job
    pending = artifact(identifier, 'output.pending.mp4')
    output = artifact(identifier, 'output.mp4')
    if document.pending_sha256:
        retained = output if output.exists() else pending
        if retained.exists() and await hash_file(retained) == document.pending_sha256:
            await validate_media(retained, job.seconds, (job.width, job.height), require_audio=job.has_audio)
            if retained != output:
                retained.replace(output)
            with document_lock(artifact(identifier, 'job.json')):
                document = load(identifier)
                document.job.output_sha256 = document.pending_sha256
                document.job.output_url = f'/api/videos/character-swaps/{identifier}/output'
                document.pending_sha256 = ''
                document.job.status = 'ready'
                document.job.phase = 'ready'
                document.job.error_code = ''
                document.job.updated_at = now()
                save(document)
            return
        raise CharacterSwapError('output_mismatch')
    if client is None:
        raise CharacterSwapError('output_mismatch')
    delivered = artifact(identifier, 'delivered.mp4')
    delivered.unlink(missing_ok=True)
    _change(identifier, status='running', phase='downloading')
    await client.download_video(remote_id, delivered)
    async with _media_lock:
        info = await probe_media(delivered, input_format='mov')
        if (not 2 <= min(info.width, info.height) or max(info.width, info.height) > 4096
                or not 5 <= info.fps <= 120 or abs(info.video_duration - job.seconds) > max(.15, 2 / info.fps)
                or abs(info.width / info.height / (job.width / job.height) - 1) > .05):
            raise CharacterSwapError('output_mismatch')
        _change(identifier, status='running', phase='checking_output')
        await _command(directory(identifier), [tool('ffmpeg'), '-v', 'error', '-xerror', '-f', 'mov', '-protocol_whitelist', 'file,pipe',
            '-i', str(delivered), '-map', '0:v:0', '-f', 'null', '-'])
        pending.unlink(missing_ok=True)
        await _command(directory(identifier), [tool('ffmpeg'), '-v', 'error', '-xerror', '-f', 'mov', '-protocol_whitelist', 'file,pipe',
            '-i', str(delivered), '-i', str(artifact(identifier, 'selected.mp4')), '-map', '0:v:0', '-map', '1:a:0?',
            '-t', str(job.seconds), '-vf', f'scale={job.width}:{job.height}:force_original_aspect_ratio=decrease,pad={job.width}:{job.height}:(ow-iw)/2:(oh-ih)/2,fps=24,setsar=1',
            '-map_metadata', '-1', '-map_chapters', '-1',
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'copy', '-movflags', '+faststart', str(pending)])
        await validate_media(pending, job.seconds, (job.width, job.height), require_audio=job.has_audio)
        digest = await hash_file(pending)
        with document_lock(artifact(identifier, 'job.json')):
            document = load(identifier)
            document.pending_sha256 = digest
            save(document)
        # Recovery adopts a validated pending output even if publication was interrupted.
        await _publish(identifier, client, remote_id)


def _retain_frame(identifier: str, data: bytes) -> None:
    digest = hashlib.sha256(data).hexdigest()
    with document_lock(artifact(identifier, 'job.json')):
        document = load(identifier)
        document.raw_frame_sha256 = digest
        save(document)
    path = artifact(identifier, 'generated-frame.original')
    temporary = path.with_name('.generated-frame.tmp')
    if temporary.is_symlink():
        raise CharacterSwapError('storage_unavailable')
    try:
        with temporary.open('xb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


async def _publish_frame(identifier: str) -> None:
    document = load(identifier)
    job = document.job
    edited = artifact(identifier, 'edited-frame.png')
    pending = artifact(identifier, 'frame.pending.png')
    if not document.pending_frame_sha256:
        raw = artifact(identifier, 'generated-frame.original')
        if not document.raw_frame_sha256 or not raw.exists() or await hash_file(raw) != document.raw_frame_sha256:
            raise CharacterSwapError('frame_output_missing')
        async with _media_lock:
            normalized = artifact(identifier, 'generated-frame.png')
            await _command(directory(identifier), normalization_argv(raw, normalized))
            info = await probe_media(normalized)
            if abs(info.width / info.height / (job.width / job.height) - 1) > .05:
                raise CharacterSwapError('output_mismatch')
            pending.unlink(missing_ok=True)
            await _command(directory(identifier), [tool('ffmpeg'), '-v', 'error', '-i', str(normalized), '-frames:v', '1',
                '-vf', f'scale={job.width}:{job.height},format=rgb24', str(pending)])
            _uri(identifier, 'frame.pending.png', 'image/png')
            digest = await hash_file(pending)
            with document_lock(artifact(identifier, 'job.json')):
                document = load(identifier)
                document.pending_frame_sha256 = digest
                save(document)
    retained = edited if edited.exists() and await hash_file(edited) == document.pending_frame_sha256 else pending
    if not retained.exists() or await hash_file(retained) != document.pending_frame_sha256:
        raise CharacterSwapError('output_mismatch')
    if retained != edited: retained.replace(edited)
    with document_lock(artifact(identifier, 'job.json')):
        document = load(identifier)
        document.edited_frame_sha256 = document.pending_frame_sha256
        document.pending_frame_sha256 = ''
        document.job.edited_frame_url = f'/api/videos/character-swaps/{identifier}/frame-edited'
        document.job.stage = 'video'; document.job.model_id = 'runway/aleph-2'
        document.job.status = 'prepared'; document.job.phase = 'review_frame'
        document.job.quote = None; document.job.error_code = ''
        document.job.revision += 1; document.job.updated_at = now()
        save(document)


async def _run(identifier: str, *, submit_request: bool) -> None:
    stage = load(identifier).job.stage
    try:
        document = load(identifier)
        await _check_inputs(document)
        record = _receipt(document)
        if record.state == 'failed':
            raise CharacterSwapError(record.error_code or 'provider_request_failed')
        if stage == 'frame' and (document.raw_frame_sha256 or document.pending_frame_sha256):
            await _publish_frame(identifier)
            return
        if document.pending_sha256:
            await _publish(identifier, None, document.job.remote_id or '')
            return
        client = OpenRouterClient()
        if submit_request:
            request = _request(document)
            _change(identifier, status='submitting', phase='submitting')
            if isinstance(request, OpenRouterImageEditRequest):
                result = await client.submit_image_edit(record.id, request, record.quote_id)
                _retain_frame(identifier, result.data)
                _change(identifier, status='running', phase='checking_frame')
                await _publish_frame(identifier)
                return
            remote = await client.submit_video_edit(record.id, request, record.quote_id)
            _change(identifier, status='queued', phase='queued', remote_id=remote.id)
            record = ledger.get(record.id)
        remote_id = record.remote_id
        if stage == 'frame':
            raise CharacterSwapError('frame_output_missing' if record.state == 'completed' else 'submission_unknown')
        if remote_id is None:
            raise CharacterSwapError('submission_unknown')
        _change(identifier, status='queued', phase='queued', remote_id=remote_id)
        async with asyncio.timeout(3600):
            while True:
                remote = await client.poll_video(remote_id)
                if remote.status == 'completed':
                    ledger.update(record.id, state='completed', actual_cost_usd=remote.actual_cost_usd)
                    await _publish(identifier, client, remote_id)
                    return
                if remote.status in {'failed', 'cancelled', 'expired'}:
                    ledger.update(record.id, state='failed', error_code='provider_video_'+remote.status)
                    raise CharacterSwapError('provider_video_'+remote.status)
                _change(identifier, status='queued' if remote.status in {'pending', 'queued'} else 'running', phase='generating')
                await asyncio.sleep(POLL_INTERVAL)
    except asyncio.CancelledError:
        job = get(identifier)
        if job.stage == stage and job.status != 'ready':
            unknown = job.status == 'submitting' and job.phase == 'submitting' and (stage == 'frame' or job.remote_id is None)
            _change(identifier, status='submission_unknown' if unknown else 'stopped', phase='stopped', error_code='submission_unknown' if unknown else '')
        raise
    except (OpenRouterError, CharacterSwapError, VideoMediaError, VideoProjectError) as error:
        _change(identifier, status='submission_unknown' if error.code == 'submission_unknown' else 'failed', phase='failed', error_code=error.code)
    except Exception:
        logger.exception('Character swap failed for %s', identifier)
        job = get(identifier)
        unknown = job.status == 'submitting' and job.phase == 'submitting' and (stage == 'frame' or job.remote_id is None)
        _change(identifier, status='submission_unknown' if unknown else 'failed', phase='failed', error_code='submission_unknown' if unknown else 'processing_failed')
    finally:
        document = load(identifier)
        if artifact(identifier, 'worker.json').exists():
            _unresolved.add(identifier)
        else:
            if not document.pending_sha256: artifact(identifier, 'output.pending.mp4').unlink(missing_ok=True)
            if not document.pending_frame_sha256: artifact(identifier, 'frame.pending.png').unlink(missing_ok=True)


async def stop(identifier: str, body: CharacterSwapRevisionRequest) -> CharacterSwapJob:
    with document_lock(artifact(identifier, 'job.json')):
        job = get(identifier)
        if job.revision != body.revision:
            raise CharacterSwapError('revision_conflict')
        if job.provider != 'openrouter':
            raise CharacterSwapError('legacy_provider_readonly')
        _stopping.add(identifier)
    try:
        task = _tasks.get(identifier)
        await cancel_and_wait(task)
        await _recover_worker(directory(identifier))
        _unresolved.discard(identifier)
        job = get(identifier)
        if job.status not in {'prepared', 'ready', 'submission_unknown', 'stopped'}:
            _change(identifier, status='stopped', phase='stopped', revise=True)
        return get(identifier)
    finally:
        _stopping.discard(identifier)


async def resume(identifier: str, body: CharacterSwapRevisionRequest) -> CharacterSwapJob:
    async with admission_lock:
        require_setup_idle()
        if _closing or _tasks or _quotations or identifier in _stopping:
            raise CharacterSwapError('busy')
        await _recover_worker(directory(identifier))
        _unresolved.discard(identifier)
        with document_lock(artifact(identifier, 'job.json')):
            job = get(identifier)
            if job.revision != body.revision:
                raise CharacterSwapError('revision_conflict')
            if not job.resume_available:
                raise CharacterSwapError('resume_unavailable')
            _receipt(load(identifier))
            _change(identifier, status='queued', phase='queued', revise=True)
            _start(identifier, submit_request=False)
            return get(identifier)


async def _recover_worker(path: Path) -> None:
    ownership = path / 'worker.json'
    if ownership.exists():
        if ownership.is_symlink() or ownership.stat().st_size > 4096:
            raise CharacterSwapError('worker_unresolved')
        identity = WorkerIdentity.model_validate_json(ownership.read_bytes())
        receipt = path / 'worker-receipt.json'
        if identity.receipt != receipt.name or receipt.is_symlink() or (receipt.exists() and receipt.stat().st_size > 65536):
            raise CharacterSwapError('worker_unresolved')
        local_identity = WorkerIdentity(pid=identity.pid, token=identity.token, receipt=str(receipt))
        if not await terminate_verified(local_identity):
            raise CharacterSwapError('worker_unresolved')
        ownership.unlink()


async def recover() -> None:
    global _closing
    _closing = False
    if not _tasks and not _preparations and not _quotations:
        _unresolved.clear()
    try:
        path = root()
    except CharacterSwapError:
        logger.error('Character swap storage unavailable; retained without recovery')
        return
    if not path.exists():
        return
    for child in path.iterdir():
        if re.fullmatch(r'\.[0-9a-f]{32}\.preparing', child.name):
            if child.is_symlink():
                logger.error('Escaped character swap preparation retained')
                continue
            try:
                marker = child / 'preparation.json'
                if marker.is_symlink() or marker.stat().st_size > 1024:
                    raise CharacterSwapError('storage_unavailable')
                from .atomic_files import read_object
                if read_object(marker) != {'id': child.name[1:33], 'kind': 'character_swap_preparation'}:
                    raise CharacterSwapError('storage_unavailable')
                await _recover_worker(child)
                shutil.rmtree(child)
            except Exception:
                if (child / 'worker.json').exists():
                    _unresolved.add(child.name[1:33])
                logger.exception('Unresolved character swap preparation retained')
    for child in path.iterdir():
        if _ID.fullmatch(child.name) is None or child.name in _tasks:
            continue
        try:
            await _recover_worker(directory(child.name))
            _unresolved.discard(child.name)
            document = load(child.name)
            job = document.job
            if job.provider == 'openrouter' and (document.pending_sha256 or document.pending_frame_sha256
                    or job.stage == 'frame' and document.raw_frame_sha256 or job.status in {'submitting', 'queued', 'running'}
                    or job.status == 'submission_unknown' and job.stage == 'video' and job.video_receipt_id is not None
                    and _receipt(document).remote_id is not None):
                _start(job.id, submit_request=False)
        except Exception:
            if (child / 'worker.json').exists():
                _unresolved.add(child.name)
                try:
                    _change(child.name, status='failed', phase='failed', error_code='worker_unresolved')
                except CharacterSwapError:
                    pass
            logger.exception('Unresolved character swap job retained: %s', child.name)


def work_busy() -> bool:
    return bool(_tasks or _preparations or _quotations or _stopping or _unresolved)


async def shutdown() -> None:
    global _closing
    _closing = True
    jobs = tuple(_tasks.values())
    preparations = (*_preparations.values(), *_quotations.values())
    for task in (*jobs, *preparations):
        if not task.done() and not task.cancelling():
            task.cancel()
    await await_cleanup(asyncio.gather(*jobs, *preparations, return_exceptions=True))
    for identifier in tuple(_unresolved):
        staging = root() / ('.' + identifier + '.preparing')
        path = staging if staging.is_dir() else directory(identifier)
        try:
            await await_cleanup(_recover_worker(path))
            _unresolved.discard(identifier)
        except Exception:
            logger.exception('Character swap worker could not be drained: %s', identifier)
    if _unresolved:
        raise CharacterSwapError('worker_unresolved')
