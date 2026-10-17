"""Conditioning selectors cannot silently become ordinary text generation."""
from __future__ import annotations

import unittest

from pydantic import TypeAdapter, ValidationError

from app.generation_contracts import AceGenerationSettings
from app.music_queue_contracts import MusicQueueAceRequest, MusicQueueEngineSelection
from app.song_from_beat_contracts import AceMusicConditioning


class SongFromBeatContractTests(unittest.TestCase):
    def test_exact_roles_and_bound_source_are_required(self) -> None:
        adapter = TypeAdapter(AceMusicConditioning)
        body = {'mode': 'ace_complete', 'source': {'kind': 'beat_render', 'beat_id': 'a' * 32,
            'beat_revision': 0, 'render_id': 'b' * 32, 'sha256': 'c' * 64},
            'source_sha256': 'c' * 64, 'track_classes': ['bass', 'guitar']}
        self.assertEqual(adapter.validate_python(body).mode, 'ace_complete')
        for update in ({'track_classes': []}, {'track_classes': ['bass', 'bass']},
                {'track_classes': ['regional_authenticity']}, {'source_sha256': 'd' * 64},
                {'path': '/private/raw.wav'}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                adapter.validate_python({**body, **update})
        lego = {'mode': 'ace_lego', 'source': {'kind': 'version', 'track_id': 1,
            'version_id': 'a' * 32}, 'source_sha256': 'b' * 64, 'track_name': 'vocals'}
        self.assertEqual(adapter.validate_python(lego).mode, 'ace_lego')
        for engine in ('stable_audio3', 'yue2'):
            with self.subTest(engine=engine), self.assertRaises(ValidationError):
                MusicQueueEngineSelection.model_validate({'engine': engine, 'conditioning': lego})
        self.assertEqual(MusicQueueEngineSelection.model_validate({'engine': 'ace_step',
            'conditioning': lego}).conditioning.mode, 'ace_lego')

    def test_old_text_payload_serialization_is_unchanged(self) -> None:
        settings = AceGenerationSettings(engine='ace_step', duration=10)
        old = '{"engine":"ace_step","request":' + settings.model_dump_json() + '}'
        restored = MusicQueueAceRequest.model_validate_json(old)
        self.assertEqual(restored.model_dump_json(), old)
        self.assertNotIn('conditioning', restored.model_dump(mode='json'))
