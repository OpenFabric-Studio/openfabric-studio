"""Regional candidates remain bounded, path free and explicitly unreviewed."""
from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.beat_preset_contracts import (BeatPresetCompileRequest, BeatPresetScale,
    BeatPresetTuning, BeatRhythmDefinition)


class BeatPresetContractTests(unittest.TestCase):
    def test_compile_request_requires_owned_id_and_rejects_paths_and_coercions(self) -> None:
        value = {'preset_id': 'lofi_kyoto', 'kit_id': 'private-compatible-fixture', 'document_id': 'a' * 32, 'seed': 4471}
        self.assertEqual(BeatPresetCompileRequest.model_validate(value).seed, 4471)
        for changes in ({'document_id': 'a' * 32 + '\n'}, {'preset_id': '../koto'},
                        {'seed': True}, {'bpm': float('nan')}, {'tonic_midi': 128}, {'path': '/private'}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                BeatPresetCompileRequest.model_validate({**value, **changes})

    def test_candidate_scale_is_ordered_and_variant_notes_are_required(self) -> None:
        value = {'id': 'jp_in_report', 'title': 'In candidate', 'region': 'japan',
            'degrees_cents': [0.0, 100.0, 500.0, 700.0, 800.0], 'notes': 'Report 6.2 candidate, not universal.'}
        self.assertEqual(BeatPresetScale.model_validate(value).review_status, 'unreviewed')
        for changes in ({'degrees_cents': [0.0, 500.0, 100.0]}, {'degrees_cents': [0.0, 1200.0]},
                        {'degrees_cents': [0.0, float('nan')]}, {'notes': ''}, {'review_status': 'reviewed'}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                BeatPresetScale.model_validate({**value, **changes})

    def test_unmeasured_tuning_cannot_smuggle_equal_temperament_as_measurement(self) -> None:
        value = {'id': 'slendro_pack', 'title': 'Sample-specific slendro required',
            'kind': 'sample_specific_required', 'notes': 'No ensemble tuning measured.'}
        self.assertIsNone(BeatPresetTuning.model_validate(value).degrees_cents)
        with self.assertRaises(ValidationError):
            BeatPresetTuning.model_validate({**value, 'degrees_cents': [0.0, 240.0, 480.0, 720.0, 960.0]})

    def test_rhythm_grouping_markers_and_grid_are_coherent(self) -> None:
        value = {'id': 'in_teentaal_fusion', 'title': 'Teentaal fusion candidate', 'region': 'india',
            'meter': {'numerator': 4, 'denominator': 4}, 'bars': 4, 'steps_per_quarter': 4,
            'grouping_steps': [16, 16, 16, 16], 'markers': [{'index': 0, 'label': 'sam'},
                {'index': 32, 'label': 'khali'}], 'bpm_min': 70.0, 'bpm_max': 120.0,
            'notes': 'Report 6.3 transcription candidate; no musician review.'}
        self.assertEqual(BeatRhythmDefinition.model_validate(value).bars, 4)
        for changes in ({'grouping_steps': [16, 16]}, {'markers': [{'index': 64, 'label': 'sam'}]},
                        {'bpm_min': 130.0}, {'steps_per_quarter': 5}, {'region': 'elsewhere'}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                BeatRhythmDefinition.model_validate({**value, **changes})
