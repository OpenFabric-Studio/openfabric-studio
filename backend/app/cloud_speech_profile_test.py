"""Cloud profiles preserve local data and make reference transfer explicit."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import voice_profiles
from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest


class CloudProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.storage = patch.object(voice_profiles, "PROFILES_ROOT", self.root / "profiles")
        self.storage.start()
        self.addCleanup(self.storage.stop)
        self.addCleanup(self.temporary.cleanup)

    def test_existing_profile_defaults_local_and_retains_recording(self) -> None:
        profile = voice_profiles.create_profile(name="Reader", consent_confirmed=True,
            audio_bytes=b"reference fixture", filename="ref.wav", reference_transcript="Real words.")
        self.assertEqual(profile.renderer, "local")
        self.assertIsNone(profile.cloud)
        self.assertEqual(Path(profile.reference_audio_path).read_bytes(), b"reference fixture")

    def test_cloud_preset_needs_neither_recording_nor_fake_transcript(self) -> None:
        from app.voice_profile_contracts import CreateCloudSpeechVoiceProfileRequest
        with patch("app.cloud_speech.validate_configuration"):
            profile = voice_profiles.create_cloud_profile(CreateCloudSpeechVoiceProfileRequest(
                name="Cloud narrator", model="openai/gpt-4o-mini-tts", voice="alloy"))
        self.assertEqual(profile.renderer, "openrouter")
        self.assertEqual(profile.reference_audio_path, "")
        self.assertEqual(profile.reference_transcript, "")
        self.assertEqual(list((voice_profiles.profiles_root() / profile.id).glob("*")), [])
        saved = voice_profiles.get_profile(profile.id)
        self.assertIsNotNone(saved.cloud)
        if saved.cloud is not None:
            self.assertEqual(saved.cloud.voice, "alloy")

    def test_reference_clone_requires_separate_transfer_permission(self) -> None:
        from app.voice_profile_contracts import CloudSpeechConfiguration
        profile = voice_profiles.create_profile(name="Reader", consent_confirmed=True,
            audio_bytes=b"reference fixture", filename="ref.wav", reference_transcript="Real words.")
        body = PatchSpeechVoiceProfileRequest(renderer="openrouter", cloud=CloudSpeechConfiguration(
            model="fish-audio/s2.1-pro", clone_reference=True))
        with patch("app.cloud_speech.validate_configuration"), self.assertRaises(voice_profiles.VoiceProfileError) as caught:
            voice_profiles.patch_profile(profile.id, body)
        self.assertEqual(caught.exception.code, "cloud_reference_permission_required")
        self.assertEqual(voice_profiles.get_profile(profile.id).renderer, "local")

    def test_switching_renderer_preserves_local_reference_and_transcript(self) -> None:
        from app.voice_profile_contracts import CloudSpeechConfiguration
        profile = voice_profiles.create_profile(name="Reader", consent_confirmed=True,
            audio_bytes=b"reference fixture", filename="ref.wav", reference_transcript="Real words.")
        with patch("app.cloud_speech.validate_configuration"):
            changed = voice_profiles.patch_profile(profile.id, PatchSpeechVoiceProfileRequest(
                renderer="openrouter", cloud=CloudSpeechConfiguration(model="openai/gpt-4o-mini-tts", voice="alloy")))
        restored = voice_profiles.patch_profile(profile.id, PatchSpeechVoiceProfileRequest(renderer="local"))
        self.assertEqual(changed.renderer, "openrouter")
        self.assertEqual(restored.renderer, "local")
        self.assertEqual(restored.reference_transcript, "Real words.")
        self.assertEqual(Path(restored.reference_audio_path).read_bytes(), b"reference fixture")

    def test_v2_migration_adds_local_renderer_without_rewriting_existing_columns(self) -> None:
        profile = voice_profiles.create_profile(name="Reader", consent_confirmed=True,
            audio_bytes=b"reference fixture", filename="ref.wav", notes="Notes", reference_transcript="Real words.")
        with sqlite3.connect(voice_profiles._db_path()) as connection:
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(voice_profiles)")}
            for column in ("renderer", "cloud_json"):
                if column in columns:
                    connection.execute(f"ALTER TABLE voice_profiles DROP COLUMN {column}")
            connection.execute("PRAGMA user_version = 2")
        migrated = voice_profiles.get_profile(profile.id)
        self.assertEqual(migrated.renderer, "local")
        self.assertEqual(migrated.notes, "Notes")
        self.assertEqual(migrated.reference_transcript, "Real words.")
        self.assertEqual(Path(migrated.reference_audio_path).read_bytes(), b"reference fixture")

class CloudSnapshotTests(CloudProfileTests):
    def preset(self):
        from app.voice_profile_contracts import CreateCloudSpeechVoiceProfileRequest
        with patch("app.cloud_speech.validate_configuration"):
            return voice_profiles.create_cloud_profile(CreateCloudSpeechVoiceProfileRequest(name="Cloud narrator", model="hexgrad/kokoro-82m", voice="af_heart"))

    def test_preset_capture_freezes_model_voice_without_copying_reference(self) -> None:
        from app.speech_references import capture
        from app.voice_profile_contracts import CloudSpeechConfiguration
        profile = self.preset()
        with patch("app.cloud_speech.validate_configuration"), patch("app.cloud_speech.model_fingerprint", return_value="b" * 64):
            snapshot = capture(profile.id, "en", self.root / "snapshot", None)
            voice_profiles.patch_profile(profile.id, PatchSpeechVoiceProfileRequest(cloud=CloudSpeechConfiguration(model="hexgrad/kokoro-82m", voice="af_bella")))
        self.assertIsNotNone(snapshot.cloud)
        if snapshot.cloud is not None:
            self.assertEqual(snapshot.cloud.voice, "af_heart")
            self.assertEqual(snapshot.cloud.model_fingerprint, "b" * 64)
        self.assertEqual(snapshot.reference_audio_path, "")
        self.assertEqual(snapshot.prompt_text, "")
        self.assertIsNone(snapshot.engine_identity)

    def test_cloud_profile_never_uses_local_mock_as_a_paid_voice(self) -> None:
        from app import speech_clone
        profile = self.preset()
        with patch.object(speech_clone, "mock_enabled", return_value=True), patch("app.cloud_speech.synthesize", return_value=speech_clone.SynthesisOutcome(status="failed", detail="openrouter_not_configured")) as cloud:
            result = speech_clone.synthesize_to_path(profile_id=profile.id, text="Hello", output_path=self.root / "trial.wav")
        cloud.assert_called_once()
        self.assertEqual(result.status, "failed")
        self.assertFalse((self.root / "trial.wav").exists())
