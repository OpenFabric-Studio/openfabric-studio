from __future__ import annotations

from pathlib import Path
import asyncio
from dataclasses import replace
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app import module_catalog as catalog
from app.module_contracts import ModuleDownloadProgress, ModuleInstallRequest, ModulePlanRequest
from app.module_install import DownloadArtifact, SourcePin
from app.module_jobs import InstallContext, ModuleJobService, ModuleSetupError
from app.module_music_models_test import tensor
from app import music_training_setup as setup


class MusicTrainingPreparationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = catalog.ModuleEnvironment.for_root(self.root / 'managed', platform='darwin', architecture='arm64')
        # Plans require a uv path. CI runners do not ship uv; the installer commands stay mocked.
        if self.environment.uv is None:
            self.environment = replace(self.environment, uv=str(self.root / 'fixture-uv'))
        self.commands: list[list[str]] = []
        self.downloads: list[str] = []
        self.enterContext(patch.object(catalog, 'available_bytes', return_value=100 * 1024**3))

    async def clone(self, context: InstallContext, identifier: str, pin: SourcePin, target: Path) -> None:
        source = next(source for recipe in setup.recipes().values() for source in recipe.sources if source.folder == target.name)
        (target / '.git').mkdir(parents=True)
        (target / '.git/HEAD').write_text(pin.commit)
        marker = target / source.marker
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('# pinned source')

    async def fake_run(self, argv: list[str], **_: object) -> None:
        self.commands.append(argv)
        if argv[1] == 'venv':
            target = Path(argv[-1])
            (target / 'bin').mkdir(parents=True)
            interpreter = self.environment.root / 'tools/python/fixture-python'
            interpreter.parent.mkdir(parents=True, exist_ok=True)
            interpreter.write_text('fixture interpreter')
            (target / 'bin/python').symlink_to(interpreter)
            (target / 'lib/python3.12/site-packages/fixture.dist-info').mkdir(parents=True)
            (target / 'lib/python3.12/site-packages/fixture.dist-info/METADATA').write_text('Version: 1')
            (target / 'lib/python3.12/site-packages/fixture').mkdir()

    async def download(self, artifact: DownloadArtifact, target: Path, context: InstallContext, **_: object) -> Path:
        self.downloads.append(target.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'{"fixture":1}' if target.suffix == '.json' else tensor() if target.suffix == '.safetensors' else b'license')
        return target

    def external_boundaries(self, context: InstallContext) -> None:
        self.enterContext(patch.object(context, 'update_detail'))
        self.enterContext(patch.object(context, 'run', side_effect=self.fake_run))
        self.enterContext(patch('app.module_install._clone', side_effect=self.clone))
        self.enterContext(patch('app.module_install.download_artifact', side_effect=self.download))

    async def test_yue_plan_uses_separate_training_paths_and_known_model_bytes(self) -> None:
        request = ModulePlanRequest(features=['yue2'], download_models=True, music_training_engine='yue2')
        with patch.object(catalog, 'probe', new=AsyncMock(return_value=catalog.ProbeResult(False, ''))), patch.object(catalog, 'available_bytes', return_value=100 * 1024**3):
            review = await catalog.plan(request, self.environment)
        self.assertEqual(review.music_training_engine, 'yue2')
        self.assertEqual([step.module_id for step in review.steps], ['yue2'])
        self.assertEqual(review.estimated_download_bytes, 7799983228)
        self.assertTrue(review.download_size_unknown)
        self.assertGreaterEqual(review.required_free_bytes, 2 * 7799983228 + 8 * 1024**3)
        self.assertTrue(review.can_install)
        self.assertTrue(any('training' in step.name.lower() for step in review.steps))
        self.assertTrue(any(row.declared_license == 'CC-BY-NC-4.0' for row in review.steps[0].licenses))
        self.assertFalse(self.environment.root.exists())

    async def test_owned_score_cli_permission_loss_offers_repair_without_reinstall_or_download(self) -> None:
        from app import module_install
        context = InstallContext(ModuleJobService(self.environment), '8' * 32)
        self.external_boundaries(context)
        await setup.prepare(context, 'yue2')
        engine = self.environment.paths['yue2']
        (engine / 'tools').mkdir(parents=True)
        (engine / 'tools/model_manager_v2.py').write_text('# reviewed native installer')
        (engine / 'model_specs').mkdir()
        (engine / 'model_specs/sheetsage2.json').write_text('{"family":"sheetsage2"}')
        cli = engine / 'build/macos-metal-release/bin/audiocpp_cli'
        cli.parent.mkdir(parents=True); cli.write_bytes(b'preserved CLI'); cli.chmod(0o644)
        module_install._record_owned(context, 'yue2', engine, module_install.load_manifest().engine.tag)
        request = ModulePlanRequest(features=['yue2'], download_models=True, music_training_engine='yue2')
        reviewed = setup.plan(request, self.environment)
        self.assertEqual(reviewed.steps[0].operation, 'verify')
        self.assertTrue(reviewed.can_install)
        self.assertEqual(reviewed.estimated_download_bytes, 0)
        commands, downloads = len(self.commands), len(self.downloads)
        await setup.prepare(context, 'yue2')
        self.assertEqual(cli.stat().st_mode & 0o111, 0o111)
        self.assertEqual(cli.read_bytes(), b'preserved CLI')
        self.assertEqual((len(self.commands), len(self.downloads)), (commands, downloads))
        self.assertEqual(setup.plan(request, self.environment).steps[0].operation, 'reuse')

    async def test_installer_reuses_verified_assets_and_owned_environment_without_mutating_generation(self) -> None:
        original = self.environment.paths['yue2'] / 'private-original.txt'
        original.parent.mkdir(parents=True)
        original.write_text('keep original')
        context = InstallContext(ModuleJobService(self.environment), 'a' * 32)
        self.external_boundaries(context)
        result = await setup.prepare(context, 'yue2')
        self.assertEqual(result.state, 'verified')
        self.assertTrue(setup.prepared(self.environment, 'yue2'))
        count, downloaded = len(self.commands), len(self.downloads)
        await setup.prepare(context, 'yue2')
        self.assertEqual(len(self.commands), count)
        self.assertEqual(len(self.downloads), downloaded)
        self.assertTrue(all(str(self.environment.paths['yue2']) not in value for argv in self.commands for value in argv))
        self.assertEqual(original.read_text(), 'keep original')

    async def test_unowned_training_directory_is_preserved_and_not_installed(self) -> None:
        root = self.environment.root / 'training/yue2'
        root.mkdir(parents=True)
        sentinel = root / 'user.txt'
        sentinel.write_text('preserve')
        context = InstallContext(ModuleJobService(self.environment), 'b' * 32)
        with patch.object(context, 'run', new=AsyncMock()) as run:
            with self.assertRaisesRegex(ModuleSetupError, 'installation_conflict'):
                await setup.prepare(context, 'yue2')
        run.assert_not_awaited()
        self.assertEqual(sentinel.read_text(), 'preserve')

    async def test_pins_requirements_and_source_terms_are_reviewed(self) -> None:
        recipe = setup.recipes()
        self.assertEqual(set(recipe), {'yue2'})
        self.assertEqual([item.pin.commit for item in recipe['yue2'].sources], ['6a9355efff60589d93b41b569676f09d5db85d09', 'b058ec652802e1de24f1d429b3c7c1fb868f791c'])
        terms = next(row for row in setup.training_license_declarations('yue2') if row.component_id == 'yue-training-base')
        self.assertEqual(terms.source_url, 'https://github.com/multimodal-art-projection/YuE/blob/1647252d68b70cbead046ffd3f7caf036dde47ee/MODEL_LICENSE')
        self.assertEqual(terms.restriction, 'noncommercial')
        self.assertEqual(terms.redistribution, 'unknown')
        self.assertEqual(terms.identities[0].repository_url, 'https://huggingface.co/Comfy-Org/YuE2')
        self.assertEqual(terms.identities[0].revision, '2f76ca75e6ee094169de899cc7fc99d6887e2196')
        self.assertEqual(terms.identities[0].sha256, '33765adbf9813c9a50318218760b2fd819a319862460a04884607581961c6fee')
        self.assertTrue(any(item.kind == 'publisher_model_card' for item in terms.evidence))
        for item in (Path(setup.__file__).parent / 'assets').glob('music-training-*-requirements.txt'):
            self.assertTrue(all('==' in line and not line.startswith(('-', '#')) for line in item.read_text().splitlines()))

    async def test_partial_owned_environment_retry_preserves_sources_and_rechecks_packages(self) -> None:
        context = InstallContext(ModuleJobService(self.environment), 'c' * 32)
        self.external_boundaries(context)
        failed = False
        async def fail_once(argv: list[str], **kwargs: object) -> None:
            nonlocal failed
            if len(argv) > 2 and argv[1:3] == ['pip', 'install'] and not failed:
                failed = True
                raise ModuleSetupError('installer_failed')
            await self.fake_run(argv, **kwargs)
        with patch.object(context, 'run', side_effect=fail_once):
            with self.assertRaisesRegex(ModuleSetupError, 'installer_failed'):
                await setup.prepare(context, 'yue2')
        self.assertFalse(setup.environment_ready(self.environment, 'yue2'))
        source = setup.training_root(self.environment, 'yue2') / 'trainer/train_cli.py'
        original = source.read_bytes()
        await setup.prepare(context, 'yue2')
        self.assertTrue(setup.prepared(self.environment, 'yue2'))
        self.assertEqual(source.read_bytes(), original)
        source.write_text('# changed local source')
        self.assertFalse(setup.environment_ready(self.environment, 'yue2'))

    async def test_paths_reject_training_symlink_to_generation_tree(self) -> None:
        original = self.environment.paths['yue2']
        original.mkdir(parents=True)
        parent = self.environment.root / 'training'
        parent.mkdir()
        (parent / 'yue2').symlink_to(original, target_is_directory=True)
        context = InstallContext(ModuleJobService(self.environment), 'e' * 32)
        self.external_boundaries(context)
        with self.assertRaisesRegex(ModuleSetupError, 'unsafe_install_path'):
            await setup.prepare(context, 'yue2')
        self.assertFalse(list(original.iterdir()))

    async def test_job_cancel_reload_resume_retains_engine_and_reuses_prepared_dependencies(self) -> None:
        from app import module_jobs as jobs
        self.enterContext(patch.object(jobs, '_application_busy', return_value=False))
        self.enterContext(patch('app.module_install._clone', side_effect=self.clone))
        async def run(context: InstallContext, argv: list[str], **kwargs: object) -> None:
            await self.fake_run(argv, **kwargs)
        self.enterContext(patch.object(InstallContext, 'run', new=run))
        started = asyncio.Event()
        blocked = True
        async def download(artifact: DownloadArtifact, target: Path, context: InstallContext, **kwargs: object) -> Path:
            if blocked:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.with_suffix('.part').write_bytes(b'verified resumable partial fixture')
                context.update_download(ModuleDownloadProgress(artifact_name=target.name, bytes_received=10, total_bytes=artifact.bytes, attempt=1, phase='downloading'))
                started.set()
                await asyncio.Event().wait()
            self.assertTrue(target.with_suffix('.part').exists())
            return await self.download(artifact, target, context, **kwargs)
        self.enterContext(patch('app.module_install.download_artifact', side_effect=download))
        request = ModulePlanRequest(features=['yue2'], download_models=True, music_training_engine='yue2')
        reviewed = await catalog.plan(request, self.environment)
        service = ModuleJobService(self.environment)
        reader = ModuleJobService(self.environment)
        try:
            created = await service.create(ModuleInstallRequest(**request.model_dump(), plan_token=reviewed.plan_token))
            await asyncio.wait_for(started.wait(), 2)
            count = len(self.commands)
            await service.cancel(created.id)
            self.assertEqual(reader.get(created.id).state, 'cancelled')
            self.assertEqual(reader.get(created.id).music_training_engine, 'yue2')
            self.assertEqual(reader.get(created.id).steps[0].download.bytes_received, 10)
            self.assertTrue(setup.environment_ready(self.environment, 'yue2'))
            blocked = False
            await reader.resume(created.id)
            await asyncio.wait_for(reader.tasks[created.id], 2)
            self.assertEqual(reader.get(created.id).state, 'completed')
            self.assertTrue(setup.prepared(self.environment, 'yue2'))
            self.assertEqual(len(self.commands), count)
        finally:
            await reader.shutdown()
            await service.shutdown()

    async def test_prepared_environment_can_reuse_or_finish_models_without_installer_tools(self) -> None:
        context = InstallContext(ModuleJobService(self.environment), 'f' * 32)
        self.external_boundaries(context)
        await setup.prepare(context, 'yue2')
        environment = replace(self.environment, uv=None)
        with patch('shutil.which', return_value=None):
            reviewed = await catalog.plan(ModulePlanRequest(features=['yue2'], download_models=True, music_training_engine='yue2'), environment)
        self.assertEqual(reviewed.steps[0].operation, 'reuse')

    async def test_changed_source_revision_is_preserved_and_refused_before_dependency_changes(self) -> None:
        context = InstallContext(ModuleJobService(self.environment), '1' * 32)
        self.external_boundaries(context)
        await setup.prepare(context, 'yue2')
        head = setup.training_root(self.environment, 'yue2') / 'trainer/.git/HEAD'
        head.write_text('0' * 40)
        count = len(self.commands)
        with self.assertRaisesRegex(ModuleSetupError, 'installation_conflict'):
            await setup.prepare(context, 'yue2')
        self.assertEqual(head.read_text(), '0' * 40)
        self.assertEqual(len(self.commands), count)

    async def test_assets_removed_after_review_cannot_expand_queued_downloads(self) -> None:
        from app import module_jobs as jobs
        from app.module_jobs import InstallOutcome
        self.enterContext(patch.object(jobs, '_application_busy', return_value=False))
        asset = setup.training_assets(self.environment, 'yue2')[0].target
        asset.parent.mkdir(parents=True)
        asset.write_bytes(tensor())
        request = ModulePlanRequest(features=['yue2'], download_models=True, music_training_engine='yue2')
        reviewed = await catalog.plan(request, self.environment)
        self.assertEqual(reviewed.estimated_download_bytes, 0)
        service = ModuleJobService(self.environment)
        try:
            with patch.object(setup, 'prepare', new=AsyncMock(return_value=InstallOutcome('verified', 'fixture'))) as install:
                created = await service.create(ModuleInstallRequest(**request.model_dump(), plan_token=reviewed.plan_token))
                asset.unlink()
                await asyncio.wait_for(service.tasks[created.id], 2)
            self.assertEqual(service.get(created.id).state, 'failed')
            self.assertEqual(service.get(created.id).error_code, 'plan_changed')
            install.assert_not_awaited()
        finally:
            await service.shutdown()

    async def test_removing_an_installed_package_invalidates_environment_receipt(self) -> None:
        context = InstallContext(ModuleJobService(self.environment), '2' * 32)
        self.external_boundaries(context)
        await setup.prepare(context, 'yue2')
        package = setup.training_root(self.environment, 'yue2') / 'venv/lib/python3.12/site-packages/fixture'
        package.rmdir()
        self.assertFalse(setup.environment_ready(self.environment, 'yue2'))

    async def test_import_check_uses_source_directory_without_namespace_shadowing(self) -> None:
        context = InstallContext(ModuleJobService(self.environment), '3' * 32)
        self.external_boundaries(context)
        recipe = setup.recipes()['yue2']
        check = replace(recipe.environments[0], check='from comfy.utils import ProgressBar')
        reviewed = replace(recipe, environments=(check,))
        async def clone(context: InstallContext, identifier: str, pin: SourcePin, target: Path) -> None:
            await self.clone(context, identifier, pin, target)
            if target.name == 'comfy':
                (target / 'utils').mkdir()
                (target / 'utils/__init__.py').write_text('WRONG_NAMESPACE = True')
                (target / 'comfy/utils.py').write_text('ProgressBar = object')
        async def run(argv: list[str], **kwargs: object) -> None:
            if argv[1] == '-c':
                cwd = kwargs.get('cwd')
                self.assertIsInstance(cwd, Path)
                raw_env = kwargs.get('env')
                self.assertIsInstance(raw_env, dict)
                expected = os.pathsep.join(str(setup.training_root(self.environment, 'yue2') / path) for path in check.pythonpath)
                if isinstance(raw_env, dict):
                    self.assertEqual(raw_env.get('PYTHONPATH'), expected)
                if isinstance(cwd, Path):
                    result = subprocess.run([sys.executable, '-c', argv[2]], cwd=cwd,
                        env={**os.environ, 'PYTHONPATH': expected}, capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
            else:
                await self.fake_run(argv, **kwargs)
        with patch.object(setup, 'recipes', return_value={'yue2': reviewed}), patch('app.module_install._clone', side_effect=clone), patch.object(context, 'run', side_effect=run):
            await setup.prepare(context, 'yue2')

    def test_recipe_identity_does_not_depend_on_helper_file_contents(self) -> None:
        expected = setup.recipe_hash('yue2')
        with patch.object(setup, '__file__', str(self.root / 'unrelated-helper.py')):
            self.assertEqual(setup.recipe_hash('yue2'), expected)

    def test_recipe_identity_binds_source_dependency_and_model_pins(self) -> None:
        expected = setup.recipe_hash('yue2')
        recipes = setup.recipes()
        recipe = recipes['yue2']
        source = replace(recipe.sources[0], pin=replace(recipe.sources[0].pin, commit='0' * 40))
        with patch.object(setup, 'recipes', return_value={**recipes, 'yue2': replace(recipe, sources=(source, recipe.sources[1]))}):
            self.assertNotEqual(setup.recipe_hash('yue2'), expected)
        artifact = setup._published_artifacts('yue2')[0].model_copy(update={'sha256': '0' * 64})
        with patch.object(setup, '_published_artifacts', return_value=[artifact]):
            self.assertNotEqual(setup.recipe_hash('yue2'), expected)
        requirements = recipe.environments[0].requirements
        contents = (setup._ASSETS / requirements).read_text()
        (self.root / requirements).write_text(contents + 'fixture-package==1\n')
        with patch.object(setup, '_ASSETS', self.root):
            self.assertNotEqual(setup.recipe_hash('yue2'), expected)

    async def test_exact_legacy_ownership_is_rechecked_before_migration_without_reinstalling(self) -> None:
        from app.atomic_files import write_object
        context = InstallContext(ModuleJobService(self.environment), '4' * 32)
        self.external_boundaries(context)
        await setup.prepare(context, 'yue2')
        marker = setup._receipt_path(self.environment, 'yue2')
        receipt = setup.TrainingOwnership.model_validate_json(marker.read_bytes())
        legacy = '203b4f3261a6104e2d0207fc0e8a153b1c8b14f0fae983854d1eeb2d3fee7897'
        write_object(marker, {'schema_version': 1, 'root': receipt.root, 'recipe': legacy, 'fingerprint': receipt.fingerprint})
        self.assertFalse(setup.environment_ready(self.environment, 'yue2'))
        count = len(self.commands)
        await setup.prepare(context, 'yue2')
        added = self.commands[count:]
        self.assertFalse(any(argv[1:3] == ['pip', 'install'] for argv in added))
        self.assertEqual(sum(argv[1:3] == ['pip', 'check'] for argv in added), 1)
        self.assertEqual(sum(argv[1] == '-c' for argv in added), 1)
        self.assertTrue(setup.environment_ready(self.environment, 'yue2'))

    async def test_failed_legacy_validation_preserves_receipt_and_unknown_recipe_is_not_adopted(self) -> None:
        from app.atomic_files import write_object
        context = InstallContext(ModuleJobService(self.environment), '5' * 32)
        self.external_boundaries(context)
        await setup.prepare(context, 'yue2')
        marker = setup._receipt_path(self.environment, 'yue2')
        receipt = setup.TrainingOwnership.model_validate_json(marker.read_bytes())
        legacy = '203b4f3261a6104e2d0207fc0e8a153b1c8b14f0fae983854d1eeb2d3fee7897'
        write_object(marker, {'schema_version': 1, 'root': receipt.root, 'recipe': legacy, 'fingerprint': receipt.fingerprint})
        original = marker.read_bytes()
        with patch.object(context, 'run', new=AsyncMock(side_effect=ModuleSetupError('environment_unverified'))):
            with self.assertRaisesRegex(ModuleSetupError, 'environment_unverified'):
                await setup.prepare(context, 'yue2')
        self.assertEqual(marker.read_bytes(), original)
        write_object(marker, {'schema_version': 1, 'root': receipt.root, 'recipe': '0' * 64, 'fingerprint': receipt.fingerprint})
        with self.assertRaisesRegex(ModuleSetupError, 'installation_conflict'):
            await setup.prepare(context, 'yue2')
