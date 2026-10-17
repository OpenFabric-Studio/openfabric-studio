from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.music_analysis_contracts import VersionMusicSource
from app.music_dsp_contracts import DspMixInput, DspMixRequest, DspTransformRequest, DuckSettings, EqBand, WarpAnchor


class MusicDspContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = VersionMusicSource(track_id=1, version_id='a' * 32)

    def transform(self, **changes: object) -> DspTransformRequest:
        return DspTransformRequest.model_validate({'source': self.source.model_dump(), 'source_sha256': 'b' * 64,
            'revision': 0, 'anchors': [{'source_sec': 1.0, 'target_sec': 2.0}, {'source_sec': 3.0, 'target_sec': 4.0}],
            'output_duration_sec': 2.0, **changes})

    def test_source_to_target_span_is_explicit_and_offsets_are_not_silently_padded(self) -> None:
        request = self.transform()
        self.assertEqual(request.anchors[0], WarpAnchor(source_sec=1.0, target_sec=2.0))
        self.assertEqual(request.output_duration_sec, 2.0)
        with self.assertRaises(ValidationError):
            self.transform(output_duration_sec=4.0)

    def test_anchors_must_be_forward_and_each_local_stretch_must_be_safe(self) -> None:
        for anchors in ([{'source_sec': 1.0, 'target_sec': 2.0}, {'source_sec': 1.0, 'target_sec': 4.0}],
                [{'source_sec': 1.0, 'target_sec': 4.0}, {'source_sec': 3.0, 'target_sec': 2.0}],
                [{'source_sec': 1.0, 'target_sec': 2.0}, {'source_sec': 2.0, 'target_sec': 4.0}]):
            with self.subTest(anchors=anchors), self.assertRaises(ValidationError):
                self.transform(anchors=anchors)

    def test_nonfinite_boolean_unbounded_and_path_inputs_are_rejected(self) -> None:
        for changes in ({'pitch_shift_semitones': float('nan')}, {'gain_db': float('inf')},
                {'revision': True}, {'pitch_shift_semitones': 12.01}, {'path': '/private/source.wav'},
                {'source': {'kind': 'path', 'path': '../source.wav'}}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.transform(**changes)

    def test_eq_boost_fade_duration_and_ratios_are_bounded(self) -> None:
        with self.assertRaises(ValidationError):
            self.transform(eq_bands=[EqBand(frequency_hz=100.0, gain_db=6.0), EqBand(frequency_hz=1000.0, gain_db=6.0)])
        with self.assertRaises(ValidationError):
            self.transform(fades={'fade_in_sec': 2.0, 'fade_out_sec': 2.0})
        with self.assertRaises(ValidationError):
            self.transform(min_duration_ratio=1.2, max_duration_ratio=.9)

    def test_mix_sidechains_must_exist_and_must_not_form_cycles(self) -> None:
        first = DspMixInput(id='c' * 32, source=self.source, source_sha256='b' * 64,
            duration_sec=2.0, duck=DuckSettings(sidechain_input_id='d' * 32))
        second = DspMixInput(id='d' * 32, source=self.source, source_sha256='b' * 64,
            duration_sec=2.0, duck=DuckSettings(sidechain_input_id='c' * 32))
        with self.assertRaises(ValidationError):
            DspMixRequest(revision=0, duration_sec=2.0, inputs=[first])
        with self.assertRaises(ValidationError):
            DspMixRequest(revision=0, duration_sec=2.0, inputs=[first, second])

    def test_mix_bounds_timeline_and_duplicate_identity(self) -> None:
        first = DspMixInput(id='c' * 32, source=self.source, source_sha256='b' * 64,
            start_sec=1.0, duration_sec=2.0)
        with self.assertRaises(ValidationError):
            DspMixRequest(revision=0, duration_sec=2.0, inputs=[first])
        with self.assertRaises(ValidationError):
            DspMixRequest(revision=0, duration_sec=3.0, inputs=[first, first])

    def test_mono_mix_rejects_pan_instead_of_silently_ignoring_it(self) -> None:
        item = DspMixInput(id='c' * 32, source=self.source, source_sha256='b' * 64,
            duration_sec=2.0, pan=.5)
        with self.assertRaises(ValidationError):
            DspMixRequest(revision=0, duration_sec=2.0, channels=1, inputs=[item])
