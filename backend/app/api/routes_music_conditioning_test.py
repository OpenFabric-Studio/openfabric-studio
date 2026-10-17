"""Conditioning preflight never starts a model or accepts a filesystem path."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
import httpx

from app.api.routes_music_conditioning import router
from app.local_music_contracts import LocalMusicError
from app.music_conditioning_contracts import MusicConditioningCapabilities, MusicConditioningCapability


class MusicConditioningRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        app = FastAPI(); app.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8010')

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_capabilities_are_read_only_and_do_not_claim_runtime_evidence(self) -> None:
        capabilities = MusicConditioningCapabilities(modes=[MusicConditioningCapability(engine='stable_audio3',
            mode='audio_to_audio', proof_status='source_verified', enabled_small=False, enabled_medium=False,
            output_semantics='generated_full_mix', reason='conditioning_encoder_missing', notes='Source reviewed, runtime unverified.')])
        with patch('app.music_conditioning.capabilities', return_value=capabilities), patch('app.local_music.submit', new=AsyncMock()) as submit:
            response = await self.client.get('/api/music-conditioning/capabilities')
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()['modes'][0]['runtime_verified'], False)
        submit.assert_not_awaited()

    async def test_preview_rejects_paths_and_cross_origin_before_source_io(self) -> None:
        value = {'engine': 'stable_audio3', 'prompt': 'Piano', 'duration_seconds': 5,
            'conditioning': {'mode': 'audio_to_audio', 'source': {'kind': 'version', 'track_id': 1,
                'version_id': 'a' * 32}, 'source_sha256': 'b' * 64, 'noise_level': .6}}
        with patch('app.music_conditioning.preview', new=AsyncMock()) as preview:
            invalid = await self.client.post('/api/music-conditioning/preview', json={**value, 'init_audio': '/private/source.wav'})
            rejected = await self.client.post('/api/music-conditioning/preview', json=value, headers={'origin': 'https://evil.invalid'})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(rejected.status_code, 403)
        preview.assert_not_awaited()

    async def test_source_failure_is_a_stable_code_without_private_diagnostics(self) -> None:
        value = {'engine': 'stable_audio3', 'prompt': 'Piano', 'duration_seconds': 5,
            'conditioning': {'mode': 'audio_to_audio', 'source': {'kind': 'version', 'track_id': 1,
                'version_id': 'a' * 32}, 'source_sha256': 'b' * 64, 'noise_level': .6}}
        with patch('app.music_conditioning.preview', new=AsyncMock(side_effect=LocalMusicError('conditioning_source_changed'))):
            response = await self.client.post('/api/music-conditioning/preview', json=value)
        self.assertEqual((response.status_code, response.json()), (409, {'detail': 'conditioning_source_changed'}))

    async def test_source_inspection_is_path_free_and_returns_a_hash_without_a_generation_request(self) -> None:
        source = {'kind': 'version', 'track_id': 1, 'version_id': 'a' * 32}
        result = {'source': source, 'source_sha256': 'b' * 64, 'source_bytes': 1000,
            'source_rate_hz': 44100, 'source_channels': 2, 'source_frames': 220500, 'duration_sec': 5.0}
        with patch('app.music_conditioning.inspect_source', new=AsyncMock(return_value=result), create=True) as inspect:
            response = await self.client.post('/api/music-conditioning/source', json={'source': source})
            invalid = await self.client.post('/api/music-conditioning/source', json={'source': source, 'path': '/private.wav'})
            rejected = await self.client.post('/api/music-conditioning/source', json={'source': source},
                headers={'origin': 'https://evil.invalid'})
        self.assertEqual((response.status_code, response.json()), (200, result))
        self.assertEqual((invalid.status_code, rejected.status_code), (422, 403))
        inspect.assert_awaited_once()
