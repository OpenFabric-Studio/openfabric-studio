from __future__ import annotations

import asyncio
import errno
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
        # Automatic speech setup is reviewed only when uv is visible. The installer itself is mocked.
        if self.environment.uv is None:
            self.environment = replace(self.environment, uv=str(self.root / 'fixture-uv'))
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

    async def test_explicit_ace_base_selection_is_persisted_and_uses_only_its_asset_installer(self) -> None:
        from app import song_capabilities, module_music_models
        python = self.root / 'fixture-python'; python.write_bytes(b'fixture')
        request = ModulePlanRequest(features=['ace_step'], download_models=True, ace_generation_model='acestep-v15-base')
        with patch.object(song_capabilities, 'source_ready', return_value=True), \
             patch.object(catalog, 'engine_python', return_value=python), \
             patch.object(catalog, 'available_bytes', return_value=100 * 1024**3), \
             patch.object(module_music_models, 'setup_ace_conditioning_models', new=AsyncMock(
                 return_value=jobs.InstallOutcome('verified', 'Fixture assets only'))) as prepare:
            reviewed = await catalog.plan(request, self.environment)
            service = jobs.ModuleJobService(self.environment)
            try:
                job = await service.create(ModuleInstallRequest(**request.model_dump(), plan_token=reviewed.plan_token))
                await self.wait_for_terminal(service, job.id)
                self.assertEqual(service.get(job.id).state, 'completed')
                self.assertEqual(service.get(job.id).ace_generation_model, 'acestep-v15-base')
                self.assertEqual(service.store.read(job.id).plan.ace_generation_model, 'acestep-v15-base')
                prepare.assert_awaited_once()
            finally:
                await service.shutdown()

    async def test_retired_setup_history_is_preserved_without_hiding_other_jobs(self) -> None:
        service = jobs.ModuleJobService(self.environment)
        retired = await service.create(await self.request(['ebooks']))
        await self.wait_for_terminal(service, retired.id)
        retained = await service.create(await self.request(['ebooks']))
        await self.wait_for_terminal(service, retained.id)
        document = service.store.read(retired.id).model_dump_json().replace('"ebooks"', '"heartmula"')
        with sqlite3.connect(service.store.path) as connection:
            connection.execute('UPDATE setup_jobs SET document=? WHERE id=?', (document, retired.id))
        before = service.store.path.read_bytes()
        self.assertEqual([job.id for job in service.list().jobs], [retained.id])
        with self.assertRaisesRegex(jobs.ModuleSetupError, 'job_missing'):
            service.get(retired.id)
        with self.assertRaisesRegex(jobs.ModuleSetupError, 'setup_not_resumable'):
            await service.resume(retired.id)
        self.assertEqual(before, service.store.path.read_bytes())
        await service.shutdown()

    async def test_history_limit_counts_supported_jobs_after_retired_backlog(self) -> None:
        service = jobs.ModuleJobService(self.environment)
        created = await service.create(await self.request(['ebooks']))
        await self.wait_for_terminal(service, created.id)
        template = service.store.read(created.id)
        expected: list[str] = []
        with sqlite3.connect(service.store.path) as connection:
            for number in range(1, 102):
                stored = template.model_copy(deep=True)
                stored.job.id = f'{number:032x}'
                document = stored.model_dump_json()
                if number <= 51:
                    expected.append(stored.job.id)
                else:
                    document = document.replace('"ebooks"', '"heartmula"')
                connection.execute('INSERT INTO setup_jobs(id,state,worker,document) VALUES(?,?,0,?)',
                    (stored.job.id, stored.job.state, document))
        before = service.store.path.read_bytes()
        self.assertEqual([job.id for job in service.list().jobs], list(reversed(expected[-50:])))
        self.assertEqual(before, service.store.path.read_bytes())
        await service.shutdown()

    async def test_retired_backlog_does_not_hide_corrupt_history_or_active_recovery(self) -> None:
        service = jobs.ModuleJobService(self.environment)
        created = await service.create(await self.request(['ebooks']))
        await self.wait_for_terminal(service, created.id)
        template = service.store.read(created.id)
        with sqlite3.connect(service.store.path) as connection:
            corrupt = template.model_dump_json().replace('"awaiting_manual"', '"invalid"')
            connection.execute('UPDATE setup_jobs SET document=? WHERE id=?', (corrupt, created.id))
            for number in range(1, 52):
                stored = template.model_copy(deep=True)
                stored.job.id = f'{number:032x}'
                stored.job.state = 'running' if number == 1 else 'awaiting_manual'
                document = stored.model_dump_json().replace('"ebooks"', '"heartmula"')
                connection.execute('INSERT INTO setup_jobs(id,state,worker,document) VALUES(?,?,0,?)',
                    (stored.job.id, stored.job.state, document))
        recovery = service.store.records(recovery=True)
        self.assertEqual([record.job.id for record in recovery], [f'{1:032x}'])
        self.assertIsNotNone(recovery[0]._retired_document)
        with self.assertRaisesRegex(jobs.ModuleSetupError, 'job_corrupt'):
            service.list()
        await service.shutdown()

    async def test_retired_setup_recovery_drains_workers_and_preserves_retired_ids(self) -> None:
        service = jobs.ModuleJobService(self.environment)
        created = await service.create(await self.request(['ebooks']))
        await self.wait_for_terminal(service, created.id)
        stored = service.store.read(created.id)
        stored.job.state = 'running'
        stored.job.current_step = 0
        stored.worker = jobs.WorkerIdentity(pid=12345, token='a' * 32,
            receipt=str(self.environment.root / '.setup/workers' / f'{created.id}.json'))
        document = stored.model_dump_json().replace('"ebooks"', '"heartmula"')
        with sqlite3.connect(service.store.path) as connection:
            connection.execute('UPDATE setup_jobs SET state=?,worker=1,document=? WHERE id=?', ('running', document, created.id))
        recovered = jobs.ModuleJobService(self.environment)
        try:
            with patch.object(jobs, 'terminate_verified', AsyncMock(return_value=False)) as terminate:
                await recovered.recover()
            terminate.assert_awaited_once_with(stored.worker)
            self.assertEqual(recovered.list().jobs, [])
            with self.assertRaisesRegex(jobs.ModuleSetupError, 'setup_busy'):
                await recovered.create(await self.request(['ebooks']))
            with patch.object(jobs, 'terminate_verified', AsyncMock(return_value=True)) as terminate:
                await recovered.recover()
            terminate.assert_awaited_once_with(stored.worker)
            with sqlite3.connect(service.store.path) as connection:
                state, worker, retained = connection.execute('SELECT state,worker,document FROM setup_jobs WHERE id=?', (created.id,)).fetchone()
            self.assertEqual(state, 'failed')
            self.assertEqual(worker, 0)
            self.assertIn('"heartmula"', retained)
            self.assertNotIn('"media"', retained)
            other = await recovered.create(await self.request(['ebooks']))
            await self.wait_for_terminal(recovered, other.id)
        finally:
            await recovered.shutdown()
            await service.shutdown()

    async def test_retired_setup_compatibility_still_rejects_corrupt_records(self) -> None:
        service = jobs.ModuleJobService(self.environment)
        created = await service.create(await self.request(['ebooks']))
        await self.wait_for_terminal(service, created.id)
        original = service.store.read(created.id).model_dump_json()
        documents = (original.replace('"ebooks"', '"unknown"'),
            original.replace('"ebooks"', '"heartmula"').replace('"awaiting_manual"', '"invalid"'))
        for document in documents:
            with self.subTest(document=document), sqlite3.connect(service.store.path) as connection:
                connection.execute('UPDATE setup_jobs SET document=? WHERE id=?', (document, created.id))
            with self.assertRaisesRegex(jobs.ModuleSetupError, 'job_corrupt'):
                service.list()
        await service.shutdown()

    async def test_download_bytes_and_retry_phase_survive_a_new_service_reader(self) -> None:
        from app import module_contracts
        self.assertTrue(hasattr(module_contracts, 'ModuleDownloadProgress'), 'download progress needs an app-owned persisted schema')
        started = asyncio.Event()
        release = asyncio.Event()
        async def install(context: jobs.InstallContext, identifier: catalog.ModuleId, download: bool) -> jobs.InstallOutcome:
            self.assertTrue(hasattr(context, 'update_download'), 'installer byte progress must reach the durable step')
            progress = module_contracts.ModuleDownloadProgress(artifact_name='pinned.zip', bytes_received=4, total_bytes=10,
                attempt=2, max_attempts=3, phase='retrying', retry_after_sec=2)
            context.update_download(progress)
            started.set()
            await release.wait()
            return jobs.InstallOutcome('verified', 'Verified fixture.')
        service = jobs.ModuleJobService(self.environment, installer=install)
        created = await service.create(await self.request(['media']))
        try:
            await asyncio.wait_for(started.wait(), 1)
            retained = jobs.ModuleJobService(self.environment).get(created.id)
            self.assertEqual(retained.steps[0].download.bytes_received, 4)
            self.assertEqual(retained.steps[0].download.phase, 'retrying')
            self.assertEqual(retained.steps[0].download.attempt, 2)
        finally:
            release.set()
            await service.shutdown()

    async def test_create_requires_unchanged_reviewed_plan(self) -> None:
        service = jobs.ModuleJobService(self.environment)
        request = await self.request(['ebooks'])
        request.plan_token = '0' * 64
        with self.assertRaisesRegex(jobs.ModuleSetupError, 'plan_changed'):
            await service.create(request)
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_space_is_rechecked_before_the_first_expensive_install_step(self) -> None:
        installer = AsyncMock(return_value=jobs.InstallOutcome('verified', 'Fixture installed.'))
        service = jobs.ModuleJobService(self.environment, installer=installer)
        try:
            created = await service.create(await self.request(['media']))
            with patch.object(catalog, 'available_bytes', return_value=1):
                await self.wait_for_terminal(service, created.id)
            self.assertEqual(service.get(created.id).error_code, 'insufficient_disk')
            installer.assert_not_awaited()
        finally:
            await service.shutdown()

    async def test_later_install_steps_recheck_space_after_a_dependency_finishes(self) -> None:
        installed: list[catalog.ModuleId] = []
        async def install(context: jobs.InstallContext, identifier: catalog.ModuleId, download: bool) -> jobs.InstallOutcome:
            installed.append(identifier)
            disk.return_value = 1
            return jobs.InstallOutcome('verified', 'Fixture installed.')
        service = jobs.ModuleJobService(self.environment, installer=install)
        try:
            with patch.object(catalog, 'available_bytes', return_value=100 * 1024**3) as disk:
                created = await service.create(await self.request(['speech']))
                await self.wait_for_terminal(service, created.id)
            self.assertEqual(installed, ['media'])
            self.assertEqual(service.get(created.id).steps[0].state, 'verified')
            self.assertEqual(service.get(created.id).error_code, 'insufficient_disk')
        finally:
            await service.shutdown()

    async def test_zero_download_verification_is_not_blocked_by_unknown_free_space(self) -> None:
        from app import module_install
        reviewed = await catalog.plan(ModulePlanRequest(features=['media']), self.environment)
        reviewed.steps[0].operation = 'verify'
        reviewed.steps[0].estimated_download_bytes = 0
        reviewed.required_free_bytes = 0
        reviewed.estimated_download_bytes = 0
        current = reviewed.model_copy(update={'free_bytes': None, 'can_install': False}, deep=True)
        service = jobs.ModuleJobService(self.environment)
        try:
            with patch.object(service, '_review', AsyncMock(return_value=reviewed)), patch.object(jobs, 'plan', AsyncMock(return_value=current)), \
                    patch.object(module_install, 'verify', AsyncMock(return_value=jobs.InstallOutcome('verified', 'Read-only verification.'))) as verify:
                created = await service.create(ModuleInstallRequest(features=['media'], plan_token=reviewed.plan_token))
                await self.wait_for_terminal(service, created.id)
            verify.assert_awaited_once()
            self.assertEqual(service.get(created.id).state, 'completed')
        finally:
            await service.shutdown()

    async def test_per_step_review_cannot_download_more_than_the_accepted_plan(self) -> None:
        reviewed = await catalog.plan(ModulePlanRequest(features=['media']), self.environment)
        reviewed.steps[0].estimated_download_bytes = 100
        current = reviewed.model_copy(deep=True)
        current.steps[0].estimated_download_bytes = 101
        installer = AsyncMock(return_value=jobs.InstallOutcome('verified', 'Fixture installed.'))
        service = jobs.ModuleJobService(self.environment, installer=installer)
        try:
            with patch.object(service, '_review', AsyncMock(return_value=reviewed)), patch.object(jobs, 'plan', AsyncMock(return_value=current)):
                created = await service.create(ModuleInstallRequest(features=['media'], plan_token=reviewed.plan_token))
                await self.wait_for_terminal(service, created.id)
            self.assertEqual(service.get(created.id).error_code, 'plan_changed')
            installer.assert_not_awaited()
        finally:
            await service.shutdown()

    async def test_disk_full_installer_error_has_a_stable_failure_code(self) -> None:
        sentinel = self.root / 'preserved-original'
        sentinel.write_bytes(b'keep this')
        service = jobs.ModuleJobService(self.environment, installer=AsyncMock(side_effect=OSError(errno.ENOSPC, 'disk full')))
        try:
            created = await service.create(await self.request(['media']))
            await self.wait_for_terminal(service, created.id)
            self.assertEqual(service.get(created.id).error_code, 'insufficient_disk')
            self.assertEqual(sentinel.read_bytes(), b'keep this')
        finally:
            await service.shutdown()

    async def test_installed_only_request_cannot_create_a_job_or_run_an_installer(self) -> None:
        status = await catalog.inventory(self.environment)
        for module in status.modules:
            if module.id == 'media':
                module.state = 'ready'
        installer = AsyncMock(side_effect=AssertionError('already installed'))
        service = jobs.ModuleJobService(self.environment, installer=installer)
        with patch.object(catalog, 'inventory', AsyncMock(return_value=status)):
            request = await self.request(['media'])
            with self.assertRaisesRegex(jobs.ModuleSetupError, 'already_installed'):
                await service.create(request)
        installer.assert_not_awaited()
        self.assertEqual(service.list().jobs, [])
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_reinstall_intent_is_persisted_and_retained_on_explicit_retry(self) -> None:
        status = await catalog.inventory(self.environment)
        for module in status.modules:
            if module.id in ('speech', 'media'):
                module.state = 'installed'
            if module.id == 'speech':
                module.can_reinstall = True
        attempts: list[list[catalog.ModuleId]] = []
        async def installer(context: jobs.InstallContext, identifier: catalog.ModuleId, download: bool) -> jobs.InstallOutcome:
            self.assertEqual(identifier, 'speech', 'installed media must not run an installer')
            attempts.append(context.service.store.read(context.identifier).plan.reinstall)
            if len(attempts) == 1:
                raise jobs.ModuleSetupError('installer_failed')
            return jobs.InstallOutcome('verified', 'Fixture dependencies reinstalled.')
        service = jobs.ModuleJobService(self.environment, installer=installer)
        try:
            with patch.object(catalog, 'inventory', AsyncMock(return_value=status)):
                reviewed = await catalog.plan(ModulePlanRequest(features=['speech'], reinstall=['speech']), self.environment)
                created = await service.create(ModuleInstallRequest(features=['speech'], reinstall=['speech'], plan_token=reviewed.plan_token))
                await self.wait_for_terminal(service, created.id)
                self.assertEqual(service.get(created.id).state, 'failed')
                self.assertEqual(jobs.ModuleJobService(self.environment).get(created.id).reinstall, ['speech'])
                await service.resume(created.id)
                await self.wait_for_terminal(service, created.id)
            self.assertEqual(service.get(created.id).state, 'completed')
            self.assertEqual(attempts, [['speech'], ['speech']])
        finally:
            await service.shutdown()

    async def test_reinstall_retry_reuses_completed_engines_and_keeps_unfinished_intent(self) -> None:
        status = await catalog.inventory(self.environment)
        for module in status.modules:
            if module.id in ('media', 'speech', 'stable_audio3'):
                module.state = 'installed'
                module.supported = True
                module.can_reinstall = module.id != 'media'
        attempts: list[catalog.ModuleId] = []
        async def installer(context: jobs.InstallContext, identifier: catalog.ModuleId, download: bool) -> jobs.InstallOutcome:
            attempts.append(identifier)
            if identifier == 'stable_audio3' and attempts.count(identifier) == 1:
                raise jobs.ModuleSetupError('installer_failed')
            return jobs.InstallOutcome('verified', 'Fixture dependency reinstall complete.')
        service = jobs.ModuleJobService(self.environment, installer=installer)
        try:
            with patch.object(catalog, 'inventory', AsyncMock(return_value=status)):
                reviewed = await catalog.plan(ModulePlanRequest(features=['speech', 'stable_audio3'], reinstall=['speech', 'stable_audio3']), self.environment)
                created = await service.create(ModuleInstallRequest(features=reviewed.features, reinstall=reviewed.reinstall, plan_token=reviewed.plan_token))
                await self.wait_for_terminal(service, created.id)
                self.assertEqual(service.get(created.id).state, 'failed')
                await service.resume(created.id)
                self.assertEqual(service.store.read(created.id).plan.reinstall, ['stable_audio3'])
                await service.cancel(created.id)
                await service.resume(created.id)
                await self.wait_for_terminal(service, created.id)
            self.assertEqual(service.get(created.id).state, 'completed')
            self.assertEqual(attempts, ['speech', 'stable_audio3', 'stable_audio3'])
            self.assertEqual(service.get(created.id).reinstall, ['speech', 'stable_audio3'])
            self.assertEqual(service.store.read(created.id).plan.reinstall, ['stable_audio3'])
        finally:
            await service.shutdown()

    async def test_late_progress_cannot_mutate_cancelled_job_or_its_last_bytes(self) -> None:
        from app.module_contracts import ModuleDownloadProgress
        contexts: list[jobs.InstallContext] = []
        started = asyncio.Event()
        retained = ModuleDownloadProgress(artifact_name='model.bin', bytes_received=5, total_bytes=20, attempt=1, phase='downloading')
        async def install(context: jobs.InstallContext, identifier: catalog.ModuleId, download: bool) -> jobs.InstallOutcome:
            contexts.append(context)
            context.update_download(retained)
            started.set()
            await asyncio.Event().wait()
            return jobs.InstallOutcome('verified', 'unreachable')
        service = jobs.ModuleJobService(self.environment, installer=install)
        try:
            created = await service.create(await self.request(['media']))
            await started.wait()
            cancelled = await service.cancel(created.id)
            contexts[0].update_download(ModuleDownloadProgress(artifact_name='model.bin', bytes_received=20, total_bytes=20, attempt=1, phase='complete'))
            contexts[0].update_detail('A late phase must not replace the cancelled state.')
            contexts[0].record_output()
            self.assertEqual(jobs.ModuleJobService(self.environment).get(created.id), cancelled)
            self.assertEqual(cancelled.steps[0].download, retained)
        finally:
            await service.shutdown()

    async def test_native_progress_stream_is_bounded_validated_and_survives_reload(self) -> None:
        from app.module_contracts import ModuleDownloadProgress
        progress = ModuleDownloadProgress(artifact_name='model.gguf', bytes_received=5, total_bytes=20, attempt=2, phase='retrying', retry_after_sec=1)
        payload = 'OPENFABRIC_DOWNLOAD_PROGRESS ' + progress.model_dump_json()
        script = 'import sys;sys.stdout.write(' + repr('OPENFABRIC_DOWNLOAD_PROGRESS ' + 'x' * 10000 + '\n' + 'OPENFABRIC_DOWNLOAD_PROGRESS {"bytes_received":-1}\n' + payload + '\n') + ');sys.stdout.flush()'
        async def install(context: jobs.InstallContext, identifier: catalog.ModuleId, download: bool) -> jobs.InstallOutcome:
            await context.run([sys.executable, '-c', script], download_progress=True)
            return jobs.InstallOutcome('verified', 'Offline fixture.')
        service = jobs.ModuleJobService(self.environment, installer=install)
        try:
            created = await service.create(await self.request(['media']))
            await self.wait_for_terminal(service, created.id)
            retained = jobs.ModuleJobService(self.environment).get(created.id)
            self.assertEqual(retained.state, 'completed')
            self.assertEqual(retained.steps[0].download, progress)
        finally:
            await service.shutdown()

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

    async def test_automatic_setup_detail_survives_reload_and_clears_previous_download(self) -> None:
        from app.module_contracts import ModuleDownloadProgress
        started, release = asyncio.Event(), asyncio.Event()
        async def installer(context: jobs.InstallContext, identifier: catalog.ModuleId, download: bool) -> jobs.InstallOutcome:
            context.update_download(ModuleDownloadProgress(artifact_name='model.safetensors', bytes_received=10, total_bytes=10, attempt=1, max_attempts=3, phase='complete'))
            context.update_detail('Verifying installed music weights.')
            started.set()
            await release.wait()
            return jobs.InstallOutcome('verified', 'Automatic setup complete.')
        service = jobs.ModuleJobService(self.environment, installer=installer)
        created = await service.create(await self.request(['media']))
        try:
            await asyncio.wait_for(started.wait(), 2)
            other = jobs.ModuleJobService(self.environment).get(created.id)
            self.assertEqual(other.steps[0].detail, 'Verifying installed music weights.')
            self.assertIsNone(other.steps[0].download)
            self.assertEqual(other.state, 'running')
        finally:
            release.set()
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

    async def test_shutdown_clears_queued_task_cancelled_before_execution_for_resume(self) -> None:
        service = jobs.ModuleJobService(self.environment)
        try:
            created = await service.create(await self.request(['ebooks']))
            service.tasks[created.id].cancel()
            await service.shutdown()
            self.assertEqual(service.tasks, {})
            self.assertEqual(service.get(created.id).state, 'interrupted')
            await service.resume(created.id)
            await self.wait_for_terminal(service, created.id)
            self.assertEqual(service.get(created.id).state, 'awaiting_manual')
        finally:
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

    async def test_failure_preserves_phase_and_safe_recovery_across_readers(self) -> None:
        async def installer(context: jobs.InstallContext, identifier: catalog.ModuleId, download: bool) -> jobs.InstallOutcome:
            context.update_detail('Verifying installed music weights.')
            raise jobs.ModuleSetupError('artifact_integrity')
        service = jobs.ModuleJobService(self.environment, installer=installer)
        try:
            created = await service.create(await self.request(['media']))
            await self.wait_for_terminal(service, created.id)
            failed = jobs.ModuleJobService(self.environment).get(created.id)
            step = failed.steps[0]
            self.assertEqual(step.detail, 'Verifying installed music weights.')
            self.assertIsNotNone(step.failure)
            assert step.failure is not None
            self.assertEqual(step.failure.code, 'artifact_integrity')
            self.assertEqual(step.failure.phase, step.detail)
            self.assertTrue(step.failure.retryable)
            self.assertIn('verification', step.failure.summary.lower())
            self.assertIn('preserved', step.failure.recovery.lower())
            self.assertIn(step.detail, [event.detail for event in step.events])
        finally:
            await service.shutdown()

    async def test_legacy_failure_has_safe_diagnostics_without_inventing_a_phase(self) -> None:
        service = jobs.ModuleJobService(self.environment)
        try:
            created = await service.create(await self.request(['ebooks']))
            await self.wait_for_terminal(service, created.id)
            saved = service.store.read(created.id)
            saved.job.state = 'failed'
            saved.job.error_code = 'private token=secret'
            saved.job.steps[0].state = 'failed'
            saved.job.steps[0].error_code = 'artifact_integrity'
            service.store.write(saved)
            read = service.get(created.id)
            self.assertIsNotNone(read.steps[0].failure)
            assert read.steps[0].failure is not None
            self.assertIsNone(read.steps[0].failure.phase)
            self.assertNotIn('secret', read.model_dump_json())
            self.assertIsNotNone(service.list().jobs[0].steps[0].failure)
            self.assertIsNone(service.store.read(created.id).job.steps[0].failure)
            saved.job.error_code = 'worker_unverified'
            saved.job.current_step = 0
            saved.job.steps[0].state = 'running'
            saved.job.steps[0].failure = read.steps[0].failure
            service.store.write(saved)
            legacy = service.get(created.id)
            self.assertEqual(legacy.steps[0].state, 'failed')
            self.assertIsNotNone(legacy.steps[0].failure)
            assert legacy.steps[0].failure is not None
            self.assertFalse(legacy.steps[0].failure.retryable)
            self.assertIsNone(legacy.steps[0].failure.phase)
            self.assertEqual(service.store.read(created.id).job.steps[0].state, 'running')
        finally:
            await service.shutdown()

    async def test_phase_history_is_bounded_and_output_time_survives_reload(self) -> None:
        async def installer(context: jobs.InstallContext, identifier: catalog.ModuleId, download: bool) -> jobs.InstallOutcome:
            for index in range(60):
                context.update_detail(f'Checking component {index}.')
            await context.run([sys.executable, '-c', 'print("checked")'])
            return jobs.InstallOutcome('verified', 'Components checked.')
        service = jobs.ModuleJobService(self.environment, installer=installer)
        try:
            created = await service.create(await self.request(['media']))
            await self.wait_for_terminal(service, created.id)
            step = jobs.ModuleJobService(self.environment).get(created.id).steps[0]
            self.assertLessEqual(len(step.events), 40)
            self.assertIsNotNone(step.last_output_at)
            self.assertEqual(step.events[-1].detail, 'Components checked.')
        finally:
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
        history = service.get(created.id).steps[0].events
        resumed = await service.resume(created.id)
        self.assertIsNone(resumed.current_step)
        self.assertEqual(resumed.steps[0].events, history)
        self.assertIsNone(resumed.steps[0].failure)
        self.assertIsNone(resumed.steps[0].last_output_at)
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

    async def test_resume_requires_review_when_verification_would_download_more(self) -> None:
        for estimate in (5_000_000_000, None):
            with self.subTest(estimate=estimate):
                service = jobs.ModuleJobService(self.environment)
                old = await service.create(await self.request(['ebooks']))
                await self.wait_for_terminal(service, old.id)
                stored = service.store.read(old.id)
                stored.plan.steps[0].operation = 'verify'
                stored.plan.steps[0].estimated_download_bytes = 100
                service.store.write(stored)
                current = stored.plan.model_copy(deep=True)
                current.steps[0].estimated_download_bytes = estimate
                try:
                    with patch.object(service, '_review', AsyncMock(return_value=current)):
                        with self.assertRaisesRegex(jobs.ModuleSetupError, 'plan_changed'):
                            await service.resume(old.id)
                    self.assertEqual(service.store.read(old.id).plan.steps[0].estimated_download_bytes, 100)
                    self.assertEqual(service.get(old.id).state, 'awaiting_manual')
                finally:
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

    async def test_unverified_recovered_worker_has_a_non_retryable_failed_step(self) -> None:
        service = jobs.ModuleJobService(self.environment)
        created = await service.create(await self.request(['ebooks']))
        await self.wait_for_terminal(service, created.id)
        stored = service.store.read(created.id)
        stored.job.state = 'running'
        stored.job.current_step = 0
        stored.job.steps[0].state = 'running'
        stored.worker = jobs.WorkerIdentity(pid=12345, token='a' * 32,
            receipt=str(self.environment.root / '.setup/workers' / f'{created.id}.json'))
        service.store.write(stored)
        try:
            with patch.object(jobs, 'terminate_verified', AsyncMock(return_value=False)):
                await service.recover()
            failed = service.get(created.id)
            self.assertEqual(failed.state, 'failed')
            self.assertEqual(failed.steps[0].state, 'failed')
            failure = failed.steps[0].failure
            self.assertIsNotNone(failure)
            assert failure is not None
            self.assertEqual(failure.code, 'worker_unverified')
            self.assertFalse(failure.retryable)
        finally:
            with patch.object(jobs, 'terminate_verified', AsyncMock(return_value=True)):
                await service.cancel(created.id)
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
