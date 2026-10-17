from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.module_contracts import ModuleComponentIdentity, ModuleLicenseDeclaration, ModulePlanRequest, ModuleInstallRequest


def license_payload(**overrides: object) -> dict[str, object]:
    return {'component_id': 'example', 'name': 'Example weights', 'scope': 'model',
        'status': 'unknown', 'notes': 'No applicable model terms observed.', 'reviewed_at': '2026-10-17', **overrides}


def evidence_payload(**overrides: object) -> dict[str, object]:
    return {'kind': 'license_file', 'source_url': 'https://example.org/LICENSE',
        'observed_at': '2026-10-17', 'reviewer': 'OpenFabric evidence review', **overrides}


class ModuleContractsTests(unittest.TestCase):
    def test_conditioning_selection_is_exclusive_and_preserves_legacy_default(self) -> None:
        self.assertIn('stable_conditioning_variant', ModulePlanRequest.model_fields)
        self.assertIsNone(ModulePlanRequest(features=['stable_audio3']).stable_conditioning_variant)
        for variant in ('small-music', 'medium'):
            value = ModulePlanRequest.model_validate({'features': ['stable_audio3'], 'download_models': True,
                'stable_conditioning_variant': variant})
            self.assertEqual(value.stable_conditioning_variant, variant)
        for changes in ({'download_models': False}, {'features': ['stable_audio3', 'media']},
                {'reinstall': ['stable_audio3']}, {'stable_generation_variant': 'medium'},
                {'stable_training_variant': 'small-music'}, {'music_training_engine': 'stable_audio3'}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                ModulePlanRequest.model_validate({'features': ['stable_audio3'], 'download_models': True,
                    'stable_conditioning_variant': 'small-music', **changes})

    def test_legacy_declarations_keep_unknown_evidence_defaults(self) -> None:
        for payload in (license_payload(), license_payload(status='declared', declared_license='MIT', source_url='https://example.org/LICENSE')):
            declaration = ModuleLicenseDeclaration.model_validate(payload).model_dump()
            self.assertEqual(declaration.get('restriction'), 'unknown')
            self.assertEqual(declaration.get('redistribution'), 'unknown')
            self.assertEqual(declaration.get('copyleft'), 'unknown')
            self.assertEqual(declaration.get('evidence'), [])
            self.assertEqual(declaration.get('identities'), [])

    def test_dataset_and_samples_scopes_do_not_imply_redistribution_rights(self) -> None:
        for scope in ('dataset', 'samples'):
            declaration = ModuleLicenseDeclaration.model_validate(license_payload(scope=scope))
            self.assertEqual(declaration.model_dump()['redistribution'], 'unknown')

    def test_observed_license_requires_a_public_https_source_reference(self) -> None:
        declaration = ModuleLicenseDeclaration.model_validate(license_payload(evidence=[evidence_payload()]))
        self.assertEqual(len(declaration.model_dump()['evidence']), 1)
        for url in (None, '', 'https://', 'https:///LICENSE', 'http://example.org/LICENSE',
                    'https://user:secret@example.org/LICENSE', 'https://example.org\\@evil.org/LICENSE',
                    'https://example.org/with space', 'https://example.org/line\nbreak'):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                ModuleLicenseDeclaration.model_validate(license_payload(evidence=[evidence_payload(source_url=url)]))

    def test_declaration_source_also_rejects_malformed_and_credential_urls(self) -> None:
        for url in ('https://', 'https:///LICENSE', 'https://user:secret@example.org/LICENSE', 'https://example.org\\@evil.org/LICENSE'):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                ModuleLicenseDeclaration.model_validate(license_payload(status='declared', declared_license='MIT', source_url=url))

    def test_identity_and_evidence_hashes_and_text_are_bounded(self) -> None:
        identity: dict[str, object] = {'repository_url': 'https://example.org/models', 'revision': 'a' * 40,
            'filename': 'weights/model.safetensors', 'sha256': 'b' * 64}
        record = ModuleLicenseDeclaration.model_validate(license_payload(identities=[identity],
            evidence=[evidence_payload(sha256='c' * 64, revision='v1.0')]))
        self.assertEqual(record.model_dump()['identities'], [identity])
        for bad in ('', 'g' * 64, 'A' * 64, 'b' * 63, 'b' * 65, float('nan'), float('inf')):
            for field in ('identities', 'evidence'):
                value = {**identity, 'sha256': bad} if field == 'identities' else evidence_payload(sha256=bad)
                with self.subTest(field=field, bad=bad), self.assertRaises(ValidationError):
                    ModuleLicenseDeclaration.model_validate(license_payload(**{field: [value]}))
        for values in ({'evidence': [evidence_payload()] * 21}, {'identities': [identity] * 41},
                       {'evidence': [evidence_payload(reviewer='x' * 121)]},
                       {'evidence': [evidence_payload(reviewer='   ')]},
                       {'evidence': [evidence_payload(revision='x' * 201)]},
                       {'evidence': [evidence_payload(observed_at='2026-02-30')]},
                       {'identities': [{}]}, {'identities': [{**identity, 'filename': '../model'}]},
                       {'identities': [{**identity, 'filename': '/private/model'}]},
                       {'identities': [{**identity, 'filename': 'C:\\private\\model'}]},
                       {'identities': [{**identity, 'repository_url': 'https://secret@example.org/models'}]},
                       {'evidence': [evidence_payload(kind='artifact_hash')]},
                       {'evidence': [evidence_payload(kind='unavailable', sha256='c' * 64)]},
                       {'evidence': [evidence_payload(extra='unsupported')]},
                       {'restriction': 'approved'}, {'redistribution': True}, {'copyleft': 'commercial_ban'}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                ModuleLicenseDeclaration.model_validate(license_payload(**values))

    def test_hash_length_bound_is_exported_to_browser_schema(self) -> None:
        schema = ModuleComponentIdentity.model_json_schema()
        hash_schema = schema['properties']['sha256']['anyOf'][0]
        self.assertEqual(hash_schema.get('minLength'), 64)
        self.assertEqual(hash_schema.get('maxLength'), 64)

    def test_unavailable_evidence_never_grants_permissions(self) -> None:
        record = ModuleLicenseDeclaration.model_validate(license_payload(evidence=[evidence_payload(kind='unavailable', source_url=None)]))
        self.assertEqual(record.model_dump()['restriction'], 'unknown')
        self.assertEqual(record.model_dump()['redistribution'], 'unknown')
        for status in ('unknown', 'declared'):
            payload = license_payload(status=status, evidence=[evidence_payload(kind='unavailable', source_url=None)])
            if status == 'declared':
                payload.update(declared_license='MIT', source_url='https://example.org/LICENSE')
            for field, value in (('restriction', 'unrestricted_declared'), ('redistribution', 'declared_allowed')):
                with self.subTest(status=status, field=field), self.assertRaises(ValidationError):
                    ModuleLicenseDeclaration.model_validate({**payload, field: value})

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
