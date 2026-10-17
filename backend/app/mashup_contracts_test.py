from __future__ import annotations

import unittest
from pydantic import ValidationError

from app.mashup_contracts import (MashupCreateRequest, MashupGeneratePlanRequest,
    MashupSourceInput, MashupTiming, MashupTimingSection)


class MashupContractsTest(unittest.TestCase):
    def source(self, track: int = 1) -> MashupSourceInput:
        return MashupSourceInput(source={'kind': 'version', 'track_id': track, 'version_id': str(track) * 32},
            sha256='a' * 64, excerpt_start_sec=0, excerpt_end_sec=8)

    def test_sources_are_distinct_immutable_versions_and_bounded_excerpts(self) -> None:
        with self.assertRaises(ValidationError):
            MashupCreateRequest(title='Pair', submission_key='test', source_a=self.source(), source_b=self.source())
        good = self.source().model_dump()
        for changes in ({'excerpt_end_sec': 601}, {'excerpt_start_sec': 9}, {'sha256': 'a' * 64 + '\n'},
                        {'path': '/private/file.wav'}, {'excerpt_end_sec': float('nan')}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                MashupSourceInput.model_validate({**good, **changes})
        MashupCreateRequest(title='Pair', submission_key='test', source_a=self.source(), source_b=self.source(2))

    def test_manual_sections_use_ordered_reviewed_boundaries(self) -> None:
        timing = MashupTiming(bpm=120, meter={'numerator': 4, 'denominator': 4},
            downbeat_frames=[0, 96000, 192000], sections=[MashupTimingSection(id='A', start_frame=0,
                end_frame=192000, bars=2)])
        for changes in ({'downbeat_frames': [0, 192000, 96000]}, {'downbeat_frames': [0, 192000]},
                        {'sections': [{'id': 'A', 'start_frame': 1, 'end_frame': 192000, 'bars': 2}]}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                MashupTiming.model_validate({**timing.model_dump(), **changes})

    def test_pitch_override_is_explicit_and_target_clock_is_bounded(self) -> None:
        request = {'expected_revision': 0, 'orientation': 'a_vocals_b_backing', 'target_bpm': 120,
            'meter': {'numerator': 4, 'denominator': 4}, 'vocal_pitch_semitones': 4}
        with self.assertRaises(ValidationError):
            MashupGeneratePlanRequest.model_validate(request)
        MashupGeneratePlanRequest.model_validate({**request, 'allow_large_pitch_shift': True})
        with self.assertRaises(ValidationError):
            MashupGeneratePlanRequest.model_validate({**request, 'allow_large_pitch_shift': True, 'target_bpm': 301})


if __name__ == '__main__':
    unittest.main()
