"""Source evidence is distinct from installed bytes and executed model quality."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import song_capabilities
from app.module_catalog import ModuleEnvironment, engine_python
from app.module_music_models import ModelArtifact
from app.music_queue_contracts import MusicQueueError


class AceCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_installed_source_and_exact_weight_proof_are_separate_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = ModuleEnvironment.for_root(Path(directory), platform='darwin', architecture='arm64')
            root = env.paths['ace_step']
            python = engine_python(root, env.platform)
            python.parent.mkdir(parents=True)
            python.write_bytes(b'fixture-python')
            source = root / 'acestep/inference.py'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'fixture-source')
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            weights = root / 'checkpoints/acestep-v15-base/model.safetensors'
            weights.parent.mkdir(parents=True)
            weights.write_bytes(b'verified-weights')
            artifact = ModelArtifact(path='checkpoints/acestep-v15-base/model.safetensors',
                url='https://huggingface.co/test/base/resolve/' + 'a' * 40 + '/model.safetensors',
                sha256=hashlib.sha256(weights.read_bytes()).hexdigest(), bytes=weights.stat().st_size)
            with patch.object(song_capabilities, 'SOURCE_FILES', {'acestep/inference.py': (source_hash,)}), \
                 patch.object(song_capabilities, 'ace_conditioning_artifacts', return_value=[artifact]):
                state = song_capabilities.capabilities(env)
                self.assertTrue(all(row.ready and not row.runtime_verified for row in state.modes))
                proof = await song_capabilities.verify_runtime(env)
                self.assertEqual(proof.model_sha256, artifact.sha256)
                weights.write_bytes(b'corrupt!-weights')
                with self.assertRaises(MusicQueueError) as error:
                    await song_capabilities.verify_runtime(env)
                self.assertEqual(error.exception.code, 'conditioning_model_changed')
                source.write_bytes(b'changed-source')
                self.assertFalse(song_capabilities.capabilities(env).modes[0].ready)
                with self.assertRaises(MusicQueueError) as error:
                    await song_capabilities.verify_runtime(env)
                self.assertEqual(error.exception.code, 'conditioning_runtime_changed')
