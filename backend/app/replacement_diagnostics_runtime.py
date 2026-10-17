"""Read-only CPU dependency selection, using the caller's existing worker owner.

No engine import, model loading, cache placement, installation or download is
part of this capability probe. Preserve the venv interpreter path: resolving a
python symlink can silently select the base interpreter's package environment.
"""
from __future__ import annotations

from pathlib import Path
import sys

from pydantic import ValidationError

from .audio_quality import RunCommand
from .module_catalog import configured_environment, engine_python
from .module_evidence import environment_verified
from .music_analysis_contracts import MusicAnalysisContract

MAX_PROBE_BYTES = 1024
PROBE_SCRIPT = '''import importlib.metadata
import importlib.util
import json
import re
names = ("pydantic", "librosa", "numpy", "scipy", "soundfile")
available = False
try:
    present = all(importlib.util.find_spec(name) is not None for name in names)
    versions = {name: importlib.metadata.version(name) for name in names}
    pydantic = re.fullmatch(r"2\\.([0-9]+)\\.[0-9]+(?:\\.post[0-9]+)?(?:\\+[A-Za-z0-9.]+)?", versions["pydantic"])
    available = bool(present and pydantic is not None and int(pydantic.group(1)) >= 13
        and versions["librosa"] in ("0.10.2", "0.11.0"))
except (ImportError, OSError, ValueError, AttributeError):
    pass
print(json.dumps({"available": available}, separators=(",", ":")))
'''


class _PitchProbe(MusicAnalysisContract):
    available: bool


async def _compatible(python: Path, run: RunCommand) -> bool:
    try:
        if not python.is_file():
            return False
    except OSError:
        return False
    # An exception here may mean an unverified descendant. Only the caller's
    # owner can establish cleanup, so cancellation and cleanup errors propagate.
    output = await run([str(python), '-I', '-c', PROBE_SCRIPT], 10.0)
    if len(output) > MAX_PROBE_BYTES:
        return False
    try:
        return _PitchProbe.model_validate_json(output).available
    except (ValueError, ValidationError):
        return False


async def pitch_python(run: RunCommand) -> Path | None:
    """Prefer compatible backend, then verified managed Seed-VC/RVC, else None.

    None means no compatible installed runtime was established. It does not
    authorize setup, imports in the API process, or a model/cache fallback.
    """
    backend = Path(sys.executable)
    if await _compatible(backend, run):
        return backend
    environment = configured_environment()
    for identifier in ('singing', 'rvc'):
        python = engine_python(environment.paths[identifier], environment.platform)
        if python != backend and environment_verified(environment, identifier) and await _compatible(python, run):
            return python
    return None
