from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.module_contracts import ModulePlanRequest, ModuleInstallRequest


class ModuleContractsTests(unittest.TestCase):
    def test_catalog_ids_and_download_consent_are_strict(self) -> None:
        for payload in ({'features': ['shell']}, {'features': ['speech'], 'download_models': 'yes'},
                        {'features': ['speech'], 'destination': '/tmp/elsewhere'},
                        {'features': ['speech', 'speech']}, {'features': []}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                ModulePlanRequest.model_validate(payload)
        valid = ModulePlanRequest(features=['speech'], download_models=False)
        self.assertEqual(valid.features, ['speech'])

    def test_install_requires_the_reviewed_plan_token(self) -> None:
        with self.assertRaises(ValidationError):
            ModuleInstallRequest(features=['speech'])


if __name__ == '__main__':
    unittest.main()
