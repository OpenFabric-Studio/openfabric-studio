from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app import module_catalog as catalog
from app.module_contracts import ModulePlanRequest
from app.module_runtime import RuntimeEvidence


class ModuleCatalogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = catalog.ModuleEnvironment.for_root(self.root, platform='linux', architecture='x86_64')

    async def test_platform_support_and_dependencies_are_feature_specific(self) -> None:
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))):
            inventory = await catalog.inventory(self.environment)
        modules = {module.id: module for module in inventory.modules}
        self.assertEqual(modules['video'].state, 'unsupported')
        self.assertTrue(modules['ebooks'].supported)
        self.assertIn('media', modules['speech'].dependencies)
        self.assertEqual(modules['yue2'].automation, 'manual')

    async def test_existing_engine_folders_and_http_do_not_mean_ready(self) -> None:
        engine = self.environment.paths['speech']
        (engine / 'GPT_SoVITS').mkdir(parents=True)
        (engine / 'api.py').write_text('# vendor source', encoding='utf-8')
        python = catalog.engine_python(engine, self.environment.platform)
        python.parent.mkdir(parents=True)
        python.write_text('incomplete environment', encoding='utf-8')
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))):
            inventory = await catalog.inventory(self.environment)
        speech = next(module for module in inventory.modules if module.id == 'speech')
        self.assertEqual(speech.state, 'partial')
        self.assertTrue(any(item.code == 'inference_unverified' for item in speech.evidence))

    async def test_inventory_is_read_only_and_never_imports_gpu_models(self) -> None:
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))):
            await catalog.inventory(self.environment)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertNotIn('torch', sys.modules)

    async def test_external_installations_are_reviewed_without_automatic_mutation(self) -> None:
        external = self.root / 'outside-managed-layout'
        external.mkdir()
        (external / 'private.txt').write_text('keep', encoding='utf-8')
        paths = dict(self.environment.paths)
        paths['ace_step'] = external
        environment = catalog.ModuleEnvironment.for_root(self.root, platform='darwin', architecture='arm64', paths=paths)
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))):
            plan = await catalog.plan(ModulePlanRequest(features=['ace_step'], download_models=True), environment)
        ace = next(step for step in plan.steps if step.module_id == 'ace_step')
        self.assertEqual(ace.operation, 'manual')
        self.assertEqual((external / 'private.txt').read_text(), 'keep')

    async def test_plan_expands_dependencies_once_and_binds_download_consent(self) -> None:
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))):
            first = await catalog.plan(ModulePlanRequest(features=['speech', 'ebooks']), self.environment)
            second = await catalog.plan(ModulePlanRequest(features=['speech', 'ebooks'], download_models=True), self.environment)
        self.assertEqual([step.module_id for step in first.steps], ['media', 'speech', 'ebooks'])
        self.assertNotEqual(first.plan_token, second.plan_token)
        self.assertTrue(any(step.operation == 'manual' for step in first.steps))

    async def test_media_requires_both_real_tools(self) -> None:
        async def only_ffmpeg(argv: list[str], **_kwargs: object) -> catalog.ProbeResult:
            return catalog.ProbeResult(True, 'ffmpeg version 9.0.1') if Path(argv[0]).name == 'ffmpeg' else catalog.ProbeResult(False, '')
        with patch.object(catalog, 'probe', only_ffmpeg):
            inventory = await catalog.inventory(self.environment)
        media = next(module for module in inventory.modules if module.id == 'media')
        self.assertNotEqual(media.state, 'ready')

    async def test_probe_is_bounded_on_output_overflow_and_timeout(self) -> None:
        overflow = await catalog.probe([sys.executable, '-c', 'import sys,time;sys.stdout.write("x"*100000);sys.stdout.flush();time.sleep(10)'], timeout=1, max_bytes=1024)
        timed_out = await catalog.probe([sys.executable, '-c', 'import time;time.sleep(10)'], timeout=0.1)
        self.assertFalse(overflow.ok)
        self.assertFalse(timed_out.ok)

    async def test_source_install_plan_exposes_missing_git_before_downloads(self) -> None:
        environment = replace(self.environment, uv='/fixed/uv')
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))), patch.object(catalog.shutil, 'which', return_value=None):
            reviewed = await catalog.plan(ModulePlanRequest(features=['speech']), environment)
        speech = next(step for step in reviewed.steps if step.module_id == 'speech')
        self.assertEqual(speech.operation, 'manual')
        self.assertTrue(any('Git' in action.detail for action in speech.actions))

    async def test_external_environment_can_be_verified_without_replacement(self) -> None:
        engine = self.root / 'external-speech'
        (engine / 'GPT_SoVITS').mkdir(parents=True)
        (engine / 'api.py').write_text('# source')
        python = catalog.engine_python(engine, 'linux')
        python.parent.mkdir(parents=True)
        python.write_text('# interpreter')
        paths = dict(self.environment.paths)
        paths['speech'] = engine
        environment = replace(self.environment, paths=paths)
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))):
            reviewed = await catalog.plan(ModulePlanRequest(features=['speech']), environment)
        self.assertEqual(next(step.operation for step in reviewed.steps if step.module_id == 'speech'), 'verify')

    async def test_verified_existing_runtime_is_ready_with_model_evidence(self) -> None:
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))), patch.object(catalog, '_runtime_inventory', AsyncMock(return_value={'ace_step': RuntimeEvidence(ready=True, installed=True, capabilities=('music_generation_models',))})):
            status = await catalog.inventory(self.environment)
        ace = next(module for module in status.modules if module.id == 'ace_step')
        self.assertEqual(ace.state, 'ready')
        self.assertIn('music_generation_models', ace.capabilities)

    async def test_download_counts_use_manifest_and_dependencies_remain_unknown(self) -> None:
        environment = replace(self.environment, platform='darwin', architecture='arm64', uv='/fixed/uv')
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))), patch.object(catalog.shutil, 'which', return_value='/fixed/tool'):
            reviewed = await catalog.plan(ModulePlanRequest(features=['speech']), environment)
        from app.module_install import load_manifest
        asset = load_manifest().ffmpeg.assets['darwin-arm64']
        self.assertIsNotNone(asset.ffprobe)
        self.assertEqual(reviewed.steps[0].estimated_download_bytes, asset.bytes + (asset.ffprobe.bytes if asset.ffprobe else 0))
        self.assertIsNone(reviewed.steps[1].estimated_download_bytes)
        self.assertTrue(reviewed.download_size_unknown)
        self.assertGreater(reviewed.required_free_bytes, reviewed.estimated_download_bytes)


if __name__ == '__main__':
    unittest.main()
