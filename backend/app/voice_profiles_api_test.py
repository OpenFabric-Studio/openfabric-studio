"""HTTP tests for speech voice profiles and speech-clone worker (temp data root)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app import speech_clone, voice_profiles
from app.api import routes_speech_clone, routes_voice_profiles


class VoiceProfilesApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "voice-profiles"
        self.root.mkdir()
        self.trials = Path(self.temporary.name) / "trials"
        self.trials.mkdir()
        self.engine_missing = Path(self.temporary.name) / "no-engine"
        self.profiles_patch = patch.object(voice_profiles, "PROFILES_ROOT", self.root)
        self.trials_patch = patch.object(speech_clone, "TRIALS_ROOT", self.trials)
        self.engine_patch = patch.object(speech_clone, "ENGINE_DIR", self.engine_missing)
        self.profiles_patch.start()
        self.trials_patch.start()
        self.engine_patch.start()
        os.environ.pop("OPENFABRIC_SPEECH_CLONE_MOCK", None)
        app = FastAPI()
        app.include_router(routes_voice_profiles.router)
        app.include_router(routes_speech_clone.router)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.profiles_patch.stop()
        self.trials_patch.stop()
        self.engine_patch.stop()
        os.environ.pop("OPENFABRIC_SPEECH_CLONE_MOCK", None)
        self.temporary.cleanup()

    async def _create(self, name: str = "Narrator", consent: str = "true") -> httpx.Response:
        return await self.client.post(
            "/api/voice-profiles",
            data={"name": name, "consent_confirmed": consent, "notes": "demo"},
            files={"audio": ("ref.wav", b"RIFF....WAVE", "audio/wav")},
        )

    async def test_create_list_delete_happy_path(self) -> None:
        created = await self._create()
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        self.assertEqual(body["name"], "Narrator")
        self.assertTrue(body["consent_confirmed"])
        self.assertTrue(body["id"])
        self.assertTrue(Path(body["reference_audio_path"]).is_file())

        listed = await self.client.get("/api/voice-profiles")
        self.assertEqual(listed.status_code, 200, listed.text)
        profiles = listed.json()["profiles"]
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["id"], body["id"])

        got = await self.client.get(f"/api/voice-profiles/{body['id']}")
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.json()["notes"], "demo")

        deleted = await self.client.delete(f"/api/voice-profiles/{body['id']}")
        self.assertEqual(deleted.status_code, 204, deleted.text)
        empty = await self.client.get("/api/voice-profiles")
        self.assertEqual(empty.json()["profiles"], [])

    async def test_create_requires_consent(self) -> None:
        response = await self._create(consent="false")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"], "consent_required")

    async def test_speech_clone_trial_returns_engine_not_installed(self) -> None:
        created = await self._create()
        profile_id = created.json()["id"]
        trial = await self.client.post(
            "/api/speech-clone/trials",
            json={"profile_id": profile_id, "text": "Hello from OpenFabric.", "engine": "gpt-sovits"},
        )
        self.assertEqual(trial.status_code, 501, trial.text)
        payload = trial.json()
        self.assertEqual(payload["status"], "engine_not_installed")
        self.assertEqual(payload["profile_id"], profile_id)
        self.assertTrue(any("GPT-SoVITS" in hint for hint in payload["install_hints"]))
        self.assertFalse(speech_clone.engine_installed())

    async def test_speech_clone_mock_writes_wav(self) -> None:
        created = await self._create()
        profile_id = created.json()["id"]
        os.environ["OPENFABRIC_SPEECH_CLONE_MOCK"] = "1"
        trial = await self.client.post(
            "/api/speech-clone/trials",
            json={"profile_id": profile_id, "text": "Mock narration.", "engine": "gpt-sovits"},
        )
        self.assertEqual(trial.status_code, 200, trial.text)
        payload = trial.json()
        self.assertEqual(payload["status"], "mock_completed")
        self.assertTrue(payload["trial_id"])
        self.assertTrue(Path(payload["output_path"]).is_file())
        self.assertGreater(Path(payload["output_path"]).stat().st_size, 44)

    async def test_engine_status_endpoint(self) -> None:
        status = await self.client.get("/api/speech-clone/engine")
        self.assertEqual(status.status_code, 200, status.text)
        body = status.json()
        self.assertFalse(body["installed"])
        self.assertFalse(body["mock"])
        self.assertIsNone(body["root"])

    async def test_engine_ready_when_checkout_present(self) -> None:
        checkout = Path(self.temporary.name) / "gpt-sovits"
        (checkout / "GPT_SoVITS").mkdir(parents=True)
        self.engine_patch.stop()
        self.engine_patch = patch.object(speech_clone, "ENGINE_DIR", checkout)
        self.engine_patch.start()
        created = await self._create()
        profile_id = created.json()["id"]
        trial = await self.client.post(
            "/api/speech-clone/trials",
            json={"profile_id": profile_id, "text": "Detected engine.", "engine": "gpt-sovits"},
        )
        self.assertEqual(trial.status_code, 200, trial.text)
        self.assertEqual(trial.json()["status"], "engine_ready")
        self.assertTrue(speech_clone.engine_installed())
