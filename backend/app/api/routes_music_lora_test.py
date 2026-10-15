from __future__ import annotations

import asyncio
import threading
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from typing import BinaryIO

from fastapi import FastAPI
import httpx

from app import music_lora
from app.api.routes_music_lora import router
from app.music_lora_test import adapter_bytes
from app.music_lora_contracts import MusicAdapter


class MusicLoraApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.patch = patch.object(music_lora.db, 'DATA_DIR', Path(self.temporary.name))
        self.patch.start(); self.addCleanup(self.patch.stop)
        app = FastAPI(); app.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:9000')
        self.addAsyncCleanup(self.client.aclose)

    async def upload(self, headers: dict[str, str] | None = None) -> httpx.Response:
        return await self.client.post('/api/music-lora/adapters',
            data={'engine': 'stable_audio3', 'target': 'small-music', 'name': 'Style'},
            files={'file': ('style.safetensors', adapter_bytes(), 'application/octet-stream')}, headers=headers)

    async def test_import_list_and_export_never_expose_local_paths(self) -> None:
        response = await self.upload()
        self.assertEqual(response.status_code, 200, response.text)
        identifier = response.json()['id']
        self.assertNotIn(self.temporary.name, response.text)
        listing = await self.client.get('/api/music-lora/adapters')
        self.assertEqual(listing.json()['adapters'][0]['id'], identifier)
        exported = await self.client.get(f'/api/music-lora/adapters/{identifier}/file')
        self.assertEqual(exported.content, adapter_bytes())
        self.assertIn('.safetensors', exported.headers['content-disposition'])

    async def test_foreign_origin_refused_before_upload(self) -> None:
        response = await self.upload({'origin': 'https://evil.example'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(music_lora.list_adapters().adapters, [])

    async def test_queue_reservation_blocks_adapter_import_and_allows_registry_reads(self) -> None:
        from app import music_queue_ownership as ownership
        with patch.object(ownership, '_owner', None), patch.object(ownership, '_resources_idle', new=AsyncMock(return_value=True)):
            owner = await ownership.reserve()
            response = await self.upload()
            self.assertEqual(response.status_code, 409)
            self.assertIn('music_queue_busy', response.text)
            self.assertEqual((await self.client.get('/api/music-lora/adapters')).status_code, 200)
            self.assertEqual(music_lora.list_adapters().adapters, [])
            self.assertTrue(await owner.release())

    async def test_formats_errors_and_missing_ids_are_safe(self) -> None:
        response = await self.client.post('/api/music-lora/adapters', data={'engine': 'yue2', 'target': 'ar', 'name': 'private name'}, files={'file': ('private.pt', b'pickle')})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('private', response.text)
        response = await self.client.get('/api/music-lora/adapters/bad/file')
        self.assertEqual(response.status_code, 404)
        with patch.object(music_lora, 'list_adapters', side_effect=OSError('private path')):
            response = await self.client.get('/api/music-lora/adapters')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private', response.text)

    async def test_capabilities_identify_integrated_experimental_trainers(self) -> None:
        with patch.object(music_lora, 'YUE2_LORA_AVAILABLE', True):
            response = await self.client.get('/api/music-lora/capabilities')
        capabilities = {item['engine']: item for item in response.json()['engines']}
        self.assertTrue(capabilities['stable_audio3']['training'])
        self.assertTrue(capabilities['yue2']['loading'])
        self.assertTrue(capabilities['yue2']['training'])
        self.assertTrue(capabilities['heartmula']['loading'])
        self.assertTrue(capabilities['heartmula']['training'])
        self.assertEqual(capabilities['yue2']['reason'], 'experimental')
        self.assertEqual(capabilities['heartmula']['reason'], 'experimental')

    async def test_upload_cancellation_drains_import_before_closing_input(self) -> None:
        started, released = threading.Event(), threading.Event()
        original = music_lora.import_stream
        def delayed(stream: BinaryIO, *, filename: str, name: str, engine: str, target: str, source_job_id: str | None = None) -> MusicAdapter:
            started.set()
            if not released.wait(2):
                raise TimeoutError('test import did not resume')
            return original(stream, filename=filename, name=name, engine=engine, target=target, source_job_id=source_job_id)
        with patch.object(music_lora, 'import_stream', side_effect=delayed):
            task = asyncio.create_task(self.upload())
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            task.cancel()
            await asyncio.sleep(0.03)
            drained = not task.done()
            released.set()
            await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(drained, 'Import must own its file until the filesystem work drains')
        self.assertEqual(len(music_lora.list_adapters().adapters), 1)

    async def test_streamed_request_bound_precedes_multipart_parser(self) -> None:
        with patch('app.api.routes_music_lora.MAX_BODY_BYTES', 16):
            response = await self.upload()
        self.assertEqual(response.status_code, 413)
