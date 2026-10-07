"""Build a small, allowlisted report; never read logs, media, or credentials."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
from collections.abc import Sequence

from .module_contracts import ModuleInventory
from .support_contracts import EngineRuntime, SupportAction, SupportEngineState, SupportModuleState, SupportReport


def _app_version() -> str | None:
    path = Path(__file__).resolve().parents[2] / 'desktop/package.json'
    try:
        if path.stat().st_size > 65536:
            return None
        value: object = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            return None
        version = value.get('version')
        if isinstance(version, str) and len(version) <= 50 and re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?', version):
            return version
    except (OSError, ValueError):
        pass
    return None


def _physical_memory() -> int | None:
    if sys.platform == 'win32':
        return None
    if sys.platform not in ('darwin', 'linux'):
        return None
    try:
        pages, size = os.sysconf('SC_PHYS_PAGES'), os.sysconf('SC_PAGE_SIZE')
        value = pages * size
        return value if 0 < value <= 17_592_186_044_416 else None
    except (OSError, ValueError):
        return None


def build_report(inventory: ModuleInventory, engines: Sequence[EngineRuntime], actions: Sequence[SupportAction]) -> SupportReport:
    architecture = inventory.architecture.lower()
    return SupportReport(source='backend', app_version=_app_version(),
        python_version=f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}',
        platform='darwin' if inventory.platform == 'darwin' else 'win32' if inventory.platform == 'win32' else 'linux' if inventory.platform == 'linux' else 'unknown',
        architecture='arm64' if architecture in ('arm64', 'aarch64') else 'x64' if architecture in ('x64', 'x86_64', 'amd64') else 'x86' if architecture in ('x86', 'i386', 'i686') else 'unknown',
        acceleration=inventory.acceleration, physical_memory_bytes=_physical_memory(), backend_connection='reachable',
        modules=[SupportModuleState(id=module.id, state=module.state, supported=module.supported,
            managed=module.managed, restart_required=module.restart_required) for module in inventory.modules],
        engines=[SupportEngineState(id=engine.id, state=engine.state, owned=engine.owned, idle_state=engine.idle_state) for engine in engines],
        recent_actions=list(actions[-20:]))
