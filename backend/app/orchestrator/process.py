"""Launch owned native supervisors; backend death tears down their children."""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path
from typing import IO
from typing import Optional

import httpx

from ..config import LOG_DIR, LOG_TAIL_LINES, ProcessSpec
from .windows_job import WindowsJob
from .windows_supervisor import supervisor_command as windows_command
from .posix_supervisor import read_receipt, supervisor_command as posix_command

IS_WINDOWS = sys.platform == "win32"


class StartCancelled(Exception):
    """A model start was abandoned because Stop or shutdown was requested."""


def _build_env(spec: ProcessSpec) -> dict[str, str]:
    env = os.environ.copy()
    env.update(spec.env)
    if spec.extra_path_dirs:
        prefix = os.pathsep.join(str(p) for p in spec.extra_path_dirs)
        env["PATH"] = f"{prefix}{os.pathsep}{env.get('PATH', '')}"
    return env


def tail_log(name: str, lines: int = LOG_TAIL_LINES) -> str:
    path = LOG_DIR / f"{name}.log"
    if not path.exists():
        return ""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(content.splitlines()[-lines:])


class ManagedProcess:
    def __init__(self, spec: ProcessSpec) -> None:
        self.spec = spec
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._log_file: IO[str] | None = None
        self._owns_group = False
        self._windows_job: WindowsJob | None = None
        self._supervisor_token: str | None = None
        self._supervisor_receipt: Path | None = None

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def require_owned(self) -> None:
        """Verify the owned supervisor identity before a user-requested stop.

        Never infer ownership from an HTTP port or a numeric PID. Windows
        retains the kernel job handle; POSIX supervisors issue a token-bound
        receipt before they start an engine.
        """
        if self._proc is None or not self.is_running:
            raise RuntimeError('Native process ownership could not be verified')
        if IS_WINDOWS:
            if self._windows_job is None:
                raise RuntimeError('Native process ownership could not be verified')
            return
        if not self._owns_group or self._supervisor_token is None or self._supervisor_receipt is None:
            raise RuntimeError('Native process ownership could not be verified')
        receipt = read_receipt(self._supervisor_receipt, self._supervisor_token, self._proc.pid)
        if receipt is None or receipt.returncode is not None:
            raise RuntimeError('Native process ownership could not be verified')

    def exit_summary(self) -> str:
        code = self._proc.returncode if self._proc else None
        if self._proc is not None and self._supervisor_receipt is not None and self._supervisor_token is not None:
            receipt = read_receipt(self._supervisor_receipt, self._supervisor_token, self._proc.pid)
            if receipt is not None and receipt.returncode is not None:
                code = receipt.returncode
        return f"process '{self.spec.name}' exited (code {code}).\n{tail_log(self.spec.name)}"

    def start(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f"{self.spec.name}.log"
        self._log_file = open(log_path, "w", encoding="utf-8", errors="replace")
        creationflags = 0
        if sys.platform == 'win32':
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            command = self.spec.cmd
            if IS_WINDOWS:
                self._windows_job = WindowsJob.create()
                command = windows_command(self._windows_job.name, command)
            else:
                self._supervisor_token = uuid.uuid4().hex
                self._supervisor_receipt = LOG_DIR / f'.{self.spec.name}.native.{self._supervisor_token}.json'
                command = posix_command(self._supervisor_token, self._supervisor_receipt, command)
            self._proc = subprocess.Popen(
                command,
                cwd=str(self.spec.cwd),
                env=_build_env(self.spec),
                stdin=subprocess.PIPE,
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                start_new_session=not IS_WINDOWS,
            )
        except BaseException:
            if self._windows_job is not None:
                self._windows_job.close()
                self._windows_job = None
            self._close_log()
            raise
        self._owns_group = not IS_WINDOWS

    async def wait_healthy(self, cancel: Optional[asyncio.Event] = None) -> None:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self.spec.startup_timeout
        async with httpx.AsyncClient(timeout=3.0) as client:
            while True:
                if cancel is not None and cancel.is_set():
                    raise StartCancelled(f"start of '{self.spec.name}' was cancelled")
                if not self.is_running:
                    code = self._proc.returncode if self._proc else None
                    raise RuntimeError(
                        f"process '{self.spec.name}' exited during startup (code {code}).\n"
                        f"{tail_log(self.spec.name)}"
                    )
                if self.spec.health_url:
                    try:
                        resp = await client.get(self.spec.health_url)
                        if resp.status_code < 500:
                            return
                    except httpx.HTTPError:
                        pass
                else:
                    return
                if loop.time() > deadline:
                    raise TimeoutError(
                        f"process '{self.spec.name}' did not become healthy within "
                        f"{self.spec.startup_timeout:.0f}s.\n{tail_log(self.spec.name)}"
                    )
                if cancel is None:
                    await asyncio.sleep(1.0)
                else:
                    try:
                        await asyncio.wait_for(cancel.wait(), 1.0)
                    except asyncio.TimeoutError:
                        pass

    async def wait_stopped(self, timeout: float) -> bool:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while self.is_running or self._session_alive() or (self._windows_job is not None and self._windows_job.active_processes() > 0):
            if loop.time() > deadline:
                return False
            await asyncio.sleep(0.5)
        return True

    def _session_alive(self) -> bool:
        if sys.platform == 'win32' or not self._owns_group or self._proc is None:
            return False
        try:
            os.killpg(self._proc.pid, 0)
            return True
        except ProcessLookupError:
            return False

    async def stop(self) -> None:
        if self._windows_job is not None:
            # Closing the pipe also stops a supervisor whose startup races Stop.
            if self._proc is not None and self._proc.stdin is not None:
                self._proc.stdin.close()
            self._windows_job.terminate()
            if not await self.wait_stopped(10.0):
                raise RuntimeError(f"Process tree '{self.spec.name}' did not stop")
            self._windows_job.close()
            self._windows_job = None
            self._close_log()
            return
        if not self.is_running:
            if self._proc is not None and self._session_alive():
                if self._supervisor_token is not None and (self._supervisor_receipt is None or read_receipt(self._supervisor_receipt, self._supervisor_token, self._proc.pid) is None):
                    raise RuntimeError('Native session ownership could not be verified')
                await self._force_kill(self._proc.pid)
                if not await self.wait_stopped(10.0):
                    raise RuntimeError(f"Process tree '{self.spec.name}' did not stop")
            self._close_log()
            return
        if self._proc is None:
            raise RuntimeError('Native process ownership is missing')
        pid = self._proc.pid
        if sys.platform == 'win32':
            try:
                self._proc.send_signal(signal.CTRL_BREAK_EVENT)
            except (OSError, ValueError):
                pass
        else:
            if self._owns_group:
                try:
                    os.killpg(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                self._proc.terminate()
        stopped = await self.wait_stopped(self.spec.shutdown_timeout)
        if not stopped:
            await self._force_kill(pid)
            if not await self.wait_stopped(10.0):
                raise RuntimeError(f"Process '{self.spec.name}' did not stop")
        elif self._owns_group and self._supervisor_token is None:
            # A launcher may exit before a descendant which ignored SIGTERM.
            # Reap the owned session even when the parent stopped gracefully.
            await self._force_kill(pid)
        self._close_log()

    async def _force_kill(self, pid: int) -> None:
        if sys.platform == 'win32':
            proc = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        else:
            try:
                if self._owns_group:
                    os.killpg(pid, signal.SIGKILL)
                elif self._proc is not None:
                    self._proc.kill()
            except OSError:
                pass

    def _close_log(self) -> None:
        if self._proc is not None and self._proc.poll() is not None and self._proc.stdin is not None:
            self._proc.stdin.close()
        if self._supervisor_receipt is not None and not self.is_running and not self._session_alive():
            self._supervisor_receipt.unlink(missing_ok=True)
        if self._log_file:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None
