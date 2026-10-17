from __future__ import annotations

import unittest
import json
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
import httpx

from app import music_analysis
from app.api import routes_music_analysis
from app.music_analysis_contracts import MusicAnalysisJob, VersionMusicSource


class MusicAnalysisRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.enterContext(patch.object(music_analysis, '_closing', False))
        application = FastAPI()
        application.include_router(routes_music_analysis.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
            base_url='http://127.0.0.1:9000')
        self.addAsyncCleanup(self.client.aclose)
        self.source = {'kind': 'version', 'track_id': 1, 'version_id': 'a' * 32}
        self.job = MusicAnalysisJob(id='b' * 32, source=VersionMusicSource.model_validate(self.source),
            created_at='2026-10-17T00:00:00Z')

    async def test_new_analysis_is_durable_202_and_reads_do_not_submit(self) -> None:
        with patch.object(music_analysis, 'start', new=AsyncMock(return_value=self.job)) as submit, \
                patch.object(music_analysis, 'list_jobs', return_value=[self.job]), \
                patch.object(music_analysis, 'get', return_value=self.job):
            created = await self.client.post('/api/music/analysis', json={'source': self.source})
            self.assertEqual(created.status_code, 202, created.text)
            listed = await self.client.get('/api/music/analysis')
            self.assertEqual(listed.json()[0]['id'], self.job.id)
            loaded = await self.client.get('/api/music/analysis/' + self.job.id)
            self.assertEqual(loaded.status_code, 200)
            submit.assert_awaited_once()

    async def test_untrusted_sources_and_extra_fields_never_reach_owner(self) -> None:
        cases = [{'source': {'kind': 'path', 'path': '/private/token.wav'}},
            {'source': {**self.source, 'path': '../secret.wav'}},
            {'source': self.source, 'device': 'cuda'}, {'source': {**self.source, 'track_id': True}}]
        with patch.object(music_analysis, 'start', new=AsyncMock()) as submit:
            for body in cases:
                response = await self.client.post('/api/music/analysis', json=body)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json(), {'detail': 'analysis_request_invalid'})
            submit.assert_not_awaited()

    async def test_cross_origin_mutation_is_rejected_before_owner(self) -> None:
        with patch.object(music_analysis, 'start', new=AsyncMock()) as submit:
            response = await self.client.post('/api/music/analysis', json={'source': self.source},
                headers={'Origin': 'https://example.invalid'})
        self.assertEqual(response.status_code, 403)
        submit.assert_not_awaited()

    async def test_chunked_request_limit_applies_without_content_length(self) -> None:
        async def chunks():
            yield b'{"source":'
            yield b' ' * 131073
            yield b'null}'
        with patch.object(music_analysis, 'start', new=AsyncMock()) as submit:
            response = await self.client.post('/api/music/analysis', content=chunks())
        self.assertEqual(response.status_code, 413, response.text)
        submit.assert_not_awaited()

    async def test_cancel_retry_and_cas_errors_are_stable(self) -> None:
        for operation in ('cancel', 'retry'):
            with patch.object(music_analysis, operation, new=AsyncMock(return_value=self.job)) as boundary:
                response = await self.client.post(f'/api/music/analysis/{self.job.id}/{operation}')
                self.assertEqual(response.status_code, 200, response.text)
                boundary.assert_awaited_once_with(self.job.id)
        with patch.object(music_analysis, 'put_overrides', side_effect=music_analysis.MusicAnalysisError('analysis_revision_conflict')):
            response = await self.client.patch(f'/api/music/analysis/{self.job.id}/overrides',
                json={'analysis_id': self.job.id, 'expected_revision': 0, 'beats_sec': [.5]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {'detail': 'analysis_revision_conflict'})

    async def test_storage_failure_and_unknown_job_never_expose_internal_errors(self) -> None:
        for error, expected in ((OSError('/private/secret-token'), (503, 'analysis_storage_unavailable')),
                (music_analysis.MusicAnalysisError('analysis_job_not_found', 404), (404, 'analysis_job_not_found'))):
            with patch.object(music_analysis, 'get', side_effect=error):
                response = await self.client.get('/api/music/analysis/' + self.job.id)
            self.assertEqual(response.status_code, expected[0])
            self.assertEqual(response.json(), {'detail': expected[1]})

    async def test_unknown_production_asset_is_a_safe_missing_source(self) -> None:
        from app.artist_production import ProductionError
        body = {'source': {'kind': 'production_asset', 'asset_id': 'd' * 32, 'sha256': 'e' * 64}}
        with patch('app.artist_production.get_asset', side_effect=ProductionError('lineage_missing')):
            for path in ('/api/music/analysis/preview', '/api/music/analysis'):
                response = await self.client.post(path, json=body)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(response.json(), {'detail': 'source_missing'})

    async def test_preview_reads_budget_without_submitting_and_override_body_is_bounded(self) -> None:
        from app.music_analysis_contracts import MusicAnalysisStoragePreview
        estimate = MusicAnalysisStoragePreview(source_bytes=1, retained_bytes=1, temporary_bytes=1,
            required_free_bytes=1073741826, free_bytes=1, can_start=False)
        with patch.object(music_analysis, 'preview', new=AsyncMock(return_value=estimate)), \
                patch.object(music_analysis, 'start', new=AsyncMock()) as submit:
            response = await self.client.post('/api/music/analysis/preview', json={'source': self.source})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()['can_start'])
            submit.assert_not_awaited()
        with patch.object(music_analysis, 'put_overrides') as override:
            response = await self.client.patch(f'/api/music/analysis/{self.job.id}/overrides',
                content=b' ' * (2 * 1024**2 + 1))
        self.assertEqual(response.status_code, 413, response.text)
        override.assert_not_called()

    async def test_source_scoped_history_validates_bounded_shared_source_union(self) -> None:
        with patch.object(music_analysis, 'list_jobs', return_value=[self.job]) as listing:
            response = await self.client.get('/api/music/analysis', params={'source': json.dumps(self.source)})
            self.assertEqual(response.status_code, 200, response.text)
            listing.assert_called_once_with(VersionMusicSource.model_validate(self.source))
        with patch.object(music_analysis, 'list_jobs') as listing:
            for raw in ('x' * 2049, '{"kind":"path","path":"/secret"}', '{"track_id":1}'):
                response = await self.client.get('/api/music/analysis', params={'source': raw})
                self.assertEqual(response.status_code, 422, response.text)
            listing.assert_not_called()
