"""Match native adapters to the pinned Stable Audio 3 MLX model layers."""
from __future__ import annotations

import re

from .contracts import JsonObject
from .music_lora_contracts import MusicLoraError


def validate_stable(tensors: dict[str, list[int]], config: JsonObject, target: str) -> None:
    """Reject layers the native loader would silently skip and wrong base shapes."""
    if target not in ('small-music', 'medium') or config.get('base_model') != ('sa3-sm-music' if target == 'small-music' else 'sa3-medium'):
        raise MusicLoraError('music_lora_target_mismatch', 422)
    rank, kind = config.get('rank'), config.get('adapter_type', 'lora')
    if not tensors or not isinstance(rank, int) or isinstance(rank, bool) or not 1 <= rank <= 1024 or kind not in (
        'lora', 'dora-rows', 'dora-cols', 'bora', 'lora-xs', 'dora-rows-xs', 'dora-cols-xs', 'bora-xs',
    ) or not isinstance(kind, str):
        raise MusicLoraError('music_lora_format_invalid', 422)
    # Pinned 77e0398c: models/defs/dit_mlx.py, dit_mlx_medium.py and sa3_pipeline.py.
    width, depth, inner = (1024, 20, 4096) if target == 'small-music' else (1536, 24, 6144)
    qkv, query, kv = (3, 1, 2) if target == 'small-music' else (5, 2, 3)
    top = {
        'transformer.project_in': (width, 256), 'transformer.project_out': (256, width),
        'transformer.global_cond_embedder.0': (width, width), 'transformer.global_cond_embedder.2': (6 * width, width),
        'to_cond_embed.0': (width, 768), 'to_cond_embed.2': (width, width),
        'to_global_embed.0': (width, 768), 'to_global_embed.2': (width, width),
        'to_timestep_embed.0': (width, 256), 'to_timestep_embed.2': (width, width),
        'preprocess_conv': (256, 256), 'postprocess_conv': (256, 256),
    }
    blocks = {
        'self_attn.to_qkv': (qkv * width, width), 'self_attn.to_out': (width, width),
        'cross_attn.to_q': (query * width, width), 'cross_attn.to_kv': (kv * width, width),
        'cross_attn.to_out': (width, width), 'ff.ff.0.proj': (2 * inner, width), 'ff.ff.2': (width, inner),
        'to_local_embed.0': (width, 257), 'to_local_embed.seq.0': (width, 257),
        'to_local_embed.2': (width, width), 'to_local_embed.seq.2': (width, width),
    }
    for name, shape in tensors.items():
        dimensions: tuple[int, int] | None
        layer, marker, parameter = name.rpartition('.parametrizations.weight.0.')
        if not marker:
            raise MusicLoraError('music_lora_format_invalid', 422)
        if layer == 'conditioners.seconds_total.embedder.embedding.1':
            dimensions = (768, 256)
        else:
            layer = layer.removeprefix('model.')
            match = re.fullmatch(r'transformer\.layers\.(0|[1-9][0-9]*)\.(.+)', layer)
            if match is not None:
                if int(match.group(1)) >= depth:
                    raise MusicLoraError('music_lora_target_mismatch', 422)
                dimensions = blocks.get(match.group(2))
            else:
                dimensions = top.get(layer)
            if dimensions is None:
                raise MusicLoraError('music_lora_format_invalid', 422)
        width_out, width_in = dimensions
        expected = {'M_xs': [rank, rank]} if kind.endswith('-xs') else {'lora_A': [rank, width_in], 'lora_B': [width_out, rank]}
        if kind.endswith('-xs') and rank > min(width_in, width_out):
            raise MusicLoraError('music_lora_target_mismatch', 422)
        if kind.startswith('dora-'):
            expected['magnitude'] = [width_out if kind.startswith('dora-rows') else width_in]
        elif kind in ('bora', 'bora-xs'):
            expected.update({'magnitude_r': [width_out], 'magnitude_c': [width_in]})
        required = expected.get(parameter)
        if required is None:
            raise MusicLoraError('music_lora_format_invalid', 422)
        vector = parameter in ('magnitude', 'magnitude_r', 'magnitude_c')
        if shape != required and not (vector and shape in ([required[0], 1], [1, required[0]])):
            raise MusicLoraError('music_lora_target_mismatch', 422)
