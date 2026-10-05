"""Local video/native submissions cannot pass each other's admission window."""
from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

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

    async def test_native_lease_blocks_optional_and_character_submission(self) -> None:
        from app import optional_engines, video_character_training
        from app.video_projects import VideoProjectError
        with tempfile.TemporaryDirectory() as directory, \
             patch('app.config.DATA_DIR', Path(directory)), \
             patch.object(optional_engines, '_STOPPING', False), \
             patch.object(video_character_training, '_STOPPING', False), \
             patch.object(optional_engines, 'work_busy', return_value=False), \
             patch.object(video_character_training, 'work_busy', return_value=False):
            lease = await admission.reserve_native(lambda: False, model_id='ace_step')
            try:
                output = optional_engines._output('kokoro', '.wav')
                with patch.object(optional_engines, '_execute', new=AsyncMock()) as execute:
                    with self.assertRaises(optional_engines.OptionalEngineError) as error:
                        await optional_engines._run('kokoro', [], cwd=Path(directory), output=output, timeout=1, runtime='fixture')
                    self.assertEqual(error.exception.code, 'engine_busy')
                    execute.assert_not_awaited()
                with patch.object(video_character_training, '_create_job', new=AsyncMock()) as create:
                    with self.assertRaises(VideoProjectError) as error:
                        await video_character_training.create_job(name='Fixture', consent_confirmed=True, uploads=[])
                    self.assertEqual(error.exception.code, 'busy')
                    create.assert_not_awaited()
            finally:
                await lease.release()

    async def test_setup_rejects_native_after_waiting_for_admission(self) -> None:
        with patch('app.module_jobs.work_busy', return_value=False) as setup:
            async with admission.admission_lock:
                pending = asyncio.create_task(admission.reserve_native(lambda: False))
                await asyncio.sleep(0)
                setup.return_value = True
            with self.assertRaises(admission.ResourceBusyError) as caught:
                await pending
        self.assertEqual(caught.exception.code, 'module_setup_busy')
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
