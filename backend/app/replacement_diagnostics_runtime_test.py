from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app.module_catalog import ModuleEnvironment, engine_python
from app import replacement_diagnostics_runtime as runtime


class PitchRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.environment = ModuleEnvironment.for_root(self.root, platform='darwin', architecture='arm64')

    def interpreter(self, engine: str) -> Path:
        from pydantic import TypeAdapter
        from app.module_contracts import ModuleId
        identifier = TypeAdapter(ModuleId).validate_python(engine)
        path = engine_python(self.environment.paths[identifier], self.environment.platform)
        path.parent.mkdir(parents=True)
        path.symlink_to(sys.executable)
        return path

    async def test_compatible_backend_wins_without_reading_engine_environments(self) -> None:
        run = AsyncMock(return_value=b'{"available":true}')
        with patch.object(runtime, 'configured_environment') as configured:
            result = await runtime.pitch_python(run)
        self.assertEqual(result, Path(sys.executable))
        self.assertEqual(run.await_count, 1)
        self.assertEqual(run.call_args.args[0][1:3], ['-I', '-c'])
        configured.assert_not_called()

    async def test_verified_seed_then_rvc_only_and_venv_symlink_is_not_resolved(self) -> None:
        seed, rvc = self.interpreter('singing'), self.interpreter('rvc')
        seen: list[str] = []
        async def run(argv: list[str], timeout: float) -> bytes:
            seen.append(argv[0]); self.assertLessEqual(timeout, 10)
            return b'{"available":true}' if argv[0] == str(seed) else b'{"available":false}'
        with patch.object(runtime, 'configured_environment', return_value=self.environment), \
                patch.object(runtime, 'environment_verified', return_value=True):
            result = await runtime.pitch_python(run)
        self.assertEqual(result, seed)
        self.assertNotEqual(result, seed.resolve())
        self.assertEqual(seen, [sys.executable, str(seed)])
        self.assertNotIn(str(rvc), seen)

    async def test_unverified_or_incompatible_managed_environment_is_not_selected(self) -> None:
        seed, rvc = self.interpreter('singing'), self.interpreter('rvc')
        seen: list[str] = []
        async def run(argv: list[str], timeout: float) -> bytes:
            seen.append(argv[0]); return b'{"available":false}'
        with patch.object(runtime, 'configured_environment', return_value=self.environment), \
                patch.object(runtime, 'environment_verified', side_effect=lambda env, identifier: identifier == 'rvc'):
            result = await runtime.pitch_python(run)
        self.assertIsNone(result)
        self.assertEqual(seen, [sys.executable, str(rvc)])
        self.assertNotIn(str(seed), seen)

    async def test_no_managed_runtime_and_malformed_probe_never_claim_availability(self) -> None:
        for body in (b'{"available":false}', b'{"available":"true"}', b'{"available":1}',
                     b'{"available":true,"extra":1}', b'not-json', b'x' * 1025):
            with self.subTest(body=body[:60]), patch.object(runtime, 'configured_environment', return_value=self.environment), \
                    patch.object(runtime, 'environment_verified', return_value=False):
                self.assertIsNone(await runtime.pitch_python(AsyncMock(return_value=body)))

    async def test_cancel_or_uncertain_callback_cleanup_failure_propagates(self) -> None:
        for error in (asyncio.CancelledError(), RuntimeError('cleanup_uncertain')):
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                await runtime.pitch_python(AsyncMock(side_effect=error))

    async def test_stdlib_probe_does_not_import_dsp_or_model_packages(self) -> None:
        code = runtime.PROBE_SCRIPT + '\nimport sys; print([name for name in ("numpy","scipy","librosa","soundfile","torch","transformers") if name in sys.modules])'
        completed = await asyncio.to_thread(subprocess.run, [sys.executable, '-I', '-c', code],
            capture_output=True, text=True, timeout=10, check=True)
        lines = completed.stdout.splitlines()
        self.assertEqual(json.loads(lines[0]), {'available': True})
        self.assertEqual(lines[1], '[]')

    async def test_probe_refuses_missing_rvc_pydantic_and_unsupported_versions(self) -> None:
        for pydantic, librosa in ((None, '0.11.0'), ('1.10.0', '0.11.0'), ('2.12.5', '0.11.0'),
                                  ('3.0.0', '0.11.0'), ('2.13.5', '0.12.0'), ('2.13.0rc1', '0.11.0')):
            prelude = ('import importlib.util,importlib.metadata\n'
                'from unittest.mock import patch\n'
                f'versions={{"pydantic":{pydantic!r},"librosa":{librosa!r},"numpy":"2.2.6","scipy":"1.18.1","soundfile":"0.13.1"}}\n'
                'def version(name):\n'
                ' value=versions[name]\n'
                ' if value is None: raise importlib.metadata.PackageNotFoundError(name)\n'
                ' return value\n'
                'with patch.object(importlib.metadata,"version",side_effect=version):\n'
                f' exec({runtime.PROBE_SCRIPT!r})\n')
            completed = await asyncio.to_thread(subprocess.run, [sys.executable, '-I', '-c', prelude],
                capture_output=True, text=True, timeout=10, check=True)
            with self.subTest(pydantic=pydantic, librosa=librosa):
                self.assertEqual(json.loads(completed.stdout), {'available': False})
