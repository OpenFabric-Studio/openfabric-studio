"""Owned, bounded FFmpeg conversion for the legacy YuE upload boundary."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterable, Awaitable, Callable
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
import wave

import httpx

from .config import FFMPEG_BIN_DIR
from .job_lifecycle import await_cleanup, kill_process_tree
from .resource_admission import admission_lock, require_setup_idle
from .video_process import WorkerOutputError, read_owned_output, spawn_owned

_LOG = logging.getLogger(__name__)
CONVERSION_TIMEOUT = 120.0
CHUNK_BYTES = 65536
_TASKS: set[asyncio.Task[httpx.Response]] = set()
_PENDING: dict[asyncio.subprocess.Process, tempfile.TemporaryDirectory[str]] = {}
_CLEANUP_PENDING: set[tempfile.TemporaryDirectory[str]] = set()
_STOPPING = False


class UploadConversionError(Exception):
    def __init__(self, code: str, status_code: int = 400) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def get_ffmpeg_bin() -> str | None:
    candidate = FFMPEG_BIN_DIR / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if candidate.is_file():
        return str(candidate)
    env_bin = os.environ.get("FFMPEG_BIN")
    if env_bin:
        found = shutil.which(env_bin)
        if found:
            return found
        if Path(env_bin).is_file():
            return env_bin
    return shutil.which("ffmpeg")


def _validate_wav(path: Path) -> None:
    with wave.open(str(path), "rb") as audio:
        if audio.getnframes() < 1 or audio.getframerate() != 44100 or audio.getnchannels() != 1 or audio.getsampwidth() != 2:
            raise UploadConversionError("audio_conversion_failed")
        remaining = audio.getnframes()
        while remaining:
            frames = min(remaining, CHUNK_BYTES // 2)
            if len(audio.readframes(frames)) != frames * 2:
                raise UploadConversionError("audio_conversion_failed")
            remaining -= frames


async def _convert(source: Path, output: Path, scratch: tempfile.TemporaryDirectory[str]) -> None:
    ffmpeg = get_ffmpeg_bin()
    if ffmpeg is None:
        raise UploadConversionError("audio_converter_unavailable", 503)
    # The supervisor reserves stdin for parent liveness. Seekable input/output
    # files also let FFmpeg write accurate RIFF lengths before publication.
    proc: asyncio.subprocess.Process | None = None
    root = Path(scratch.name)
    try:
        proc = await spawn_owned(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
             "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", str(output)],
            receipt_path=root / "worker.json", stdout=asyncio.subprocess.PIPE,
        )
        diagnostic = await read_owned_output(proc, max_bytes=65536, timeout=CONVERSION_TIMEOUT)
        if proc.returncode != 0:
            _LOG.warning("YuE upload conversion failed: %s", diagnostic.decode(errors="replace"))
            raise UploadConversionError("audio_conversion_failed")
    except TimeoutError as error:
        raise UploadConversionError("audio_conversion_timed_out", 504) from error
    except (OSError, EOFError, wave.Error, WorkerOutputError) as error:
        _LOG.warning("YuE upload conversion failed", exc_info=True)
        raise UploadConversionError("audio_conversion_failed") from error
    finally:
        # Scratch also belongs to the upload/forwarding task, so a successful
        # converter drain must not remove the output before HTTPX consumes it.
        if proc is not None:
            await _drain(proc, scratch)
    # The output is forwarded as a file now, so validate only after all owned
    # writers are gone. A descendant's final write must not invalidate a PCM
    # check performed before the drain.
    try:
        await await_cleanup(asyncio.to_thread(_validate_wav, output))
    except (OSError, EOFError, wave.Error) as error:
        _LOG.warning("YuE upload conversion output is invalid", exc_info=True)
        raise UploadConversionError("audio_conversion_failed") from error


async def _drain(proc: asyncio.subprocess.Process, scratch: tempfile.TemporaryDirectory[str]) -> None:
    # Keep the process object and receipt until descendant accounting succeeds.
    # A failed drain must keep setup/library admission closed, even after the
    # HTTP task has settled. Shutdown retries the same owned process object.
    _PENDING[proc] = scratch
    drain = asyncio.create_task(kill_process_tree(proc))
    try:
        await await_cleanup(drain)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        _LOG.exception("YuE upload converter cleanup failed")
        raise UploadConversionError("audio_converter_cleanup_failed", 503) from error
    finally:
        # await_cleanup can report cancellation after a successful drain. Its
        # completed task is the proof that quarantine may still be released.
        if drain.done() and not drain.cancelled() and drain.exception() is None:
            _PENDING.pop(proc, None)


async def file_chunks(path: Path) -> AsyncGenerator[bytes, None]:
    """Read a seekable upload without retaining the full recording in memory."""
    with path.open("rb") as source:
        while chunk := await await_cleanup(asyncio.to_thread(source.read, CHUNK_BYTES)):
            yield chunk


async def _cleanup_scratch(scratch: tempfile.TemporaryDirectory[str]) -> None:
    # Retain failed deletion for shutdown to retry. Dropping ownership here
    # would silently leak staged recordings and release setup admission while
    # cleanup still needs to mutate the temporary directory.
    cleanup = asyncio.create_task(asyncio.to_thread(scratch.cleanup))
    try:
        await await_cleanup(cleanup)
    except OSError as error:
        _LOG.warning("YuE upload scratch cleanup failed", exc_info=True)
        raise UploadConversionError("audio_upload_cleanup_failed", 503) from error
    finally:
        if cleanup.done() and not cleanup.cancelled() and cleanup.exception() is None:
            _CLEANUP_PENDING.discard(scratch)
        else:
            _CLEANUP_PENDING.add(scratch)


async def _forward_upload(chunks: AsyncIterable[bytes], convert: bool,
                          send: Callable[[Path], Awaitable[httpx.Response]]) -> httpx.Response:
    try:
        scratch = tempfile.TemporaryDirectory(prefix="openfabric-yue-upload-")
    except OSError as error:
        _LOG.warning("YuE upload scratch creation failed", exc_info=True)
        raise UploadConversionError("audio_upload_storage_failed", 503) from error
    root = Path(scratch.name)
    source, output = root / "input.audio", root / "output.wav"
    try:
        try:
            with source.open("wb") as recording:
                async for chunk in chunks:
                    # ASGI chooses incoming chunk sizes. Slice a memoryview so
                    # writes remain bounded even when a server gives us a large
                    # message, without copying that complete message again.
                    view = memoryview(chunk)
                    for start in range(0, len(view), CHUNK_BYTES):
                        await await_cleanup(asyncio.to_thread(recording.write, view[start:start + CHUNK_BYTES]))
        except OSError as error:
            _LOG.warning("YuE upload staging failed", exc_info=True)
            raise UploadConversionError("audio_upload_storage_failed", 503) from error
        if convert:
            await _convert(source, output, scratch)
        try:
            return await send(output if convert else source)
        except OSError as error:
            _LOG.warning("YuE upload file forwarding failed", exc_info=True)
            raise UploadConversionError("audio_upload_storage_failed", 503) from error
    finally:
        # A failed descendant drain retains both scratch and admission. The
        # same process is retried by shutdown before its files can be removed.
        if not any(retained is scratch for retained in _PENDING.values()):
            try:
                await _cleanup_scratch(scratch)
            except UploadConversionError:
                # Cleanup is logged and retained for retry. Keep an accepted
                # native path token or the original conversion/network error;
                # replacing either with a failure invites duplicate uploads.
                pass


async def forward_upload(chunks: AsyncIterable[bytes], *, convert: bool,
                         send: Callable[[Path], Awaitable[httpx.Response]]) -> httpx.Response:
    """Own receive, conversion and forwarding through shutdown/cancellation."""
    async with admission_lock:
        require_setup_idle()
        if _STOPPING:
            raise UploadConversionError("audio_converter_stopping", 503)
        if _PENDING:
            raise UploadConversionError("audio_converter_cleanup_failed", 503)
        if _CLEANUP_PENDING:
            raise UploadConversionError("audio_upload_cleanup_failed", 503)
        task = asyncio.create_task(_forward_upload(chunks, convert, send))
        _TASKS.add(task)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if not task.done() and not task.cancelling():
            task.cancel()
        await await_cleanup(asyncio.gather(task, return_exceptions=True))
        raise
    finally:
        _TASKS.discard(task)


def start() -> None:
    global _STOPPING
    _STOPPING = False


def work_busy() -> bool:
    return bool(_PENDING) or bool(_CLEANUP_PENDING) or any(not task.done() for task in _TASKS)


async def shutdown() -> None:
    global _STOPPING
    _STOPPING = True
    tasks = tuple(_TASKS)
    for task in tasks:
        if not task.done() and not task.cancelling():
            task.cancel()
    await await_cleanup(asyncio.gather(*tasks, return_exceptions=True))
    _TASKS.difference_update(tasks)

    async def retry(proc: asyncio.subprocess.Process, scratch: tempfile.TemporaryDirectory[str]) -> None:
        await _drain(proc, scratch)
        await _cleanup_scratch(scratch)

    await await_cleanup(asyncio.gather(*(retry(proc, scratch) for proc, scratch in tuple(_PENDING.items())), return_exceptions=True))
    await await_cleanup(asyncio.gather(*(_cleanup_scratch(scratch) for scratch in tuple(_CLEANUP_PENDING)), return_exceptions=True))
