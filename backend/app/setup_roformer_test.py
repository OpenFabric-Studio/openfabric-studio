"""RoFormer setup preserves dotenv and publishes only verified weights."""
from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import setup_roformer as setup


class RoformerSetupTests(unittest.TestCase):
    def test_download_verifies_size_and_hash_before_replacing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'weights.ckpt'
            destination.write_bytes(b'prior checkpoint')
            payload = b'new trusted weights'
            with patch.object(setup, 'urlopen', return_value=io.BytesIO(payload)), patch.object(setup, 'CHECKPOINT_SIZE', len(payload)), patch.object(setup, 'CHECKPOINT_SHA256', '0' * 64):
                with self.assertRaisesRegex(ValueError, 'checkpoint_integrity_failed'):
                    setup.download_checkpoint(destination)
            self.assertEqual(destination.read_bytes(), b'prior checkpoint')
            self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_existing_verified_checkpoint_needs_no_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'weights.ckpt'
            payload = b'trusted weights'
            destination.write_bytes(payload)
            with patch.object(setup, 'urlopen') as network, patch.object(setup, 'CHECKPOINT_SIZE', len(payload)), patch.object(setup, 'CHECKPOINT_SHA256', hashlib.sha256(payload).hexdigest()):
                setup.download_checkpoint(destination)
            network.assert_not_called()

    def test_configuration_changes_only_inference_settings(self) -> None:
        original = 'audio:\n  chunk_size: 352800\nmodel:\n  depth: 6\ntraining:\n  batch_size: 4\n  use_amp: true # original\ninference:\n  batch_size: 4\n  num_overlap: 2\n'
        actual = setup.inference_config(original)
        self.assertEqual(actual, original.replace('use_amp: true', 'use_amp: false').replace('inference:\n  batch_size: 4', 'inference:\n  batch_size: 1'))

    def test_atomic_text_works_without_descriptor_permissions_api(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / '.env'
            destination.write_text('previous settings')
            with patch('sys.platform', 'win32'), patch.object(setup.os, 'fchmod', side_effect=AttributeError('Unavailable on Windows'), create=True):
                setup.atomic_text(destination, 'replacement settings')
            self.assertEqual(destination.read_text(), 'replacement settings')
            self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_dotenv_update_preserves_unrelated_values_and_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = Path(temporary) / '.env'
            original = '# Existing settings\nPRIVATE_TOKEN="example-sensitive-value"\nDATA_DIR=/data\nVOICE_ROFORMER_CONFIG="old"\n'
            env.write_text(original)
            env.chmod(0o600)
            setup.configure_env(env, {'VOICE_ROFORMER_CONFIG': '/new/config.yaml', 'VOICE_ROFORMER_CHECKPOINT': '/new/model.ckpt'})
            actual = env.read_text()
            self.assertIn('# Existing settings\nPRIVATE_TOKEN="example-sensitive-value"\nDATA_DIR=/data\n', actual)
            self.assertEqual(actual.count('VOICE_ROFORMER_CONFIG='), 1)
            self.assertIn('VOICE_ROFORMER_CONFIG="/new/config.yaml"', actual)
            self.assertEqual(env.stat().st_mode & 0o777, 0o600)

    def test_dotenv_update_rejects_unrelated_keys_and_newlines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = Path(temporary) / '.env'
            for values in ({'PRIVATE_TOKEN': 'changed'}, {'VOICE_ROFORMER_CONFIG': '/path\nPRIVATE_TOKEN=changed'}):
                with self.assertRaises(ValueError):
                    setup.configure_env(env, values)
            self.assertFalse(env.exists())


if __name__ == '__main__':
    unittest.main()
