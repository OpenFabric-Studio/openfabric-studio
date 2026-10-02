"""Exercise app response schemas through HTTP without starting model engines."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from app import db, voice_build, video_jobs
from app.main import app


class ContractHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch.object(db, '_db', None), patch.object(db, 'DATA_DIR', root), patch.object(db, 'DB_PATH', root/'catalog.db'), patch.object(db, 'FILES_DIR', root/'files'), patch.object(db, 'VIDEOS_DIR', root/'videos'), patch.object(voice_build, 'VOICES_DIR', root/'voices')]
        for item in self.patches: item.start()
        self.connection = db.get_db()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        self.connection.close()
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    async def test_voice_creation_upload_and_status_match_wire_contract(self):
        response = await self.client.post('/api/voices', json={'name': 'Singer'})
        self.assertEqual(response.status_code, 200, response.text)
        voice_id = response.json()['id']
        response = await self.client.post(f'/api/voices/{voice_id}/recordings', files=[('files', ('song.wav', b'RIFFtest', 'audio/wav'))])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['voice']['recordings'][0]['filename'], 'song.wav')
        response = await self.client.get('/api/voices')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['voices']), 1)
        response = await self.client.get('/api/voices/apply/100')
        self.assertEqual(response.json()['status'], 'idle')

    async def test_track_and_project_crud_preserve_generated_contract_fields(self):
        response = await self.client.post('/api/tracks/upload', files={'audio': ('song.wav', b'RIFFtest', 'audio/wav')})
        self.assertEqual(response.status_code, 200, response.text)
        track_id = response.json()['id']
        self.assertIn('short_id', response.json())
        response = await self.client.get('/api/tracks')
        self.assertEqual(response.json()['data'][0]['id'], track_id)
        response = await self.client.get(f'/api/tracks/{track_id}/stems/status')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('stems', response.json())
        response = await self.client.get(f'/api/tracks/{track_id}/midi/status')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(set(response.json()['sources']), {'full', 'vocals', 'drums', 'bass', 'other'})
        response = await self.client.post('/api/projects', json={'name': 'Editor', 'data': {'version': 1, 'lanes': []}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['version'], 1)
        response = await self.client.get('/api/orchestrator/status')
        self.assertEqual(response.status_code, 200, response.text)
        response = await self.client.get('/api/orchestrator/config')
        self.assertEqual(response.status_code, 200, response.text)

    async def test_track_params_fail_at_boundary_instead_of_becoming_empty(self):
        for params in ('[1,2]', '{"nested":[{"number":NaN}]}'):
            response = await self.client.post('/api/tracks', data={'model': 'ace_step', 'params': params}, files={'audio': ('song.wav', b'audio', 'audio/wav')})
            self.assertEqual(response.status_code, 422)
        self.assertEqual(db.list_tracks(), [])

    async def test_invalid_track_numbers_are_rejected_before_persistence(self):
        for field, value in [('duration_ms', 'inf'), ('duration_ms', '-1'), ('wall_ms', 'nan'), ('seed', '9'*100)]:
            with self.subTest(field=field, value=value):
                response = await self.client.post('/api/tracks', data={'model': 'ace_step', field: value}, files={'audio': ('song.wav', b'audio', 'audio/wav')})
                self.assertEqual(response.status_code, 422)
        self.assertEqual(db.list_tracks(), [])

    async def test_video_public_view_validates_with_actual_serializer(self):
        from app.client_contracts import VideoJobResponse
        job = video_jobs.VideoJob(id='a'*32, track_id=1, title='Song', prompt='Prompt', seconds=4, start_sec=0, frames=97, seed=1, created_at='2026-10-01T00:00:00Z')
        payload = video_jobs.public_view(video_jobs._payload(job))
        self.assertEqual(VideoJobResponse.model_validate(payload).status, 'queued')
