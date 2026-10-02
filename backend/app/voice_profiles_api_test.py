"""HTTP tests for speech voice profiles (temp data root)."""
from __future__ import annotations

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
        self.profiles_patch = patch.object(voice_profiles, "PROFILES_ROOT", self.root)
        self.profiles_patch.start()
        app = FastAPI()
        app.include_router(routes_voice_profiles.router)
        app.include_router(routes_speech_clone.router)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.profiles_patch.stop()
        self.temporary.cleanup()

    async def _create(self, name: str = "Narrator", consent: str = "true") -> dict:
        response = await self.client.post(
            "/api/voice-profiles",
            data={"name": name, "consent_confirmed": consent, "notes": "demo"},
            files={"audio": ("ref.wav", b"RIFF....WAVE", "audio/wav")},
        )
        return response

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
