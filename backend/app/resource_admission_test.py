"""Local video/native submissions cannot pass each other's admission window."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from app import resource_admission as admission


class ResourceAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_exclusive_model_reservation_does_not_block_another_model(self) -> None:
        first = await admission.reserve_native(lambda: False, model_id='yue2', exclusive=True)
        try:
            with self.assertRaises(admission.ResourceBusyError):
                await admission.reserve_native(lambda: False, model_id='yue2', exclusive=True)
            other = await admission.reserve_native(lambda: False, model_id='ace_step', exclusive=True)
            await other.release()
            self.assertTrue(admission.native_work_inflight())
        finally:
            await first.release()
        self.assertFalse(admission.native_work_inflight())

    async def asyncSetUp(self) -> None:
        self.lock = patch.object(admission, "admission_lock", asyncio.Lock())
        self.lock.start()
        self.count = patch.object(admission, "_native_inflight", 0)
        self.count.start()

    async def asyncTearDown(self) -> None:
        self.count.stop()
        self.lock.stop()

    async def test_busy_video_rejects_native_without_reservation(self) -> None:
        with self.assertRaises(admission.ResourceBusyError):
            await admission.reserve_native(lambda: True)
        self.assertFalse(admission.native_work_inflight())

    async def test_native_reservation_is_visible_until_idempotent_release(self) -> None:
        lease = await admission.reserve_native(lambda: False)
        self.assertTrue(admission.native_work_inflight())
        await asyncio.gather(lease.release(), lease.release())
        self.assertFalse(admission.native_work_inflight())

    async def test_exception_drains_context_reservation(self) -> None:
        with self.assertRaisesRegex(ValueError, "fixture"):
            async with admission.native_admission(lambda: False):
                self.assertTrue(admission.native_work_inflight())
                raise ValueError("fixture")
        self.assertFalse(admission.native_work_inflight())

    async def test_admission_checks_busy_after_waiting_for_shared_lock(self) -> None:
        busy = False
        async with admission.admission_lock:
            pending = asyncio.create_task(admission.reserve_native(lambda: busy))
            await asyncio.sleep(0)
            busy = True
        with self.assertRaises(admission.ResourceBusyError):
            await pending
        self.assertFalse(admission.native_work_inflight())

    async def test_cancellation_does_not_abandon_context_reservation(self) -> None:
        started = asyncio.Event()
        async def operation() -> None:
            async with admission.native_admission(lambda: False):
                started.set()
                await asyncio.Event().wait()
        task = asyncio.create_task(operation())
        await started.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(admission.native_work_inflight())
