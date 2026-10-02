"""A backend pipe owns native sessions through crash and launcher exit."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import ProcessSpec
from app.orchestrator.process import ManagedProcess


class NativeReceiptTests(unittest.TestCase):
    def test_receipt_requires_current_pid_token_and_bounded_typed_exit_code(self) -> None:
        from app.orchestrator.posix_supervisor import read_receipt
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipt.json'
            identity = {'pid': 42, 'token': 'a' * 32, 'returncode': 3}
            path.write_text(json.dumps(identity))
            receipt = read_receipt(path, 'a' * 32, 42)
            self.assertIsNotNone(receipt)
            for field, replacement in (('pid', True), ('pid', 43), ('token', 'b' * 32), ('returncode', True), ('returncode', 100000)):
                path.write_text(json.dumps({**identity, field: replacement}))
                with self.subTest(field=field, replacement=replacement):
                    self.assertIsNone(read_receipt(path, 'a' * 32, 42))


@unittest.skipIf(sys.platform == 'win32', 'POSIX session supervision')
class NativeParentSupervisionTests(unittest.IsolatedAsyncioTestCase):
    async def _wait_file(self, marker: Path) -> None:
        for _ in range(100):
            if marker.is_file():
                return
            await asyncio.sleep(.025)
        self.fail('Owned child did not start')

    async def _assert_gone(self, pid: int) -> None:
        for _ in range(200):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            await asyncio.sleep(.025)
        self.fail('Native descendant survived supervisor completion')

    async def test_backend_pipe_eof_kills_the_complete_native_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'child-pid'
            script = root / 'engine.py'
            script.write_text("import subprocess,sys,time\nfrom pathlib import Path\n"
                              "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])\n"
                              "Path(sys.argv[1]).write_text(str(child.pid))\ntime.sleep(120)\n")
            process = ManagedProcess(ProcessSpec(name='fixture', cwd=root, cmd=[sys.executable, str(script), str(marker)], shutdown_timeout=1))
            with patch('app.orchestrator.process.LOG_DIR', root):
                process.start()
            try:
                await self._wait_file(marker)
                descendant = int(marker.read_text())
                self.assertIsNotNone(process._proc)
                if process._proc is None:
                    self.fail('Native supervisor missing')
                self.assertIsNotNone(process._proc.stdin, 'Native supervisor must own a parent-liveness pipe')
                if process._proc.stdin is not None:
                    process._proc.stdin.close()
                self.assertTrue(await process.wait_stopped(5))
                await self._assert_gone(descendant)
            finally:
                await process.stop()

    async def test_launcher_exit_kills_descendants_and_preserves_original_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'child-pid'
            script = root / 'engine.py'
            script.write_text("import subprocess,sys\nfrom pathlib import Path\n"
                              "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])\n"
                              "Path(sys.argv[1]).write_text(str(child.pid))\n")
            process = ManagedProcess(ProcessSpec(name='fixture', cwd=root, cmd=[sys.executable, str(script), str(marker)], shutdown_timeout=1))
            with patch('app.orchestrator.process.LOG_DIR', root):
                process.start()
            try:
                await self._wait_file(marker)
                descendant = int(marker.read_text())
                self.assertTrue(await process.wait_stopped(5))
                await self._assert_gone(descendant)
                self.assertIn('(code 0)', process.exit_summary())
            finally:
                await process.stop()
