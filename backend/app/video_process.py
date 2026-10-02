"""Local worker supervisor: parent pipe EOF kills its complete process group.

The random token and receipt identify this supervisor before recovery can kill
it. A PID alone is never treated as proof of ownership.
"""

from __future__ import annotations
import argparse, asyncio, json, os, signal, subprocess, sys, tempfile, threading, time, uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO
from pydantic import BaseModel, Field, ValidationError

if not __package__:
    # The wrapper may run from a model/tool directory outside the backend.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.orchestrator.windows_job import NAME_PATTERN, WindowsJob


# Recovery keeps the same handle when accounting fails, rather than silently
# releasing ownership or leaking a fresh handle on every retry.
_recovering_windows_jobs: dict[str, WindowsJob] = {}


class WorkerIdentity(BaseModel):
    pid: int = Field(ge=1)
    token: str = Field(pattern=r"^[0-9a-f]{32}$")
    receipt: str


class WorkerReceipt(BaseModel):
    pid: int = Field(ge=1, strict=True)
    token: str = Field(pattern=r"^[0-9a-f]{32}$")
    returncode: int | None = None
    job_name: str | None = Field(default=None, pattern="^" + NAME_PATTERN + "$", max_length=80)


class WorkerOutputError(Exception):
    """A captured worker stream is missing or exceeds its permitted size."""


async def read_owned_output(
    proc: asyncio.subprocess.Process, *, max_bytes: int, timeout: float
) -> bytes:
    """Collect stdout through EOF without closing the supervisor stdin pipe.

    The process owner must terminate and reap the worker on failure, including
    output overflow: waiting without draining a full pipe can otherwise hang.
    """
    if max_bytes < 1:
        raise ValueError("invalid_output_limit")
    if proc.stdout is None:
        raise WorkerOutputError("worker_stdout_unavailable")
    output = bytearray()
    async with asyncio.timeout(timeout):
        while chunk := await proc.stdout.read(min(65536, max_bytes - len(output) + 1)):
            if len(output) + len(chunk) > max_bytes:
                raise WorkerOutputError("worker_output_too_large")
            output.extend(chunk)
        await proc.wait()
    return bytes(output)


def _receipt(path: Path, value: WorkerReceipt) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, delete=False, encoding="utf-8"
        ) as handle:
            temporary = Path(handle.name)
            handle.write(value.model_dump_json())
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _watch_parent(stream: IO[bytes], job: WindowsJob | None) -> None:
    try:
        while stream.read(1):
            pass
    finally:
        if job is not None:
            job.terminate()
        elif sys.platform != "win32":
            os.killpg(os.getpgrp(), signal.SIGKILL)
        else:
            raise OSError("worker_job_required")


def _supervise_worker(command: list[str], receipt: Path, value: WorkerReceipt,
                      parent: IO[bytes], cwd: str | None, job: WindowsJob | None) -> int:
    if job is not None:
        # Assign the wrapper before it can create any descendants.
        job.assign_current()
        value.job_name = job.name
    _receipt(receipt, value)
    threading.Thread(target=_watch_parent, args=(parent, job), daemon=True,
                     name="tool-parent-liveness").start()
    try:
        proc = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL)
        value.returncode = proc.wait()
    except Exception:
        value.returncode = 1
    _receipt(receipt, value)
    if job is not None:
        # A tool launcher can exit while GPU/download/codec children remain.
        job.terminate(max(0, value.returncode) & 0xFFFFFFFF)
    return value.returncode


def _supervise() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--cwd")
    parser.add_argument("--job")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command: list[str] = args.command
    if command and command[0] == "--":
        command = command[1:]
    value = WorkerReceipt(pid=os.getpid(), token=args.token)
    if sys.platform == "win32" and args.job is None:
        parser.error("Windows tool workers require an owned job")
    job = WindowsJob.open(args.job) if args.job is not None else None
    try:
        # A daemon blocked in BufferedReader.read can abort Python shutdown.
        # The raw pipe remains held by the watcher until wrapper process exit;
        # closing it here could race EOF handling with a successful POSIX exit.
        parent = os.fdopen(os.dup(sys.stdin.fileno()), "rb", buffering=0)
        return _supervise_worker(command, Path(args.receipt), value, parent, args.cwd, job)
    finally:
        if job is not None:
            job.close()


async def spawn_owned(
    argv: list[str],
    *,
    receipt_path: Path,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    stdout: int | None = None,
    on_identity: Callable[[WorkerIdentity], None] | None = None,
) -> asyncio.subprocess.Process:
    from .job_lifecycle import await_cleanup, kill_process_tree, register_windows_job

    token = uuid.uuid4().hex
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--token",
        token,
        "--receipt",
        str(receipt_path.resolve()),
    ]
    job = WindowsJob.create() if sys.platform == "win32" else None
    if job is not None:
        command += ["--job", job.name]
    if cwd is not None:
        command += ["--cwd", str(cwd)]
    command += ["--", *argv]
    creation = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *command,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=stdout,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=sys.platform != "win32",
        )
    )
    try:
        proc = await asyncio.shield(creation)
        if job is not None:
            register_windows_job(proc, job)
        if on_identity is not None:
            on_identity(
                WorkerIdentity(pid=proc.pid, token=token, receipt=str(receipt_path))
            )
        return proc
    except BaseException:
        try:
            await await_cleanup(creation)
        finally:
            # await_cleanup can propagate a second cancellation after creation
            # succeeded. The created wrapper still belongs to us in that case.
            if creation.done() and not creation.cancelled() and creation.exception() is None:
                proc = creation.result()
                if job is not None:
                    register_windows_job(proc, job)
                await await_cleanup(kill_process_tree(proc))
            elif job is not None:
                job.close()
        raise


async def terminate_verified(identity: WorkerIdentity) -> bool:
    """False means a live process could not be verified; never kill that PID."""
    from .job_lifecycle import spawn_process, communicate_process

    if sys.platform == "win32":
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            f'(Get-CimInstance Win32_Process -Filter "ProcessId = {identity.pid}").CommandLine',
        ]
    else:
        command = ["ps", "-p", str(identity.pid), "-o", "command="]
    proc = await spawn_process(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, _ = await communicate_process(proc, 5)
    text = out.decode(errors="replace").strip()
    if text and (
        str(Path(__file__).resolve()) not in text
        or f"--token {identity.token}" not in text
    ):
        return False
    try:
        path = Path(identity.receipt)
        if path.stat().st_size > 65536:
            return False
        receipt = WorkerReceipt.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return not text and sys.platform != "win32"
    if receipt.pid != identity.pid or receipt.token != identity.token:
        return False
    if sys.platform == "win32":
        # Legacy receipts cannot prove descendants were members of a job.
        if receipt.job_name is None:
            return False
        from .job_lifecycle import drain_windows_job
        job = _recovering_windows_jobs.get(receipt.job_name)
        if job is None:
            try:
                job = WindowsJob.open(receipt.job_name)
            except OSError as error:
                code: object = getattr(error, "winerror", None)
                # ERROR_FILE_NOT_FOUND: the exact named kernel object is gone.
                return type(code) is int and code == 2
            _recovering_windows_jobs[receipt.job_name] = job
        try:
            job.terminate()
            async with asyncio.timeout(10):
                await drain_windows_job(job)
            job.close()
            del _recovering_windows_jobs[receipt.job_name]
            return True
        except (OSError, TimeoutError):
            return False
    if not text:
        return True
    try:
        if os.getpgid(identity.pid) != identity.pid:
            return False
        os.killpg(identity.pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    for _ in range(100):
        try:
            os.kill(identity.pid, 0)
        except ProcessLookupError:
            return True
        await asyncio.sleep(0.02)
    return False


if __name__ == "__main__":
    raise SystemExit(_supervise())
