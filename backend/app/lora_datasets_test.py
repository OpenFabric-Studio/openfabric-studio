"""Dataset catalog transactions preserve the original audio and annotations."""
from __future__ import annotations

import concurrent.futures
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import lora_datasets as library
from app.atomic_files import read_object, write_object
from app.lora_dataset_contracts import CreateLoraDatasetRequest, RenameLoraDatasetRequest, LoraDatasetRevision, LoraWorkspace


class DatasetLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.engine = self.root / 'engine'
        self.datasets = self.engine / 'datasets'
        self.datasets.mkdir(parents=True)
        self.data = self.root / 'library'
        self.data.mkdir()
        self.enterContext(patch.object(library, 'ACE_STEP_DIR', self.engine))
        self.enterContext(patch.object(library, 'DATA_DIR', self.data))

    def legacy(self, name: str = 'Saved work') -> Path:
        folder = self.datasets / 'legacy'
        folder.mkdir(exist_ok=True)
        audio = folder / 'clip.wav'
        audio.write_bytes(b'original')
        document = folder / 'dataset.json'
        write_object(document, {'metadata': {'name': name}, 'samples': [
            {'id': 'original-id', 'audio_path': str(audio), 'filename': audio.name,
             'caption': 'carefully edited', 'labeled': True, 'raw_lyrics': 'original lyrics'},
        ]})
        return document

    def test_discovery_is_stable_and_preserves_original_document(self) -> None:
        path = self.legacy()
        original = path.read_bytes()
        entry = library.list_datasets().datasets[0]
        self.assertEqual((entry.name, entry.sample_count, entry.labeled_count), ('Saved work', 1, 1))
        self.assertEqual(library.list_datasets().datasets[0].id, entry.id)
        self.assertEqual(path.read_bytes(), original)

    def test_saved_snapshot_reads_without_native_builder_and_checks_revision(self) -> None:
        path = self.legacy()
        original = path.read_bytes()
        entry = library.list_datasets().datasets[0]
        with patch.object(library, 'load_dataset', side_effect=AssertionError('native builder must remain untouched')):
            snapshot = library.read_saved_dataset(entry.id, entry.revision)
        self.assertEqual(snapshot.samples[0].caption, 'carefully edited')
        self.assertEqual(path.read_bytes(), original)
        with self.assertRaises(library.LoraDatasetError):
            library.read_saved_dataset(entry.id, entry.revision + 1)

    def test_rename_revision_and_trash_restore_never_move_originals(self) -> None:
        path = self.legacy()
        original = path.read_bytes()
        entry = library.list_datasets().datasets[0]
        renamed = library.rename_dataset(entry.id, RenameLoraDatasetRequest(name='Renamed', revision=entry.revision))
        self.assertEqual(renamed.name, 'Renamed')
        self.assertEqual(path.read_bytes(), original)
        with self.assertRaises(library.LoraDatasetError) as caught:
            library.trash_dataset(entry.id, LoraDatasetRevision(revision=entry.revision))
        self.assertEqual(caught.exception.code, 'dataset_revision_conflict')
        trashed = library.trash_dataset(entry.id, LoraDatasetRevision(revision=renamed.revision))
        self.assertTrue(trashed.trashed)
        self.assertTrue(path.is_file())
        restored = library.restore_dataset(entry.id, LoraDatasetRevision(revision=trashed.revision))
        self.assertFalse(restored.trashed)
        self.assertEqual(path.read_bytes(), original)

    def test_create_conflict_and_parallel_creates_do_not_lose_entries(self) -> None:
        library.create_dataset(CreateLoraDatasetRequest(name='One'))
        with self.assertRaises(library.LoraDatasetError):
            library.create_dataset(CreateLoraDatasetRequest(name='one'))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda n: library.create_dataset(CreateLoraDatasetRequest(name=f'Set {n}')), range(12)))
        self.assertEqual(len(library.list_datasets().datasets), 13)

    def test_symlink_escape_malformed_json_and_identifiers_are_rejected(self) -> None:
        outside = self.root / 'outside'
        outside.mkdir()
        write_object(outside / 'private.json', {'metadata': {'name': 'private'}, 'samples': []})
        (self.datasets / 'linked').symlink_to(outside, target_is_directory=True)
        (self.datasets / 'broken.json').write_text('{broken', encoding='utf-8')
        response = library.list_datasets()
        self.assertEqual(response.datasets, [])
        self.assertTrue(response.issues)
        for identifier in ('../outside', '/absolute', 'unknown'):
            with self.subTest(identifier=identifier), self.assertRaises(library.LoraDatasetError):
                library.get_dataset(identifier)

    def test_failed_catalog_replace_keeps_old_catalog_and_workspace(self) -> None:
        entry = library.create_dataset(CreateLoraDatasetRequest(name='One'))
        workspace = library.get_workspace()
        saved = library.save_workspace(workspace.model_copy(update={'selected_dataset_id': entry.id}))
        self.assertEqual(saved.selected_dataset_id, entry.id)
        with patch.object(Path, 'replace', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                library.rename_dataset(entry.id, RenameLoraDatasetRequest(name='Lost', revision=entry.revision))
        self.assertEqual(library.get_dataset(entry.id).name, 'One')
        self.assertEqual(library.get_workspace().selected_dataset_id, entry.id)
        with self.assertRaises(library.LoraDatasetError):
            library.save_workspace(LoraWorkspace(revision=workspace.revision))

    def test_native_saved_labels_advance_revision_and_reject_stale_mutations(self) -> None:
        path = self.legacy()
        entry = library.list_datasets().datasets[0]
        raw = read_object(path)
        raw['samples'][0]['caption'] = 'new native caption'
        write_object(path, raw)
        refreshed = library.get_dataset(entry.id)
        self.assertGreater(refreshed.revision, entry.revision)
        with self.assertRaises(library.LoraDatasetError) as caught:
            library.rename_dataset(entry.id, RenameLoraDatasetRequest(name='Stale', revision=entry.revision))
        self.assertEqual(caught.exception.code, 'dataset_revision_conflict')

    def test_unsafe_duplicate_or_missing_sample_ids_are_not_loadable(self) -> None:
        path = self.legacy()
        original = read_object(path)
        for identifier in ('../outside', '/absolute', 'C:\\outside', ''):
            with self.subTest(identifier=identifier):
                raw = read_object(path)
                raw['samples'][0]['id'] = identifier
                write_object(path, raw)
                self.assertEqual(library.list_datasets().datasets, [])
        original['samples'].append(dict(original['samples'][0]))
        write_object(path, original)
        self.assertEqual(library.list_datasets().datasets, [])

    def test_malformed_audio_path_cannot_hide_valid_datasets_or_break_known_entries(self) -> None:
        good = self.legacy()
        malformed = self.datasets / 'malformed.json'
        raw = read_object(good)
        raw['metadata'] = {'name': 'Malformed'}
        raw['samples'][0]['audio_path'] = 'invalid\x00source.wav'
        write_object(malformed, raw)
        listed = library.list_datasets()
        self.assertEqual([entry.name for entry in listed.datasets], ['Saved work'])
        self.assertEqual([(issue.path, issue.code) for issue in listed.issues], [('datasets/malformed.json', 'dataset_invalid')])
        raw['samples'][0]['audio_path'] = str(self.datasets / 'legacy' / 'clip.wav')
        write_object(malformed, raw)
        self.assertEqual(len(library.list_datasets().datasets), 2)
        raw['samples'][0]['audio_path'] = 'invalid\x00source.wav'
        write_object(malformed, raw)
        refreshed = library.list_datasets()
        availability = {entry.name: entry.available for entry in refreshed.datasets}
        self.assertEqual(availability, {'Saved work': True, 'Malformed': False})
        with self.assertRaises(library.LoraDatasetError):
            library._source({'audio_path': 'invalid\x00source.wav'})

    def test_catalog_capacity_never_publishes_an_unreadable_catalog(self) -> None:
        with patch.object(library, '_MAX_DATASETS', 2, create=True):
            first = library.create_dataset(CreateLoraDatasetRequest(name='First'))
            second = library.create_dataset(CreateLoraDatasetRequest(name='Second'))
            before = library._catalog_path().read_bytes()
            with self.assertRaises(library.LoraDatasetError):
                library.create_dataset(CreateLoraDatasetRequest(name='Third'))
            self.assertEqual(library._catalog_path().read_bytes(), before)
            self.legacy('Additional legacy')
            listed = library.list_datasets()
            self.assertEqual({entry.id for entry in listed.datasets}, {first.id, second.id})
            self.assertTrue(listed.issues)
            catalog = library._read_catalog(library._catalog_path())
            catalog.datasets.append(first.model_copy(update={'id': 'a' * 32}))
            with self.assertRaises(library.LoraDatasetError):
                library._write_catalog(library._catalog_path(), catalog)
            self.assertEqual(len(library.list_datasets().datasets), 2)

    def test_scan_merge_keeps_all_saved_annotations_and_unavailable_sources(self) -> None:
        original = read_object(self.legacy())
        scanned = {'metadata': {'name': 'scan'}, 'samples': [
            {'id': 'new-id', 'audio_path': str(self.datasets / 'legacy' / 'clip.wav'), 'caption': '', 'labeled': False},
            {'id': 'new-file', 'audio_path': str(self.datasets / 'legacy' / 'new.wav'), 'caption': '', 'labeled': False},
        ]}
        merged = library.merge_scan(original, scanned)
        samples = merged['samples']
        self.assertIsInstance(samples, list)
        self.assertEqual(samples[0], original['samples'][0])
        self.assertEqual(len(samples), 2)
        self.assertEqual(library.merge_scan(original, {'samples': []})['samples'], original['samples'])
