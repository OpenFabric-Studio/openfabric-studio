"""All local app routes enforce the browser boundary before consuming bodies."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from starlette.types import Message

from app import main, stems
from app.api import routes_stems, routes_orchestrator, routes_videos
from app.video_jobs import VideoJobError
from app.video_projects import VideoProjectError


class LocalAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.audio = Path(temporary) / 'fixture.wav'
        self.audio.write_bytes(b'owned audio fixture')
        self.start = self.enterContext(patch.object(stems, 'start', new=AsyncMock(return_value=stems.StemJob(status='queued'))))
        self.enterContext(patch.object(routes_stems.db, 'get_track', return_value={'audio_path': str(self.audio)}))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False), base_url='http://127.0.0.1:9000')
        self.addAsyncCleanup(self.client.aclose)

    async def test_foreign_origin_form_cannot_start_legacy_stem_job(self) -> None:
        response = await self.client.post('/api/tracks/1/stems?force=true', headers={
            'origin': 'https://outside.example', 'sec-fetch-site': 'cross-site',
            'content-type': 'application/x-www-form-urlencoded',
        }, content=b'')
        self.assertEqual(response.status_code, 403, response.text)
        self.start.assert_not_awaited()

    async def test_foreign_host_cannot_switch_engines_or_read_private_routes(self) -> None:
        switch = self.enterContext(patch.object(routes_orchestrator.manager, 'switch_to', new=AsyncMock()))
        headers = {'host': 'attacker.example:9000', 'origin': 'http://attacker.example:9000', 'sec-fetch-site': 'same-origin'}
        for method, path in [('POST', '/api/orchestrator/switch'), ('GET', '/api/orchestrator/status'), ('GET', '/api/openrouter/requests')]:
            with self.subTest(method=method, path=path):
                response = await self.client.request(method, path, headers=headers, json={'model': 'ace_step'} if method == 'POST' else None)
                self.assertEqual(response.status_code, 403, response.text)
        switch.assert_not_awaited()

    async def test_same_origin_and_native_clients_can_start_jobs(self) -> None:
        for headers in ({}, {'origin': 'http://127.0.0.1:9000', 'sec-fetch-site': 'same-origin'}):
            with self.subTest(headers=headers):
                response = await self.client.post('/api/tracks/1/stems', headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.start.await_count, 2)

    async def test_remote_client_cannot_spoof_localhost_headers(self) -> None:
        transport = httpx.ASGITransport(app=main.app, client=('198.51.100.9', 1234), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:9000') as remote:
            response = await remote.post('/api/tracks/1/stems', headers={'host': '127.0.0.1:9000'})
        self.assertEqual(response.status_code, 403)
        self.start.assert_not_awaited()

    async def test_explicit_loopback_development_origin_remains_supported(self) -> None:
        with patch.dict('os.environ', {'OPENFABRIC_SETUP_ALLOWED_ORIGINS': 'http://localhost:5173'}):
            response = await self.client.post('/api/tracks/1/stems', headers={
                'origin': 'http://localhost:5173', 'sec-fetch-site': 'same-site',
            })
        self.assertEqual(response.status_code, 200, response.text)

    async def test_node_shutdown_request_needs_its_matching_origin(self) -> None:
        # Node fetch sends Sec-Fetch-Mode without the browser's Origin header.
        with patch.object(routes_orchestrator.manager.state, 'active_model', None):
            for origin, status in [(None, 403), ('http://127.0.0.1:9000', 200), ('http://localhost:9001', 403)]:
                with self.subTest(origin=origin):
                    headers = {'sec-fetch-mode': 'cors'}
                    if origin is not None:
                        headers['origin'] = origin
                    response = await self.client.post('/api/orchestrator/stop', headers=headers)
                    self.assertEqual(response.status_code, status, response.text)

    async def test_untrusted_upload_is_rejected_before_body_consumption(self) -> None:
        messages: list[Message] = []
        reads = 0

        async def receive() -> Message:
            nonlocal reads
            reads += 1
            return {'type': 'http.request', 'body': b'invalid multipart data', 'more_body': False}

        async def send(message: Message) -> None:
            messages.append(message)

        await main.app({
            'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
            'method': 'POST', 'scheme': 'http', 'path': '/api/tracks/upload',
            'raw_path': b'/api/tracks/upload', 'query_string': b'',
            'headers': [(b'host', b'127.0.0.1:9000'), (b'origin', b'https://outside.example'), (b'content-type', b'multipart/form-data; boundary=fixture')],
            'server': ('127.0.0.1', 9000), 'client': ('127.0.0.1', 12345),
        }, receive, send)
        self.assertEqual(reads, 0)
        self.assertEqual(messages[0].get('status'), 403)

    async def test_loopback_hosts_and_ipv6_are_allowed(self) -> None:
        for host in ('localhost:9000', '127.0.0.1:9000', '[::1]:9000'):
            with self.subTest(host=host):
                response = await self.client.get('/api/orchestrator/status', headers={'host': host})
                self.assertEqual(response.status_code, 200, response.text)

    async def test_ambiguous_host_headers_are_rejected(self) -> None:
        response = await self.client.get('/api/orchestrator/status', headers=[('host', '127.0.0.1:9000'), ('host', 'attacker.example')])
        self.assertEqual(response.status_code, 403, response.text)

    async def test_unsafe_video_roots_return_structured_errors_from_lists(self) -> None:
        for path, target, error in [
            ('/api/videos', 'list_videos', VideoJobError('not_found')),
            ('/api/videos/projects', 'projects.list_projects', VideoProjectError('not_found')),
        ]:
            with self.subTest(path=path):
                if target == 'list_videos':
                    boundary = patch.object(routes_videos, 'list_videos', side_effect=error)
                else:
                    boundary = patch.object(routes_videos.projects, 'list_projects', side_effect=error)
                with boundary:
                    response = await self.client.get(path)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(response.json(), {'detail': 'not_found'})
