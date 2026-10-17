"""Conditioned requests describe retained source identity, never filesystem selectors."""
from __future__ import annotations

import unittest

from pydantic import TypeAdapter, ValidationError

from app.music_conditioning_contracts import StableMusicConditioning
from app.local_music_contracts import LocalMusicRequest


class MusicConditioningContractTests(unittest.TestCase):
    def conditioning(self, **changes: object) -> dict[str, object]:
        return {'mode': 'audio_to_audio', 'source': {'kind': 'version', 'track_id': 1,
            'version_id': 'a' * 32}, 'source_sha256': 'b' * 64, 'noise_level': 0.6, **changes}

    def test_source_conditioning_rejects_full_noise_and_paths(self) -> None:
        adapter = TypeAdapter(StableMusicConditioning)
        self.assertEqual(adapter.validate_python(self.conditioning()).mode, 'audio_to_audio')
        for changes in ({'noise_level': 1.0}, {'noise_level': 0.9999}, {'noise_level': 0.0}, {'noise_level': float('nan')},
                        {'noise_level': True}, {'init_audio': '/private/source.wav'},
                        {'source_sha256': 'b' * 64 + '\n'}, {'source': {'kind': 'url', 'url': 'https://example.invalid'}}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                adapter.validate_python(self.conditioning(**changes))

    def test_inpaint_has_a_bounded_nonempty_region_and_crossfade_inside_it(self) -> None:
        adapter = TypeAdapter(StableMusicConditioning)
        value = self.conditioning(mode='inpaint', start_sec=1.0, end_sec=2.0, crossfade_sec=0.02)
        value.pop('noise_level')
        self.assertEqual(adapter.validate_python(value).mode, 'inpaint')
        for changes in ({'start_sec': 2.0}, {'end_sec': float('inf')}, {'crossfade_sec': 0.6},
                        {'start_sec': 1.99, 'crossfade_sec': 0.02}, {'noise_level': 1.0}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                adapter.validate_python({**value, **changes})

    def test_legacy_text_stays_default_and_conditioned_lyrics_or_outside_mask_refuse(self) -> None:
        old = LocalMusicRequest(engine='stable_audio3', prompt='Piano', duration_seconds=10)
        self.assertIsNone(old.conditioning)
        request = {'engine': 'stable_audio3', 'prompt': 'Piano', 'duration_seconds': 10,
            'conditioning': self.conditioning()}
        self.assertEqual(LocalMusicRequest.model_validate(request).conditioning.mode, 'audio_to_audio')
        for changes in ({'lyrics': 'Words'}, {'conditioning': {**self.conditioning(mode='inpaint'),
                'start_sec': 0.0, 'end_sec': 11.0}}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                LocalMusicRequest.model_validate({**request, **changes})

    def test_legacy_queue_payload_does_not_gain_an_immutable_null_field(self) -> None:
        legacy = {'engine': 'stable_audio3', 'title': '', 'prompt': 'Piano', 'lyrics': '',
            'settings_mode': 'manual', 'duration_seconds': 10, 'seed': 0, 'variant': 'small-music', 'adapter': None}
        self.assertEqual(LocalMusicRequest.model_validate(legacy).model_dump(), legacy)
