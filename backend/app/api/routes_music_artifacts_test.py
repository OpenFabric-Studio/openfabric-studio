from __future__ import annotations

import unittest
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
import httpx

from app import music_artifacts
from app.api import routes_music_artifacts
from app.music_artifact_contracts import MusicArtifactInventory


class MusicArtifactRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        application = FastAPI()
        application.include_router(routes_music_artifacts.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=application, raise_app_exceptions=False), base_url='http://127.0.0.1:9000')
        self.addAsyncCleanup(self.client.aclose)

    async def test_inventory_is_read_only_and_origin_is_required_for_mutation(self) -> None:
        response = MusicArtifactInventory(generated_at='2026-10-17T00:00:00Z', groups=[], scan_complete=True)
        with patch.object(music_artifacts, 'inventory', new=AsyncMock(return_value=response)) as inventory, \
                patch.object(music_artifacts, 'create_plan', new=AsyncMock()) as plan:
            received = await self.client.get('/api/music/artifacts')
            self.assertEqual(received.status_code, 200)
            self.assertEqual(received.json()['groups'], [])
            inventory.assert_awaited_once()
            plan.assert_not_awaited()
            received = await self.client.post('/api/music/artifacts/cleanup-plans', json={'analysis_job_ids': ['a' * 32]}, headers={'Origin': 'https://example.invalid'})
            self.assertEqual(received.status_code, 403)
            plan.assert_not_awaited()

    async def test_untrusted_selection_and_confirmation_are_rejected_without_cleanup(self) -> None:
        with patch.object(music_artifacts, 'confirm_plan', new=AsyncMock()) as confirm, \
                patch.object(music_artifacts, 'create_plan', new=AsyncMock()) as plan:
            for body in ({'analysis_job_ids': []}, {'analysis_job_ids': ['a' * 32], 'path': '/private/source'}):
                received = await self.client.post('/api/music/artifacts/cleanup-plans', json=body)
                self.assertEqual(received.status_code, 422)
                self.assertEqual(received.json(), {'detail': 'music_artifact_invalid'})
            received = await self.client.post('/api/music/artifacts/cleanup-plans/' + 'a' * 32 + '/confirm', json={'plan_sha256': 'b' * 64, 'confirm_cleanup': 1})
            self.assertEqual(received.status_code, 422)
            plan.assert_not_awaited()
            confirm.assert_not_awaited()

    async def test_chunked_limits_and_storage_errors_do_not_expose_paths(self) -> None:
        async def chunks() -> AsyncIterator[bytes]:
            yield b'{"analysis_job_ids":'
            yield b' ' * 131073
            yield b'[]}'
        with patch.object(music_artifacts, 'create_plan', new=AsyncMock()) as plan:
            received = await self.client.post('/api/music/artifacts/cleanup-plans', content=chunks())
            self.assertEqual(received.status_code, 413)
            plan.assert_not_awaited()
        with patch.object(music_artifacts, 'get_plan', side_effect=OSError('/private/secret')):
            received = await self.client.get('/api/music/artifacts/cleanup-plans/' + 'a' * 32)
        self.assertEqual(received.status_code, 503)
        self.assertEqual(received.json(), {'detail': 'music_artifact_storage_unavailable'})
