"""The generator preserves strict tagged unions instead of dropping constraints."""
from __future__ import annotations

import unittest
from scripts.generate_contracts import type_of
from app.contracts import JsonObject


class ContractGenerationTest(unittest.TestCase):
    def test_discriminated_union_and_nullable_wrapper(self) -> None:
        schema: JsonObject = {
            'oneOf': [{'$ref': '#/$defs/Local'}, {'$ref': '#/$defs/Cloud'}],
            'discriminator': {'propertyName': 'provider', 'mapping': {
                'local': '#/$defs/Local', 'openrouter': '#/$defs/Cloud',
            }},
        }
        tagged = '((Local | Cloud) & { "provider": "local" | "openrouter" })'
        self.assertEqual(type_of(schema), tagged)
        self.assertEqual(type_of({'anyOf': [schema, {'type': 'null'}]}), f'({tagged} | null)')

    def test_unsupported_constraints_still_fail(self) -> None:
        with self.assertRaises(ValueError):
            type_of({'type': 'string', 'format': 'secret-unvalidated-format'})

    def test_discriminator_without_union_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            type_of({'type': 'object', 'discriminator': {'propertyName': 'provider'}})

    def test_invalid_discriminator_metadata_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            type_of({'oneOf': [{'type': 'string'}, {'type': 'null'}],
                     'discriminator': {'propertyName': 5}})
