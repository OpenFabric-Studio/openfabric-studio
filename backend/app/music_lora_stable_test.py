"""Native Stable adapters must target actual layers, never silently skip them."""
from __future__ import annotations

import unittest

from app.contracts import JsonObject
from app.music_lora_contracts import MusicLoraError
from app.music_lora_stable import validate_stable


class StableAdapterCompatibilityTests(unittest.TestCase):
    def validate(self, tensors: dict[str, list[int]], target: str = 'small-music',
                 adapter_type: str = 'lora', rank: int = 2) -> None:
        config: JsonObject = {'adapter_type': adapter_type, 'rank': rank,
            'base_model': 'sa3-sm-music' if target == 'small-music' else 'sa3-medium'}
        validate_stable(tensors, config, target)

    def pair(self, layer: str, width_in: int, width_out: int, rank: int = 2) -> dict[str, list[int]]:
        prefix = layer + '.parametrizations.weight.0.'
        return {prefix + 'lora_A': [rank, width_in], prefix + 'lora_B': [width_out, rank]}

    def test_unknown_and_unfused_self_attention_names_are_rejected(self) -> None:
        for layer in ('nonexistent', 'model.transformer.layers.0.self_attn.to_q',
                      'model.model.transformer.layers.0.self_attn.to_qkv',
                      'transformer.layers.00.self_attn.to_qkv',
                      'model.conditioners.seconds_total.embedder.embedding.1'):
            with self.subTest(layer=layer), self.assertRaises(MusicLoraError) as caught:
                self.validate(self.pair(layer, 1024, 3072))
            self.assertEqual(caught.exception.code, 'music_lora_format_invalid')

    def test_small_and_medium_attention_have_different_fused_shapes(self) -> None:
        self.validate(self.pair('model.transformer.layers.19.self_attn.to_qkv', 1024, 3072))
        self.validate(self.pair('transformer.layers.23.self_attn.to_qkv', 1536, 7680), 'medium')
        for target, layer, width_in, width_out in (
            ('small-music', 'transformer.layers.20.self_attn.to_qkv', 1024, 3072),
            ('medium', 'transformer.layers.24.self_attn.to_qkv', 1536, 7680),
            ('small-music', 'transformer.layers.0.self_attn.to_qkv', 1536, 7680),
            ('medium', 'transformer.layers.0.self_attn.to_qkv', 1024, 3072),
        ):
            with self.subTest(target=target, layer=layer), self.assertRaises(MusicLoraError) as caught:
                self.validate(self.pair(layer, width_in, width_out), target)
            self.assertEqual(caught.exception.code, 'music_lora_target_mismatch')

    def test_cross_attention_feedforward_and_seconds_conditioning_shapes(self) -> None:
        for target, width, inner, query, kv in (
            ('small-music', 1024, 4096, 1024, 2048), ('medium', 1536, 6144, 3072, 4608),
        ):
            for suffix, width_in, width_out in (
                ('cross_attn.to_q', width, query), ('cross_attn.to_kv', width, kv),
                ('cross_attn.to_out', width, width), ('ff.ff.0.proj', width, 2 * inner),
                ('ff.ff.2', inner, width), ('to_local_embed.0', 257, width),
                ('to_local_embed.seq.2', width, width),
            ):
                with self.subTest(target=target, suffix=suffix):
                    self.validate(self.pair('model.transformer.layers.0.' + suffix, width_in, width_out), target)
            self.validate(self.pair('conditioners.seconds_total.embedder.embedding.1', 256, 768), target)
        with self.assertRaises(MusicLoraError):
            self.validate(self.pair('conditioners.seconds_total.embedder.embedding.1', 768, 256))

    def test_top_level_embeddings_projectors_and_convs(self) -> None:
        for layer, width_in, width_out in (
            ('transformer.project_in', 256, 1024), ('transformer.project_out', 1024, 256),
            ('transformer.global_cond_embedder.0', 1024, 1024), ('transformer.global_cond_embedder.2', 1024, 6144),
            ('to_cond_embed.0', 768, 1024), ('to_cond_embed.2', 1024, 1024),
            ('to_global_embed.0', 768, 1024), ('to_global_embed.2', 1024, 1024),
            ('to_timestep_embed.0', 256, 1024), ('to_timestep_embed.2', 1024, 1024),
            ('preprocess_conv', 256, 256), ('postprocess_conv', 256, 256),
        ):
            with self.subTest(layer=layer):
                self.validate(self.pair(layer, width_in, width_out))

    def test_magnitude_and_xs_shapes_match_the_target_weight(self) -> None:
        layer = 'model.transformer.layers.0.self_attn.to_qkv'
        prefix = layer + '.parametrizations.weight.0.'
        self.validate({**self.pair(layer, 1024, 3072), prefix + 'magnitude': [3072]}, adapter_type='dora-rows')
        self.validate({**self.pair(layer, 1024, 3072), prefix + 'magnitude': [1, 1024]}, adapter_type='dora-cols')
        self.validate({**self.pair(layer, 1024, 3072), prefix + 'magnitude_r': [3072], prefix + 'magnitude_c': [1024]}, adapter_type='bora')
        self.validate({prefix + 'M_xs': [2, 2]}, adapter_type='lora-xs')
        for tensors, kind, rank in (
            ({**self.pair(layer, 1024, 3072), prefix + 'magnitude': [1024]}, 'dora-rows', 2),
            ({prefix + 'M_xs': [1025, 1025]}, 'lora-xs', 1025),
            ({**self.pair(layer, 1024, 3072), prefix + 'unknown': [2]}, 'lora', 2),
        ):
            with self.subTest(kind=kind), self.assertRaises(MusicLoraError):
                self.validate(tensors, adapter_type=kind, rank=rank)
