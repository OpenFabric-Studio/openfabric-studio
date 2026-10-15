"""Numerical regressions for the original/Transformers HeartMuLa boundary."""
from __future__ import annotations

import unittest

import numpy as np

from app.heart_lora_conversion import original_to_hf_rows, hf_to_original_rows, map_original_key


class HeartLoraConversionTests(unittest.TestCase):
    def test_qk_permutation_preserves_interleaved_rotary_attention(self) -> None:
        rng = np.random.default_rng(42)
        weights = rng.normal(size=(16, 5)).astype(np.float32)
        inputs = rng.normal(size=(3, 5)).astype(np.float32)
        original = (inputs @ weights.T).reshape(3, 2, 8)
        converted = (inputs @ original_to_hf_rows(weights, 2).T).reshape(3, 2, 8)
        frequencies = np.array([1, .1, .01, .001], dtype=np.float32)
        angles = np.arange(3, dtype=np.float32)[:, None] * frequencies
        pairs = original.reshape(3, 2, 4, 2)
        expected = np.stack((pairs[..., 0] * np.cos(angles)[:, None] - pairs[..., 1] * np.sin(angles)[:, None],
                             pairs[..., 1] * np.cos(angles)[:, None] + pairs[..., 0] * np.sin(angles)[:, None]), axis=-1)
        rotated = converted * np.tile(np.cos(angles), (1, 2))[:, None] + np.concatenate((-converted[..., 4:], converted[..., :4]), axis=-1) * np.tile(np.sin(angles), (1, 2))[:, None]
        np.testing.assert_allclose(rotated, expected.transpose(0, 1, 3, 2).reshape(3, 2, 8), atol=1e-6)
        np.testing.assert_array_equal(hf_to_original_rows(original_to_hf_rows(weights, 2), 2), weights)

    def test_only_b_factor_needs_row_conversion(self) -> None:
        rng = np.random.default_rng(13)
        a = rng.normal(size=(2, 5)).astype(np.float32)
        b = rng.normal(size=(16, 2)).astype(np.float32)
        np.testing.assert_allclose(hf_to_original_rows(b, 2) @ a, hf_to_original_rows(b @ a, 2), atol=1e-6)

    def test_rejects_invalid_dimensions_and_nonfinite_weights(self) -> None:
        for weights, heads in ((np.zeros((7, 2), dtype=np.float32), 2), (np.zeros((8, 2), dtype=np.float32), 0),
                               (np.full((8, 2), np.nan, dtype=np.float32), 2)):
            with self.subTest(heads=heads, shape=weights.shape), self.assertRaises(ValueError):
                original_to_hf_rows(weights, heads)

    def test_maps_all_supported_original_projection_and_norm_names(self) -> None:
        expected = {'attn.q_proj.weight': 'self_attn.q_proj.weight', 'attn.k_proj.weight': 'self_attn.k_proj.weight',
                    'attn.v_proj.weight': 'self_attn.v_proj.weight', 'attn.output_proj.weight': 'self_attn.o_proj.weight',
                    'mlp.w1.weight': 'mlp.gate_proj.weight', 'mlp.w2.weight': 'mlp.down_proj.weight', 'mlp.w3.weight': 'mlp.up_proj.weight',
                    'sa_norm.scale': 'input_layernorm.weight', 'mlp_norm.scale': 'post_attention_layernorm.weight'}
        for source, target in expected.items():
            self.assertEqual(map_original_key('decoder.layers.2.' + source), 'decoder.layers.2.' + target)
        self.assertEqual(map_original_key('backbone.norm.scale'), 'backbone.norm.weight')
        self.assertEqual(map_original_key('audio_head'), 'audio_head')


if __name__ == '__main__':
    unittest.main()
