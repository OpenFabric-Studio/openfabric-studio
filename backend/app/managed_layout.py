"""Read-only defaults for the chosen writable installation home.

Call after dotenv is loaded. Explicit engine environment variables are resolved
by config.py ahead of these defaults, preserving existing external installations.
"""
from __future__ import annotations

import os
from pathlib import Path


def _default(group: str, name: str, fallback: str) -> str:
    root = os.environ.get('OPENFABRIC_MODULE_ROOT', '').strip()
    if root:
        return str(Path(root).expanduser() / group / name)
    if Path(fallback).exists():
        return fallback
    return str(Path.home() / '.openfabric-studio' / 'runtime' / group / name)


def engine_default(name: str, fallback: str) -> str:
    return _default('engines', name, fallback)


def tool_default(name: str, fallback: str) -> str:
    return _default('tools', name, fallback)
