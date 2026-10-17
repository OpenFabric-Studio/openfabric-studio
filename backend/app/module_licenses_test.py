from __future__ import annotations

import unittest
from typing import get_args

from app.module_contracts import ModuleId
from app.module_licenses import declarations
from app.module_music_models import ModelManifest, manifest_path
from app.module_native_setup import MODEL_REVISIONS


class ModuleLicensesTests(unittest.TestCase):
    def test_native_weight_restrictions_are_separate_from_engine_code(self) -> None:
        records = {record.component_id: record for record in declarations('yue2')}
        self.assertEqual(records['audio-cpp'].restriction, 'unrestricted_declared')
        for identifier, repository in (
            ('yue2_main_q8_0', 'audio-cpp/Yue2-3B-GGUF'),
            ('yue2_main_q4_0', 'audio-cpp/Yue2-3B-GGUF'),
            ('yue2_vae_f16', 'audio-cpp/Yue2-3B-GGUF'),
            ('sheetsage2_orig', 'audio-cpp/SheetSage2-GGUF'),
            ('muscriptor_small_f32', 'audio-cpp/audio.cpp-gguf'),
        ):
            with self.subTest(identifier=identifier):
                record = records[identifier]
                self.assertEqual(record.restriction, 'noncommercial')
                self.assertEqual(record.redistribution, 'unknown')
                self.assertEqual(record.identities[0].revision, MODEL_REVISIONS[repository])
                self.assertIsNotNone(record.identities[0].sha256)
                self.assertTrue(record.evidence)
        self.assertEqual(records['native-source-rights'].restriction, 'unknown')

    def test_stable_weights_and_encoder_keep_their_own_conditional_terms(self) -> None:
        records = {record.component_id: record for record in declarations('stable_audio3')}
        self.assertEqual(records['stable-audio3-code'].restriction, 'unrestricted_declared')
        for identifier, license_name in (('stable-audio3-optimized', 'Stability AI Community License'),
                                         ('stable-audio3-t5gemma', 'Gemma Terms of Use')):
            with self.subTest(identifier=identifier):
                record = records[identifier]
                self.assertEqual(record.restriction, 'conditional')
                self.assertEqual(record.declared_license, license_name)
                self.assertEqual(record.redistribution, 'unknown')
                self.assertTrue(any(item.kind == 'publisher_terms' for item in record.evidence))
                self.assertEqual({item.revision for item in record.identities}, {
                    '08b17423649b5714b22eab20f96bf00751f2358a', '08c64b96b1e59942aade69759f60fb88c58c90c4'})
        self.assertEqual(records['stable-audio3-local-bundles'].status, 'unknown')
        self.assertEqual(records['stable-audio3-local-bundles'].restriction, 'unknown')

    def test_music_payload_evidence_covers_current_manifest_pins(self) -> None:
        manifest = ModelManifest.model_validate_json(manifest_path().read_bytes())
        identities = {
            (identity.repository_url, identity.revision, identity.filename, identity.sha256)
            for module in ('ace_step', 'stable_audio3')
            for record in declarations(module)
            for identity in record.identities
        }
        for bundle, artifacts in manifest.bundles.items():
            for artifact in artifacts:
                if not artifact.path.endswith(('.safetensors', '.npz')):
                    continue
                repository, revision_and_filename = artifact.url.split('/resolve/', 1)
                revision, filename = revision_and_filename.split('/', 1)
                with self.subTest(bundle=bundle, filename=filename):
                    self.assertIn((repository, revision, filename, artifact.sha256), identities)

    def test_ace_encoder_and_source_rights_are_separate_declarations(self) -> None:
        records = {record.component_id: record for record in declarations('ace_step')}
        self.assertEqual(records['ace-step-code'].declared_license, 'MIT')
        self.assertEqual(records['ace-step-qwen3-embedding'].declared_license, 'Apache-2.0')
        self.assertEqual(records['ace-step-qwen3-embedding'].scope, 'auxiliary')
        self.assertEqual(records['ace-source-rights'].status, 'unknown')
        self.assertEqual(records['ace-source-rights'].restriction, 'unknown')
        self.assertEqual(records['ace-local-checkpoints'].restriction, 'unknown')

    def test_pinned_sheetsage_identity_keeps_the_publisher_readme_discrepancy_visible(self) -> None:
        record = next(record for record in declarations('yue2') if record.component_id == 'sheetsage2_orig')
        self.assertEqual(record.identities[0].sha256, 'd858bfdf5ac7be16261ac6ee38754d6fca502149131b44a5262b4039fb72876e')
        self.assertIn('README', record.notes)
        self.assertIn('52bb5846', record.notes)

    def test_returned_evidence_is_independent_and_retired_engine_stays_absent(self) -> None:
        self.assertNotIn('heartmula', get_args(ModuleId))
        record = next(record for record in declarations('yue2') if record.component_id == 'yue2_main_q8_0')
        record.identities[0].sha256 = '0' * 64
        record.evidence.clear()
        fresh = next(record for record in declarations('yue2') if record.component_id == 'yue2_main_q8_0')
        self.assertNotEqual(fresh.identities[0].sha256, '0' * 64)
        self.assertTrue(fresh.evidence)


if __name__ == '__main__':
    unittest.main()
