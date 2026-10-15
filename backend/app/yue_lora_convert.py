"""Strict CPU conversion of standard YuE2 NAR LoRA factors from the pinned trainer.

The caller must hash the actual training base; trainer metadata has no base identity.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import tempfile
from typing import Annotated, Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, StrictBool, TypeAdapter

from .contracts import JsonObject, JsonValue

BASE_REPOSITORY = 'Comfy-Org/YuE2'
BASE_REVISION = '2f76ca75e6ee094169de899cc7fc99d6887e2196'
BASE_SHA256 = '33765adbf9813c9a50318218760b2fd819a319862460a04884607581961c6fee'
TRAINER_REVISION = '6a9355efff60589d93b41b569676f09d5db85d09'
COMFY_REVISION = 'b058ec652802e1de24f1d429b3c7c1fb868f791c'
NATIVE_REVISION = 'f2b4937306daa25f5c78520f3c626ed31495a37a'
MAX_BYTES = 512 * 1024**2
MAX_HEADER_BYTES = 4 * 1024**2
_json: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_KEY = re.compile(r'diffusion_model\.model\.layers\.(0|[1-9][0-9]*)\.(self_attn\.(?:[qkvo]_proj|qkv_proj)|mlp\.(?:gate|up|down|gate_up)_proj)\.(lora_down\.weight|lora_up\.weight|alpha)')
_DIMENSIONS = {'self_attn.q_proj': (2048, (('self_attn.q_proj', 2048),)),
    'self_attn.k_proj': (2048, (('self_attn.k_proj', 1024),)),
    'self_attn.v_proj': (2048, (('self_attn.v_proj', 1024),)),
    'self_attn.o_proj': (2048, (('self_attn.o_proj', 2048),)),
    'self_attn.qkv_proj': (2048, (('self_attn.q_proj', 2048), ('self_attn.k_proj', 1024), ('self_attn.v_proj', 1024))),
    'mlp.gate_proj': (2048, (('mlp.gate_proj', 6144),)),
    'mlp.up_proj': (2048, (('mlp.up_proj', 6144),)),
    'mlp.down_proj': (6144, (('mlp.down_proj', 2048),)),
    'mlp.gate_up_proj': (2048, (('mlp.gate_proj', 6144), ('mlp.up_proj', 6144)))}


class _Tensor(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dtype: Literal['F32', 'F16', 'BF16']
    shape: list[Annotated[int, Field(strict=True, ge=1, le=1_000_000)]] = Field(max_length=2)
    data_offsets: tuple[Annotated[int, Field(strict=True, ge=0, le=MAX_BYTES)], Annotated[int, Field(strict=True, ge=0, le=MAX_BYTES)]]


class _TrainerInfo(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    kind: Literal['acoustic']
    rank: Annotated[int, Field(strict=True, ge=1, le=1024)]
    alpha: Annotated[float, Field(strict=True, gt=0, le=65536)]
    targets: Literal['attention', 'attention+mlp', 'mlp']
    train_acoustic_head: StrictBool
    steps: Annotated[int, Field(strict=True, ge=0, le=100000)] | None = None
    final_loss: Annotated[float, Field(strict=True)] | None = None


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError('Duplicate JSON key')
        output[key] = value
    return output


def _object(encoded: bytes | str) -> JsonObject:
    value: object = json.loads(encoded, object_pairs_hook=_unique)
    checked = _json.validate_python(value)
    if not isinstance(checked, dict):
        raise ValueError('Expected JSON object')
    return checked


def _load(data: bytes) -> tuple[dict[str, NDArray[np.float32]], _TrainerInfo]:
    if not 8 < len(data) <= MAX_BYTES:
        raise ValueError('Invalid checkpoint size')
    length = struct.unpack('<Q', data[:8])[0]
    if not 2 <= length <= MAX_HEADER_BYTES or 8 + length >= len(data):
        raise ValueError('Invalid checkpoint header')
    header = _object(data[8:8 + length])
    metadata = header.pop('__metadata__', None)
    if not isinstance(metadata, dict) or any(not isinstance(value, str) for value in metadata.values()):
        raise ValueError('Trainer metadata required')
    encoded_info = metadata.get('yue2_trainer')
    if not isinstance(encoded_info, str):
        raise ValueError('Trainer metadata required')
    info = _TrainerInfo.model_validate(_object(encoded_info))
    if info.train_acoustic_head:
        raise ValueError('Acoustic head training is unsupported')
    if not 1 <= len(header) <= 28 * 9 * 3 or any(len(name) > 256 for name in header):
        raise ValueError('Invalid tensor count')
    specs = {name: _Tensor.model_validate(spec) for name, spec in header.items()}
    payload = memoryview(data)[8 + length:]
    offset = 0
    tensors: dict[str, NDArray[np.float32]] = {}
    for name, spec in sorted(specs.items(), key=lambda pair: pair[1].data_offsets[0]):
        begin, end = spec.data_offsets
        if begin != offset or end - begin != math.prod(spec.shape) * (4 if spec.dtype == 'F32' else 2) or end > len(payload):
            raise ValueError('Invalid tensor offsets')
        raw = payload[begin:end]
        if spec.dtype == 'BF16':
            words = np.frombuffer(raw, dtype='<u2').astype('<u4') << np.uint32(16)
            tensor = words.view('<f4').astype(np.float32).reshape(spec.shape)
        else:
            tensor = np.frombuffer(raw, dtype='<f4' if spec.dtype == 'F32' else '<f2').astype(np.float32).reshape(spec.shape)
        if not np.isfinite(tensor).all():
            raise ValueError('Non-finite tensor')
        tensors[name] = tensor
        offset = end
    if offset != len(payload):
        raise ValueError('Truncated or trailing tensor data')
    return tensors, info


def _convert(tensors: dict[str, NDArray[np.float32]], info: _TrainerInfo) -> dict[str, NDArray[np.float32]]:
    groups: dict[tuple[int, str], dict[str, NDArray[np.float32]]] = {}
    for name, tensor in tensors.items():
        match = _KEY.fullmatch(name)
        if match is None or int(match[1]) >= 28:
            raise ValueError('Unsupported NAR trainer tensor')
        layer, projection, factor = int(match[1]), match[2], match[3]
        if (info.targets == 'attention' and not projection.startswith('self_attn.')) or (info.targets == 'mlp' and not projection.startswith('mlp.')):
            raise ValueError('Tensor does not match training targets')
        groups.setdefault((layer, projection), {})[factor] = tensor
    output: dict[str, NDArray[np.float32]] = {}
    for (layer, projection), factors in groups.items():
        if set(factors) != {'lora_down.weight', 'lora_up.weight', 'alpha'}:
            raise ValueError('Missing LoRA factor or alpha')
        down, up, alpha = factors['lora_down.weight'], factors['lora_up.weight'], factors['alpha']
        width_in, splits = _DIMENSIONS[projection]
        if down.shape != (info.rank, width_in) or up.shape != (sum(width for _, width in splits), info.rank):
            raise ValueError('Wrong YuE2-3B projection shape')
        if alpha.shape != () or float(alpha.item()) != info.alpha:
            raise ValueError('LoRA alpha mismatch')
        offset = 0
        for target, width in splits:
            prefix = f'layers.{layer}.nar_{target}.'
            if prefix + 'lora_A' in output:
                raise ValueError('Overlapping fused and unfused projections')
            output[prefix + 'lora_A'] = down
            # The native loader applies B@A without alpha/rank.
            scaled = up[offset:offset + width] * np.float32(info.alpha / info.rank)
            if not np.isfinite(scaled).all():
                raise ValueError('Scaled LoRA factor overflow')
            output[prefix + 'lora_B'] = scaled
            offset += width
    return output


def validate_training_checkpoint(path: Path, *, max_steps: int, rank: int) -> None:
    """Require a completed run with finite loss and changed initially-zero factors."""
    if path.is_symlink() or not 8 < path.stat().st_size <= MAX_BYTES:
        raise ValueError('Invalid training checkpoint')
    with path.open('rb') as handle:
        tensors, info = _load(handle.read(MAX_BYTES + 1))
    if info.steps != max_steps or info.rank != rank or info.final_loss is None:
        raise ValueError('Incomplete training checkpoint')
    if not any(np.any(tensor != 0) for name, tensor in tensors.items() if name.endswith('.lora_down.weight')):
        raise ValueError('Training did not change the initially-zero LoRA factors')


def convert_nar(source: Path, destination: Path, *, base_sha256: str) -> None:
    """Convert a verified run's checkpoint, refusing to replace any destination."""
    if base_sha256 != BASE_SHA256:
        raise ValueError('Training base identity does not match the pinned YuE2 BF16 base')
    if source.suffix != '.safetensors' or source.is_symlink() or destination.suffix != '.safetensors':
        raise ValueError('Expected safetensors checkpoint paths')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    with source.open('rb') as handle:
        if not 8 < os.fstat(handle.fileno()).st_size <= MAX_BYTES:
            raise ValueError('Invalid checkpoint size')
        data = handle.read(MAX_BYTES + 1)
    tensors, info = _load(data)
    converted = _convert(tensors, info)
    provenance = {'base_repository': BASE_REPOSITORY, 'base_revision': BASE_REVISION, 'base_sha256': base_sha256,
        'trainer_revision': TRAINER_REVISION, 'comfy_revision': COMFY_REVISION, 'native_revision': NATIVE_REVISION,
        'source_sha256': hashlib.sha256(data).hexdigest(), 'stage': 'nar', 'alpha_folded_into_B': True}
    header: JsonObject = {'__metadata__': {'openfabric_yue2_conversion': json.dumps(provenance, sort_keys=True)}}
    chunks: list[bytes] = []
    offset = 0
    for name, tensor in sorted(converted.items()):
        chunk = tensor.astype('<f4').tobytes()
        header[name] = {'dtype': 'F32', 'shape': [int(width) for width in tensor.shape], 'data_offsets': [offset, offset + len(chunk)]}
        chunks.append(chunk)
        offset += len(chunk)
    encoded = json.dumps(header, separators=(',', ':')).encode()
    if len(encoded) > MAX_HEADER_BYTES or 8 + len(encoded) + offset > MAX_BYTES:
        raise ValueError('Converted adapter exceeds native size limit')
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.yue-lora-', dir=destination.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(struct.pack('<Q', len(encoded)))
            handle.write(encoded)
            for chunk in chunks:
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
