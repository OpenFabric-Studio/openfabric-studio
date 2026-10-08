"""Durability and section boundary control with a mocked speech boundary."""
from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.voice_profile_test_fixtures import wav_bytes
from app.speech_references import SpeechRenderSnapshot

from app import audiobook_narration, audiobooks, speech_clone, voice_profiles
from app.audiobook_contracts import AudiobookChapterInput, CreateAudiobookRequest


class NarrationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.patches = [patch.object(audiobooks, "BOOKS_ROOT", self.root / "books"),
                        patch.object(voice_profiles, "PROFILES_ROOT", self.root / "profiles"),
                        patch.dict("os.environ", {"OPENFABRIC_AUDIOBOOK_SYNC": "0", "OPENFABRIC_SPEECH_CLONE_MOCK": "1"})]
        for item in self.patches:
            item.start()
        self.profile = voice_profiles.create_profile(name="Reader", consent_confirmed=True, audio_bytes=wav_bytes(), filename="ref.wav", reference_transcript="Reference.", notes="Reference.")

    async def asyncTearDown(self) -> None:
        if hasattr(audiobooks, "shutdown"):
            await audiobooks.shutdown()
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def create(self) -> str:
        return audiobooks.create_book(CreateAudiobookRequest(title="Book", profile_id=self.profile.id,
                    chapters=[AudiobookChapterInput(title="One", text="Paragraph of narration. " * 150)])).book.id

    async def wait_for(self, identifier: str, status: str) -> None:
        async with asyncio.timeout(5):
            while audiobooks.get_book(identifier).status != status:
                await asyncio.sleep(0.01)

    async def test_sections_survive_pause_and_resume_without_resynthesis(self) -> None:
        self.assertTrue(hasattr(audiobooks, "pause_book"), "section boundary control is missing")
        await audiobooks.start()
        entered, release = threading.Event(), threading.Event()
        calls = 0
        original = speech_clone.synthesize_to_path

        def synthesize(*, profile_id: str, text: str, output_path: Path, require_consent: bool = True, text_language: str | None = None, snapshot: SpeechRenderSnapshot | None = None) -> speech_clone.SynthesisOutcome:
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                release.wait(5)
            return original(profile_id=profile_id, text=text, output_path=output_path, require_consent=require_consent, text_language=text_language, snapshot=snapshot)

        with patch.object(speech_clone, "synthesize_to_path", side_effect=synthesize):
            identifier = self.create()
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            paused = audiobooks.pause_book(identifier)
            self.assertEqual(paused.status, "paused")
            release.set()
            await audiobooks.wait_for_book(identifier)
            jobs = audiobooks.list_jobs(book_id=identifier)
            self.assertEqual(jobs[0].completed_sections, 1)
            self.assertGreater(jobs[0].total_sections, 1)
            await audiobooks.resume_book(identifier)
            await self.wait_for(identifier, "done")
            finished = audiobooks.list_jobs(book_id=identifier)[0]
            with audiobooks._LOCK, audiobooks._connect() as connection:
                rows = connection.execute("SELECT section_text FROM audiobook_sections WHERE job_id = ?", (finished.id,)).fetchall()
            # Identical spoken sections reuse the durable cache, so calls match unique text, not repeats.
            self.assertEqual(calls, len({row[0] for row in rows}))
            self.assertLessEqual(calls, finished.total_sections)
            self.assertTrue(audiobooks.export_path_for(identifier).is_file())

    async def test_cancel_retains_completed_sections_and_recovery_pauses_interrupted_jobs(self) -> None:
        self.assertTrue(hasattr(audiobooks, "cancel_book"), "cancel and recovery are missing")
        await audiobooks.start()
        identifier = self.create()
        audiobooks.cancel_book(identifier)
        await audiobooks.wait_for_book(identifier)
        self.assertEqual(audiobooks.get_book(identifier).status, "cancelled")
        with audiobooks._LOCK, audiobooks._connect() as connection:
            connection.execute("UPDATE audiobook_books SET status = 'running' WHERE id = ?", (identifier,))
            connection.execute("UPDATE audiobook_jobs SET status = 'running' WHERE book_id = ?", (identifier,))
        await audiobooks.start()
        self.assertEqual(audiobooks.get_book(identifier).status, "paused")
        self.assertEqual(audiobooks.list_jobs(book_id=identifier)[0].detail, "narration_interrupted")

    async def test_export_failure_can_retry_without_resynthesizing_done_sections(self) -> None:
        await audiobooks.start()
        identifier = self.create()
        await self.wait_for(identifier, "done")
        await audiobooks.wait_for_book(identifier)
        with audiobooks._LOCK, audiobooks._connect() as connection:
            connection.execute("UPDATE audiobook_books SET status = 'failed', export_path = NULL WHERE id = ?", (identifier,))
        with patch.object(speech_clone, "synthesize_to_path", side_effect=AssertionError("completed audio must be reused")):
            audiobooks.retry_failed(identifier)
            await self.wait_for(identifier, "done")
            await audiobooks.wait_for_book(identifier)
        self.assertTrue(audiobooks.export_path_for(identifier).is_file())

    async def test_setup_busy_includes_a_persisted_queued_book_without_a_worker(self) -> None:
        self.assertTrue(hasattr(audiobook_narration, "work_busy"), "queued narration does not block setup")
        await audiobooks.start()
        with patch.object(audiobooks, "_schedule_book"):
            identifier = self.create()
        self.assertTrue(audiobook_narration.work_busy())
        audiobooks.cancel_book(identifier)
        self.assertFalse(audiobook_narration.work_busy())

    async def test_shared_speech_engine_serializes_two_books(self) -> None:
        self.assertTrue(hasattr(speech_clone, "SYNTHESIS_LOCK"), "shared speech serialization is missing")
        await audiobooks.start()
        active = 0
        peak = 0
        lock = threading.Lock()
        original = speech_clone._synthesize_unlocked

        def synthesize(*, profile_id: str, text: str, output_path: Path, prompt_text: str | None,
                       prompt_language: str | None, text_language: str | None, require_consent: bool, snapshot: SpeechRenderSnapshot | None = None) -> speech_clone.SynthesisOutcome:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                time.sleep(0.005)
                return original(profile_id=profile_id, text=text, output_path=output_path, prompt_text=prompt_text,
                                prompt_language=prompt_language, text_language=text_language, require_consent=require_consent, snapshot=snapshot)
            finally:
                with lock:
                    active -= 1

        with patch.object(speech_clone, "_synthesize_unlocked", side_effect=synthesize):
            first, second = self.create(), self.create()
            trial = asyncio.create_task(asyncio.to_thread(speech_clone.synthesize_to_path, profile_id=self.profile.id,
                                                          text="Trial", output_path=self.root / "trial.wav"))
            await audiobooks.wait_for_book(first)
            await audiobooks.wait_for_book(second)
            await trial
        self.assertEqual(audiobooks.get_book(first).status, "done")
        self.assertEqual(audiobooks.get_book(second).status, "done")
        self.assertEqual(peak, 1)

    async def test_control_between_admission_and_running_is_never_overwritten(self) -> None:
        await audiobooks.start()
        with patch.object(audiobooks, "_schedule_book"):
            identifier = self.create()
        original = audiobook_narration._active
        first = True

        def active(book_id: str) -> bool:
            nonlocal first
            admitted = original(book_id)
            if first:
                first = False
                audiobooks.pause_book(book_id)
            return admitted

        with patch.object(audiobook_narration, "_active", side_effect=active):
            await asyncio.to_thread(audiobook_narration.run_sync, identifier)
        self.assertEqual(audiobooks.get_book(identifier).status, "paused")
        self.assertEqual(audiobooks.list_jobs(book_id=identifier)[0].completed_sections, 0)

    async def test_resume_rejects_changed_section_identity(self) -> None:
        await audiobooks.start()
        with patch.object(audiobooks, "_schedule_book"):
            identifier = self.create()
        job = audiobooks.list_jobs(book_id=identifier)[0]
        with audiobooks._LOCK, audiobooks._connect() as connection:
            connection.execute("INSERT INTO audiobook_sections (job_id, section_index, section_text, text_sha256, status, output_path) VALUES (?, 0, 'changed', ?, 'done', NULL)", (job.id, '0' * 64))
        await asyncio.to_thread(audiobook_narration.run_sync, identifier)
        self.assertEqual(audiobooks.get_book(identifier).status, "failed")
        self.assertEqual(audiobooks.list_jobs(book_id=identifier)[0].detail, "narration_sections_changed")

    async def test_cancel_and_shutdown_wait_for_the_current_section_and_preserve_its_audio(self) -> None:
        await audiobooks.start()
        entered, release = threading.Event(), threading.Event()
        original = speech_clone.synthesize_to_path

        def synthesize(*, profile_id: str, text: str, output_path: Path, require_consent: bool = True, text_language: str | None = None, snapshot: SpeechRenderSnapshot | None = None) -> speech_clone.SynthesisOutcome:
            entered.set()
            release.wait(5)
            return original(profile_id=profile_id, text=text, output_path=output_path, require_consent=require_consent, text_language=text_language, snapshot=snapshot)

        with patch.object(speech_clone, "synthesize_to_path", side_effect=synthesize):
            identifier = self.create()
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            audiobooks.cancel_book(identifier)
            draining = asyncio.create_task(audiobooks.shutdown())
            await asyncio.sleep(0.02)
            self.assertFalse(draining.done())
            release.set()
            await draining
        self.assertEqual(audiobooks.get_book(identifier).status, "cancelled")
        job = audiobooks.list_jobs(book_id=identifier)[0]
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(job.completed_sections, 1)
        self.assertTrue(any(audiobooks.chapters_dir(identifier).glob("sections/*.wav")))

    async def test_consent_revocation_prevents_the_next_section(self) -> None:
        await audiobooks.start()
        entered, release = threading.Event(), threading.Event()
        original = speech_clone.synthesize_to_path
        calls = 0

        def synthesize(*, profile_id: str, text: str, output_path: Path, require_consent: bool = True, text_language: str | None = None, snapshot: SpeechRenderSnapshot | None = None) -> speech_clone.SynthesisOutcome:
            nonlocal calls
            calls += 1
            if calls == 1:
                outcome = original(profile_id=profile_id, text=text, output_path=output_path, require_consent=require_consent, text_language=text_language, snapshot=snapshot)
                entered.set()
                release.wait(5)
                return outcome
            return original(profile_id=profile_id, text=text, output_path=output_path, require_consent=require_consent, text_language=text_language, snapshot=snapshot)

        with patch.object(speech_clone, "synthesize_to_path", side_effect=synthesize):
            identifier = self.create()
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest
            voice_profiles.patch_profile(self.profile.id, PatchSpeechVoiceProfileRequest(consent_confirmed=False))
            release.set()
            await audiobooks.wait_for_book(identifier)
        self.assertEqual(audiobooks.get_book(identifier).status, "failed")
        self.assertEqual(audiobooks.list_jobs(book_id=identifier)[0].detail, "consent_required")

    async def test_legacy_database_upgrade_keeps_existing_jobs_and_export(self) -> None:
        root = audiobooks.books_root()
        import sqlite3
        with sqlite3.connect(root / "audiobooks.db") as connection:
            connection.execute("CREATE TABLE audiobook_books (id TEXT PRIMARY KEY,title TEXT,profile_id TEXT,chapter_count INTEGER,status TEXT,export_path TEXT,created_at TEXT,updated_at TEXT)")
            connection.execute("CREATE TABLE audiobook_jobs (id TEXT PRIMARY KEY,book_id TEXT,chapter_index INTEGER,chapter_title TEXT,chapter_text TEXT,status TEXT,detail TEXT,output_path TEXT,created_at TEXT,updated_at TEXT)")
            connection.execute("INSERT INTO audiobook_books VALUES (?, 'Legacy', ?, 1, 'done', 'export.wav', 'now', 'now')", ('a' * 32, self.profile.id))
            connection.execute("INSERT INTO audiobook_jobs VALUES (?, ?, 0, 'Old', 'Original text', 'done', '', 'chapter.wav', 'now', 'now')", ('b' * 32, 'a' * 32))
        await audiobooks.start()
        book = audiobooks.get_book('a' * 32)
        self.assertEqual(book.status, "done")
        self.assertEqual(book.export_path, "export.wav")
        self.assertIsNone(book.source_import_id)
        self.assertEqual(audiobooks.list_jobs(book_id=book.id)[0].status, "done")

    async def test_cached_repeated_section_stops_after_consent_revocation(self) -> None:
        await audiobooks.start()
        original = speech_clone.synthesize_to_path

        def synthesize(*, profile_id: str, text: str, output_path: Path, require_consent: bool = True, text_language: str | None = None, snapshot: SpeechRenderSnapshot | None = None) -> speech_clone.SynthesisOutcome:
            outcome = original(profile_id=profile_id, text=text, output_path=output_path, require_consent=require_consent, text_language=text_language, snapshot=snapshot)
            from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest
            voice_profiles.patch_profile(profile_id, PatchSpeechVoiceProfileRequest(consent_confirmed=False))
            return outcome

        with patch.object(speech_clone, "synthesize_to_path", side_effect=synthesize):
            identifier = audiobooks.create_book(CreateAudiobookRequest(
                title="Repeated", profile_id=self.profile.id,
                chapters=[AudiobookChapterInput(text="x" * 2400)],
            )).book.id
            await audiobooks.wait_for_book(identifier)
        self.assertEqual(audiobooks.get_book(identifier).status, "failed")
        job = audiobooks.list_jobs(book_id=identifier)[0]
        self.assertEqual(job.detail, "consent_required")
        self.assertEqual(job.completed_sections, 1)

    async def test_revocation_during_last_section_keeps_pcm_without_publishing_chapter(self) -> None:
        await audiobooks.start()
        original = speech_clone.synthesize_to_path
        def synthesize(*, profile_id: str, text: str, output_path: Path, require_consent: bool = True, text_language: str | None = None, snapshot: SpeechRenderSnapshot | None = None) -> speech_clone.SynthesisOutcome:
            result = original(profile_id=profile_id, text=text, output_path=output_path, require_consent=require_consent, text_language=text_language, snapshot=snapshot)
            from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest
            voice_profiles.patch_profile(profile_id, PatchSpeechVoiceProfileRequest(consent_confirmed=False))
            return result
        with patch.object(speech_clone, "synthesize_to_path", side_effect=synthesize):
            identifier = audiobooks.create_book(CreateAudiobookRequest(title="Last", profile_id=self.profile.id,
                chapters=[AudiobookChapterInput(text="One final section.")])).book.id
            await audiobooks.wait_for_book(identifier)
        self.assertEqual(audiobooks.get_book(identifier).status, "failed")
        job = audiobooks.list_jobs(book_id=identifier)[0]
        self.assertEqual(job.completed_sections, 1)
        self.assertEqual(job.detail, "consent_required")
        with self.assertRaises(audiobooks.AudiobookError):
            audiobooks.chapter_audio_path(identifier, 0)

    async def test_revocation_after_last_chapter_prevents_book_publication(self) -> None:
        await audiobooks.start()
        original = audiobook_narration._process_chapter
        def chapter(identifier: str, profile_id: str, item: audiobook_narration._Chapter) -> None:
            original(identifier, profile_id, item)
            from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest
            voice_profiles.patch_profile(profile_id, PatchSpeechVoiceProfileRequest(consent_confirmed=False))
        with patch.object(audiobook_narration, "_process_chapter", side_effect=chapter):
            identifier = audiobooks.create_book(CreateAudiobookRequest(title="Last", profile_id=self.profile.id,
                chapters=[AudiobookChapterInput(text="One final section.")])).book.id
            await audiobooks.wait_for_book(identifier)
        self.assertEqual(audiobooks.get_book(identifier).status, "failed")
        with self.assertRaises(audiobooks.AudiobookError):
            audiobooks.export_path_for(identifier)

    async def test_revocation_during_encoding_prevents_export_pointer_publication(self) -> None:
        await audiobooks.start()
        def publish(book_id: str, *, title: str, author: str, chapters: list[tuple[str, Path]], cover: Path | None) -> tuple[Path | None, Path | None, str]:
            from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest
            voice_profiles.patch_profile(self.profile.id, PatchSpeechVoiceProfileRequest(consent_confirmed=False))
            return None, None, ""
        with patch("app.audiobook_publish.publish_formats", side_effect=publish):
            identifier = audiobooks.create_book(CreateAudiobookRequest(title="Last", profile_id=self.profile.id,
                chapters=[AudiobookChapterInput(text="One final section.")])).book.id
            await audiobooks.wait_for_book(identifier)
        self.assertEqual(audiobooks.get_book(identifier).status, "failed")
        self.assertIsNone(audiobooks.get_book(identifier).export_path)
