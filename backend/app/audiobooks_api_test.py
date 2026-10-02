"""HTTP tests for audiobook synthesis + export (mock speech path)."""
from __future__ import annotations

import os
import tempfile
import unittest
import wave
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app import audiobooks, speech_clone, voice_profiles
from app.api import routes_audiobooks, routes_voice_profiles


class AudiobooksApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.profiles_root = Path(self.temporary.name) / "voice-profiles"
        self.books_root = Path(self.temporary.name) / "audiobooks"
        self.trials = Path(self.temporary.name) / "trials"
        self.profiles_root.mkdir()
        self.books_root.mkdir()
        self.trials.mkdir()
        self.profiles_patch = patch.object(voice_profiles, "PROFILES_ROOT", self.profiles_root)
        self.books_patch = patch.object(audiobooks, "BOOKS_ROOT", self.books_root)
        self.trials_patch = patch.object(speech_clone, "TRIALS_ROOT", self.trials)
        self.engine_patch = patch.object(
            speech_clone, "ENGINE_DIR", Path(self.temporary.name) / "missing-engine"
        )
        self.profiles_patch.start()
        self.books_patch.start()
        self.trials_patch.start()
        self.engine_patch.start()
        self._env = {
            "OPENFABRIC_SPEECH_CLONE_MOCK": os.environ.get("OPENFABRIC_SPEECH_CLONE_MOCK"),
            "OPENFABRIC_AUDIOBOOK_SYNC": os.environ.get("OPENFABRIC_AUDIOBOOK_SYNC"),
        }
        os.environ["OPENFABRIC_SPEECH_CLONE_MOCK"] = "1"
        os.environ["OPENFABRIC_AUDIOBOOK_SYNC"] = "1"
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
        self.trials_patch.stop()
        self.engine_patch.stop()
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temporary.cleanup()

    async def _profile(self) -> str:
        response = await self.client.post(
            "/api/voice-profiles",
            data={"name": "Reader", "consent_confirmed": "true"},
            files={"audio": ("ref.wav", b"RIFF....WAVE", "audio/wav")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    async def test_imported_starter_voice_can_narrate_an_audiobook(self) -> None:
        catalog = await self.client.get("/api/voice-profiles/starter-voices")
        self.assertEqual(catalog.status_code, 200, catalog.text)
        starter = catalog.json()["voices"][0]
        imported = await self.client.post(f"/api/voice-profiles/starter-voices/{starter['id']}/import")
        self.assertEqual(imported.status_code, 200, imported.text)
        profile = imported.json()
        self.assertEqual(profile["notes"], starter["transcript"])
        self.assertEqual(profile["starter_voice_id"], starter["id"])
        created = await self.client.post(
            "/api/audiobooks",
            json={
                "title": "Starter voice book",
                "profile_id": profile["id"],
                "chapters": [{"title": "One", "text": "A short narration trial."}],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        book_id = created.json()["book"]["id"]
        book = await self.client.get(f"/api/audiobooks/{book_id}")
        self.assertEqual(book.status_code, 200, book.text)
        self.assertEqual(book.json()["status"], "done", book.text)
        self.assertEqual(book.json()["profile_id"], profile["id"])
        chapter = await self.client.get(f"/api/audiobooks/{book_id}/chapters/0/audio")
        self.assertEqual(chapter.status_code, 200, chapter.text)
        self.assertTrue(chapter.content.startswith(b"RIFF"))

    async def test_create_book_synthesizes_chapters_and_export(self) -> None:
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
        book_id = payload["book"]["id"]
        # Sync worker runs during POST, so book should already be done.
        book = await self.client.get(f"/api/audiobooks/{book_id}")
        self.assertEqual(book.status_code, 200, book.text)
        self.assertEqual(book.json()["status"], "done", book.text)
        self.assertTrue(book.json()["export_path"])
        self.assertTrue(Path(book.json()["export_path"]).is_file())

        jobs = await self.client.get(f"/api/audiobooks/{book_id}/jobs")
        self.assertEqual(jobs.status_code, 200, jobs.text)
        job_list = jobs.json()["jobs"]
        self.assertEqual(len(job_list), 2)
        for job in job_list:
            self.assertEqual(job["status"], "done", job)
            self.assertTrue(job["output_path"])
            self.assertTrue(Path(job["output_path"]).is_file())
            self.assertGreater(Path(job["output_path"]).stat().st_size, 44)

        export = await self.client.get(f"/api/audiobooks/{book_id}/export")
        self.assertEqual(export.status_code, 200, export.text)
        self.assertTrue(export.content.startswith(b"RIFF"))
        self.assertIn("audio/wav", export.headers.get("content-type", ""))

        chapter = await self.client.get(f"/api/audiobooks/{book_id}/chapters/0/audio")
        self.assertEqual(chapter.status_code, 200, chapter.text)
        self.assertTrue(chapter.content.startswith(b"RIFF"))

        # Concatenated export should be longer than a single silent chapter.
        with wave.open(str(Path(book.json()["export_path"])), "rb") as handle:
            export_frames = handle.getnframes()
        with wave.open(job_list[0]["output_path"], "rb") as handle:
            chapter_frames = handle.getnframes()
        self.assertGreaterEqual(export_frames, chapter_frames * 2)

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

    async def test_rejects_oversized_chapter_text(self) -> None:
        profile_id = await self._profile()
        huge = "x" * (audiobooks.MAX_CHAPTER_CHARS + 1)
        response = await self.client.post(
            "/api/audiobooks",
            json={
                "title": "Too long",
                "profile_id": profile_id,
                "chapters": [{"text": huge}],
            },
        )
        self.assertEqual(response.status_code, 422, response.text)

    async def test_retry_failed_chapters(self) -> None:
        profile_id = await self._profile()
        # First create succeeds under mock.
        created = await self.client.post(
            "/api/audiobooks",
            json={
                "title": "Retry Book",
                "profile_id": profile_id,
                "chapters": [{"title": "Only", "text": "Hello."}],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        book_id = created.json()["book"]["id"]
        # Force a failed job and clear export, then retry under mock.
        with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
            audiobooks._ensure_schema(connection)
            connection.execute(
                """
                UPDATE audiobook_jobs
                SET status = 'failed', detail = 'forced', output_path = NULL
                WHERE book_id = ?
                """,
                (book_id,),
            )
            connection.execute(
                """
                UPDATE audiobook_books
                SET status = 'failed', export_path = NULL
                WHERE id = ?
                """,
                (book_id,),
            )
            connection.commit()

        retried = await self.client.post(f"/api/audiobooks/{book_id}/retry")
        self.assertEqual(retried.status_code, 200, retried.text)
        book = await self.client.get(f"/api/audiobooks/{book_id}")
        self.assertEqual(book.json()["status"], "done", book.text)
        export = await self.client.get(f"/api/audiobooks/{book_id}/export")
        self.assertEqual(export.status_code, 200, export.text)
