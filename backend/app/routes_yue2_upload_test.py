"""Legacy raw uploads use seekable files and bounded forwarding chunks."""
from __future__ import annotations

from collections.abc import AsyncIterator
import asyncio
from functools import partial
import hashlib
import io
from pathlib import Path
import unittest
import tempfile
import threading
import wave
from unittest.mock import AsyncMock, patch

import httpx
from starlette.requests import Request
from starlette.requests import ClientDisconnect
from starlette.types import Message
from fastapi import HTTPException

from app import module_jobs, work_busy, yue_upload
from app.api import routes_yue2_upload as route
from app.orchestrator.state import ModelStatus


def make_request(data: bytes, filename: str) -> Request:
    chunks = iter(data[index:index + 17003] for index in range(0, len(data), 17003))

    async def receive() -> Message:
        chunk = next(chunks, b"")
        return {"type": "http.request", "body": chunk, "more_body": bool(chunk)}

    return Request({"type": "http", "method": "POST", "path": "/api/yue2/v1/ui/upload",
                    "headers": [(b"x-audiocpp-filename", filename.encode()),
                                (b"content-length", str(len(data)).encode()),
                                (b"content-type", b"application/octet-stream") ]}, receive=receive)


class InspectUploadTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.chunks: list[int] = []
        self.digest = hashlib.sha256()
        self.total = 0
        self.headers = httpx.Headers()
        self.busy: list[bool] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.headers = request.headers
        if not isinstance(request.stream, httpx.AsyncByteStream):
            raise AssertionError("upload must have an async file stream")
        async for chunk in request.stream:
            self.chunks.append(len(chunk))
            self.digest.update(chunk)
            self.total += len(chunk)
            self.busy.append(yue_upload.work_busy())
        return httpx.Response(201, content=b'{"path":"native/upload.wav"}',
                              headers={"content-type": "application/json", "x-native-result": "kept"})


class YueUploadRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        yue_upload.start()
        self.addAsyncCleanup(yue_upload.shutdown)
        self.status = patch.object(route.manager.state.models["yue2"], "status", ModelStatus.RUNNING)
        self.status.start()
        self.addCleanup(self.status.stop)
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.enterContext(patch.object(yue_upload.tempfile, "TemporaryDirectory",
                                      new=partial(tempfile.TemporaryDirectory, dir=self.scratch.name)))

    def assert_scratch_clean(self) -> None:
        self.assertEqual(list(Path(self.scratch.name).iterdir()), [])

    async def test_wav_is_forwarded_without_body_buffering_and_holds_admission(self) -> None:
        data = b"arbitrary native WAV passthrough" * 40000
        transport = InspectUploadTransport()
        async with httpx.AsyncClient(transport=transport) as client:
            with patch.object(route, "_client", client), \
                 patch.object(Request, "body", new=AsyncMock(side_effect=AssertionError("whole request buffered"))), \
                 patch.object(Path, "read_bytes", side_effect=AssertionError("whole file buffered")):
                result = await route.upload_audio(make_request(data, "original.WAV"))
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.body, b'{"path":"native/upload.wav"}')
        self.assertEqual(result.headers["x-native-result"], "kept")
        self.assertEqual(transport.digest.digest(), hashlib.sha256(data).digest())
        self.assertEqual(transport.total, len(data))
        self.assertLessEqual(max(transport.chunks), 65536)
        self.assertTrue(all(transport.busy))
        self.assertEqual(transport.headers["content-length"], str(len(data)))
        self.assertEqual(transport.headers["content-type"], "audio/wav")
        self.assertEqual(transport.headers["x-audiocpp-filename"], "original.WAV")
        self.assertFalse(yue_upload.work_busy())
        self.assert_scratch_clean()

    @unittest.skipUnless(yue_upload.get_ffmpeg_bin(), "FFmpeg is unavailable")
    async def test_conversion_validates_and_forwards_wav_without_whole_file_reads(self) -> None:
        source = io.BytesIO()
        with wave.open(source, "wb") as audio:
            audio.setnchannels(2)
            audio.setsampwidth(2)
            audio.setframerate(22050)
            audio.writeframes(b"\0\0" * 2 * 220500)
        transport = InspectUploadTransport()
        original_read = wave.Wave_read.readframes

        def bounded_read(audio: wave.Wave_read, frames: int) -> bytes:
            if frames > 32768:
                raise AssertionError("whole PCM buffered")
            return original_read(audio, frames)

        async with httpx.AsyncClient(transport=transport) as client:
            with patch.object(route, "_client", client), \
                 patch.object(Request, "body", new=AsyncMock(side_effect=AssertionError("whole request buffered"))), \
                 patch.object(Path, "read_bytes", side_effect=AssertionError("whole file buffered")), \
                 patch.object(wave.Wave_read, "readframes", bounded_read):
                result = await route.upload_audio(make_request(source.getvalue(), "input.bin"))
        self.assertEqual(result.status_code, 201)
        self.assertGreater(transport.total, 882000)
        self.assertLessEqual(max(transport.chunks), 65536)
        self.assertTrue(all(transport.busy))
        self.assertEqual(transport.headers["content-length"], str(transport.total))
        self.assertEqual(transport.headers["x-audiocpp-filename"], "upload.wav")
        self.assertFalse(yue_upload.work_busy())
        self.assert_scratch_clean()

    async def test_setup_refuses_wav_before_consuming_the_request(self) -> None:
        request = make_request(b"native WAV", "original.wav")
        receive = AsyncMock(side_effect=AssertionError("request consumed during setup"))
        request = Request(request.scope, receive=receive)
        with patch("app.module_jobs.work_busy", return_value=True), \
             patch.object(route._client, "post", new=AsyncMock()) as send:
            with self.assertRaises(HTTPException) as failure:
                await route.upload_audio(request)
        self.assertEqual(failure.exception.status_code, 409)
        self.assertEqual(failure.exception.detail, "module_setup_busy")
        receive.assert_not_awaited()
        send.assert_not_awaited()
        self.assert_scratch_clean()

    async def test_disconnect_releases_receive_ownership_and_removes_staged_file(self) -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0

        async def receive() -> Message:
            nonlocal count
            count += 1
            if count == 1:
                return {"type": "http.request", "body": b"partial recording", "more_body": True}
            entered.set()
            await release.wait()
            return {"type": "http.disconnect"}

        request = make_request(b"", "original.wav")
        request = Request(request.scope, receive=receive)
        with patch.object(route._client, "post", new=AsyncMock()) as send:
            task = asyncio.create_task(route.upload_audio(request))
            await entered.wait()
            try:
                self.assertTrue(yue_upload.work_busy())
                self.assertTrue(work_busy.local_work_busy())
                # Even if the music models stop, the receiving upload itself
                # must continue to exclude a setup mutation.
                with patch.object(route.manager.state.models["yue2"], "status", ModelStatus.STOPPED):
                    with self.assertRaisesRegex(module_jobs.ModuleSetupError, "setup_busy"):
                        module_jobs._reserve_setup()
            finally:
                release.set()
                with self.assertRaises(ClientDisconnect):
                    await task
        send.assert_not_awaited()
        self.assertFalse(yue_upload.work_busy())
        self.assert_scratch_clean()

    async def test_shutdown_cancels_receiving_upload_and_cleans_partial_file(self) -> None:
        entered = asyncio.Event()

        async def receive() -> Message:
            entered.set()
            await asyncio.Future()
            return {"type": "http.disconnect"}

        request = make_request(b"", "original.wav")
        task = asyncio.create_task(route.upload_audio(Request(request.scope, receive=receive)))
        await entered.wait()
        self.assertTrue(yue_upload.work_busy())
        await yue_upload.shutdown()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(yue_upload.work_busy())
        self.assert_scratch_clean()

    async def test_cancellation_during_forwarding_closes_stream_before_scratch_cleanup(self) -> None:
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def send(url: str, *, headers: dict[str, str], content: AsyncIterator[bytes]) -> httpx.Response:
            try:
                self.assertTrue(await anext(content))
                self.assertTrue(yue_upload.work_busy())
                entered.set()
                await asyncio.Future()
                return httpx.Response(201)
            finally:
                cleaned.set()

        with patch.object(route._client, "post", side_effect=send):
            task = asyncio.create_task(route.upload_audio(make_request(b"native WAV" * 100000, "original.wav")))
            await entered.wait()
            self.assertTrue(yue_upload.work_busy())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(cleaned.is_set())
        self.assertFalse(yue_upload.work_busy())
        self.assert_scratch_clean()

    async def test_upstream_transport_error_is_safe_and_cleans_staged_upload(self) -> None:
        async def send(url: str, *, headers: dict[str, str], content: AsyncIterator[bytes]) -> httpx.Response:
            self.assertTrue(await anext(content))
            raise httpx.ConnectError("private host or internal path")

        with patch.object(route._client, "post", side_effect=send):
            result = await route.upload_audio(make_request(b"native WAV", "original.wav"))
        self.assertEqual(result.status_code, 502)
        self.assertEqual(result.body, b'{"error":"upstream_request_failed"}')
        self.assertFalse(yue_upload.work_busy())
        self.assert_scratch_clean()

    async def test_native_rejection_preserves_opaque_status_and_body(self) -> None:
        async def send(url: str, *, headers: dict[str, str], content: AsyncIterator[bytes]) -> httpx.Response:
            async for _ in content:
                pass
            return httpx.Response(422, content=b"native format rejection", headers={"x-native-result": "kept"})

        with patch.object(route._client, "post", side_effect=send):
            result = await route.upload_audio(make_request(b"native WAV", "original.wav"))
        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.body, b"native format rejection")
        self.assertEqual(result.headers["x-native-result"], "kept")
        self.assert_scratch_clean()

    @unittest.skipUnless(yue_upload.get_ffmpeg_bin(), "FFmpeg is unavailable")
    async def test_invalid_conversion_is_safe_and_does_not_forward_partial_output(self) -> None:
        with patch.object(route._client, "post", new=AsyncMock()) as send:
            with self.assertRaises(HTTPException) as failure:
                await route.upload_audio(make_request(b"not audio", "input.bin"))
        self.assertEqual(failure.exception.status_code, 400)
        self.assertEqual(failure.exception.detail, "audio_conversion_failed")
        send.assert_not_awaited()
        self.assertFalse(yue_upload.work_busy())
        self.assert_scratch_clean()

    async def test_upload_storage_failure_is_safe_and_removes_scratch(self) -> None:
        with patch.object(Path, "open", side_effect=OSError("private disk path")), \
             patch.object(route._client, "post", new=AsyncMock()) as send:
            with self.assertRaises(HTTPException) as failure:
                await route.upload_audio(make_request(b"native WAV", "original.wav"))
        self.assertEqual(failure.exception.status_code, 503)
        self.assertEqual(failure.exception.detail, "audio_upload_storage_failed")
        send.assert_not_awaited()
        self.assertFalse(yue_upload.work_busy())
        self.assert_scratch_clean()

    async def test_scratch_creation_failure_returns_stable_error(self) -> None:
        with patch.object(yue_upload.tempfile, "TemporaryDirectory", side_effect=OSError("private disk path")):
            with self.assertRaises(HTTPException) as failure:
                await route.upload_audio(make_request(b"native WAV", "original.wav"))
        self.assertEqual(failure.exception.status_code, 503)
        self.assertEqual(failure.exception.detail, "audio_upload_storage_failed")
        self.assertFalse(yue_upload.work_busy())
        self.assert_scratch_clean()

    async def test_file_read_failure_returns_stable_error_and_cleans_scratch(self) -> None:
        async def chunks(path: Path) -> AsyncIterator[bytes]:
            raise OSError("private disk path")
            yield b"unreachable"

        transport = InspectUploadTransport()
        async with httpx.AsyncClient(transport=transport) as client:
            with patch.object(route, "_client", client), patch.object(route, "file_chunks", new=chunks):
                with self.assertRaises(HTTPException) as failure:
                    await route.upload_audio(make_request(b"native WAV", "original.wav"))
        self.assertEqual(failure.exception.status_code, 503)
        self.assertEqual(failure.exception.detail, "audio_upload_storage_failed")
        self.assertFalse(yue_upload.work_busy())
        self.assert_scratch_clean()

    async def test_failed_scratch_cleanup_retains_admission_until_shutdown_retries(self) -> None:
        native_body = b'{"path":"native/upload.wav"}'
        with patch.object(type(self.scratch), "cleanup", side_effect=OSError("private disk path")), \
             patch.object(route._client, "post", new=AsyncMock(return_value=httpx.Response(201, content=native_body,
                                         headers={"x-native-result": "kept"}))):
            try:
                result = await route.upload_audio(make_request(b"native WAV", "original.wav"))
            except (HTTPException, OSError):
                self.fail("scratch cleanup masked the completed native response")
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.body, native_body)
        self.assertEqual(result.headers["x-native-result"], "kept")
        self.assertTrue(yue_upload.work_busy())
        self.assertTrue(work_busy.local_work_busy())
        self.assertTrue(list(Path(self.scratch.name).iterdir()))
        receive = AsyncMock(side_effect=AssertionError("request consumed during cleanup quarantine"))
        request = make_request(b"native WAV", "original.wav")
        with self.assertRaises(HTTPException) as failure:
            await route.upload_audio(Request(request.scope, receive=receive))
        self.assertEqual(failure.exception.detail, "audio_upload_cleanup_failed")
        receive.assert_not_awaited()
        await yue_upload.shutdown()
        self.assertFalse(yue_upload.work_busy())
        self.assert_scratch_clean()

    async def test_cleanup_failure_preserves_upstream_transport_failure(self) -> None:
        with patch.object(type(self.scratch), "cleanup", side_effect=OSError("private disk path")), \
             patch.object(route._client, "post", new=AsyncMock(side_effect=httpx.ConnectError("private host"))):
            result = await route.upload_audio(make_request(b"native WAV", "original.wav"))
        self.assertEqual(result.status_code, 502)
        self.assertEqual(result.body, b'{"error":"upstream_request_failed"}')
        self.assertTrue(yue_upload.work_busy())
        await yue_upload.shutdown()
        self.assertFalse(yue_upload.work_busy())
        self.assert_scratch_clean()

    @unittest.skipUnless(yue_upload.get_ffmpeg_bin(), "FFmpeg is unavailable")
    async def test_cleanup_failure_preserves_conversion_failure(self) -> None:
        with patch.object(type(self.scratch), "cleanup", side_effect=OSError("private disk path")), \
             patch.object(route._client, "post", new=AsyncMock()) as send:
            with self.assertRaises(HTTPException) as failure:
                await route.upload_audio(make_request(b"invalid recording", "input.bin"))
        self.assertEqual(failure.exception.status_code, 400)
        self.assertEqual(failure.exception.detail, "audio_conversion_failed")
        send.assert_not_awaited()
        self.assertTrue(yue_upload.work_busy())
        await yue_upload.shutdown()
        self.assertFalse(yue_upload.work_busy())
        self.assert_scratch_clean()

    async def test_cleanup_failure_preserves_forwarding_cancellation(self) -> None:
        entered = asyncio.Event()

        async def send(url: str, *, headers: dict[str, str], content: AsyncIterator[bytes]) -> httpx.Response:
            entered.set()
            await asyncio.Future[None]()
            return httpx.Response(201)

        with patch.object(type(self.scratch), "cleanup", side_effect=OSError("private disk path")), \
             patch.object(route._client, "post", side_effect=send):
            task = asyncio.create_task(route.upload_audio(make_request(b"native WAV", "original.wav")))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(yue_upload.work_busy())
        await yue_upload.shutdown()
        self.assertFalse(yue_upload.work_busy())
        self.assert_scratch_clean()

    async def test_cancellation_during_successful_cleanup_does_not_leave_false_quarantine(self) -> None:
        entered, release = threading.Event(), threading.Event()
        original_cleanup = type(self.scratch).cleanup

        def cleanup(scratch: tempfile.TemporaryDirectory[str]) -> None:
            entered.set()
            if not release.wait(5):
                raise RuntimeError("cleanup test timed out")
            original_cleanup(scratch)

        with patch.object(type(self.scratch), "cleanup", new=cleanup), \
             patch.object(route._client, "post", new=AsyncMock(return_value=httpx.Response(201, content=b"accepted"))):
            task = asyncio.create_task(route.upload_audio(make_request(b"native WAV", "original.wav")))
            try:
                async with asyncio.timeout(5):
                    while not entered.is_set():
                        await asyncio.sleep(0.01)
                task.cancel()
                await asyncio.sleep(0.01)
                self.assertFalse(task.done(), "cancellation must wait for the mutating cleanup thread")
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertFalse(yue_upload.work_busy())
        self.assert_scratch_clean()
