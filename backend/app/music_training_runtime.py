"""Dispatch reviewed per-model workers without importing their GPU dependencies."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct
import sys
from typing import Literal
from pydantic import TypeAdapter

from .contracts import JsonObject
from .music_lora_contracts import MusicLoraError
from .music_training_contracts import MusicLoraTrainingRequest, MusicLoraTrainingStatus, MusicTrainingEngine
from .video_projects import atomic_text
from .yue_lora_runtime import PreparedAudio


def fingerprint(request: MusicLoraTrainingRequest) -> str:
    return hashlib.sha256(request.model_dump_json(exclude_none=True).encode()).hexdigest()


def status(engine: MusicTrainingEngine) -> MusicLoraTrainingStatus:
    from .yue_lora_runtime import status as yue_status
    return yue_status()


def prepare_worker(request: MusicLoraTrainingRequest, directory: Path, sources: list[Path], *,
                   prepared_audio: tuple[PreparedAudio, ...] | None = None) -> tuple[list[str], Path, dict[str, str]]:
    from .yue_lora_runtime import prepare_worker as yue_worker
    return yue_worker(request, directory, sources, prepared_audio=prepared_audio)


def prepare_package_worker(request: MusicLoraTrainingRequest, directory: Path, sources: list[Path]) -> tuple[list[str], Path, dict[str, str]]:
    path = directory / 'request.json'
    if path.exists():
        raise MusicLoraError('music_training_existing_run', 409)
    atomic_text(path, json.dumps({'request': request.model_dump(mode='json', exclude_none=True),
        'sources': [str(source) for source in sources], 'request_sha256': fingerprint(request)}, ensure_ascii=False, allow_nan=False))
    worker = Path(__file__).resolve().parents[1] / 'scripts/music_training_package_worker.py'
    return [sys.executable, str(worker), '--request', str(path)], directory, dict(os.environ)


def artifact_base(path: Path, request: MusicLoraTrainingRequest, *, target: Literal['ar', 'nar'] | None = None) -> str:
    """Bind the worker completion to the adapter's independently validated base."""
    adapter = TypeAdapter(JsonObject)
    with path.open('rb') as source:
        prefix = source.read(8)
        if len(prefix) != 8:
            raise MusicLoraError('music_training_completion_invalid', 422)
        length = struct.unpack('<Q', prefix)[0]
        if not 2 <= length <= 4 * 1024**2:
            raise MusicLoraError('music_training_completion_invalid', 422)
        header = adapter.validate_json(source.read(length))
    metadata = header.get('__metadata__')
    if not isinstance(metadata, dict) or not isinstance(encoded := metadata.get('openfabric_yue2_conversion'), str):
        raise MusicLoraError('music_training_completion_invalid', 422)
    config = adapter.validate_json(encoded)
    base = config.get('base_sha256')
    if not isinstance(base, str):
        raise MusicLoraError('music_training_completion_invalid', 422)
    if target is not None:
        from .yue_lora_convert import BASE_SHA256
        if config.get('stage') != target or base != BASE_SHA256:
            raise MusicLoraError('music_training_completion_invalid', 422)
    return base
