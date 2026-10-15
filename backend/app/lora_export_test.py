"""LoRA export creates a fresh copy and never removes or overwrites user data."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app import config, lora_export
from app.lora_export_contracts import LoraExportRequest, LoraTrainedCheckpoint, LoraTrainedFilesRequest, LoraTrainedFilesResponse


class LoraExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.engine = self.root / 'engine'
        self.library = self.root / 'library'
        self.engine.mkdir()
        self.library.mkdir()
        self.enterContext(patch.object(config, 'ACE_STEP_DIR', self.engine))
        self.enterContext(patch.object(config, 'DATA_DIR', self.library))
        self.output = self.engine / 'lora_output' / 'run-one'
        self.source = self.output / 'final'
        self.adapter(self.source, b'final')
        self.destination = self.root / 'my-export'

    def adapter(self, source: Path, payload: bytes) -> None:
        (source / 'adapter').mkdir(parents=True)
        (source / 'adapter' / 'adapter_config.json').write_text('{}', encoding='utf-8')
        (source / 'adapter' / 'adapter_model.safetensors').write_bytes(payload)
        (source / 'training_state.json').write_text('{}', encoding='utf-8')

    def body(self, destination: Path | None = None, source: str = 'lora_output/run-one') -> LoraExportRequest:
        return LoraExportRequest(lora_output_dir=source, export_path=str(destination or self.destination))

    def assert_error(self, code: str, destination: Path | None = None, source: str = 'lora_output/run-one') -> None:
        with self.assertRaises(lora_export.LoraExportError) as error:
            lora_export.export_lora(self.body(destination, source))
        self.assertEqual(error.exception.code, code)

    def test_preview_writes_nothing_and_fresh_export_copies_final_and_returns_adapter_path(self) -> None:
        preview = lora_export.preview_export(self.body())
        self.assertFalse(self.destination.exists())
        self.assertEqual(preview.source, str(self.source))
        result = lora_export.export_lora(self.body())
        self.assertEqual(result.export_path, str(self.destination))
        self.assertEqual(result.adapter_path, str(self.destination / 'adapter'))
        self.assertEqual((self.destination / 'adapter' / 'adapter_model.safetensors').read_bytes(), b'final')
        self.assertEqual((self.source / 'adapter' / 'adapter_model.safetensors').read_bytes(), b'final')

    def test_all_existing_destinations_and_source_are_preserved(self) -> None:
        for kind in ('sentinel', 'empty', 'file', 'dangling', 'symlink'):
            target = self.root / kind
            if kind in ('sentinel', 'empty'):
                target.mkdir()
            if kind == 'sentinel':
                (target / 'precious.wav').write_bytes(b'keep me')
            elif kind == 'file':
                target.write_bytes(b'keep file')
            elif kind in ('dangling', 'symlink'):
                target.symlink_to(self.root / 'missing' if kind == 'dangling' else self.source, target_is_directory=True)
            with self.subTest(kind=kind):
                self.assert_error('lora_export_destination_exists', target)
                self.assertTrue(os.path.lexists(target))
        self.assertEqual((self.root / 'sentinel' / 'precious.wav').read_bytes(), b'keep me')
        self.assertEqual((self.root / 'file').read_bytes(), b'keep file')

    def test_latest_numeric_checkpoint_is_selected_only_when_final_is_absent(self) -> None:
        alternate = self.engine / 'lora_output' / 'interrupted'
        self.adapter(alternate / 'checkpoints' / 'epoch_9', b'older')
        self.adapter(alternate / 'checkpoints' / 'epoch_100', b'newest')
        self.adapter(alternate / 'checkpoints' / 'epoch_nope', b'ignore')
        result = lora_export.export_lora(self.body(source='lora_output/interrupted'))
        self.assertEqual(Path(result.source).name, 'epoch_100')
        self.assertEqual((self.destination / 'adapter' / 'adapter_model.safetensors').read_bytes(), b'newest')

    def test_checkpoint_names_match_the_installed_trainer_loss_suffix(self) -> None:
        alternate = self.engine / 'lora_output' / 'stopped-training'
        self.adapter(alternate / 'checkpoints' / 'epoch_9_loss_0.0025', b'older')
        self.adapter(alternate / 'checkpoints' / 'epoch_100_loss_0.2500', b'newest')
        self.adapter(alternate / 'checkpoints' / 'epoch_200_loss_bad', b'ignore')
        result = lora_export.export_lora(self.body(source='lora_output/stopped-training'))
        self.assertEqual(Path(result.source).name, 'epoch_100_loss_0.2500')
        self.assertEqual((self.destination / 'adapter' / 'adapter_model.safetensors').read_bytes(), b'newest')

    def test_source_must_be_real_trained_output_strictly_inside_the_engine(self) -> None:
        for source in ('.', './', '../lora_output/run-one', str(self.output), 'lora_output/../../private', 'lora_output\\run-one', 'lora_output/run\x00one'):
            with self.subTest(source=source):
                self.assert_error('lora_export_source_unsafe', source=source)
        self.assertFalse(self.destination.exists())
        (self.source / 'adapter' / 'adapter_model.safetensors').unlink()
        self.assert_error('lora_export_adapter_missing')

    def test_existing_custom_training_output_roots_remain_exportable(self) -> None:
        custom = self.engine / 'custom-runs' / 'run-two' / 'final'
        self.adapter(custom, b'custom source')
        result = lora_export.export_lora(self.body(source='custom-runs/run-two'))
        self.assertEqual(result.source, str(custom))
        self.assertEqual((self.destination / 'adapter' / 'adapter_model.safetensors').read_bytes(), b'custom source')

    def test_source_symlinks_cannot_export_arbitrary_data(self) -> None:
        outside = self.root / 'outside'
        self.adapter(outside, b'secret')
        (self.engine / 'lora_output' / 'linked').symlink_to(outside, target_is_directory=True)
        self.assert_error('lora_export_source_unsafe', source='lora_output/linked')
        (self.source / 'unrelated').symlink_to(outside, target_is_directory=True)
        self.assert_error('lora_export_source_unsafe')
        self.assertFalse(self.destination.exists())

    def test_incomplete_final_falls_back_to_a_complete_checkpoint(self) -> None:
        (self.source / 'adapter' / 'adapter_model.safetensors').unlink()
        self.adapter(self.output / 'checkpoints' / 'epoch_20', b'checkpoint')
        result = lora_export.export_lora(self.body())
        self.assertEqual(Path(result.source).name, 'epoch_20')
        self.assertEqual((self.destination / 'adapter' / 'adapter_model.safetensors').read_bytes(), b'checkpoint')

    def test_missing_outputs_and_symlinked_final_or_checkpoint_are_refused(self) -> None:
        self.assert_error('lora_export_source_missing', source='lora_output/missing')
        for name in ('final-link', 'checkpoint-link'):
            output = self.engine / 'lora_output' / name
            output.mkdir()
            if name == 'final-link':
                (output / 'final').symlink_to(self.source, target_is_directory=True)
            else:
                (output / 'checkpoints').mkdir()
                (output / 'checkpoints' / 'epoch_10').symlink_to(self.source, target_is_directory=True)
            with self.subTest(name=name):
                self.assert_error('lora_export_source_unsafe', source=f'lora_output/{name}')

    def test_destination_must_be_new_absolute_with_existing_parent_outside_protected_roots(self) -> None:
        for destination in (Path('relative/export'), self.root / 'missing-parent' / 'export', self.engine / 'export', self.library / 'export', self.root / 'a' / '..' / 'export'):
            with self.subTest(destination=destination):
                self.assert_error('lora_export_destination_unsafe', destination)
        for protected in (self.engine, self.library):
            with self.subTest(protected=protected):
                with patch.object(lora_export, '_protected_roots', return_value=[protected / 'not-created']):
                    self.assert_error('lora_export_destination_unsafe', protected)

    def test_destination_parent_symlink_cannot_bypass_protected_roots(self) -> None:
        parent = self.root / 'linked-library'
        parent.symlink_to(self.library, target_is_directory=True)
        self.assert_error('lora_export_destination_unsafe', parent / 'new-export')
        self.assertFalse((self.library / 'new-export').exists())

    def test_destination_race_does_not_overwrite_the_winner(self) -> None:
        original_copy = lora_export._copy_tree
        def race(source: Path, destination: Path, **kwargs: object) -> str:
            destination.mkdir()
            (destination / 'precious.wav').write_bytes(b'concurrent user file')
            return original_copy(source, destination, **kwargs)
        with patch.object(lora_export, '_copy_tree', side_effect=race):
            self.assert_error('lora_export_destination_exists')
        self.assertEqual((self.destination / 'precious.wav').read_bytes(), b'concurrent user file')

    def test_partial_copy_failure_preserves_sources_and_never_cleans_existing_data(self) -> None:
        def fail(source: Path, destination: Path, **kwargs: object) -> str:
            destination.mkdir()
            (destination / 'partial.txt').write_text('partial', encoding='utf-8')
            raise OSError('private implementation details')
        with patch.object(lora_export, '_copy_tree', side_effect=fail), self.assertLogs(lora_export.logger, level='ERROR'):
            self.assert_error('lora_export_copy_failed')
        self.assertEqual((self.destination / 'partial.txt').read_text(), 'partial')
        self.assertEqual((self.source / 'adapter' / 'adapter_model.safetensors').read_bytes(), b'final')

    def test_exclusive_file_copy_refuses_racing_existing_file(self) -> None:
        existing = self.root / 'existing.safetensors'
        existing.write_bytes(b'preserve')
        with self.assertRaises(FileExistsError):
            lora_export._copy_file(self.source / 'adapter' / 'adapter_model.safetensors', existing)
        self.assertEqual(existing.read_bytes(), b'preserve')

    def test_exclusive_file_copy_refuses_source_symlink_created_after_validation(self) -> None:
        linked = self.root / 'linked-weight'
        linked.symlink_to(self.source / 'adapter' / 'adapter_model.safetensors')
        target = self.root / 'weight-copy'
        with self.assertRaises(lora_export.LoraExportError):
            lora_export._copy_file(linked, target)
        self.assertFalse(target.exists())

    def test_destination_directory_swap_cannot_write_through_symlink_or_change_existing_permissions(self) -> None:
        outside = self.root / 'unrelated-existing-folder'
        outside.mkdir()
        sentinel = outside / 'sentinel.wav'
        sentinel.write_bytes(b'precious')
        outside.chmod(0o711)
        mode = stat.S_IMODE(outside.stat().st_mode)
        original_copy = lora_export._copy_file
        swapped = False
        def swap(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> str:
            nonlocal swapped
            parent = Path(destination).parent
            if parent.name == 'adapter' and not swapped:
                parent.rmdir()
                parent.symlink_to(outside, target_is_directory=True)
                swapped = True
            return original_copy(source, destination)
        with patch.object(lora_export, '_copy_file', side_effect=swap):
            self.assert_error('lora_export_destination_unsafe')
        self.assertTrue(swapped)
        self.assertEqual(list(outside.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b'precious')
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), mode)

    def test_racing_file_inside_new_export_is_never_overwritten(self) -> None:
        original_copy = lora_export._copy_file
        winner: Path | None = None
        def race(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> str:
            nonlocal winner
            if winner is None:
                winner = Path(destination)
                winner.write_bytes(b'concurrent user data')
            return original_copy(source, destination)
        with patch.object(lora_export, '_copy_file', side_effect=race):
            self.assert_error('lora_export_destination_exists')
        self.assertIsNotNone(winner)
        if winner is not None:
            self.assertEqual(winner.read_bytes(), b'concurrent user data')

    def test_trained_file_check_prefers_final_and_never_writes_or_copies(self) -> None:
        self.adapter(self.output / 'checkpoints' / 'epoch_100', b'checkpoint')
        before = {path.relative_to(self.root): path.stat().st_mtime_ns for path in self.root.rglob('*')}
        with patch.object(lora_export, '_copy_tree') as copy, patch.object(Path, 'mkdir') as mkdir:
            result = lora_export.check_trained_files(LoraTrainedFilesRequest(lora_output_dir='./lora_output/run-one'))
        self.assertIsNotNone(result.checkpoint)
        if result.checkpoint is not None:
            self.assertEqual(result.checkpoint.source, str(self.source))
            self.assertEqual(result.checkpoint.adapter_path, str(self.source / 'adapter'))
            self.assertEqual(result.checkpoint.kind, 'final')
        copy.assert_not_called()
        mkdir.assert_not_called()
        self.assertEqual(before, {path.relative_to(self.root): path.stat().st_mtime_ns for path in self.root.rglob('*')})

    def test_trained_file_check_selects_latest_numeric_checkpoint_with_vendor_suffix(self) -> None:
        output = self.engine / 'custom-training' / 'interrupted'
        self.adapter(output / 'checkpoints' / 'epoch_9', b'old')
        self.adapter(output / 'checkpoints' / 'epoch_100_loss_0.2500', b'latest')
        self.adapter(output / 'checkpoints' / 'epoch_200_loss_bad', b'ignore')
        result = lora_export.check_trained_files(LoraTrainedFilesRequest(lora_output_dir='custom-training/interrupted'))
        self.assertIsNotNone(result.checkpoint)
        if result.checkpoint is not None:
            self.assertEqual(result.checkpoint.source, str(output / 'checkpoints' / 'epoch_100_loss_0.2500'))
            self.assertEqual(result.checkpoint.kind, 'checkpoint')

    def test_trained_file_check_returns_none_for_missing_or_incomplete_adapter(self) -> None:
        request = LoraTrainedFilesRequest(lora_output_dir='lora_output/missing')
        self.assertIsNone(lora_export.check_trained_files(request).checkpoint)
        self.assertFalse((self.engine / 'lora_output' / 'missing').exists())
        (self.source / 'adapter' / 'adapter_model.safetensors').unlink()
        result = lora_export.check_trained_files(LoraTrainedFilesRequest(lora_output_dir='lora_output/run-one'))
        self.assertIsNone(result.checkpoint)

    def test_trained_file_check_finds_complete_checkpoint_after_incomplete_final(self) -> None:
        (self.source / 'adapter' / 'adapter_model.safetensors').unlink()
        checkpoint = self.output / 'checkpoints' / 'epoch_100_loss_0.2500'
        self.adapter(checkpoint, b'checkpoint')
        result = lora_export.check_trained_files(LoraTrainedFilesRequest(lora_output_dir='lora_output/run-one'))
        self.assertIsNotNone(result.checkpoint)
        if result.checkpoint is not None:
            self.assertEqual(result.checkpoint.source, str(checkpoint))
            self.assertEqual(result.checkpoint.kind, 'checkpoint')

    def test_trained_file_check_skips_incomplete_newest_checkpoint_for_complete_older_checkpoint(self) -> None:
        output = self.engine / 'custom-training' / 'interrupted'
        checkpoint = output / 'checkpoints' / 'epoch_9_loss_0.5000'
        self.adapter(checkpoint, b'complete')
        self.adapter(output / 'checkpoints' / 'epoch_100_loss_0.2500', b'incomplete')
        (output / 'checkpoints' / 'epoch_100_loss_0.2500' / 'adapter' / 'adapter_config.json').unlink()
        result = lora_export.check_trained_files(LoraTrainedFilesRequest(lora_output_dir='custom-training/interrupted'))
        self.assertIsNotNone(result.checkpoint)
        if result.checkpoint is not None:
            self.assertEqual(result.checkpoint.source, str(checkpoint))
        exported = lora_export.export_lora(self.body(source='custom-training/interrupted'))
        self.assertEqual(exported.source, str(checkpoint))

    def test_fallback_never_skips_unsafe_final_or_checkpoint_candidates(self) -> None:
        (self.source / 'adapter' / 'adapter_model.safetensors').unlink()
        self.adapter(self.output / 'checkpoints' / 'epoch_20', b'complete')
        (self.source / 'private-link').symlink_to(self.root, target_is_directory=True)
        self.assert_error('lora_export_source_unsafe')
        for kind in ('directory-link', 'dangling-link', 'file', 'unsafe-adapter'):
            relative = f'lora_output/{kind}'
            checkpoints = self.engine / relative / 'checkpoints'
            self.adapter(checkpoints / 'epoch_9', b'complete')
            newest = checkpoints / 'epoch_100_loss_0.2500'
            if kind in {'directory-link', 'dangling-link'}:
                newest.symlink_to(self.source if kind == 'directory-link' else self.root / 'absent', target_is_directory=True)
            elif kind == 'file':
                newest.write_bytes(b'not a checkpoint directory')
            else:
                self.adapter(newest, b'incomplete')
                (newest / 'adapter' / 'adapter_model.safetensors').unlink()
                (newest / 'adapter' / 'private-link').symlink_to(self.root, target_is_directory=True)
            with self.subTest(kind=kind), self.assertRaises(lora_export.LoraExportError) as error:
                lora_export.check_trained_files(LoraTrainedFilesRequest(lora_output_dir=relative))
            self.assertEqual(error.exception.code, 'lora_export_source_unsafe')
            self.assertFalse(self.destination.exists())

    def test_only_incomplete_checkpoints_produce_no_match_and_stable_export_error(self) -> None:
        output = self.engine / 'lora_output' / 'incomplete-checkpoint'
        self.adapter(output / 'checkpoints' / 'epoch_9', b'incomplete')
        (output / 'checkpoints' / 'epoch_9' / 'adapter' / 'adapter_model.safetensors').unlink()
        relative = 'lora_output/incomplete-checkpoint'
        self.assertIsNone(lora_export.check_trained_files(LoraTrainedFilesRequest(lora_output_dir=relative)).checkpoint)
        self.assert_error('lora_export_adapter_missing', source=relative)

    def test_candidate_directory_read_failure_is_not_treated_as_missing_or_complete(self) -> None:
        request = LoraTrainedFilesRequest(lora_output_dir='lora_output/run-one')
        with patch.object(lora_export.os, 'scandir', side_effect=PermissionError('private directory unreadable')), \
             self.assertLogs(lora_export.logger, level='ERROR'), self.assertRaises(lora_export.LoraExportError) as error:
            lora_export.check_trained_files(request)
        self.assertEqual(error.exception.code, 'lora_export_storage_unavailable')

    def test_trained_file_check_rejects_unsafe_paths_and_symlinks(self) -> None:
        for source in ('.', './', '../lora_output/run-one', str(self.output), 'C:\\private',
                       'lora_output/../../private', 'lora_output\\run-one', 'lora_output/run\x00one'):
            with self.subTest(source=source), self.assertRaises(lora_export.LoraExportError) as error:
                lora_export.check_trained_files(LoraTrainedFilesRequest(lora_output_dir=source))
            self.assertEqual(error.exception.code, 'lora_export_source_unsafe')
        for relative, target in (('linked-output', self.output), ('linked-final/final', self.source),
                                 ('linked-checkpoints/checkpoints', self.output),
                                 ('linked-checkpoint/checkpoints/epoch_10', self.source)):
            link = self.engine / relative
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target, target_is_directory=True)
            request = LoraTrainedFilesRequest(lora_output_dir=relative.split('/')[0])
            with self.subTest(relative=relative), self.assertRaises(lora_export.LoraExportError) as error:
                lora_export.check_trained_files(request)
            self.assertEqual(error.exception.code, 'lora_export_source_unsafe')
        (self.source / 'private-link').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(lora_export.LoraExportError) as error:
            lora_export.check_trained_files(LoraTrainedFilesRequest(lora_output_dir='lora_output/run-one'))
        self.assertEqual(error.exception.code, 'lora_export_source_unsafe')

    def test_trained_file_check_sanitizes_storage_errors_and_handles_disappearing_files(self) -> None:
        request = LoraTrainedFilesRequest(lora_output_dir='lora_output/run-one')
        with patch.object(lora_export, '_source', side_effect=PermissionError('private path and exception')), \
             self.assertLogs(lora_export.logger, level='ERROR'), self.assertRaises(lora_export.LoraExportError) as error:
            lora_export.check_trained_files(request)
        self.assertEqual(error.exception.code, 'lora_export_storage_unavailable')
        self.assertEqual(str(error.exception), 'lora_export_storage_unavailable')
        with patch.object(lora_export, '_source', side_effect=FileNotFoundError('removed while checking')):
            self.assertIsNone(lora_export.check_trained_files(request).checkpoint)

    def test_trained_file_contract_bounds_paths_and_checkpoint_kind(self) -> None:
        for data in ({'source': '', 'adapter_path': '/adapter', 'kind': 'final'},
                     {'source': 'x' * 4097, 'adapter_path': '/adapter', 'kind': 'final'},
                     {'source': '/source', 'adapter_path': 'x' * 4097, 'kind': 'checkpoint'},
                     {'source': '/source', 'adapter_path': '/adapter', 'kind': 'unknown'}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                LoraTrainedCheckpoint.model_validate(data)
        self.assertEqual(LoraTrainedFilesResponse(checkpoint=None).model_dump(), {'checkpoint': None})
