"""Local music refuses foreign origins and never echoes prompts/errors."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
import httpx

from app.api.routes_local_music import router
from app.local_music_contracts import LocalMusicError


class LocalMusicApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        app = FastAPI()
        app.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:9000')

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_foreign_origin_is_refused_before_generation(self) -> None:
        with patch('app.local_music.submit', new=AsyncMock()) as submit:
            response = await self.client.post('/api/local-music/jobs', json={'engine': 'stable_audio3', 'prompt': 'Jazz', 'duration_seconds': 10}, headers={'origin': 'https://evil.example'})
        self.assertEqual(response.status_code, 403)
        submit.assert_not_awaited()

    async def test_bounded_invalid_request_does_not_echo_prompt(self) -> None:
        sentinel = 'private prompt content'
        response = await self.client.post('/api/local-music/jobs', json={'engine': 'stable_audio3', 'prompt': sentinel})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(sentinel, response.text)
        response = await self.client.post('/api/local-music/jobs', content=b' ' * 131073, headers={'content-type': 'application/json'})
        self.assertEqual(response.status_code, 413)

    async def test_errors_use_stable_codes_without_internal_details(self) -> None:
        with patch('app.local_music.get', side_effect=LocalMusicError('local_music_job_not_found', 404)):
            response = await self.client.get('/api/local-music/jobs/' + 'a' * 32)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {'detail': 'local_music_job_not_found'})
        with patch('app.local_music.list_jobs', side_effect=OSError('private filesystem error')):
            response = await self.client.get('/api/local-music/jobs')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private filesystem error', response.text)

    async def test_cancel_foreign_origin_does_not_interrupt_owned_job(self) -> None:
        with patch('app.local_music.cancel', new=AsyncMock()) as cancel:
            response = await self.client.post('/api/local-music/jobs/' + 'a' * 32 + '/cancel', headers={'origin': 'null'})
        self.assertEqual(response.status_code, 403)
        cancel.assert_not_awaited()
