"""Read-along exports retain accepted PCM and honest passage-level timing."""
from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
import unittest
import wave
import sys
from pathlib import Path
from unittest.mock import patch

from app import audiobooks, reading_media, voice_profiles, retained_audio, video_projects
from app.reading_media_contracts import ReadAlongRequest, ReadingCue, RetainedAudioSource, RetainedAudioVideoRequest
from app.audiobook_workflows_test import _WorkflowFixture
from app.audiobook_contracts import AudiobookChapterInput, CreateAudiobookRequest, PronunciationEntry
from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest, SpeechCloneTrialRequest
from app.video_contracts import VideoRevisionRequest


class ReadingMediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root.mkdir(exist_ok=True)
        self.audio = self.root / 'accepted.wav'
        with wave.open(str(self.audio), 'wb') as audio:
            audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\x01\x00' * 16000)
        for item in (patch.object(audiobooks, 'BOOKS_ROOT', self.root / 'books'),
                     patch.object(reading_media, '_STOPPING', False)):
            item.start()
            self.addCleanup(item.stop)
        self.cues = [ReadingCue(passage_id='b' * 32, start_ms=0, end_ms=1000,
                               display_text='Original <spelling> & words.', spoken_text='Changed pronunciation.')]
        self.snapshot = reading_media.ChapterSnapshot(book_id='a' * 32, chapter_index=0,
            revision=1, title='Chapter', audio_path=self.audio,
            audio_sha256=hashlib.sha256(self.audio.read_bytes()).hexdigest(),
            duration_ms=2000, cues=self.cues, profile_ids=[], components=[])

    def test_subtitles_use_original_spelling_and_passage_boundaries(self) -> None:
        srt = reading_media.subtitles(self.cues, 'srt', 2000)
        vtt = reading_media.subtitles(self.cues, 'vtt', 2000)
        self.assertIn('00:00:00,000 --> 00:00:01,000', srt)
        self.assertIn('Original &lt;spelling&gt; &amp; words.', srt)
        self.assertNotIn('Changed pronunciation', srt)
        self.assertTrue(vtt.startswith('WEBVTT\n'))
        self.assertEqual(srt.count('-->'), 1)

    def test_preview_clips_cue_end_without_inventing_words(self) -> None:
        result = reading_media.subtitles(self.cues, 'vtt', 500)
        self.assertIn('00:00:00.500', result)
        self.assertIn('Original', result)

    def test_unsafe_artifact_paths_are_rejected(self) -> None:
        for filename in ('../private.wav', '/private.wav', 'movie.mp4/../../private.wav'):
            with self.subTest(filename=filename), self.assertRaises(reading_media.ReadingMediaError):
                reading_media.artifact('c' * 32, filename)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required for CPU export regression')
    def test_cpu_export_is_durable_and_does_not_change_source(self) -> None:
        before = self.audio.read_bytes()
        async def run() -> None:
            with patch.object(reading_media, 'chapter_snapshot', return_value=self.snapshot), \
                 patch.object(reading_media, '_current', return_value=True):
                job = await reading_media.create('a' * 32, 0, ReadAlongRequest(revision=1, preview_seconds=2))
                await reading_media.wait(job.id)
                finished = reading_media.get(job.id)
                self.assertEqual(finished.status, 'done', finished.detail)
                self.assertTrue(reading_media.artifact(job.id, 'movie.mp4').is_file())
                self.assertIn('Original', reading_media.artifact(job.id, 'captions.vtt').read_text())
                self.assertEqual(finished.timing, 'passage')
                self.assertEqual(reading_media.list_exports('a' * 32, 0).exports[0].id, job.id)
                movie = reading_media.serve(job.id, 'movie.mp4')
                movie.write_bytes(movie.read_bytes() + b'altered')
                with self.assertRaisesRegex(reading_media.ReadingMediaError, 'reading_artifact_changed'):
                    reading_media.serve(job.id, 'movie.mp4')
        asyncio.run(run())

        self.assertEqual(before, self.audio.read_bytes())

    def test_source_changed_before_publication_preserves_recording(self) -> None:
        async def run() -> None:
            with patch.object(reading_media, 'chapter_snapshot', return_value=self.snapshot), \
                 patch.object(reading_media, '_current', return_value=False), \
                 patch.object(reading_media, '_encode', return_value=None):
                job = await reading_media.create('a' * 32, 0, ReadAlongRequest(revision=1))
                await reading_media.wait(job.id)
                self.assertEqual(reading_media.get(job.id).detail, 'reading_source_changed')
                self.assertFalse(reading_media.artifact(job.id, 'movie.mp4').exists())
        asyncio.run(run())
        self.assertTrue(self.audio.exists())

    def test_cancel_before_worker_starts_is_persisted_and_resumable(self) -> None:
        async def run() -> None:
            with patch.object(reading_media, 'chapter_snapshot', return_value=self.snapshot), \
                 patch.object(reading_media, '_current', return_value=True):
                job = await reading_media.create('a' * 32, 0, ReadAlongRequest(revision=1))
                result = await reading_media.cancel(job.id)
                self.assertEqual(result.status, 'cancelled')
                self.assertFalse(reading_media.work_busy())
                self.assertTrue(reading_media.artifact(job.id, 'source.wav').exists())
        asyncio.run(run())

    def test_two_resumes_queued_behind_admission_start_only_one_worker(self) -> None:
        from app.resource_admission import admission_lock
        from app.reading_media_contracts import ReadAlongExport
        async def encode(stored: reading_media._StoredExport) -> None:
            await asyncio.Event().wait()
        async def run() -> None:
            with patch.object(reading_media, 'chapter_snapshot', return_value=self.snapshot), \
                 patch.object(reading_media, '_current', return_value=True), \
                 patch.object(reading_media, '_encode', side_effect=encode):
                job = await reading_media.create('a' * 32, 0, ReadAlongRequest(revision=1))
                await reading_media.cancel(job.id)
                await admission_lock.acquire()
                try:
                    first = asyncio.create_task(reading_media.resume(job.id))
                    second = asyncio.create_task(reading_media.resume(job.id))
                    await asyncio.sleep(0)
                finally:
                    admission_lock.release()
                results = await asyncio.gather(first, second, return_exceptions=True)
                try:
                    self.assertEqual(sum(isinstance(result, ReadAlongExport) for result in results), 1)
                    self.assertEqual(sum(isinstance(result, reading_media.ReadingMediaError) for result in results), 1)
                    self.assertEqual(len(reading_media._TASKS), 1)
                finally:
                    await reading_media.cancel(job.id)
        asyncio.run(run())

    def test_restart_marks_interrupted_jobs_without_starting_encoding(self) -> None:
        async def run() -> None:
            with patch.object(reading_media, 'chapter_snapshot', return_value=self.snapshot), \
                 patch.object(reading_media, '_current', return_value=True):
                job = await reading_media.create('a' * 32, 0, ReadAlongRequest(revision=1))
                await reading_media.cancel(job.id)
                reading_media._state(job.id, 'running')
                with patch.object(reading_media, '_encode', side_effect=AssertionError('restart must not auto-render')):
                    await reading_media.start()
                self.assertEqual(reading_media.get(job.id).detail, 'reading_interrupted')
                self.assertEqual(reading_media.get(job.id).status, 'failed')
        asyncio.run(run())

    def test_audio_handoff_copies_exact_clip_and_stores_source_identity(self) -> None:
        source = RetainedAudioSource(kind='chapter', source_id='a' * 32, chapter_index=0, revision=1)
        retained = retained_audio.RetainedSnapshot(self.audio, 2000, self.snapshot.audio_sha256, [], [])
        before = self.audio.read_bytes()
        with patch.object(video_projects, 'DATA_DIR', self.root / 'library'), \
             patch.object(retained_audio, 'snapshot', return_value=retained):
            project = asyncio.run(video_projects.create_retained_audio_project(RetainedAudioVideoRequest(
                source=source, source_sha256=retained.sha256, clip_start_ms=500, clip_end_ms=1500)))
            self.assertIsNone(project.job)
            self.assertEqual(project.speech_clip.duration_sec if project.speech_clip else 0, 1)
            document = video_projects.load(project.id)
            self.assertIsNotNone(document.retained_audio)
            with wave.open(str(video_projects.speech_file(project.id)), 'rb') as selected:
                self.assertEqual(selected.getnframes(), 8000)
                self.assertEqual(selected.readframes(8000), b'\x01\x00' * 8000)
        self.assertEqual(self.audio.read_bytes(), before)

    def test_long_audio_requires_explicit_trim_and_changed_hash_is_rejected(self) -> None:
        source = RetainedAudioSource(kind='chapter', source_id='a' * 32, chapter_index=0, revision=1)
        retained = retained_audio.RetainedSnapshot(self.audio, 18000, self.snapshot.audio_sha256, [], [])
        with patch.object(retained_audio, 'snapshot', return_value=retained):
            with self.assertRaisesRegex(video_projects.VideoProjectError, 'retained_audio_trim_required'):
                asyncio.run(video_projects.create_retained_audio_project(RetainedAudioVideoRequest(
                    source=source, source_sha256=retained.sha256)))
            with self.assertRaisesRegex(video_projects.VideoProjectError, 'retained_audio_changed'):
                asyncio.run(video_projects.create_retained_audio_project(RetainedAudioVideoRequest(
                    source=source, source_sha256='0' * 64, clip_end_ms=1000)))

    def test_cancel_drains_the_owned_codec_process(self) -> None:
        from app import audiobook_publish
        async def encode(stored: reading_media._StoredExport) -> None:
            await audiobook_publish._run_owned([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=60)
        async def run() -> None:
            with patch.object(reading_media, 'chapter_snapshot', return_value=self.snapshot), \
                 patch.object(reading_media, '_current', return_value=True), \
                 patch.object(reading_media, '_encode', side_effect=encode):
                job = await reading_media.create('a' * 32, 0, ReadAlongRequest(revision=1))
                for _ in range(200):
                    if audiobook_publish._OWNED:
                        break
                    await asyncio.sleep(.01)
                self.assertTrue(audiobook_publish._OWNED)
                result = await reading_media.cancel(job.id)
                self.assertEqual(result.status, 'cancelled')
                self.assertFalse(audiobook_publish._OWNED)
                self.assertFalse(audiobook_publish.cleanup_pending())
                self.assertTrue(reading_media.artifact(job.id, 'source.wav').exists())
        asyncio.run(run())

    def test_http_boundary_rejects_client_paths_and_requires_completed_artifacts(self) -> None:
        import httpx
        from fastapi import FastAPI
        from app.api.routes_reading_media import router
        app = FastAPI()
        app.include_router(router)
        async def run() -> None:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                invalid = await client.post('/api/reading-media/audio/info', json={
                    'kind': 'speech_trial', 'source_id': '../../private'})
                self.assertEqual(invalid.status_code, 422)
                missing = await client.get('/api/reading-media/exports/' + 'a' * 32 + '/movie.mp4')
                self.assertEqual(missing.status_code, 404)
        asyncio.run(run())

    def test_invalid_encoder_output_is_not_published(self) -> None:
        async def encode(stored: reading_media._StoredExport) -> None:
            reading_media.artifact(stored.export.id, 'movie.mp4').write_bytes(b'not an MP4')
        async def run() -> None:
            with patch.object(reading_media, 'chapter_snapshot', return_value=self.snapshot), \
                 patch.object(reading_media, '_current', return_value=True), \
                 patch.object(reading_media, '_encode', side_effect=encode):
                job = await reading_media.create('a' * 32, 0, ReadAlongRequest(revision=1))
                await reading_media.wait(job.id)
                self.assertEqual(reading_media.get(job.id).detail, 'reading_output_invalid')
                self.assertIsNone(reading_media.get(job.id).video_url)
                self.assertFalse(reading_media.artifact(job.id, 'movie.mp4').exists())
        asyncio.run(run())

    def test_reading_card_holds_through_pauses_without_extending_subtitles(self) -> None:
        import subprocess
        async def run() -> None:
            with patch.object(reading_media, 'chapter_snapshot', return_value=self.snapshot):
                job = await reading_media.create('a' * 32, 0, ReadAlongRequest(revision=1))
                await reading_media.cancel(job.id)
            stored = reading_media._load(job.id)
            stored.cues.append(ReadingCue(passage_id='c' * 32, start_ms=1500, end_ms=1800,
                display_text='Following passage.', spoken_text='Following passage.'))
            texts: list[str] = []
            with patch.object(reading_media, '_card', side_effect=lambda path, text, width, height, generated: texts.append(text)), \
                 patch('app.audiobook_publish._run_owned', return_value=subprocess.CompletedProcess([], 0, '', '')):
                await reading_media._encode(stored)
            self.assertEqual(texts, ['Original <spelling> & words.', 'Original <spelling> & words.',
                                    'Following passage.', 'Following passage.'])
            self.assertIn('00:00:01,000', reading_media.subtitles(stored.cues, 'srt', 2000))
        asyncio.run(run())


class ChapterSourceTests(_WorkflowFixture):
    def test_original_spelling_survives_pronunciation_and_mock_origin_is_honest(self) -> None:
        book = audiobooks.create_book(CreateAudiobookRequest(title='Book', profile_id=self.profile.id,
            language='en', chapters=[AudiobookChapterInput(text='Seth reads the words.')],
            pronunciations=[PronunciationEntry(written='Seth', spoken='S-e-th')])).book
        job = audiobooks.list_jobs(book_id=book.id)[0]
        source = reading_media.chapter_snapshot(book.id, 0, job.revision)
        self.assertEqual(source.cues[0].display_text, 'Seth reads the words.')
        self.assertIn('S-e-th', source.cues[0].spoken_text)
        self.assertEqual(source.components[0].content_origin, 'unknown')
        self.assertEqual(source.profile_ids, [self.profile.id])

    def test_revoked_consent_blocks_source_resolution(self) -> None:
        identifier = self.create()
        job = audiobooks.list_jobs(book_id=identifier)[0]
        voice_profiles.patch_profile(self.profile.id, PatchSpeechVoiceProfileRequest(consent_confirmed=False))
        with self.assertRaisesRegex(reading_media.ReadingMediaError, 'consent_required'):
            reading_media.chapter_snapshot(identifier, 0, job.revision)

    def test_retained_chapter_ownership_survives_duplicate_and_undo(self) -> None:
        identifier = self.create()
        job = audiobooks.list_jobs(book_id=identifier)[0]
        source = RetainedAudioSource(kind='chapter', source_id=identifier, chapter_index=0, revision=job.revision)
        info = retained_audio.info(source)
        with patch.object(video_projects, 'DATA_DIR', self.root / 'library'):
            project = asyncio.run(video_projects.create_retained_audio_project(RetainedAudioVideoRequest(
                source=source, source_sha256=info.source_sha256)))
            duplicate = video_projects.duplicate(project.id, VideoRevisionRequest(revision=project.revision))
            cleared = video_projects.clear_speech(project.id, VideoRevisionRequest(revision=project.revision))
            self.assertIsNone(video_projects.load(project.id).retained_audio)
            restored = video_projects.undo(project.id, VideoRevisionRequest(revision=cleared.revision))
            identity = video_projects.load(restored.id).retained_audio
            self.assertIsNotNone(identity)
            self.assertIsNotNone(video_projects.load(duplicate.id).retained_audio)
            if identity is None or restored.speech_clip is None:
                self.fail('Restored recording identity missing')
            components = retained_audio.video_components(identity, restored.speech_clip.sha256)
            self.assertEqual(components[-1].source_sha256, restored.speech_clip.sha256)
            self.assertEqual(components[-1].content_origin, 'unknown')
            voice_profiles.patch_profile(self.profile.id, PatchSpeechVoiceProfileRequest(consent_confirmed=False))
            for project_id in (project.id, duplicate.id):
                with self.assertRaisesRegex(video_projects.VideoProjectError, 'consent_required'):
                    video_projects.speech_file(project_id)

    def test_speech_trial_handoff_requires_retained_manifest_and_consent(self) -> None:
        from app import speech_clone, export_provenance
        with patch.object(speech_clone, 'TRIALS_ROOT', self.root / 'trials'):
            trial = speech_clone.start_trial(SpeechCloneTrialRequest(profile_id=self.profile.id,
                text='Hello world.', text_language='en'))
            if trial.trial_id is None:
                self.fail('Mock trial did not complete')
            source = RetainedAudioSource(kind='speech_trial', source_id=trial.trial_id)
            info = retained_audio.info(source)
            self.assertEqual(info.content_origin, 'unknown')
            media = speech_clone.TRIALS_ROOT / f'{trial.trial_id}.wav'
            export_provenance.path_for(media, 'json').unlink()
            with self.assertRaisesRegex(reading_media.ReadingMediaError, 'retained_audio_unverified'):
                retained_audio.info(source)
