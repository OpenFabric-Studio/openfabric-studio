from __future__ import annotations

import asyncio
from dataclasses import replace
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app import module_catalog as catalog
from app.module_contracts import ModuleId, ModuleInventory, ModuleLicenseDeclaration, ModulePlanRequest
from app.module_runtime import RuntimeEvidence


class ModuleCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_approval_scope_changes_when_selected_component_terms_change(self) -> None:
        request = ModulePlanRequest(features=['yue2'], download_models=True)
        previous = catalog.approval_scope(request, self.environment)
        original = catalog.declarations
        changed = original('yue2')
        changed[1].notes = 'Updated publisher model terms require renewed review.'
        def declarations(identifier: ModuleId) -> list[ModuleLicenseDeclaration]:
            return changed if identifier == 'yue2' else original(identifier)
        with patch.object(catalog, 'declarations', side_effect=declarations):
            self.assertNotEqual(catalog.approval_scope(request, self.environment), previous)

    async def test_catalog_only_exposes_remaining_music_engines(self) -> None:
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))):
            inventory = await catalog.inventory(self.environment)
        identifiers = {module.id for module in inventory.modules}
        self.assertNotIn('heartmula', identifiers)
        self.assertNotIn('heartmula', self.environment.paths)
        self.assertTrue({'ace_step', 'yue2', 'stable_audio3'}.issubset(identifiers))

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

    async def test_beats_dependencies_are_reused_but_explicit_missing_weights_are_verified(self) -> None:
        from app import music_beats_runtime
        with patch.object(catalog, 'probe', new=AsyncMock(return_value=catalog.ProbeResult(False, ''))), \
                patch.object(music_beats_runtime, 'status', return_value=RuntimeEvidence(installed=True, ready=False)):
            dependencies = await catalog.plan(ModulePlanRequest(features=['music_beats']), self.environment)
            weights = await catalog.plan(ModulePlanRequest(features=['music_beats'], download_models=True), self.environment)
        dependency_step = next(step for step in dependencies.steps if step.module_id == 'music_beats')
        model_step = next(step for step in weights.steps if step.module_id == 'music_beats')
        self.assertEqual(dependency_step.operation, 'verify')
        self.assertEqual(model_step.operation, 'verify')
        self.assertIn('media', [step.module_id for step in weights.steps])
        self.assertTrue(model_step.licenses)

    async def test_partial_beats_dependency_recovery_keeps_unknown_sizing_and_rejects_low_disk(self) -> None:
        with patch.object(catalog, 'probe', new=AsyncMock(return_value=catalog.ProbeResult(False, ''))):
            inventory = await catalog.inventory(self.environment)
        inventory = inventory.model_copy(update={'modules': [module.model_copy(update={'state': 'ready'}) if module.id == 'media'
            else module.model_copy(update={'state': 'partial'}) if module.id == 'music_beats' else module for module in inventory.modules]})
        with patch.object(catalog, 'inventory', new=AsyncMock(return_value=inventory)), \
                patch.object(catalog, 'available_bytes', return_value=1024**3):
            for selected in (False, True):
                reviewed = await catalog.plan(ModulePlanRequest(features=['music_beats'], download_models=selected), self.environment)
                step = next(step for step in reviewed.steps if step.module_id == 'music_beats')
                self.assertEqual(step.operation, 'verify')
                self.assertIsNone(step.estimated_download_bytes)
                self.assertTrue(reviewed.download_size_unknown)
                self.assertGreaterEqual(reviewed.required_free_bytes, 8 * 1024**3)
                self.assertFalse(reviewed.can_install)

    async def test_verified_beats_dependencies_forecast_only_explicit_missing_checkpoint(self) -> None:
        from app import music_beats_runtime
        from app.music_beats import BeatRuntimeIdentity
        identity = BeatRuntimeIdentity(source_sha256='a' * 64, weights_sha256='b' * 64, python_sha256='c' * 64,
            python_version='3.12.13', packages=music_beats_runtime.PACKAGES)
        with patch.object(catalog, 'probe', new=AsyncMock(return_value=catalog.ProbeResult(False, ''))):
            inventory = await catalog.inventory(self.environment)
        inventory = inventory.model_copy(update={'modules': [module.model_copy(update={'state': 'ready'}) if module.id == 'media'
            else module.model_copy(update={'state': 'partial'}) if module.id == 'music_beats' else module for module in inventory.modules]})

        def observed(root: Path, *, require_weights: bool = True) -> BeatRuntimeIdentity:
            if require_weights:
                raise music_beats_runtime.BeatRuntimeError('analysis_weights_missing')
            return identity

        with patch.object(catalog, 'inventory', new=AsyncMock(return_value=inventory)), \
                patch.object(music_beats_runtime, 'identity', side_effect=observed), \
                patch.object(catalog, 'available_bytes', return_value=1024**2):
            dependencies = await catalog.plan(ModulePlanRequest(features=['music_beats']), self.environment)
            weights = await catalog.plan(ModulePlanRequest(features=['music_beats'], download_models=True), self.environment)
        self.assertEqual(next(step for step in dependencies.steps if step.module_id == 'music_beats').estimated_download_bytes, 0)
        step = next(step for step in weights.steps if step.module_id == 'music_beats')
        self.assertEqual(step.estimated_download_bytes, music_beats_runtime.WEIGHTS_BYTES)
        self.assertFalse(weights.download_size_unknown)
        self.assertGreaterEqual(weights.required_free_bytes, music_beats_runtime.WEIGHTS_BYTES)
        self.assertFalse(weights.can_install)

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
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))), patch.object(catalog, '_runtime_inventory', AsyncMock(return_value={})):
            inventory = await catalog.inventory(self.environment)
        self.assertEqual(inventory.model_dump().get('recommended_features'), ['ace_step'])
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertNotIn('torch', sys.modules)

    async def test_installed_features_and_dependencies_are_reused_without_a_new_install(self) -> None:
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))):
            status = await catalog.inventory(self.environment)
        for module in status.modules:
            if module.id in ('speech', 'media'):
                module.state = 'installed'
        with patch.object(catalog, 'inventory', AsyncMock(return_value=status)):
            reviewed = await catalog.plan(ModulePlanRequest(features=['speech']), self.environment)
        self.assertEqual([step.operation for step in reviewed.steps], ['reuse', 'reuse'])
        self.assertFalse(reviewed.can_install)
        self.assertEqual(reviewed.estimated_download_bytes, 0)
        self.assertEqual(reviewed.required_free_bytes, 0)
        self.assertTrue(any('already installed' in warning.lower() for warning in reviewed.warnings))

    async def test_owned_native_tool_permission_repair_is_offered_without_reinstall(self) -> None:
        status = await catalog.inventory(self.environment)
        for module in status.modules:
            if module.id in ('media', 'yue2'):
                module.state = 'installed'
        with patch.object(catalog, 'inventory', AsyncMock(return_value=status)), patch('app.module_install.native_tools_need_repair', return_value=True), patch.object(catalog, '_download_bytes', return_value=0):
            reviewed = await catalog.plan(ModulePlanRequest(features=['yue2'], download_models=True), self.environment)
        self.assertEqual(next(step for step in reviewed.steps if step.module_id == 'yue2').operation, 'verify')
        self.assertTrue(reviewed.can_install)
        self.assertEqual(reviewed.estimated_download_bytes, 0)

    async def test_missing_native_score_prerequisites_offer_verification_even_when_inventory_is_ready(self) -> None:
        from app import module_install, module_native_setup
        from app.module_jobs import InstallContext, ModuleJobService
        environment = replace(self.environment, platform='darwin', architecture='arm64')
        engine = environment.paths['yue2']
        binary_dir = engine / 'build/macos-metal-release/bin'; binary_dir.mkdir(parents=True)
        for name in ('audiocpp_server', 'audiocpp_cli', 'audiocpp_gguf'):
            path = binary_dir / name; path.write_bytes(b'existing binary'); path.chmod(0o755)
        (engine / 'tools').mkdir(); (engine / 'tools/model_manager_v2.py').write_text('# pinned downloader')
        (engine / 'model_specs').mkdir(); spec = engine / 'model_specs/sheetsage2.json'; spec.write_text('{"family":"sheetsage2"}')
        context = InstallContext(ModuleJobService(environment), 'f' * 32)
        module_install._record_owned(context, 'yue2', engine, module_install.load_manifest().engine.tag)
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))), patch.object(catalog, '_runtime_inventory', AsyncMock(return_value={})):
            status = await catalog.inventory(environment)
        for module in status.modules:
            if module.id in ('yue2', 'media'):
                module.state = 'ready'
        for missing in ('cli', 'spec', 'model'):
            cli = binary_dir / 'audiocpp_cli'; cli.write_bytes(b'existing binary'); cli.chmod(0o755)
            spec.write_text('{"family":"sheetsage2"}')
            if missing == 'cli':
                cli.unlink()
            elif missing == 'spec':
                spec.unlink()
            with self.subTest(missing=missing), patch.object(catalog, 'inventory', AsyncMock(return_value=status)), \
                    patch.object(catalog, 'available_bytes', return_value=100 * 1024**3), \
                    patch.object(module_native_setup, 'native_setup_present', return_value=missing != 'model'):
                reviewed = await catalog.plan(ModulePlanRequest(features=['yue2'], download_models=True), environment)
            self.assertEqual(next(step for step in reviewed.steps if step.module_id == 'yue2').operation, 'verify')
            self.assertTrue(reviewed.can_install)
            if missing != 'model':
                self.assertEqual(reviewed.estimated_download_bytes, sum(file.bytes for file in module_install.load_manifest().engine.assets['darwin-arm64'].files))

    async def test_fresh_native_setup_with_model_downloads_installs_the_engine(self) -> None:
        environment = replace(self.environment, platform='darwin', architecture='arm64')
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))), \
                patch.object(catalog, '_runtime_inventory', AsyncMock(return_value={})), \
                patch.object(catalog, 'available_bytes', return_value=100 * 1024**3):
            reviewed = await catalog.plan(ModulePlanRequest(features=['yue2'], download_models=True), environment)
        self.assertEqual(next(step for step in reviewed.steps if step.module_id == 'yue2').operation, 'install')
        self.assertTrue(reviewed.can_install)
        self.assertFalse(environment.paths['yue2'].exists())

    async def test_reinstallation_is_explicit_scoped_and_changes_review_token(self) -> None:
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))):
            status = await catalog.inventory(self.environment)
        for module in status.modules:
            if module.id in ('speech', 'media'):
                module.state = 'installed'
            if module.id == 'speech':
                module.can_reinstall = True
        request = ModulePlanRequest(features=['speech'], reinstall=['speech'])
        with patch.object(catalog, 'inventory', AsyncMock(return_value=status)):
            ordinary = await catalog.plan(ModulePlanRequest(features=['speech']), self.environment)
            explicit = await catalog.plan(request, self.environment)
        self.assertEqual(explicit.reinstall, ['speech'])
        self.assertEqual([step.operation for step in explicit.steps], ['reuse', 'install'])
        self.assertTrue(explicit.can_install)
        self.assertNotEqual(ordinary.plan_token, explicit.plan_token)
        self.assertNotEqual(catalog.approval_scope(ModulePlanRequest(features=['speech']), self.environment), catalog.approval_scope(request, self.environment))
        status.modules = [module.model_copy(update={'can_reinstall': False}) for module in status.modules]
        from app.module_jobs import ModuleSetupError
        with patch.object(catalog, 'inventory', AsyncMock(return_value=status)), self.assertRaisesRegex(ModuleSetupError, 'reinstall_unavailable'):
            await catalog.plan(request, self.environment)

    async def test_managed_music_with_verified_environment_can_finish_missing_models_without_package_installation(self) -> None:
        environment = replace(self.environment, platform='darwin', architecture='arm64')
        engine = environment.paths['stable_audio3']
        for marker in catalog.DEFINITIONS['stable_audio3'].source_markers:
            path = engine / marker
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# fixture source')
        python = catalog.engine_python(engine, environment.platform)
        python.parent.mkdir(parents=True)
        python.write_text('fixture interpreter')
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))), patch.object(catalog, '_environment_receipt', return_value=True):
            status = await catalog.inventory(environment)
            reviewed = await catalog.plan(ModulePlanRequest(features=['stable_audio3'], download_models=True), environment)
        module = next(item for item in status.modules if item.id == 'stable_audio3')
        self.assertEqual(module.state, 'partial')
        self.assertIn('verified_environment', module.capabilities)
        self.assertEqual(next(step.operation for step in reviewed.steps if step.module_id == 'stable_audio3'), 'verify')

    async def test_starter_selection_prefers_one_ready_then_installed_music_engine(self) -> None:
        from app import module_music_models
        environment = replace(self.environment, platform='darwin', architecture='arm64')
        ready = RuntimeEvidence(ready=True, installed=True)
        installed = RuntimeEvidence(installed=True)
        cases: tuple[tuple[dict[ModuleId, RuntimeEvidence], list[ModuleId]], ...] = (
            ({'ace_step': ready, 'yue2': ready, 'stable_audio3': ready}, ['ace_step']),
            ({'ace_step': installed, 'yue2': ready, 'stable_audio3': ready}, ['yue2']),
            ({'ace_step': installed, 'yue2': installed, 'stable_audio3': ready}, ['stable_audio3']),
            ({'ace_step': installed, 'yue2': installed, 'stable_audio3': installed}, ['ace_step']),
            ({'yue2': installed, 'stable_audio3': installed}, ['yue2']),
            ({'stable_audio3': installed}, ['stable_audio3']),
            ({'speech': ready, 'video': ready}, ['ace_step']),
        )
        for runtimes, expected in cases:
            with self.subTest(runtimes=runtimes), patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))), patch.object(catalog, '_runtime_inventory', AsyncMock(return_value=runtimes)), patch.object(module_music_models, 'music_models_present', return_value=True):
                inventory = await catalog.inventory(environment)
                self.assertEqual(inventory.model_dump().get('recommended_features'), expected)

    async def test_starter_selection_uses_only_supported_features_and_falls_back_to_media(self) -> None:
        cases = (
            ('darwin', 'arm64', ['ace_step']),
            ('linux', 'x86_64', ['ace_step']),
            ('win32', 'amd64', ['ace_step']),
            ('linux', 'arm64', ['media']),
            ('darwin', 'x86_64', ['media']),
            ('freebsd', 'x86_64', []),
        )
        for platform, architecture, expected in cases:
            environment = replace(self.environment, platform=platform, architecture=architecture)
            with self.subTest(platform=platform, architecture=architecture), patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))), patch.object(catalog, '_runtime_inventory', AsyncMock(return_value={})):
                inventory = await catalog.inventory(environment)
                self.assertEqual(inventory.model_dump().get('recommended_features'), expected)

        unsupported_music = {'stable_audio3': RuntimeEvidence(ready=True, installed=True)}
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))), patch.object(catalog, '_runtime_inventory', AsyncMock(return_value=unsupported_music)):
            inventory = await catalog.inventory(self.environment)
        self.assertEqual(inventory.model_dump().get('recommended_features'), ['ace_step'])

    def test_starter_selection_contract_defaults_and_validates_feature_bounds(self) -> None:
        payload = {'platform': 'linux', 'architecture': 'x86_64', 'acceleration': 'cpu', 'managed_root': '/managed', 'checked_at': '2026-10-15', 'modules': []}
        inventory = ModuleInventory.model_validate(payload)
        self.assertEqual(inventory.model_dump().get('recommended_features'), [])
        self.assertIsNot(inventory.recommended_features, ModuleInventory.model_validate(payload).recommended_features)
        for features in (['ace_step'] * (len(catalog.MODULE_IDS) + 1), ['unknown']):
            with self.subTest(features=features), self.assertRaises(ValidationError):
                ModuleInventory.model_validate({**payload, 'recommended_features': features})
        maximum = ModuleInventory.model_validate({**payload, 'recommended_features': list(catalog.MODULE_IDS)})
        self.assertEqual(maximum.model_dump().get('recommended_features'), list(catalog.MODULE_IDS))

    async def test_starter_plan_expands_only_selected_music_dependencies(self) -> None:
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))), patch.object(catalog, '_runtime_inventory', AsyncMock(return_value={})):
            inventory = await catalog.inventory(self.environment)
            plan = await catalog.plan(ModulePlanRequest(features=inventory.recommended_features), self.environment)
        self.assertEqual(plan.features, ['ace_step'])
        self.assertEqual([step.module_id for step in plan.steps], ['media', 'ace_step'])

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

    async def test_existing_external_music_plan_includes_missing_models_and_disk_admission(self) -> None:
        from app import module_music_models
        engine = self.root / 'external-ace'
        for marker in catalog.DEFINITIONS['ace_step'].source_markers:
            path = engine / marker
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# existing')
        python = catalog.engine_python(engine, 'darwin')
        python.parent.mkdir(parents=True)
        python.write_text('# existing interpreter')
        environment = replace(self.environment, platform='darwin', architecture='arm64', paths={**self.environment.paths, 'ace_step': engine})
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))), patch.object(module_music_models, 'model_download_bytes', return_value=10 * 1024**3), patch.object(catalog.shutil, 'disk_usage', return_value=catalog.shutil.disk_usage(self.root)._replace(free=15 * 1024**3)):
            reviewed = await catalog.plan(ModulePlanRequest(features=['ace_step'], download_models=True), environment)
            without_models = await catalog.plan(ModulePlanRequest(features=['ace_step'], download_models=False), environment)
        ace = next(step for step in reviewed.steps if step.module_id == 'ace_step')
        self.assertEqual(ace.operation, 'verify')
        self.assertEqual(ace.estimated_download_bytes, 10 * 1024**3)
        self.assertGreaterEqual(reviewed.required_free_bytes, 20 * 1024**3, 'cache plus installed models must both fit')
        self.assertFalse(reviewed.can_install)
        self.assertEqual(next(step for step in without_models.steps if step.module_id == 'ace_step').estimated_download_bytes, 0)

    async def test_existing_native_engine_can_finish_models_automatically(self) -> None:
        from app import module_native_setup
        environment = replace(self.environment, platform='darwin', architecture='arm64', paths={**self.environment.paths, 'yue2': self.root / 'external-native'})
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))), patch.object(module_native_setup, 'native_environment_present', return_value=True), patch.object(module_native_setup, 'native_setup_present', return_value=False):
            status = await catalog.inventory(environment)
            reviewed = await catalog.plan(ModulePlanRequest(features=['yue2'], download_models=True), environment)
        module = next(item for item in status.modules if item.id == 'yue2')
        self.assertEqual(module.automation, 'automatic')
        self.assertEqual(module.state, 'partial')
        native = next(item for item in reviewed.steps if item.module_id == 'yue2')
        self.assertEqual(native.operation, 'verify')
        self.assertIsNone(native.estimated_download_bytes)
        self.assertTrue(reviewed.download_size_unknown)

    async def test_external_model_destination_requires_free_space_on_its_own_volume(self) -> None:
        from app import module_music_models
        engine = self.root / 'external-ace'
        for marker in catalog.DEFINITIONS['ace_step'].source_markers:
            path = engine / marker
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# source')
        python = catalog.engine_python(engine, 'darwin')
        python.parent.mkdir(parents=True)
        python.write_text('# interpreter')
        environment = replace(self.environment, platform='darwin', architecture='arm64', paths={**self.environment.paths, 'ace_step': engine})
        usage = catalog.shutil.disk_usage(self.root)
        def disk(path: Path) -> object:
            return usage._replace(free=1024**2 if path.resolve().is_relative_to(engine.resolve()) else 100 * 1024**3)
        with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))), patch.object(module_music_models, 'model_download_bytes', return_value=1024**3), patch.object(catalog.shutil, 'disk_usage', side_effect=disk):
            reviewed = await catalog.plan(ModulePlanRequest(features=['ace_step'], download_models=True), environment)
        self.assertFalse(reviewed.can_install, 'free cache space does not make a full model destination writable')
        self.assertEqual(reviewed.free_bytes, 1024**2)

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
    async def test_unverified_local_checkpoints_keep_music_setup_available(self) -> None:
        from app import local_music_runtime
        with tempfile.TemporaryDirectory() as temporary:
            environment = catalog.ModuleEnvironment.for_root(Path(temporary), platform='darwin', architecture='arm64')
            for identifier in local_music_runtime.ENGINES:
                for marker in catalog.DEFINITIONS[identifier].source_markers:
                    path = environment.paths[identifier] / marker
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('# source')
                for path in local_music_runtime.weight_paths(environment, identifier):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'unverified local checkpoint')
            with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))), patch.object(catalog, '_environment_receipt', return_value=True):
                inventory = await catalog.inventory(environment)
            modules = {module.id: module for module in inventory.modules}
            for identifier in local_music_runtime.ENGINES:
                self.assertEqual(modules[identifier].state, 'partial')
                self.assertFalse(next(row for row in modules[identifier].evidence if row.code == 'local_model_files').verified)
                self.assertTrue(any(row.code == 'inference_unverified' for row in modules[identifier].evidence))
                self.assertNotIn('music_generation_models', modules[identifier].capabilities)

    async def test_local_music_modules_have_honest_platform_setup_and_licenses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            mac = catalog.ModuleEnvironment.for_root(Path(temporary), platform='darwin', architecture='arm64')
            if mac.uv is None:
                mac = replace(mac, uv=str(Path(temporary) / 'fixture-uv'))
            linux = replace(mac, platform='linux', architecture='x86_64')
            with patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, ''))):
                inventory = await catalog.inventory(mac)
                reviewed = await catalog.plan(ModulePlanRequest(features=['stable_audio3'], download_models=True), mac)
                dependencies = await catalog.plan(ModulePlanRequest(features=['stable_audio3']), mac)
            modules = {module.id: module for module in inventory.modules}
            for identifier in ('stable_audio3',):
                self.assertTrue(modules[identifier].supported)
                self.assertEqual(modules[identifier].state, 'missing')
                self.assertIn('download', catalog.DEFINITIONS[identifier].guidance)
                self.assertNotIn('No weights are downloaded', catalog.DEFINITIONS[identifier].guidance)
                self.assertTrue(modules[identifier].licenses)
            self.assertFalse(catalog.supported('stable_audio3', linux))
            self.assertEqual([step.module_id for step in reviewed.steps], ['media', 'stable_audio3'])
            self.assertGreater(reviewed.required_free_bytes, dependencies.required_free_bytes)
            self.assertTrue(any(row.scope == 'code' and row.declared_license == 'MIT' for row in modules['stable_audio3'].licenses))

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
        self.assertIn('transformers', catalog.DEFINITIONS['rvc'].packages)
        self.assertLessEqual(len(catalog.DEFINITIONS['kokoro'].description), 300)
        self.assertLessEqual(len(catalog.DEFINITIONS['chatterbox'].guidance), 1000)
        from app.optional_engines import PACKAGES
        for identifier, packages in PACKAGES.items():
            self.assertEqual(catalog.DEFINITIONS[identifier].packages, packages)
