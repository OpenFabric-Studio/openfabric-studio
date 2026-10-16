"""A retired engine is rejected for new work without losing saved audio."""
from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.contracts import SavedTrack
from app.local_music_contracts import LocalMusicRequest
from app.music_lora import capabilities
from app.music_lora_contracts import MusicAdapter
from app.music_queue_contracts import MusicQueueEngineSelection


class RemovedMusicEngineTests(unittest.TestCase):
    def test_retired_engine_cannot_submit_generation_or_queue_work(self) -> None:
        for model, value in (
            (LocalMusicRequest, {'engine': 'heartmula', 'prompt': 'Jazz', 'duration_seconds': 30}),
            (MusicQueueEngineSelection, {'engine': 'heartmula'}),
        ):
            with self.subTest(model=model.__name__), self.assertRaises(ValidationError):
                model.model_validate(value)

    def test_retired_engine_has_no_adapter_capability_or_import_target(self) -> None:
        self.assertEqual({item.engine for item in capabilities().engines}, {'ace_step', 'yue2', 'stable_audio3'})
        with self.assertRaises(ValidationError):
            MusicAdapter.model_validate({'id': 'a' * 32, 'engine': 'heartmula', 'target': 'heartmula-3b',
                'name': 'Old adapter', 'sha256': 'b' * 64, 'created_at': '2026-10-16'})

    def test_existing_audio_keeps_its_original_engine_provenance(self) -> None:
        track = SavedTrack.model_validate({'id': 1, 'short_id': 1, 'model': 'heartmula',
            'created_at': '2026-10-16', 'title': 'Saved song', 'lyrics': '', 'seed': 1,
            'duration_ms': 1000, 'wall_ms': None, 'params': {}, 'filename': 'saved.wav',
            'audio_url': '/api/tracks/1/audio', 'abc_url': None, 'stems': None, 'midi': None})
        self.assertEqual(track.model, 'heartmula')
        self.assertEqual(track.audio_url, '/api/tracks/1/audio')
