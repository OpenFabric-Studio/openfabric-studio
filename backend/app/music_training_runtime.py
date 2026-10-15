"""Dispatch reviewed per-model workers without importing their GPU dependencies."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct
import sys
from pydantic import TypeAdapter

from .contracts import JsonObject
from .music_lora_contracts import MusicLoraError
from .music_training_contracts import MusicLoraTrainingRequest, MusicLoraTrainingStatus, MusicTrainingEngine
from .video_projects import atomic_text


def fingerprint(request: MusicLoraTrainingRequest) -> str:
    return hashlib.sha256(request.model_dump_json(exclude_none=True).encode()).hexdigest()


def status(engine: MusicTrainingEngine) -> MusicLoraTrainingStatus:
    if engine == 'yue2':
        from .yue_lora_runtime import status as yue_status
        return yue_status()
    from .heart_lora_runtime import status as heart_status
    return heart_status()


def prepare_worker(request: MusicLoraTrainingRequest, directory: Path, sources: list[Path]) -> tuple[list[str], Path, dict[str, str]]:
    if request.engine == 'yue2':
        from .yue_lora_runtime import prepare_worker as yue_worker
        return yue_worker(request, directory, sources)
    from .heart_lora_runtime import prepare_worker as heart_worker
    return heart_worker(request, directory, sources)


def prepare_package_worker(request: MusicLoraTrainingRequest, directory: Path, sources: list[Path]) -> tuple[list[str], Path, dict[str, str]]:
    path = directory / 'request.json'
    if path.exists():
        raise MusicLoraError('music_training_existing_run', 409)
    atomic_text(path, json.dumps({'request': request.model_dump(mode='json', exclude_none=True),
        'sources': [str(source) for source in sources], 'request_sha256': fingerprint(request)}, ensure_ascii=False, allow_nan=False))
    worker = Path(__file__).resolve().parents[1] / 'scripts/music_training_package_worker.py'
    return [sys.executable, str(worker), '--request', str(path)], directory, dict(os.environ)


def artifact_base(path: Path, request: MusicLoraTrainingRequest) -> str:
    """Bind the worker completion to the adapter's independently validated base."""
    adapter = TypeAdapter(JsonObject)
    with path.open('rb') as source:
        length = struct.unpack('<Q', source.read(8))[0]
        if not 2 <= length <= 4 * 1024**2:
            raise MusicLoraError('music_training_completion_invalid', 422)
        header = adapter.validate_json(source.read(length))
    metadata = header.get('__metadata__')
    key = 'heart_lora_config' if request.engine == 'heartmula' else 'openfabric_yue2_conversion'
    if not isinstance(metadata, dict) or not isinstance(encoded := metadata.get(key), str):
        raise MusicLoraError('music_training_completion_invalid', 422)
    config = adapter.validate_json(encoded)
    if request.engine == 'heartmula' and config.get('rank') != request.rank:
        raise MusicLoraError('music_training_completion_invalid', 422)
    base = config.get('base_sha256')
    if not isinstance(base, str):
        raise MusicLoraError('music_training_completion_invalid', 422)
    return base
