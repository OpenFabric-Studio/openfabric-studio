from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app import db, rvc_models
from app.replacement_contracts import RvcModelImport


class RvcRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        for name, value in [('DATA_DIR', self.root), ('DB_PATH', self.root / 'catalog.db'),
                ('FILES_DIR', self.root / 'files'), ('_db', None)]:
            self.enterContext(patch.object(db, name, value))
        db.FILES_DIR.mkdir()
        self.voice = self.root / 'voice'
        self.voice.mkdir()
        (self.voice / 'voice.json').write_text('{"status":"new"}')
        self.enterContext(patch('app.voice_build.voice_dir', return_value=self.voice))
        self.enterContext(patch('app.voice_authorization.authorize', return_value=None))
        self.metadata = {'voice_id': 'a' * 32, 'label': 'Self-trained fixture',
            'evidence_ref': 'Local training manifest', 'rights_declaration': 'I recorded and control the source performance.',
            'trusted_local_checkpoint': True, 'pickle_execution_acknowledged': True, 'declared_terms_acknowledged': True,
            'weights_license': {'component_id': 'local-model', 'name': 'Local voice', 'scope': 'model', 'status': 'unknown',
                'notes': 'No publisher license established.', 'reviewed_at': '2026-10-17'}}

    def tearDown(self) -> None:
        if db._db is not None:
            db._db.close()

    def test_import_is_opaque_and_bound_to_real_voice_without_seed_ready_requirement(self) -> None:
        request = RvcModelImport.model_validate(self.metadata)
        opaque = b'this is deliberately not a valid pickle or torch checkpoint'
        with patch.object(rvc_models, 'observe_runtime', side_effect=rvc_models.RvcModelError('replacement_engine_unavailable')):
            model = rvc_models.import_model(request, BytesIO(opaque), checkpoint_name='voice.pth')
            self.assertEqual(model.checkpoint_sha256, hashlib.sha256(opaque).hexdigest())
            self.assertEqual(model.voice_id, request.voice_id)
            self.assertFalse(model.ready)
            self.assertIsNone(model.observed_runtime)
            self.assertEqual(rvc_models.list_models().models[0].id, model.id)
            self.assertEqual((rvc_models.model_root(model.id) / 'model.pth').read_bytes(), opaque)

    def test_local_observed_model_hash_is_not_labeled_publisher_declared_export_evidence(self) -> None:
        from app import replacement_jobs
        with patch.object(rvc_models, 'observe_runtime', side_effect=rvc_models.RvcModelError('replacement_engine_unavailable')):
            model = rvc_models.import_model(RvcModelImport.model_validate(self.metadata), BytesIO(b'opaque'), checkpoint_name='voice.pth')
        evidence = replacement_jobs._rvc_music(model)
        self.assertEqual(evidence.components[0].identities, [])
        self.assertEqual(evidence.restriction, 'unknown')
        self.assertNotIn(model.rights_declaration, evidence.model_dump_json())
        self.assertNotIn(model.evidence_ref, evidence.model_dump_json())

    def test_untrusted_missing_rights_invalid_urls_and_numeric_acknowledgements_are_rejected(self) -> None:
        for key, value in [('trusted_local_checkpoint', False), ('pickle_execution_acknowledged', 1),
                ('rights_declaration', ''), ('source_url', 'https://user:password@example.org/model')]:
            with self.subTest(key=key), self.assertRaises(ValidationError):
                RvcModelImport.model_validate({**self.metadata, key: value})

    def test_import_replay_reattaches_before_fresh_authorization_and_disk_checks(self) -> None:
        request = RvcModelImport.model_validate({**self.metadata, 'request_id': 'f' * 32})
        with patch.object(rvc_models, 'observe_runtime', side_effect=rvc_models.RvcModelError('replacement_engine_unavailable')):
            first = rvc_models.import_model(request, BytesIO(b'opaque'), checkpoint_name='first.pth')
        with patch('app.voice_authorization.authorize', side_effect=RuntimeError('consent changed')), \
                patch.object(rvc_models.shutil, 'disk_usage', side_effect=AssertionError('reattach must not admit storage')):
            replay = rvc_models.import_model(request, BytesIO(b'opaque'), checkpoint_name='renamed.pth')
        self.assertEqual(replay.id, first.id)
        self.assertEqual(len(rvc_models.list_models().models), 1)
        with self.assertRaises(rvc_models.RvcModelError) as conflict:
            rvc_models.import_model(request, BytesIO(b'changed'), checkpoint_name='first.pth')
        self.assertEqual(conflict.exception.code, 'replacement_request_conflict')

    def test_legacy_import_deduplicates_identical_content_but_explicit_new_request_can_register_again(self) -> None:
        request = RvcModelImport.model_validate(self.metadata)
        with patch.object(rvc_models, 'observe_runtime', side_effect=rvc_models.RvcModelError('replacement_engine_unavailable')):
            first = rvc_models.import_model(request, BytesIO(b'opaque'), checkpoint_name='voice.pth')
            second = rvc_models.import_model(request, BytesIO(b'opaque'), checkpoint_name='voice.pth')
            explicit = rvc_models.import_model(RvcModelImport.model_validate({**self.metadata, 'request_id': 'f' * 32}),
                BytesIO(b'opaque'), checkpoint_name='voice.pth')
        self.assertEqual(first.id, second.id)
        self.assertNotEqual(first.id, explicit.id)

    def test_missing_setup_receipt_does_not_certify_runtime_from_source_presence(self) -> None:
        self.runtime_fixture(verified=False)
        with self.assertRaises(rvc_models.RvcModelError) as error:
            rvc_models.observe_runtime()
        self.assertEqual(error.exception.code, 'replacement_engine_unavailable')

    def test_archives_indices_without_metadata_and_oversized_payloads_do_not_publish_entries(self) -> None:
        request = RvcModelImport.model_validate(self.metadata)
        with self.assertRaises(rvc_models.RvcModelError):
            rvc_models.import_model(request, BytesIO(b'opaque'), checkpoint_name='download.zip')
        with patch.object(rvc_models, 'MAX_MODEL_BYTES', 4):
            with self.assertRaises(rvc_models.RvcModelError):
                rvc_models.import_model(request, BytesIO(b'oversized'), checkpoint_name='voice.pth')
        self.assertEqual(rvc_models.list_models().models, [])

    def test_mutated_checkpoint_and_symbolic_storage_are_never_ready(self) -> None:
        request = RvcModelImport.model_validate(self.metadata)
        with patch.object(rvc_models, 'observe_runtime', side_effect=rvc_models.RvcModelError('replacement_engine_unavailable')):
            model = rvc_models.import_model(request, BytesIO(b'opaque'), checkpoint_name='voice.pth')
        (rvc_models.model_root(model.id) / 'model.pth').write_bytes(b'changed')
        with self.assertRaises(rvc_models.RvcModelError) as error:
            rvc_models.resolve(model.id)
        self.assertEqual(error.exception.code, 'replacement_model_changed')
        path = rvc_models.model_root(model.id) / 'model.pth'
        path.unlink()
        path.symlink_to(self.root / 'outside.pth')
        with self.assertRaises(rvc_models.RvcModelError):
            rvc_models.resolve(model.id)

    def runtime_fixture(self, *, verified: bool = True) -> Path:
        from app import config
        engine = self.root / 'rvc'
        engine.mkdir()
        hashes = {}
        for name in rvc_models.SOURCE_HASHES:
            path = engine / name
            path.parent.mkdir(parents=True, exist_ok=True)
            content = ('isolated source fixture ' + name).encode()
            path.write_bytes(content)
            hashes[name] = hashlib.sha256(content).hexdigest()
        for name in ('assets/hubert_base/config.json', 'assets/hubert_base/preprocessor_config.json',
                'assets/hubert_base/model.safetensors', 'assets/rmvpe/rmvpe.pt', 'webui.py', '.venv/bin/python'):
            path = engine / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'opaque fixture')
        self.enterContext(patch.object(config, 'RVC_DIR', engine))
        self.enterContext(patch.object(rvc_models, 'SOURCE_HASHES', hashes))
        from app.module_catalog import ModuleEnvironment
        from app.module_evidence import VerificationReceipt, environment_fingerprint
        environment = ModuleEnvironment.for_root(self.root, paths={'rvc': engine}, platform='darwin')
        self.enterContext(patch('app.module_catalog.configured_environment', return_value=environment))
        (engine / '.venv/lib/python3.11/site-packages').mkdir(parents=True)
        fingerprint = environment_fingerprint(environment, 'rvc')
        self.assertIsNotNone(fingerprint)
        if verified and fingerprint is not None:
            receipt = self.root / '.setup/verification/rvc.json'
            receipt.parent.mkdir(parents=True)
            receipt.write_text(VerificationReceipt(root=str(engine), fingerprint=fingerprint,
                environment_verified=True).model_dump_json())
        return engine

    def test_pinned_runtime_rejects_missing_or_changed_auxiliary_and_unknown_cli(self) -> None:
        engine = self.runtime_fixture()
        model = rvc_models.import_model(RvcModelImport.model_validate(self.metadata), BytesIO(b'opaque'), checkpoint_name='voice.pth')
        self.assertTrue(model.ready)
        self.assertIsNotNone(model.observed_runtime)
        self.assertEqual(rvc_models.resolve(model.id).public.checkpoint_sha256, model.checkpoint_sha256)
        (engine / 'assets/rmvpe/rmvpe.pt').write_bytes(b'changed auxiliary')
        with self.assertRaises(rvc_models.RvcModelError) as error:
            rvc_models.resolve(model.id)
        self.assertEqual(error.exception.code, 'replacement_model_changed')
        (engine / 'infer/cli.py').write_text('unknown options')
        self.assertFalse(rvc_models.get(model.id).ready)

    def test_explicit_no_index_settings_skip_automatic_index_discovery(self) -> None:
        self.runtime_fixture()
        from app.optional_engines import prepare_rvc_command
        model, audio, output = self.root / 'voice.pth', self.root / 'input.wav', self.root / 'output.wav'
        model.write_bytes(b'opaque')
        audio.write_bytes(b'opaque audio')
        argv, _ = prepare_rvc_command(model, audio, output, speaker_id=2, index_rate=0.0)
        self.assertEqual(argv[argv.index('--index-rate') + 1], '0.0')
        self.assertEqual(argv[argv.index('--speaker-id') + 1], '2')
        self.assertNotIn('--index', argv)
