"""Model-free numerical proof of the pinned YuE2 NAR adapter conversion."""
from __future__ import annotations

import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from numpy.typing import NDArray

from app import yue_lora_convert as convert

PREFIX = 'diffusion_model.model.layers.0.'


def fixture(path: Path, tensors: dict[str, NDArray[np.float32]], *, overrides: dict[str, object] | None = None, storage_dtype: str = 'F32') -> None:
    info: dict[str, object] = {'kind': 'acoustic', 'rank': 2, 'alpha': 6.0, 'targets': 'attention+mlp', 'train_acoustic_head': False}
    info.update(overrides or {})
    header: dict[str, object] = {'__metadata__': {'yue2_trainer': json.dumps(info)}}
    chunks: list[bytes] = []
    offset = 0
    for name, tensor in tensors.items():
        dtype = 'F32' if name.endswith('.alpha') else storage_dtype
        if dtype == 'BF16':
            data = (tensor.astype('<f4').view('<u4') >> np.uint32(16)).astype('<u2').tobytes()
        else:
            data = tensor.astype('<f4' if dtype == 'F32' else '<f2').tobytes()
        header[name] = {'dtype': dtype, 'shape': list(tensor.shape), 'data_offsets': [offset, offset + len(data)]}
        chunks.append(data)
        offset += len(data)
    encoded = json.dumps(header).encode()
    path.write_bytes(struct.pack('<Q', len(encoded)) + encoded + b''.join(chunks))


def factors(projection: str, width_in: int, width_out: int) -> dict[str, NDArray[np.float32]]:
    rng = np.random.default_rng(7)
    return {PREFIX + projection + '.lora_down.weight': rng.normal(size=(2, width_in)).astype(np.float32),
        PREFIX + projection + '.lora_up.weight': rng.normal(size=(width_out, 2)).astype(np.float32),
        PREFIX + projection + '.alpha': np.array(6, dtype=np.float32)}


def read_tensors(path: Path) -> dict[str, NDArray[np.float32]]:
    data = path.read_bytes()
    length = struct.unpack('<Q', data[:8])[0]
    header: object = json.loads(data[8:8 + length])
    if not isinstance(header, dict):
        raise AssertionError('Expected header')
    output: dict[str, NDArray[np.float32]] = {}
    for name, spec in header.items():
        if name == '__metadata__':
            continue
        if not isinstance(name, str) or not isinstance(spec, dict):
            raise AssertionError('Expected tensor')
        begin, end = spec['data_offsets']
        output[name] = np.frombuffer(data[8 + length + begin:8 + length + end], dtype='<f4').reshape(spec['shape'])
    return output


class YueLoraConvertTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.output = self.root / 'trainer.safetensors', self.root / 'native.safetensors'

    def convert(self) -> None:
        convert.convert_nar(self.source, self.output, base_sha256=convert.BASE_SHA256)

    def test_fused_qkv_and_gate_up_preserve_scaled_matrix_delta(self) -> None:
        source = factors('self_attn.qkv_proj', 2048, 4096) | factors('mlp.gate_up_proj', 2048, 12288)
        fixture(self.source, source)
        self.convert()
        output = read_tensors(self.output)
        for projection, widths, names in (
            ('self_attn.qkv_proj', [2048, 1024, 1024], ['self_attn.q_proj', 'self_attn.k_proj', 'self_attn.v_proj']),
            ('mlp.gate_up_proj', [6144, 6144], ['mlp.gate_proj', 'mlp.up_proj']),
        ):
            down, up = source[PREFIX + projection + '.lora_down.weight'], source[PREFIX + projection + '.lora_up.weight']
            expected = (up @ down[:, :13]) * 3
            actual = np.concatenate([output['layers.0.nar_' + name + '.lora_B'] @ output['layers.0.nar_' + name + '.lora_A'][:, :13] for name in names])
            np.testing.assert_allclose(actual, expected, rtol=2e-5, atol=4e-6)
            self.assertEqual([output['layers.0.nar_' + name + '.lora_B'].shape[0] for name in names], widths)
        self.assertEqual(len(output), 10)

    def test_unfused_all_supported_projections_preserve_delta(self) -> None:
        dimensions = {'self_attn.q_proj': (2048, 2048), 'self_attn.k_proj': (2048, 1024),
            'self_attn.v_proj': (2048, 1024), 'self_attn.o_proj': (2048, 2048),
            'mlp.gate_proj': (2048, 6144), 'mlp.up_proj': (2048, 6144), 'mlp.down_proj': (6144, 2048)}
        source: dict[str, NDArray[np.float32]] = {}
        for projection, (width_in, width_out) in dimensions.items():
            source.update(factors(projection, width_in, width_out))
        fixture(self.source, source)
        self.convert()
        output = read_tensors(self.output)
        for projection in dimensions:
            expected = source[PREFIX + projection + '.lora_up.weight'] @ source[PREFIX + projection + '.lora_down.weight'][:, :13] * 3
            actual = output['layers.0.nar_' + projection + '.lora_B'] @ output['layers.0.nar_' + projection + '.lora_A'][:, :13]
            np.testing.assert_allclose(actual, expected, rtol=2e-5, atol=4e-6)

    def test_bf16_and_fp16_exports_remain_exact_when_promoted(self) -> None:
        for dtype in ('BF16', 'F16'):
            with self.subTest(dtype=dtype):
                source = factors('self_attn.q_proj', 2048, 2048)
                source[PREFIX + 'self_attn.q_proj.lora_down.weight'].fill(0.5)
                source[PREFIX + 'self_attn.q_proj.lora_up.weight'].fill(0.25)
                fixture(self.source, source, storage_dtype=dtype)
                self.convert()
                output = read_tensors(self.output)
                self.assertTrue(np.all(output['layers.0.nar_self_attn.q_proj.lora_A'] == 0.5))
                self.assertTrue(np.all(output['layers.0.nar_self_attn.q_proj.lora_B'] == 0.75))
                self.output.unlink()

    def test_rejects_wrong_base_stage_head_unknown_and_missing_tensors(self) -> None:
        good = factors('self_attn.q_proj', 2048, 2048)
        fixture(self.source, good)
        with self.assertRaises(ValueError):
            convert.convert_nar(self.source, self.output, base_sha256='0' * 64)
        cases: list[tuple[dict[str, NDArray[np.float32]], dict[str, object]]] = [
            (good, {'kind': 'planner'}), (good, {'train_acoustic_head': True}),
            ({name.replace('diffusion_model.model.', 'text_encoders.model.'): tensor for name, tensor in good.items()}, {}),
            (good | {'vae2llm.weight': np.zeros((2048, 64), dtype=np.float32)}, {}),
            ({name: tensor for name, tensor in good.items() if not name.endswith('.lora_up.weight')}, {}),
            ({name: tensor for name, tensor in good.items() if not name.endswith('.alpha')}, {}),
            (good, {'rank': 3}), (good, {'alpha': 5}),
            ({name.replace('layers.0.', 'layers.28.'): tensor for name, tensor in good.items()}, {}),
            ({name.replace('layers.0.', 'layers.00.'): tensor for name, tensor in good.items()}, {}),
        ]
        for tensors, metadata in cases:
            with self.subTest(metadata=metadata, keys=list(tensors)):
                fixture(self.source, tensors, overrides=metadata)
                with self.assertRaises(ValueError):
                    self.convert()
                self.assertFalse(self.output.exists())

    def test_rejects_shape_nonfinite_duplicate_and_overlapping_projections(self) -> None:
        good = factors('self_attn.q_proj', 2048, 2048)
        nonfinite = factors('self_attn.q_proj', 2048, 2048)
        nonfinite[PREFIX + 'self_attn.q_proj.lora_up.weight'][0, 0] = np.nan
        for source in (factors('self_attn.q_proj', 4, 4), nonfinite,
            good | factors('self_attn.qkv_proj', 2048, 4096)):
            fixture(self.source, source)
            with self.assertRaises(ValueError):
                self.convert()
        self.source.write_bytes(struct.pack('<Q', 13) + b'{"x":1,"x":2}')
        with self.assertRaises(ValueError):
            self.convert()

    def test_preserves_existing_destination_and_source_on_failed_publication(self) -> None:
        fixture(self.source, factors('self_attn.q_proj', 2048, 2048))
        before = self.source.read_bytes()
        self.output.write_bytes(b'existing')
        with self.assertRaises(FileExistsError):
            self.convert()
        self.assertEqual(self.output.read_bytes(), b'existing')
        self.output.unlink()
        with patch.object(convert.os, 'link', side_effect=OSError('disk full')), self.assertRaises(OSError):
            self.convert()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ['trainer.safetensors'])

    def test_completed_training_requires_steps_finite_loss_and_changed_factors(self) -> None:
        source = factors('self_attn.q_proj', 2048, 2048)
        fixture(self.source, source, overrides={'steps': 2, 'final_loss': 0.4})
        convert.validate_training_checkpoint(self.source, max_steps=2, rank=2)
        for metadata in ({'steps': 1, 'final_loss': 0.4}, {'steps': 2}, {'steps': 2, 'final_loss': float('nan')}):
            fixture(self.source, source, overrides=metadata)
            with self.assertRaises(ValueError):
                convert.validate_training_checkpoint(self.source, max_steps=2, rank=2)
        source[PREFIX + 'self_attn.q_proj.lora_down.weight'].fill(0)
        fixture(self.source, source, overrides={'steps': 2, 'final_loss': 0.4})
        with self.assertRaises(ValueError):
            convert.validate_training_checkpoint(self.source, max_steps=2, rank=2)


if __name__ == '__main__':
    unittest.main()
