"""Cast checks inspect routing without synthesis or persisted preview artifacts."""
from __future__ import annotations

import io
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app import audiobook_narration, audiobooks, speech_clone, voice_profiles
from app.api.routes_audiobooks import router
from app.audiobook_contracts import CreateAudiobookRequest
from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest


class CastCheckTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for target in (patch.object(voice_profiles, 'PROFILES_ROOT', self.root / 'profiles'),
                       patch.object(audiobooks, 'BOOKS_ROOT', self.root / 'books'),
                       patch.object(audiobooks, '_schedule_book')):
            target.start()
            self.addCleanup(target.stop)
        pcm = io.BytesIO()
        with wave.open(pcm, 'wb') as handle:
            handle.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            handle.writeframes(b'\x01\x00' * 800)
        self.narrator = voice_profiles.create_profile(name='Reader', consent_confirmed=True,
            audio_bytes=pcm.getvalue(), filename='reference.wav')
        self.alice = voice_profiles.create_profile(name='Alice voice', consent_confirmed=True,
            audio_bytes=pcm.getvalue(), filename='reference.wav')
        self.body = {'title': 'Scene', 'profile_id': self.narrator.id, 'chapters': [{'text':
            'Opening.\n alice: Hello.\nBob: Unknown.\nNote: A heading.\nTwin: Same voice.'}],
            'cast': [{'name': 'Alice', 'profile_id': self.alice.id},
                     {'name': 'Twin', 'profile_id': self.narrator.id},
                     {'name': 'Unused', 'profile_id': self.alice.id}]}
        app = FastAPI()
        app.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://127.0.0.1:9000')
        self.addAsyncCleanup(self.client.aclose)

    async def test_draft_check_matches_narration_and_warns_without_synthesis_or_writes(self) -> None:
        before = sorted(str(path) for path in self.root.rglob('*'))
        with patch.object(speech_clone, 'synthesize_to_path', side_effect=AssertionError('No synthesis')):
            response = await self.client.post('/api/audiobooks/cast-check', json=self.body)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        request = CreateAudiobookRequest.model_validate(self.body)
        expected = audiobook_narration.plan_text(request.chapters[0].text, request.profile_id, request.cast, [])
        self.assertEqual([(row['text'], row['profile_id'], row['speaker']) for row in result['turns']], expected)
        self.assertEqual(result['turns'][1]['profile_name'], 'Alice voice')
        warnings = result['warnings']
        self.assertEqual([item['speaker'] for item in warnings if item['code'] == 'unmatched_label'], ['Bob', 'Note'])
        self.assertTrue(any(item['code'] == 'shared_narrator' and item['speaker'] == 'Twin' for item in warnings))
        self.assertTrue(any(item['code'] == 'unused_cast' and item['speaker'] == 'Unused' for item in warnings))
        self.assertEqual(sorted(str(path) for path in self.root.rglob('*')), before)

    async def test_missing_and_revoked_profiles_remain_visible(self) -> None:
        voice_profiles.patch_profile(self.alice.id, PatchSpeechVoiceProfileRequest(consent_confirmed=False))
        body = {**self.body, 'cast': [*self.body['cast'], {'name': 'Lost', 'profile_id': 'f' * 32}]}
        response = await self.client.post('/api/audiobooks/cast-check', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        codes = {(item['code'], item['speaker']) for item in response.json()['warnings']}
        self.assertIn(('consent_required', 'Alice'), codes)
        self.assertIn(('profile_missing', 'Lost'), codes)

    async def test_saved_check_uses_chapter_revision_and_rejects_stale_inputs(self) -> None:
        book = audiobooks.create_book(CreateAudiobookRequest.model_validate(self.body)).book
        revision = audiobooks.list_jobs(book_id=book.id)[0].revision
        path = f'/api/audiobooks/{book.id}/cast-check'
        result = await self.client.post(path, json={'chapter_index': 0, 'revision': revision})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['revision'], revision)
        stale = await self.client.post(path, json={'chapter_index': 0, 'revision': revision + 1})
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()['detail'], 'chapter_changed')

    async def test_duplicate_names_invalid_chapters_and_untrusted_origins_are_rejected(self) -> None:
        duplicate = {**self.body, 'cast': [{'name': 'Alice', 'profile_id': self.alice.id},
                                         {'name': ' alice ', 'profile_id': self.narrator.id}]}
        response = await self.client.post('/api/audiobooks/cast-check', json=duplicate)
        self.assertEqual(response.status_code, 400, response.text)
        invalid = await self.client.post('/api/audiobooks/cast-check', json={**self.body, 'chapter_index': 99})
        self.assertEqual(invalid.status_code, 404, invalid.text)
        forbidden = await self.client.post('/api/audiobooks/cast-check', json=self.body, headers={'Origin': 'https://outside.invalid'})
        self.assertEqual(forbidden.status_code, 403, forbidden.text)

    async def test_invalid_pronunciations_return_stable_input_errors(self) -> None:
        cases = [([{'written': 'x', 'spoken': 'y' * 200}], 'pronunciation_too_long'),
                 ([{'written': 'x', 'spoken': 'a'}, {'written': 'X', 'spoken': 'b'}], 'duplicate_pronunciation')]
        for pronunciations, code in cases:
            with self.subTest(code=code):
                response = await self.client.post('/api/audiobooks/cast-check', json={**self.body,
                    'chapters': [{'text': 'x ' * 1000}], 'pronunciations': pronunciations})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()['detail'], code)
