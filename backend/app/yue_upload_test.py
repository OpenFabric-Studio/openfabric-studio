"""Upload conversion owns subprocess descendants through cancellation."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import io
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
import wave
from unittest.mock import AsyncMock, patch

import httpx

from app import yue_upload as upload
from app import work_busy
from app.video_process import spawn_owned
from fastapi import HTTPException


async def convert_bytes(data: bytes) -> bytes:
    """Small test fixtures can be collected; production always streams files."""
    async def chunks() -> AsyncIterator[bytes]:
        yield data

    async def receive(path: Path) -> httpx.Response:
        return httpx.Response(200, content=path.read_bytes())

    return (await upload.forward_upload(chunks(), convert=True, send=receive)).content


class YueUploadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        upload.start()
        self.addAsyncCleanup(upload.shutdown)

    async def test_shutdown_drains_conversion_and_blocks_reentry(self) -> None:
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def convert(source: Path, output: Path, scratch: tempfile.TemporaryDirectory[str]) -> None:
            try:
                entered.set()
                await asyncio.Future()
            finally:
                cleaned.set()

        with patch.object(upload, "_convert", side_effect=convert):
            task = asyncio.create_task(convert_bytes(b"input"))
            await entered.wait()
            self.assertTrue(upload.work_busy())
            self.assertTrue(work_busy.local_work_busy())
            await upload.shutdown()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(cleaned.is_set())
        self.assertFalse(upload.work_busy())
        with self.assertRaisesRegex(upload.UploadConversionError, "audio_converter_stopping"):
            await convert_bytes(b"input")

    async def test_setup_blocks_conversion_before_process_creation(self) -> None:
        with patch("app.module_jobs.work_busy", return_value=True), patch.object(upload, "spawn_owned", new=AsyncMock()) as spawn:
            with self.assertRaises(HTTPException) as error:
                await convert_bytes(b"input")
        self.assertEqual(error.exception.detail, "module_setup_busy")
        spawn.assert_not_awaited()

    async def test_timeout_is_stable_and_drains_owned_process(self) -> None:
        workers: list[asyncio.subprocess.Process] = []

        async def spawn(argv: list[str], *, receipt_path: Path, stdout: int) -> asyncio.subprocess.Process:
            proc = await spawn_owned([sys.executable, "-c", "import time; time.sleep(60)"], receipt_path=receipt_path, stdout=stdout)
            workers.append(proc)
            return proc

        with patch.object(upload, "get_ffmpeg_bin", return_value="fixture"), \
             patch.object(upload, "spawn_owned", side_effect=spawn), \
             patch.object(upload, "read_owned_output", new=AsyncMock(side_effect=TimeoutError("private path"))), \
             patch.object(upload, "kill_process_tree", wraps=upload.kill_process_tree) as drain:
            with self.assertRaises(upload.UploadConversionError) as error:
                await convert_bytes(b"input")
        self.assertEqual(error.exception.code, "audio_conversion_timed_out")
        self.assertEqual(error.exception.status_code, 504)
        self.assertNotIn("private", str(error.exception))
        drain.assert_awaited_once()
        self.assertIsNotNone(workers[0].returncode)

    async def test_failed_drain_retains_admission_and_scratch_until_shutdown_retry(self) -> None:
        workers: list[asyncio.subprocess.Process] = []
        receipts: list[Path] = []

        async def spawn(argv: list[str], *, receipt_path: Path, stdout: int) -> asyncio.subprocess.Process:
            proc = await spawn_owned([sys.executable, "-c", "import time; time.sleep(60)"], receipt_path=receipt_path, stdout=stdout)
            workers.append(proc)
            receipts.append(receipt_path)
            return proc

        with patch.object(upload, "get_ffmpeg_bin", return_value="fixture"), \
             patch.object(upload, "spawn_owned", side_effect=spawn), \
             patch.object(upload, "read_owned_output", new=AsyncMock(side_effect=TimeoutError())), \
             patch.object(upload, "kill_process_tree", new=AsyncMock(side_effect=RuntimeError("drain unavailable"))):
            with self.assertRaisesRegex(upload.UploadConversionError, "audio_converter_cleanup_failed"):
                await convert_bytes(b"input")
        self.assertTrue(upload.work_busy())
        self.assertTrue(work_busy.local_work_busy())
        self.assertIsNone(workers[0].returncode)
        self.assertTrue(receipts[0].parent.exists())
        with self.assertRaisesRegex(upload.UploadConversionError, "audio_converter_cleanup_failed"):
            await convert_bytes(b"input")
        await upload.shutdown()
        self.assertIsNotNone(workers[0].returncode)
        self.assertFalse(upload.work_busy())
        self.assertFalse(receipts[0].parent.exists())

    async def test_successful_drain_cancel_does_not_leave_false_quarantine(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        proc = await spawn_owned([sys.executable, "-c", "import time; time.sleep(60)"],
                                 receipt_path=Path(scratch.name) / "worker.json", stdout=asyncio.subprocess.PIPE)
        entered, release = asyncio.Event(), asyncio.Event()
        original_drain = upload.kill_process_tree

        async def drain(worker: asyncio.subprocess.Process) -> None:
            await original_drain(worker)
            entered.set()
            await release.wait()

        with patch.object(upload, "kill_process_tree", side_effect=drain):
            task = asyncio.create_task(upload._drain(proc, scratch))
            try:
                async with asyncio.timeout(5):
                    await entered.wait()
                self.assertIsNotNone(proc.returncode)
                self.assertTrue(upload.work_busy())
                task.cancel()
                await asyncio.sleep(0.01)
                self.assertFalse(task.done(), "cancellation must wait for the owned drain")
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertFalse(upload.work_busy())

    @unittest.skipUnless(upload.get_ffmpeg_bin(), "FFmpeg is unavailable")
    async def test_real_conversion_retains_complete_mono_wav(self) -> None:
        source = io.BytesIO()
        with wave.open(source, "wb") as audio:
            audio.setnchannels(2)
            audio.setsampwidth(2)
            audio.setframerate(22050)
            audio.writeframes(b"\0\0" * 2 * 2205)
        result = await convert_bytes(source.getvalue())
        with wave.open(io.BytesIO(result), "rb") as converted:
            self.assertEqual(converted.getnchannels(), 1)
            self.assertEqual(converted.getframerate(), 44100)
            self.assertEqual(converted.getnframes(), 4410)
            self.assertEqual(len(converted.readframes(4410)), 8820)

    @unittest.skipUnless(upload.get_ffmpeg_bin(), "FFmpeg is unavailable")
    async def test_validates_quiescent_output_after_descendant_drain(self) -> None:
        source = io.BytesIO()
        with wave.open(source, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(44100)
            audio.writeframes(b"\0\0" * 4410)
        original_drain = upload.kill_process_tree

        async def drain(proc: asyncio.subprocess.Process) -> None:
            # Simulate a last descendant write before the owned drain can
            # establish that the output is immutable for validation/forwarding.
            output = Path(upload._PENDING[proc].name) / "output.wav"
            await original_drain(proc)
            with output.open("r+b") as recording:
                recording.truncate(output.stat().st_size - 3)

        with patch.object(upload, "kill_process_tree", side_effect=drain):
            with self.assertRaisesRegex(upload.UploadConversionError, "audio_conversion_failed"):
                await convert_bytes(source.getvalue())
        self.assertFalse(upload.work_busy())


@unittest.skipIf(sys.platform == "win32", "POSIX executable fixture")
class YueUploadOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        upload.start()
        self.addAsyncCleanup(upload.shutdown)

    async def test_cancellation_reaps_converter_and_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            converter = root / "ffmpeg"
            parent_file, child_file = root / "parent.pid", root / "child.pid"
            converter.write_text(
                f"#!{sys.executable}\n"
                "import os, subprocess, sys, time\n"
                f"open({str(parent_file)!r}, 'w').write(str(os.getpid()))\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                f"open({str(child_file)!r}, 'w').write(str(child.pid))\n"
                "time.sleep(60)\n"
            )
            converter.chmod(0o700)
            task: asyncio.Task[bytes] | None = None
            pids: list[int] = []
            try:
                with patch.object(upload, "get_ffmpeg_bin", return_value=str(converter)):
                    task = asyncio.create_task(convert_bytes(b"input"))
                    async with asyncio.timeout(5):
                        while not child_file.exists():
                            await asyncio.sleep(0.01)
                    pids = [int(parent_file.read_text()), int(child_file.read_text())]
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                await asyncio.sleep(0.05)
                for pid in pids:
                    with self.assertRaises(ProcessLookupError, msg=f"converter process {pid} leaked"):
                        os.kill(pid, 0)
            finally:
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                for pid in pids:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
