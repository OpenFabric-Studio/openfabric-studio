"""HTTP boundary tests exercise typed request and stable public errors."""
from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import db, reference_imports
from app.api.routes_references import router
from app.api.routes_tracks import router as tracks_router
from app.reference_tools import score_pitches


class ReferenceRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        application = FastAPI()
        application.include_router(router)
        application.include_router(tracks_router)
        self.client = self.enterContext(TestClient(application))

    def test_abc_route_preserves_musical_pitch(self) -> None:
        response = self.client.post('/api/references/tools/abc', json={'abc': 'X:1\nK:G\nF =F F | F', 'transpose_semitones': 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(score_pitches(response.json()['abc']), [68, 67, 67, 68])

    def test_request_validation_is_bounded_and_stable(self) -> None:
        for body in [{'abc': 'X:1\nK:C\nC', 'transpose_semitones': True}, {'abc': 'X:1\nK:C\nC', 'tempo_bpm': 900}, {'abc': 'C', 'extra': 'rejected'}]:
            response = self.client.post('/api/references/tools/abc', json=body)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json(), {'detail': 'invalid_reference_request'})
        self.assertEqual(self.client.get('/api/references/imports/not-an-id').status_code, 422)

    def test_unsupported_music_returns_stable_error(self) -> None:
        response = self.client.post('/api/references/tools/abc', json={'abc': 'X:1\nK:C\n(3CDE'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {'detail': 'unsupported_abc'})

    def test_internal_storage_details_are_never_returned(self) -> None:
        with patch.object(reference_imports, 'list_imports', side_effect=OSError('secret/local/path')):
            response = self.client.get('/api/references/imports')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'detail': 'import_failed'})

    def test_lyrics_route_preserves_language_and_source_time(self) -> None:
        response = self.client.post('/api/references/tools/lyrics', json={'text': '1\n00:00:01,000 --> 00:00:02,000\nOriginal', 'format': 'srt', 'language': 'fr'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['lines'][0]['language'], 'fr')
        self.assertEqual(response.json()['lines'][0]['start_seconds'], 1)

    def test_unsupported_provider_never_starts_network(self) -> None:
        response = self.client.post('/api/references/probe', json={'url': 'https://evil.test/watch?v=abcdefghijk'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {'detail': 'provider_not_allowed'})

    def test_track_delete_busy_guard_returns_conflict_and_preserves_source(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, value in [('DATA_DIR', root), ('DB_PATH', root / 'catalog.db'), ('FILES_DIR', root / 'files'), ('_db', None)]:
            self.enterContext(patch.object(db, name, value))
        source = db.model_dir('upload') / 'source.wav'
        source.write_bytes(b'preserved original')
        track = db.insert_track(model='upload', title='Source', lyrics='', seed=None, duration_ms=1000, wall_ms=None, params={}, audio_path=source, abc_path=None)
        @asynccontextmanager
        async def busy(_track_id: int) -> AsyncIterator[None]:
            raise reference_imports.ReferenceImportError('busy')
            yield
        try:
            with patch.object(reference_imports, 'protect_track_removal', side_effect=busy):
                response = self.client.delete(f'/api/tracks/{track}')
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json(), {'detail': 'busy'})
            self.assertIsNotNone(db.get_track(track))
            self.assertEqual(source.read_bytes(), b'preserved original')
        finally:
            if db._db is not None:
                db._db.close()
