"""Own job tasks and subprocess sessions through cancellation and shutdown."""
from __future__ import annotations

import asyncio
import os
import signal
import sys
from collections.abc import Awaitable, Mapping
from typing import IO, TypeVar

from .orchestrator.windows_job import WindowsJob

T = TypeVar("T")

# Strong ownership survives an exited wrapper or a failed descendant drain.
# Only verified empty jobs are removed; PID reuse cannot alias a process object.
_windows_jobs: dict[asyncio.subprocess.Process, WindowsJob] = {}
_windows_drains: dict[asyncio.subprocess.Process, asyncio.Task[None]] = {}


def register_windows_job(proc: asyncio.subprocess.Process, job: WindowsJob) -> None:
    previous = _windows_jobs.get(proc)
    if previous is not None and previous is not job:
        raise RuntimeError("worker_job_already_registered")
    _windows_jobs[proc] = job


async def drain_windows_job(job: WindowsJob) -> None:
    """Require kernel accounting proof before releasing a job query handle."""
    while job.active_processes() != 0:
        await asyncio.sleep(0.02)


async def _kill_windows_tree(proc: asyncio.subprocess.Process, job: WindowsJob) -> None:
    # EOF also covers cancellation before the wrapper has joined its job.
    if proc.stdin is not None:
        proc.stdin.close()
    job.terminate()
    async with asyncio.timeout(10):
        await proc.communicate()
        await drain_windows_job(job)
    job.close()
    del _windows_jobs[proc]


async def await_cleanup(operation: Awaitable[T]) -> T:
    """Finish owned cleanup before propagating cancellation of its caller."""
    drained = asyncio.ensure_future(operation)
    cancelled = False
    while not drained.done():
        try:
            await asyncio.shield(drained)
        except asyncio.CancelledError:
            cancelled = True
    result = drained.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


def request_cancel(task: asyncio.Task[None] | None) -> None:
    """Request cancellation synchronously before yielding to any queued jobs."""
    if task is None or task is asyncio.current_task():
        return
    if not task.done() and not task.cancelling():
        task.cancel()


async def cancel_and_wait(task: asyncio.Task[None] | None) -> None:
    """Cancel once, then await cleanup; repeated cancellation must not interrupt it."""
    if task is None or task is asyncio.current_task():
        return
    request_cancel(task)
    await await_cleanup(asyncio.gather(task, return_exceptions=True))


async def kill_process_tree(proc: asyncio.subprocess.Process | None) -> None:
    if proc is None:
        return
    job = _windows_jobs.get(proc)
    if job is not None:
        # Cancellation and owner-finally may converge on this same process.
        # One owned drain prevents concurrent PIPE reads and handle double-close.
        drain = _windows_drains.get(proc)
        if drain is None:
            drain = asyncio.create_task(_kill_windows_tree(proc, job))
            _windows_drains[proc] = drain
        try:
            await await_cleanup(drain)
        finally:
            if drain.done() and _windows_drains.get(proc) is drain:
                del _windows_drains[proc]
        return
    if sys.platform == "win32":
        if proc.returncode is None:
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(proc.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
    else:
        # The session group can still contain descendants after the parent exits.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    # wait() can hang after SIGKILL when a PIPE transport is paused with a
    # full buffer. Drain it as well so the subprocess transport can close.
    await proc.communicate()


async def spawn_process(
    *argv: str,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    stdout: int | IO[str] | IO[bytes] | None = None,
    stderr: int | IO[str] | IO[bytes] | None = None,
) -> asyncio.subprocess.Process:
    """Start an owned session, including when cancellation races process creation."""
    creation = asyncio.create_task(asyncio.create_subprocess_exec(
        *argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr,
        start_new_session=sys.platform != "win32",
    ))
    try:
        return await asyncio.shield(creation)
    except asyncio.CancelledError:
        proc = await creation
        await kill_process_tree(proc)
        raise


async def communicate_process(
    proc: asyncio.subprocess.Process, timeout: float | None = None,
) -> tuple[bytes, bytes]:
    """Capture output, terminating and reaping the session on timeout/cancellation."""
    try:
        async with asyncio.timeout(timeout):
            out, err = await proc.communicate()
        return out or b"", err or b""
    except (asyncio.CancelledError, TimeoutError):
        await kill_process_tree(proc)
        raise
