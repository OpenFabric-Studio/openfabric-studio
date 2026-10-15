from __future__ import annotations

import json
import unittest

from app.heart_lora_contracts import HeartLoraMetadata, validate_heart_adapter


class HeartLoraContractsTests(unittest.TestCase):
    def metadata(self) -> HeartLoraMetadata:
        return HeartLoraMetadata(rank=8, alpha=8, target_modules=['q_proj'])

    def test_validates_plain_peft_shapes_and_exact_base(self) -> None:
        meta = self.metadata()
        tensors = {f'base_model.model.{stack}.layers.{layer}.self_attn.q_proj.lora_{factor}.weight': shape
                   for stack, depth in (('backbone', 28), ('decoder', 3)) for layer in range(depth)
                   for factor, shape in (('A', [8, 3072]), ('B', [3072, 8]))}
        self.assertEqual(validate_heart_adapter(tensors, {'heart_lora_config': meta.model_dump_json()}), meta)
        for key, shape in [('base_model.model.backbone.layers.28.self_attn.q_proj.lora_A.weight', [8, 3072]),
                           ('base_model.model.backbone.layers.0.self_attn.k_proj.lora_A.weight', [8, 3072]),
                           ('base_model.model.backbone.layers.0.self_attn.q_proj.lora_B.weight', [1024, 8])]:
            bad = dict(tensors);bad[key] = shape
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_heart_adapter(bad, {'heart_lora_config': meta.model_dump_json()})

    def test_rejects_sparse_layer_coverage_that_peft_would_expand(self) -> None:
        tensors = {'base_model.model.backbone.layers.0.self_attn.q_proj.lora_A.weight': [8, 3072],
                   'base_model.model.backbone.layers.0.self_attn.q_proj.lora_B.weight': [3072, 8]}
        with self.assertRaisesRegex(ValueError, 'incomplete_factors'):
            validate_heart_adapter(tensors, {'heart_lora_config': self.metadata().model_dump_json()})

    def test_rejects_unpaired_and_unsupported_factors(self) -> None:
        meta = {'heart_lora_config': self.metadata().model_dump_json()}
        for tensors in ({}, {'base_model.model.backbone.layers.0.self_attn.q_proj.lora_A.weight': [8, 3072]},
                        {'base_model.model.audio_head.lora_A.weight': [8, 3072]},
                        {'base_model.model.backbone.layers.0.self_attn.q_proj.lora_magnitude_vector': [3072]}):
            with self.subTest(tensors=tensors), self.assertRaises(ValueError):
                validate_heart_adapter(tensors, meta)

    def test_rejects_wrong_base_nonfinite_or_extra_behavior(self) -> None:
        original = json.loads(self.metadata().model_dump_json())
        for field, value in [('base_revision', '0' * 40), ('alpha', float('nan')), ('use_dora', True),
                             ('target_modules', ['q_proj', 'q_proj']), ('rank', True)]:
            bad = dict(original);bad[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                HeartLoraMetadata.model_validate(bad)
