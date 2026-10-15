"""Request and response contract regressions at the native model boundary."""
from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.ace_lora_contracts import AceLoraLoadRequest, AceLoraScaleRequest, AceLoraToggleRequest


class AceLoraContractTests(unittest.TestCase):
    def test_load_name_and_path_are_bounded_and_unknown_fields_rejected(self) -> None:
        for payload in ({'lora_path': 'x' * 4097}, {'lora_path': ''},
                        {'lora_path': 'adapter', 'adapter_name': 'x' * 501},
                        {'lora_path': 'adapter', 'delete_files': True}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                AceLoraLoadRequest.model_validate(payload)
        self.assertEqual(AceLoraLoadRequest(lora_path='adapter', adapter_name='named').adapter_name, 'named')

    def test_scale_and_toggle_cannot_coerce_untrusted_values(self) -> None:
        for value in (-0.1, 1.1, float('inf'), float('nan'), '0.5', True):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                AceLoraScaleRequest.model_validate({'scale': value})
        for value in ('false', 0, 1):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                AceLoraToggleRequest.model_validate({'use_lora': value})
        self.assertEqual(AceLoraScaleRequest(scale=0.75).scale, 0.75)
