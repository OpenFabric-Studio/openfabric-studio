"""Owned local movie sources, bounded seek previews and durable MP4 scenes."""
from __future__ import annotations

import asyncio
import logging
import math
import re
import shutil
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import UploadFile
from pydantic import Field, TypeAdapter, ValidationError

from .atomic_files import document_lock, write_object
from .config import DATA_DIR
from .contracts import JsonObject
from .job_lifecycle import await_cleanup, cancel_and_wait, kill_process_tree
from .resource_admission import admission_lock, require_setup_idle
from .video_contracts import VideoContract
from .video_io import hash_file
from .video_media import VideoMediaError, probe_media, tool, validate_media
from .video_process import WorkerIdentity, WorkerOutputError, read_owned_output, spawn_owned, terminate_verified
from .video_projects import VideoProjectError
from .video_scene_contracts import VideoSceneCut, VideoSceneCutRequest, VideoSceneCutsResponse, VideoScenePreview, VideoScenePreviewRequest, VideoSceneReadiness, VideoSceneSource, VideoSceneSourcesResponse

logger = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 2*1024**3
MAX_NATIVE_BYTES = 8*1024**3
MAX_CUT_BYTES = 2*1024**3
MAX_PREVIEW_BYTES = 64*1024**2
_ID = re.compile(r'^[0-9a-f]{32}$')
_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_imports: dict[str, asyncio.Task[VideoSceneSource]] = {}
_previews: dict[str, asyncio.Task[VideoScenePreview]] = {}
_cuts: dict[str, asyncio.Task[None]] = {}
_unresolved: set[Path] = set()
_closing = False
_media_lock = asyncio.Lock()


class VideoSceneError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SourceSignature(VideoContract):
    device: int = Field(ge=0)
    inode: int = Field(ge=0)
    bytes: int = Field(gt=0, le=8589934592)
    mtime_ns: int = Field(ge=0)
    ctime_ns: int = Field(ge=0)


class StoredSource(VideoContract):
    source: VideoSceneSource
    path: str = Field(min_length=1, max_length=4096, repr=False)
    input_format: Literal['avi', 'mov', 'matroska']
    signature: SourceSignature


class StoredPreview(VideoContract):
    preview: VideoScenePreview
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    created_at: str


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def root() -> Path:
    path = DATA_DIR/'videos'/'scenes'
    if any(item.is_symlink() for item in (DATA_DIR, DATA_DIR/'videos', path)):
        raise VideoSceneError('storage_unavailable')
    return path.resolve()


def _child(parent: Path, name: str) -> Path:
    path = parent/name
    if path.is_symlink() or not path.resolve().is_relative_to(parent.resolve()):
        raise VideoSceneError('storage_unavailable')
    return path


def _identifier(value: str) -> str:
    if _ID.fullmatch(value) is None:
        raise VideoSceneError('not_found')
    return value


def directory(source_id: str) -> Path:
    return _child(root(), _identifier(source_id))


def cut_directory(source_id: str, cut_id: str) -> Path:
    return _child(_child(directory(source_id), 'cuts'), _identifier(cut_id))


def _preview_directory(source_id: str, preview_id: str) -> Path:
    return _child(_child(directory(source_id), 'previews'), _identifier(preview_id))


def _write(path: Path, model: VideoContract | WorkerIdentity) -> None:
    write_object(path, _JSON.validate_json(model.model_dump_json()))


def _read(path: Path) -> bytes:
    try:
        if path.is_symlink() or path.stat().st_size > 65536:
            raise VideoSceneError('storage_unavailable')
        data = path.read_bytes()
        if len(data) > 65536:
            raise VideoSceneError('storage_unavailable')
        return data
    except FileNotFoundError as error:
        raise VideoSceneError('not_found') from error


def _source(source_id: str) -> StoredSource:
    try:
        document = StoredSource.model_validate_json(_read(_child(directory(source_id), 'source.json')))
        if (document.source.id != source_id or document.source.bytes != document.signature.bytes
                or _format(document.source.name) != document.input_format
                or document.source.origin == 'upload' and document.path != 'source.video'):
            raise ValueError('metadata_id')
        return document
    except (OSError, ValueError) as error:
        raise VideoSceneError('storage_unavailable') from error


def _signature(path: Path, *, limit: int | None = None) -> SourceSignature:
    try:
        if not path.is_absolute() or any(item.is_symlink() for item in (path, *path.parents)):
            raise VideoSceneError('source_changed')
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise VideoSceneError('source_changed')
        if limit is not None and info.st_size > limit:
            raise VideoSceneError('upload_too_large')
        return SourceSignature(device=info.st_dev, inode=info.st_ino, bytes=info.st_size,
            mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns)
    except (OSError, ValidationError) as error:
        raise VideoSceneError('source_changed') from error


def _input(document: StoredSource) -> Path:
    path = Path(document.path) if document.source.origin == 'native' else _child(directory(document.source.id), 'source.video')
    if _signature(path) != document.signature:
        raise VideoSceneError('source_changed')
    return path


def get_source(source_id: str) -> VideoSceneSource:
    document = _source(source_id)
    _input(document)
    return document.source


def list_sources() -> VideoSceneSourcesResponse:
    path = root()
    sources = [_source(child.name).source for child in path.iterdir() if _ID.fullmatch(child.name)] if path.exists() else []
    return VideoSceneSourcesResponse(sources=sorted(sources, key=lambda source: (source.created_at, source.id), reverse=True)[:1000])


def readiness() -> VideoSceneReadiness:
    try:
        tool('ffmpeg'); tool('ffprobe')
        available = True
    except VideoMediaError:
        available = False
    return VideoSceneReadiness(ffmpeg_ready=available, native_picker_available=sys.platform == 'darwin')


async def _capture_command(path: Path, argv: list[str], *, timeout: float) -> tuple[bytes, int]:
    ownership = _child(path, 'worker.json')
    receipt = _child(path, 'worker-receipt.json')
    proc = await spawn_owned(argv, receipt_path=receipt, stdout=asyncio.subprocess.PIPE,
        on_identity=lambda identity: _write(ownership, WorkerIdentity(pid=identity.pid, token=identity.token, receipt=receipt.name)))
    try:
        output = await read_owned_output(proc, max_bytes=16384, timeout=timeout)
        return output, proc.returncode if proc.returncode is not None else 1
    except WorkerOutputError as error:
        raise VideoSceneError('invalid_media') from error
    finally:
        try:
            await await_cleanup(kill_process_tree(proc))
        except BaseException:
            _unresolved.add(path)
            raise
        ownership.unlink(missing_ok=True)
        receipt.unlink(missing_ok=True)


async def _command(path: Path, argv: list[str], *, timeout: float) -> None:
    _out, status = await _capture_command(path, argv, timeout=timeout)
    if status != 0:
        raise VideoSceneError('invalid_media')


async def _pick_file(path: Path) -> Path:
    if sys.platform != 'darwin':
        raise VideoSceneError('picker_unavailable')
    script = 'POSIX path of (choose file with prompt "Open a movie for local scene cutting" of type {"avi", "mkv", "mp4", "mov", "webm"})'
    try:
        output, status = await _capture_command(path, ['osascript', '-e', script], timeout=600)
    except (OSError, TimeoutError) as error:
        raise VideoSceneError('picker_failed') from error
    value = output.decode('utf-8', errors='strict').strip()
    if status != 0:
        raise VideoSceneError('picker_cancelled' if '-128' in value or 'cancel' in value.lower() else 'picker_failed')
    if not value or len(value) > 4096 or '\n' in value:
        raise VideoSceneError('picker_failed')
    return Path(value)


def _format(name: str) -> Literal['avi', 'mov', 'matroska']:
    suffix = Path(name).suffix.lower()
    if suffix == '.avi':
        return 'avi'
    if suffix in {'.mp4', '.mov'}:
        return 'mov'
    if suffix in {'.webm', '.mkv'}:
        return 'matroska'
    raise VideoSceneError('unsupported_format')


async def _upload(upload: UploadFile, path: Path) -> None:
    total = 0
    with path.open('xb') as handle:
        while chunk := await upload.read(1024*1024):
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise VideoSceneError('upload_too_large')
            handle.write(chunk)
    if not total:
        raise VideoSceneError('invalid_media')


async def _import(source_id: str, staging: Path, upload: UploadFile | None) -> VideoSceneSource:
    published = False
    try:
        async with _media_lock:
            if upload is None:
                path = await _pick_file(staging)
                if not path.is_absolute() or path.is_symlink():
                    raise VideoSceneError('source_changed')
                path = path.parent.resolve()/path.name
                name = path.name
                origin: Literal['native', 'upload'] = 'native'
            else:
                name = (upload.filename or '').replace('\\', '/').rsplit('/', 1)[-1]
                _format(name)
                path = staging/'source.video'
                await _upload(upload, path)
                origin = 'upload'
            if not 1 <= len(name) <= 255:
                raise VideoSceneError('unsupported_format')
            format_name = _format(name)
            signature = _signature(path, limit=MAX_NATIVE_BYTES if origin == 'native' else MAX_UPLOAD_BYTES)
            info = await probe_media(path, input_format=format_name)
            if (not 0 < info.video_duration <= 21600 or not 1 <= info.fps <= 120
                    or min(info.width, info.height) < 2 or max(info.width, info.height) > 4096):
                raise VideoSceneError('invalid_media')
            width, height = info.display_size
            if min(width, height) < 2 or max(width, height) > 4096:
                raise VideoSceneError('invalid_media')
            if _signature(path) != signature:
                raise VideoSceneError('source_changed')
            source = VideoSceneSource(id=source_id, name=name, origin=origin, duration_sec=info.video_duration,
                width=width, height=height, fps=info.fps, has_audio=info.audio_duration > 0,
                bytes=signature.bytes, created_at=now())
            document = StoredSource(source=source, path=str(path) if origin == 'native' else 'source.video',
                input_format=format_name, signature=signature)
            _write(staging/'source.json', document)
            staging.rename(directory(source_id))
            published = True
            return source
    except VideoMediaError as error:
        raise VideoSceneError(error.code) from error
    finally:
        if not published:
            if (staging/'worker.json').exists():
                _unresolved.add(staging)
            else:
                shutil.rmtree(staging)


async def _create(upload: UploadFile | None) -> VideoSceneSource:
    task: asyncio.Task[VideoSceneSource] | None = None
    try:
        async with admission_lock:
            require_setup_idle()
            if _closing or work_busy():
                raise VideoSceneError('busy')
            source_id = uuid4().hex
            staging = _child(root(), '.'+source_id+'.importing')
            staging.parent.mkdir(parents=True, exist_ok=True)
            staging.mkdir()
            write_object(staging/'preparation.json', {'id': source_id, 'kind': 'video_scene_import'})
            task = asyncio.create_task(_import(source_id, staging, upload))
            _imports[source_id] = task
            task.add_done_callback(lambda finished: _imports.pop(source_id, None))
        return await task
    finally:
        if task is not None and not task.done():
            task.cancel()
            await await_cleanup(asyncio.gather(task, return_exceptions=True))
        if upload is not None:
            await await_cleanup(upload.close())


async def create(video: UploadFile) -> VideoSceneSource:
    return await _create(video)


async def pick() -> VideoSceneSource:
    return await _create(None)


def _interval(start: float, seconds: float, source: VideoSceneSource) -> None:
    if not math.isfinite(start) or not math.isfinite(seconds) or start < 0 or seconds <= 0 or start+seconds > source.duration_sec+.000001:
        raise VideoSceneError('invalid_interval')


def _argv(document: StoredSource, path: Path, start: float, seconds: float, output: Path, width: int, height: int, limit: int) -> list[str]:
    return [tool('ffmpeg'), '-v', 'error', '-xerror', '-ss', str(start), '-f', document.input_format,
        '-protocol_whitelist', 'file,pipe', '-i', str(path), '-t', str(seconds),
        '-map', '0:v:0', '-map', '0:a:0?', '-vf', f'scale={width}:{height},fps=24,setsar=1',
        '-af', 'apad', '-map_metadata', '-1', '-map_chapters', '-1', '-c:v', 'libx264',
        '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
        '-movflags', '+faststart', '-fs', str(limit), str(output)]


async def _preview(source_id: str, preview_id: str, body: VideoScenePreviewRequest) -> VideoScenePreview:
    document = _source(source_id)
    path = _input(document)
    seconds = min(15., document.source.duration_sec-body.start_sec)
    _interval(body.start_sec, seconds, document.source)
    folder = _preview_directory(source_id, preview_id)
    folder.parent.mkdir(exist_ok=True)
    folder.mkdir()
    published = False
    try:
        async with _media_lock:
            scale = min(1., 960/max(document.source.width, document.source.height))
            width = max(2, int(document.source.width*scale)//2*2)
            height = max(2, int(document.source.height*scale)//2*2)
            output = folder/'pending.mp4'
            await _command(folder, _argv(document, path, body.start_sec, seconds, output, width, height, MAX_PREVIEW_BYTES), timeout=180)
            _input(document)
            await validate_media(output, seconds, (width, height), require_audio=document.source.has_audio)
            if output.stat().st_size > MAX_PREVIEW_BYTES:
                raise VideoSceneError('output_too_large')
            result = VideoScenePreview(id=preview_id, source_id=source_id, start_sec=body.start_sec,
                duration_sec=seconds, width=width, height=height, url=f'/api/videos/scenes/sources/{source_id}/previews/{preview_id}')
            _write(folder/'preview.json', StoredPreview(preview=result, sha256=await hash_file(output), created_at=now()))
            output.rename(folder/'preview.mp4')
            published = True
            previews = [child for child in folder.parent.iterdir() if _ID.fullmatch(child.name) and not child.is_symlink()]
            for old in sorted(previews, key=lambda child: (child == folder, child.stat().st_mtime_ns), reverse=True)[3:]:
                shutil.rmtree(old)
            return result
    finally:
        if not published:
            if (folder/'worker.json').exists():
                _unresolved.add(folder)
            else:
                shutil.rmtree(folder)


async def preview(source_id: str, body: VideoScenePreviewRequest) -> VideoScenePreview:
    task: asyncio.Task[VideoScenePreview] | None = None
    try:
        async with admission_lock:
            require_setup_idle()
            if _closing or work_busy():
                raise VideoSceneError('busy')
            get_source(source_id)
            preview_id = uuid4().hex
            task = asyncio.create_task(_preview(source_id, preview_id, body))
            _previews[preview_id] = task
            task.add_done_callback(lambda finished: _previews.pop(preview_id, None))
        return await task
    finally:
        if task is not None and not task.done():
            task.cancel()
            await await_cleanup(asyncio.gather(task, return_exceptions=True))


def preview_media(source_id: str, preview_id: str) -> Path:
    folder = _preview_directory(source_id, preview_id)
    try:
        record = StoredPreview.model_validate_json(_read(_child(folder, 'preview.json')))
        if record.preview.id != preview_id or record.preview.source_id != source_id:
            raise ValueError('metadata_id')
        path = _child(folder, 'preview.mp4')
        if not path.is_file():
            raise VideoSceneError('not_found')
        return path
    except (OSError, ValueError) as error:
        raise VideoSceneError('storage_unavailable') from error


def load_cut(source_id: str, cut_id: str) -> VideoSceneCut:
    try:
        cut = VideoSceneCut.model_validate_json(_read(_child(cut_directory(source_id, cut_id), 'cut.json')))
        if cut.source_id != source_id or cut.id != cut_id:
            raise ValueError('metadata_id')
        return cut
    except (OSError, ValueError) as error:
        raise VideoSceneError('storage_unavailable') from error


def save_cut(cut: VideoSceneCut) -> None:
    checked = VideoSceneCut.model_validate(cut.model_dump())
    _write(_child(cut_directory(checked.source_id, checked.id), 'cut.json'), checked)


def get_cut(source_id: str, cut_id: str) -> VideoSceneCut:
    with document_lock(_child(cut_directory(source_id, cut_id), 'cut.json')):
        return load_cut(source_id, cut_id)


def list_cuts(source_id: str) -> VideoSceneCutsResponse:
    _source(source_id)
    path = _child(directory(source_id), 'cuts')
    cuts = [get_cut(source_id, child.name) for child in path.iterdir() if _ID.fullmatch(child.name)] if path.exists() else []
    return VideoSceneCutsResponse(cuts=sorted(cuts, key=lambda cut: (cut.created_at, cut.id), reverse=True)[:1000])


def _change(source_id: str, cut_id: str, *, status: Literal['queued', 'cutting', 'ready', 'failed', 'cancelled'], error_code: str = '') -> None:
    with document_lock(_child(cut_directory(source_id, cut_id), 'cut.json')):
        cut = load_cut(source_id, cut_id)
        cut.status = status
        cut.error_code = error_code
        cut.updated_at = now()
        save_cut(cut)


async def _run_cut(source_id: str, cut_id: str) -> None:
    folder = cut_directory(source_id, cut_id)
    pending = _child(folder, 'pending.mp4')
    try:
        async with _media_lock:
            _change(source_id, cut_id, status='cutting')
            cut = get_cut(source_id, cut_id)
            document = _source(source_id)
            path = _input(document)
            await _command(folder, _argv(document, path, cut.start_sec, cut.seconds, pending, cut.width, cut.height, MAX_CUT_BYTES), timeout=3600)
            _input(document)
            await _validate_cut(folder, pending, cut)
            if pending.stat().st_size > MAX_CUT_BYTES:
                raise VideoSceneError('output_too_large')
            digest = await hash_file(pending)
            with document_lock(_child(folder, 'cut.json')):
                cut = load_cut(source_id, cut_id)
                cut.output_sha256 = digest
                cut.bytes = pending.stat().st_size
                save_cut(cut)
                pending.rename(_child(folder, 'cut.mp4'))
                cut.status = 'ready'
                cut.output_url = f'/api/videos/scenes/sources/{source_id}/cuts/{cut_id}/media'
                cut.updated_at = now()
                save_cut(cut)
    except asyncio.CancelledError:
        _change(source_id, cut_id, status='cancelled', error_code='cancelled')
        raise
    except (VideoSceneError, VideoMediaError, VideoProjectError, OSError, ValueError, TimeoutError) as error:
        code = error.code if isinstance(error, (VideoSceneError, VideoMediaError, VideoProjectError)) else 'storage_unavailable'
        _change(source_id, cut_id, status='failed', error_code=code)
        logger.warning('Local scene export failed (%s)', code)
    finally:
        if (folder/'worker.json').exists():
            _unresolved.add(folder)
        else:
            pending.unlink(missing_ok=True)


async def create_cut(source_id: str, body: VideoSceneCutRequest) -> VideoSceneCut:
    async with admission_lock:
        require_setup_idle()
        if _closing or work_busy():
            raise VideoSceneError('busy')
        source = get_source(source_id)
        _interval(body.start_sec, body.end_sec-body.start_sec, source)
        cut_id = uuid4().hex
        folder = cut_directory(source_id, cut_id)
        folder.parent.mkdir(exist_ok=True)
        folder.mkdir()
        timestamp = now()
        cut = VideoSceneCut(id=cut_id, source_id=source_id, name=body.name.strip(), start_sec=body.start_sec,
            end_sec=body.end_sec, seconds=body.end_sec-body.start_sec, created_at=timestamp, updated_at=timestamp,
            width=max(2, source.width//2*2), height=max(2, source.height//2*2), has_audio=source.has_audio)
        save_cut(cut)
        task = asyncio.create_task(_run_cut(source_id, cut_id))
        _cuts[cut_id] = task
        task.add_done_callback(lambda finished: _cuts.pop(cut_id, None))
        return cut


async def cancel(source_id: str, cut_id: str) -> VideoSceneCut:
    cut = get_cut(source_id, cut_id)
    if cut.status in {'queued', 'cutting'}:
        await cancel_and_wait(_cuts.get(cut_id))
        if get_cut(source_id, cut_id).status in {'queued', 'cutting'}:
            _change(source_id, cut_id, status='cancelled', error_code='cancelled')
    return get_cut(source_id, cut_id)


async def resolve_cut(source_id: str, cut_id: str) -> tuple[Path, VideoSceneCut]:
    cut = get_cut(source_id, cut_id)
    if cut.status != 'ready' or not cut.output_sha256:
        raise VideoSceneError('cut_not_ready')
    path = _child(cut_directory(source_id, cut_id), 'cut.mp4')
    try:
        if path.stat().st_size != cut.bytes or await hash_file(path) != cut.output_sha256:
            raise VideoSceneError('cut_changed')
    except (OSError, VideoProjectError) as error:
        raise VideoSceneError('cut_changed') from error
    return path, cut


async def _validate_cut(folder: Path, path: Path, cut: VideoSceneCut) -> None:
    await validate_media(path, cut.seconds, (cut.width, cut.height), require_audio=cut.has_audio, decode=False)
    maps = ['-map', '0:v:0']+(['-map', '0:a:0'] if cut.has_audio else [])
    await _command(folder, [tool('ffmpeg'), '-v', 'error', '-xerror', '-f', 'mov',
        '-protocol_whitelist', 'file,pipe', '-i', str(path), *maps, '-f', 'null', '-'], timeout=3600)


async def _recover_worker(path: Path) -> None:
    try:
        base = root()
        if not path.is_absolute() or not path.is_relative_to(base) or path == base or path.resolve() != path:
            raise VideoSceneError('worker_unresolved')
        for parent in (path, *path.parents):
            if parent == base:
                break
            if parent.is_symlink():
                raise VideoSceneError('worker_unresolved')
    except (OSError, VideoSceneError) as error:
        raise VideoSceneError('worker_unresolved') from error
    try:
        ownership = _child(path, 'worker.json')
        if not ownership.exists():
            _unresolved.discard(path)
            return
        identity = WorkerIdentity.model_validate_json(_read(ownership))
        if identity.receipt != 'worker-receipt.json':
            raise VideoSceneError('worker_unresolved')
        receipt = _child(path, identity.receipt)
        if not await terminate_verified(WorkerIdentity(pid=identity.pid, token=identity.token, receipt=str(receipt))):
            raise VideoSceneError('worker_unresolved')
        ownership.unlink()
        receipt.unlink(missing_ok=True)
        _unresolved.discard(path)
    except (OSError, ValueError, VideoSceneError) as error:
        _unresolved.add(path)
        raise VideoSceneError('worker_unresolved') from error


async def _recover_cut(source_id: str, cut_id: str) -> None:
    folder = cut_directory(source_id, cut_id)
    try:
        await _recover_worker(folder)
    except VideoSceneError:
        _unresolved.add(folder)
        _change(source_id, cut_id, status='failed', error_code='worker_unresolved')
        return
    cut = get_cut(source_id, cut_id)
    if cut.status not in {'queued', 'cutting'} and not (cut.status == 'failed' and cut.error_code == 'worker_unresolved'):
        return
    output = _child(folder, 'cut.mp4')
    pending = _child(folder, 'pending.mp4')
    candidate = output if output.is_file() else pending
    if cut.output_sha256 and candidate.is_file():
        try:
            if candidate.stat().st_size != cut.bytes or await hash_file(candidate) != cut.output_sha256:
                raise VideoSceneError('cut_changed')
            await _validate_cut(folder, candidate, cut)
            if candidate == pending:
                pending.rename(output)
            with document_lock(_child(folder, 'cut.json')):
                checked = load_cut(source_id, cut_id)
                checked.status = 'ready'
                checked.updated_at = now()
                checked.output_url = f'/api/videos/scenes/sources/{source_id}/cuts/{cut_id}/media'
                save_cut(checked)
            return
        except (VideoSceneError, VideoMediaError, VideoProjectError, OSError, ValueError):
            pass
    pending.unlink(missing_ok=True)
    _change(source_id, cut_id, status='failed', error_code='interrupted')


async def _recover_source(source_id: str) -> None:
    folder = directory(source_id)
    for kind in ('cuts', 'previews'):
        children = _child(folder, kind)
        if not children.exists():
            continue
        for child in children.iterdir():
            if _ID.fullmatch(child.name) is None:
                continue
            try:
                if kind == 'cuts':
                    await _recover_cut(source_id, child.name)
                else:
                    checked = _preview_directory(source_id, child.name)
                    await _recover_worker(checked)
                    if not _child(checked, 'preview.mp4').is_file() or not _child(checked, 'preview.json').is_file():
                        shutil.rmtree(checked)
            except (VideoSceneError, VideoMediaError, VideoProjectError, OSError, ValueError):
                # Reconcile siblings even when one retained document is unreadable.
                logger.warning('Unresolved scene artifact retained')


async def recover() -> None:
    global _closing
    _closing = False
    if _imports or _previews or _cuts:
        return
    try:
        path = root()
    except (VideoSceneError, OSError):
        logger.warning('Scene storage unavailable; retained without recovery')
        return
    _unresolved.clear()
    if not path.exists():
        return
    for child in path.iterdir():
        if re.fullmatch(r'\.[0-9a-f]{32}\.importing', child.name):
            try:
                folder = _child(path, child.name)
                marker = _child(folder, 'preparation.json')
                await _recover_worker(folder)
                if _JSON.validate_json(_read(marker)) != {'id': child.name[1:33], 'kind': 'video_scene_import'}:
                    raise VideoSceneError('storage_unavailable')
                shutil.rmtree(folder)
            except (VideoSceneError, OSError, ValueError):
                _unresolved.add(child)
                logger.warning('Unresolved scene import retained')
        elif _ID.fullmatch(child.name):
            try:
                await _recover_source(child.name)
            except (VideoSceneError, VideoMediaError, OSError, ValueError):
                logger.warning('Unresolved scene source retained')


def work_busy() -> bool:
    return bool(_imports or _previews or _cuts or _unresolved)


async def shutdown() -> None:
    global _closing
    _closing = True
    tasks = (*_imports.values(), *_previews.values(), *_cuts.values())
    for task in tasks:
        if not task.done() and not task.cancelling():
            task.cancel()
    await await_cleanup(asyncio.gather(*tasks, return_exceptions=True))
    for path in tuple(_unresolved):
        try:
            await await_cleanup(_recover_worker(path))
        except VideoSceneError:
            logger.warning('Scene worker could not be drained')
    if _unresolved:
        raise VideoSceneError('worker_unresolved')
