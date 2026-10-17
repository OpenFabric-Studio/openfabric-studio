from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from pydantic import ValidationError
from app import lora_datasets
from app.atomic_files import write_object
from app.music_lora_contracts import MusicLoraError
from app.music_training_source_contracts import MusicLoraSource
from app.music_training_sources import source_path
from app.stable_lora_contracts import StableLoraTrainingRequest
from app.stable_lora_runtime import fingerprint


class MusicTrainingSourcesTests(unittest.TestCase):
    def test_exactly_one_bounded_source_is_required(self) -> None:
        for value in ({}, {'track_id': True}, {'track_id': 1, 'dataset': {'dataset_id': 'a' * 32, 'revision': 0, 'sample_id': 'clip'}}, {'dataset': {'dataset_id': '../outside', 'revision': 0, 'sample_id': 'clip'}}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                MusicLoraSource.model_validate(value)

    def test_existing_stable_request_fingerprint_stays_compatible(self) -> None:
        request = StableLoraTrainingRequest(name='Old run', samples=[{'track_id': 1, 'caption': 'Piano'}])
        old = '{"name":"Old run","variant":"small-music","samples":[{"track_id":1,"caption":"Piano"}],"max_steps":1000,"rank":16,"learning_rate":0.0001,"seed":42}'
        self.assertEqual(fingerprint(request), hashlib.sha256(old.encode()).hexdigest())

    def test_regional_manifest_selection_is_distinct_bounded_and_exclusive(self) -> None:
        reference = {'manifest_id': 'a' * 32, 'manifest_sha256': 'b' * 64, 'excerpt_id': 'c' * 32}
        selected = MusicLoraSource.model_validate({'regional_manifest': reference})
        self.assertIsNotNone(selected.regional_manifest)
        for value in ({'track_id': 1, 'regional_manifest': reference}, {'regional_manifest': {**reference, 'excerpt_id': 'c' * 32 + '\n'}},
                {'regional_manifest': {**reference, 'manifest_sha256': 'b' * 64 + '\n'}}):
            with self.assertRaises(ValidationError):
                MusicLoraSource.model_validate(value)
        with self.assertRaises(MusicLoraError) as unsupported:
            source_path(selected, prefix='music_training')
        self.assertEqual(unsupported.exception.code, 'music_training_dataset_invalid')

    def test_dataset_source_uses_saved_revision_and_rejects_escapes_without_native_engine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            engine = root / 'engine'; dataset = engine / 'datasets/set'; dataset.mkdir(parents=True)
            library = root / 'library'; library.mkdir()
            audio = dataset / 'clip.wav'; audio.write_bytes(b'original audio')
            document = dataset / 'dataset.json'
            write_object(document, {'metadata': {'name': 'Saved music'}, 'samples': [{'id': 'clip', 'audio_path': str(audio), 'caption': 'Piano', 'labeled': True}]})
            with patch.object(lora_datasets, 'ACE_STEP_DIR', engine), patch.object(lora_datasets, 'DATA_DIR', library):
                entry = lora_datasets.list_datasets().datasets[0]
                selected = MusicLoraSource(dataset={'dataset_id': entry.id, 'revision': entry.revision, 'sample_id': 'clip'})
                self.assertEqual(source_path(selected, prefix='music_training'), audio)
                outside = root / 'outside.wav'; outside.write_bytes(b'preserve outside')
                audio.unlink(); audio.symlink_to(outside)
                with self.assertRaises(MusicLoraError):
                    source_path(selected, prefix='music_training')
                self.assertEqual(outside.read_bytes(), b'preserve outside')
