"""A native stop must be verified before a replacement can start."""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from app.config import MODELS, ProcessSpec
from app.orchestrator.manager import OrchestratorManager
from app.orchestrator.process import ManagedProcess
from app.orchestrator.state import ModelStatus


class NativeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_start_is_registered_before_health_finishes(self) -> None:
        manager = OrchestratorManager()
        process = Mock(spec=ManagedProcess)
        entered, release = asyncio.Event(), asyncio.Event()
        async def healthy(cancel: asyncio.Event | None = None) -> None:
            entered.set()
            await release.wait()
        process.wait_healthy = healthy
        process.stop = AsyncMock()
        with patch('app.orchestrator.manager.ManagedProcess', return_value=process):
            task = asyncio.create_task(manager.switch_to('ace_step'))
            try:
                await entered.wait()
                self.assertEqual(manager._processes.get('ace_step'), [process])
            finally:
                release.set()
                await task
                await manager.stop_all()

    @unittest.skipIf(sys.platform == 'win32', 'Real POSIX native supervisor')
    async def test_cancelled_start_drains_real_supervisor_and_clears_starting_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec = ProcessSpec(name='startup-fixture', cwd=root,
                cmd=[sys.executable, '-c', 'import time; time.sleep(120)'], shutdown_timeout=.2)
            process = ManagedProcess(spec)
            manager = OrchestratorManager()
            entered = asyncio.Event()
            async def healthy(cancel: asyncio.Event | None = None) -> None:
                entered.set()
                await asyncio.Future[None]()
            definition = replace(MODELS['ace_step'], processes=[spec])
            with patch.dict(MODELS, {'ace_step': definition}), patch('app.orchestrator.process.LOG_DIR', root), \
                 patch('app.orchestrator.manager.ManagedProcess', return_value=process), \
                 patch.object(process, 'wait_healthy', new=healthy):
                task = asyncio.create_task(manager.switch_to('ace_step'))
                try:
                    await entered.wait()
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertFalse(process.is_running)
                    self.assertEqual(manager.state.models['ace_step'].status, ModelStatus.STOPPED)
                    self.assertNotIn('ace_step', manager._processes)
                finally:
                    await process.stop()

    async def test_failed_startup_drain_remains_owned_until_shutdown_retries(self) -> None:
        manager = OrchestratorManager()
        process = Mock(spec=ManagedProcess)
        process.wait_healthy = AsyncMock(side_effect=RuntimeError('Health failed'))
        process.stop = AsyncMock(side_effect=[RuntimeError('Drain failed'), None])
        with patch('app.orchestrator.manager.ManagedProcess', return_value=process), \
             self.assertLogs('orchestrator', level='ERROR'):
            with self.assertRaisesRegex(RuntimeError, 'Drain failed'):
                await manager.switch_to('ace_step')
        self.assertEqual(manager._processes.get('ace_step'), [process])
        await manager.stop_all()
        self.assertNotIn('ace_step', manager._processes)
        self.assertEqual(manager.state.models['ace_step'].status, ModelStatus.STOPPED)

    async def test_repeated_cancellation_waits_for_startup_cleanup(self) -> None:
        manager = OrchestratorManager()
        process = Mock(spec=ManagedProcess)
        healthy_entered, cleanup_entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def healthy(cancel: asyncio.Event | None = None) -> None:
            healthy_entered.set()
            await asyncio.Future[None]()
        async def stop() -> None:
            cleanup_entered.set()
            await release.wait()
        process.wait_healthy = healthy
        process.stop = stop
        with patch('app.orchestrator.manager.ManagedProcess', return_value=process):
            task = asyncio.create_task(manager.switch_to('ace_step'))
            try:
                await healthy_entered.wait()
                task.cancel()
                try:
                    await asyncio.wait_for(cleanup_entered.wait(), .1)
                except TimeoutError:
                    pass
                self.assertTrue(cleanup_entered.is_set(), 'Cancellation skipped owned startup cleanup')
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done(), 'Repeated cancellation interrupted cleanup')
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertNotIn('ace_step', manager._processes)
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_failed_startup_drain_blocks_a_different_model_replacement(self) -> None:
        manager = OrchestratorManager()
        process = Mock(spec=ManagedProcess)
        process.wait_healthy = AsyncMock(side_effect=RuntimeError('Health failed'))
        process.stop = AsyncMock(side_effect=RuntimeError('Drain failed'))
        with patch('app.orchestrator.manager.ManagedProcess', return_value=process) as create, \
             self.assertLogs('orchestrator', level='ERROR'):
            with self.assertRaisesRegex(RuntimeError, 'Drain failed'):
                await manager.switch_to('ace_step')
            with self.assertRaisesRegex(RuntimeError, 'Drain failed'):
                await manager.switch_to('yue2')
        self.assertEqual(create.call_count, 1, 'A failed drain allowed another engine to start')

    async def test_failed_force_kill_does_not_claim_process_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            process = ManagedProcess(ProcessSpec(name='fixture', cwd=Path(directory), cmd=['fixture']))
            process._proc = Mock()
            process._proc.poll.return_value = None
            process._proc.pid = 42
            with patch.object(process, 'wait_stopped', new=AsyncMock(return_value=False)), \
                 patch.object(process, '_force_kill', new=AsyncMock()):
                with self.assertRaises(RuntimeError):
                    await process.stop()

    async def test_restart_never_starts_replacement_after_failed_stop(self) -> None:
        manager = OrchestratorManager()
        manager.state.models['yue2'].status = ModelStatus.RUNNING
        with patch.object(manager, '_stop_model', new=AsyncMock(side_effect=RuntimeError('stop failed'))), \
             patch.object(manager, '_start_model', new=AsyncMock()) as start:
            with self.assertRaises(RuntimeError):
                await manager.restart_model('yue2')
            start.assert_not_awaited()

    async def test_cancel_does_not_restart_engine_user_already_stopped(self) -> None:
        manager = OrchestratorManager()
        with patch.object(manager, '_start_model', new=AsyncMock()) as start:
            await manager.restart_model('yue2')
            start.assert_not_awaited()
