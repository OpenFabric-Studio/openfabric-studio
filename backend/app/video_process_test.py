"""The video supervisor ties worker lifetime to the owning backend pipe."""

from __future__ import annotations
import asyncio, json, os, signal, subprocess, sys, tempfile, time, unittest
from collections.abc import Callable, Mapping
from pathlib import Path
from unittest.mock import patch


class VideoProcessTests(unittest.TestCase):
    @unittest.skipIf(sys.platform == 'win32', 'POSIX process group recovery')
    def test_dead_supervisor_does_not_prove_its_descendants_exited(self) -> None:
        from app.video_process import WorkerIdentity, WorkerReceipt, spawn_owned, terminate_verified
        from app.job_lifecycle import kill_process_tree

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = root / 'receipt.json'
            child_pid = root / 'child.pid'
            command = ('import subprocess,sys;from pathlib import Path;'
                'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);'
                f'Path({str(child_pid)!r}).write_text(str(child.pid))')

            async def run() -> None:
                proc = await spawn_owned([sys.executable, '-c', command], receipt_path=receipt)
                try:
                    self.assertEqual(await asyncio.wait_for(proc.wait(), 5), 0)
                    stored = WorkerReceipt.model_validate_json(receipt.read_bytes())
                    identity = WorkerIdentity(pid=proc.pid, token=stored.token, receipt=str(receipt))
                    self.assertFalse(await terminate_verified(identity), 'A dead supervisor still has a live process group')
                    receipt.unlink()
                    self.assertFalse(await terminate_verified(identity), 'A missing receipt cannot prove that group exited')
                finally:
                    await kill_process_tree(proc)
                    if proc.stdin is not None:
                        proc.stdin.close()

            asyncio.run(run())

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


class BoundedCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app import video_process

        self.worker = video_process
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(tempfile, "tempdir", str(self.root)))
        self.processes: list[asyncio.subprocess.Process] = []
        self.receipts: list[Path] = []
        original = video_process.spawn_owned

        async def capture(
            argv: list[str], *, receipt_path: Path,
            stdout: int | None = None, stderr: int | None = asyncio.subprocess.STDOUT,
            env: Mapping[str, str] | None = None,
        ) -> asyncio.subprocess.Process:
            proc = await original(argv, receipt_path=receipt_path, stdout=stdout, stderr=stderr, env=env)
            self.processes.append(proc)
            self.receipts.append(receipt_path)
            return proc

        self.enterContext(patch.object(video_process, "spawn_owned", side_effect=capture))

    async def asyncTearDown(self) -> None:
        from app.job_lifecycle import kill_process_tree

        for proc in self.processes:
            await kill_process_tree(proc)
            if proc.stdin is not None:
                proc.stdin.close()

    async def test_stdout_stays_separate_and_receipt_is_removed_after_success(self) -> None:
        script = "import sys,time;print('3',flush=True);time.sleep(.05);print('4',flush=True);print('diagnostic',file=sys.stderr)"
        result = await self.worker.run_owned_command([sys.executable, "-c", script], timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"3\n4\n")
        self.assertFalse(self.receipts[0].parent.exists())

    async def test_nonzero_returncode_is_preserved(self) -> None:
        result = await self.worker.run_owned_command([sys.executable, "-c", "print('10');raise SystemExit(7)"], timeout=5)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, b"10\n")

    async def test_binary_stdout_is_preserved(self) -> None:
        result = await self.worker.run_owned_command(
            [sys.executable, "-c", "import sys;sys.stdout.buffer.write(bytes([0,255,0,128]))"], timeout=5,
        )
        self.assertEqual(result.stdout, bytes([0, 255, 0, 128]))

    async def test_configured_environment_and_merged_diagnostics_are_preserved(self) -> None:
        environment = dict(os.environ, OPENFABRIC_COMMAND_FIXTURE="configured")
        result = await self.worker.run_owned_command(
            [sys.executable, "-c", "import os,sys;print(os.environ['OPENFABRIC_COMMAND_FIXTURE'],file=sys.stderr)"],
            timeout=5, env=environment, stderr=asyncio.subprocess.STDOUT,
        )
        self.assertEqual(result.stdout, b"configured\n")

    async def test_output_overflow_drains_before_propagating(self) -> None:
        with self.assertRaises(self.worker.WorkerOutputError):
            await asyncio.wait_for(self.worker.run_owned_command(
                [sys.executable, "-c", "import sys,time;sys.stdout.buffer.write(b'x'*1048576);sys.stdout.flush();time.sleep(60)"],
                timeout=5, max_bytes=128,
            ), 3)
        self.assertIsNotNone(self.processes[0].returncode)
        self.assertFalse(self.receipts[0].parent.exists())

    async def test_timeout_reaps_the_supervisor(self) -> None:
        with self.assertRaises(TimeoutError):
            await self.worker.run_owned_command([sys.executable, "-c", "import time;time.sleep(60)"], timeout=0.05)
        self.assertIsNotNone(self.processes[0].returncode)
        self.assertFalse(self.receipts[0].parent.exists())

    async def test_cancellation_reaps_the_supervisor(self) -> None:
        task = asyncio.create_task(self.worker.run_owned_command(
            [sys.executable, "-c", "import time;time.sleep(60)"], timeout=60,
        ))
        async with asyncio.timeout(5):
            while not self.processes:
                await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNotNone(self.processes[0].returncode)
        self.assertFalse(self.receipts[0].parent.exists())

    async def test_failed_drain_preserves_the_ownership_receipt(self) -> None:
        with patch("app.job_lifecycle.kill_process_tree", side_effect=OSError("fixture drain failure")):
            with self.assertRaises(OSError):
                await self.worker.run_owned_command([sys.executable, "-c", "pass"], timeout=5)
        self.assertTrue(self.receipts[0].is_file())


class IncompleteSpawnCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_ambiguous_spawn_preserves_inputs_and_receipts(self) -> None:
        import io
        from fastapi import UploadFile
        from app import video_characters, video_projects
        for kind in ('character','reference'):
            for receipt_present in (False,True):
                with self.subTest(kind=kind,receipt_present=receipt_present), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)/'owned'
                    root.mkdir()
                    source = root/'.fixture.upload'
                    source.write_bytes(b'owned input')
                    receipt = source.with_suffix('.worker.json')
                    if receipt_present:
                        receipt.write_text('invalid proof')
                    upload = UploadFile(io.BytesIO(b'fixture'),filename='still.png')
                    with self.assertRaises(video_projects.VideoProjectError) as error:
                        if kind == 'character':
                            await video_characters._cleanup(None,source,root,False,upload,spawn_attempted=True)
                        else:
                            await video_projects._cleanup_reference(None,source,root/'output.png',False,upload,spawn_attempted=True)
                    self.assertEqual(error.exception.code,'cleanup_failed')
                    self.assertTrue(root.exists())
                    self.assertTrue(source.exists())
                    self.assertEqual(receipt.exists(),receipt_present)
                    self.assertTrue(upload.file.closed)

    async def test_valid_receipt_is_drained_before_unpublished_inputs_are_removed(self) -> None:
        import io
        from fastapi import UploadFile
        from app import video_characters, video_projects
        from app.job_lifecycle import kill_process_tree
        from app.video_process import spawn_owned
        for kind in ('character','reference'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)/'owned'
                root.mkdir()
                source = root/'.fixture.upload'
                source.write_bytes(b'owned input')
                receipt = source.with_suffix('.worker.json')
                process = await spawn_owned([sys.executable,'-c','import time;time.sleep(60)'],receipt_path=receipt)
                upload = UploadFile(io.BytesIO(b'fixture'),filename='still.png')
                try:
                    async with asyncio.timeout(5):
                        while not receipt.is_file():
                            await asyncio.sleep(.01)
                    if kind == 'character':
                        await video_characters._cleanup(None,source,root,False,upload,spawn_attempted=True)
                    else:
                        await video_projects._cleanup_reference(None,source,root/'output.png',False,upload,spawn_attempted=True)
                    self.assertFalse(source.exists())
                    self.assertFalse(receipt.exists())
                    self.assertTrue(upload.file.closed)
                finally:
                    await kill_process_tree(process)
