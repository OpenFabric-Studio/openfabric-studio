"""Transcription owns the same YuE server through stop and reset."""
from __future__ import annotations
import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from app import native_yue, resource_admission


class OwnedNativeTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_mutation_drains_restart_before_releasing_admission(self) -> None:
        entered, resetting, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def send(endpoint, payload):
            entered.set()
            await asyncio.Future()
        async def reset(model):
            resetting.set()
            await release.wait()
        async def work():
            async with native_yue.owned_session() as session:
                await session.post('/v1/tasks/run', {})
        with patch.object(native_yue, 'send', side_effect=send), patch.object(native_yue, 'engine_running', return_value=True), patch.object(native_yue.manager, 'restart_model', side_effect=reset):
            task = asyncio.create_task(work())
            await entered.wait()
            task.cancel()
            await resetting.wait()
            self.assertTrue(resource_admission.native_work_inflight())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_failed_get_never_resets_native_engine(self) -> None:
        with patch.object(native_yue, 'send', new=AsyncMock(side_effect=ValueError())), patch.object(native_yue, 'engine_running', return_value=True), patch.object(native_yue.manager, 'restart_model', new=AsyncMock()) as reset:
            with self.assertRaises(ValueError):
                async with native_yue.owned_session() as session:
                    await session.get('/v1/models')
            reset.assert_not_awaited()
        self.assertFalse(resource_admission.native_work_inflight())
