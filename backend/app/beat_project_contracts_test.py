from __future__ import annotations

import unittest
from pydantic import ValidationError

from app.beat_project_contracts import EditorChannelSettings, RenderedProjectLane


class MusicProjectContractTests(unittest.TestCase):
    def test_house_ranges_and_retained_local_audio_boundary(self) -> None:
        for patch in ({'volume': 1.6}, {'pan': 1.1}, {'comp': {'threshold': -24, 'ratio': 0}},
                {'delay': {'enabled': False, 'time': .3, 'feedback': 1, 'mix': 0}}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                EditorChannelSettings.model_validate(patch)
        raw = {'lane_id': 'lane-0', 'label': 'Kick', 'artifact_id': 'a' * 32, 'sha256': 'b' * 64,
            'source_url': '/api/beats/renders/' + 'c' * 32 + '/artifacts/' + 'a' * 32,
            'trim_end_sec': 2}
        self.assertEqual(RenderedProjectLane.model_validate(raw).start_sec, 0)
        for patch in ({'source_url': 'https://example.invalid/a.wav'}, {'source_url': '/api/../private'},
                {'sha256': 'b' * 64 + '\n'}, {'trim_start_sec': 3}, {'start_sec': True}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                RenderedProjectLane.model_validate({**raw, **patch})
