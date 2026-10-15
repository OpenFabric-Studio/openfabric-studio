"""Stem subprocess delegation regressions without Demucs or model downloads."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pydantic import TypeAdapter

from app import db, stems
from app.video_process import spawn_owned


def assert_worker_stops_after_parent_crash(test: unittest.TestCase, root: Path, operation: str) -> None:
    marker = root / "worker.pids"
    child = ("import os,subprocess,sys,time;from pathlib import Path; "
             "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); "
             f"marker=Path({str(marker)!r}); temporary=marker.with_suffix('.pending'); "
             "temporary.write_text(f'{os.getpid()} {child.pid} {os.getpgrp()}'); temporary.replace(marker); time.sleep(60)")
    code = ("import asyncio,os,sys;from pathlib import Path\n"
            f"root=Path({str(root)!r}); worker_script={child!r}; worker=[sys.executable,'-c',worker_script]\n"
            "async def main():\n" + "\n".join(" " + line for line in operation.splitlines()) + "\nasyncio.run(main())")
    env = os.environ.copy()
    env.update(OPENFABRIC_CONFIG=str(root / "config.json"), OPENFABRIC_DATA_DIR=str(root / "library"),
               SEED_VC_DIR=str(root / "seed-vc"), PYTHONPATH=str(Path(__file__).resolve().parents[1]),
               PYTHONDONTWRITEBYTECODE="1")
    pids: list[int] = []
    with (root / "backend.log").open("w") as log:
        parent = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=log, stderr=log)
        try:
            until = time.monotonic() + 5
            while not marker.exists() and parent.poll() is None and time.monotonic() < until:
                time.sleep(0.02)
            test.assertTrue(marker.is_file(), (root / "backend.log").read_text())
            pids = [int(value) for value in marker.read_text().split()]
            parent.kill()
            parent.wait(5)
            for pid in pids[:2]:
                until = time.monotonic() + 5
                while time.monotonic() < until:
                    state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
                    if not state or state.startswith("Z"):
                        break
                    time.sleep(0.02)
                test.assertTrue(not state or state.startswith("Z"), f"Fixture worker {pid} survived backend death: {state}")
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(5)
            if pids:
                try:
                    os.killpg(pids[2], signal.SIGKILL)
                except ProcessLookupError:
                    pass


@unittest.skipIf(sys.platform == "win32", "POSIX hard-parent-death fixture")
class StemParentCrashTests(unittest.TestCase):
    def test_default_separation_stops_with_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            assert_worker_stops_after_parent_crash(self, Path(temporary).resolve(),
                "from app import stems\nstems.LOG_DIR=root/'logs'; stems.DEMUCS_DIR=root\n"
                "stems._demucs_cmd=lambda *args,**kwargs:worker\n"
                "await stems.separate_file(root/'source.wav',root/'output',log_name='crash')")

    def test_saved_track_separation_stops_with_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            assert_worker_stops_after_parent_crash(self, Path(temporary).resolve(),
                "from app import db,stems\nstems.LOG_DIR=root/'logs'; stems.DEMUCS_DIR=root\n"
                "stems._demucs_cmd=lambda *args,**kwargs:worker\n"
                "audio=db.model_dir('upload')/'source.wav'; audio.write_bytes(b'fixture')\n"
                "identifier=db.insert_track(model='upload',title='Fixture',lyrics='',seed=None,duration_ms=1000,wall_ms=None,params={},audio_path=audio,abc_path=None)\n"
                "job=await stems.start(identifier)\nawait job.task")


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


class SavedStemReplacementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        for name, value in [("_db", None), ("DATA_DIR", self.root / "library"),
                            ("DB_PATH", self.root / "library/catalog.db"),
                            ("FILES_DIR", self.root / "library/files")]:
            self.enterContext(patch.object(db, name, value))
        self.connection = db.get_db()
        self.addCleanup(self.connection.close)
        self.enterContext(patch.object(stems, "LOG_DIR", self.root / "logs"))
        self.enterContext(patch.object(stems, "DEMUCS_DIR", self.root))
        self.enterContext(patch.object(stems, "_gpu_lock", asyncio.Lock()))
        self.enterContext(patch.dict(stems._jobs, {}, clear=True))
        self.addAsyncCleanup(stems.shutdown)
        self.audio = db.model_dir("upload") / "source.wav"
        self.audio.write_bytes(b"source fixture")
        self.track_id = self.track(self.audio)
        self.output = db.stems_dir("upload", self.track_id)
        self.output.mkdir(parents=True)
        self.previous = {name: str(self.output / f"{name}.wav") for name in stems.STEM_NAMES}
        for name, path in self.previous.items():
            Path(path).write_bytes(f"previous {name}".encode())
        db.update_track_stems(self.track_id, self.previous)

    def track(self, audio: Path) -> int:
        return db.insert_track(model="upload", title="Fixture", lyrics="", seed=None,
                               duration_ms=1000, wall_ms=None, params={}, audio_path=audio, abc_path=None)

    def catalog(self) -> dict[str, str]:
        row = db.get_track(self.track_id)
        if row is None:
            raise AssertionError("Fixture track disappeared")
        return TypeAdapter(dict[str, str]).validate_json(row["stems_json"]) if row["stems_json"] else {}

    def assert_previous_retained(self) -> None:
        self.assertEqual(self.catalog(), self.previous)
        for name, path in self.previous.items():
            self.assertEqual(Path(path).read_bytes(), f"previous {name}".encode())

    async def test_failed_rerun_keeps_previous_stems_and_catalog(self) -> None:
        def command(audio: Path, out_dir: Path) -> list[str]:
            script = f"from pathlib import Path; Path({str(out_dir / 'vocals.wav')!r}).write_bytes(b'partial'); raise SystemExit(1)"
            return [sys.executable, "-c", script]
        with patch.object(stems, "_demucs_cmd", side_effect=command), self.assertLogs("app.stems", level="ERROR"):
            job = await stems.start(self.track_id, force=True)
            if job.task is not None:
                await job.task
        self.assertEqual(job.status, "failed")
        self.assert_previous_retained()
        self.assertEqual(set(self.output.rglob("*.wav")), {Path(path) for path in self.previous.values()})

    async def test_canceled_rerun_keeps_previous_stems_and_catalog(self) -> None:
        ready = self.root / "ready"
        def command(audio: Path, out_dir: Path) -> list[str]:
            script = (f"from pathlib import Path; import time; Path({str(out_dir / 'vocals.wav')!r}).write_bytes(b'partial'); "
                      f"Path({str(ready)!r}).write_text('ready'); time.sleep(60)")
            return [sys.executable, "-c", script]
        with patch.object(stems, "_demucs_cmd", side_effect=command):
            job = await stems.start(self.track_id, force=True)
            async with asyncio.timeout(5):
                while not ready.exists():
                    await asyncio.sleep(0.01)
            await stems.cancel(self.track_id)
        self.assertEqual(job.status, "cancelled")
        self.assert_previous_retained()
        self.assertEqual(set(self.output.rglob("*.wav")), {Path(path) for path in self.previous.values()})

    async def test_successful_rerun_publishes_replacement_and_preserves_shared_old_stem(self) -> None:
        shared = Path(self.previous["vocals"])
        remaining_track = self.track(shared)
        def command(audio: Path, out_dir: Path) -> list[str]:
            script = ("from pathlib import Path; "
                      f"root=Path({str(out_dir)!r}); "
                      f"[(root / (name + '.wav')).write_bytes(b'replacement') for name in {stems.STEM_NAMES!r}]")
            return [sys.executable, "-c", script]
        with patch.object(stems, "_demucs_cmd", side_effect=command):
            job = await stems.start(self.track_id, force=True)
            if job.task is not None:
                await job.task
        self.assertEqual(job.status, "done")
        self.assertEqual(set(self.catalog()), set(stems.STEM_NAMES))
        for name, path in self.catalog().items():
            self.assertNotEqual(path, self.previous[name])
            self.assertEqual(Path(path).read_bytes(), b"replacement")
        self.assertEqual(shared.read_bytes(), b"previous vocals")
        self.assertIsNotNone(db.get_track(remaining_track))
        self.assertFalse(Path(self.previous["drums"]).exists())


if __name__ == "__main__":
    unittest.main()
