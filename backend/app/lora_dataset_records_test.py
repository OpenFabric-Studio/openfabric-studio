"""Removing trash records is atomic and never removes dataset files."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app import lora_dataset_contracts as contracts, lora_datasets as library
from app.atomic_files import read_object


class DatasetRecordRemovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.engine = self.root / 'engine'
        self.data = self.root / 'library'
        (self.engine / 'datasets').mkdir(parents=True)
        self.enterContext(patch.object(library, 'ACE_STEP_DIR', self.engine))
        self.enterContext(patch.object(library, 'DATA_DIR', self.data))

    def create(self, name: str, *, trashed: bool = True) -> contracts.LoraDatasetEntry:
        entry = library.create_dataset(contracts.CreateLoraDatasetRequest(name=name))
        return library.trash_dataset(entry.id, contracts.LoraDatasetRevision(revision=entry.revision)) if trashed else entry

    def remove(self, *entries: contracts.LoraDatasetEntry) -> contracts.RemoveLoraDatasetRecordsResponse:
        body = contracts.RemoveLoraDatasetRecordsRequest(records=[{'id': entry.id, 'revision': entry.revision} for entry in entries])
        return library.remove_dataset_records(body)

    def test_removal_preserves_every_file_and_does_not_rediscover_record(self) -> None:
        entry = self.create('Remove me')
        folder = self.engine / entry.audio_dir
        for relative in ('audio.wav', 'tensors/saved.pt', 'trained/adapter/weights.safetensors'):
            path = folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'preserve this file')
        before = {path: path.read_bytes() for path in folder.rglob('*') if path.is_file()}
        response = self.remove(entry)
        self.assertEqual(response.removed_ids, [entry.id])
        self.assertEqual(library.list_datasets().datasets, [])
        self.assertEqual({path: path.read_bytes() for path in folder.rglob('*') if path.is_file()}, before)
        stored = library._read_catalog(library._catalog_path())
        self.assertIn(entry.dataset_path, stored.removed_dataset_paths)
        self.assertNotIn(entry.id, stored.dataset_hashes)
        self.assertNotIn(entry.id, stored.source_signatures)
        self.assertEqual(library.list_datasets().datasets, [], 'a later catalog read must keep the tombstone')

    def test_one_batch_removes_only_its_trashed_records(self) -> None:
        first, second, active = self.create('First'), self.create('Second'), self.create('Active', trashed=False)
        self.assertEqual(self.remove(first, second).removed_ids, [first.id, second.id])
        self.assertEqual([entry.id for entry in library.list_datasets().datasets], [active.id])

    def test_active_stale_and_missing_records_reject_the_entire_batch(self) -> None:
        first, second = self.create('First'), self.create('Second')
        active = self.create('Active', trashed=False)
        for rejected, code in ((active, 'dataset_not_trashed'), (second.model_copy(update={'revision': second.revision + 1}), 'dataset_revision_conflict'), (second.model_copy(update={'id': 'f' * 32}), 'dataset_not_found')):
            with self.subTest(code=code):
                before = library._catalog_path().read_bytes()
                with self.assertRaises(library.LoraDatasetError) as caught:
                    self.remove(first, rejected)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(library._catalog_path().read_bytes(), before)

    def test_missing_and_unsafe_dataset_files_can_be_forgotten_without_touching_sources(self) -> None:
        entry = self.create('Missing')
        (self.engine / entry.dataset_path).unlink()
        self.assertEqual(self.remove(entry).removed_ids, [entry.id])
        unsafe = self.create('Unsafe')
        path = self.engine / unsafe.dataset_path
        outside = self.root / 'outside.json'
        outside.write_text('private original')
        path.unlink(); path.symlink_to(outside)
        self.assertEqual(self.remove(unsafe).removed_ids, [unsafe.id])
        self.assertTrue(path.is_symlink())
        self.assertEqual(outside.read_text(), 'private original')

    def test_interrupted_catalog_write_keeps_records_and_tombstones_unchanged(self) -> None:
        entry = self.create('One')
        before = library._catalog_path().read_bytes()
        with patch.object(library, 'write_object', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.remove(entry)
        self.assertEqual(library._catalog_path().read_bytes(), before)
        self.assertEqual([item.id for item in library.list_datasets().datasets], [entry.id])

    def test_request_rejects_duplicate_empty_invalid_or_unversioned_records(self) -> None:
        for records in ([], [{'id': 'a' * 32, 'revision': 1}] * 2, [{'id': '../escape', 'revision': 1}], [{'id': 'a' * 32}], [{'id': 'a' * 32, 'revision': True}]):
            with self.subTest(records=records), self.assertRaises(ValidationError):
                contracts.RemoveLoraDatasetRecordsRequest.model_validate({'records': records})

    def test_exact_workspace_references_are_cleared_without_changing_other_settings(self) -> None:
        removed, active = self.create('Removed'), self.create('Active', trashed=False)
        catalog = library._read_catalog(library._catalog_path())
        catalog.workspace.selected_dataset_id = active.id
        catalog.workspace.training_tensor_dir = 'datasets/active/tensors'
        catalog.workspace.form.lora_rank = 32
        catalog.workspace.auto_label_task = contracts.LoraObservedTask(task_id='removed-task', dataset_id=removed.id, dataset_path=removed.dataset_path)
        catalog.workspace.preprocess_task = contracts.LoraObservedTask(task_id='keep-task', dataset_id=active.id, dataset_path=active.dataset_path)
        old_revision = catalog.workspace.revision
        library._write_catalog(library._catalog_path(), catalog)
        self.remove(removed)
        current = library.get_workspace()
        self.assertIsNone(current.auto_label_task)
        self.assertIsNotNone(current.preprocess_task)
        self.assertEqual(current.selected_dataset_id, active.id)
        self.assertEqual(current.training_tensor_dir, 'datasets/active/tensors')
        self.assertEqual(current.form.lora_rank, 32)
        self.assertEqual(current.revision, old_revision + 1)

    def test_removed_record_capacity_failure_is_atomic(self) -> None:
        first, second = self.create('First'), self.create('Second')
        self.remove(first)
        before = library._catalog_path().read_bytes()
        with patch.object(library, '_MAX_REMOVED_RECORDS', 1):
            with self.assertRaises(library.LoraDatasetError) as caught:
                self.remove(second)
        self.assertEqual(caught.exception.code, 'dataset_catalog_full')
        self.assertEqual(library._catalog_path().read_bytes(), before)
