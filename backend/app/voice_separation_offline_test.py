from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import voice_separation as separation


class OfflineDemucsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.engine, self.cache = self.root / 'engine', self.root / 'cache'
        (self.engine / 'demucs/remote').mkdir(parents=True)
        self.python = self.engine / '.venv/bin/python'
        self.python.parent.mkdir(parents=True)
        self.python.write_bytes(b'python')
        (self.engine / 'demucs/pretrained.py').write_text('def get_model(name, repo=None):\n pass\n')
        (self.engine / 'demucs/repo.py').write_text('class LocalRepo: pass\n')
        (self.engine / 'demucs/separate.py').write_text('def main(): pass\n')
        (self.engine / 'demucs/remote/htdemucs.yaml').write_text("models: ['955717e8']\n")
        self.cache.mkdir()
        self.weights = self.cache / '955717e8-8726e21a.th'

    def test_source_checkout_alone_never_claims_offline_models_ready(self) -> None:
        with self.assertRaises(separation.VoiceSeparationError):
            separation.offline_demucs('fast', engine=self.engine, checkpoints=self.cache)

    def test_readiness_freezes_weights_and_builds_local_only_command(self) -> None:
        content = b'fixture weights'
        checksum = hashlib.sha256(content).hexdigest()
        with patch.object(separation, '_DEMUCS_MODELS', {'fast': ('htdemucs', (('955717e8', checksum),))}):
            self.weights.rename(self.weights) if self.weights.exists() else None
            weight = self.cache / f'955717e8-{checksum[:8]}.th'
            weight.write_bytes(content)
            ready = separation.offline_demucs('fast', engine=self.engine, checkpoints=self.cache)
            work = self.root / 'work'
            command = ready.command(self.root / 'source.wav', work)
            self.assertEqual(command[:3], [str(self.python), '-m', 'demucs'])
            self.assertIn('--repo', command)
            self.assertNotIn('uv', command)
            self.assertEqual((work / 'models' / weight.name).read_bytes(), content)
            weight.write_bytes(b'changed')
            with self.assertRaises(separation.VoiceSeparationError):
                ready.command(self.root / 'source.wav', self.root / 'retry')

    def test_symbolic_checkpoint_escape_is_not_offered(self) -> None:
        outside = self.root / 'outside.th'
        outside.write_bytes(b'fixture')
        self.weights.symlink_to(outside)
        with self.assertRaises(separation.VoiceSeparationError):
            separation.offline_demucs('fast', engine=self.engine, checkpoints=self.cache)
