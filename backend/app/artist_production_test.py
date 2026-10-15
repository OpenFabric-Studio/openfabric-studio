"""Strict lineage uses temporary catalogs and synthetic bytes, never model work."""
from __future__ import annotations

import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db


class ProductionLineageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        for name, value in [('DATA_DIR', self.root), ('DB_PATH', self.root / 'aicollector.db'),
                            ('FILES_DIR', self.root / 'files'), ('_db', None)]:
            self.enterContext(patch.object(db, name, value))
        db.FILES_DIR.mkdir()

    def tearDown(self) -> None:
        if db._db is not None:
            db._db.close()

    def source(self, name: str, origin: str = 'human_recorded', provider: str = 'local') -> str:
        from app.artist_production import register_source, review_asset
        from app.artist_production_contracts import SourceDeclaration, RightsReviewRequest
        path = db.FILES_DIR / name
        path.write_bytes(name.encode())
        asset = register_source(path, SourceDeclaration.model_validate({
            'role': 'guide_vocal', 'origin': origin, 'provider': provider, 'rights_holder': 'Fixture owner',
            'original_filename': name, 'acquisition_method': 'authorized recording', 'acquired_at': '2026-10-14',
            'evidence_ref': 'local agreement fixture', 'permission_scope': 'production and release',
            'permitted_uses': ['production', 'release'],
        }))
        review_asset(asset.id, RightsReviewRequest(status='approved', reviewer_label='Local reviewer',
            evidence_ref='signed fixture agreement', note='Fixture declaration only'))
        return asset.id

    def test_reviewed_human_sources_and_transform_survive_catalog_restart(self) -> None:
        from app.artist_production import create_transform, validate_graph, get_asset
        guide = self.source('guide.wav')
        backing = self.source('backing.wav', 'licensed_third_party')
        output = db.FILES_DIR / 'master.wav'
        output.write_bytes(b'fixture mix')
        master = create_transform(output, 'master', [guide, backing], 'seed-vc + mix')
        self.assertTrue(validate_graph(master.id).technical_eligible)
        self.assertFalse(validate_graph(master.id).approved_for_release)
        db.get_db().close()
        db._db = None
        self.assertEqual(get_asset(master.id).parent_ids, [guide, backing])
        self.assertTrue(validate_graph(master.id).technical_eligible)

    def test_suno_ancestry_and_unknown_review_cannot_pass_strict_gate(self) -> None:
        from app.artist_production import create_transform, validate_graph
        blocked = self.source('suno.wav', 'ai_generated', 'suno')
        output = db.FILES_DIR / 'converted.wav'
        output.write_bytes(b'conversion of blocked audio')
        child = create_transform(output, 'voice_converted', [blocked], 'seed-vc')
        gate = validate_graph(child.id)
        self.assertFalse(gate.technical_eligible)
        self.assertIn('reference_only_source', gate.blockers)

    def test_known_generated_hash_cannot_be_redeclared_as_human(self) -> None:
        from app.artist_production import register_source, ProductionError
        from app.artist_production_contracts import SourceDeclaration
        source = self.source('generated.wav', 'ai_generated', 'suno')
        self.assertTrue(source)
        with self.assertRaises(ProductionError) as error:
            register_source(db.FILES_DIR / 'generated.wav', SourceDeclaration(role='guide_vocal',
                origin='human_recorded', provider='local', original_filename='renamed.wav'))
        self.assertEqual(error.exception.code, 'source_origin_conflict')

    def test_failed_or_cancelled_voice_bytes_cannot_be_redeclared_as_human(self) -> None:
        from app import audio_version_store
        from app.artist_production import register_source, ProductionError
        from app.artist_production_contracts import SourceDeclaration
        from app.audio_version_contracts import AudioVersion
        from app.contracts import JobStatus
        statuses: tuple[JobStatus, ...] = ('failed', 'cancelled')
        for index, status in enumerate(statuses, start=1):
            with self.subTest(status=status):
                original = db.FILES_DIR / f'original-{status}.wav'
                original.write_bytes(b'original human fixture')
                track_id = db.insert_track(model='upload', title='Fixture', lyrics='', seed=None,
                    duration_ms=None, wall_ms=None, params={}, audio_path=original, abc_path=None)
                generated = db.FILES_DIR / f'version-{status}.wav'
                generated.write_bytes(f'converted {status} fixture'.encode())
                version = AudioVersion(id=f'{index:032x}', track_id=track_id, kind='voice',
                    voice_id='a' * 32, status=status, created_at='2026-10-14T00:00:00Z')
                audio_version_store.insert(db.get_db(),
                    audio_version_store.StoredAudioVersion(version, generated, None))
                reupload = db.FILES_DIR / f'reuploaded-{status}.wav'
                reupload.write_bytes(generated.read_bytes())
                with self.assertRaises(ProductionError) as error:
                    register_source(reupload, SourceDeclaration(role='guide_vocal',
                        origin='human_recorded', provider='local', original_filename=reupload.name))
                self.assertEqual(error.exception.code, 'source_origin_conflict')

    def test_reupload_missing_source_creates_new_unreviewed_identity(self) -> None:
        from app.artist_production import get_asset, register_source, validate_graph
        identifier = self.source('guide.wav')
        original = get_asset(identifier)
        copy = db.FILES_DIR / 'reupload.wav'
        copy.write_bytes((db.FILES_DIR / 'guide.wav').read_bytes())
        self.assertEqual(register_source(copy, original.declaration).id, identifier)
        (db.FILES_DIR / 'guide.wav').unlink()
        replacement = register_source(copy, original.declaration)
        self.assertNotEqual(replacement.id, identifier)
        self.assertEqual(replacement.sha256, original.sha256)
        self.assertFalse(validate_graph(replacement.id).technical_eligible)
        self.assertIn('artifact_missing', validate_graph(identifier).blockers)

    def test_source_evidence_amendment_preserves_origin_and_requires_fresh_review(self) -> None:
        from app.artist_production import get_asset, amend_source, validate_graph, ProductionError
        from app.artist_production_contracts import AmendSourceRequest
        identifier = self.source('guide.wav')
        initial = get_asset(identifier)
        declaration = initial.declaration.model_copy(update={'rights_holder': 'Corrected fixture holder'})
        amended = amend_source(identifier, AmendSourceRequest(declaration=declaration, reviewer_label='Fixture reviewer'))
        self.assertEqual(amended.declaration.rights_holder, 'Corrected fixture holder')
        self.assertEqual(amended.declaration_revisions[0].declaration.rights_holder, 'Fixture owner')
        self.assertEqual(amended.origin, 'human_recorded')
        self.assertFalse(validate_graph(identifier).technical_eligible)
        from app.artist_production import review_asset
        from app.artist_production_contracts import RightsReviewRequest
        stale = RightsReviewRequest(status='approved', reviewer_label='Fixture reviewer', evidence_ref='fixture',
            declaration_revision_id=initial.declaration_revisions[-1].id)
        with self.assertRaises(ProductionError) as rejected:
            review_asset(identifier, stale)
        self.assertEqual(rejected.exception.code, 'source_evidence_changed')
        with self.assertRaises(ProductionError):
            review_asset(identifier, stale.model_copy(update={'declaration_revision_id': None}))
        review_asset(identifier, stale.model_copy(update={'declaration_revision_id': amended.declaration_revisions[-1].id}))
        self.assertTrue(validate_graph(identifier).technical_eligible)
        with self.assertRaises(ProductionError):
            amend_source(identifier, AmendSourceRequest(declaration=declaration.model_copy(update={'origin': 'licensed_third_party'}), reviewer_label='Fixture reviewer'))

    def test_missing_or_changed_parent_blocks_even_with_approved_review(self) -> None:
        from app.artist_production import create_transform, validate_graph
        guide = self.source('guide.wav')
        output = db.FILES_DIR / 'master.wav'
        output.write_bytes(b'master')
        master = create_transform(output, 'master', [guide], 'fixture')
        (db.FILES_DIR / 'guide.wav').write_bytes(b'changed')
        self.assertIn('artifact_changed', validate_graph(master.id).blockers)
        (db.FILES_DIR / 'guide.wav').unlink()
        self.assertIn('artifact_missing', validate_graph(master.id).blockers)

    def test_append_only_usage_idempotency_and_tamper_detection(self) -> None:
        from app.artist_production import append_usage, usage_ledger, verify_ledger, ProductionError
        from app.artist_production_contracts import UsageEventInput
        event = UsageEventInput(idempotency_key='fixture:convert:failed', operation='convert', outcome='failed',
            artist_id='a' * 32, consent_version_id='b' * 32, error_code='convert_failed')
        first = append_usage(event)
        self.assertEqual(append_usage(event).id, first.id)
        self.assertEqual(len(usage_ledger().events), 1)
        self.assertTrue(verify_ledger())
        with self.assertRaises(ProductionError):
            append_usage(event.model_copy(update={'outcome': 'done'}))
        with self.assertRaises(Exception):
            db.get_db().execute("UPDATE model_usage_events SET body_json='{}'")

    def test_symlink_asset_escape_rejected(self) -> None:
        from app.artist_production import register_source, ProductionError
        from app.artist_production_contracts import SourceDeclaration
        outside = self.root / 'outside.wav'
        outside.write_bytes(b'private')
        linked = db.FILES_DIR / 'linked.wav'
        linked.symlink_to(outside)
        with self.assertRaises(ProductionError):
            register_source(linked, SourceDeclaration(role='guide_vocal', origin='human_recorded', original_filename='linked.wav'))

    def test_additive_migration_and_library_move_preserve_graph_hashes(self) -> None:
        from app.artist_production import create_transform, get_asset, validate_graph, migrate
        from app.data_root import migrate_library
        parent = self.source('guide.wav')
        output = db.FILES_DIR / 'master.wav'
        output.write_bytes(b'master')
        master = create_transform(output, 'master', [parent], 'fixture transform')
        migrate(db.get_db())
        migrate(db.get_db())
        old_root = self.root
        destination = old_root.parent / (old_root.name + '-moved')
        self.addCleanup(shutil.rmtree, destination, True)
        db.get_db().close()
        db._db = None
        migrate_library(old_root, destination)
        with patch.object(db, 'DB_PATH', destination / 'aicollector.db'), patch.object(db, 'FILES_DIR', destination / 'files'):
            self.assertEqual(get_asset(master.id).sha256, master.sha256)
            self.assertTrue(validate_graph(master.id).technical_eligible)
            db.get_db().close()
            db._db = None


if __name__ == '__main__':
    unittest.main()


class HumanGuidePipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app import voice_build
        from app.artist_contracts import CreateArtistRequest, ArtistConsentRequest
        from app.artists import create_artist, add_consent_revision
        from app.artist_production_test_fixtures import tone_wav_bytes
        from app.voice_artifacts import StoredModel, publish
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        for name, value in [('DATA_DIR', self.root), ('DB_PATH', self.root / 'catalog.db'),
                            ('FILES_DIR', self.root / 'files'), ('_db', None)]:
            self.enterContext(patch.object(db, name, value))
        for name, value in [('DATA_DIR', self.root), ('VOICES_DIR', self.root / 'voices'),
                            ('SEED_VC_DIR', self.root / 'engine'), ('LOG_DIR', self.root / 'logs')]:
            self.enterContext(patch.object(voice_build, name, value))
        db.FILES_DIR.mkdir()
        self.voice_id = 'a' * 32
        voice = self.root / 'voices' / self.voice_id
        (voice / 'recordings').mkdir(parents=True)
        meta = voice_build.new_voice_meta(self.voice_id, 'Fixture singer')
        meta['status'] = 'ready'
        for name, field in [('model.pth', 'checkpoint'), ('config.yml', 'config'), ('reference.wav', 'reference')]:
            path = voice / name
            path.write_bytes(tone_wav_bytes(seconds=.5) if field == 'reference' else b'fixture model')
            meta[field] = str(path)
        (voice / 'preview.wav').write_bytes(tone_wav_bytes(seconds=.5))
        voice_build.write_meta(voice, meta)
        publish(voice, [StoredModel(id='fixture-v1', kind='trained', steps=200, checkpoint='model.pth',
            config='config.yml', reference='reference.wav', preview='preview.wav')], 'fixture-v1')
        engine = self.root / 'engine'
        (engine / '.venv' / 'bin').mkdir(parents=True)
        (engine / '.venv' / 'bin' / 'python').write_bytes(b'not executed')
        (engine / 'inference.py').write_text('# fixture, never a real converter\n')
        artist = create_artist(CreateArtistRequest(name='Fixture artist', voice_id=self.voice_id))
        self.artist_id = artist.id
        self.consent = add_consent_revision(artist.id, ArtistConsentRequest(status='active',
            signed_evidence_ref='signed fixture agreement', operator_label='Fixture reviewer',
            human_declaration='Fixture consent declaration for application regression tests only.',
            allowed_operations=['train', 'convert', 'export'], allowed_uses=['evaluation', 'production', 'commercial']))
        from app.singer_range_contracts import SingerRangeRequest
        from app.singer_ranges import save_profile
        self.range = save_profile(artist.id, SingerRangeRequest(comfortable_low_midi=48, comfortable_high_midi=84,
            policy='warn', operator_label='Fixture operator', operator_declaration='Fixture operator range confirmation for regression testing.',
            artist_confirmation_declaration='Fixture artist range confirmation for regression testing.'))

    async def asyncTearDown(self) -> None:
        from app import voice_build
        await voice_build.shutdown()
        if db._db is not None:
            db._db.close()

    def source(self, name: str, role: str, frequency: float) -> str:
        from app.artist_production import register_source, review_asset
        from app.artist_production_contracts import SourceDeclaration, RightsReviewRequest
        from app.artist_production_test_fixtures import tone_wav_bytes
        path = db.FILES_DIR / name
        path.write_bytes(tone_wav_bytes(seconds=.5, frequency_hz=frequency))
        asset = register_source(path, SourceDeclaration.model_validate({'role': role, 'origin': 'human_recorded',
            'provider': 'local', 'rights_holder': 'Fixture owner', 'original_filename': name,
            'evidence_ref': 'signed fixture', 'permission_scope': 'production and release',
            'permitted_uses': ['production', 'release']}))
        review_asset(asset.id, RightsReviewRequest(status='approved', reviewer_label='Fixture reviewer', evidence_ref='fixture'))
        return asset.id

    async def test_isolated_guide_pipeline_skips_separation_and_persists_exact_version_lineage(self) -> None:
        import asyncio
        import shutil
        from unittest.mock import AsyncMock
        from app import voice_build
        from app.artist_production import get_run, production_provenance, usage_ledger
        from app.artist_production_contracts import CleanRenderRequest
        from app.production_render import start_clean_render
        from app.voice_contracts import VoiceTrialMetrics
        guide = self.source('guide.wav', 'guide_vocal', 220)
        backing = self.source('backing.wav', 'accompaniment', 110)
        async def inference(command: list[str], **kwargs: object) -> int:
            if 'app.guide_range' in command:
                import sys
                from app.job_lifecycle import communicate_process, spawn_process
                process = await spawn_process(sys.executable, *command[1:], cwd=str(Path(__file__).parents[1]),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                output, _ = await communicate_process(process, 20)
                voice_build.LOG_DIR.mkdir(parents=True, exist_ok=True)
                (voice_build.LOG_DIR / f'{kwargs["log_name"]}.log').write_bytes(output)
                return process.returncode
            if '--mono' in command:
                import sys
                from app.job_lifecycle import communicate_process, spawn_process
                process = await spawn_process(sys.executable, *command[1:], stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                await communicate_process(process, 10)
                return process.returncode
            output = Path(command[command.index('--output') + 1])
            shutil.copyfile(command[command.index('--source') + 1], output / 'vc_fixture.wav')
            return 0
        async def measure(audio: Path, expected: float, job: voice_build.ApplyJob, label: str) -> VoiceTrialMetrics:
            return VoiceTrialMetrics(duration_sec=expected, duration_delta_sec=0, level_db=-20, peak=.2, clipped_fraction=0)
        with patch.object(voice_build, 'separate_file', new=AsyncMock(side_effect=AssertionError('Guide must skip separation'))), \
             patch.object(voice_build, '_spawn', side_effect=inference), \
             patch.object(voice_build, '_measure_conversion', side_effect=measure), \
             patch('app.seed_vc_compat.ensure_compatibility'):
            run = await start_clean_render(CleanRenderRequest(artist_id=self.artist_id, consent_version_id=self.consent.id,
                voice_id=self.voice_id, model_revision_id='fixture-v1', guide_asset_id=guide,
                accompaniment_asset_id=backing, title='Independent fixture', guide_singer_confirmed=True, guide_isolated=True))
            job = voice_build._applies[run.track_id]
            # Real CPU probes have their own 20s/10s deadlines before publication.
            await asyncio.wait_for(asyncio.shield(job.task), 60)
        stored = get_run(run.id)
        self.assertEqual(stored.status, 'done', stored.error_code)
        self.assertIsNotNone(stored.audio_version_id)
        provenance = production_provenance(stored.audio_version_id)
        self.assertIsNotNone(provenance)
        self.assertTrue(provenance.gate.technical_eligible)
        self.assertFalse(provenance.gate.approved_for_release)
        self.assertEqual(provenance.model.model_revision_id, 'fixture-v1')
        import soundfile as sf
        from app import audio_versions
        self.assertEqual(sf.info(audio_versions.resolve_source(stored.track_id, stored.audio_version_id)).subtype, 'FLOAT')
        self.assertEqual({node.role for node in provenance.assets}, {'guide_vocal', 'accompaniment', 'voice_converted', 'master'})
        self.assertEqual([event.outcome for event in usage_ledger().events], ['requested', 'done'])
        from app import audio_exports
        export = await audio_exports.create_export(stored.track_id, stored.audio_version_id, 'wav')
        requested_export = usage_ledger().events[-1]
        self.assertEqual(requested_export.outcome, 'requested')
        self.assertEqual(requested_export.source_graph_id, stored.output_asset_id)
        self.assertEqual(requested_export.voice_model_revision, 'fixture-v1')
        self.assertEqual(requested_export.voice_model_sha256, provenance.model.files[0].sha256)
        await asyncio.wait_for(asyncio.shield(audio_exports._tasks[export.id]), 15)
        finished = audio_exports.get_export(stored.track_id, stored.audio_version_id, export.id)
        self.assertEqual(finished.status, 'done', finished.error_code)
        self.assertIsNotNone(finished.provenance.production)
        self.assertEqual(finished.provenance.production.graph_root_id, stored.output_asset_id)
        pending_export = await audio_exports.create_export(stored.track_id, stored.audio_version_id, 'flac')
        await audio_exports.cancel_export(stored.track_id, stored.audio_version_id, pending_export.id)
        cancelled_export = usage_ledger().events[-1]
        self.assertEqual(cancelled_export.outcome, 'cancelled')
        self.assertEqual(cancelled_export.voice_model_revision, 'fixture-v1')
        self.assertEqual(cancelled_export.source_graph_id, stored.output_asset_id)
        with self.assertRaises(audio_exports.AudioExportError) as blocked:
            await audio_exports.create_export(stored.track_id, stored.audio_version_id, 'wav', strict_release=True)
        self.assertEqual(blocked.exception.code, 'dependency_inventory_incomplete')
        from app import audio_versions
        from app.audio_version_store import set_state
        from app.production_render import recover
        from app.export_provenance import digest
        before_recovery = digest(audio_versions.resolve_source(stored.track_id, stored.audio_version_id))
        set_state(db.get_db(), stored.audio_version_id, 'failed', 'interrupted')
        recover()
        self.assertEqual(digest(audio_versions.resolve_source(stored.track_id, stored.audio_version_id)), before_recovery)
        with self.assertRaises(Exception) as retry:
            await audio_versions.retry_version(stored.track_id, stored.audio_version_id)
        self.assertEqual(retry.exception.detail, 'production_retry_requires_new_run')
        output = audio_versions.resolve_source(stored.track_id, stored.audio_version_id)
        output.write_bytes(b'changed output; retained for operator review')
        recover()
        self.assertEqual(get_run(run.id).error_code, 'production_recovery_review_required')
        correction = usage_ledger().events[-1]
        self.assertEqual(correction.outcome, 'interrupted')
        self.assertEqual(correction.error_code, 'production_recovery_review_required')
        prior = next(event for event in usage_ledger().events if event.idempotency_key == f'production:{run.id}:done')
        self.assertEqual(correction.supersedes_event_id, prior.id)
        count = len(usage_ledger().events)
        recover()
        self.assertEqual(len(usage_ledger().events), count)

    async def test_revoked_permission_blocks_legacy_entry_points(self) -> None:
        import httpx
        from fastapi import FastAPI
        from app.api import routes_voices, routes_artist_production
        from app.artists import add_consent_revision
        from app.artist_contracts import ArtistConsentRequest
        from app import voice_build, voice_comparisons
        from app.voice_contracts import VoiceComparisonRequest
        add_consent_revision(self.artist_id, ArtistConsentRequest(status='revoked', operator_label='Fixture reviewer',
            human_declaration='Fixture authorization revoked for application regression testing.'))
        app = FastAPI()
        app.include_router(routes_voices.router)
        app.include_router(routes_artist_production.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post(f'/api/voices/{self.voice_id}/prepare', json={})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()['detail'], 'artist_consent_revoked')
        for action in (lambda: voice_build.start_build(self.voice_id),
                       lambda: voice_build.start_apply(self.voice_id, 1),
                       lambda: voice_comparisons.start_comparison(self.voice_id,
                           VoiceComparisonRequest(source_id='b' * 32, model_ids=['fixture-v1']))):
            with self.assertRaises(Exception) as denied:
                action()
            self.assertEqual(denied.exception.detail, 'artist_consent_revoked')

    async def test_changed_checkpoint_is_rejected_before_inference(self) -> None:
        from app.artist_production_contracts import CleanRenderRequest, ProductionRun
        from app.artist_production import ProductionError
        from app.production_render import model_snapshot, require_run
        guide = self.source('guide.wav', 'guide_vocal', 220)
        backing = self.source('backing.wav', 'accompaniment', 110)
        model = model_snapshot(self.voice_id, 'fixture-v1')
        (self.root / 'voices' / self.voice_id / 'model.pth').write_bytes(b'changed model')
        run = ProductionRun(id='b' * 32, project_id=1, created_at='fixture', status='queued', model=model, range_profile=self.range,
            request=CleanRenderRequest(artist_id=self.artist_id, consent_version_id=self.consent.id,
                voice_id=self.voice_id, model_revision_id='fixture-v1', guide_asset_id=guide,
                accompaniment_asset_id=backing, title='Fixture', guide_singer_confirmed=True, guide_isolated=True))
        with self.assertRaises(ProductionError) as error:
            require_run(run)
        self.assertEqual(error.exception.code, 'model_identity_changed')

    async def test_saved_run_cannot_rebind_input_or_checkpoint_identity(self) -> None:
        import sqlite3
        from app.artist_production_contracts import CleanRenderRequest, ProductionRun
        from app.artist_production import get_run, save_run
        from app.production_render import model_snapshot
        guide = self.source('guide.wav', 'guide_vocal', 220)
        backing = self.source('backing.wav', 'accompaniment', 110)
        run = ProductionRun(id='b' * 32, project_id=1, created_at='fixture', status='queued',
            model=model_snapshot(self.voice_id, 'fixture-v1'), range_profile=self.range,
            request=CleanRenderRequest(artist_id=self.artist_id, consent_version_id=self.consent.id,
                voice_id=self.voice_id, model_revision_id='fixture-v1', guide_asset_id=guide,
                accompaniment_asset_id=backing, title='Fixture', guide_singer_confirmed=True, guide_isolated=True))
        save_run(run)
        changed = run.model_copy(deep=True)
        changed.request.guide_asset_id = backing
        with self.assertRaises(sqlite3.IntegrityError):
            save_run(changed)
        changed = run.model_copy(deep=True)
        changed.model.files[0].sha256 = 'f' * 64
        with self.assertRaises(sqlite3.IntegrityError):
            save_run(changed)
        self.assertEqual(get_run(run.id).request.guide_asset_id, guide)

    async def test_identical_audio_bytes_cannot_be_both_independent_inputs(self) -> None:
        from app.artist_production_contracts import CleanRenderRequest
        from app.artist_production import ProductionError
        from app.production_render import start_clean_render
        guide = self.source('guide.wav', 'guide_vocal', 220)
        backing = self.source('backing.wav', 'accompaniment', 220)
        with self.assertRaises(ProductionError) as rejected:
            await start_clean_render(CleanRenderRequest(artist_id=self.artist_id, consent_version_id=self.consent.id,
                voice_id=self.voice_id, model_revision_id='fixture-v1', guide_asset_id=guide, accompaniment_asset_id=backing,
                title='Fixture', guide_singer_confirmed=True, guide_isolated=True))
        self.assertEqual(rejected.exception.code, 'independent_accompaniment_required')

    async def test_duplicate_source_upload_removes_only_its_unreferenced_copy(self) -> None:
        from io import BytesIO
        from fastapi import UploadFile
        from app.api.routes_artist_production import upload_asset
        from app.artist_production import get_asset
        identifier = self.source('guide.wav', 'guide_vocal', 220)
        source = get_asset(identifier)
        upload = UploadFile(file=BytesIO((db.FILES_DIR / 'guide.wav').read_bytes()), filename='duplicate.wav')
        saved = await upload_asset(upload, source.declaration.model_dump_json())
        self.assertEqual(saved.id, identifier)
        self.assertTrue((db.FILES_DIR / 'guide.wav').is_file())
        self.assertEqual(list((db.FILES_DIR / 'artist-production' / 'sources').iterdir()), [])
        self.assertTrue(upload.file.closed)
