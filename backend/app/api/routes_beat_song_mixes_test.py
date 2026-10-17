from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_beat_song_mixes import router
from app.beat_song_mix_contracts import BeatSongMixError
from app.beat_song_mix_contracts_test import draft_fixture


class BeatSongRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app, base_url='http://127.0.0.1:9000')

    def test_invalid_body_large_request_and_foreign_origin_do_not_start_work(self) -> None:
        with patch('app.api.routes_beat_song_mixes.beat_song_mix.start', new=AsyncMock()) as start:
            response = self.client.post('/api/beat-song-mixes', json={'path': '../private.wav'})
            self.assertEqual((response.status_code, response.json()['detail']), (422, 'beat_mix_invalid'))
            response = self.client.post('/api/beat-song-mixes', content='x' * (2 * 1024**2 + 1), headers={'content-type': 'application/json'})
            self.assertEqual(response.status_code, 413)
            response = self.client.post('/api/beat-song-mixes', json={}, headers={'origin': 'https://foreign.example'})
            self.assertEqual(response.status_code, 403)
            start.assert_not_awaited()

    def test_saved_history_is_read_only_and_errors_are_stable(self) -> None:
        with patch('app.api.routes_beat_song_mixes.beat_song_mix.list_jobs', return_value=[]), \
                patch('app.api.routes_beat_song_mixes.beat_song_mix.start', new=AsyncMock()) as start:
            self.assertEqual(self.client.get('/api/beat-song-mixes').json(), {'jobs': []})
            start.assert_not_awaited()
        with patch('app.api.routes_beat_song_mixes.beat_song_mix.preview', new=AsyncMock(side_effect=BeatSongMixError('song_source_changed'))):
            response = self.client.post('/api/beat-song-mixes/plans', json=draft_fixture().model_dump(mode='json'))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {'detail': 'song_source_changed', 'error': {'code': 'song_source_changed'}})
