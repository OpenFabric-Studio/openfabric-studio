"""Audiobook import, pronunciation, chapter regenerate, and encoded exports."""
from __future__ import annotations

import json
import os
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app import audiobooks, speech_clone, voice_profiles
from app.api import routes_audiobooks, routes_voice_profiles
from app.ebook_import import chapters_from_plain_text
from app.ebook_import_test import epub_fixture


def _png() -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b"")


class PlainTextChapterTests(unittest.TestCase):
    def test_headings_split_chapters_and_plain_prose_stays_one_chapter(self) -> None:
        extracted = chapters_from_plain_text("Chapter 1\nAlpha.\n\n# Second\nBeta.", "Notes")
        self.assertEqual([chapter.title for chapter in extracted.chapters], ["Chapter 1", "Second"])
        self.assertEqual(extracted.chapters[0].text, "Alpha.")
        self.assertEqual(extracted.chapters[1].text, "Beta.")
        single = chapters_from_plain_text("No headings here.", "Notes")
        self.assertEqual(len(single.chapters), 1)
        self.assertEqual(single.warnings[0].code, "chapter_detection")


class AudiobookOptionsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.patches = [
            patch.object(voice_profiles, "PROFILES_ROOT", root / "profiles"),
            patch.object(audiobooks, "BOOKS_ROOT", root / "books"),
            patch.object(speech_clone, "TRIALS_ROOT", root / "trials"),
            patch.object(speech_clone, "ENGINE_DIR", root / "missing-engine"),
            patch.dict(os.environ, {"OPENFABRIC_SPEECH_CLONE_MOCK": "1", "OPENFABRIC_AUDIOBOOK_SYNC": "1"}),
        ]
        for item in self.patches:
            item.start()
        app = FastAPI()
        app.include_router(routes_voice_profiles.router)
        app.include_router(routes_audiobooks.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")
        created = await self.client.post(
            "/api/voice-profiles",
            data={"name": "Reader", "consent_confirmed": "true", "reference_transcript": "Reference."},
            files={"audio": ("ref.wav", b"RIFF....WAVE", "audio/wav")},
        )
        self.assertEqual(created.status_code, 200, created.text)
        self.profile_id = created.json()["id"]
        self.spoken: list[str] = []
        original = speech_clone.synthesize_to_path

        def record(**kwargs: object) -> speech_clone.SynthesisOutcome:
            self.spoken.append(str(kwargs["text"]))
            return original(**kwargs)  # type: ignore[arg-type]

        self.record = patch.object(speech_clone, "synthesize_to_path", side_effect=record)
        self.record.start()

    async def asyncTearDown(self) -> None:
        self.record.stop()
        await self.client.aclose()
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    async def _create(self, chapters: list[dict[str, str]], **extra: object) -> dict[str, object]:
        response = await self.client.post("/api/audiobooks", json={
            "title": "Options", "profile_id": self.profile_id, "chapters": chapters, **extra,
        })
        self.assertEqual(response.status_code, 200, response.text)
        book_id = response.json()["book"]["id"]
        fetched = await self.client.get(f"/api/audiobooks/{book_id}")
        self.assertEqual(fetched.status_code, 200, fetched.text)
        body = fetched.json()
        self.assertEqual(body["status"], "done", fetched.text)
        return {"book": body, "jobs": response.json()["jobs"]}

    async def test_txt_epub_and_pasted_text_become_reviewable_chapters(self) -> None:
        pasted = await self.client.post("/api/audiobooks/imports/text", json={
            "title": "Pasted", "author": "Ada", "text": "Chapter 1\nAlpha.\n\nChapter 2\nBeta.",
        })
        self.assertEqual(pasted.status_code, 200, pasted.text)
        self.assertEqual(pasted.json()["author"], "Ada")
        self.assertEqual([chapter["title"] for chapter in pasted.json()["chapters"]], ["Chapter 1", "Chapter 2"])
        text = await self.client.post("/api/audiobooks/imports", files={"file": ("story.txt", b"Just prose.", "text/plain")})
        self.assertEqual(text.status_code, 200, text.text)
        self.assertEqual(text.json()["chapters"][0]["text"], "Just prose.")
        self.assertEqual(text.json()["source_filename"], "story.txt")
        epub = await self.client.post("/api/audiobooks/imports", files={"file": ("book.epub", epub_fixture(), "application/epub+zip")})
        self.assertEqual(epub.status_code, 200, epub.text)
        self.assertGreaterEqual(len(epub.json()["chapters"]), 2)
        self.assertTrue(any("First" in chapter["text"] or "sentence" in chapter["text"] for chapter in epub.json()["chapters"]))
        rejected = await self.client.post("/api/audiobooks/imports", files={"file": ("notes.pdf", b"%PDF", "application/pdf")})
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.json()["detail"], "unsupported_ebook_format")

    async def test_pronunciation_map_is_spoken_and_one_chapter_can_be_regenerated(self) -> None:
        created = await self._create(
            [{"title": "One", "text": "Meet Dr. Smith."}, {"title": "Two", "text": "Leave NASA today."}],
            pronunciations=[{"written": "Dr.", "spoken": "Doctor"}],
            author="Jo",
        )
        book_id = str(created["book"]["id"])
        self.assertEqual(created["book"]["author"], "Jo")
        self.assertEqual(self.spoken, ["Meet Doctor Smith.", "Leave NASA today."])
        updated = await self.client.put(f"/api/audiobooks/{book_id}/pronunciations", json={
            "pronunciations": [{"written": "Dr.", "spoken": "Doctor"}, {"written": "NASA", "spoken": "N A S A"}],
        })
        self.assertEqual(updated.status_code, 200, updated.text)
        before = len(self.spoken)
        regenerated = await self.client.post(f"/api/audiobooks/{book_id}/chapters/1/regenerate")
        self.assertEqual(regenerated.status_code, 200, regenerated.text)
        self.assertEqual(regenerated.json()["status"], "done")
        self.assertEqual(self.spoken[before:], ["Leave N A S A today."])
        self.assertEqual(self.spoken.count("Meet Doctor Smith."), 1)

    async def test_removed_cast_actor_consent_is_checked_for_retained_chapter_publication(self) -> None:
        actor = await self.client.post("/api/voice-profiles", data={"name": "Alice", "consent_confirmed": "true", "reference_transcript": "Reference."},
            files={"audio": ("alice.wav", b"RIFF....WAVE", "audio/wav")})
        self.assertEqual(actor.status_code, 200, actor.text)
        actor_id = actor.json()["id"]
        created = await self.client.post("/api/audiobooks", json={
            "title": "Retained cast", "profile_id": self.profile_id,
            "cast": [{"name": "Alice", "profile_id": actor_id}],
            "chapters": [{"title": "Actor", "text": "Alice: The actor's original chapter."},
                         {"title": "Narrator", "text": "The narrator's other chapter."}],
        })
        self.assertEqual(created.status_code, 200, created.text)
        identifier = created.json()["book"]["id"]
        original_chapter = await self.client.get(f"/api/audiobooks/{identifier}/chapters/0/audio")
        self.assertEqual(original_chapter.status_code, 200, original_chapter.text)
        removed = await self.client.put(f"/api/audiobooks/{identifier}/cast", json={"cast": []})
        self.assertEqual(removed.status_code, 200, removed.text)
        revoked = await self.client.patch(f"/api/voice-profiles/{actor_id}", json={"consent_confirmed": False})
        self.assertEqual(revoked.status_code, 200, revoked.text)
        # This change governs new publication; previously completed exports
        # retain their existing download policy.
        existing_export = await self.client.get(f"/api/audiobooks/{identifier}/export")
        self.assertEqual(existing_export.status_code, 200, existing_export.text)
        regenerated = await self.client.post(f"/api/audiobooks/{identifier}/chapters/1/regenerate")
        self.assertEqual(regenerated.status_code, 200, regenerated.text)
        self.assertEqual(regenerated.json()["status"], "failed")
        refused = await self.client.get(f"/api/audiobooks/{identifier}/export")
        self.assertEqual(refused.status_code, 404)
        retained = await self.client.get(f"/api/audiobooks/{identifier}/chapters/0/audio")
        self.assertEqual(retained.status_code, 200, retained.text)
        self.assertEqual(retained.content, original_chapter.content)
        # Replacing the retained actor chapter removes that actor's actual
        # audio provenance and permits the new narrator-only publication.
        replaced = await self.client.post(f"/api/audiobooks/{identifier}/chapters/0/regenerate")
        self.assertEqual(replaced.status_code, 200, replaced.text)
        self.assertEqual(replaced.json()["status"], "done")

    async def test_mp3_and_m4b_exports_keep_wav_and_fail_clearly_without_a_codec(self) -> None:
        created = await self._create([
            {"title": "Dawn", "text": "First light."},
            {"title": "Dusk", "text": "Last light."},
        ], author="Riley")
        book_id = str(created["book"]["id"])
        self.assertTrue(created["book"]["mp3_ready"])
        self.assertTrue(created["book"]["m4b_ready"])
        wav = await self.client.get(f"/api/audiobooks/{book_id}/export")
        mp3 = await self.client.get(f"/api/audiobooks/{book_id}/exports/mp3")
        m4b = await self.client.get(f"/api/audiobooks/{book_id}/exports/m4b")
        self.assertEqual(wav.status_code, 200)
        self.assertTrue(wav.content.startswith(b"RIFF"))
        self.assertEqual(mp3.status_code, 200)
        self.assertEqual(m4b.status_code, 200)
        self.assertGreater(len(mp3.content), 100)
        probed = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format_tags=title,artist", "-show_chapters", "-of", "json",
            str(audiobooks.export_format_path(book_id, "m4b")),
        ], text=True))
        self.assertEqual(probed["format"]["tags"]["title"], "Options")
        self.assertEqual(probed["format"]["tags"]["artist"], "Riley")
        self.assertEqual([chapter["tags"]["title"] for chapter in probed["chapters"]], ["Dawn", "Dusk"])
        cover = await self.client.post(f"/api/audiobooks/{book_id}/cover", files={"file": ("cover.png", _png(), "image/png")})
        self.assertEqual(cover.status_code, 200, cover.text)
        self.assertTrue(cover.json()["has_cover"])
        streams = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json",
            str(audiobooks.export_format_path(book_id, "m4b")),
        ], text=True))
        self.assertIn("video", {item["codec_type"] for item in streams["streams"]})
        with patch("app.audiobook_publish.available_encoders", return_value=set()):
            missing = await self._create([{"title": "Only", "text": "Wav only."}])
        missing_id = str(missing["book"]["id"])
        self.assertTrue(missing["book"]["export_path"])
        self.assertFalse(missing["book"]["mp3_ready"])
        self.assertIn("mp3:export_codec_missing", str(missing["book"]["export_note"]))
        self.assertIn("m4b:export_codec_missing", str(missing["book"]["export_note"]))
        denied = await self.client.get(f"/api/audiobooks/{missing_id}/exports/mp3")
        self.assertEqual(denied.status_code, 503)
        self.assertEqual(denied.json()["detail"], "export_codec_missing")
        kept = await self.client.get(f"/api/audiobooks/{missing_id}/export")
        self.assertEqual(kept.status_code, 200)
        self.assertTrue(kept.content.startswith(b"RIFF"))
