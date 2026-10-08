"""Upload limits, complete audio decoding, and profile mutation origin regressions."""
from __future__ import annotations

import io
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from starlette.datastructures import UploadFile

from app import voice_profiles
from app.api import routes_voice_profiles
from app.voice_profile_test_fixtures import wav_bytes
from app.voice_profile_contracts import SpeechVoiceProfile


def flac_bytes(frames: int, declared: int | None = None) -> bytes:
    buffer = io.BytesIO()
    sf.write(buffer, np.random.default_rng(1).uniform(-0.5, 0.5, frames), 16000, format="FLAC")
    raw = bytearray(buffer.getvalue())
    if declared is not None:
        packed = int.from_bytes(raw[18:26], "big")
        raw[18:26] = ((packed >> 36 << 36) | declared).to_bytes(8, "big")
    return bytes(raw)


class ReferenceAudioValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.enterContext(patch.object(voice_profiles, "PROFILES_ROOT", self.root / "profiles"))

    def create(self, audio: bytes, filename: str = "ref.wav") -> SpeechVoiceProfile:
        return voice_profiles.create_profile(name="Reader", consent_confirmed=True,
            audio_bytes=audio, filename=filename, reference_transcript="Reference.")

    def assert_invalid(self, audio: bytes, filename: str = "ref.wav", code: str = "invalid_audio") -> None:
        with self.assertRaises(voice_profiles.VoiceProfileError) as caught:
            self.create(audio, filename)
        self.assertEqual(caught.exception.code, code)
        self.assertFalse((self.root / "profiles").exists(), "Invalid input must not create storage")

    def test_rejects_malformed_wav_before_storage(self) -> None:
        self.assert_invalid(b"RIFF....WAVE")

    def test_rejects_declared_data_truncation_even_when_decoder_clamps_frames(self) -> None:
        self.assert_invalid(wav_bytes()[:-2])

    def test_rejects_truncated_chunk_with_corrected_riff_size(self) -> None:
        raw = bytearray(wav_bytes()[:-2])
        struct.pack_into("<I", raw, 4, len(raw) - 8)
        self.assert_invalid(bytes(raw))

    def test_rejects_incomplete_pcm_frame_with_corrected_chunk_sizes(self) -> None:
        raw = bytearray(wav_bytes()[:-1])
        struct.pack_into("<I", raw, 4, len(raw) - 8)
        struct.pack_into("<I", raw, 40, len(raw) - 44)
        self.assert_invalid(bytes(raw))

    def test_rejects_incomplete_pcm_frame_with_false_block_alignment(self) -> None:
        raw = bytearray(wav_bytes()[:-1])
        struct.pack_into("<I", raw, 4, len(raw) - 8)
        struct.pack_into("<I", raw, 40, len(raw) - 44)
        struct.pack_into("<H", raw, 32, 1)
        self.assert_invalid(bytes(raw))

    def test_rejects_decode_failure_without_raw_error(self) -> None:
        self.assert_invalid(b"fLaCnot a recording", "ref.flac")

    def test_rejects_understated_flac_frame_count(self) -> None:
        self.assert_invalid(flac_bytes(16000, declared=100), "ref.flac")

    def test_rejects_truncated_flac_tail_with_understated_frame_count(self) -> None:
        self.assert_invalid(flac_bytes(16000, declared=100)[:-100], "ref.flac")

    def test_bounds_flac_decode_even_when_metadata_understates_duration(self) -> None:
        with patch.object(voice_profiles, "MAX_REFERENCE_SECONDS", 1):
            self.assert_invalid(flac_bytes(16001, declared=100), "ref.flac", code="audio_decode_limit")

    def test_rejects_unknown_flac_frame_count_before_engine_reads(self) -> None:
        self.assert_invalid(flac_bytes(16000, declared=0), "ref.flac")

    def test_rejects_nonfinite_float_samples(self) -> None:
        path = self.root / "nan.wav"
        sf.write(path, np.array([0.0, np.nan, np.inf], dtype=np.float32), 16000, subtype="FLOAT")
        self.assert_invalid(path.read_bytes())

    def test_rejects_file_byte_limit_before_decoder(self) -> None:
        with patch.object(voice_profiles, "MAX_REFERENCE_AUDIO_BYTES", 100, create=True), patch.object(sf, "info") as info:
            self.assert_invalid(wav_bytes(), code="audio_too_large")
        info.assert_not_called()

    def test_validation_storage_failure_returns_unavailable_without_profile(self) -> None:
        with patch.object(Path, "write_bytes", side_effect=OSError("private temporary storage")), self.assertRaises(voice_profiles.VoiceProfileError) as caught:
            self.create(wav_bytes())
        self.assertEqual(caught.exception.code, "profile_storage_unavailable")
        self.assertEqual(caught.exception.status, 503)
        self.assertFalse((self.root / "profiles").exists())

    def test_rejects_decoded_duration_limit(self) -> None:
        with patch.object(voice_profiles, "MAX_REFERENCE_SECONDS", 1, create=True):
            self.assert_invalid(wav_bytes(frames=16001), code="audio_decode_limit")

    def test_rejects_decoded_channel_and_sample_rate_limits(self) -> None:
        for options in ({"channels": 9}, {"rate": 192001}):
            with self.subTest(options=options):
                self.assert_invalid(wav_bytes(**options), code="audio_decode_limit")

    def test_decode_failure_preserves_existing_profiles(self) -> None:
        raw = wav_bytes()
        existing = self.create(raw)
        with patch.object(sf, "read", side_effect=RuntimeError("private decoder detail")), self.assertRaises(voice_profiles.VoiceProfileError) as caught:
            self.create(raw)
        self.assertEqual(caught.exception.code, "invalid_audio")
        self.assertEqual(voice_profiles.list_profiles(), [existing])
        self.assertEqual(Path(existing.reference_audio_path).read_bytes(), raw)
        self.assertEqual(len(list((self.root / "profiles").iterdir())), 2)

    def test_preserves_valid_pcm_flac_and_compressed_wav_bytes(self) -> None:
        variants = [("pcm.wav", wav_bytes()), ("odd.wav", wav_bytes(frames=7, width=1))]
        padded = bytearray(wav_bytes())
        padded[12:12] = b"JUNK\x01\0\0\0x\0"
        struct.pack_into("<I", padded, 4, len(padded) - 8)
        variants.append(("odd-padded-chunk.wav", bytes(padded)))
        samples = np.zeros(1600, dtype=np.float32)
        for extension, format_name, subtype in (("flac", "FLAC", "PCM_16"), ("wav", "WAV", "ULAW"), ("wav", "WAVEX", "PCM_24"), ("wav", "RF64", "PCM_16")):
            path = self.root / f"fixture-{format_name}.{extension}"
            sf.write(path, samples, 16000, format=format_name, subtype=subtype)
            variants.append((path.name, path.read_bytes()))
        big_endian = self.root / "big-endian.wav"
        sf.write(big_endian, samples, 16000, endian="BIG")
        variants.append((big_endian.name, big_endian.read_bytes()))
        for filename, raw in variants:
            with self.subTest(filename=filename):
                profile = self.create(raw, filename)
                self.assertEqual(Path(profile.reference_audio_path).read_bytes(), raw)


class ReferenceUploadBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "profiles"
        self.enterContext(patch.object(voice_profiles, "PROFILES_ROOT", self.root))
        application = FastAPI()
        application.include_router(routes_voice_profiles.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://127.0.0.1:9000")
        self.addAsyncCleanup(self.client.aclose)

    async def upload(self, raw: bytes = wav_bytes(), headers: dict[str, str] | None = None) -> httpx.Response:
        return await self.client.post("/api/voice-profiles", headers=headers,
            data={"name": "Reader", "consent_confirmed": "true"}, files={"audio": ("ref.wav", raw, "audio/wav")})

    async def test_invalid_audio_returns_stable_error_and_no_profile(self) -> None:
        response = await self.upload(b"RIFF....WAVE")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "invalid_audio")
        self.assertFalse(self.root.exists())

    async def test_file_size_rejected_before_read(self) -> None:
        with patch.object(voice_profiles, "MAX_REFERENCE_AUDIO_BYTES", 100, create=True), patch.object(UploadFile, "read", new=AsyncMock(side_effect=AssertionError("Oversized file read"))):
            response = await self.upload()
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"], "audio_too_large")
        self.assertFalse(self.root.exists())

    async def test_missing_file_size_still_uses_bounded_read(self) -> None:
        file = UploadFile(io.BytesIO(b"x" * 1000), filename="ref.wav")
        with patch.object(voice_profiles, "MAX_REFERENCE_AUDIO_BYTES", 100), self.assertRaises(HTTPException) as caught:
            await routes_voice_profiles.create_voice_profile(name="Reader", consent_confirmed=True, audio=file,
                notes="", reference_transcript="", reference_language="en")
        self.assertEqual(caught.exception.status_code, 413)
        self.assertEqual(file.file.tell(), 101)
        self.assertFalse(self.root.exists())

    async def test_full_decode_uses_bounded_chunks(self) -> None:
        with patch.object(sf, "read", wraps=sf.read) as read:
            response = await self.upload(wav_bytes(frames=65537))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([call.kwargs["frames"] for call in read.call_args_list], [65536, 1])

    async def test_streamed_multipart_is_bounded_without_content_length(self) -> None:
        consumed = 0
        async def chunks():
            nonlocal consumed
            yield b'--reference\r\nContent-Disposition: form-data; name="audio"; filename="ref.wav"\r\nContent-Type: audio/wav\r\n\r\n'
            for _ in range(10):
                consumed += 1
                yield b"x" * 512
            yield b"\r\n--reference--\r\n"
        with patch.object(routes_voice_profiles, "MAX_UPLOAD_REQUEST_BYTES", 1024, create=True):
            response = await self.client.post("/api/voice-profiles", content=chunks(), headers={"content-type": "multipart/form-data; boundary=reference"})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"], "audio_too_large")
        self.assertLess(consumed, 10)
        self.assertFalse(self.root.exists())

    async def test_decoding_runs_off_event_loop(self) -> None:
        loop_thread = threading.get_ident()
        threads: list[int] = []
        original = sf.read
        def record(*args, **kwargs):
            threads.append(threading.get_ident())
            return original(*args, **kwargs)
        with patch.object(sf, "read", side_effect=record):
            response = await self.upload()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(threads)
        self.assertNotIn(loop_thread, threads)

    async def test_untrusted_origin_rejects_every_mutation_before_work(self) -> None:
        profile = voice_profiles.create_profile(name="Reader", consent_confirmed=True, audio_bytes=wav_bytes(), filename="ref.wav")
        requests = (("POST", "/api/voice-profiles", {}),
            ("POST", "/api/voice-profiles/starter-voices/vctk-p225/import", {}),
            ("POST", "/api/voice-profiles/cloud", {"json": {"name": "Cloud", "model": "openai/gpt-4o-mini-tts", "voice": "alloy"}}),
            ("PATCH", f"/api/voice-profiles/{profile.id}", {"json": {"name": "Changed"}}),
            ("DELETE", f"/api/voice-profiles/{profile.id}", {}))
        for method, path, options in requests:
            with self.subTest(method=method, path=path):
                response = await self.client.request(method, path, headers={"origin": "https://evil.example"}, **options)
                self.assertEqual(response.status_code, 403, response.text)
                self.assertEqual(response.json()["detail"], "setup_origin_forbidden")
        self.assertEqual(voice_profiles.list_profiles(), [profile])

    async def test_cross_site_create_rejected_before_consuming_body(self) -> None:
        async def body():
            self.fail("Untrusted body must not be read")
            yield b"ignored"
        response = await self.client.post("/api/voice-profiles", content=body(), headers={"origin": "http://127.0.0.1:9000", "sec-fetch-site": "cross-site"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.root.exists())
