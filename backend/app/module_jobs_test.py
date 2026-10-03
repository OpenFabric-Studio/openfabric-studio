from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
import sys
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app import module_catalog as catalog
from app.module_contracts import ModuleInstallRequest, ModulePlanRequest
from app import module_jobs as jobs


class ModuleJobsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = catalog.ModuleEnvironment.for_root(self.root, platform='darwin', architecture='arm64')
        self.probe_patch = patch.object(catalog, 'probe', AsyncMock(return_value=catalog.ProbeResult(False, '')))
        self.probe_patch.start()
        self.addCleanup(self.probe_patch.stop)
        self.enterContext(patch.object(jobs, '_application_busy', return_value=False))

    async def request(self, features: list[catalog.ModuleId]) -> ModuleInstallRequest:
        plan = await catalog.plan(ModulePlanRequest(features=features), self.environment)
        return ModuleInstallRequest(features=features, plan_token=plan.plan_token)

    async def wait_for_terminal(self, service: jobs.ModuleJobService, identifier: str) -> None:
        for _ in range(100):
            if service.get(identifier).state not in ('queued', 'running'):
                return
            await asyncio.sleep(0.01)
        self.fail('Installation did not settle')

    async def test_manual_steps_persist_without_claiming_success(self) -> None:
        service = jobs.ModuleJobService(self.environment)
        created = await service.create(await self.request(['ebooks']))
        await self.wait_for_terminal(service, created.id)
        self.assertEqual(service.get(created.id).state, 'awaiting_manual')
        other = jobs.ModuleJobService(self.environment)
        self.assertEqual(other.get(created.id).steps[0].state, 'manual')
        await service.shutdown()

    async def test_create_requires_unchanged_reviewed_plan(self) -> None:
        service = jobs.ModuleJobService(self.environment)
        request = await self.request(['ebooks'])
        request.plan_token = '0' * 64
        with self.assertRaisesRegex(jobs.ModuleSetupError, 'plan_changed'):
            await service.create(request)
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_concurrent_requests_share_one_owned_installation(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        async def install(context: jobs.InstallContext, identifier: catalog.ModuleId, download: bool) -> jobs.InstallOutcome:
            started.set()
            await release.wait()
            return jobs.InstallOutcome('verified', 'Components verified.')
        service = jobs.ModuleJobService(self.environment, installer=install)
        created = await service.create(await self.request(['media']))
        await started.wait()
        with self.assertRaisesRegex(jobs.ModuleSetupError, 'setup_busy'):
            await service.create(await self.request(['media']))
        release.set()
        await self.wait_for_terminal(service, created.id)
        self.assertEqual(service.get(created.id).state, 'completed')
        await service.shutdown()

    async def test_cancel_waits_for_worker_cleanup_and_survives_reload(self) -> None:
        started = asyncio.Event()
        cleaned = asyncio.Event()
        async def install(context: jobs.InstallContext, identifier: catalog.ModuleId, download: bool) -> jobs.InstallOutcome:
            try:
                started.set()
                await context.run([sys.executable, '-c', 'import time;time.sleep(60)'])
            finally:
                cleaned.set()
            return jobs.InstallOutcome('verified', 'Components verified.')
        service = jobs.ModuleJobService(self.environment, installer=install)
        created = await service.create(await self.request(['media']))
        await started.wait()
        cancelled = await service.cancel(created.id)
        self.assertTrue(cleaned.is_set())
        self.assertEqual(cancelled.state, 'cancelled')
        self.assertEqual(jobs.ModuleJobService(self.environment).get(created.id).state, 'cancelled')
        await service.shutdown()

    async def test_failures_expose_stable_codes_without_raw_exception(self) -> None:
        async def install(context: jobs.InstallContext, identifier: catalog.ModuleId, download: bool) -> jobs.InstallOutcome:
            raise RuntimeError('private token=secret /private/internal/path')
        service = jobs.ModuleJobService(self.environment, installer=install)
        created = await service.create(await self.request(['media']))
        await self.wait_for_terminal(service, created.id)
        failed = service.get(created.id)
        self.assertEqual(failed.state, 'failed')
        self.assertEqual(failed.error_code, 'installation_failed')
        self.assertNotIn('secret', failed.model_dump_json())
        await service.shutdown()

    async def test_recovery_marks_interrupted_jobs_without_automatic_downloads(self) -> None:
        service = jobs.ModuleJobService(self.environment)
        created = await service.create(await self.request(['ebooks']))
        await self.wait_for_terminal(service, created.id)
        stored = service.store.read(created.id)
        stored.job.state = 'running'
        service.store.write(stored)
        recovered = jobs.ModuleJobService(self.environment)
        with patch.object(recovered, 'installer', AsyncMock(side_effect=AssertionError('must not install'))):
            await recovered.recover()
        self.assertEqual(recovered.get(created.id).state, 'interrupted')
        await service.shutdown()

    async def test_resume_is_explicit_and_rechecks_disk_and_plan(self) -> None:
        service = jobs.ModuleJobService(self.environment)
        created = await service.create(await self.request(['ebooks']))
        await self.wait_for_terminal(service, created.id)
        resumed = await service.resume(created.id)
        await self.wait_for_terminal(service, resumed.id)
        self.assertEqual(service.get(created.id).state, 'awaiting_manual')
        await service.shutdown()

    async def test_resume_cannot_bypass_another_backend_active_job(self) -> None:
        first = jobs.ModuleJobService(self.environment)
        old = await first.create(await self.request(['ebooks']))
        await self.wait_for_terminal(first, old.id)
        started = asyncio.Event()
        async def install(context: jobs.InstallContext, identifier: catalog.ModuleId, download: bool) -> jobs.InstallOutcome:
            started.set()
            await asyncio.Event().wait()
            return jobs.InstallOutcome('verified', 'unused')
        running = jobs.ModuleJobService(self.environment, installer=install)
        active = await running.create(await self.request(['media']))
        await started.wait()
        with self.assertRaisesRegex(jobs.ModuleSetupError, 'setup_busy'):
            await jobs.ModuleJobService(self.environment).resume(old.id)
        await running.cancel(active.id)
        await first.shutdown()

    async def test_resume_requires_review_for_changed_catalog_artifacts(self) -> None:
        service = jobs.ModuleJobService(self.environment)
        old = await service.create(await self.request(['ebooks']))
        await self.wait_for_terminal(service, old.id)
        with patch.object(catalog, 'CATALOG_VERSION', 'changed-pins'):
            with self.assertRaisesRegex(jobs.ModuleSetupError, 'plan_changed'):
                await service.resume(old.id)
        self.assertEqual(service.get(old.id).state, 'awaiting_manual')
        await service.shutdown()

    async def test_setup_and_speech_admission_exclude_each_other(self) -> None:
        service = jobs.ModuleJobService(self.environment)
        with jobs.speech_admission():
            with self.assertRaisesRegex(jobs.ModuleSetupError, 'setup_busy'):
                await service.create(await self.request(['ebooks']))
        started = asyncio.Event()
        async def install(context: jobs.InstallContext, identifier: catalog.ModuleId, download: bool) -> jobs.InstallOutcome:
            started.set()
            await asyncio.Event().wait()
            return jobs.InstallOutcome('verified', 'unused')
        service = jobs.ModuleJobService(self.environment, installer=install)
        active = await service.create(await self.request(['media']))
        await started.wait()
        self.assertTrue(jobs.work_busy())
        with self.assertRaisesRegex(jobs.ModuleSetupError, 'setup_busy'):
            with jobs.speech_admission():
                self.fail('speech must not enter')
        await service.cancel(active.id)
        self.assertFalse(jobs.work_busy())
        await service.shutdown()

    async def test_failed_worker_drain_keeps_setup_quarantined_until_recovered(self) -> None:
        async def install(context: jobs.InstallContext, identifier: catalog.ModuleId, download: bool) -> jobs.InstallOutcome:
            stored = context.service.store.read(context.identifier)
            stored.worker = jobs.WorkerIdentity(pid=12345, token='a' * 32,
                receipt=str(context.environment.root / '.setup/workers' / f'{context.identifier}.json'))
            context.service.store.write(stored)
            raise jobs.ModuleSetupError('worker_unverified')
        service = jobs.ModuleJobService(self.environment, installer=install)
        created = await service.create(await self.request(['media']))
        await self.wait_for_terminal(service, created.id)
        self.assertTrue(jobs.work_busy())
        with patch.object(jobs, 'terminate_verified', AsyncMock(return_value=True)):
            await service.cancel(created.id)
        self.assertFalse(jobs.work_busy())
        await service.shutdown()

    async def test_another_backend_cannot_recover_a_live_owned_installation(self) -> None:
        started = asyncio.Event()
        async def install(context: jobs.InstallContext, identifier: catalog.ModuleId, download: bool) -> jobs.InstallOutcome:
            started.set()
            await asyncio.Event().wait()
            return jobs.InstallOutcome('verified', 'unused')
        service = jobs.ModuleJobService(self.environment, installer=install)
        created = await service.create(await self.request(['media']))
        await started.wait()
        other = jobs.ModuleJobService(self.environment)
        await other.recover()
        self.assertEqual(service.get(created.id).state, 'running')
        with self.assertRaisesRegex(jobs.ModuleSetupError, 'setup_busy'):
            await other.cancel(created.id)
        await service.cancel(created.id)

    async def test_read_only_job_views_do_not_migrate_or_write_database(self) -> None:
        service = jobs.ModuleJobService(self.environment)
        created = await service.create(await self.request(['ebooks']))
        await self.wait_for_terminal(service, created.id)
        with sqlite3.connect(service.store.path) as connection:
            connection.execute('PRAGMA user_version=17')
        before = service.store.path.read_bytes()
        self.assertEqual(service.get(created.id).state, 'awaiting_manual')
        self.assertEqual(len(service.list().jobs), 1)
        self.assertEqual(before, service.store.path.read_bytes())
        await service.shutdown()

    async def test_idle_service_refreshes_changed_storage_environment(self) -> None:
        with patch.object(jobs, '_service', None), patch.object(jobs, 'configured_environment', return_value=self.environment):
            first = jobs.service()
            moved = replace(self.environment, data_dir=self.root / 'different-library')
            with patch.object(jobs, 'configured_environment', return_value=moved):
                refreshed = jobs.service()
            self.assertIsNot(first, refreshed)
            self.assertEqual(refreshed.environment.data_dir, moved.data_dir)


if __name__ == '__main__':
    unittest.main()
