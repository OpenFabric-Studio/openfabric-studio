"""Settings stops only the idle, exact native run owned by this backend."""
from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app import resource_admission
from app import support_controls as controls
from app.config import MODELS
from app.orchestrator.manager import OrchestratorManager
from app.orchestrator.process import ManagedProcess
from app.orchestrator.state import ModelStatus


class SupportControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.manager = OrchestratorManager()
        self.enterContext(patch.object(controls, 'manager', self.manager))
        self.enterContext(patch.object(resource_admission, 'admission_lock', asyncio.Lock()))
        self.enterContext(patch.object(resource_admission, '_native_inflight', 0))
        self.enterContext(patch.object(controls, '_idle_state', new=AsyncMock(return_value='idle')))
        self.process = ManagedProcess(MODELS['ace_step'].processes[0])
        self.process._proc = Mock()
        self.process._proc.poll.return_value = None
        self.process._proc.pid = 42
        self.enterContext(patch.object(self.process, 'require_owned'))
        self.stop = self.enterContext(patch.object(self.process, 'stop', new=AsyncMock()))
        self.manager._processes['ace_step'] = [self.process]
        self.manager._instances['ace_step'] = 'a' * 32
        self.manager.state.models['ace_step'].status = ModelStatus.RUNNING
        self.manager.state.active_model = 'ace_step'

    async def test_idle_stop_drains_exact_owned_engine(self) -> None:
        await controls.stop_engine('ace_step', 'a' * 32)
        self.stop.assert_awaited_once()
        self.assertEqual(self.manager.state.models['ace_step'].status, ModelStatus.STOPPED)
        self.assertIsNone(self.manager.state.active_model)
        self.assertNotIn('ace_step', self.manager._instances)

    async def test_replacement_run_is_not_stopped_by_a_stale_click(self) -> None:
        with self.assertRaises(controls.EngineControlError) as caught:
            await controls.stop_engine('ace_step', 'b' * 32)
        self.assertEqual(caught.exception.code, 'engine_changed')
        self.stop.assert_not_awaited()

    async def test_active_native_lease_is_rejected_before_stop(self) -> None:
        with patch.object(resource_admission, '_native_inflight', 1):
            with self.assertRaises(controls.EngineControlError) as caught:
                await controls.stop_engine('ace_step', 'a' * 32)
        self.assertEqual(caught.exception.code, 'engine_busy')
        self.stop.assert_not_awaited()

    async def test_busy_and_unverified_checks_fail_closed(self) -> None:
        for state, code in (('busy', 'engine_busy'), ('unknown', 'engine_state_unverified')):
            with self.subTest(state=state), patch.object(controls, '_idle_state', new=AsyncMock(return_value=state)):
                with self.assertRaises(controls.EngineControlError) as caught:
                    await controls.stop_engine('ace_step', 'a' * 32)
                self.assertEqual(caught.exception.code, code)
        self.stop.assert_not_awaited()

    async def test_unverified_process_identity_never_signals_process(self) -> None:
        with patch.object(self.process, 'require_owned', side_effect=RuntimeError('/private/receipt')):
            with self.assertRaises(controls.EngineControlError) as caught:
                await controls.stop_engine('ace_step', 'a' * 32)
        self.assertEqual(caught.exception.code, 'engine_not_owned')
        self.assertNotIn('/private', str(caught.exception))
        self.stop.assert_not_awaited()

    async def test_admission_waits_until_owned_stop_has_drained(self) -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        async def stop() -> None:
            entered.set()
            await release.wait()
        self.stop.side_effect = stop
        stopping = asyncio.create_task(controls.stop_engine('ace_step', 'a' * 32))
        await entered.wait()
        async def admission() -> None:
            async with resource_admission.admission_lock:
                self.assertEqual(self.manager.state.models['ace_step'].status, ModelStatus.STOPPED)
        pending = asyncio.create_task(admission())
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        release.set()
        await asyncio.gather(stopping, pending)

    async def test_request_cancellation_still_drains_and_releases_admission(self) -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        async def stop() -> None:
            entered.set()
            await release.wait()
        self.stop.side_effect = stop
        task = asyncio.create_task(controls.stop_engine('ace_step', 'a' * 32))
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(resource_admission.admission_lock.locked())
        self.assertEqual(self.manager.state.models['ace_step'].status, ModelStatus.STOPPED)

    async def test_failed_drain_keeps_process_owned_and_does_not_claim_success(self) -> None:
        self.stop.side_effect = RuntimeError('/private/log with credentials')
        with self.assertLogs('app.support_controls', level='ERROR'), self.assertRaises(controls.EngineControlError) as caught:
            await controls.stop_engine('ace_step', 'a' * 32)
        self.assertEqual(caught.exception.code, 'engine_stop_failed')
        self.assertIn('ace_step', self.manager._processes)
        self.assertEqual(self.manager.state.models['ace_step'].status, ModelStatus.STOPPING)

    async def test_same_engine_restart_cannot_replace_a_failed_stop_tree(self) -> None:
        self.stop.side_effect = RuntimeError('Drain pending')
        with self.assertLogs('app.support_controls', level='ERROR'), self.assertRaises(controls.EngineControlError):
            await controls.stop_engine('ace_step', 'a' * 32)
        retained = self.manager._processes['ace_step']
        with patch.object(self.manager, '_start_model', new=AsyncMock()) as start:
            with self.assertRaises(RuntimeError):
                await self.manager.switch_to('ace_step')
            start.assert_not_awaited()
            self.assertIs(self.manager._processes['ace_step'], retained)
            self.assertEqual(self.manager._instances['ace_step'], 'a' * 32)
            self.stop.side_effect = None
            await self.manager.switch_to('ace_step')
            start.assert_awaited_once_with('ace_step')
            self.assertNotIn('ace_step', self.manager._processes)

    def test_upstream_accounting_requires_typed_queue_counts_and_training_state(self) -> None:
        self.assertEqual(controls._accounting_state({'jobs': {'queued': 0, 'running': 0}, 'queue_size': 0}, {'is_training': False}), 'idle')
        self.assertEqual(controls._accounting_state({'data': {'jobs': {'queued': 0, 'running': 1}}, 'code': 200}, {'data': {'is_training': False}, 'code': 200}), 'busy')
        for stats, training in (({}, {}), ({'jobs': {'queued': '0', 'running': 0}}, {'is_training': False}),
                                ({'jobs': {'queued': 0, 'running': 0}}, {}),
                                ({'data': {'jobs': {'queued': 0, 'running': 0}}, 'code': 500}, {'is_training': False}),
                                ({'jobs': {'queued': False, 'running': 0}}, {'is_training': False})):
            self.assertEqual(controls._accounting_state(stats, training), 'unknown')


class NativeIdentityTests(unittest.TestCase):
    def test_wrong_receipt_or_foreign_process_cannot_be_claimed(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch('app.orchestrator.process.IS_WINDOWS', False):
            proc = ManagedProcess(MODELS['ace_step'].processes[0])
            proc._proc = Mock()
            proc._proc.poll.return_value = None
            proc._proc.pid = 42
            proc._owns_group = True
            proc._supervisor_token = 'a' * 32
            proc._supervisor_receipt = Path(directory) / 'receipt.json'
            proc._supervisor_receipt.write_text('{"pid":42,"token":"wrong","returncode":null}')
            with self.assertRaises(RuntimeError):
                proc.require_owned()
            proc._supervisor_receipt.write_text('{"pid":42,"token":"' + 'a' * 32 + '","returncode":null}')
            proc.require_owned()

    def test_windows_requires_an_owned_job_handle_and_live_process(self) -> None:
        proc = ManagedProcess(MODELS['ace_step'].processes[0])
        proc._proc = Mock()
        proc._proc.poll.return_value = None
        with patch('app.orchestrator.process.IS_WINDOWS', True):
            with self.assertRaises(RuntimeError):
                proc.require_owned()
            proc._windows_job = Mock()
            proc.require_owned()
            proc._proc.poll.return_value = 0
            with self.assertRaises(RuntimeError):
                proc.require_owned()
