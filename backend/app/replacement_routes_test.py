from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
import httpx

from app.api.routes_replacement import router
from app.replacement_contracts import ReplacementPlansResponse


class ReplacementRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        app = FastAPI()
        app.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1')
        self.addAsyncCleanup(self.client.aclose)

    async def test_untrusted_origin_and_oversized_json_never_reach_owner(self) -> None:
        with patch('app.replacement_jobs.create', new_callable=AsyncMock) as create:
            remote = await self.client.post('/api/voices/replace/plans', json={}, headers={'Origin': 'https://example.org'})
            self.assertEqual(remote.status_code, 403)
            large = await self.client.post('/api/voices/replace/plans', content=' ' * (65536 + 1), headers={'Content-Type': 'application/json'})
            self.assertEqual(large.status_code, 413)
            create.assert_not_awaited()

    async def test_source_filter_is_typed_and_no_public_path_is_accepted(self) -> None:
        source = {'kind': 'version', 'track_id': 2, 'version_id': 'a' * 32}
        with patch('app.replacement_jobs.list_plans', return_value=ReplacementPlansResponse()) as listing:
            response = await self.client.get('/api/voices/replace/plans', params={'source': json.dumps(source)})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(listing.call_args.kwargs['source'].track_id, 2)
            response = await self.client.get('/api/voices/replace/plans', params={'source': json.dumps({'path': '/etc/passwd'})})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(listing.call_count, 1)

    async def test_asset_intake_rejects_duplicate_fields_and_untrusted_origins_before_copy(self) -> None:
        with patch('app.replacement_jobs.import_asset', new_callable=AsyncMock) as upload:
            response = await self.client.post('/api/voices/replace/assets', files=[
                ('audio', ('one.wav', b'opaque', 'audio/wav')),
                ('audio', ('two.wav', b'opaque', 'audio/wav')),
                ('metadata', (None, '{}'))])
            self.assertEqual(response.status_code, 422)
            remote = await self.client.post('/api/voices/replace/assets', files={'audio': ('one.wav', b'opaque')},
                data={'metadata': '{}'}, headers={'Origin': 'https://example.org'})
            self.assertEqual(remote.status_code, 403)
            upload.assert_not_awaited()

    async def test_revision_conflicts_are_stable_and_do_not_expose_internal_errors(self) -> None:
        from app.replacement_jobs import ReplacementError
        with patch('app.replacement_jobs.prepare', new_callable=AsyncMock,
                side_effect=ReplacementError('replacement_revision_conflict', current_revision=3)):
            response = await self.client.post('/api/voices/replace/plans/' + 'a' * 32 + '/prepare',
                json={'expected_revision': 1, 'request_id': 'b' * 32})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json(), {'detail': {'code': 'replacement_revision_conflict', 'current_revision': 3}})
        with patch('app.replacement_jobs.prepare', new_callable=AsyncMock, side_effect=OSError('/private/secret')):
            response = await self.client.post('/api/voices/replace/plans/' + 'a' * 32 + '/prepare',
                json={'expected_revision': 1, 'request_id': 'b' * 32})
            self.assertEqual(response.json(), {'detail': 'replacement_storage_failed'})
