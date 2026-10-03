"""Shared speech boundary validation without an engine or model downloads."""
from __future__ import annotations

import tempfile
import asyncio
import threading
import time
import unittest
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import patch

import httpx
from app import speech_clone, voice_profiles, module_jobs
from app.module_jobs import ModuleSetupError


class SpeechBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        speech_clone.start()

    def test_health_probe_has_an_absolute_header_deadline(self) -> None:
        def slow(request: httpx.Request) -> httpx.Response:
            time.sleep(0.03)
            return httpx.Response(400, content=b"{}", request=request)

        async def async_slow(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.03)
            return httpx.Response(400, content=b"{}", request=request)

        client = httpx.Client(transport=httpx.MockTransport(slow))
        async_client = httpx.AsyncClient(transport=httpx.MockTransport(async_slow))
        with patch.object(speech_clone.httpx, "Client", return_value=client), patch.object(speech_clone.httpx, "AsyncClient", return_value=async_client):
            self.assertFalse(speech_clone.api_reachable(timeout_s=0.01))

    def test_trickling_audio_cannot_extend_the_absolute_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out.wav"
            speech_clone._write_silent_wav(output)
            audio = output.read_bytes()
            output.write_bytes(b"existing")

            class Trickle(httpx.SyncByteStream, httpx.AsyncByteStream):
                closed = False

                def __iter__(self) -> Iterator[bytes]:
                    for index in range(0, len(audio), 512):
                        time.sleep(0.005)
                        yield audio[index:index + 512]

                async def __aiter__(self) -> AsyncIterator[bytes]:
                    for index in range(0, len(audio), 512):
                        await asyncio.sleep(0.005)
                        yield audio[index:index + 512]

                def close(self) -> None:
                    self.closed = True

                async def aclose(self) -> None:
                    self.closed = True

            stream = Trickle()
            transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=stream, request=request))
            client = httpx.Client(transport=transport)
            async_client = httpx.AsyncClient(transport=transport)
            with patch.object(speech_clone, "API_TIMEOUT_S", 0.02), patch.object(speech_clone.httpx, "Client", return_value=client), patch.object(speech_clone.httpx, "AsyncClient", return_value=async_client):
                with self.assertRaises(TimeoutError):
                    speech_clone._synthesize_via_api(refer_wav_path="fixture.wav", prompt_text="Reference", prompt_language="en", text="Hello", text_language="en", output_path=output)
            self.assertTrue(stream.closed)
            self.assertEqual(output.read_bytes(), b"existing")

    def test_setup_admission_blocks_synthesis_with_stable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch("app.module_jobs.speech_admission", side_effect=ModuleSetupError("setup_busy")), patch.object(speech_clone, "_synthesize_unlocked") as synthesis:
                outcome = speech_clone.synthesize_to_path(profile_id="a" * 32, text="Hello", output_path=Path(temporary) / "out.wav")
            self.assertEqual(outcome.status, "failed")
            self.assertEqual(outcome.detail, "setup_busy")
            synthesis.assert_not_called()

    def test_valid_audio_is_published_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "fixture.wav"
            speech_clone._write_silent_wav(fixture)
            audio = fixture.read_bytes()
            output = Path(temporary) / "out.wav"
            client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=audio, request=request)))
            with patch.object(speech_clone.httpx, "AsyncClient", return_value=client):
                self.assertEqual(speech_clone._synthesize_via_api(refer_wav_path="fixture.wav", prompt_text="Reference", prompt_language="en", text="Hello", text_language="en", output_path=output), output)
            self.assertEqual(output.read_bytes(), audio)
            self.assertEqual(list(Path(temporary).glob("*.tmp.wav")), [])
    def test_non_audio_reply_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out.wav"
            output.write_bytes(b"existing")
            client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"not a WAV", request=request)))
            with patch.object(speech_clone.httpx, "AsyncClient", return_value=client):
                with self.assertRaises(RuntimeError):
                    speech_clone._synthesize_via_api(refer_wav_path="fixture.wav", prompt_text="Reference", prompt_language="en", text="Hello", text_language="en", output_path=output)
            self.assertEqual(output.read_bytes(), b"existing")

    def test_wav_header_without_complete_pcm_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out.wav"
            speech_clone._write_silent_wav(output)
            audio = output.read_bytes()[:-2]
            output.write_bytes(b"existing")
            client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=audio, request=request)))
            with patch.object(speech_clone.httpx, "AsyncClient", return_value=client):
                with self.assertRaises(RuntimeError):
                    speech_clone._synthesize_via_api(refer_wav_path="fixture.wav", prompt_text="Reference", prompt_language="en", text="Hello", text_language="en", output_path=output)
            self.assertEqual(output.read_bytes(), b"existing")

    def test_audio_reply_limit_is_enforced_before_publication(self) -> None:
        self.assertTrue(hasattr(speech_clone, "MAX_SPEECH_AUDIO_BYTES"), "audio response bound is missing")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out.wav"
            speech_clone._write_silent_wav(output)
            audio = output.read_bytes()
            output.unlink()
            client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=audio, request=request)))
            with patch.object(speech_clone, "MAX_SPEECH_AUDIO_BYTES", 64), patch.object(speech_clone.httpx, "AsyncClient", return_value=client):
                with self.assertRaises(RuntimeError):
                    speech_clone._synthesize_via_api(refer_wav_path="fixture.wav", prompt_text="Reference", prompt_language="en", text="Hello", text_language="en", output_path=output)
            self.assertFalse(output.exists())


class SpeechCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        speech_clone.start()

    def test_only_successful_real_synthesis_records_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(voice_profiles, "PROFILES_ROOT", root / "profiles"):
                profile = voice_profiles.create_profile(name="Reader", consent_confirmed=True, audio_bytes=b"RIFF....WAVE", filename="ref.wav", notes="Reference.")
                output = root / "out.wav"
                speech_clone._write_silent_wav(output)
                with patch.object(speech_clone, "mock_enabled", return_value=False), patch.object(speech_clone, "resolve_engine_root", return_value=root), patch.object(speech_clone, "api_reachable", return_value=True), patch.object(speech_clone, "_synthesize_via_api", return_value=output), patch.object(speech_clone, "record_capability_success") as record:
                    outcome = speech_clone.synthesize_to_path(profile_id=profile.id, text="Hello", output_path=output)
                    self.assertEqual(outcome.status, "completed")
                    record.assert_called_once_with("speech")
                    record.reset_mock()
                    with patch.object(speech_clone, "mock_enabled", return_value=True):
                        self.assertEqual(speech_clone.synthesize_to_path(profile_id=profile.id, text="Hello", output_path=output).status, "mock_completed")
                    record.assert_not_called()

    def test_receipt_failure_preserves_successful_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(voice_profiles, "PROFILES_ROOT", root / "profiles"):
                profile = voice_profiles.create_profile(name="Reader", consent_confirmed=True, audio_bytes=b"RIFF....WAVE", filename="ref.wav", notes="Reference.")
                output = root / "out.wav"
                speech_clone._write_silent_wav(output)
                with patch.object(speech_clone, "mock_enabled", return_value=False), patch.object(speech_clone, "resolve_engine_root", return_value=root), patch.object(speech_clone, "api_reachable", return_value=True), patch.object(speech_clone, "_synthesize_via_api", return_value=output), patch.object(speech_clone, "record_capability_success", side_effect=OSError("injected receipt failure")), self.assertLogs("app.speech_clone", level="ERROR"):
                    outcome = speech_clone.synthesize_to_path(profile_id=profile.id, text="Hello", output_path=output)
                self.assertEqual(outcome.status, "completed")
                self.assertTrue(output.is_file())


class SpeechShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_shutdown_still_waits_for_synthesis_drain(self) -> None:
        speech_clone.start()
        entered, release = threading.Event(), threading.Event()

        def hold() -> None:
            with speech_clone.SYNTHESIS_LOCK:
                entered.set()
                release.wait(3)

        holder = asyncio.create_task(asyncio.to_thread(hold))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            drain = asyncio.create_task(speech_clone.shutdown())
            await asyncio.sleep(0.01)
            drain.cancel()
            await asyncio.sleep(0.01)
            self.assertFalse(drain.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await drain
        finally:
            release.set()
            await holder
            speech_clone.start()

    async def test_shutdown_drains_active_trial_and_rejects_queued_and_new_trials(self) -> None:
        self.assertTrue(hasattr(speech_clone, "begin_shutdown"), "speech admission is not closed on shutdown")
        speech_clone.start()
        entered, release = threading.Event(), threading.Event()
        calls = 0

        def synthesis(*, profile_id: str, text: str, output_path: Path, prompt_text: str | None,
                      prompt_language: str | None, text_language: str | None, require_consent: bool) -> speech_clone.SynthesisOutcome:
            nonlocal calls
            calls += 1
            entered.set()
            release.wait(3)
            return speech_clone.SynthesisOutcome(status="completed", detail="", output_path=output_path)

        with tempfile.TemporaryDirectory() as temporary, patch.object(speech_clone, "_synthesize_unlocked", side_effect=synthesis):
            path = Path(temporary) / "out.wav"
            active = asyncio.create_task(asyncio.to_thread(speech_clone.synthesize_to_path, profile_id="a" * 32, text="First", output_path=path))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                queued = asyncio.create_task(asyncio.to_thread(speech_clone.synthesize_to_path, profile_id="a" * 32, text="Queued", output_path=path))
                async with asyncio.timeout(2):
                    while module_jobs._speech_inflight != 2:
                        await asyncio.sleep(0.001)
                speech_clone.begin_shutdown()
                stopped = await asyncio.to_thread(speech_clone.synthesize_to_path, profile_id="a" * 32, text="New", output_path=path)
                self.assertEqual(stopped.detail, "speech_backend_stopping")
                drain = asyncio.create_task(speech_clone.shutdown())
                await asyncio.sleep(0.01)
                self.assertFalse(drain.done())
                release.set()
                self.assertEqual((await active).status, "completed")
                self.assertEqual((await queued).detail, "speech_backend_stopping")
                await drain
                self.assertEqual(calls, 1)
            finally:
                release.set()
                await active
                speech_clone.start()
