from __future__ import annotations

import unittest
from pydantic import ValidationError

from app.regional_evaluation_contracts import RegionalEvaluationRequest


class RegionalEvaluationContractTests(unittest.TestCase):
    def test_comparison_requires_exact_adapter_and_retained_manifest_without_conditioning(self) -> None:
        body = {'manifest_id': 'a' * 32, 'manifest_sha256': 'b' * 64, 'adapter_id': 'c' * 32,
            'adapter_sha256': 'd' * 64, 'submission_key': 'e' * 32,
            'request': {'engine': 'stable_audio3', 'prompt': 'Reviewed fixture phrase', 'duration_seconds': 5,
                'seed': 42, 'adapter': {'id': 'c' * 32, 'strength': 1}}}
        self.assertEqual(RegionalEvaluationRequest.model_validate(body).request.seed, 42)
        for changes in ({'adapter_sha256': 'd' * 64 + '\n'}, {'adapter_id': 'f' * 32},
                {'request': {**body['request'], 'adapter': None}}, {'path': '/private/source.wav'}):
            with self.assertRaises(ValidationError):
                RegionalEvaluationRequest.model_validate({**body, **changes})
