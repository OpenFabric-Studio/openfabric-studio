"""An owned POSIX session dies when its backend pipe closes or child exits."""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class NativeReceipt:
    pid: int
    token: str
    returncode: int | None


def read_receipt(path: Path, token: str, pid: int) -> NativeReceipt | None:
    try:
        if path.stat().st_size > 4096:
            return None
        value: object = json.loads(path.read_bytes())
        if not isinstance(value, dict) or set(value) != {'pid', 'token', 'returncode'}:
            return None
        code: object = value['returncode']
        if type(value['pid']) is not int or value['pid'] != pid or value['token'] != token:
            return None
        if code is not None and (type(code) is not int or not -128 <= code <= 255):
            return None
        return NativeReceipt(pid, token, code if isinstance(code, int) else None)
    except (OSError, ValueError):
        return None


def write_receipt(path: Path, value: NativeReceipt) -> None:
    descriptor, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.partial', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(asdict(value), stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def supervisor_command(token: str, receipt: Path, command: list[str]) -> list[str]:
    if re.fullmatch(r'[0-9a-f]{32}', token) is None or not command or any('\0' in argument for argument in command):
        raise ValueError('Invalid native supervisor identity')
    return [sys.executable, '-u', str(Path(__file__).resolve()), '--token', token,
            '--receipt', str(receipt.resolve()), '--', *command]


def kill_own_session() -> None:
    if sys.platform == 'win32' or os.getpgrp() != os.getpid():
        raise RuntimeError('Native supervisor does not own its session')
    os.killpg(os.getpid(), signal.SIGKILL)


def _watch_parent() -> None:
    while os.read(sys.stdin.fileno(), 1):
        pass
    kill_own_session()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--token', required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    command: list[str] = arguments.command
    if command and command[0] == '--':
        command = command[1:]
    if sys.platform == 'win32' or os.getpgrp() != os.getpid():
        raise RuntimeError('Native supervisor does not own its session')
    supervisor_command(arguments.token, arguments.receipt, command)
    # Leave the supervisor alive while the group signal lets the native child
    # drain. It then kills remaining descendants before it exits itself.
    def graceful_stop(signum: int, frame: object) -> None:
        pass
    signal.signal(signal.SIGTERM, graceful_stop)
    signal.signal(signal.SIGINT, graceful_stop)
    write_receipt(arguments.receipt, NativeReceipt(os.getpid(), arguments.token, None))
    threading.Thread(target=_watch_parent, daemon=True, name='native-parent-liveness').start()
    code = 1
    try:
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL)
        code = child.wait()
    finally:
        try:
            write_receipt(arguments.receipt, NativeReceipt(os.getpid(), arguments.token, code))
        finally:
            kill_own_session()


if __name__ == '__main__':
    main()
