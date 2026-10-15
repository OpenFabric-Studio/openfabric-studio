from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
import httpx
from app.api.routes_stable_lora import router
from app.music_lora_contracts import MusicLoraError


class StableLoraApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        app = FastAPI()
        app.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:9000')

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_foreign_origin_rejected_before_training(self) -> None:
        with patch('app.stable_lora.submit', new=AsyncMock()) as submit:
            response = await self.client.post('/api/music-lora/stable-training/jobs', json={}, headers={'origin': 'https://evil.example'})
        self.assertEqual(response.status_code, 403)
        submit.assert_not_awaited()

    async def test_invalid_bounded_request_does_not_echo_captions(self) -> None:
        response = await self.client.post('/api/music-lora/stable-training/jobs', json={'name': 'private sentinel caption'})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('private sentinel caption', response.text)
        response = await self.client.post('/api/music-lora/stable-training/jobs', content=b' ' * (2 * 1024 * 1024 + 1), headers={'content-type': 'application/json'})
        self.assertEqual(response.status_code, 413)

    async def test_missing_job_returns_typed_safe_code(self) -> None:
        with patch('app.stable_lora.get', side_effect=MusicLoraError('stable_lora_job_not_found', 404)):
            response = await self.client.get('/api/music-lora/stable-training/jobs/' + 'a' * 32)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {'detail': 'stable_lora_job_not_found'})
