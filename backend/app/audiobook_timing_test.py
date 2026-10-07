"""Pacing preserves accepted dry takes; displayed words and forecasts stay honest."""
from __future__ import annotations

import hashlib
import asyncio
from concurrent.futures import ThreadPoolExecutor
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from app import audiobooks, audiobook_narration, audiobook_workflows, speech_clone
from app import audiobook_workflows_test as fixtures
from app.audiobook_contracts import CreateAudiobookRequest, AudiobookChapterInput, CastMember, PronunciationEntry


class PacingTests(fixtures._WorkflowFixture):
    def dialogue(self) -> str:
        other = fixtures.voice_profiles.create_profile(name="Other", consent_confirmed=True,
            audio_bytes=Path(self.profile.reference_audio_path).read_bytes(), filename="ref.wav", reference_transcript="Other reference.")
        return audiobooks.create_book(CreateAudiobookRequest(title="Dialog", profile_id=self.profile.id,
            language="en", cast=[CastMember(name="Sam", profile_id=other.id)],
            chapters=[AudiobookChapterInput(text="Narration.\nSam: Reply.\nNarration again.")])).book.id

    def test_pacing_reassembles_without_synthesis_and_rejects_old_revision(self) -> None:
        self.assertTrue(hasattr(audiobooks, "set_pacing"), "Saved narration pacing is missing")
        from app.audiobook_contracts import SetAudiobookPacingRequest, AudiobookPacingChapter
        identifier = self.dialogue()
        job = audiobooks.list_jobs(book_id=identifier)[0]
        previous = audiobook_workflows.get_passages(identifier, 0)
        paths = [audiobook_workflows.passage_audio_path(identifier, p.id, previous.revision) for p in previous.passages]
        hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        duration = audiobook_narration._wav_ms(audiobooks.chapter_audio_path(identifier, 0))
        request = SetAudiobookPacingRequest(passage_gap_ms=100, speaker_change_gap_ms=250,
            chapters=[AudiobookPacingChapter(chapter_index=0, revision=job.revision)])
        with patch.object(speech_clone, "synthesize_to_path", side_effect=AssertionError("Pacing must retain dry takes")):
            audiobooks.set_pacing(identifier, request)
        updated = audiobook_workflows.get_passages(identifier, 0)
        self.assertEqual(updated.revision, previous.revision + 1)
        self.assertEqual(audiobook_narration._wav_ms(audiobooks.chapter_audio_path(identifier, 0)), duration + 700)
        self.assertEqual([hashlib.sha256(path.read_bytes()).hexdigest() for path in paths], hashes)
        for first, second in zip(updated.passages, updated.passages[1:]):
            self.assertEqual(second.start_ms - first.end_ms, 350)
        with self.assertRaises(audiobooks.AudiobookError) as caught:
            audiobooks.set_pacing(identifier, request)
        self.assertEqual(caught.exception.code, "chapter_changed")

    def test_override_zero_replaces_default_and_final_override_is_retained(self) -> None:
        self.assertTrue(hasattr(audiobooks, "set_pacing"), "Passage pause overrides are missing")
        from app.audiobook_contracts import SetAudiobookPacingRequest, AudiobookPacingChapter, AudiobookPassageGap
        identifier = self.dialogue()
        passages = audiobook_workflows.get_passages(identifier, 0)
        original = audiobook_narration._wav_ms(audiobooks.chapter_audio_path(identifier, 0))
        audiobooks.set_pacing(identifier, SetAudiobookPacingRequest(passage_gap_ms=100, speaker_change_gap_ms=250,
            chapters=[AudiobookPacingChapter(chapter_index=0, revision=passages.revision,
                passages=[AudiobookPassageGap(passage_id=passages.passages[0].id, gap_after_ms=0),
                          AudiobookPassageGap(passage_id=passages.passages[-1].id, gap_after_ms=200)])]))
        current = audiobook_workflows.get_passages(identifier, 0)
        self.assertEqual(current.passages[1].start_ms, current.passages[0].end_ms)
        self.assertEqual(audiobook_narration._wav_ms(audiobooks.chapter_audio_path(identifier, 0)), original + 550)
        self.assertEqual(current.passages[-1].gap_after_ms, 200)

    def test_display_spelling_saved_before_pronunciation_and_legacy_not_invented(self) -> None:
        identifier = audiobooks.create_book(CreateAudiobookRequest(title="Names", profile_id=self.profile.id,
            language="en", pronunciations=[PronunciationEntry(written="OpenFabric", spoken="Open fabric")],
            chapters=[AudiobookChapterInput(text="Welcome to OpenFabric.")])).book.id
        passage = audiobook_workflows.get_passages(identifier, 0).passages[0]
        self.assertEqual(getattr(passage, "display_text", None), "Welcome to OpenFabric.")
        self.assertEqual(passage.text, "Welcome to Open fabric.")
        with audiobooks._connect() as connection:
            connection.execute("UPDATE audiobook_sections SET display_text=NULL WHERE passage_id=?", (passage.id,))
            connection.commit()
        self.assertIsNone(audiobook_workflows.get_passages(identifier, 0).passages[0].display_text)

    def test_sample_offsets_do_not_accumulate_rounded_milliseconds(self) -> None:
        self.assertTrue(hasattr(audiobook_narration, "concat_timed_wavs"), "Sample-derived assembly is missing")
        identifier = self.create()
        root = audiobooks.chapters_dir(identifier)
        paths = [root / f"fraction-{index}.wav" for index in range(5)]
        for path in paths:
            fixtures.pcm(path, frames=11, rate=44100)
        target = root / "timed.wav"
        offsets = audiobook_narration.concat_timed_wavs(identifier, paths, target, [1, 1, 1, 1, 0], controlled=False)
        with wave.open(str(target), "rb") as handle:
            self.assertEqual(handle.getnframes(), 55 + 4 * 44)
        self.assertEqual(offsets[-1], (5, 5))

    def test_paused_pacing_can_edit_inputs_and_resume_a_new_synthesis_plan(self) -> None:
        from app.audiobook_contracts import SetAudiobookPacingRequest, AudiobookPacingChapter
        for edit in ["text", "cast", "pronunciations", "language"]:
            with self.subTest(edit=edit):
                identifier = self.dialogue()
                version = audiobook_workflows.get_passages(identifier, 0)
                old_paths = [audiobook_workflows.passage_audio_path(identifier, passage.id) for passage in version.passages]
                with patch.object(audiobooks, "_schedule_book"):
                    audiobooks.set_pacing(identifier, SetAudiobookPacingRequest(passage_gap_ms=100,
                        chapters=[AudiobookPacingChapter(chapter_index=0, revision=version.revision)]))
                audiobooks.pause_book(identifier)
                if edit == "text":
                    audiobooks.set_chapter_text(identifier, 0, "A new chapter.")
                elif edit == "cast":
                    audiobooks.set_cast(identifier, [])
                elif edit == "pronunciations":
                    audiobooks.set_pronunciations(identifier, [PronunciationEntry(written="Narration", spoken="Reading")])
                else:
                    audiobooks.set_languages(identifier, "ja", [])
                async def resume() -> None:
                    await audiobooks.resume_book(identifier)
                    await audiobooks.wait_for_book(identifier)
                asyncio.run(resume())
                self.assertEqual(audiobooks.get_book(identifier).status, "done")
                self.assertTrue(all(path.is_file() for path in old_paths))

    def test_scene_audition_previews_book_gap_defaults(self) -> None:
        from app.audiobook_contracts import AudiobookAuditionOptions, SetAudiobookPacingRequest, AudiobookPacingChapter
        identifier = self.dialogue()
        version = audiobook_workflows.get_passages(identifier, 0)
        audiobooks.set_pacing(identifier, SetAudiobookPacingRequest(passage_gap_ms=100, speaker_change_gap_ms=250,
            chapters=[AudiobookPacingChapter(chapter_index=0, revision=version.revision)]))
        audition = audiobook_workflows.start_book_audition(identifier, AudiobookAuditionOptions(mode="scene"))
        clips = [audiobook_workflows.audition_audio_path(audition.id, clip.index) for clip in audition.clips]
        dry_total = sum(audiobook_narration._wav_ms(path) for path in clips)
        self.assertEqual(audiobook_narration._wav_ms(audiobook_workflows.audition_audio_path(audition.id)), dry_total + 700)

    def test_repairs_keep_composition_gaps_and_changed_words_drop_original_mapping(self) -> None:
        from app.audiobook_contracts import (SetAudiobookPacingRequest, AudiobookPacingChapter,
            CreateAudiobookRepairRequest, AcceptAudiobookRepairRequest)
        identifier = self.dialogue()
        version = audiobook_workflows.get_passages(identifier, 0)
        audiobooks.set_pacing(identifier, SetAudiobookPacingRequest(passage_gap_ms=100, speaker_change_gap_ms=250,
            chapters=[AudiobookPacingChapter(chapter_index=0, revision=version.revision)]))
        version = audiobook_workflows.get_passages(identifier, 0)
        old_export = audiobooks.export_path_for(identifier)
        old_bytes = old_export.read_bytes()
        untouched = audiobook_workflows.passage_audio_path(identifier, version.passages[1].id)
        unchanged = untouched.read_bytes()
        def fresh(**kwargs: object) -> speech_clone.SynthesisOutcome:
            path = kwargs["output_path"]
            assert isinstance(path, Path)
            fixtures.pcm(path, value=100, frames=8000, rate=16000)
            return speech_clone.SynthesisOutcome(status="completed", detail="", output_path=path)
        with patch.object(speech_clone, "synthesize_to_path", side_effect=fresh):
            repair = audiobook_workflows.start_repair(identifier, 0, version.passages[0].id,
                CreateAudiobookRepairRequest(revision=version.revision, text="Changed words."))
        accepted = audiobook_workflows.accept_repair(repair.id, AcceptAudiobookRepairRequest(revision=version.revision))
        self.assertIsNone(accepted.passages[0].display_text)
        self.assertEqual(accepted.passages[1].display_text, version.passages[1].display_text)
        self.assertEqual(accepted.passages[0].end_ms, 500)
        self.assertEqual(accepted.passages[1].start_ms, 850)
        self.assertEqual(untouched.read_bytes(), unchanged)
        self.assertEqual(old_export.read_bytes(), old_bytes)
        audiobooks.regenerate_chapter(identifier, 0)
        self.assertEqual(audiobooks.get_book(identifier).status, "done")
        self.assertEqual(audiobook_workflows.get_passages(identifier, 0).passages[0].text, "Changed words.")

    def test_interrupted_pacing_recovers_without_synthesizing(self) -> None:
        from app.audiobook_contracts import SetAudiobookPacingRequest, AudiobookPacingChapter
        identifier = self.dialogue()
        version = audiobook_workflows.get_passages(identifier, 0)
        with patch.object(audiobooks, "_schedule_book"):
            result = audiobooks.set_pacing(identifier, SetAudiobookPacingRequest(passage_gap_ms=300,
                chapters=[AudiobookPacingChapter(chapter_index=0, revision=version.revision)]))
        self.assertEqual(result.status, "queued")
        asyncio.run(audiobooks.start())
        self.assertEqual(audiobooks.get_book(identifier).status, "paused")
        with patch.object(speech_clone, "synthesize_to_path", side_effect=AssertionError("Recovery must reuse dry PCM")):
            async def resume() -> None:
                await audiobooks.resume_book(identifier)
                await audiobooks.wait_for_book(identifier)
            asyncio.run(resume())
        self.assertEqual(audiobooks.get_book(identifier).status, "done")
        current = audiobook_workflows.get_passages(identifier, 0)
        self.assertEqual(current.passages[1].start_ms - current.passages[0].end_ms, 300)

    def test_concurrent_pacing_only_one_current_revision_can_publish(self) -> None:
        from app.audiobook_contracts import SetAudiobookPacingRequest, AudiobookPacingChapter
        identifier = self.dialogue()
        version = audiobook_workflows.get_passages(identifier, 0)
        def save(milliseconds: int) -> str:
            try:
                audiobooks.set_pacing(identifier, SetAudiobookPacingRequest(passage_gap_ms=milliseconds,
                    chapters=[AudiobookPacingChapter(chapter_index=0, revision=version.revision)]))
                return "saved"
            except audiobooks.AudiobookError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(save, [100, 200]))
        self.assertEqual(sorted(results), ["chapter_changed", "saved"])
        self.assertEqual(audiobook_workflows.get_passages(identifier, 0).revision, version.revision + 1)

    def test_pacing_uses_accepted_cast_after_current_cast_edits(self) -> None:
        from app.audiobook_contracts import SetAudiobookPacingRequest, AudiobookPacingChapter
        identifier = self.dialogue()
        before = audiobook_workflows.get_passages(identifier, 0)
        audiobooks.set_cast(identifier, [])
        version = audiobook_workflows.get_passages(identifier, 0)
        with patch.object(speech_clone, "synthesize_to_path", side_effect=AssertionError("Keep accepted cast audio")):
            audiobooks.set_pacing(identifier, SetAudiobookPacingRequest(speaker_change_gap_ms=250,
                chapters=[AudiobookPacingChapter(chapter_index=0, revision=version.revision)]))
        self.assertEqual(audiobooks.get_book(identifier).status, "done")
        current = audiobook_workflows.get_passages(identifier, 0)
        self.assertEqual([item.profile_id for item in current.passages], [item.profile_id for item in before.passages])
        self.assertEqual(current.passages[1].start_ms - current.passages[0].end_ms, 250)


class DisplayMappingTests(unittest.TestCase):
    def test_unicode_case_insensitive_match_keeps_existing_pronunciation_semantics(self) -> None:
        pronunciations = [PronunciationEntry(written="I", spoken="Eye")]
        for original in ["İ reads.", "ı reads.", "I reads.", "i reads."]:
            with self.subTest(original=original):
                pieces = audiobook_narration.plan_text(original, "a" * 32, [], pronunciations)
                display = audiobook_narration.plan_display_text(original, "a" * 32, [], pronunciations)
                self.assertEqual(display, [original])
                self.assertTrue(pieces)

    def test_multiple_sections_keep_original_phrase_and_casing(self) -> None:
        self.assertTrue(hasattr(audiobook_narration, "plan_display_text"), "Spoken/display mapping is missing")
        text = "OpenFabric has useful voices. " * 100
        pronunciations = [PronunciationEntry(written="OpenFabric", spoken="Open fabric")]
        planned = audiobook_narration.plan_text(text, "a" * 32, [], pronunciations)
        display = audiobook_narration.plan_display_text(text, "a" * 32, [], pronunciations)
        self.assertEqual(len(planned), len(display))
        self.assertEqual("".join(item or "" for item in display), text.strip())
        self.assertTrue(all("Open fabric" not in (item or "") for item in display))


class DurationGuidanceTests(fixtures._WorkflowFixture):
    def test_completed_measured_takes_use_exact_snapshot_key(self) -> None:
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec("app.narration_duration"), "Measured duration guidance is missing")
        from app import narration_duration
        from app.audiobook_contracts import NarrationDurationRequest
        from app.speech_references import capture
        with patch.object(speech_clone, "known_engine_identity", return_value="verified-test-renderer"):
            snapshot = capture(self.profile.id, "en", self.root / "references", "verified-test-renderer")
            audio = self.root / "measured.wav"
            # Alternating non-silent PCM, exactly two seconds.
            with wave.open(str(audio), "wb") as handle:
                handle.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                handle.writeframes(b"\x00\x20\x00\xe0" * 8000)
            narration_duration.record_measurement(snapshot, "Some real narration words.", audio)
            result = narration_duration.guidance(NarrationDurationRequest(profile_id=self.profile.id, language="en", text="Some real narration words.", target_seconds=15))
            self.assertEqual(result.state, "approximate")
            self.assertEqual(result.measurement_count, 1)
            self.assertEqual(result.measured_audio_ms, 2000)
            self.assertLessEqual(result.estimated_min_ms or 0, 2000)
            self.assertGreaterEqual(result.estimated_max_ms or 0, 2000)
            fixtures.pcm(Path(self.profile.reference_audio_path), value=2)
            self.assertEqual(narration_duration.guidance(NarrationDurationRequest(profile_id=self.profile.id, language="en")).state, "unavailable")

    def test_repair_guidance_uses_accepted_snapshot_when_profile_changes(self) -> None:
        from app import narration_duration
        from app.audiobook_contracts import NarrationDurationRequest
        from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest
        def synthesize(**kwargs: object) -> speech_clone.SynthesisOutcome:
            path = kwargs["output_path"]
            assert isinstance(path, Path)
            with wave.open(str(path), "wb") as handle:
                handle.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                handle.writeframes(b"\x00\x20\x00\xe0" * 8000)
            return speech_clone.SynthesisOutcome(status="completed", detail="", output_path=path)
        with patch.object(speech_clone, "known_engine_identity", return_value="verified-test-renderer"), patch.object(speech_clone, "synthesize_to_path", side_effect=synthesize):
            identifier = self.create(text="Some real narration words.")
            version = audiobook_workflows.get_passages(identifier, 0)
            fixtures.voice_profiles.patch_profile(self.profile.id, PatchSpeechVoiceProfileRequest(reference_transcript="Changed reference words."))
            current = narration_duration.guidance(NarrationDurationRequest(profile_id=self.profile.id, language="en", text="Some real narration words."))
            self.assertEqual(current.state, "unavailable")
            request = NarrationDurationRequest.model_validate({"profile_id":self.profile.id,"language":"en","text":"Some real narration words.",
                "passage_source":{"book_id":identifier,"chapter_index":0,"passage_id":version.passages[0].id,"revision":version.revision}})
            accepted = narration_duration.guidance(request)
            self.assertEqual(accepted.state, "approximate")

    def test_unknown_model_and_silent_mock_are_not_measured_pace(self) -> None:
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec("app.narration_duration"), "Honest unavailable guidance is missing")
        from app import narration_duration
        from app.audiobook_contracts import NarrationDurationRequest
        from app.speech_references import capture
        with patch.object(speech_clone, "known_engine_identity", return_value=None):
            result = narration_duration.guidance(NarrationDurationRequest(profile_id=self.profile.id, language="en"))
            self.assertEqual(result.reason, "model_unverified")
            self.assertIsNone(result.suggested_characters)
        with patch.object(speech_clone, "known_engine_identity", return_value="verified-test-renderer"):
            snapshot = capture(self.profile.id, "en", self.root / "references", "verified-test-renderer")
            silent = self.root / "silent.wav"
            fixtures.pcm(silent, value=0, frames=16000)
            narration_duration.record_measurement(snapshot, "Silence should never become measured speech.", silent)
            result = narration_duration.guidance(NarrationDurationRequest(profile_id=self.profile.id, language="en"))
            self.assertEqual(result.state, "unavailable")
            self.assertEqual(result.measurement_count, 0)


class TimingHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        import httpx
        from fastapi import FastAPI
        from app.api.routes_audiobooks import router
        self.fixture = fixtures._WorkflowFixture()
        self.fixture.setUp()
        app = FastAPI()
        app.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.fixture.doCleanups()

    async def test_pacing_api_validates_bounds_and_stale_revisions(self) -> None:
        identifier = self.fixture.create()
        original = audiobooks.list_jobs(book_id=identifier)[0]
        body = {"passage_gap_ms":100, "chapters":[{"chapter_index":0,"revision":original.revision}]}
        invalid = await self.client.put(f"/api/audiobooks/{identifier}/pacing", json={**body,"speaker_change_gap_ms":5001})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(audiobooks.list_jobs(book_id=identifier)[0].revision, original.revision)
        response = await self.client.put(f"/api/audiobooks/{identifier}/pacing", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "done")
        repeated = await self.client.put(f"/api/audiobooks/{identifier}/pacing", json=body)
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(repeated.json()["detail"], "chapter_changed")

    async def test_duration_api_unknown_model_is_safe_and_invalid_target_rejected(self) -> None:
        body = {"profile_id":self.fixture.profile.id,"language":"en","text":"Some narration."}
        response = await self.client.post("/api/audiobooks/duration-guidance", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"], "unavailable")
        self.assertEqual(response.json()["reason"], "model_unverified")
        self.assertNotIn(str(self.fixture.root), response.text)
        invalid = await self.client.post("/api/audiobooks/duration-guidance", json={**body,"target_seconds":20})
        self.assertEqual(invalid.status_code, 422)

    async def test_non_local_origin_cannot_change_pacing(self) -> None:
        identifier = self.fixture.create()
        revision = audiobooks.list_jobs(book_id=identifier)[0].revision
        response = await self.client.put(f"/api/audiobooks/{identifier}/pacing", json={"passage_gap_ms":100,
            "chapters":[{"chapter_index":0,"revision":revision}]}, headers={"Origin":"https://untrusted.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(audiobooks.list_jobs(book_id=identifier)[0].revision, revision)
