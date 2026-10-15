"""HeartMuLa torchtune interleaved Q/K rows versus Transformers split halves."""
from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def _shape(values: NDArray[np.float32], heads: int) -> tuple[int, int, int]:
    if (values.ndim != 2 or heads < 1 or values.shape[0] % (2 * heads) != 0
            or not all(values.shape) or not np.isfinite(values).all()):
        raise ValueError('heart_lora_invalid_qk_projection')
    return heads, values.shape[0] // (2 * heads), values.shape[1]


def original_to_hf_rows(values: NDArray[np.float32], heads: int) -> NDArray[np.float32]:
    """Permute each head from (real, imaginary) pairs into separate halves."""
    count, half, width = _shape(values, heads)
    return values.reshape(count, half, 2, width).transpose(0, 2, 1, 3).reshape(values.shape)


def hf_to_original_rows(values: NDArray[np.float32], heads: int) -> NDArray[np.float32]:
    """Apply the inverse to merged Q/K weights or LoRA B output rows."""
    count, half, width = _shape(values, heads)
    return values.reshape(count, 2, half, width).transpose(0, 2, 1, 3).reshape(values.shape)


def map_original_key(key: str) -> str:
    """Map the reviewed original HeartMuLa state dictionary into HF names."""
    for original, hf in (('.attn.q_proj.', '.self_attn.q_proj.'), ('.attn.k_proj.', '.self_attn.k_proj.'),
                         ('.attn.v_proj.', '.self_attn.v_proj.'), ('.attn.output_proj.', '.self_attn.o_proj.'),
                         ('.mlp.w1.', '.mlp.gate_proj.'), ('.mlp.w2.', '.mlp.down_proj.'), ('.mlp.w3.', '.mlp.up_proj.'),
                         ('.sa_norm.scale', '.input_layernorm.weight'), ('.mlp_norm.scale', '.post_attention_layernorm.weight'),
                         ('.norm.scale', '.norm.weight')):
        key = key.replace(original, hf)
    return key
