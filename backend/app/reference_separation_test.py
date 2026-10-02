"""Existing separation adapters accept a precise caller-owned spawning boundary."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app import voice_separation as separation


class ReferenceSeparationSpawnTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = self.root / 'original.wav'
        self.source.write_bytes(b'original')
        self.engine = self.root / 'engine'
        (self.engine / 'utils').mkdir(parents=True)
        (self.engine / 'inference.py').write_text('# fixture')
        (self.engine / 'utils/settings.py').write_text('--model_type --config_path --start_check_point --input_folder --store_dir --pcm_type --filename_template')
        config = self.root / 'config.yaml'
        weights = self.root / 'weights.ckpt'
        config.write_text('fixture')
        weights.write_bytes(b'fixture')
        for name, value in [('ROFORMER_DIR', self.engine), ('ROFORMER_CONFIG', config), ('ROFORMER_CHECKPOINT', weights), ('LOG_DIR', self.root / 'logs')]:
            self.enterContext(patch.object(separation, name, value))
        self.enterContext(patch.object(separation, 'roformer_python', return_value=Path(sys.executable)))

    async def test_demucs_passes_caller_owner_through_existing_service(self) -> None:
        owner = AsyncMock()
        expected = self.root / 'vocals.wav'
        with patch.object(separation, 'separate_file', AsyncMock(return_value={'vocals': expected})) as split:
            result = await separation.separate_vocal(self.source, self.root / 'out', quality='fast', log_name='owned', spawn=owner)
        self.assertEqual(result, expected)
        self.assertIs(split.call_args.kwargs['spawn'], owner)

    async def test_roformer_uses_owner_with_exact_argv_environment_and_log_descriptor(self) -> None:
        async def owner(argv: list[str], cwd: Path, env: Mapping[str, str], stdout: int) -> asyncio.subprocess.Process:
            self.assertEqual(cwd, self.engine)
            self.assertEqual(argv[0], sys.executable)
            self.assertIn('PATH', env)
            self.assertGreaterEqual(stdout, 0)
            output = Path(argv[argv.index('--store_dir') + 1])
            (output / 'source_vocals.wav').write_bytes(b'vocal')
            process = AsyncMock(spec=asyncio.subprocess.Process)
            process.wait.return_value = 0
            return process
        with patch.object(separation, 'spawn_process', AsyncMock(side_effect=AssertionError('Unowned spawn forbidden'))):
            result = await separation.separate_vocal(self.source, self.root / 'out', quality='roformer', log_name='owned', spawn=owner)
        self.assertEqual(result.read_bytes(), b'vocal')
        self.assertEqual(self.source.read_bytes(), b'original')
