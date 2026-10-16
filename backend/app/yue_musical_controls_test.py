"""Musical choices are app guidance, never invented native request options."""
from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.generation_contracts import YueGenerationSettings
from app.generation_library import snapshot_from_params
from app.yue_contracts import YueOptions


class YueMusicalControlsTests(unittest.TestCase):
    def test_accepts_typed_choices_and_retains_them_in_a_snapshot(self) -> None:
        choices = {'bpm': 88, 'key_scale': 'Am', 'time_signature': '6/8'}
        try:
            options = YueOptions.model_validate({'style': 'Folk', **choices})
        except ValidationError as error:
            self.fail(f'Musical choices were rejected: {error}')
        snapshot = snapshot_from_params('yue2', options.model_dump(mode='json'), 'Lyrics', 42)
        self.assertIsInstance(snapshot, YueGenerationSettings)
        self.assertEqual(snapshot.model_dump(include={'bpm', 'keyScale', 'timeSignature'}),
            {'bpm': 88, 'keyScale': 'Am', 'timeSignature': '6/8'})
        self.assertEqual(YueGenerationSettings.model_validate_json(snapshot.model_dump_json()), snapshot)

    def test_legacy_defaults_leave_the_music_unconstrained(self) -> None:
        defaults = {'bpm': None, 'keyScale': None, 'timeSignature': None}
        self.assertEqual(YueGenerationSettings(engine='yue2').model_dump(include=set(defaults)), defaults)

    def test_limits_and_unknown_keys_are_rejected(self) -> None:
        for field, value in (('bpm', 19), ('bpm', 301), ('bpm', 88.5), ('bpm', True),
                             ('key_scale', 'G# major'), ('key_scale', 'Ddor'),
                             ('time_signature', '999/4')):
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                YueOptions.model_validate({'style': 'Folk', field: value})

    def test_external_score_requires_choices_to_be_applied_first(self) -> None:
        for field, value in (('bpm', 88), ('key_scale', 'Am'), ('time_signature', '3/4')):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                YueOptions.model_validate({'style': 'Folk', 'cot': 'melody', 'abc': 'X:1\nK:C\nCDEF|', field: value})

    def test_blank_score_uses_guidance_without_sending_empty_external_abc(self) -> None:
        from app.yue_jobs import _native_options
        for blank in ('', ' ', '\n\t'):
            with self.subTest(blank=blank):
                try:
                    options = YueOptions.model_validate({'style': 'Folk', 'abc': blank, 'bpm': 88})
                except ValidationError as error:
                    self.fail(f'Blank ABC blocked automatic musical guidance: {error}')
                self.assertNotIn('abc', _native_options(options))
