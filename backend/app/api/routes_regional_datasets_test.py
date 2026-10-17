from __future__ import annotations

from collections.abc import AsyncIterator
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
import httpx

from app import regional_datasets as service
from app.api import routes_regional_datasets as routes
from app.regional_dataset_contracts import RegionalRecordingInspection
from app.regional_acoustic_contracts import RegionalAcousticJob, RegionalAcousticScanRequest


class RegionalDatasetRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        application = FastAPI(); application.include_router(routes.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=application, raise_app_exceptions=False), base_url='http://127.0.0.1:9000')
        self.addAsyncCleanup(self.client.aclose)

    async def test_inspection_accepts_retained_ids_and_never_public_paths(self) -> None:
        body = {'recording_id': 'a' * 32, 'source': {'dataset_id': 'b' * 32, 'revision': 0, 'sample_id': 'sample'}}
        result = RegionalRecordingInspection(recording_id='a' * 32, source_sha256='b' * 64,
            decoded_pcm_sha256='c' * 64, source_bytes=400, frames=80, sample_rate_hz=8000, channels=1)
        with patch.object(service, 'inspect_recording', new=AsyncMock(return_value=result)) as inspected:
            received = await self.client.post('/api/music/regional-datasets/inspect', json=body)
            self.assertEqual(received.status_code, 200)
            self.assertEqual(received.json()['source_sha256'], result.source_sha256)
            inspected.assert_awaited_once()
            received = await self.client.post('/api/music/regional-datasets/inspect', json={**body, 'path': '/private/file'})
            self.assertEqual(received.status_code, 422)
            received = await self.client.post('/api/music/regional-datasets/inspect', json=body, headers={'Origin': 'https://example.invalid'})
            self.assertEqual(received.status_code, 403)
            self.assertEqual(inspected.await_count, 1)

    async def test_acoustic_scan_routes_accept_saved_review_only_and_preserve_origin_boundary(self) -> None:
        body = {'dataset_id': 'a' * 32, 'review_revision': 1, 'review_sha256': 'b' * 64, 'submission_key': 'c' * 32}
        job = RegionalAcousticJob(id='d' * 32, request=RegionalAcousticScanRequest.model_validate(body), status='queued',
            created_at='2026-10-17T00:00:00Z')
        with patch.object(service, 'start_acoustic_scan', new=AsyncMock(return_value=job)) as started:
            result = await self.client.post('/api/music/regional-datasets/acoustic-scans', json=body)
            self.assertEqual(result.status_code, 202); self.assertEqual(result.json()['id'], job.id)
            self.assertEqual((await self.client.post('/api/music/regional-datasets/acoustic-scans', json={**body, 'path': '/private/audio.wav'})).status_code, 422)
            self.assertEqual((await self.client.post('/api/music/regional-datasets/acoustic-scans', json=body,
                headers={'Origin': 'https://example.invalid'})).status_code, 403)
            started.assert_awaited_once()
        with patch.object(service, 'get_acoustic_scan', return_value=job), \
                patch.object(service, 'cancel_acoustic_scan', new=AsyncMock(return_value=job)) as cancelled:
            self.assertEqual((await self.client.get('/api/music/regional-datasets/acoustic-scans/' + job.id)).status_code, 200)
            self.assertEqual((await self.client.post('/api/music/regional-datasets/acoustic-scans/' + job.id + '/cancel', json={})).status_code, 200)
            cancelled.assert_awaited_once_with(job.id)

    async def test_retained_acoustic_audition_supports_range_without_native_work(self) -> None:
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.wav'; path.write_bytes(b'RIFF' + b'fixture' * 100)
            with patch.object(service, 'acoustic_audition', new=AsyncMock(return_value=path)) as audition:
                url = '/api/music/regional-datasets/acoustic-scans/' + 'a' * 32 + '/candidates/' + 'b' * 64 + '/audio/left'
                response = await self.client.get(url, headers={'Range': 'bytes=0-3'})
                self.assertEqual(response.status_code, 206); self.assertEqual(response.content, b'RIFF')
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                audition.assert_awaited_once_with('a' * 32, 'b' * 64, 'left')
                self.assertEqual((await self.client.get(url.replace('/left', '/other'))).status_code, 422)

    async def test_limits_ids_and_errors_do_not_expose_private_evidence(self) -> None:
        async def chunks() -> AsyncIterator[bytes]:
            yield b'{"source":'
            yield b' ' * (8 * 1024**2 + 1)
            yield b'{}}'
        with patch.object(service, 'inspect_recording', new=AsyncMock()) as inspected:
            response = await self.client.post('/api/music/regional-datasets/inspect', content=chunks())
            self.assertEqual(response.status_code, 413); inspected.assert_not_awaited()
        with patch.object(service, 'get', side_effect=OSError('/private/contract.pdf')):
            response = await self.client.get('/api/music/regional-datasets/preparations/' + 'a' * 32)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'detail': 'regional_dataset_storage_unavailable'})
        response = await self.client.get('/api/music/regional-datasets/preparations/not-an-id')
        self.assertEqual(response.status_code, 422)
