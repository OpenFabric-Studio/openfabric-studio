"""Stem subprocess delegation regressions without Demucs or model downloads."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import stems
from app.video_process import spawn_owned


class SeparateFileSpawnTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.logs = self.root / "logs"
        self.engine = self.root / "engine"
        self.engine.mkdir()
        self.audio = self.root / "input.wav"
        self.audio.write_bytes(b"original input")
        self.output = self.root / "output"
        self.enterContext(patch.object(stems, "LOG_DIR", self.logs))
        self.enterContext(patch.object(stems, "DEMUCS_DIR", self.engine))
        self.enterContext(patch.object(stems, "gpu_lock", asyncio.Lock()))

    async def test_separation_delegates_command_environment_and_log_to_owner(
        self,
    ) -> None:
        command = [
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; "
            "Path(sys.argv[1]).write_bytes(b'isolated stem'); print('separated')",
            str(self.output / "vocals.wav"),
        ]
        captured: list[tuple[list[str], Path, dict[str, str]]] = []
        processes: list[asyncio.subprocess.Process | None] = []

        async def owned(
            argv: list[str], cwd: Path, env: Mapping[str, str], stdout: int
        ) -> asyncio.subprocess.Process:
            captured.append((argv, cwd, dict(env)))
            return await spawn_owned(
                argv,
                cwd=cwd,
                env=env,
                stdout=stdout,
                receipt_path=self.root / "worker.json",
            )

        with patch.object(stems, "_demucs_cmd", return_value=command) as build:
            result = await stems.separate_file(
                self.audio,
                self.output,
                log_name="owned",
                quality="high",
                on_proc=processes.append,
                spawn=owned,
            )
        build.assert_called_once_with(self.audio, self.output, quality="high")
        self.assertEqual(result, {"vocals": self.output / "vocals.wav"})
        self.assertEqual(captured[0][0:2], (command, self.engine))
        self.assertEqual(captured[0][2]["PATH"], stems._demucs_env()["PATH"])
        self.assertEqual(
            captured[0][2]["DO_NOT_TRACK"], os.environ.get("DO_NOT_TRACK", "1")
        )
        self.assertIn("separated", (self.logs / "owned.log").read_text())
        self.assertEqual(len(processes), 2)
        self.assertIsNotNone(processes[0])
        self.assertIsNone(processes[1])
        self.assertFalse(stems.gpu_lock.locked())
        self.assertEqual(self.audio.read_bytes(), b"original input")

    async def test_cancellation_drains_owned_worker_and_removes_partial_stems(
        self,
    ) -> None:
        running = asyncio.Event()
        processes: list[asyncio.subprocess.Process | None] = []

        async def owned(
            argv: list[str], cwd: Path, env: Mapping[str, str], stdout: int
        ) -> asyncio.subprocess.Process:
            proc = await spawn_owned(
                argv,
                cwd=cwd,
                env=env,
                stdout=stdout,
                receipt_path=self.root / "worker.json",
            )
            running.set()
            return proc

        command = [sys.executable, "-c", "import time; time.sleep(60)"]
        with patch.object(stems, "_demucs_cmd", return_value=command):
            task = asyncio.create_task(
                stems.separate_file(
                    self.audio,
                    self.output,
                    log_name="cancelled",
                    on_proc=processes.append,
                    spawn=owned,
                )
            )
            try:
                await asyncio.wait_for(running.wait(), timeout=5)
                (self.output / "vocals.wav").write_bytes(b"partial stem")
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=5)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        proc = processes[0]
        self.assertIsNotNone(proc)
        if proc is not None:
            self.assertIsNotNone(proc.returncode)
        self.assertIsNone(processes[-1])
        self.assertFalse(self.output.exists())
        self.assertFalse(stems.gpu_lock.locked())

    async def test_default_spawn_preserves_existing_separation_behavior(self) -> None:
        command = [
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; "
            "Path(sys.argv[1]).write_bytes(b'isolated stem')",
            str(self.output / "vocals.wav"),
        ]
        with patch.object(stems, "_demucs_cmd", return_value=command):
            result = await stems.separate_file(
                self.audio, self.output, log_name="default"
            )
        self.assertEqual(result, {"vocals": self.output / "vocals.wav"})

    async def test_failed_termination_preserves_live_process_callback_ownership(
        self,
    ) -> None:
        running = asyncio.Event()
        processes: list[asyncio.subprocess.Process | None] = []
        worker: asyncio.subprocess.Process | None = None
        terminate = stems._kill_tree

        async def owned(
            argv: list[str], cwd: Path, env: Mapping[str, str], stdout: int
        ) -> asyncio.subprocess.Process:
            nonlocal worker
            worker = await spawn_owned(
                argv,
                cwd=cwd,
                env=env,
                stdout=stdout,
                receipt_path=self.root / "worker.json",
            )
            running.set()
            return worker

        command = [sys.executable, "-c", "import time; time.sleep(60)"]
        with patch.object(stems, "_demucs_cmd", return_value=command):
            task = asyncio.create_task(
                stems.separate_file(
                    self.audio,
                    self.output,
                    log_name="failed-cleanup",
                    on_proc=processes.append,
                    spawn=owned,
                )
            )
            try:
                await asyncio.wait_for(running.wait(), timeout=5)
                with patch.object(
                    stems,
                    "_kill_tree",
                    new=AsyncMock(side_effect=OSError("kill failed")),
                ):
                    task.cancel()
                    with self.assertRaisesRegex(OSError, "kill failed"):
                        await task
                self.assertIsNotNone(worker)
                self.assertIs(processes[-1], worker)
                if worker is not None:
                    self.assertIsNone(worker.returncode)
                self.assertTrue(self.output.exists())
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await terminate(worker)


if __name__ == "__main__":
    unittest.main()
