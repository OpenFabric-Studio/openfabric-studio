"""The initial HeartMuLa artifact is standard PEFT LoRA in corrected HF layout."""
from __future__ import annotations

import re
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import JsonObject

HeartProjection = Literal['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj']
HEART_BASE_REVISION: Final = 'd12ac79c6b3387d5c9eee456323495d8f08bb09d'
HEART_TRAINER_REVISION: Final = 'a0e7ffadfcd0d3d0f68b400f9697c464da6f9408'
HEART_CODEC_SOURCE_REVISION: Final = '5858ca8f1ffe58d62be7c007ea55f1033c5ca456'
HEART_BASE_SHA256: Final = '852e5728588705c4722f3a562265626c1324632060eb4cfb541bbecb22514509'
HEART_TOKENIZER_SHA256: Final = '79e3e522635f3171300913bb421464a87de6222182a0570b9b2ccba2a964b2b4'


class HeartLoraMetadata(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    schema_version: Literal[1] = 1
    format: Literal['peft_lora'] = 'peft_lora'
    base_repository: Literal['HeartMuLa/HeartMuLa-oss-3B'] = 'HeartMuLa/HeartMuLa-oss-3B'
    base_revision: Literal['d12ac79c6b3387d5c9eee456323495d8f08bb09d'] = HEART_BASE_REVISION
    trainer_revision: Literal['a0e7ffadfcd0d3d0f68b400f9697c464da6f9408'] = HEART_TRAINER_REVISION
    encoder_revision: Literal['e6020510612d6653b73d6cd54393ef80929e3c81'] = 'e6020510612d6653b73d6cd54393ef80929e3c81'
    decoder_revision: Literal['f889dab0532cfa4bf459f2a3367eb6d346b8eeda'] = 'f889dab0532cfa4bf459f2a3367eb6d346b8eeda'
    qk_layout: Literal['hf_split_half'] = 'hf_split_half'
    rope_original_context: Literal[8192] = 8192
    rank: int = Field(ge=1, le=32)
    alpha: float = Field(gt=0, le=1024)
    target_modules: list[HeartProjection] = Field(min_length=1, max_length=7)
    tokenizer_sha256: Literal['79e3e522635f3171300913bb421464a87de6222182a0570b9b2ccba2a964b2b4'] = HEART_TOKENIZER_SHA256
    base_sha256: Literal['852e5728588705c4722f3a562265626c1324632060eb4cfb541bbecb22514509'] = HEART_BASE_SHA256

    @model_validator(mode='after')
    def unique_targets(self) -> Self:
        if len(set(self.target_modules)) != len(self.target_modules):
            raise ValueError('heart_lora_duplicate_targets')
        return self


def validate_heart_adapter(tensors: dict[str, list[int]], metadata: JsonObject) -> HeartLoraMetadata:
    """Reject ignored modules, incomplete factors, incompatible bases and dimensions."""
    encoded = metadata.get('heart_lora_config')
    if not isinstance(encoded, str) or len(encoded) > 8192:
        raise ValueError('heart_lora_invalid_metadata')
    config = HeartLoraMetadata.model_validate_json(encoded)
    if not tensors or len(tensors) > 31 * 7 * 2:
        raise ValueError('heart_lora_invalid_tensor_count')
    pattern = r'base_model\.model\.(backbone|decoder)\.layers\.(0|[1-9][0-9]*)\.(self_attn\.[qkvo]_proj|mlp\.(?:gate|up|down)_proj)\.lora_([AB])\.weight'
    groups: dict[str, set[str]] = {}
    actual_targets: set[str] = set()
    for key, shape in tensors.items():
        match = re.fullmatch(pattern, key)
        if match is None:
            raise ValueError('heart_lora_unsupported_tensor')
        stack, layer, projection, factor = match.groups()
        if int(layer) >= (28 if stack == 'backbone' else 3):
            raise ValueError('heart_lora_invalid_layer')
        name = projection.rsplit('.', 1)[1]
        if name not in config.target_modules:
            raise ValueError('heart_lora_unconfigured_projection')
        kv_width = 1024 if stack == 'backbone' else 1536
        width_out = kv_width if name in ('k_proj', 'v_proj') else 8192 if name in ('gate_proj', 'up_proj') else 3072
        width_in = 8192 if name == 'down_proj' else 3072
        if shape != ([config.rank, width_in] if factor == 'A' else [width_out, config.rank]):
            raise ValueError('heart_lora_invalid_shape')
        groups.setdefault(key.rsplit('.lora_', 1)[0], set()).add(factor)
        actual_targets.add(name)
    required_groups = {f'base_model.model.{stack}.layers.{layer}.{"mlp" if name in ("gate_proj", "up_proj", "down_proj") else "self_attn"}.{name}'
        for stack, depth in (('backbone', 28), ('decoder', 3)) for layer in range(depth) for name in config.target_modules}
    if (actual_targets != set(config.target_modules) or set(groups) != required_groups
            or any(factors != {'A', 'B'} for factors in groups.values())):
        raise ValueError('heart_lora_incomplete_factors')
    return config
