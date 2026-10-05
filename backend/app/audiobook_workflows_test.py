"""Render provenance and reviewed repair regressions with synthetic PCM only."""
from __future__ import annotations

import asyncio
import tempfile
import threading
import shutil
import subprocess
import sqlite3
import sys
import hashlib
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app import audiobook_narration, audiobook_workflows, audiobooks, speech_clone, voice_profiles
from app.speech_references import SpeechRenderSnapshot
from app.audiobook_contracts import (AudiobookChapterInput, CreateAudiobookRequest, CastMember, PronunciationEntry,
    CreateAudiobookAuditionRequest, CreateAudiobookRepairRequest, AcceptAudiobookRepairRequest)


def pcm(path: Path, value: int = 1, frames: int = 800, rate: int = 8000) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        handle.writeframes(value.to_bytes(2, "little", signed=True) * frames)


class _WorkflowFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.format_patch = patch("app.audiobook_publish.publish_formats", return_value=(None, None, ""))
        for item in (
            patch.object(audiobook_workflows, "_STOPPING", False),
            patch.object(audiobooks, "BOOKS_ROOT", self.root / "books"),
            patch.object(voice_profiles, "PROFILES_ROOT", self.root / "profiles"),
            patch.dict("os.environ", {"OPENFABRIC_AUDIOBOOK_SYNC": "1", "OPENFABRIC_SPEECH_CLONE_MOCK": "1"}),
            self.format_patch,
        ):
            item.start()
            self.addCleanup(item.stop)
        reference = self.root / "reference.wav"
        pcm(reference)
        self.profile = voice_profiles.create_profile(name="Reader", consent_confirmed=True,
            audio_bytes=reference.read_bytes(), filename="ref.wav", notes="Reference words.", reference_transcript="Reference words.")

    def create(self, language: str = "en", text: str = "One sentence.") -> str:
        return audiobooks.create_book(CreateAudiobookRequest(title="Book", profile_id=self.profile.id,
            language=language, chapters=[AudiobookChapterInput(text=text)])).book.id



class RenderIdentityTests(_WorkflowFixture):
    def test_legacy_completed_section_path_is_retained_without_resynthesis(self) -> None:
        with patch.object(audiobooks, "_schedule_book"):
            identifier = self.create()
        job = audiobooks.list_jobs(book_id=identifier)[0]
        directory = audiobooks.chapters_dir(identifier) / "sections"
        directory.mkdir()
        original = directory / f"{job.id}-0000.wav"
        pcm(original, rate=16000, frames=4000)
        with audiobooks._connect() as connection:
            connection.execute("""INSERT INTO audiobook_sections(job_id,section_index,section_text,text_sha256,status,output_path,profile_id,passage_id)
                VALUES(?,0,?,?, 'done',?,?,?)""", (job.id, "One sentence.", hashlib.sha256(b"One sentence.").hexdigest(), str(original), self.profile.id, "a" * 32))
            connection.commit()
        with patch.object(speech_clone, "synthesize_to_path", side_effect=AssertionError("completed PCM must survive migration")):
            audiobook_narration.run_sync(identifier)
        self.assertEqual(audiobooks.get_book(identifier).status, "done")
        self.assertEqual(audiobook_workflows.passage_audio_path(identifier, "a" * 32), original)

    def test_language_reaches_speech_boundary(self) -> None:
        with patch.object(speech_clone, "synthesize_to_path", wraps=speech_clone.synthesize_to_path) as synth:
            identifier = self.create("ja")
        self.assertEqual(audiobooks.get_book(identifier).status, "done")
        self.assertEqual(synth.call_args.kwargs.get("text_language"), "ja")

    def test_explicit_redo_synthesizes_fresh_even_when_text_is_unchanged(self) -> None:
        with patch.object(speech_clone, "synthesize_to_path", wraps=speech_clone.synthesize_to_path) as synth:
            identifier = self.create()
            audiobooks.regenerate_chapter(identifier, 0)
        self.assertEqual(synth.call_count, 2)

    def test_reference_change_invalidates_cross_book_cache(self) -> None:
        with patch.object(speech_clone, "synthesize_to_path", wraps=speech_clone.synthesize_to_path) as synth:
            self.create()
            pcm(Path(self.profile.reference_audio_path), value=2)
            self.create()
        self.assertEqual(synth.call_count, 2)

    def test_unverified_checkpoint_never_uses_cross_book_cache(self) -> None:
        with patch.object(speech_clone, "known_engine_identity", return_value=None, create=True), \
             patch.object(speech_clone, "synthesize_to_path", wraps=speech_clone.synthesize_to_path) as synth:
            self.create()
            self.create()
        self.assertEqual(synth.call_count, 2)

    def test_running_job_freezes_reference_and_transcript_across_sections(self) -> None:
        captured: list[tuple[str, str]] = []

        def synthesize(**kwargs: object) -> speech_clone.SynthesisOutcome:
            target = kwargs["output_path"]
            self.assertIsInstance(target, Path)
            snapshot = kwargs.get("snapshot")
            self.assertIsNotNone(snapshot, "narration must capture acoustic inputs before first section")
            assert isinstance(target, Path)
            assert isinstance(snapshot, SpeechRenderSnapshot)
            captured.append((snapshot.reference_sha256, snapshot.prompt_text))
            pcm(target)
            pcm(Path(self.profile.reference_audio_path), value=3)
            from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest
            voice_profiles.patch_profile(self.profile.id, PatchSpeechVoiceProfileRequest(notes="Changed later."))
            return speech_clone.SynthesisOutcome(status="completed", detail="", output_path=target)

        with patch.object(speech_clone, "synthesize_to_path", side_effect=synthesize):
            identifier = self.create(text="x" * 1200 + "y" * 1200)
        self.assertEqual(audiobooks.get_book(identifier).status, "done")
        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[0], captured[1])

    def test_unsupported_language_fails_before_a_book_is_created(self) -> None:
        with self.assertRaises(voice_profiles.VoiceProfileError) as caught:
            self.create("es")
        self.assertEqual(caught.exception.code, "speech_language_unsupported")
        self.assertEqual(audiobooks.list_books(), [])

    def test_region_language_records_actual_engine_mode(self) -> None:
        identifier = self.create("en-GB")
        job = audiobooks.list_jobs(book_id=identifier)[0]
        self.assertEqual(job.render_language, "en")
        self.assertTrue(job.language_ready)

    def test_render_snapshot_supplies_explicit_engine_settings(self) -> None:
        from app.speech_references import capture
        received: list[dict[str, str | int | float]] = []
        reference = Path(self.profile.reference_audio_path)

        async def request(url: str, payload: dict[str, str | int | float]) -> bytearray:
            received.append(payload)
            return bytearray(reference.read_bytes())

        with patch.object(speech_clone, "mock_enabled", return_value=False), \
             patch.object(speech_clone, "resolve_engine_root", return_value=self.root), \
             patch.object(speech_clone, "api_reachable", return_value=True), \
             patch.object(speech_clone, "record_capability_success"), \
             patch.object(speech_clone, "_request_speech_audio", side_effect=request):
            snapshot = capture(self.profile.id, "en", audiobooks.books_root() / "inputs", None)
            speech_clone.synthesize_to_path(profile_id=self.profile.id, text="Line.", output_path=self.root / "out.wav", snapshot=snapshot)
        self.assertEqual(received[0].get("speed"), 1.0)
        self.assertEqual(received[0].get("top_k"), 20)
        self.assertEqual(received[0].get("temperature"), 0.6)

    def test_setup_cannot_admit_audition_during_reference_capture(self) -> None:
        workflow = audiobook_workflows
        body = CreateAudiobookAuditionRequest(title="Draft", profile_id=self.profile.id,
            chapters=[AudiobookChapterInput(text="Line.")])
        with patch("app.module_jobs.work_busy", return_value=True):
            with self.assertRaises(audiobooks.AudiobookError) as caught:
                workflow.start_audition(body)
        self.assertEqual(caught.exception.code, "setup_busy")

    def test_missing_reference_transcript_is_an_explicit_error(self) -> None:
        from app.speech_references import capture
        from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest
        voice_profiles.patch_profile(self.profile.id, PatchSpeechVoiceProfileRequest(reference_transcript=""))
        with self.assertRaises(voice_profiles.VoiceProfileError) as caught:
            capture(self.profile.id, "en", audiobooks.books_root() / "inputs", None)
        self.assertEqual(caught.exception.code, "reference_transcript_required")


class ReviewedWorkflowTests(_WorkflowFixture):

    def test_cast_audition_uses_actual_spoken_lines_and_does_not_create_a_book(self) -> None:
        workflow = audiobook_workflows
        actor = voice_profiles.create_profile(name="Actor", consent_confirmed=True,
            audio_bytes=Path(self.profile.reference_audio_path).read_bytes(), filename="ref.wav", reference_transcript="Reference.")
        body = CreateAudiobookAuditionRequest(title="Draft", profile_id=self.profile.id, language="ja",
            chapters=[AudiobookChapterInput(text="Narrator line.\nAlice: Dr. Jones arrives.\nAlice: More.")],
            cast=[CastMember(name="Alice", profile_id=actor.id)],
            pronunciations=[PronunciationEntry(written="Dr.", spoken="Doctor")])
        with patch.object(speech_clone, "synthesize_to_path", wraps=speech_clone.synthesize_to_path) as synth:
            result = workflow.start_audition(body)
        completed = workflow.get_audition(result.id)
        self.assertEqual(completed.status, "done")
        self.assertEqual([clip.speaker for clip in completed.clips], ["Narrator", "Alice"])
        self.assertIn("Doctor Jones", completed.clips[1].text)
        self.assertNotIn("Alice:", completed.clips[1].text)
        self.assertEqual(synth.call_count, 2)
        self.assertTrue(all(clip.mock for clip in completed.clips))
        self.assertEqual(audiobooks.list_books(), [])

    def test_cast_audition_finds_an_actor_who_enters_in_a_later_chapter(self) -> None:
        workflow = audiobook_workflows
        actor = voice_profiles.create_profile(name="Actor", consent_confirmed=True,
            audio_bytes=Path(self.profile.reference_audio_path).read_bytes(), filename="ref.wav", reference_transcript="Reference.")
        body = CreateAudiobookAuditionRequest(title="Draft", profile_id=self.profile.id,
            chapters=[AudiobookChapterInput(text="Narrator first."), AudiobookChapterInput(text="Alice: Arriving later.")],
            cast=[CastMember(name="Alice", profile_id=actor.id)])
        result = workflow.start_audition(body)
        self.assertEqual([clip.speaker for clip in result.clips], ["Narrator", "Alice"])

    def test_unspoken_cast_member_is_explicitly_skipped(self) -> None:
        actor = voice_profiles.create_profile(name="Actor", consent_confirmed=True,
            audio_bytes=Path(self.profile.reference_audio_path).read_bytes(), filename="ref.wav", reference_transcript="Reference.")
        result = audiobook_workflows.start_audition(CreateAudiobookAuditionRequest(title="Draft", profile_id=self.profile.id,
            chapters=[AudiobookChapterInput(text="Narrator only.")], cast=[CastMember(name="Alice", profile_id=actor.id)]))
        self.assertEqual(getattr(result, "skipped_speakers", []), ["Alice"])

    def test_passages_have_stable_ids_audio_and_offsets(self) -> None:
        workflow = audiobook_workflows
        identifier = self.create(text="x" * 1200 + "y" * 1200)
        before = workflow.get_passages(identifier, 0)
        after = workflow.get_passages(identifier, 0)
        self.assertEqual(before, after)
        self.assertEqual(len(before.passages), 2)
        self.assertEqual(before.passages[0].end_ms, before.passages[1].start_ms)
        self.assertTrue(workflow.passage_audio_path(identifier, before.passages[0].id).is_file())
        self.assertEqual(before.passages[0].language, "en")

    def test_repair_is_fresh_and_accept_preserves_untouched_pcm_and_old_export(self) -> None:
        workflow = audiobook_workflows
        identifier = self.create(text="x" * 1200 + "y" * 1200)
        before = workflow.get_passages(identifier, 0)
        original_export = audiobooks.export_path_for(identifier)
        original_bytes = original_export.read_bytes()
        untouched = workflow.passage_audio_path(identifier, before.passages[1].id)
        untouched_bytes = untouched.read_bytes()

        def fresh(**kwargs: object) -> speech_clone.SynthesisOutcome:
            target = kwargs["output_path"]
            assert isinstance(target, Path)
            pcm(target, value=19, frames=8000, rate=16000)
            return speech_clone.SynthesisOutcome(status="completed", detail="", output_path=target)

        with patch.object(speech_clone, "synthesize_to_path", side_effect=fresh) as synth:
            repair = workflow.start_repair(identifier, 0, before.passages[0].id,
                CreateAudiobookRepairRequest(revision=before.revision))
        self.assertEqual(synth.call_count, 1)
        self.assertEqual(audiobooks.export_path_for(identifier).read_bytes(), original_bytes)
        ready = workflow.get_repair(repair.id)
        self.assertEqual(ready.status, "ready")
        accepted = workflow.accept_repair(repair.id, AcceptAudiobookRepairRequest(revision=before.revision))
        self.assertEqual(accepted.revision, before.revision + 1)
        self.assertEqual(accepted.passages[0].id, before.passages[0].id)
        self.assertEqual(workflow.passage_audio_path(identifier, before.passages[1].id), untouched)
        self.assertEqual(untouched.read_bytes(), untouched_bytes)
        self.assertEqual(original_export.read_bytes(), original_bytes)
        self.assertNotEqual(audiobooks.export_path_for(identifier), original_export)
        self.assertGreater(accepted.passages[0].end_ms, before.passages[0].end_ms)
        self.assertEqual(workflow.get_repair(repair.id).status, "accepted")

    def test_older_candidate_cannot_overwrite_accepted_repair(self) -> None:
        workflow = audiobook_workflows
        identifier = self.create()
        version = workflow.get_passages(identifier, 0)
        first = workflow.start_repair(identifier, 0, version.passages[0].id, CreateAudiobookRepairRequest(revision=version.revision))
        second = workflow.start_repair(identifier, 0, version.passages[0].id, CreateAudiobookRepairRequest(revision=version.revision))
        workflow.accept_repair(first.id, AcceptAudiobookRepairRequest(revision=version.revision))
        with self.assertRaises(audiobooks.AudiobookError) as caught:
            workflow.accept_repair(second.id, AcceptAudiobookRepairRequest(revision=version.revision))
        self.assertEqual(caught.exception.code, "passage_changed")

    def test_failed_recomposition_and_revoked_consent_leave_accepted_audio(self) -> None:
        workflow = audiobook_workflows
        identifier = self.create()
        version = workflow.get_passages(identifier, 0)
        original = audiobooks.export_path_for(identifier)
        repair = workflow.start_repair(identifier, 0, version.passages[0].id, CreateAudiobookRepairRequest(revision=version.revision, text="Corrected sentence."))
        with patch.object(audiobook_narration, "concat_wavs", side_effect=OSError("disk failed")):
            with self.assertRaises(audiobooks.AudiobookError):
                workflow.accept_repair(repair.id, AcceptAudiobookRepairRequest(revision=version.revision))
        self.assertEqual(audiobooks.export_path_for(identifier), original)
        self.assertEqual(workflow.get_passages(identifier, 0).revision, version.revision)
        from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest
        voice_profiles.patch_profile(self.profile.id, PatchSpeechVoiceProfileRequest(consent_confirmed=False))
        with self.assertRaises((audiobooks.AudiobookError, voice_profiles.VoiceProfileError)):
            workflow.accept_repair(repair.id, AcceptAudiobookRepairRequest(revision=version.revision))
        self.assertEqual(audiobooks.export_path_for(identifier), original)

    def test_settings_edit_invalidates_a_ready_candidate(self) -> None:
        workflow = audiobook_workflows
        identifier = self.create()
        version = workflow.get_passages(identifier, 0)
        repair = workflow.start_repair(identifier, 0, version.passages[0].id, CreateAudiobookRepairRequest(revision=version.revision))
        audiobooks.set_languages(identifier, "ja", [])
        with self.assertRaises(audiobooks.AudiobookError) as caught:
            workflow.accept_repair(repair.id, AcceptAudiobookRepairRequest(revision=version.revision))
        self.assertEqual(caught.exception.code, "passage_changed")

    def test_accepted_spoken_text_survives_explicit_redo(self) -> None:
        workflow = audiobook_workflows
        identifier = self.create()
        version = workflow.get_passages(identifier, 0)
        repair = workflow.start_repair(identifier, 0, version.passages[0].id,
            CreateAudiobookRepairRequest(revision=version.revision, text="Reviewed replacement."))
        workflow.accept_repair(repair.id, AcceptAudiobookRepairRequest(revision=version.revision))
        audiobooks.regenerate_chapter(identifier, 0)
        passage = workflow.get_passages(identifier, 0).passages[0]
        self.assertEqual(passage.text, "Reviewed replacement.")
        self.assertEqual(passage.id, version.passages[0].id)

    def test_revoked_preview_consent_masks_urls_without_mutating_readonly_polls(self) -> None:
        workflow = audiobook_workflows
        result = workflow.start_audition(CreateAudiobookAuditionRequest(title="Preview", profile_id=self.profile.id,
            chapters=[AudiobookChapterInput(text="Line.")], mode="scene"))
        before = workflow._load(result.id, "audition")
        from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest
        voice_profiles.patch_profile(self.profile.id, PatchSpeechVoiceProfileRequest(consent_confirmed=False))
        self.assertIsNone(workflow.get_audition(result.id).clips[0].audio_url)
        self.assertIsNone(workflow.get_audition(result.id).scene_audio_url)
        self.assertEqual(workflow._load(result.id, "audition"), before)
        with self.assertRaises((audiobooks.AudiobookError, voice_profiles.VoiceProfileError)):
            workflow.audition_audio_path(result.id, 0)

    def test_cancel_during_recomposition_cannot_commit_a_candidate(self) -> None:
        workflow = audiobook_workflows
        identifier = self.create()
        version = workflow.get_passages(identifier, 0)
        original = audiobooks.export_path_for(identifier)
        repair = workflow.start_repair(identifier, 0, version.passages[0].id, CreateAudiobookRepairRequest(revision=version.revision))
        join = audiobook_narration.concat_wavs

        def cancelling(book_id: str, paths: list[Path], target: Path, *, controlled: bool = True) -> Path:
            workflow.cancel(repair.id, "repair")
            return join(book_id, paths, target, controlled=controlled)

        with patch.object(audiobook_narration, "concat_wavs", side_effect=cancelling):
            with self.assertRaises(audiobooks.AudiobookError):
                workflow.accept_repair(repair.id, AcceptAudiobookRepairRequest(revision=version.revision))
        self.assertEqual(audiobooks.export_path_for(identifier), original)
        self.assertEqual(workflow.get_repair(repair.id).status, "cancelled")

    def test_cancellation_after_ready_publication_is_not_overwritten_by_worker_finally(self) -> None:
        workflow = audiobook_workflows
        identifier = self.create()
        version = workflow.get_passages(identifier, 0)
        save = workflow._save
        cancelled = False

        def cancel_ready(identifier: str, kind: str, state: workflow._AuditionState | workflow._RepairState) -> None:
            nonlocal cancelled
            save(identifier, kind, state)
            if kind == "repair" and state.public.status == "ready" and not cancelled:
                cancelled = True
                workflow.cancel(identifier, "repair")

        with patch.object(workflow, "_save", side_effect=cancel_ready):
            repair = workflow.start_repair(identifier, 0, version.passages[0].id, CreateAudiobookRepairRequest(revision=version.revision))
        self.assertEqual(workflow.get_repair(repair.id).status, "cancelled")

    def test_source_draft_snapshot_survives_later_review_edits(self) -> None:
        from app import ebook_import
        from app.audiobook_contracts import PatchEbookDraftRequest
        draft = ebook_import.import_pasted("Original", "Reviewed source text.")
        identifier = audiobooks.create_book(CreateAudiobookRequest(title=draft.title, profile_id=self.profile.id,
            chapters=[AudiobookChapterInput(text=draft.chapters[0].text)]), source_import_id=draft.id,
            source_import_revision=draft.revision).book.id
        ebook_import.patch_draft(draft.id, PatchEbookDraftRequest(title="Edited later", chapters=draft.chapters, revision=draft.revision))
        self.assertTrue(hasattr(audiobooks, "source_draft_snapshot"))
        saved = audiobooks.source_draft_snapshot(identifier)
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual(saved.title, "Original")
        self.assertEqual(saved.revision, draft.revision)


class WorkflowHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.fixture = _WorkflowFixture()
        self.fixture.setUp()
        from app.api import routes_audiobooks
        app = FastAPI()
        app.include_router(routes_audiobooks.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.fixture.doCleanups()

    async def test_sync_audition_calls_engine_outside_event_loop(self) -> None:
        original = speech_clone._synthesize_unlocked

        def synthesize(*, profile_id: str, text: str, output_path: Path, prompt_text: str | None,
                       prompt_language: str | None, text_language: str | None, require_consent: bool,
                       snapshot: SpeechRenderSnapshot | None = None) -> speech_clone.SynthesisOutcome:
            with self.assertRaises(RuntimeError):
                asyncio.get_running_loop()
            return original(profile_id=profile_id, text=text, output_path=output_path,
                prompt_text=prompt_text, prompt_language=prompt_language, text_language=text_language,
                require_consent=require_consent, snapshot=snapshot)

        with patch.object(speech_clone, "_synthesize_unlocked", side_effect=synthesize):
            response = await self.client.post("/api/audiobooks/auditions", json={"title": "Draft",
                "profile_id": self.fixture.profile.id, "chapters": [{"text": "Line."}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "done")

    async def test_interrupted_acceptance_cleans_only_its_journaled_stages(self) -> None:
        identifier = self.fixture.create()
        version = audiobook_workflows.get_passages(identifier, 0)
        repair = audiobook_workflows.start_repair(identifier, 0, version.passages[0].id,
            CreateAudiobookRepairRequest(revision=version.revision))
        state = audiobook_workflows._RepairState.model_validate_json(audiobook_workflows._load(repair.id, "repair"))
        self.assertTrue(hasattr(state, "pending_accept_token"), "acceptance staging ownership is not durable")
        token = "d" * 32
        state.pending_accept_token = token
        audiobook_workflows._save(repair.id, "repair", state)
        stage = audiobooks.book_dir(identifier) / f"export-{token}.wav"
        stage.write_bytes(b"interrupted partial")
        original = audiobooks.export_path_for(identifier)
        await audiobook_workflows.start()
        self.assertFalse(stage.exists())
        self.assertTrue(original.is_file())
        self.assertEqual(audiobook_workflows.get_repair(repair.id).status, "ready")


class WorkflowCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app import audiobook_workflows
        self.workflow = audiobook_workflows
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [patch.object(audiobooks, "BOOKS_ROOT", self.root / "books"),
            patch.object(voice_profiles, "PROFILES_ROOT", self.root / "profiles"),
            patch.dict("os.environ", {"OPENFABRIC_AUDIOBOOK_SYNC": "0", "OPENFABRIC_SPEECH_CLONE_MOCK": "1"})]
        for item in self.patches:
            item.start()
        reference = self.root / "ref.wav"
        pcm(reference)
        self.profile = voice_profiles.create_profile(name="Reader", consent_confirmed=True,
            audio_bytes=reference.read_bytes(), filename="ref.wav", reference_transcript="Reference.")
        await self.workflow.start()

    async def asyncTearDown(self) -> None:
        await self.workflow.shutdown()
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    async def test_cancel_and_shutdown_own_the_current_blocking_call(self) -> None:
        entered, release = threading.Event(), threading.Event()
        calls = 0

        def synthesize(**kwargs: object) -> speech_clone.SynthesisOutcome:
            nonlocal calls
            calls += 1
            entered.set()
            release.wait(3)
            target = kwargs["output_path"]
            assert isinstance(target, Path)
            speech_clone._write_silent_wav(target)
            return speech_clone.SynthesisOutcome(status="mock_completed", detail="", output_path=target)

        body = CreateAudiobookAuditionRequest(title="Scene", profile_id=self.profile.id, mode="scene",
            chapters=[AudiobookChapterInput(text="Narrator first.")])
        with patch.object(speech_clone, "synthesize_to_path", side_effect=synthesize):
            result = self.workflow.start_audition(body)
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            self.workflow.cancel(result.id, "audition")
            stopping = asyncio.create_task(self.workflow.shutdown())
            await asyncio.sleep(0.01)
            self.assertFalse(stopping.done())
            self.assertTrue(self.workflow.work_busy())
            release.set()
            await stopping
        self.assertEqual(calls, 1)
        self.assertEqual(self.workflow.get_audition(result.id).status, "cancelled")

        self.assertIsNone(self.workflow.get_audition(result.id).scene_audio_url)
        self.assertFalse(self.workflow.work_busy())

    async def test_recovery_marks_interrupted_work_without_restarting_the_model(self) -> None:
        with patch.object(self.workflow, "_schedule"):
            result = self.workflow.start_audition(CreateAudiobookAuditionRequest(title="Draft", profile_id=self.profile.id,
                chapters=[AudiobookChapterInput(text="Line.")]))
        with patch.object(speech_clone, "synthesize_to_path", side_effect=AssertionError("recovery must not synthesize")):
            await self.workflow.start()
        self.assertEqual(self.workflow.get_audition(result.id).status, "cancelled")

    async def test_unverified_codec_drain_keeps_admission_and_partial_bytes_owned(self) -> None:
        from app import audiobook_publish
        from app.job_lifecycle import kill_process_tree

        async def unverified(proc: asyncio.subprocess.Process | None) -> None:
            await kill_process_tree(proc)
            raise RuntimeError("injected verification failure")

        partial = audiobooks.books_root() / "owned-partial.wav"
        partial.write_bytes(b"partial")
        with patch.object(audiobook_publish, "kill_process_tree", side_effect=unverified), self.assertLogs("app.audiobook_publish", level="ERROR"):
            with self.assertRaises(audiobooks.AudiobookError) as caught:
                await audiobook_publish._run_owned([sys.executable, "-c", "print('ok')"], 5)
        self.assertEqual(caught.exception.code, "audiobook_export_cleanup_failed")
        audiobook_publish.defer_cleanup([partial])
        self.assertTrue(self.workflow.work_busy())
        self.assertTrue(partial.is_file())
        await audiobook_publish.shutdown()
        self.assertFalse(audiobook_publish.cleanup_pending())
        self.assertFalse(partial.exists())

    async def test_codec_start_recovers_valid_receipts_and_blocks_unknown_metadata(self) -> None:
        from app import audiobook_publish
        from app.video_process import WorkerReceipt
        root = audiobooks.books_root() / "_codec_workers"
        root.mkdir()
        owned = root / ("a" * 32 + ".json")
        owned.write_text(WorkerReceipt(pid=2147483647, token="b" * 32, returncode=0).model_dump_json())
        unknown = root / ("c" * 32 + ".json")
        unknown.write_text("{invalid metadata")
        self.assertTrue(hasattr(audiobook_publish, "start"), "durable codec recovery is missing")
        with patch.object(audiobook_publish, "_RECOVERY_BLOCKED", set()), \
             patch.object(audiobook_publish, "terminate_verified", return_value=True), \
             patch.object(audiobook_publish, "_group_exited", return_value=True):
            await audiobook_publish.start()
            self.assertFalse(owned.exists())
            self.assertTrue(unknown.exists())
            self.assertTrue(audiobook_publish.cleanup_pending())


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe unavailable")
class EncodedRepairTests(_WorkflowFixture):
    def setUp(self) -> None:
        super().setUp()
        self.format_patch.stop()

    def duration(self, path: Path) -> float:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10, check=True)
        return float(result.stdout.strip())

    def test_all_encoded_formats_contain_the_repaired_canonical_audio(self) -> None:
        identifier = self.create()
        version = audiobook_workflows.get_passages(identifier, 0)
        originals = {fmt: audiobooks.export_format_path(identifier, fmt) for fmt in ("wav", "mp3", "m4b")}
        original_bytes = {fmt: path.read_bytes() for fmt, path in originals.items()}

        def fresh(**kwargs: object) -> speech_clone.SynthesisOutcome:
            target = kwargs["output_path"]
            assert isinstance(target, Path)
            pcm(target, value=15000, frames=16000, rate=16000)
            return speech_clone.SynthesisOutcome(status="completed", detail="", output_path=target)

        with patch.object(speech_clone, "synthesize_to_path", side_effect=fresh):
            repair = audiobook_workflows.start_repair(identifier, 0, version.passages[0].id,
                CreateAudiobookRepairRequest(revision=version.revision))
        audiobook_workflows.accept_repair(repair.id, AcceptAudiobookRepairRequest(revision=version.revision))
        for fmt in originals:
            selected = audiobooks.export_format_path(identifier, fmt)
            self.assertGreater(self.duration(selected), 0.8, fmt)
            self.assertNotEqual(selected, originals[fmt])
            self.assertEqual(originals[fmt].read_bytes(), original_bytes[fmt])

    def test_revocation_during_encoding_preserves_every_original_representation(self) -> None:
        from app import audiobook_publish
        from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest
        identifier = self.create()
        version = audiobook_workflows.get_passages(identifier, 0)
        originals = {fmt: audiobooks.export_format_path(identifier, fmt) for fmt in ("wav", "mp3", "m4b")}
        contents = {fmt: path.read_bytes() for fmt, path in originals.items()}
        repair = audiobook_workflows.start_repair(identifier, 0, version.passages[0].id,
            CreateAudiobookRepairRequest(revision=version.revision))
        publish = audiobook_publish.publish_formats

        def revoke(book_id: str, *, title: str, author: str, chapters: list[tuple[str, Path]], cover: Path | None,
                   canonical_wav: Path | None = None, destination_stem: str = "export") -> tuple[Path | None, Path | None, str]:
            result = publish(book_id, title=title, author=author, chapters=chapters, cover=cover,
                canonical_wav=canonical_wav, destination_stem=destination_stem)
            voice_profiles.patch_profile(self.profile.id, PatchSpeechVoiceProfileRequest(consent_confirmed=False))
            return result

        with patch.object(audiobook_publish, "publish_formats", side_effect=revoke):
            with self.assertRaises(audiobooks.AudiobookError):
                audiobook_workflows.accept_repair(repair.id, AcceptAudiobookRepairRequest(revision=version.revision))
        for fmt in originals:
            self.assertEqual(audiobooks.export_format_path(identifier, fmt), originals[fmt])
            self.assertEqual(originals[fmt].read_bytes(), contents[fmt])

    def test_database_failure_after_encoding_keeps_all_original_bytes_and_removes_stages(self) -> None:
        identifier = self.create()
        version = audiobook_workflows.get_passages(identifier, 0)
        originals = {fmt: audiobooks.export_format_path(identifier, fmt) for fmt in ("wav", "mp3", "m4b")}
        contents = {fmt: path.read_bytes() for fmt, path in originals.items()}
        repair = audiobook_workflows.start_repair(identifier, 0, version.passages[0].id,
            CreateAudiobookRepairRequest(revision=version.revision))

        class FailedPublication(sqlite3.Connection):
            def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Cursor:
                if sql.startswith("UPDATE audiobook_books SET export_path=?"):
                    raise sqlite3.OperationalError("injected publication failure")
                return super().execute(sql, parameters)

        def connect() -> sqlite3.Connection:
            connection = sqlite3.connect(audiobooks._db_path(), factory=FailedPublication)
            connection.row_factory = sqlite3.Row
            return connection

        before = set(audiobooks.book_dir(identifier).rglob("*"))
        with patch.object(audiobooks, "_connect", side_effect=connect), self.assertLogs("app.audiobook_workflows", level="ERROR"):
            with self.assertRaises(audiobooks.AudiobookError) as caught:
                audiobook_workflows.accept_repair(repair.id, AcceptAudiobookRepairRequest(revision=version.revision))
        self.assertEqual(caught.exception.code, "audiobook_storage_unavailable")
        self.assertEqual(set(audiobooks.book_dir(identifier).rglob("*")), before)
        for fmt in originals:
            self.assertEqual(audiobooks.export_format_path(identifier, fmt), originals[fmt])
            self.assertEqual(originals[fmt].read_bytes(), contents[fmt])
