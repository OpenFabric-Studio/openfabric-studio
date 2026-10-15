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
