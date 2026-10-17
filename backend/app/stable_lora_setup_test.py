from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from app import module_catalog as catalog, module_jobs as jobs, module_music_models as models
from app.module_contracts import ModuleInstallRequest, ModulePlanRequest
from app.module_install import DownloadArtifact
from app.module_music_models_test import npz
from app import stable_lora_runtime as runtime
from app import local_music_runtime as generation_runtime


class StableTrainingSetupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = catalog.ModuleEnvironment.for_root(self.root / 'managed', platform='darwin', architecture='arm64')
        scripts = self.environment.paths['stable_audio3'] / 'optimized/mlx/scripts'
        scripts.mkdir(parents=True)
        for script in ('lora_train_mlx.py', 'pre_encode_mlx.py'):
            (scripts / script).write_text('# installed fixture')
        self.enterContext(patch.object(runtime, 'environment_verified', return_value=True))
        self.enterContext(patch.object(generation_runtime, 'environment_verified', return_value=True))
        self.enterContext(patch.object(jobs, '_application_busy', return_value=False))
        self.enterContext(patch.object(catalog, 'available_bytes', return_value=100 * 1024**3))
        with patch.object(catalog, 'probe', new=AsyncMock(return_value=catalog.ProbeResult(False, ''))), patch.object(catalog, '_runtime_inventory', new=AsyncMock(return_value={})):
            inventory = await catalog.inventory(self.environment)
        next(module for module in inventory.modules if module.id == 'stable_audio3').state = 'installed'
        self.enterContext(patch.object(catalog, 'inventory', new=AsyncMock(return_value=inventory)))
        self.put(runtime.training_weights(self.environment, 'small-music')[0])

    def put(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(npz())

    def request(self, variant: str = 'small-music') -> ModulePlanRequest:
        return ModulePlanRequest.model_validate({'features': ['stable_audio3'], 'download_models': True, 'stable_training_variant': variant})

    def generation_request(self, variant: str = 'medium') -> ModulePlanRequest:
        self.assertIn('stable_generation_variant', ModulePlanRequest.model_fields)
        return ModulePlanRequest.model_validate({'features': ['stable_audio3'], 'download_models': True, 'stable_generation_variant': variant})

    def conditioning_request(self, variant: str = 'small-music') -> ModulePlanRequest:
        return ModulePlanRequest.model_validate({'features': ['stable_audio3'], 'download_models': True,
            'stable_conditioning_variant': variant})

    async def test_conditioning_plan_downloads_only_missing_encoder_without_training_base(self) -> None:
        for path in generation_runtime.weight_paths(self.environment, 'stable_audio3'):
            self.put(path)
        review = await catalog.plan(self.conditioning_request(), self.environment)
        self.assertEqual(review.stable_conditioning_variant, 'small-music')
        self.assertEqual(review.estimated_download_bytes, 214946620)
        self.assertEqual(review.required_free_bytes, 2 * 214946620)
        self.assertTrue(review.can_install)
        identities = [identity for license in review.steps[0].licenses for identity in license.identities]
        self.assertIn('a48f80d81c30d74c45e2a3047082c4891f715e24c44645adb9c1f4f07afdaf0c', [item.sha256 for item in identities])
        self.assertFalse(any('-base_' in (item.filename or '') for item in identities))
        self.assertNotEqual(review.plan_token, (await catalog.plan(self.request(), self.environment)).plan_token)
        self.assertNotEqual(catalog.approval_scope(self.conditioning_request(), self.environment),
            catalog.approval_scope(self.generation_request('small-music'), self.environment))

    async def test_conditioning_cancel_reload_resume_keeps_variant_and_never_downloads_training_base(self) -> None:
        for path in generation_runtime.weight_paths(self.environment, 'stable_audio3'):
            self.put(path)
        request = self.conditioning_request()
        review = await catalog.plan(request, self.environment)
        entered = asyncio.Event()
        block = True
        downloaded: list[str] = []
        async def download(artifact: DownloadArtifact, destination: Path, context: jobs.InstallContext, **_: object) -> Path:
            downloaded.append(destination.name)
            if block:
                entered.set(); await asyncio.Event().wait()
            destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(npz())
            return destination
        service, reader = jobs.ModuleJobService(self.environment), jobs.ModuleJobService(self.environment)
        try:
            with patch('app.module_install.download_artifact', side_effect=download), patch('app.module_install.install', new=AsyncMock(side_effect=AssertionError('new environment forbidden'))):
                created = await service.create(ModuleInstallRequest(**request.model_dump(), plan_token=review.plan_token))
                await asyncio.wait_for(entered.wait(), 1)
                await service.cancel(created.id)
                self.assertEqual(reader.get(created.id).stable_conditioning_variant, 'small-music')
                self.assertEqual(reader.store.read(created.id).plan.stable_conditioning_variant, 'small-music')
                block = False
                await reader.resume(created.id); await self.wait_for_terminal(reader, created.id)
                self.assertEqual(reader.get(created.id).state, 'completed')
                self.assertEqual(downloaded, ['same_s_encoder_f32.npz', 'same_s_encoder_f32.npz'])
        finally:
            await reader.shutdown(); await service.shutdown()

    async def test_generation_plan_uses_selected_inference_bundle_despite_ready_small_and_training_medium(self) -> None:
        for path in (*generation_runtime.weight_paths(self.environment, 'stable_audio3'), *runtime.training_weights(self.environment, 'medium')):
            self.put(path)
        request = self.generation_request()
        review = await catalog.plan(request, self.environment)
        self.assertEqual(review.model_dump().get('stable_generation_variant'), 'medium')
        self.assertEqual([(step.module_id, step.operation) for step in review.steps], [('stable_audio3', 'verify')])
        artifacts = models._manifest().bundles['stable-medium']
        missing = [item for item in artifacts if not models._valid_file(models._path(self.environment.paths['stable_audio3'], item.path))]
        self.assertEqual(review.estimated_download_bytes, sum(item.bytes for item in missing))
        self.assertEqual({Path(item.path).name for item in missing}, {'dit_medium_f16.npz', 'same_l_decoder_f32.npz'})
        self.assertTrue(review.can_install)
        ready_small = await catalog.plan(self.generation_request('small-music'), self.environment)
        self.assertEqual(ready_small.steps[0].operation, 'reuse')
        self.assertFalse(ready_small.can_install)
        self.assertNotEqual(review.plan_token, ready_small.plan_token)
        self.assertNotEqual(catalog.approval_scope(request, self.environment), catalog.approval_scope(self.generation_request('small-music'), self.environment))

    async def test_generation_download_cancel_reload_resume_preserves_shared_weights_and_variant(self) -> None:
        for path in generation_runtime.weight_paths(self.environment, 'stable_audio3'):
            self.put(path)
        shared = generation_runtime.weight_paths(self.environment, 'stable_audio3')[0]
        original = shared.read_bytes()
        request = self.generation_request()
        review = await catalog.plan(request, self.environment)
        started = asyncio.Event()
        downloaded: list[str] = []
        block_decoder = True
        async def download(artifact: DownloadArtifact, destination: Path, context: jobs.InstallContext, **_: object) -> Path:
            downloaded.append(destination.name)
            if destination.name == 'same_l_decoder_f32.npz' and block_decoder:
                started.set()
                await asyncio.Event().wait()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(npz())
            return destination
        service = jobs.ModuleJobService(self.environment)
        reader = jobs.ModuleJobService(self.environment)
        try:
            with patch('app.module_install.download_artifact', side_effect=download), patch('app.module_install.install', new=AsyncMock(side_effect=AssertionError('environment install forbidden'))), patch('app.module_install.verify', new=AsyncMock(side_effect=AssertionError('aggregate verification forbidden'))):
                with self.assertRaisesRegex(jobs.ModuleSetupError, 'already_installed'):
                    small = await catalog.plan(self.generation_request('small-music'), self.environment)
                    await service.create(ModuleInstallRequest(**self.generation_request('small-music').model_dump(), plan_token=small.plan_token))
                with self.assertRaisesRegex(jobs.ModuleSetupError, 'plan_changed'):
                    await service.create(ModuleInstallRequest(**request.model_dump(), plan_token=small.plan_token))
                created = await service.create(ModuleInstallRequest(**request.model_dump(), plan_token=review.plan_token))
                await asyncio.wait_for(started.wait(), 1)
                await service.cancel(created.id)
                retained = reader.get(created.id)
                self.assertEqual(retained.state, 'cancelled')
                self.assertEqual(retained.model_dump().get('stable_generation_variant'), 'medium')
                self.assertEqual(reader.store.read(created.id).plan.model_dump().get('stable_generation_variant'), 'medium')
                block_decoder = False
                await reader.resume(created.id)
                await self.wait_for_terminal(reader, created.id)
                self.assertEqual(reader.get(created.id).state, 'completed')
                self.assertEqual(downloaded, ['dit_medium_f16.npz', 'same_l_decoder_f32.npz', 'same_l_decoder_f32.npz'])
                self.assertEqual(shared.read_bytes(), original)
                stable = next(engine for engine in generation_runtime.engine_status(self.environment).engines if engine.id == 'stable_audio3')
                self.assertTrue(stable.medium_weights_ready)
        finally:
            await reader.shutdown()
            await service.shutdown()

    async def test_generation_plan_does_not_require_training_scripts_and_refuses_unverified_environment(self) -> None:
        scripts = self.environment.paths['stable_audio3'] / 'optimized/mlx/scripts'
        for name in ('lora_train_mlx.py', 'pre_encode_mlx.py'):
            (scripts / name).unlink()
        review = await catalog.plan(self.generation_request(), self.environment)
        self.assertTrue(review.can_install)
        with patch.object(generation_runtime, 'environment_verified', return_value=False):
            unavailable = await catalog.plan(self.generation_request(), self.environment)
            self.assertEqual(unavailable.steps[0].operation, 'manual')
            self.assertFalse(unavailable.can_install)
            service = jobs.ModuleJobService(self.environment)
            with self.assertRaisesRegex(jobs.ModuleSetupError, 'environment_unverified'):
                await service.create(ModuleInstallRequest(**self.generation_request().model_dump(), plan_token=unavailable.plan_token))

    async def wait_for_terminal(self, service: jobs.ModuleJobService, identifier: str) -> None:
        task = service.tasks.get(identifier)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), 2)

    async def test_explicit_plan_selects_training_files_without_reinstalling_ready_inference(self) -> None:
        ordinary = await catalog.plan(ModulePlanRequest(features=['stable_audio3'], download_models=True), self.environment)
        review = await catalog.plan(self.request(), self.environment)
        self.assertEqual(next(step for step in ordinary.steps if step.module_id == 'stable_audio3').operation, 'reuse')
        self.assertEqual(review.stable_training_variant, 'small-music')
        self.assertEqual([(step.module_id, step.operation) for step in review.steps], [('stable_audio3', 'verify')])
        self.assertEqual(review.estimated_download_bytes, 1133744146)
        self.assertEqual(review.required_free_bytes, 2 * 1133744146)
        self.assertFalse(review.download_size_unknown)
        self.assertTrue(review.can_install)
        self.assertTrue(any(row.declared_license == 'Stability AI Community License' for row in review.steps[0].licenses))
        self.assertTrue(any(row.declared_license == 'Gemma Terms of Use' for row in review.steps[0].licenses))
        self.assertNotEqual(ordinary.plan_token, review.plan_token)
        self.assertNotEqual(catalog.approval_scope(self.request(), self.environment), catalog.approval_scope(self.request('medium'), self.environment))

    async def test_small_and_medium_readiness_plan_bytes_and_tokens_follow_selected_missing_files(self) -> None:
        review = await catalog.plan(self.request(), self.environment)
        for path in runtime.training_weights(self.environment, 'small-music'):
            self.put(path)
        ready = await catalog.plan(self.request(), self.environment)
        medium = await catalog.plan(self.request('medium'), self.environment)
        self.assertEqual(ready.steps[0].operation, 'reuse')
        self.assertEqual(ready.estimated_download_bytes, 0)
        self.assertFalse(ready.can_install)
        self.assertNotEqual(review.plan_token, ready.plan_token)
        self.assertEqual(medium.estimated_download_bytes, 4611590060)
        self.assertTrue(medium.can_install)
        self.assertFalse((self.environment.root / '.setup').exists(), 'review is read only')

    async def test_training_requires_verified_installed_trainer_and_space_in_destination(self) -> None:
        with patch.object(runtime, 'environment_verified', return_value=False):
            unavailable = await catalog.plan(self.request(), self.environment)
        self.assertFalse(unavailable.can_install)
        self.assertEqual(unavailable.steps[0].operation, 'manual')
        unsupported = await catalog.plan(self.request(), replace(self.environment, platform='linux', architecture='x86_64'))
        self.assertFalse(unsupported.can_install)
        self.assertEqual(unsupported.steps[0].operation, 'unsupported')
        def space(path: Path) -> int:
            return 1 if path.name == 'mlx' else 100 * 1024**3
        with patch.object(catalog, 'available_bytes', side_effect=space):
            full = await catalog.plan(self.request(), self.environment)
        self.assertFalse(full.can_install)
        self.assertEqual(full.free_bytes, 1)

    async def test_job_cancel_reload_resume_reuses_completed_asset_and_keeps_selected_variant(self) -> None:
        review = await catalog.plan(self.request(), self.environment)
        self.assertEqual(review.stable_training_variant, 'small-music')
        started = asyncio.Event()
        downloaded: list[str] = []
        block_encoder = True
        async def download(artifact: DownloadArtifact, destination: Path, context: jobs.InstallContext, **_: object) -> Path:
            downloaded.append(destination.name)
            if destination.name == 'same_s_encoder_f32.npz' and block_encoder:
                started.set()
                await asyncio.Event().wait()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(npz())
            return destination
        service = jobs.ModuleJobService(self.environment)
        try:
            with patch('app.module_install.download_artifact', side_effect=download), patch('app.module_install.install', new=AsyncMock(side_effect=AssertionError('dependency install forbidden'))), patch('app.module_install.verify', new=AsyncMock(side_effect=AssertionError('inference verify forbidden'))):
                created = await service.create(ModuleInstallRequest(**self.request().model_dump(), plan_token=review.plan_token))
                await asyncio.wait_for(started.wait(), 1)
                await service.cancel(created.id)
                self.assertEqual(service.get(created.id).state, 'cancelled')
                self.assertTrue(runtime.training_weights(self.environment, 'small-music')[1].is_file())
                self.assertFalse(runtime.training_weights(self.environment, 'small-music')[2].exists())
                reader = jobs.ModuleJobService(self.environment)
                self.assertEqual(reader.get(created.id).stable_training_variant, 'small-music')
                self.assertEqual(reader.store.read(created.id).plan.stable_training_variant, 'small-music')
                block_encoder = False
                await reader.resume(created.id)
                await self.wait_for_terminal(reader, created.id)
                self.assertEqual(reader.get(created.id).state, 'completed')
                self.assertEqual(downloaded, ['dit_sm-music-base_f16.npz', 'same_s_encoder_f32.npz', 'same_s_encoder_f32.npz'])
                await reader.shutdown()
        finally:
            await service.shutdown()

    async def test_changed_variant_token_cannot_launch_or_download(self) -> None:
        review = await catalog.plan(self.request(), self.environment)
        self.assertEqual(review.stable_training_variant, 'small-music')
        service = jobs.ModuleJobService(self.environment)
        with patch('app.module_install.download_artifact', new=AsyncMock()) as download:
            with self.assertRaisesRegex(jobs.ModuleSetupError, 'plan_changed'):
                await service.create(ModuleInstallRequest(**self.request('medium').model_dump(), plan_token=review.plan_token))
        download.assert_not_awaited()
        self.assertFalse(service.list().jobs)

    async def test_cancel_during_last_publication_can_resume_to_complete_without_downloads(self) -> None:
        review = await catalog.plan(self.request(), self.environment)
        service = jobs.ModuleJobService(self.environment)
        reader = jobs.ModuleJobService(self.environment)
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        publish = models._publish
        def blocked(source: Path, root: Path, relative: str) -> None:
            if source.name == 'same_s_encoder_f32.npz':
                loop.call_soon_threadsafe(entered.set)
                if not release.wait(5):
                    raise TimeoutError('fixture publication not released')
            publish(source, root, relative)
        async def download(artifact: DownloadArtifact, destination: Path, context: jobs.InstallContext, **_: object) -> Path:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(npz())
            return destination
        try:
            with patch('app.module_install.download_artifact', side_effect=download), patch.object(models, '_publish', side_effect=blocked):
                created = await service.create(ModuleInstallRequest(**self.request().model_dump(), plan_token=review.plan_token))
                await asyncio.wait_for(entered.wait(), 1)
                worker = service.tasks[created.id]
                cancelling = asyncio.create_task(service.cancel(created.id))
                await asyncio.sleep(0)
                self.assertTrue(worker.cancelling())
                release.set()
                cancelled = await asyncio.wait_for(cancelling, 2)
                self.assertEqual(cancelled.state, 'cancelled')
            self.assertTrue(runtime.status(self.environment).small_weights_ready)
            with patch('app.module_install.download_artifact', new=AsyncMock(side_effect=AssertionError('assets already present'))) as download_again, patch('app.module_install.install', new=AsyncMock(side_effect=AssertionError('dependency install forbidden'))), patch('app.module_install.verify', new=AsyncMock(side_effect=AssertionError('inference verification forbidden'))):
                with patch.object(catalog, 'CATALOG_VERSION', 'changed-pins'):
                    with self.assertRaisesRegex(jobs.ModuleSetupError, 'plan_changed'):
                        await reader.resume(created.id)
                await reader.resume(created.id)
                await self.wait_for_terminal(reader, created.id)
                completed = reader.get(created.id)
                self.assertEqual(completed.state, 'completed')
                self.assertEqual(completed.steps[0].state, 'verified')
                ready = await catalog.plan(self.request(), self.environment)
                with self.assertRaisesRegex(jobs.ModuleSetupError, 'already_installed'):
                    await reader.create(ModuleInstallRequest(**self.request().model_dump(), plan_token=ready.plan_token))
            download_again.assert_not_awaited()
        finally:
            release.set()
            await reader.shutdown()
            await service.shutdown()
