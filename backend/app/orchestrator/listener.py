"""OS listener identities for native readiness, never for process termination."""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

from ..job_lifecycle import await_cleanup, communicate_process, spawn_process


def _parse_pids(output: bytes, prefix: str = '') -> frozenset[int]:
    if len(output) > 65536:
        raise OSError('Native listener identity output exceeded its limit')
    try:
        lines = output.decode('ascii').splitlines()
    except UnicodeDecodeError as exc:
        raise OSError('Invalid native listener identity output') from exc
    pids: set[int] = set()
    for line in lines:
        # lsof always includes descriptor records, even with only -Fp selected.
        if prefix == 'p' and pids and re.fullmatch(r'f[0-9]+', line.strip()) is not None:
            continue
        if re.fullmatch(re.escape(prefix) + r'[0-9]+', line.strip()) is None:
            raise OSError('Invalid native listener identity output')
        pid = int(line.strip()[len(prefix):])
        if not 0 < pid <= 0xFFFFFFFF:
            raise OSError('Invalid native listener PID')
        pids.add(pid)
    return frozenset(pids)


def _linux_listener_pids(port: int, root: Path = Path('/proc')) -> frozenset[int]:
    inodes: set[str] = set()
    for name in ('tcp', 'tcp6'):
        table = root / 'net' / name
        if name == 'tcp6' and not table.exists():
            continue
        for line in table.read_text(encoding='ascii').splitlines()[1:]:
            fields = line.split()
            if len(fields) < 10:
                raise OSError('Invalid native socket table')
            if fields[3] == '0A' and int(fields[1].rsplit(':', 1)[1], 16) == port:
                inodes.add(fields[9])
    if not inodes:
        return frozenset()
    found: set[str] = set()
    pids: set[int] = set()
    for directory in root.iterdir():
        if not directory.name.isascii() or not directory.name.isdecimal():
            continue
        try:
            for descriptor in (directory / 'fd').iterdir():
                try:
                    target = os.readlink(descriptor)
                except OSError:
                    continue
                if target.startswith('socket:[') and target.endswith(']') and target[8:-1] in inodes:
                    found.add(target[8:-1])
                    pids.add(int(directory.name))
        except OSError:
            # A vanished/inaccessible process cannot prove ownership of a socket.
            continue
    if found != inodes:
        raise OSError('Native listener ownership could not be established')
    return frozenset(pids)


async def listener_pids(port: int) -> frozenset[int]:
    """Query one bounded local port; unavailable evidence fails closed."""
    if type(port) is not int or not 0 < port <= 65535:
        raise ValueError('Invalid native health port')
    command: list[str]
    prefix: str
    if sys.platform.startswith('linux'):
        return await await_cleanup(asyncio.to_thread(_linux_listener_pids, port))
    if sys.platform == 'darwin':
        command = ['/usr/sbin/lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN', '-Fp']
        prefix = 'p'
    elif sys.platform == 'win32':
        # Read-only in-process cmdlet; never start a background PowerShell job.
        script = ("$ErrorActionPreference='Stop'; "
                  f"Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue "
                  "| Select-Object -ExpandProperty OwningProcess")
        command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script]
        prefix = ''
    else:
        raise OSError('Native listener ownership is unsupported on this platform')
    # Cancellation waits for this bounded query, including a racing spawn.
    return await await_cleanup(_query_pids(command, prefix, sys.platform == 'darwin'))


async def _query_pids(command: list[str], prefix: str, allow_empty_exit: bool) -> frozenset[int]:
    process = await spawn_process(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    output, _ = await communicate_process(process, timeout=3)
    if process.returncode not in (0, 1) or (process.returncode == 1 and (output or not allow_empty_exit)):
        raise OSError('Native listener ownership query failed')
    return _parse_pids(output, prefix)
