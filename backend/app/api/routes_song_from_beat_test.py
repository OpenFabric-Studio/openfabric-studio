"""Capability reads never enqueue, initialize, or download a model."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
import httpx

from app import song_capabilities
from app.api.routes_song_from_beat import router


class SongFromBeatRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def test_capabilities_are_path_free_and_keep_execution_unverified(self) -> None:
        app = FastAPI(); app.include_router(router)
        state = song_capabilities.capabilities()
        with patch('app.song_capabilities.capabilities', return_value=state), \
             patch('app.ace_jobs.submit', new=AsyncMock()) as submit, \
             patch('app.song_capabilities.verify_runtime', new=AsyncMock()) as initialize:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
                response = await client.get('/api/song-from-beat/capabilities')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), state.model_dump(mode='json'))
        self.assertTrue(all(row['runtime_verified'] is False for row in response.json()['modes']))
        self.assertNotIn('checkpoints/', response.text)
        submit.assert_not_awaited()
        initialize.assert_not_awaited()
