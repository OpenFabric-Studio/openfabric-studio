"""The generator preserves strict tagged unions instead of dropping constraints."""
from __future__ import annotations

import json
import unittest
from pydantic import TypeAdapter
from scripts.generate_contracts import generate, runtime_schema, type_of
from app.contracts import JsonObject


class ContractGenerationTest(unittest.TestCase):
    def test_browser_schemas_omit_annotations_without_dropping_named_fields(self) -> None:
        generated = generate()
        prefix = 'const schemas = '
        encoded = generated.split(prefix, 1)[1].split(' as const', 1)[0]
        schemas = TypeAdapter(JsonObject).validate_python(json.loads(encoded))
        self.assertFalse(any('title' in self._object(schema) for schema in schemas.values()))
        title_fields = [self._object(self._object(schema).get('properties', {}))['title']
                        for schema in schemas.values()
                        if 'title' in self._object(self._object(schema).get('properties', {}))]
        self.assertGreater(len(title_fields), 0)
        self.assertTrue(all('type' in self._object(field) or 'anyOf' in self._object(field) for field in title_fields))

    @staticmethod
    def _object(value: object) -> JsonObject:
        return TypeAdapter(JsonObject).validate_python(value)

    def test_runtime_schema_preserves_constraints_and_annotation_named_data(self) -> None:
        schema: JsonObject = {
            'type': 'object', 'title': 'Unused heading', 'description': 'Unused prose', 'default': {},
            'required': ['title'], 'additionalProperties': False,
            'properties': {
                'title': {'type': 'string', 'title': 'Heading', 'default': '', 'minLength': 1},
                'description': {'type': 'array', 'items': {'type': 'number', 'minimum': 0, 'title': 'Item'}},
                'default': {'anyOf': [{'type': 'null', 'default': None}, {'type': 'object',
                    'additionalProperties': {'oneOf': [{'type': 'string', 'description': 'Text'},
                        {'enum': ['title', 'description', 'default']}]}}]},
            },
            '$defs': {'title': {'type': 'string', 'pattern': '^ok$', 'title': 'Pattern'}},
        }
        expected: JsonObject = {
            'type': 'object', 'required': ['title'], 'additionalProperties': False,
            'properties': {
                'title': {'type': 'string', 'minLength': 1},
                'description': {'type': 'array', 'items': {'type': 'number', 'minimum': 0}},
                'default': {'anyOf': [{'type': 'null'}, {'type': 'object',
                    'additionalProperties': {'oneOf': [{'type': 'string'},
                        {'enum': ['title', 'description', 'default']}]}}]},
            },
            '$defs': {'title': {'type': 'string', 'pattern': '^ok$'}},
        }
        self.assertEqual(runtime_schema(schema), expected)
        self.assertEqual(schema['title'], 'Unused heading')
        self.assertEqual(runtime_schema({'const': {'title': 'data', 'default': 2}}),
                         {'const': {'title': 'data', 'default': 2}})

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
