"""No paid HTTP: frozen inputs, receipt reuse barriers and real bounded decoding."""
from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from app import audiobooks, cloud_speech, speech_clone, voice_profiles
from app.speech_references import CloudSpeechSnapshot, SpeechRenderSnapshot
from app.voice_profiles import VoiceProfileError


class CloudSpeechAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(audiobooks, "BOOKS_ROOT", self.root / "books"), patch.object(voice_profiles, "PROFILES_ROOT", self.root / "profiles")]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.temp.cleanup)
        speech_clone.start()

    def snapshot(self) -> SpeechRenderSnapshot:
        return SpeechRenderSnapshot(profile_id="a" * 32, prompt_language="en", text_language="en", engine="openrouter",
            cloud=CloudSpeechSnapshot(model="hexgrad/kokoro-82m", voice="af_heart", model_fingerprint="b" * 64))

    def test_cloud_synthesis_refuses_missing_paid_approval_before_network(self) -> None:
        with self.assertRaises(VoiceProfileError) as caught:
            cloud_speech.require_authorization(self.snapshot(), "Hello", self.root / "output.wav")
        self.assertEqual(caught.exception.code, "cloud_speech_approval_required")

    def test_preset_request_contains_text_and_voice_but_no_reference_data(self) -> None:
        body = cloud_speech.request_body(self.snapshot(), "Exact spoken words.")
        self.assertEqual(body.input, "Exact spoken words.")
        self.assertEqual(body.voice, "af_heart")
        self.assertIsNone(body.reference_audio)
        self.assertIsNone(body.reference_transcript)

    def test_reference_mutation_is_rejected_before_inline_encoding(self) -> None:
        from app.speech_references import file_digest
        reference = self.root / "ref.wav"
        speech_clone._write_silent_wav(reference)
        snapshot = SpeechRenderSnapshot(profile_id="a" * 32, prompt_language="en", text_language="en", engine="openrouter",
            reference_audio_path=str(reference), reference_sha256=file_digest(reference), prompt_text="True reference words.",
            cloud=CloudSpeechSnapshot(model="fish-audio/s2.1-pro", clone_reference=True, reference_transfer_confirmed=True, model_fingerprint="b" * 64))
        reference.write_bytes(b"changed")
        with self.assertRaises(VoiceProfileError) as caught:
            cloud_speech.request_body(snapshot, "Hello")
        self.assertEqual(caught.exception.code, "speech_reference_changed")

    def test_invalid_mp3_preserves_existing_pcm_and_cleans_temporary_files(self) -> None:
        output = self.root / "existing.wav"
        output.write_bytes(b"existing")
        with self.assertRaises(VoiceProfileError) as caught:
            cloud_speech.normalize_audio(b"not mp3", output)
        self.assertEqual(caught.exception.code, "cloud_speech_audio_invalid")
        self.assertEqual(output.read_bytes(), b"existing")
        self.assertEqual(list((audiobooks.books_root() / "_cloud_speech").glob("*.mp3")), [])

    def test_actual_mp3_is_normalized_to_matching_24khz_mono_pcm(self) -> None:
        from app import audiobook_publish
        source = self.root / "source.wav"
        speech_clone._write_silent_wav(source)
        encoded = self.root / "source.mp3"
        result = audiobook_publish._run(["-y", "-i", str(source), str(encoded)])
        if result.returncode:
            self.skipTest("system FFmpeg has no MP3 encoder")
        output = self.root / "output.wav"
        cloud_speech.normalize_audio(encoded.read_bytes(), output)
        with wave.open(str(output), "rb") as audio:
            self.assertEqual((audio.getframerate(), audio.getnchannels(), audio.getsampwidth()), (24000, 1, 2))
            self.assertGreater(audio.getnframes(), 0)

    def test_decode_cap_rejects_audio_without_publishing_truncated_success(self) -> None:
        from app import audiobook_publish
        source = self.root / "source.wav"
        speech_clone._write_silent_wav(source)
        encoded = self.root / "source.mp3"
        result = audiobook_publish._run(["-y", "-i", str(source), str(encoded)])
        if result.returncode:
            self.skipTest("system FFmpeg has no MP3 encoder")
        with patch.object(cloud_speech, "MAX_PCM_BYTES", 1000), self.assertRaises(VoiceProfileError) as caught:
            cloud_speech.normalize_audio(encoded.read_bytes(), self.root / "output.wav")
        self.assertEqual(caught.exception.code, "cloud_speech_audio_invalid")
        self.assertFalse((self.root / "output.wav").exists())

    def test_paid_quote_cannot_be_used_for_changed_text_or_reused(self) -> None:
        import time
        from app.voice_profile_contracts import CloudSpeechApproval, CloudSpeechQuote
        snapshot = self.snapshot()
        state = cloud_speech._QuoteState(public=CloudSpeechQuote(id="c" * 32, estimated_usd=0.01, request_count=1, models=[snapshot.cloud.model], transfers=["text"], expires_at=time.time()+300),
            inputs={cloud_speech._input_id(snapshot, "Hello"): 1}, estimates={cloud_speech._input_id(snapshot, "Hello"):0.01})
        with cloud_speech._connect() as connection:
            connection.execute("INSERT INTO quotes VALUES(?,?)", (state.public.id,state.model_dump_json()))
        approval = CloudSpeechApproval(quote_id=state.public.id, transfers_confirmed=True)
        with self.assertRaises(VoiceProfileError) as caught:
            cloud_speech.approve_snapshots([("Changed",snapshot)], approval)
        self.assertEqual(caught.exception.code, "cloud_speech_quote_changed")
        self.assertEqual(cloud_speech.approve_snapshots([("Hello",snapshot)], approval), state.public.id)
        with self.assertRaises(VoiceProfileError):
            cloud_speech.approve_snapshots([("Hello",snapshot)], approval)
