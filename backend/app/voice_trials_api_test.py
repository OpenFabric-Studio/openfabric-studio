from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app import voice_build, voice_comparisons
from app.main import app
from app.voice_contracts import VoiceComparisonRequest, VoiceComparisonResponse, VoiceComparisonTrial


class VoiceTrialsHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.voice_id = "a" * 32
        self.voice = self.root / self.voice_id
        self.voice.mkdir()
        voice_build.write_meta(self.voice, voice_build.new_voice_meta(self.voice_id, "Singer"))
        self.context = patch.object(voice_build, "VOICES_DIR", self.root)
        self.context.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.context.stop()
        self.temporary.cleanup()

    async def upload(self) -> str:
        with patch.object(voice_build, "_probe_duration", new=AsyncMock(return_value=10.0)):
            response = await self.client.post(f"/api/voices/{self.voice_id}/trial-sources", files={"file": ("../held-out.wav", b"audio", "audio/wav")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["filename"], "held-out.wav")
        return str(response.json()["id"])

    async def test_uploaded_evaluation_audio_never_becomes_training_recording(self) -> None:
        source_id = await self.upload()
        response = await self.client.get(f"/api/voices/{self.voice_id}/trial-sources")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["sources"][0]["id"], source_id)
        self.assertFalse((self.voice / "recordings").exists())
        self.assertEqual(voice_build.read_meta(self.voice)["recordings"], [])

    async def test_invalid_combinations_fail_before_model_work_without_echoing_inputs(self) -> None:
        invalid = [
            {"model_ids": ["base", "base"]},
            {"model_ids": ["base"], "diffusion_steps": [30, 30]},
            {"model_ids": ["a", "b", "c", "d"], "reference_ids": ["a", "b", "c"], "diffusion_steps": [30, 50]},
            {"model_ids": ["base"], "start_sec": float("nan")},
            {"model_ids": ["base"], "duration_sec": 31},
        ]
        with patch.object(voice_comparisons, "start_comparison") as start:
            for fields in invalid:
                with self.subTest(fields=fields):
                    response = await self.client.post(f"/api/voices/{self.voice_id}/comparisons", content=json.dumps({"source_id": "c" * 32, **fields}), headers={"content-type": "application/json"})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(response.json()["detail"], "invalid_request")
                    self.assertNotIn("NaN", response.text)
                    self.assertTrue(all(set(error) == {"path", "code"} for error in response.json()["errors"]))
            start.assert_not_called()

    async def test_unknown_model_is_rejected_without_starting_background_task(self) -> None:
        source_id = await self.upload()
        with patch.object(voice_comparisons.asyncio, "create_task") as start:
            response = await self.client.post(f"/api/voices/{self.voice_id}/comparisons", json={"source_id": source_id, "model_ids": ["unknown"]})
        self.assertEqual(response.status_code, 404, response.text)
        start.assert_not_called()

    def completed(self) -> VoiceComparisonResponse:
        return VoiceComparisonResponse(id="b" * 32, voice_id=self.voice_id, status="done", source_filename="held-out.wav", request=VoiceComparisonRequest(source_id="c" * 32, model_ids=["base"]), trials=[VoiceComparisonTrial(id="0", model_id="base", reference_id="published", diffusion_steps=30, status="done")])

    async def test_rating_bounds_and_persistence_survive_cold_read(self) -> None:
        saved = self.completed()
        voice_comparisons.persist_response(self.voice, saved)
        endpoint = f"/api/voices/{self.voice_id}/comparisons/{saved.id}/trials/0/rating"
        invalid = await self.client.patch(endpoint, json={"identity": 6, "pitch": 3, "intelligibility": 4, "artifacts": 2})
        self.assertEqual(invalid.status_code, 422, invalid.text)
        response = await self.client.patch(endpoint, json={"identity": 5, "pitch": 3, "intelligibility": 4, "artifacts": 2, "notes": "Pitch stable, identity weaker."})
        self.assertEqual(response.status_code, 200, response.text)
        recovered = await self.client.get(f"/api/voices/{self.voice_id}/comparisons/{saved.id}")
        self.assertEqual(recovered.json()["trials"][0]["rating"]["identity"], 5)

    async def test_audio_endpoint_rejects_symlink_escape(self) -> None:
        saved = self.completed()
        voice_comparisons.persist_response(self.voice, saved)
        directory = self.voice / "trials" / "comparisons" / saved.id / "audio"
        directory.mkdir()
        outside = self.root / "private.wav"
        outside.write_bytes(b"private")
        (directory / "0.wav").symlink_to(outside)
        response = await self.client.get(f"/api/voices/{self.voice_id}/comparisons/{saved.id}/trials/0/audio")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertNotIn("private", response.text)

    async def test_conflicting_resume_and_checkpoint_modes_fail_at_api_boundary(self) -> None:
        with patch.object(voice_build, "start_build") as start:
            for request in ({"resume": True, "compare_checkpoints": True}, {"resume": True, "training_steps": 0}, {"compare_checkpoints": True, "training_steps": 0}):
                response = await self.client.post(f"/api/voices/{self.voice_id}/build", json=request)
                self.assertEqual(response.status_code, 422, response.text)
            start.assert_not_called()
