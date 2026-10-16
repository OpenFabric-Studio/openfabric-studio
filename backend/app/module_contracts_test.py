from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.module_contracts import ModulePlanRequest, ModuleInstallRequest


class ModuleContractsTests(unittest.TestCase):
    def test_removed_music_engine_cannot_be_installed_or_reinstalled(self) -> None:
        for payload in ({'features': ['heartmula']}, {'features': ['stable_audio3'], 'reinstall': ['heartmula']}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                ModulePlanRequest.model_validate(payload)

    def test_generation_preparation_requires_one_variant_and_download_only_scope(self) -> None:
        self.assertIn('stable_generation_variant', ModulePlanRequest.model_fields)
        for variant in ('small-music', 'medium'):
            valid = ModulePlanRequest.model_validate({'features': ['stable_audio3'], 'download_models': True,
                'stable_generation_variant': variant})
            self.assertEqual(valid.model_dump().get('stable_generation_variant'), variant)
        for overrides in ({'features': ['speech']}, {'features': ['stable_audio3', 'media']},
                          {'download_models': False}, {'reinstall': ['stable_audio3']},
                          {'stable_training_variant': 'medium'}, {'music_training_engine': 'yue2'},
                          {'stable_generation_variant': 'small-sfx'}):
            payload: dict[str, object] = {'features': ['stable_audio3'], 'download_models': True,
                'stable_generation_variant': 'medium', **overrides}
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                ModulePlanRequest.model_validate(payload)

    def test_music_training_preparation_has_one_explicit_engine_and_download_scope(self) -> None:
        self.assertIn('music_training_engine', ModulePlanRequest.model_fields)
        for engine in ('yue2',):
            valid = ModulePlanRequest.model_validate({'features': [engine], 'download_models': True, 'music_training_engine': engine})
            self.assertEqual(valid.model_dump()['music_training_engine'], engine)
        for overrides in ({'features': ['stable_audio3']}, {'features': ['yue2', 'media']}, {'download_models': False},
                          {'reinstall': ['yue2']}, {'stable_training_variant': 'small-music'}, {'music_training_engine': 'ace_step'}):
            payload: dict[str, object] = {'features': ['yue2'], 'download_models': True, 'music_training_engine': 'yue2', **overrides}
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                ModulePlanRequest.model_validate(payload)

    def test_training_preparation_requires_explicit_variant_and_download_scope(self) -> None:
        self.assertIn('stable_training_variant', ModulePlanRequest.model_fields)
        valid = ModulePlanRequest.model_validate({'features': ['stable_audio3'], 'download_models': True, 'stable_training_variant': 'small-music'})
        self.assertEqual(valid.model_dump()['stable_training_variant'], 'small-music')
        for overrides in ({'features': ['speech']}, {'features': ['stable_audio3', 'media']},
                          {'download_models': False}, {'reinstall': ['stable_audio3']},
                          {'stable_training_variant': 'small-sfx'}):
            payload: dict[str, object] = {'features': ['stable_audio3'], 'download_models': True, 'stable_training_variant': 'small-music', **overrides}
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                ModulePlanRequest.model_validate(payload)

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

    def test_reinstallation_defaults_off_and_is_limited_to_unique_selected_features(self) -> None:
        self.assertEqual(ModulePlanRequest(features=['speech']).model_dump().get('reinstall'), [])
        for payload in ({'features': ['speech'], 'reinstall': ['singing']},
                        {'features': ['speech'], 'reinstall': ['speech', 'speech']},
                        {'features': ['speech'], 'reinstall': True}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                ModulePlanRequest.model_validate(payload)
        valid = ModulePlanRequest.model_validate({'features': ['speech'], 'reinstall': ['speech']})
        self.assertEqual(valid.model_dump().get('reinstall'), ['speech'])


if __name__ == '__main__':
    unittest.main()
