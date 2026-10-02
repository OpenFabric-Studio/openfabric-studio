"""A small Windows-only launcher assigned to an owned job before it spawns."""
from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

if not __package__:
    # Direct script invocation avoids depending on the engine's working dir.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.orchestrator.windows_job import WindowsJob


def supervisor_command(name: str, command: list[str]) -> list[str]:
    if not command or any('\0' in argument for argument in command):
        raise ValueError('Invalid native command')
    return [sys.executable, '-u', str(Path(__file__).resolve()), '--job', name, '--', *command]


def _watch_parent(stream: BinaryIO, job: WindowsJob) -> None:
    try:
        while stream.read(1):
            pass
    finally:
        # EOF means parent shutdown/crash, including when no signal is sent.
        job.terminate()


def launch_child(command: list[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(command, stdin=subprocess.DEVNULL)


def supervise(job: WindowsJob, command: list[str], parent: BinaryIO,
              launch: Callable[[list[str]], subprocess.Popen[bytes]] = launch_child) -> int:
    job.assign_current()
    child = launch(command)
    threading.Thread(target=_watch_parent, args=(parent, job), daemon=True,
                     name='native-parent-liveness').start()
    code = child.wait()
    # Includes descendants left behind by a launcher which exits gracefully.
    job.terminate(max(0, code) & 0xFFFFFFFF)
    return code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    command: list[str] = arguments.command
    if command and command[0] == '--':
        command = command[1:]
    if not command:
        parser.error('Native command is required')
    job = WindowsJob.open(arguments.job)
    def stop(signum: int, frame: object) -> None:
        job.terminate()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    if sys.platform == 'win32':
        signal.signal(signal.SIGBREAK, stop)
    try:
        code = supervise(job, command, sys.stdin.buffer)
    finally:
        job.close()
    raise SystemExit(code)


if __name__ == '__main__':
    main()
