from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave
import zipfile

from app.music_training_contracts import MusicLoraTrainingRequest
from app import music_training_package
from app.music_training_package import prepare_package


class MusicTrainingPackageTests(unittest.TestCase):
    def audio(self, root: Path, seconds: int = 1) -> Path:
        source = root / 'original.wav'
        with wave.open(str(source), 'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(8000)
            output.writeframes(b'\0\0' * 8000 * seconds)
        return source

    def request(self, engine: str = 'heartmula', lyrics: str = '') -> MusicLoraTrainingRequest:
        return MusicLoraTrainingRequest.model_validate({'engine': engine, 'name': 'test',
            'samples': [{'track_id': 1, 'caption': 'synth', 'lyrics': lyrics}], 'max_steps': 1, 'segment_seconds': 6})

    def test_packages_originals_pins_and_offline_check_without_models_or_private_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve();source = self.audio(root);original = source.read_bytes()
            target = prepare_package(self.request(), [source], root / 'package.zip')
            extracted = root / 'extracted';extracted.mkdir()
            with zipfile.ZipFile(target) as archive:
                names = archive.namelist()
                self.assertIn('LICENSE', names)
                self.assertIn('NOTICE', names)
                self.assertIn('backend/scripts/heart_lora_worker.py', names)
                self.assertFalse(any(name.endswith('.safetensors') or '.env' in name for name in names))
                manifest = json.loads(archive.read('manifest.json'))
                self.assertEqual(manifest['nvidia_validation'], 'unverified')
                self.assertEqual(manifest['request']['engine'], 'heartmula')
                self.assertGreater(sum(item['bytes'] for item in manifest['assets']), 20_000_000_000)
                self.assertEqual(archive.read(manifest['sources'][0]['path']), original)
                self.assertNotIn(str(root), archive.read('manifest.json').decode())
                archive.extractall(extracted)
            result = subprocess.run([sys.executable, str(extracted / 'bootstrap.py'), '--check'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Package files verified', result.stdout)
            self.assertFalse((extracted / 'runtime').exists())
            self.assertEqual(source.read_bytes(), original)
            original_manifest = (extracted / 'manifest.json').read_bytes()
            manifest['request']['max_steps'] = 2
            (extracted / 'manifest.json').write_text(json.dumps(manifest))
            result = subprocess.run([sys.executable, str(extracted / 'bootstrap.py'), '--check'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('package_request_integrity', result.stderr)
            (extracted / 'manifest.json').write_bytes(original_manifest)
            helper = extracted / 'backend/scripts/stable_lora_worker.py'
            original_helper = helper.read_bytes()
            helper.write_text('raise RuntimeError("unverified code executed")')
            result = subprocess.run([sys.executable, str(extracted / 'bootstrap.py'), '--check'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('package_file_integrity', result.stderr)
            self.assertNotIn('unverified code executed', result.stderr)
            helper.write_bytes(original_helper)
            (extracted / 'audio/000.wav').write_bytes(b'changed')
            result = subprocess.run([sys.executable, str(extracted / 'bootstrap.py'), '--check'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_yue_bundle_contains_complete_lightweight_worker_imports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve();source = self.audio(root)
            target = prepare_package(self.request('yue2'), [source], root / 'package.zip')
            extracted = root / 'extracted';extracted.mkdir()
            with zipfile.ZipFile(target) as archive:
                archive.extractall(extracted)
            result = subprocess.run([sys.executable, str(extracted / 'backend/scripts/yue_lora_worker.py'), '--help'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_installed_desktop_layout_uses_resources_licenses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve();source = self.audio(root)
            backend = root / 'resources/backend';licenses = root / 'resources/licenses'
            original_backend = music_training_package._BACKEND
            for name in music_training_package._COMMON + music_training_package._HEART + ('scripts/music_training_package_bootstrap.py',):
                target = backend / name;target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original_backend / name, target)
            licenses.mkdir(parents=True)
            for name in ('LICENSE', 'LICENSE-MIT', 'NOTICE'):
                shutil.copyfile(original_backend.parent / name, licenses / name)
            with patch.object(music_training_package, '_BACKEND', backend), patch.object(music_training_package, '_REPOSITORY', backend.parent):
                target = prepare_package(self.request(), [source], root / 'package.zip')
            with zipfile.ZipFile(target) as archive:
                for name in ('LICENSE', 'LICENSE-MIT', 'NOTICE'):
                    self.assertEqual(archive.read(name), (licenses / name).read_bytes())

    def test_rejects_existing_destination_symlinks_and_misaligned_lyrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve();source = self.audio(root, 7)
            target = root / 'package.zip';target.write_bytes(b'keep')
            with self.assertRaises((ValueError, FileExistsError)):
                prepare_package(self.request(), [source], target)
            self.assertEqual(target.read_bytes(), b'keep')
            with self.assertRaisesRegex(ValueError, 'lyrics'):
                prepare_package(self.request(lyrics='full song words'), [source], root / 'other.zip')
            try:
                (root / 'link.wav').symlink_to(source)
            except OSError:
                return
            with self.assertRaises(ValueError):
                prepare_package(self.request(), [root / 'link.wav'], root / 'other.zip')

    def test_interrupted_archive_is_never_published(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve();source = self.audio(root)
            with patch('zipfile.ZipFile.write', side_effect=OSError('interrupted')), self.assertRaises(OSError):
                prepare_package(self.request(), [source], root / 'package.zip')
            self.assertFalse((root / 'package.zip').exists())
            self.assertFalse(any(path.name.endswith('.part') for path in root.iterdir()))
