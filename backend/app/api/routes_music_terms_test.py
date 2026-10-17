from __future__ import annotations

from collections.abc import AsyncIterator
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
import httpx

from app import music_terms
from app.api.routes_music_terms import router, require_usage
from app.music_terms_contracts import MusicTermsSnapshot


class MusicTermsRouteTests(unittest.IsolatedAsyncioTestCase):
    def test_admission_storage_errors_are_stable_and_private(self) -> None:
        with patch.object(music_terms, 'require', side_effect=OSError('secret path')):
            with self.assertLogs('app.api.routes_music_terms', level='ERROR'):
                with self.assertRaises(HTTPException) as failure:
                    require_usage('yue2', 'generation')
        self.assertEqual(failure.exception.status_code, 503)
        self.assertEqual(failure.exception.detail, 'music_terms_unavailable')
    async def asyncSetUp(self) -> None:
        app = FastAPI()
        app.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:9000')
        self.addAsyncCleanup(self.client.aclose)
        self.state = MusicTermsSnapshot(engine='ace_step', operation='generation', revision=0,
            noncommercial_enabled=False, requirements=[], allowed=True)

    async def test_get_uses_validated_engine_and_scope(self) -> None:
        with patch.object(music_terms, 'snapshot', return_value=self.state) as read:
            response = await self.client.get('/api/music-terms?engine=ace_step&operation=generation')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.state.model_dump(mode='json'))
        read.assert_called_once_with('ace_step', 'generation')

    async def test_foreign_mutation_is_rejected_before_parsing(self) -> None:
        with patch.object(music_terms, 'accept') as accept:
            response = await self.client.post('/api/music-terms/accept', content=b'{private invalid',
                headers={'origin': 'https://example.invalid', 'content-type': 'application/json'})
        self.assertEqual(response.status_code, 403)
        accept.assert_not_called()

    async def test_chunked_body_without_valid_length_is_bounded(self) -> None:
        async def chunks() -> AsyncIterator[bytes]:
            for _ in range(3):
                yield b' ' * 32768
        with patch.object(music_terms, 'accept') as accept:
            response = await self.client.post('/api/music-terms/accept', content=chunks(),
                headers={'content-length': '0', 'content-type': 'application/json'})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json(), {'detail': 'music_terms_request_too_large'})
        accept.assert_not_called()

    async def test_invalid_request_does_not_echo_private_values(self) -> None:
        response = await self.client.post('/api/music-terms/accept', json={'private_value': 'secret-sentinel'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {'detail': 'music_terms_request_invalid'})
        self.assertNotIn('secret-sentinel', response.text)

    async def test_stable_conflict_and_storage_errors(self) -> None:
        for error, status, code in ((music_terms.MusicTermsError('music_terms_changed'), 409, 'music_terms_changed'),
                                    (OSError('private filesystem detail'), 503, 'music_terms_unavailable')):
            with self.subTest(error=type(error).__name__), patch.object(music_terms, 'snapshot', side_effect=error):
                response = await self.client.get('/api/music-terms?engine=ace_step&operation=generation')
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.json(), {'detail': code})


if __name__ == '__main__':
    unittest.main()
