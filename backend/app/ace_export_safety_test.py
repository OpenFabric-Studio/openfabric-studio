"""Native ACE export regressions using temporary files, without models or a server."""
from __future__ import annotations

import ast
import asyncio
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import Mock, patch

from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / 'external/patches/ace-step.patch'
FIXTURES = Path(__file__).parent / 'fixtures/ace-export'
SOURCES = {
    'rest': 'acestep/api/train_api_service.py',
    'lora': 'acestep/ui/gradio/events/training/lora_training.py',
    'lokr': 'acestep/ui/gradio/events/training/lokr_training.py',
}
HELPER = 'acestep/training/export_safety.py'
ENTRIES = ('rest', 'lora', 'lokr')


def _translate(key: str, **values: str) -> str:
    return key + ' ' + ' '.join(values.values())


def _response(data: object, code: int = 200, error: str | None = None) -> dict[str, object]:
    return {'data': data, 'code': code, 'error': error}


class AceExportSafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._workspace = tempfile.TemporaryDirectory(prefix='ace-export-source-')
        cls.source_root = Path(cls._workspace.name)
        for path in SOURCES.values():
            target = cls.source_root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((FIXTURES / (target.name + '.txt')).read_bytes())
        sections = ['diff --git ' + section for section in PATCH.read_text().split('diff --git ')[1:]
                    if any(section.startswith(f'a/{path} b/{path}\n') for path in (*SOURCES.values(), HELPER))]
        if sections:
            subprocess.run(['git', 'apply', '--whitespace=nowarn', '-'], input=''.join(sections),
                           text=True, cwd=cls.source_root, check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._workspace.cleanup()

    def setUp(self) -> None:
        self._files = tempfile.TemporaryDirectory(prefix='ace-export-files-')
        self.addCleanup(self._files.cleanup)
        self.directory = Path(self._files.name)
        self.output = self.directory / 'training'
        self.final = self.output / 'final'
        self.final.mkdir(parents=True)
        (self.final / 'adapter').mkdir()
        (self.final / 'adapter/adapter_model.safetensors').write_bytes(b'lora-original')
        (self.final / 'lokr_weights.safetensors').write_bytes(b'lokr-original')
        (self.output / 'training-metadata.json').write_bytes(b'keep-training-metadata')
        self.logger = Mock()

    def _export(self, entry: str, destination: Path | str) -> object:
        namespace: dict[str, object] = {
            'os': os, 'shutil': shutil, 'Path': Path, 'Optional': Optional,
            'logger': self.logger, 't': _translate, 'HTTPException': HTTPException,
            'ExportLoRARequest': SimpleNamespace, 'wrap_response': _response,
        }
        sanitizer = ast.parse((FIXTURES / 'path_safety.py.txt').read_text())
        helpers = [node for node in sanitizer.body if isinstance(node, ast.FunctionDef)]
        exec(compile(ast.Module(body=helpers, type_ignores=[]), '<ace-path-safety>', 'exec'), namespace)
        namespace['_SAFE_ROOT'] = str(self.directory.resolve())
        helper = self.source_root / HELPER
        if helper.exists():
            exec(compile(helper.read_text(), '<patched-ace-export-safety>', 'exec'), namespace)
        module = ast.parse((self.source_root / SOURCES[entry]).read_text())
        body = module.body
        name = 'export_lokr' if entry == 'lokr' else 'export_lora'
        if entry == 'rest':
            registration = next(node for node in body if isinstance(node, ast.FunctionDef)
                                and node.name == 'register_training_api_routes')
            body = registration.body
        function = next(node for node in body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name == name)
        function.decorator_list = []
        if entry == 'rest':
            function.args.defaults = [ast.Constant(None)]
        exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                     '<patched-native-export>', 'exec'), namespace)
        operation = namespace[name]
        if not callable(operation):
            raise AssertionError('export fixture does not contain a callable')
        if entry == 'rest':
            return asyncio.run(operation(SimpleNamespace(export_path=str(destination), lora_output_dir=str(self.output))))
        return operation(str(destination), str(self.output))

    def _assert_rejected(self, result: object) -> None:
        if isinstance(result, dict):
            self.assertNotEqual(result.get('code'), 200, result)
        else:
            self.assertIsInstance(result, str)
            self.assertNotIn('training.lora_exported', str(result))
            self.assertNotIn('training.lokr_exported', str(result))

    def _assert_source_preserved(self) -> None:
        self.assertEqual((self.final / 'adapter/adapter_model.safetensors').read_bytes(), b'lora-original')
        self.assertEqual((self.final / 'lokr_weights.safetensors').read_bytes(), b'lokr-original')
        self.assertEqual((self.output / 'training-metadata.json').read_bytes(), b'keep-training-metadata')

    def test_existing_directory_contents_are_never_deleted_or_merged(self) -> None:
        for entry in ENTRIES:
            with self.subTest(entry=entry):
                destination = self.directory / f'existing-{entry}'
                destination.mkdir()
                sentinel = destination / 'unrelated-document.txt'
                sentinel.write_bytes(b'keep-unrelated-data')
                self._assert_rejected(self._export(entry, destination))
                self.assertEqual(sentinel.read_bytes(), b'keep-unrelated-data')
                self.assertEqual(list(destination.iterdir()), [sentinel])
                self._assert_source_preserved()

    def test_existing_files_and_dangling_symlinks_are_refused(self) -> None:
        for entry in ENTRIES:
            for kind in ('file', 'symlink'):
                with self.subTest(entry=entry, kind=kind):
                    destination = self.directory / f'{entry}-{kind}.safetensors'
                    target = self.directory / f'{entry}-missing-target'
                    if kind == 'file':
                        destination.write_bytes(b'keep-existing-file')
                    else:
                        destination.symlink_to(target)
                    self._assert_rejected(self._export(entry, destination))
                    if kind == 'file':
                        self.assertEqual(destination.read_bytes(), b'keep-existing-file')
                    else:
                        self.assertTrue(destination.is_symlink())
                        self.assertFalse(target.exists())
                    self._assert_source_preserved()

    def test_training_root_and_new_descendants_are_refused(self) -> None:
        for entry in ENTRIES:
            for destination in (self.output, self.final, self.final / 'new/deep', self.output / 'new-sibling'):
                with self.subTest(entry=entry, destination=destination):
                    self._assert_rejected(self._export(entry, destination))
                    self._assert_source_preserved()
                    if destination not in (self.output, self.final):
                        self.assertFalse(destination.exists())

    def test_training_root_parent_is_refused(self) -> None:
        for entry in ENTRIES:
            with self.subTest(entry=entry):
                self._assert_rejected(self._export(entry, self.directory))
                self._assert_source_preserved()

    def test_symlinked_destination_parent_cannot_hide_training_overlap(self) -> None:
        alias = self.directory / 'training-alias'
        alias.symlink_to(self.output, target_is_directory=True)
        for entry in ENTRIES:
            with self.subTest(entry=entry):
                destination = alias / f'new-{entry}/nested'
                self._assert_rejected(self._export(entry, destination))
                self.assertFalse(destination.exists())
                self._assert_source_preserved()

    def test_blank_destination_is_refused(self) -> None:
        for entry in ENTRIES:
            with self.subTest(entry=entry):
                self._assert_rejected(self._export(entry, '  '))
                self._assert_source_preserved()

    def test_source_symlinks_cannot_export_outside_files(self) -> None:
        outside = self.directory / 'outside-secret'
        outside.write_bytes(b'do-not-export')
        link = self.final / 'linked-secret'
        link.symlink_to(outside)
        for entry in ('rest', 'lora'):
            with self.subTest(entry=entry):
                destination = self.directory / f'unsafe-source-{entry}'
                self._assert_rejected(self._export(entry, destination))
                self.assertFalse(destination.exists())
        (self.final / 'lokr_weights.safetensors').unlink()
        (self.final / 'lokr_weights.safetensors').symlink_to(outside)
        destination = self.directory / 'unsafe-source-lokr'
        self._assert_rejected(self._export('lokr', destination))
        self.assertFalse(destination.exists())
        self.assertEqual(outside.read_bytes(), b'do-not-export')

    def test_source_directory_symlink_escape_is_refused(self) -> None:
        outside = self.directory / 'outside-directory'
        self.final.rename(outside)
        self.final.symlink_to(outside, target_is_directory=True)
        for entry in ENTRIES:
            with self.subTest(entry=entry):
                destination = self.directory / f'escaped-final-{entry}'
                self._assert_rejected(self._export(entry, destination))
                self.assertFalse(destination.exists())
        self.assertEqual((outside / 'lokr_weights.safetensors').read_bytes(), b'lokr-original')

    def test_new_destination_gets_expected_weights(self) -> None:
        for entry in ENTRIES:
            with self.subTest(entry=entry):
                destination = self.directory / f'new-export-{entry}'
                result = self._export(entry, destination)
                if entry == 'rest':
                    self.assertIsInstance(result, dict)
                    self.assertEqual(result.get('code') if isinstance(result, dict) else None, 200)
                filename = 'lokr_weights.safetensors' if entry == 'lokr' else 'adapter/adapter_model.safetensors'
                self.assertEqual((destination / filename).read_bytes(), b'lokr-original' if entry == 'lokr' else b'lora-original')
                self._assert_source_preserved()
        file_destination = self.directory / 'new-file.safetensors'
        self._export('lokr', file_destination)
        self.assertEqual(file_destination.read_bytes(), b'lokr-original')

    def test_latest_checkpoint_is_exported_when_final_is_unavailable(self) -> None:
        checkpoints = self.output / 'checkpoints'
        checkpoints.mkdir(exist_ok=True)
        self.final.rename(checkpoints / 'epoch_2')
        latest = checkpoints / 'epoch_10_loss_0.25'
        shutil.copytree(checkpoints / 'epoch_2', latest)
        (latest / 'lokr_weights.safetensors').write_bytes(b'latest-lokr')
        (latest / 'adapter/adapter_model.safetensors').write_bytes(b'latest-lora')
        for entry in ENTRIES:
            with self.subTest(entry=entry):
                destination = self.directory / f'checkpoint-{entry}'
                self._export(entry, destination)
                filename = 'lokr_weights.safetensors' if entry == 'lokr' else 'adapter/adapter_model.safetensors'
                self.assertEqual((destination / filename).read_bytes(), b'latest-lokr' if entry == 'lokr' else b'latest-lora')
        self.assertEqual((checkpoints / 'epoch_2/lokr_weights.safetensors').read_bytes(), b'lokr-original')

    def test_copy_failure_keeps_partial_new_export_and_hides_internal_error(self) -> None:
        def fail_copy(reader: object, writer: object) -> None:
            if not hasattr(writer, 'write'):
                raise AssertionError('copy target is not writable')
            writer.write(b'partial')
            raise OSError('private-error-sentinel')

        for entry in ('rest', 'lora'):
            with self.subTest(entry=entry):
                destination = self.directory / f'failed-{entry}'
                with patch.object(shutil, 'copyfileobj', side_effect=fail_copy):
                    result = self._export(entry, destination)
                self._assert_rejected(result)
                self.assertNotIn('private-error-sentinel', str(result))
                self.assertTrue(any(path.is_file() and path.read_bytes() == b'partial' for path in destination.rglob('*')))
                self._assert_source_preserved()
                self.logger.exception.assert_called()

    def test_destination_created_at_directory_copy_boundary_is_preserved(self) -> None:
        original = os.mkdir

        for entry in ('rest', 'lora'):
            with self.subTest(entry=entry):
                destination = self.directory / f'raced-{entry}'

                def race_mkdir(path: str | os.PathLike[str], mode: int = 0o777, *, dir_fd: int | None = None) -> None:
                    if Path(path) == destination.resolve():
                        original(path, mode, dir_fd=dir_fd)
                        (Path(path) / 'other-export').write_bytes(b'concurrent-writer')
                    original(path, mode, dir_fd=dir_fd)

                with patch.object(os, 'mkdir', new=race_mkdir):
                    self._assert_rejected(self._export(entry, destination))
                self.assertEqual((destination / 'other-export').read_bytes(), b'concurrent-writer')
                self._assert_source_preserved()

    def test_file_created_inside_new_export_directory_is_preserved(self) -> None:
        original = os.mkdir
        for entry in ('rest', 'lora'):
            with self.subTest(entry=entry):
                destination = self.directory / f'internal-race-{entry}'

                def race_mkdir(path: str | os.PathLike[str], mode: int = 0o777, *, dir_fd: int | None = None) -> None:
                    original(path, mode, dir_fd=dir_fd)
                    if Path(path) == destination.resolve() / 'adapter':
                        (Path(path) / 'adapter_model.safetensors').write_bytes(b'concurrent-writer')

                with patch.object(os, 'mkdir', new=race_mkdir):
                    self._assert_rejected(self._export(entry, destination))
                self.assertEqual((destination / 'adapter/adapter_model.safetensors').read_bytes(), b'concurrent-writer')
                self._assert_source_preserved()

    def test_directory_symlink_substitution_preserves_outside_bytes_and_permissions(self) -> None:
        original = os.mkdir
        outside = self.directory / 'outside-existing-directory'
        outside.mkdir(mode=0o700)
        sentinel = outside / 'unrelated-document'
        sentinel.write_bytes(b'keep-outside-data')
        outside_mode = stat.S_IMODE(outside.stat().st_mode)
        for entry in ('rest', 'lora'):
            with self.subTest(entry=entry):
                destination = self.directory / f'directory-race-{entry}'

                def race_mkdir(path: str | os.PathLike[str], mode: int = 0o777, *, dir_fd: int | None = None) -> None:
                    original(path, mode, dir_fd=dir_fd)
                    if Path(path) == destination.resolve() / 'adapter':
                        Path(path).rmdir()
                        Path(path).symlink_to(outside, target_is_directory=True)

                with patch.object(os, 'mkdir', new=race_mkdir):
                    self._assert_rejected(self._export(entry, destination))
                self.assertEqual(sentinel.read_bytes(), b'keep-outside-data')
                self.assertEqual(list(outside.iterdir()), [sentinel])
                self.assertEqual(stat.S_IMODE(outside.stat().st_mode), outside_mode)
                self._assert_source_preserved()

    def test_lokr_file_created_at_copy_boundary_is_preserved(self) -> None:
        destination = self.directory / 'raced-file.safetensors'
        original = Path.open

        def race_open(path: Path, mode: str = 'r', *args: object, **kwargs: object) -> object:
            if mode == 'xb':
                with original(path, 'wb') as writer:
                    writer.write(b'concurrent-writer')
            return original(path, mode, *args, **kwargs)

        with patch.object(Path, 'open', new=race_open):
            self._assert_rejected(self._export('lokr', destination))
        self.assertEqual(destination.read_bytes(), b'concurrent-writer')
        self._assert_source_preserved()

    def test_lokr_copy_failure_retains_partial_file_and_hides_internal_error(self) -> None:
        def fail_copy(reader: object, writer: object) -> None:
            if not hasattr(writer, 'write'):
                raise AssertionError('copy target is not writable')
            writer.write(b'partial')
            raise OSError('private-error-sentinel')

        for file_destination in (False, True):
            with self.subTest(file_destination=file_destination):
                destination = self.directory / ('failed-lokr.safetensors' if file_destination else 'failed-lokr-folder')
                with patch.object(shutil, 'copyfileobj', side_effect=fail_copy):
                    result = self._export('lokr', destination)
                self._assert_rejected(result)
                self.assertNotIn('private-error-sentinel', str(result))
                output_file = destination if file_destination else destination / 'lokr_weights.safetensors'
                self.assertEqual(output_file.read_bytes(), b'partial')
                self._assert_source_preserved()
                self.logger.exception.assert_called()


if __name__ == '__main__':
    unittest.main()
