from __future__ import annotations

import asyncio
from dataclasses import replace
import os
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

    async def test_catalog_separates_declared_code_weight_terms_and_unknown_local_assets(self) -> None:
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))):
            inventory = await catalog.inventory(self.environment)
            review = await catalog.plan(ModulePlanRequest(features=['video', 'singing', 'rvc']), self.environment)
        modules = {module.id: module for module in inventory.modules}
        self.assertTrue(hasattr(modules['video'], 'licenses'), 'licence declarations must be backend contracts')
        ltx = modules['video'].licenses
        self.assertTrue(any(row.scope == 'code' and row.declared_license == 'MIT' for row in ltx))
        self.assertTrue(any(row.component_id == 'ltx-2.3' and row.declared_license == 'LTX-2 Community License' for row in ltx))
        self.assertTrue(any(row.scope == 'model' and row.status == 'unknown' for row in modules['rvc'].licenses))
        self.assertTrue(any(row.scope == 'code' and row.declared_license == 'GPL-3.0' for row in modules['singing'].licenses))
        self.assertTrue(all(module.licenses for module in inventory.modules))
        self.assertTrue(all(row.source_url is None or row.source_url.startswith('https://') for module in inventory.modules for row in module.licenses))
        self.assertEqual(next(step for step in review.steps if step.module_id == 'singing').licenses, modules['singing'].licenses)
        from app.module_install import load_manifest
        native_rows = {row.component_id: row for row in modules['yue2'].licenses}
        for package in load_manifest().weights.packages:
            self.assertIn(package, native_rows, 'every selected native model variant needs its own declaration or explicit unknown')
            self.assertEqual(native_rows[package].scope, 'model')

    async def test_speaker_dependency_setup_is_optional_and_does_not_imply_weights_or_activation(self) -> None:
        from app import speaker_review
        from app.speaker_review_contracts import SpeakerReviewCapability
        status = SpeakerReviewCapability(available=False, deps_available=True, reason='speaker_weights_missing', setup_hint='Select verified local weights explicitly.')
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))), patch.object(speaker_review, 'capability', return_value=status) as capability:
            inventory = await catalog.inventory(self.environment)
            reviewed = await catalog.plan(ModulePlanRequest(features=['speaker_review'], download_models=True), self.environment)
            dependencies_only = await catalog.plan(ModulePlanRequest(features=['speaker_review'], download_models=False), self.environment)
        module = next((item for item in inventory.modules if item.id == 'speaker_review'), None)
        self.assertIsNotNone(module, 'the optional CPU review has a visible setup stage')
        assert module is not None
        self.assertEqual(module.state, 'partial')
        self.assertEqual(module.automation, 'automatic')
        self.assertFalse(module.dependencies)
        self.assertTrue(any(row.code == 'speaker_dependencies' and row.verified for row in module.evidence))
        self.assertEqual([step.module_id for step in reviewed.steps], ['speaker_review'])
        self.assertIn('No weights are downloaded', reviewed.steps[0].detail)
        self.assertEqual(reviewed.required_free_bytes, dependencies_only.required_free_bytes, 'ignored weight flag must not add an invented model disk requirement')
        self.assertEqual(capability.call_args.kwargs['python_path'], catalog.engine_python(self.environment.paths['speaker_review'], self.environment.platform))

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

    async def test_invalid_optional_activation_path_keeps_other_modules_visible(self) -> None:
        loop = self.root / 'invalid-speaker-python'
        loop.symlink_to(loop)
        with patch.dict(os.environ, {'OPENFABRIC_SPEAKER_REVIEW_PYTHON': str(loop), 'OPENFABRIC_SPEAKER_REVIEW_WEIGHTS': ''}), patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))):
            inventory = await catalog.inventory(self.environment)
        modules = {module.id: module for module in inventory.modules}
        self.assertEqual(modules['speaker_review'].state, 'missing')
        self.assertEqual(modules['media'].state, 'missing')
        self.assertFalse(next(row for row in modules['speaker_review'].evidence if row.code == 'speaker_activation').verified)

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


class OptionalEngineCatalogTests(unittest.IsolatedAsyncioTestCase):
    def test_new_local_engines_match_their_mac_limits(self) -> None:
        linux = catalog.ModuleEnvironment.for_root(Path('/tmp/openfabric-linux-modules'), platform='linux', architecture='x86_64')
        mac = catalog.ModuleEnvironment.for_root(Path('/tmp/openfabric-mac-modules'), platform='darwin', architecture='arm64')
        self.assertFalse(catalog.supported('wan22', linux))
        self.assertTrue(catalog.supported('wan22', mac))
        for identifier in ('kokoro', 'chatterbox', 'rvc'):
            self.assertTrue(catalog.supported(identifier, mac))
            self.assertTrue(catalog.supported(identifier, linux))
        self.assertIn('does not clone a person', catalog.DEFINITIONS['kokoro'].description)
        self.assertIn('Turbo', catalog.DEFINITIONS['chatterbox'].description)
        self.assertIn('TI2V-5B', catalog.DEFINITIONS['wan22'].description)
        self.assertIn('CPU', catalog.DEFINITIONS['rvc'].description)
        self.assertLessEqual(len(catalog.DEFINITIONS['kokoro'].description), 300)
        self.assertLessEqual(len(catalog.DEFINITIONS['chatterbox'].guidance), 1000)
        from app.optional_engines import PACKAGES
        for identifier, packages in PACKAGES.items():
            self.assertEqual(catalog.DEFINITIONS[identifier].packages, packages)
