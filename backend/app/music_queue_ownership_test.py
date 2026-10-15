"""Only the current coordinator and its inherited tasks may enter music work."""
from __future__ import annotations

import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app import music_queue_ownership as ownership, resource_admission, support_controls
from app.gpu_lease import gpu_lease
from app.stems import gpu_lock


class MusicQueueOwnershipTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.enterContext(patch.object(ownership, '_owner', None))
        self.enterContext(patch.object(resource_admission, 'admission_lock', asyncio.Lock()))
        self.enterContext(patch.object(resource_admission, '_native_inflight', 0))
        self.idle = self.enterContext(patch.object(support_controls, '_idle_state', new=AsyncMock(return_value='idle')))

    async def test_current_owner_can_submit_native_and_inherited_child_work(self) -> None:
        owner = await ownership.reserve()
        self.assertTrue(ownership.busy())
        with self.assertRaises(resource_admission.ResourceBusyError) as caught:
            await resource_admission.reserve_native(lambda: False)
        self.assertEqual(caught.exception.code, 'music_queue_busy')
        with self.assertRaises(HTTPException) as blocked:
            resource_admission.require_setup_idle()
        self.assertEqual(blocked.exception.detail, 'music_queue_busy')
        async def child() -> None:
            self.assertFalse(ownership.blocked())
            resource_admission.require_setup_idle()
            lease = await resource_admission.reserve_native(lambda: False)
            await lease.release()
            async with gpu_lease(gpu_lock, 'gpu_busy'):
                self.assertTrue(gpu_lock.locked())
        with owner.context():
            await asyncio.create_task(child())
        self.assertTrue(await owner.release())
        self.assertFalse(ownership.busy())

    async def test_stale_inherited_context_cannot_bypass_a_later_owner(self) -> None:
        first = await ownership.reserve()
        entered, continue_child = asyncio.Event(), asyncio.Event()
        async def old_child() -> None:
            entered.set()
            await continue_child.wait()
            self.assertTrue(ownership.blocked())
            with self.assertRaises(HTTPException):
                resource_admission.require_setup_idle()
        with first.context():
            task = asyncio.create_task(old_child())
        await entered.wait()
        self.assertTrue(await first.release())
        second = await ownership.reserve()
        continue_child.set()
        await task
        self.assertFalse(await first.release())
        self.assertTrue(ownership.busy())
        self.assertTrue(await second.release())

    async def test_failed_or_unknown_drain_retains_reservation(self) -> None:
        owner = await ownership.reserve()
        for state in ('busy', 'unknown'):
            self.idle.return_value = state
            self.assertFalse(await owner.release())
            self.assertTrue(ownership.busy())
        self.idle.return_value = 'idle'
        async with gpu_lock:
            self.assertFalse(await owner.release())
        with patch.object(resource_admission, '_native_inflight', 1):
            self.assertFalse(await owner.release())
        self.assertTrue(await owner.release())

    async def test_idle_accounting_window_blocks_synchronous_external_publication(self) -> None:
        entered, finish = asyncio.Event(), asyncio.Event()
        async def accounting() -> str:
            entered.set()
            await finish.wait()
            return 'idle'
        self.idle.side_effect = accounting
        task = asyncio.create_task(ownership.reserve())
        await entered.wait()
        with self.assertRaises(HTTPException):
            resource_admission.require_setup_idle()
        finish.set()
        owner = await task
        self.idle.side_effect = None
        self.assertTrue(await owner.release())

    async def test_failed_or_cancelled_claim_does_not_leave_a_reservation(self) -> None:
        self.idle.return_value = 'unknown'
        with self.assertRaises(resource_admission.ResourceBusyError):
            await ownership.reserve()
        self.assertFalse(ownership.busy())
        entered = asyncio.Event()
        async def accounting() -> str:
            entered.set()
            await asyncio.Event().wait()
            return 'idle'
        self.idle.side_effect = accounting
        task = asyncio.create_task(ownership.reserve())
        await entered.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(ownership.busy())

    async def test_external_gpu_work_is_rejected_without_acquiring_the_lock(self) -> None:
        owner = await ownership.reserve()
        with self.assertRaises(resource_admission.ResourceBusyError):
            async with gpu_lease(gpu_lock, 'gpu_busy'):
                self.fail('External work acquired the reserved accelerator')
        self.assertFalse(gpu_lock.locked())
        self.assertTrue(await owner.release())

    async def test_local_busy_excludes_only_the_current_owner(self) -> None:
        from app.work_busy import local_work_busy
        owner = await ownership.reserve()
        self.assertTrue(local_work_busy())
        with owner.context():
            self.assertFalse(local_work_busy())
        self.assertTrue(await owner.release())

    async def test_setup_reservation_never_uses_music_owner_bypass(self) -> None:
        from app import module_jobs
        owner = await ownership.reserve()
        with owner.context(), self.assertRaises(module_jobs.ModuleSetupError) as caught:
            module_jobs._reserve_setup()
        self.assertEqual(caught.exception.code, 'setup_busy')
        self.assertTrue(await owner.release())

    async def test_existing_speech_prevents_claim_and_queue_blocks_new_speech(self) -> None:
        from app import module_jobs
        with module_jobs.speech_admission():
            with self.assertRaises(resource_admission.ResourceBusyError):
                await ownership.reserve()
        owner = await ownership.reserve()
        with self.assertRaises(module_jobs.ModuleSetupError):
            with module_jobs.speech_admission():
                self.fail('Speech entered an active music reservation')
        self.assertTrue(await owner.release())

    async def test_manual_stop_drains_queue_with_no_active_native_engine(self) -> None:
        from fastapi import Request
        from app.api import routes_orchestrator
        self.enterContext(patch.object(ownership, '_stop_handler', None))
        self.enterContext(patch.object(routes_orchestrator.manager.state, 'active_model', None))
        owner = await ownership.reserve()
        async def stop_queue() -> None:
            self.assertTrue(await owner.release())
        callback = AsyncMock(side_effect=stop_queue)
        ownership.register_stop_handler(callback)
        request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'scheme': 'http', 'headers': [(b'host', b'127.0.0.1:9000')],
            'server': ('127.0.0.1', 9000), 'client': ('127.0.0.1', 3000), 'query_string': b''})
        await routes_orchestrator.stop(request)
        callback.assert_awaited_once()
        self.assertFalse(ownership.busy())
