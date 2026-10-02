"""A native stop must be verified before a replacement can start."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from app.config import ProcessSpec
from app.orchestrator.manager import OrchestratorManager
from app.orchestrator.process import ManagedProcess
from app.orchestrator.state import ModelStatus


class NativeLifecycleTests(unittest.IsolatedAsyncioTestCase):
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
