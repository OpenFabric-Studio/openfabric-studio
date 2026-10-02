"""The video supervisor ties worker lifetime to the owning backend pipe."""

from __future__ import annotations
import asyncio, json, os, signal, subprocess, sys, tempfile, time, unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch


class VideoProcessTests(unittest.TestCase):
    def test_missing_receipt_for_dead_worker_does_not_block_recovery(self) -> None:
        from app.video_process import WorkerIdentity, terminate_verified

        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.Popen([sys.executable, "-c", "pass"])
            proc.wait(5)
            self.assertTrue(
                asyncio.run(
                    terminate_verified(
                        WorkerIdentity(
                            pid=proc.pid,
                            token="a" * 32,
                            receipt=str(Path(tmp) / "missing"),
                        )
                    )
                )
            )

    def test_worker_stops_after_hard_parent_crash(self) -> None:
        from app import video_process

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pidfile = root / "child.pid"
            receipt = root / "receipt.json"
            child = (
                "import os,time;from pathlib import Path;Path("
                + repr(str(pidfile))
                + ").write_text(str(os.getpid()));time.sleep(60)"
            )
            code = (
                "import asyncio;from pathlib import Path;from app.video_process import spawn_owned\nasync def main():\n proc=await spawn_owned(["
                + repr(sys.executable)
                + ',"-c",'
                + repr(child)
                + "],receipt_path=Path("
                + repr(str(receipt))
                + "))\n await proc.wait()\nasyncio.run(main())"
            )
            parent = subprocess.Popen([sys.executable, "-c", code])
            child_pid = None
            try:
                until = time.monotonic() + 5
                while not pidfile.exists() and time.monotonic() < until:
                    time.sleep(0.02)
                self.assertTrue(pidfile.exists())
                child_pid = int(pidfile.read_text())
                parent.kill()
                parent.wait(5)
                until = time.monotonic() + 5
                alive = True
                while alive and time.monotonic() < until:
                    result = subprocess.run(
                        ["ps", "-p", str(child_pid), "-o", "stat="],
                        capture_output=True,
                        text=True,
                    )
                    alive = bool(
                        result.stdout.strip()
                    ) and not result.stdout.strip().startswith("Z")
                    time.sleep(0.02)
                self.assertFalse(alive, "Child survived backend pipe EOF")
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.wait(5)
                if child_pid:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_identity_mismatch_never_kills_unrelated_process(self) -> None:
        from app.video_process import WorkerIdentity, terminate_verified

        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.Popen(
                [sys.executable, "-c", "import time;time.sleep(60)"]
            )
            try:
                receipt = Path(tmp) / "receipt.json"
                receipt.write_text(
                    json.dumps({"pid": proc.pid, "token": "a" * 32, "returncode": None})
                )
                self.assertFalse(
                    asyncio.run(
                        terminate_verified(
                            WorkerIdentity(
                                pid=proc.pid, token="a" * 32, receipt=str(receipt)
                            )
                        )
                    )
                )
                self.assertIsNone(proc.poll())
            finally:
                proc.kill()
                proc.wait(5)

    def test_successful_worker_exits_cleanly_while_parent_pipe_is_open(self) -> None:
        from app.video_process import spawn_owned

        with tempfile.TemporaryDirectory() as tmp:

            async def run() -> int:
                proc = await spawn_owned(
                    [sys.executable, "-c", "pass"],
                    receipt_path=Path(tmp) / "receipt.json",
                )
                code = await proc.wait()
                if proc.stdin is not None:
                    proc.stdin.close()
                return code

            self.assertEqual(asyncio.run(run()), 0)


class VideoCapturedOutputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app import video_jobs

        self.video = video_jobs
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(video_jobs, "DATA_DIR", self.root))
        self.enterContext(patch.object(video_jobs, "LOG_DIR", self.root / "logs"))
        self.enterContext(
            patch.object(video_jobs, "_tool", return_value=sys.executable)
        )
        self.job = video_jobs.VideoJob(
            id="a" * 32,
            track_id=1,
            title="CPU fixture",
            prompt="test",
            seconds=2,
            start_sec=0,
            frames=49,
            seed=42,
            created_at="2026-10-01T00:00:00Z",
        )

    async def test_failed_mux_keeps_error_after_separate_output_chunks(self) -> None:
        script = (
            "import sys,time;print('first output',flush=True);time.sleep(.05);"
            "print('specific final failure',flush=True);sys.exit(1)"
        )
        with self.assertRaises(self.video.VideoJobError) as error:
            await self.video._ffmpeg(["-c", script], self.job.slot, self.job, 5)
        self.assertEqual(error.exception.code, "mux_failed")
        self.assertIn("specific final failure", error.exception.detail)
        self.assertIsNone(self.job.slot.proc)
        self.assertIsNone(self.job.worker)

    async def test_oversized_mux_output_fails_promptly_without_pipe_deadlock(
        self,
    ) -> None:
        from app.video_process import WorkerIdentity

        script = "import sys;sys.stdout.buffer.write(b'x' * (5 * 1024 * 1024))"
        original_spawn = self.video.spawn_owned
        processes: list[asyncio.subprocess.Process] = []

        async def capture(
            argv: list[str],
            *,
            receipt_path: Path,
            stdout: int | None = None,
            on_identity: Callable[[WorkerIdentity], None] | None = None,
        ) -> asyncio.subprocess.Process:
            proc = await original_spawn(
                argv,
                receipt_path=receipt_path,
                stdout=stdout,
                on_identity=on_identity,
            )
            processes.append(proc)
            return proc

        failure: BaseException | None = None
        with patch.object(self.video, "spawn_owned", side_effect=capture):
            try:
                await asyncio.wait_for(
                    self.video._ffmpeg(["-c", script], self.job.slot, self.job, 10),
                    timeout=3,
                )
            except Exception as exc:
                failure = exc
        self.assertIsInstance(failure, self.video.VideoJobError)
        if isinstance(failure, self.video.VideoJobError):
            self.assertEqual(failure.code, "mux_failed")
        self.assertIsNone(self.job.slot.proc)
        self.assertIsNone(self.job.worker)
        self.assertIsNotNone(processes[0].returncode)
