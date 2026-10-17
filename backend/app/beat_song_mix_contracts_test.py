"""The renderer accepts reviewed bounded maps, never guessed musical timing."""
from __future__ import annotations

import unittest
from pydantic import ValidationError

from app.beat_song_mix_contracts import BeatSongMixDraft, BeatSongMixStartRequest, SongSourceInput


def draft_fixture() -> BeatSongMixDraft:
    return BeatSongMixDraft(title='Layer', beat=SongSourceInput(source={'kind': 'version',
        'track_id': 1, 'version_id': 'a' * 32}, sha256='a' * 64, excerpt_end_sec=2),
        song=SongSourceInput(source={'kind': 'version', 'track_id': 2, 'version_id': 'b' * 32},
            sha256='b' * 64, excerpt_end_sec=2), project_bpm=120,
        project_meter={'numerator': 4, 'denominator': 4},
        anchors=[{'source_sec': 0, 'target_sec': 0}, {'source_sec': 2, 'target_sec': 2}])


class BeatSongContractsTest(unittest.TestCase):
    def test_explicit_manual_map_and_simple_layer_defaults(self) -> None:
        draft = draft_fixture()
        self.assertEqual((draft.mode, draft.tempo_strategy, draft.beat_repeats), ('layer', 'beat_to_song', 1))
        self.assertEqual(draft.bass_leader, 'none')
        self.assertEqual(draft.controls, [])

    def test_untrusted_maps_sources_and_limits_are_rejected(self) -> None:
        original = draft_fixture().model_dump()
        for update in ({'anchors': []}, {'anchors': [{'source_sec': 0, 'target_sec': 0},
                {'source_sec': 2, 'target_sec': 4}]}, {'project_bpm': 0}, {'beat_repeats': 129},
                {'beat_repeats': True}, {'anchors': [{'source_sec': 0, 'target_sec': 0},
                {'source_sec': 0, 'target_sec': 1}]}, {'audio_pitch_semitones': 13}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                BeatSongMixDraft.model_validate({**original, **update})
        for end in (0, 601, float('nan')):
            with self.subTest(end=end), self.assertRaises(ValidationError):
                SongSourceInput.model_validate({**original['beat'], 'excerpt_end_sec': end})

    def test_preview_requires_bounded_explicit_region(self) -> None:
        body = {'plan_id': 'c' * 32, 'expected_revision': 0, 'plan_sha256': 'd' * 64,
            'submission_key': 'render', 'mode': 'preview'}
        with self.assertRaises(ValidationError):
            BeatSongMixStartRequest.model_validate(body)
        request = BeatSongMixStartRequest.model_validate({**body, 'preview_start_sec': 0, 'preview_end_sec': 2})
        self.assertEqual(request.preview_end_sec, 2)
        with self.assertRaises(ValidationError):
            BeatSongMixStartRequest.model_validate({**body, 'preview_start_sec': 0, 'preview_end_sec': 61})
