"""HTTP tests for audiobook stub API."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app import audiobooks, voice_profiles
from app.api import routes_audiobooks, routes_voice_profiles


class AudiobooksApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.profiles_root = Path(self.temporary.name) / "voice-profiles"
        self.books_root = Path(self.temporary.name) / "audiobooks"
        self.profiles_root.mkdir()
        self.books_root.mkdir()
        self.profiles_patch = patch.object(voice_profiles, "PROFILES_ROOT", self.profiles_root)
        self.books_patch = patch.object(audiobooks, "BOOKS_ROOT", self.books_root)
        self.profiles_patch.start()
        self.books_patch.start()
        app = FastAPI()
        app.include_router(routes_voice_profiles.router)
        app.include_router(routes_audiobooks.router)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.profiles_patch.stop()
        self.books_patch.stop()
        self.temporary.cleanup()

    async def _profile(self) -> str:
        response = await self.client.post(
            "/api/voice-profiles",
            data={"name": "Reader", "consent_confirmed": "true"},
            files={"audio": ("ref.wav", b"RIFF....WAVE", "audio/wav")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    async def test_create_book_queues_chapter_jobs(self) -> None:
        profile_id = await self._profile()
        created = await self.client.post(
            "/api/audiobooks",
            json={
                "title": "Demo Book",
                "profile_id": profile_id,
                "chapters": [
                    {"title": "One", "text": "Chapter one text."},
                    {"title": "Two", "text": "Chapter two text."},
                ],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        payload = created.json()
        self.assertEqual(payload["book"]["title"], "Demo Book")
        self.assertEqual(payload["book"]["status"], "queued")
        self.assertEqual(payload["book"]["chapter_count"], 2)
        self.assertEqual(len(payload["jobs"]), 2)
        self.assertEqual(payload["jobs"][0]["status"], "queued")
        self.assertEqual(payload["jobs"][0]["chapter_index"], 0)

        listed = await self.client.get("/api/audiobooks")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(len(listed.json()["books"]), 1)

        jobs = await self.client.get(f"/api/audiobooks/{payload['book']['id']}/jobs")
        self.assertEqual(jobs.status_code, 200, jobs.text)
        self.assertEqual(len(jobs.json()["jobs"]), 2)

        all_jobs = await self.client.get("/api/audiobooks/jobs")
        self.assertEqual(all_jobs.status_code, 200, all_jobs.text)
        self.assertEqual(len(all_jobs.json()["jobs"]), 2)

    async def test_create_requires_existing_consented_profile(self) -> None:
        missing = await self.client.post(
            "/api/audiobooks",
            json={
                "title": "Nope",
                "profile_id": "0" * 32,
                "chapters": [{"text": "hi"}],
            },
        )
        self.assertEqual(missing.status_code, 404, missing.text)
