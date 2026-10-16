from __future__ import annotations

import hashlib
import json
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch
import wave

from app.music_training_contracts import MusicLoraTrainingRequest
from scripts.music_training_package_worker import cancel, run


class MusicTrainingPackageWorkerTests(unittest.TestCase):
    def test_real_worker_produces_hash_bound_completion_and_refuses_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve();directory = root / ('3' * 32);directory.mkdir()
            source = root / 'source.wav'
            with wave.open(str(source), 'wb') as output:
                output.setnchannels(1);output.setsampwidth(2);output.setframerate(8000);output.writeframes(b'\0\0' * 8000)
            request = MusicLoraTrainingRequest(engine='yue2', name='package', samples=[{'track_id': 1, 'caption': 'synth'}])
            fingerprint = hashlib.sha256(request.model_dump_json(exclude_none=True).encode()).hexdigest()
            path = directory / 'request.json'
            path.write_text(json.dumps({'request': request.model_dump(mode='json', exclude_none=True),
                'request_sha256': fingerprint, 'sources': [str(source)]}))
            run(path)
            completion = json.loads((directory / 'package-completion.json').read_text())
            self.assertEqual(completion['job_id'], directory.name)
            self.assertEqual(completion['request_sha256'], fingerprint)
            self.assertEqual(completion['archive'], 'package.zip')
            self.assertEqual(completion['sha256'], hashlib.sha256((directory / 'package.zip').read_bytes()).hexdigest())
            self.assertEqual(completion['bytes'], (directory / 'package.zip').stat().st_size)
            with self.assertRaisesRegex(ValueError, 'existing_completion'):
                run(path)

    def test_rejects_changed_request_before_copying_any_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve() / ('4' * 32);directory.mkdir()
            request = MusicLoraTrainingRequest(engine='yue2', name='package', samples=[{'track_id': 1, 'caption': 'synth'}])
            path = directory / 'request.json'
            path.write_text(json.dumps({'request': request.model_dump(mode='json', exclude_none=True),
                'request_sha256': '0' * 64, 'sources': [str(directory / 'missing.wav')]}))
            with self.assertRaisesRegex(ValueError, 'request_changed'):
                run(path)
            self.assertFalse((directory / 'package.zip').exists())

    def test_cancellation_unwinds_partial_archive(self) -> None:
        with self.assertRaises(SystemExit) as result:
            cancel(signal.SIGTERM, None)
        self.assertEqual(result.exception.code, 128 + signal.SIGTERM)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / 'source.wav'
            with wave.open(str(source), 'wb') as output:
                output.setnchannels(1);output.setsampwidth(2);output.setframerate(8000);output.writeframes(b'\0\0' * 8000)
            request = MusicLoraTrainingRequest(engine='yue2', name='package', samples=[{'track_id': 1, 'caption': 'synth'}])
            from app.music_training_package import prepare_package
            with patch('zipfile.ZipFile.write', side_effect=SystemExit(143)), self.assertRaises(SystemExit):
                prepare_package(request, [source], root / 'package.zip')
            self.assertEqual(sorted(path.name for path in root.iterdir()), ['source.wav'])
