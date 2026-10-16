from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
import httpx
from app.api.routes_training_captions import router
from app.training_caption_contracts import TrainingCaptionJob, TrainingCaptionRequest, TrainingCaptionItem
from app.music_training_source_contracts import MusicLoraSource
from app.training_caption_runtime import CaptionError


class TrainingCaptionApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        app = FastAPI()
        app.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:9000')

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_foreign_origin_cannot_start_analysis(self) -> None:
        with patch('app.training_captions.submit', new=AsyncMock()) as submit:
            response = await self.client.post('/api/music-lora/captions/jobs', json={'samples': [{'track_id': 1}]},
                headers={'origin': 'https://foreign.example'})
        self.assertEqual(response.status_code, 403)
        submit.assert_not_awaited()

    async def test_invalid_and_oversized_requests_do_not_echo_private_input(self) -> None:
        response = await self.client.post('/api/music-lora/captions/jobs', json={'samples': [{'path': 'private sentinel'}]})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('private sentinel', response.text)
        response = await self.client.post('/api/music-lora/captions/jobs', content=b' ' * (128 * 1024 + 1),
            headers={'content-type': 'application/json'})
        self.assertEqual(response.status_code, 413)

    async def test_missing_job_and_native_failures_use_safe_codes(self) -> None:
        with patch('app.training_captions.get', side_effect=CaptionError('training_caption_job_not_found', 404)):
            response = await self.client.get('/api/music-lora/captions/jobs/' + 'a' * 32)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {'detail': 'training_caption_job_not_found'})

    async def test_success_returns_typed_saved_job(self) -> None:
        source = MusicLoraSource(track_id=1)
        job = TrainingCaptionJob(id='a' * 32, status='queued', request=TrainingCaptionRequest(samples=[source]),
            items=[TrainingCaptionItem(source=source)], created_at='2026-10-16', updated_at='2026-10-16')
        with patch('app.training_captions.submit', new=AsyncMock(return_value=job)):
            response = await self.client.post('/api/music-lora/captions/jobs', json={'samples': [{'track_id': 1}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['id'], job.id)
