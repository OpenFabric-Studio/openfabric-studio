"""Streaming, cancellable owned copies/hashes of local video inputs."""

from __future__ import annotations
import asyncio, hashlib, threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar
from .job_lifecycle import await_cleanup
from .video_projects import VideoProjectError

T = TypeVar("T")


async def _owned(operation: Callable[[threading.Event], T]) -> T:
    stop = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(operation, stop))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.set()
        await await_cleanup(asyncio.gather(task, return_exceptions=True))
        raise


def _signature(path: Path) -> tuple[int, int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


async def hash_file(path: Path) -> str:
    def run(stop: threading.Event) -> str:
        before = _signature(path)
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                if stop.is_set():
                    raise InterruptedError("hash_cancelled")
                digest.update(chunk)
        if stop.is_set():
            raise InterruptedError("hash_cancelled")
        if _signature(path) != before:
            raise VideoProjectError("source_changed")
        return digest.hexdigest()

    return await _owned(run)


async def copy_verified(source: Path, dest: Path, expected_sha256: str) -> None:
    def run(stop: threading.Event) -> None:
        before = _signature(source)
        digest = hashlib.sha256()
        created = False
        try:
            with source.open("rb") as original, dest.open("xb") as copied:
                created = True
                while chunk := original.read(1024 * 1024):
                    if stop.is_set():
                        raise InterruptedError("copy_cancelled")
                    copied.write(chunk)
                    digest.update(chunk)
            if stop.is_set():
                raise InterruptedError("copy_cancelled")
            if _signature(source) != before or digest.hexdigest() != expected_sha256:
                raise VideoProjectError("source_changed")
        except BaseException:
            if created:
                dest.unlink(missing_ok=True)
            raise

    await _owned(run)
