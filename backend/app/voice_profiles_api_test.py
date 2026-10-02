"""HTTP tests for speech voice profiles and speech-clone worker (temp data root)."""
from __future__ import annotations

import os
import tempfile
import unittest
import uuid
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
        self.assertFalse(body["api_reachable"])
        self.assertTrue(body["api_base_url"])

    async def test_api_unavailable_when_checkout_present_but_api_down(self) -> None:
        checkout = Path(self.temporary.name) / "gpt-sovits"
        (checkout / "GPT_SoVITS").mkdir(parents=True)
        self.engine_patch.stop()
        self.engine_patch = patch.object(speech_clone, "ENGINE_DIR", checkout)
        self.engine_patch.start()
        created = await self._create()
        profile_id = created.json()["id"]
        with patch.object(speech_clone, "api_reachable", return_value=False):
            trial = await self.client.post(
                "/api/speech-clone/trials",
                json={"profile_id": profile_id, "text": "Detected engine.", "engine": "gpt-sovits"},
            )
        self.assertEqual(trial.status_code, 200, trial.text)
        payload = trial.json()
        self.assertEqual(payload["status"], "api_unavailable")
        self.assertTrue(speech_clone.engine_installed())
        self.assertIn("not reachable", payload["detail"])

    async def test_completed_when_api_returns_wav(self) -> None:
        checkout = Path(self.temporary.name) / "gpt-sovits"
        (checkout / "GPT_SoVITS").mkdir(parents=True)
        self.engine_patch.stop()
        self.engine_patch = patch.object(speech_clone, "ENGINE_DIR", checkout)
        self.engine_patch.start()
        created = await self._create()
        profile_id = created.json()["id"]
        fake_payload = b"RIFF" + (b"\x00" * 4) + b"WAVE" + b"fmt " + (b"\x00" * 50)

        def _fake_synth(**kwargs):
            out = Path(kwargs["output_path"]) if kwargs.get("output_path") is not None else (
                speech_clone.trials_root() / f"{uuid.uuid4().hex}.wav"
            )
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(fake_payload)
            return out

        with patch.object(speech_clone, "api_reachable", return_value=True), patch.object(
            speech_clone, "_synthesize_via_api", side_effect=_fake_synth
        ):
            trial = await self.client.post(
                "/api/speech-clone/trials",
                json={
                    "profile_id": profile_id,
                    "text": "Hello real path.",
                    "engine": "gpt-sovits",
                    "prompt_text": "demo",
                    "text_language": "en",
                },
            )
        self.assertEqual(trial.status_code, 200, trial.text)
        payload = trial.json()
        self.assertEqual(payload["status"], "completed")
        self.assertTrue(payload["trial_id"])
        self.assertTrue(Path(payload["output_path"]).is_file())
