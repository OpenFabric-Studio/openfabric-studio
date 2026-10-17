from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.replacement_diagnostics_contracts import ReplacementDiagnostics


class ReplacementDiagnosticsContractTests(unittest.TestCase):
    def test_unavailable_measurement_is_explicit_and_never_an_empty_success(self) -> None:
        body = dict(revision=1, request_sha256='a' * 64, attempt_id='b' * 32,
            created_at='2026-10-17T10:00:00Z')
        with self.assertRaises(ValidationError):
            ReplacementDiagnostics.model_validate(body)
        result = ReplacementDiagnostics.model_validate({**body,
            'unavailable': ['pitch', 'tonal']})
        self.assertIsNone(result.pitch)
        self.assertIsNone(result.tonal)
        for values in (['pitch'], ['tonal'], ['pitch', 'pitch', 'tonal']):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                ReplacementDiagnostics.model_validate({**body, 'unavailable': values})
