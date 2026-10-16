"""Managed native adapters are data, never pickle or unconstrained runtime paths."""
from __future__ import annotations

import io
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app import music_lora
from app.music_lora_contracts import MusicAdapter, MusicAdapterSelection, MusicAdapters, MusicLoraError


def adapter_bytes(engine: str = 'stable_audio3', target: str = 'small-music', *, missing_pair: bool = False, dimension: int | None = None, alpha: float | None = 2) -> bytes:
    if engine == 'stable_audio3':
        prefix = 'model.transformer.layers.0.self_attn.to_qkv.parametrizations.weight.0.'
        metadata = {'lora_config': json.dumps({'adapter_type': 'lora', 'rank': 2, 'alpha': alpha,
            'base_model': 'sa3-sm-music' if target == 'small-music' else 'sa3-medium'})}
    else:
        prefix = 'layers.0.' + ('nar_' if target == 'nar' else '') + 'self_attn.q_proj.'
        metadata = {}
    width_in = dimension if dimension is not None else (2048 if engine == 'yue2' else 1536 if target == 'medium' else 1024)
    width_out = width_in if engine == 'yue2' else (7680 if target == 'medium' else 3072)
    size_a, size_b = 2 * width_in * 2, 2 * width_out * 2
    header: dict[str, object] = {'__metadata__': metadata,
        prefix + 'lora_A': {'dtype': 'F16', 'shape': [2, width_in], 'data_offsets': [0, size_a]}}
    payload = bytes(size_a)
    if not missing_pair:
        header[prefix + 'lora_B'] = {'dtype': 'F16', 'shape': [width_out, 2], 'data_offsets': [size_a, size_a + size_b]}
        payload += bytes(size_b)
    encoded = json.dumps(header).encode()
    return struct.pack('<Q', len(encoded)) + encoded + payload


class MusicLoraTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.patch = patch.object(music_lora.db, 'DATA_DIR', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.db_path_patch = patch.object(music_lora.db, 'DB_PATH', self.root / 'catalog.db')
        self.db_path_patch.start()
        self.addCleanup(self.db_path_patch.stop)

    def register(self, engine: str = 'stable_audio3', target: str = 'small-music') -> MusicAdapter:
        return music_lora.import_stream(io.BytesIO(adapter_bytes(engine, target)), filename='adapter.safetensors',
            name='My style', engine=engine, target=target)

    def test_import_copies_validated_data_and_resolves_immutable_checksum(self) -> None:
        adapter = self.register()
        selected = MusicAdapterSelection(id=adapter.id, strength=0.6)
        path = music_lora.resolve(selected, engine='stable_audio3', target='small-music')
        self.assertEqual(path.read_bytes(), adapter_bytes())
        self.assertEqual(music_lora.get(adapter.id), adapter)
        self.assertEqual(music_lora.list_adapters().adapters, [adapter])
        path.write_bytes(path.read_bytes() + b'changed')
        with self.assertRaises(MusicLoraError) as caught:
            music_lora.resolve(selected, engine='stable_audio3', target='small-music')
        self.assertEqual(caught.exception.code, 'music_lora_adapter_changed')

    def test_retired_adapter_metadata_is_preserved_without_poisoning_other_adapters(self) -> None:
        stable = self.register()
        identifier = 'd' * 32
        directory = self.root / 'music-lora' / identifier
        directory.mkdir()
        manifest = json.dumps({**stable.model_dump(), 'id': identifier, 'engine': 'heartmula', 'target': 'heartmula-3b'})
        (directory / 'manifest.json').write_text(manifest)
        (directory / 'adapter.safetensors').write_bytes(b'old user adapter')
        listed = music_lora.list_adapters()
        self.assertEqual(listed.adapters, [stable])
        self.assertFalse(listed.history_incomplete)
        self.assertEqual((directory / 'manifest.json').read_text(), manifest)
        self.assertEqual((directory / 'adapter.safetensors').read_bytes(), b'old user adapter')

    def test_duplicate_import_names_are_rejected_across_variants_without_changing_original(self) -> None:
        adapter = self.register()
        original = music_lora.export_path(adapter.id).read_bytes()
        for name in ('My style', ' my   STYLE ', 'Ｍｙ style'):
            with self.subTest(name=name), self.assertRaises(MusicLoraError) as caught:
                music_lora.import_stream(io.BytesIO(adapter_bytes(target='medium')), filename='other.safetensors',
                    name=name, engine='stable_audio3', target='medium')
            self.assertEqual(caught.exception.code, 'music_lora_name_exists')
        self.assertEqual(music_lora.export_path(adapter.id).read_bytes(), original)
        self.assertEqual(len(music_lora.list_adapters().adapters), 1)

    def test_names_are_scoped_to_models_and_legacy_publication_is_not_blocked(self) -> None:
        self.register()
        other = self.register('yue2', 'ar')
        self.assertEqual(other.engine, 'yue2')
        source = self.root / 'trained.safetensors'; source.write_bytes(adapter_bytes())
        legacy = music_lora.register_file(source, name='My style', engine='stable_audio3', target='small-music', source_job_id='b' * 32)
        self.assertEqual(legacy.name, 'My style')
        self.assertEqual(len(music_lora.list_adapters().adapters), 3)

    def test_rejects_pickle_unknown_target_and_missing_pairs(self) -> None:
        for filename, engine, target, content in (
            ('unsafe.pt', 'stable_audio3', 'small-music', b'pickle'),
            ('a.safetensors', 'heartmula', 'small-music', adapter_bytes()),
            ('a.safetensors', 'stable_audio3', 'ar', adapter_bytes()),
            ('a.safetensors', 'stable_audio3', 'medium', adapter_bytes()),
            ('a.safetensors', 'yue2', 'nar', adapter_bytes('yue2', 'ar')),
            ('a.safetensors', 'yue2', 'ar', adapter_bytes('yue2', 'ar', missing_pair=True)),
        ):
            with self.subTest(filename=filename, engine=engine, target=target), self.assertRaises(MusicLoraError):
                music_lora.import_stream(io.BytesIO(content), filename=filename, name='Style', engine=engine, target=target)
        self.assertEqual(music_lora.list_adapters().adapters, [])

    def test_selection_bounds_and_target_resolution(self) -> None:
        for strength in (-0.1, 2.1, math.nan, math.inf, '1', True):
            with self.assertRaises(ValidationError):
                MusicAdapterSelection(id='a' * 32, strength=strength)
        adapter = self.register('yue2', 'ar')
        with self.assertRaises(MusicLoraError) as caught:
            music_lora.resolve(MusicAdapterSelection(id=adapter.id), engine='yue2', target='nar')
        self.assertEqual(caught.exception.code, 'music_lora_target_mismatch')

    def test_native_legacy_dora_alias_keeps_its_original_checkpoint(self) -> None:
        content = adapter_bytes()
        length = struct.unpack('<Q', content[:8])[0]
        header = json.loads(content[8:8 + length])
        config = json.loads(header['__metadata__']['lora_config']); config['adapter_type'] = 'dora'
        header['__metadata__']['lora_config'] = json.dumps(config)
        payload = content[8 + length:]
        header['model.transformer.layers.0.self_attn.to_qkv.parametrizations.weight.0.magnitude'] = {
            'dtype': 'F16', 'shape': [3072], 'data_offsets': [len(payload), len(payload) + 6144]}
        encoded = json.dumps(header).encode()
        content = struct.pack('<Q', len(encoded)) + encoded + payload + bytes(6144)
        adapter = music_lora.import_stream(io.BytesIO(content), filename='legacy.safetensors',
            engine='stable_audio3', target='small-music', name='Legacy DoRA')
        self.assertEqual(music_lora.export_path(adapter.id).read_bytes(), content)

    def test_explicit_null_alpha_is_rejected_before_native_float_conversion(self) -> None:
        with self.assertRaises(MusicLoraError):
            music_lora.import_stream(io.BytesIO(adapter_bytes(alpha=None)), filename='invalid.safetensors',
                engine='stable_audio3', target='small-music', name='Invalid alpha')

    def test_stable_unknown_layers_cannot_silently_generate_base_model(self) -> None:
        content = adapter_bytes()
        length = struct.unpack('<Q', content[:8])[0]
        header = content[8:8 + length].replace(b'model.transformer.layers.0.self_attn.to_qkv', b'nonexistent_layer')
        malformed = struct.pack('<Q', len(header)) + header + content[8 + length:]
        with self.assertRaises(MusicLoraError):
            music_lora.import_stream(io.BytesIO(malformed), filename='ignored.safetensors',
                engine='stable_audio3', target='small-music', name='Ignored by native loader')

    def test_yue_rejects_wrong_3b_shapes_before_native_loading(self) -> None:
        with self.assertRaises(MusicLoraError):
            music_lora.import_stream(io.BytesIO(adapter_bytes('yue2', dimension=4)), filename='wrong.safetensors',
                engine='yue2', target='ar', name='Wrong dimensions')

    def test_header_bounds_offsets_and_duplicate_keys(self) -> None:
        malformed = [struct.pack('<Q', 16_000_000), b'not safetensors',
            struct.pack('<Q', 13) + b'{"x":1,"x":2}']
        header = {'layers.0.self_attn.q_proj.lora_A': {'dtype': 'F16', 'shape': [2, 4], 'data_offsets': [0, 99]}}
        encoded = json.dumps(header).encode()
        malformed.append(struct.pack('<Q', len(encoded)) + encoded + bytes(16))
        for content in malformed:
            with self.subTest(content=content[:8]), self.assertRaises(MusicLoraError):
                music_lora.import_stream(io.BytesIO(content), filename='a.safetensors', name='Style', engine='yue2', target='ar')
        with patch.object(music_lora, 'MAX_ADAPTER_BYTES', 10), self.assertRaises(MusicLoraError):
            self.register()

    def test_symlink_roots_and_artifacts_never_escape(self) -> None:
        outside = self.root / 'outside'; outside.mkdir()
        (self.root / 'music-lora').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(MusicLoraError):
            self.register()
        self.assertEqual(list(outside.iterdir()), [])
        (self.root / 'music-lora').unlink()
        adapter = self.register()
        path = music_lora.resolve(MusicAdapterSelection(id=adapter.id), engine='stable_audio3', target='small-music')
        path.unlink(); path.symlink_to(self.root / 'private')
        with self.assertRaises(MusicLoraError):
            music_lora.export_path(adapter.id)

    def test_corrupt_manifest_is_retained_and_history_reports_it(self) -> None:
        adapter = self.register()
        path = self.root / 'music-lora' / adapter.id / 'manifest.json'
        path.write_text('{broken')
        listing = music_lora.list_adapters()
        self.assertEqual(listing.adapters, [])
        self.assertTrue(listing.history_incomplete)
        self.assertTrue(path.exists())

    def test_training_publication_is_idempotent_by_job(self) -> None:
        source = self.root / 'trained.safetensors'; source.write_bytes(adapter_bytes())
        job = 'b' * 32
        adapter = music_lora.register_file(source, name='Trained', engine='stable_audio3', target='small-music', source_job_id=job)
        with patch.object(music_lora, 'list_adapters', return_value=MusicAdapters(adapters=[])):
            self.assertEqual(music_lora.find_by_job(job), adapter)
        self.assertEqual(music_lora.register_file(source, name='Trained', engine='stable_audio3', target='small-music', source_job_id=job), adapter)
        self.assertEqual(len(music_lora.list_adapters().adapters), 1)

    def test_training_pair_publication_is_idempotent_by_job_and_target(self) -> None:
        job = 'b' * 32
        paths = {target: self.root / (target + '.safetensors') for target in ('ar', 'nar')}
        for target, source in paths.items():
            source.write_bytes(adapter_bytes('yue2', target))
        ar = music_lora.register_file(paths['ar'], name='Pair', engine='yue2', target='ar', source_job_id=job)
        try:
            nar = music_lora.register_file(paths['nar'], name='Pair', engine='yue2', target='nar', source_job_id=job)
        except MusicLoraError as error:
            self.fail('Second target was mistaken for the first: ' + error.code)
        self.assertNotEqual(ar.id, nar.id)
        self.assertEqual(music_lora.find_by_job(job, target='ar'), ar)
        self.assertEqual(music_lora.find_by_job(job, target='nar'), nar)
        self.assertEqual(music_lora.register_file(paths['ar'], name='Pair', engine='yue2', target='ar', source_job_id=job), ar)
        self.assertEqual(music_lora.register_file(paths['nar'], name='Pair', engine='yue2', target='nar', source_job_id=job), nar)
        self.assertEqual(len(music_lora.list_adapters().adapters), 2)

    def test_converted_planner_registers_using_native_ar_projection_names(self) -> None:
        from app.yue_lora_convert import BASE_SHA256, convert_ar
        from app.yue_lora_convert_test import AR_PREFIX, PREFIX, factors, fixture
        source, output = self.root / 'planner.safetensors', self.root / 'adapter-ar.safetensors'
        tensors = {name.replace(PREFIX, AR_PREFIX): tensor for name, tensor in factors('self_attn.qkv_proj', 2048, 4096).items()}
        fixture(source, tensors, overrides={'kind': 'planner', 'train_abc': True, 'train_semantic': False})
        convert_ar(source, output, base_sha256=BASE_SHA256)
        music_lora.validate_file(output, engine='yue2', target='ar')
        adapter = music_lora.register_file(output, name='Planner', engine='yue2', target='ar', source_job_id='b' * 32)
        self.assertEqual(adapter.target, 'ar')
        self.assertEqual(music_lora.export_path(adapter.id).read_bytes(), output.read_bytes())

    def test_failed_manifest_publish_never_lists_partial_import(self) -> None:
        with patch.object(music_lora, 'write_object', side_effect=OSError('disk full')), self.assertRaises(OSError):
            self.register()
        self.assertEqual(music_lora.list_adapters().adapters, [])


class MusicLoraValidationTests(unittest.TestCase):

    def test_nonfinite_adapter_weights_are_refused(self) -> None:
        content = adapter_bytes()
        length = struct.unpack('<Q', content[:8])[0]
        content = content[:8 + length] + struct.pack('<H', 0x7c00) + content[10 + length:]
        with tempfile.TemporaryDirectory() as directory, patch.object(music_lora.db, 'DATA_DIR', Path(directory)), \
                patch.object(music_lora.db, 'DB_PATH', Path(directory) / 'catalog.db'), self.assertRaises(MusicLoraError):
            music_lora.import_stream(io.BytesIO(content), filename='invalid.safetensors', name='Invalid', engine='stable_audio3', target='small-music')
