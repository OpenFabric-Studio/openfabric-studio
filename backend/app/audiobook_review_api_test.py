from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi import FastAPI
from app import audiobooks, audiobook_review as qa
from app.api.routes_audiobook_review import router
from app.audiobook_review_contracts import AsrCapability


class AsrReviewApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.enterContext(patch.object(audiobooks, "BOOKS_ROOT", Path(self.temporary.name) / "books"))
        audio = audiobooks.book_dir("a" * 32) / "source.wav"
        with wave.open(str(audio), "wb") as handle:
            handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(16000); handle.writeframes(b"\0\0" * 16000)
        snapshot = qa.PassageSnapshot("a" * 32, 0, "b" * 32, 1, "c" * 64, audio, "expected words", "en")
        self.snapshot = self.enterContext(patch.object(qa, "_snapshot", return_value=snapshot))
        self.enterContext(patch.object(qa, "capability", return_value=AsrCapability(available=False, reason="whisper_missing")))
        app = FastAPI(); app.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")
        self.addAsyncCleanup(self.client.aclose)
        await qa.start(); self.addAsyncCleanup(qa.shutdown)

    async def test_pause_settings_validate_persist_and_reject_foreign_origin(self) -> None:
        before = await self.client.get("/api/audiobooks/analysis/settings")
        self.assertEqual(before.json(), {"energy_ratio": 0.12, "min_silence_ms": 280, "padding_ms": 0})
        result = await self.client.put("/api/audiobooks/analysis/settings", json={"energy_ratio": 0.2, "min_silence_ms": 400, "padding_ms": 20})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual((await self.client.get("/api/audiobooks/analysis/settings")).json(), result.json())
        self.assertEqual((await self.client.put("/api/audiobooks/analysis/settings", json={"energy_ratio": -1})).status_code, 422)
        self.assertEqual((await self.client.put("/api/audiobooks/analysis/settings", json={}, headers={"Origin": "https://attacker.example"})).status_code, 403)

    async def test_review_is_explicit_version_checked_and_reloadable_without_private_paths(self) -> None:
        url = "/api/audiobooks/" + "a" * 32 + "/chapters/0/passages/" + "b" * 32 + "/qa"
        invalid = await self.client.post(url, json={"revision": 2, "render_identity": "c" * 64})
        self.assertEqual(invalid.status_code, 409, invalid.text)
        result = await self.client.post(url, json={"revision": 1, "render_identity": "c" * 64})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["state"], "unavailable")
        self.assertNotIn(self.temporary.name, result.text)
        loaded = await self.client.get("/api/audiobooks/qa/" + result.json()["id"])
        self.assertEqual(loaded.json(), result.json())
        forbidden = await self.client.post(url, json={"revision": 1, "render_identity": "c" * 64}, headers={"Origin": "https://attacker.example"})
        self.assertEqual(forbidden.status_code, 403)
