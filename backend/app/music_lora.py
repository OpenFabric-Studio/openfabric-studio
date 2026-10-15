"""Immutable, validated native safetensors adapters in the user's library."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import struct
import time
from typing import Annotated, BinaryIO, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from . import db
from .atomic_files import document_lock, write_object
from .config import YUE2_LORA_AVAILABLE
from .contracts import JsonObject, JsonValue
from .music_lora_stable import validate_stable
from .music_lora_contracts import AdapterEngine, AdapterTarget, MusicAdapter, MusicAdapterSelection, MusicAdapters, MusicLoraCapabilities, MusicLoraCapability, MusicLoraError

logger = logging.getLogger(__name__)
MAX_ADAPTER_BYTES = 512 * 1024 * 1024
MAX_HEADER_BYTES = 4 * 1024 * 1024
_json: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


class _Tensor(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dtype: Literal['F16', 'BF16', 'F32']
    shape: list[Annotated[int, Field(strict=True, ge=1, le=1_000_000)]] = Field(min_length=1, max_length=4)
    data_offsets: list[Annotated[int, Field(strict=True, ge=0, le=MAX_ADAPTER_BYTES)]] = Field(min_length=2, max_length=2)


def _child(path: Path, parent: Path) -> Path:
    if path.is_symlink() or path.resolve().parent != parent.resolve():
        raise MusicLoraError('music_lora_storage_unavailable', 503)
    return path


def _root(*, create: bool = False) -> Path:
    root = _child(db.DATA_DIR / 'music-lora', db.DATA_DIR)
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def _directory(identifier: str) -> Path:
    if re.fullmatch(r'[0-9a-f]{32}', identifier) is None:
        raise MusicLoraError('music_lora_adapter_not_found', 404)
    return _child(_root() / identifier, _root())


def _target(engine: str, target: str) -> tuple[AdapterEngine, AdapterTarget]:
    if engine == 'stable_audio3' and target in ('small-music', 'medium'):
        return 'stable_audio3', 'small-music' if target == 'small-music' else 'medium'
    if engine == 'yue2' and target in ('ar', 'nar'):
        return 'yue2', 'ar' if target == 'ar' else 'nar'
    raise MusicLoraError('music_lora_target_mismatch', 422)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate header key')
        result[key] = value
    return result


def _object(encoded: bytes | str) -> JsonObject:
    decoded: object = json.loads(encoded, object_pairs_hook=_unique)
    value = _json.validate_python(decoded)
    if not isinstance(value, dict):
        raise ValueError('Expected object')
    return value


def _pairs(tensors: dict[str, _Tensor], prefix: str) -> None:
    a, b = tensors.get(prefix + 'lora_A'), tensors.get(prefix + 'lora_B')
    if a is None or b is None or len(a.shape) != 2 or len(b.shape) != 2 or a.shape[0] != b.shape[1] or a.shape[0] > 1024:
        raise ValueError('Invalid A/B pair')


def _validate_native(tensors: dict[str, _Tensor], metadata: JsonObject, engine: AdapterEngine, target: AdapterTarget) -> None:
    if engine == 'yue2':
        nar = 'nar_' if target == 'nar' else ''
        pattern = r'layers\.([0-9]+)\.' + nar + r'(self_attn\.[qkvo]_proj|mlp\.(gate|up|down)_proj)\.lora_[AB]'
        prefixes: set[str] = set()
        replacements = {'vae2llm.weight': [2048, 64], 'vae2llm.bias': [2048], 'llm2vae.weight': [64, 2048], 'llm2vae.bias': [64]}
        # The selected native model is YuE2-3B: 28 layers, hidden 2048, KV 1024, MLP 6144.
        dimensions = {'self_attn.q_proj': (2048, 2048), 'self_attn.k_proj': (2048, 1024),
            'self_attn.v_proj': (2048, 1024), 'self_attn.o_proj': (2048, 2048),
            'mlp.gate_proj': (2048, 6144), 'mlp.up_proj': (2048, 6144), 'mlp.down_proj': (6144, 2048)}
        for key in tensors:
            match = re.fullmatch(pattern, key)
            if match is not None and int(match.group(1)) < 28 and str(int(match.group(1))) == match.group(1):
                tensor = tensors[key]
                width_in, width_out = dimensions[match.group(2)]
                if len(tensor.shape) != 2 or (key.endswith('lora_A') and tensor.shape[1] != width_in) or (key.endswith('lora_B') and tensor.shape[0] != width_out):
                    raise ValueError('Wrong YuE2-3B projection shape')
                prefixes.add(key[:-6])
            elif target == 'nar' and key in {'vae2llm.weight', 'vae2llm.bias', 'llm2vae.weight', 'llm2vae.bias'}:
                if tensors[key].shape != replacements[key]:
                    raise ValueError('Invalid NAR projection')
            else:
                raise ValueError('Not a matching unfused YuE adapter')
        if not prefixes:
            raise ValueError('No A/B pairs')
        for prefix in prefixes:
            _pairs(tensors, prefix)
        return
    config = metadata.get('lora_config')
    if not isinstance(config, str):
        raise ValueError('Native Stable config required')
    cfg = _object(config)
    expected = 'sa3-sm-music' if target == 'small-music' else 'sa3-medium'
    if cfg.get('base_model') != expected:
        raise MusicLoraError('music_lora_target_mismatch', 422)
    rank, alpha = cfg.get('rank'), cfg.get('alpha')
    if not isinstance(rank, int) or isinstance(rank, bool) or not 1 <= rank <= 1024:
        raise ValueError('Invalid rank')
    if 'alpha' in cfg and (not isinstance(alpha, (int, float)) or isinstance(alpha, bool) or not math.isfinite(alpha) or not 0 < alpha <= 65536):
        raise ValueError('Invalid alpha')
    adapter_type = cfg.get('adapter_type', 'lora')
    if adapter_type == 'dora':
        adapter_type = 'dora-rows'
        cfg['adapter_type'] = adapter_type
    params_by_type: dict[str, set[str]] = {
        'lora': {'lora_A', 'lora_B'}, 'dora-rows': {'lora_A', 'lora_B', 'magnitude'},
        'dora-cols': {'lora_A', 'lora_B', 'magnitude'}, 'bora': {'lora_A', 'lora_B', 'magnitude_r', 'magnitude_c'},
        'lora-xs': {'M_xs'}, 'dora-rows-xs': {'M_xs', 'magnitude'},
        'dora-cols-xs': {'M_xs', 'magnitude'}, 'bora-xs': {'M_xs', 'magnitude_r', 'magnitude_c'},
    }
    if not isinstance(adapter_type, str) or adapter_type not in params_by_type:
        raise ValueError('Unsupported adapter type')
    groups: dict[str, set[str]] = {}
    marker = '.parametrizations.weight.0.'
    for key in tensors:
        if marker not in key:
            raise ValueError('Native tensor names required')
        layer, param = key.rsplit(marker, 1)
        if not layer or param not in params_by_type[adapter_type]:
            raise ValueError('Invalid native tensor name')
        groups.setdefault(layer + marker, set()).add(param)
    for prefix, params in groups.items():
        if params != params_by_type[adapter_type]:
            raise ValueError('Incomplete native layer')
        if 'lora_A' in params:
            _pairs(tensors, prefix)
            if tensors[prefix + 'lora_A'].shape[0] != rank:
                raise ValueError('Rank mismatch')
        elif tensors[prefix + 'M_xs'].shape != [rank, rank]:
            raise ValueError('Invalid XS matrix')
    validate_stable({name: tensor.shape for name, tensor in tensors.items()}, cfg, target)


def _validate(path: Path, engine: AdapterEngine, target: AdapterTarget) -> None:
    try:
        size = path.stat().st_size
        if not 8 < size <= MAX_ADAPTER_BYTES:
            raise ValueError('Adapter size')
        with path.open('rb') as handle:
            length = struct.unpack('<Q', handle.read(8))[0]
            if not 2 <= length <= MAX_HEADER_BYTES or 8 + length >= size:
                raise ValueError('Header size')
            header = _object(handle.read(length))
        metadata = header.pop('__metadata__', {})
        if not isinstance(metadata, dict) or any(not isinstance(value, str) for value in metadata.values()):
            raise ValueError('Invalid metadata')
        if not 1 <= len(header) <= 20000 or any(len(key) > 512 for key in header):
            raise ValueError('Invalid tensor count')
        tensors = {key: _Tensor.model_validate(value) for key, value in header.items()}
        offset = 0
        for tensor in sorted(tensors.values(), key=lambda item: item.data_offsets[0]):
            start, end = tensor.data_offsets
            if start != offset or end - start != math.prod(tensor.shape) * (4 if tensor.dtype == 'F32' else 2):
                raise ValueError('Invalid tensor offsets')
            offset = end
        if offset != size - 8 - length:
            raise ValueError('Truncated or extra data')
        _validate_native(tensors, metadata, engine, target)
    except (ValueError, ValidationError, struct.error, OverflowError, RecursionError) as error:
        raise MusicLoraError('music_lora_format_invalid', 422) from error


def _digest(path: Path) -> str:
    checksum = hashlib.sha256()
    count = 0
    with path.open('rb') as handle:
        while chunk := handle.read(65536):
            count += len(chunk)
            if count > MAX_ADAPTER_BYTES:
                raise MusicLoraError('music_lora_adapter_too_large', 413)
            checksum.update(chunk)
    return checksum.hexdigest()


def get(identifier: str) -> MusicAdapter:
    manifest = _child(_directory(identifier) / 'manifest.json', _directory(identifier))
    if not manifest.is_file():
        raise MusicLoraError('music_lora_adapter_not_found', 404)
    try:
        if manifest.stat().st_size > 8192:
            raise ValueError('Manifest size')
        adapter = MusicAdapter.model_validate_json(manifest.read_bytes())
        if adapter.id != identifier:
            raise ValueError('Manifest identity')
        return adapter
    except (ValidationError, ValueError) as error:
        raise MusicLoraError('music_lora_storage_unavailable', 503) from error


def list_adapters() -> MusicAdapters:
    root = _root()
    if not root.exists():
        return MusicAdapters(adapters=[])
    adapters: list[MusicAdapter] = []
    incomplete = False
    for directory in sorted(root.iterdir()):
        if re.fullmatch(r'[0-9a-f]{32}', directory.name) is None:
            continue
        if len(adapters) >= 1000:
            incomplete = True
            break
        try:
            adapters.append(get(directory.name))
        except (MusicLoraError, OSError):
            incomplete = True
            logger.warning('Unavailable adapter metadata retained')
    return MusicAdapters(adapters=sorted(adapters, key=lambda item: item.created_at, reverse=True), history_incomplete=incomplete)


def resolve(selection: MusicAdapterSelection, *, engine: str, target: str) -> Path:
    adapter = get(selection.id)
    if (adapter.engine, adapter.target) != _target(engine, target):
        raise MusicLoraError('music_lora_target_mismatch', 409)
    path = _child(_directory(adapter.id) / 'adapter.safetensors', _directory(adapter.id))
    if not path.is_file() or _digest(path) != adapter.sha256:
        raise MusicLoraError('music_lora_adapter_changed', 409)
    return path


def export_path(identifier: str) -> Path:
    adapter = get(identifier)
    return resolve(MusicAdapterSelection(id=identifier), engine=adapter.engine, target=adapter.target)


def find_by_job(identifier: str) -> MusicAdapter | None:
    root = _root()
    if not root.exists():
        return None
    for directory in root.iterdir():
        if re.fullmatch(r'[0-9a-f]{32}', directory.name) is None:
            continue
        try:
            adapter = get(directory.name)
        except (MusicLoraError, OSError):
            continue
        if adapter.source_job_id == identifier:
            return adapter
    return None


def import_stream(stream: BinaryIO, *, filename: str, name: str, engine: str, target: str, source_job_id: str | None = None) -> MusicAdapter:
    selected_engine, selected_target = _target(engine, target)
    if Path(filename).suffix.lower() != '.safetensors':
        raise MusicLoraError('music_lora_format_invalid', 422)
    root = _root(create=True)
    with document_lock(root / 'registry.lock'):
        adapter = MusicAdapter(id=uuid.uuid4().hex, engine=selected_engine, target=selected_target,
            name=name.strip(), sha256='0' * 64, created_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), source_job_id=source_job_id)
        directory = _directory(adapter.id)
        directory.mkdir(mode=0o700)
        try:
            path = _child(directory / 'adapter.safetensors', directory)
            count = 0
            with path.open('xb') as destination:
                while chunk := stream.read(65536):
                    count += len(chunk)
                    if count > MAX_ADAPTER_BYTES:
                        raise MusicLoraError('music_lora_adapter_too_large', 413)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            _validate(path, selected_engine, selected_target)
            adapter.sha256 = _digest(path)
            payload = _json.validate_python(adapter.model_dump(mode='json'))
            if not isinstance(payload, dict):
                raise ValueError('Manifest object')
            write_object(directory / 'manifest.json', payload)
            return adapter
        except BaseException:
            shutil.rmtree(directory)
            raise


def register_file(path: Path, *, name: str, engine: str, target: str, source_job_id: str | None = None) -> MusicAdapter:
    if path.is_symlink() or not path.is_file():
        raise MusicLoraError('music_lora_format_invalid', 422)
    with document_lock(_root(create=True) / 'registry.lock'):
        existing = find_by_job(source_job_id) if source_job_id is not None else None
        if existing is not None:
            resolve(MusicAdapterSelection(id=existing.id), engine=engine, target=target)
            if existing.sha256 != _digest(path):
                raise MusicLoraError('music_lora_adapter_changed', 409)
            return existing
        with path.open('rb') as stream:
            return import_stream(stream, filename=path.name, name=name, engine=engine, target=target, source_job_id=source_job_id)


def capabilities() -> MusicLoraCapabilities:
    return MusicLoraCapabilities(engines=[
        MusicLoraCapability(engine='ace_step', loading=True, training=True, reason='native'),
        MusicLoraCapability(engine='yue2', loading=YUE2_LORA_AVAILABLE, training=False, reason='inference_only' if YUE2_LORA_AVAILABLE else 'upgrade_required'),
        MusicLoraCapability(engine='heartmula', loading=False, training=False, reason='unsupported_runtime'),
        MusicLoraCapability(engine='stable_audio3', loading=True, training=True, reason='native')])
