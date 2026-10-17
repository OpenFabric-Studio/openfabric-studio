from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import numpy as np
import soundfile as sf

from app import audio_versions, db, replacement_jobs as jobs
from app.replacement_contracts import (PreparedReplacementRequest, ReplacementActionRequest,
    SelectedSource)
from app.music_analysis_contracts import VersionMusicSource


class ReplacementJobsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        for name, value in [('DATA_DIR', self.root), ('DB_PATH', self.root / 'catalog.db'),
                ('FILES_DIR', self.root / 'files'), ('_db', None)]:
            self.enterContext(patch.object(db, name, value))
        db.FILES_DIR.mkdir()
        self.sources: list[SelectedSource] = []
        for index in (1, 2):
            path = db.FILES_DIR / f'input{index}.wav'
            time = np.arange(24000) / 8000
            sf.write(path, .1 * np.sin(2 * np.pi * (220 * index) * time), 8000, subtype='FLOAT')
            track = db.insert_track(model='upload', title='Fixture', lyrics='', seed=None,
                duration_ms=3000, wall_ms=None, params={}, audio_path=path, abc_path=None)
            version = audio_versions.retain_original(track, path)
            self.sources.append(SelectedSource(source=VersionMusicSource(track_id=track, version_id=version.id),
                audio_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        await jobs.recover()
        self.request = PreparedReplacementRequest(mode='prepared', title='Prepared fixture',
            source=self.sources[0], prepared=self.sources[1], separator='fast')

    async def test_plan_creation_replay_preserves_initial_body_after_edits_and_original_removal(self) -> None:
        from app.replacement_contracts import ReplacementUpdateRequest
        created = await jobs.create(self.request, request_id='e' * 32)
        await jobs.update(created.id, ReplacementUpdateRequest(expected_revision=1,
            request=self.request.model_copy(update={'title': 'Edited plan'})))
        with patch.object(jobs, '_source', side_effect=AssertionError('Replay must not inspect a mutable source')):
            replayed = await jobs.create(self.request, request_id='e' * 32)
            self.assertEqual(replayed.id, created.id)
            self.assertEqual(replayed.request.title, 'Edited plan')
            with self.assertRaises(jobs.ReplacementError) as rejected:
                await jobs.create(self.request.model_copy(update={'title': 'Other creation'}), request_id='e' * 32)
            self.assertEqual(rejected.exception.code, 'replacement_request_conflict')
        self.assertEqual(len(jobs.list_plans().plans), 1)

    async def asyncTearDown(self) -> None:
        await jobs.shutdown()
        if db._db is not None:
            db._db.close()

    async def test_draft_is_reloadable_without_separation_or_conversion(self) -> None:
        with patch.object(jobs, '_prepare', new_callable=AsyncMock) as prepare:
            plan = await jobs.create(self.request)
            self.assertEqual(jobs.get(plan.id).request, self.request)
            self.assertEqual(jobs.list_plans(source=self.sources[0].source).plans[0].id, plan.id)
            prepare.assert_not_awaited()
            self.assertFalse(jobs.work_busy())

    async def test_diagnostic_timeout_is_advisory_only_after_verified_drain(self) -> None:
        from types import SimpleNamespace
        from app.replacement_diagnostics import DiagnosticWorkerUnavailable
        with patch.object(jobs, '_command', AsyncMock(side_effect=TimeoutError)), \
                patch.object(jobs, '_drain', AsyncMock()) as drain, \
                patch.object(jobs, '_attempt', return_value=SimpleNamespace(worker=None, cleanup_pending=False)):
            with self.assertRaises(DiagnosticWorkerUnavailable):
                await jobs._diagnostic_command('f' * 32, ['owned-worker'], 1)
            drain.assert_awaited_once_with('f' * 32)
        with patch.object(jobs, '_command', AsyncMock(side_effect=TimeoutError)), \
                patch.object(jobs, '_drain', AsyncMock(side_effect=jobs.ReplacementError('replacement_cleanup_pending'))):
            with self.assertRaises(jobs.ReplacementError) as pending:
                await jobs._diagnostic_command('f' * 32, ['owned-worker'], 1)
            self.assertEqual(pending.exception.code, 'replacement_cleanup_pending')

    async def test_ir_requires_existing_reviewed_rights_and_rejects_unknown_identity(self) -> None:
        from app import artist_production as evidence
        from app.artist_production_contracts import SourceDeclaration, RightsReviewRequest
        from app.replacement_contracts import ReplacementMixSettings
        path = db.FILES_DIR / 'owned-ir.wav'
        signal = np.zeros(800, dtype=np.float32)
        signal[0] = 2
        sf.write(path, signal, 8000, subtype='FLOAT')
        asset = evidence.register_source(path, SourceDeclaration(role='reference', origin='human_recorded',
            provider='local', rights_holder='Fixture operator', original_filename='owned-ir.wav',
            permitted_uses=['production']))
        settings = ReplacementMixSettings(space='owned_ir', ir_asset_id=asset.id, wet=.25)
        with self.assertRaises(jobs.ReplacementError) as unreviewed:
            jobs._ir_selection(settings)
        self.assertEqual(unreviewed.exception.code, 'replacement_evidence_required')
        evidence.review_asset(asset.id, RightsReviewRequest(status='approved', reviewer_label='Fixture operator',
            evidence_ref='Local measurement ownership'))
        selected = jobs._ir_selection(settings)
        self.assertEqual(selected.source.asset_id, asset.id)
        self.assertEqual(selected.audio_sha256, asset.sha256)
        with self.assertRaises(jobs.ReplacementError) as missing:
            jobs._ir_selection(ReplacementMixSettings(space='owned_ir', ir_asset_id='f' * 32, wet=.25))
        self.assertEqual(missing.exception.code, 'replacement_source_missing')

    async def test_intake_replay_retains_one_existing_track_version_and_unknown_reference_evidence(self) -> None:
        from io import BytesIO
        from app.replacement_contracts import ReplacementAssetImport
        metadata = ReplacementAssetImport.model_validate({'request_id': 'f' * 32, 'role': 'prepared', 'title': 'Take',
            'declaration': {'role': 'reference', 'origin': 'unknown', 'provider': 'unknown', 'original_filename': 'take.wav'}})
        content = jobs._source(self.sources[1]).read_bytes()
        first = await jobs.import_asset(metadata, BytesIO(content), filename='take.wav')
        with patch.object(jobs.shutil, 'disk_usage', side_effect=AssertionError('replay storage admission')):
            second = await jobs.import_asset(metadata, BytesIO(content), filename='renamed.wav')
        self.assertEqual(first, second)
        self.assertEqual(len(db.list_tracks()), 3)
        self.assertEqual(first.selection.source.kind, 'version')
        asset = jobs.evidence.get_asset(first.evidence_asset_id)
        self.assertTrue(asset.reference_only)
        self.assertEqual(asset.origin, 'unknown')
        with self.assertRaises(jobs.ReplacementError) as conflict:
            await jobs.import_asset(metadata.model_copy(update={'title': 'Changed'}), BytesIO(content), filename='take.wav')
        self.assertEqual(conflict.exception.code, 'replacement_request_conflict')
        import json
        receipt = db.FILES_DIR / 'artist-production/sources/replacement-intake' / metadata.request_id / 'receipt.json'
        body = json.loads(receipt.read_bytes())
        body['response']['selection'] = self.sources[0].model_dump(mode='json')
        body['response']['track_id'] = self.sources[0].source.track_id
        receipt.write_text(json.dumps(body))
        with self.assertRaises(jobs.ReplacementError) as corrupt:
            await jobs.import_asset(metadata, BytesIO(content), filename='take.wav')
        self.assertEqual(corrupt.exception.code, 'replacement_artifact_changed')

    async def test_underreported_flac_decode_is_byte_bounded_and_preserves_input(self) -> None:
        from app import replacement_store as store
        path = db.FILES_DIR / 'underreported.flac'
        sf.write(path, .1 * np.sin(np.arange(80000) / 8000 * 2 * np.pi * 440), 8000, format='FLAC')
        content = bytearray(path.read_bytes())
        field = int.from_bytes(content[18:26], 'big')
        content[18:26] = ((field & ~((1 << 36) - 1)) | 8000).to_bytes(8, 'big')
        path.write_bytes(content)
        track = db.insert_track(model='upload', title='Header mismatch', lyrics='', seed=None, duration_ms=1000,
            wall_ms=None, params={}, audio_path=path, abc_path=None)
        version = audio_versions.retain_original(track, path)
        selection = SelectedSource(source=VersionMusicSource(track_id=track, version_id=version.id),
            audio_sha256=hashlib.sha256(content).hexdigest())
        plan = await jobs.create(self.request.model_copy(update={'source': selection}))
        attempt = store.StoredReplacementAttempt(id='f' * 32, plan_id=plan.id, revision=1, action='prepare',
            request=ReplacementActionRequest(expected_revision=1, request_id='e' * 32), phase='inspecting', created_at=plan.updated_at)
        with jobs._connection() as connection:
            store.claim_attempt(connection, attempt)
        with self.assertRaises(jobs.ReplacementError) as mismatch:
            await jobs._snapshot(attempt.id, selection)
        self.assertEqual(mismatch.exception.code, 'replacement_output_duration_mismatch')
        partials = list(jobs._workspace(plan.id).rglob('*decoded.partial.wav'))
        self.assertEqual(len(partials), 1)
        self.assertLessEqual(partials[0].stat().st_size, 48000 * 2 * 4 * 1.1 + 8192)
        self.assertEqual(path.read_bytes(), bytes(content))

    async def test_intake_projects_busy_and_shutdown_drains_its_owned_probe(self) -> None:
        from io import BytesIO
        from app.replacement_contracts import ReplacementAssetImport
        metadata = ReplacementAssetImport.model_validate({'request_id': 'f' * 32, 'role': 'prepared', 'title': 'Take',
            'declaration': {'role': 'reference', 'original_filename': 'take.wav'}})
        entered, release = asyncio.Event(), asyncio.Event()
        command = jobs.run_owned_command
        async def held(*args, **kwargs):
            entered.set()
            await release.wait()
            return await command(*args, **kwargs)
        with patch.object(jobs, 'run_owned_command', side_effect=held):
            task = asyncio.create_task(jobs.import_asset(metadata, BytesIO(jobs._source(self.sources[1]).read_bytes()), filename='take.wav'))
            shutdown = None
            try:
                await asyncio.wait_for(entered.wait(), 3)
                self.assertTrue(jobs.work_busy())
                shutdown = asyncio.create_task(jobs.shutdown())
                await asyncio.sleep(.01)
                self.assertFalse(shutdown.done())
            finally:
                release.set()
                await task
                if shutdown is not None:
                    await shutdown
        self.assertFalse(jobs.work_busy())

    async def test_setup_blocks_new_intake_copy_but_draft_creation_does_not_admit_processing(self) -> None:
        from io import BytesIO
        from fastapi import HTTPException
        from app.replacement_contracts import ReplacementAssetImport
        metadata = ReplacementAssetImport.model_validate({'request_id': 'f' * 32, 'role': 'prepared', 'title': 'Take',
            'declaration': {'role': 'reference', 'original_filename': 'take.wav'}})
        with patch('app.resource_admission.require_setup_idle', side_effect=HTTPException(409, 'module_setup_busy')):
            draft = await jobs.create(self.request)
            self.assertEqual(draft.status, 'draft')
            with self.assertRaises(HTTPException):
                await jobs.import_asset(metadata, BytesIO(jobs._source(self.sources[1]).read_bytes()), filename='take.wav')
        self.assertEqual(list(db.FILES_DIR.rglob('source.wav')), [])

    async def test_same_request_reattaches_before_setup_denial_and_does_not_spawn_twice(self) -> None:
        plan = await jobs.create(self.request)
        entered = asyncio.Event()
        async def held(identifier: str) -> None:
            entered.set()
            await asyncio.Event().wait()
        request = ReplacementActionRequest(expected_revision=1, request_id='a' * 32)
        with patch.object(jobs, '_run', side_effect=held), patch.object(jobs, '_require_prepare_ready'):
            accepted = await jobs.prepare(plan.id, request)
            await entered.wait()
            with patch('app.resource_admission.require_setup_idle', side_effect=AssertionError('reattach admitted new work')):
                self.assertEqual((await jobs.prepare(plan.id, request)).active_attempt_id, accepted.active_attempt_id)
            self.assertEqual(len(jobs._tasks), 1)
            await jobs.cancel(plan.id)
        self.assertFalse(jobs.work_busy())

    async def test_closing_and_low_disk_refuse_new_work_without_copy_or_job(self) -> None:
        plan = await jobs.create(self.request)
        request = ReplacementActionRequest(expected_revision=1, request_id='a' * 32)
        await jobs.shutdown()
        with self.assertRaises(jobs.ReplacementError) as closed:
            await jobs.prepare(plan.id, request)
        self.assertEqual(closed.exception.code, 'replacement_busy')
        await jobs.recover()
        with patch.object(jobs, 'storage_preview', new_callable=AsyncMock) as preview, patch.object(jobs, '_require_prepare_ready'):
            from app.replacement_contracts import ReplacementStoragePreview
            preview.return_value = ReplacementStoragePreview(source_bytes=1, retained_bytes=1,
                temporary_bytes=100, required_free_bytes=101, free_bytes=0, can_start=False)
            with self.assertRaises(jobs.ReplacementError) as low:
                await jobs.prepare(plan.id, request)
            self.assertEqual(low.exception.code, 'replacement_disk_full')
        self.assertIsNone(jobs.get(plan.id).active_attempt_id)
        self.assertFalse((db.FILES_DIR / 'replacements' / plan.id).exists())

    async def wait(self, plan_id: str):
        # Cold pitch analysis on a fresh runner can take longer than 30 seconds.
        for _ in range(9000):
            plan = jobs.get(plan_id)
            if plan.active_attempt_id is None:
                return plan
            await asyncio.sleep(.02)
        self.fail('replacement worker did not finish')

    async def test_prepared_cpu_workflow_retains_dry_inputs_requires_review_and_publishes_once(self) -> None:
        from app.replacement_contracts import AlignmentEditRequest, ReplacementMixSettings, ReplacementRenderRequest, ReplacementFitSettings
        from app.music_dsp_contracts import WarpAnchor
        from app import stems
        import json
        db.get_db().execute('UPDATE tracks SET params_json=? WHERE id=?',
            (json.dumps({'_music_evidence': {'restriction': 'noncommercial'}}), self.sources[0].source.track_id))
        db.get_db().commit()
        plan = await jobs.create(self.request.model_copy(update={'fit': ReplacementFitSettings(max_stretch=1.5,
            cleanup='light', keep_breaths=False)}))
        original = [jobs._source(source).read_bytes() for source in self.sources]
        async def separate(source, output, **options):
            output.mkdir(parents=True, exist_ok=True)
            samples, rate = sf.read(source, dtype='float32', always_2d=True)
            paths = {}
            for name, gain in (('vocals', .8), ('drums', .05), ('bass', .05), ('other', .1)):
                path = output / (name + '.wav')
                sf.write(path, samples * gain, rate, subtype='FLOAT')
                paths[name] = path
            return paths
        from types import SimpleNamespace
        with patch.object(jobs, '_require_prepare_ready'), patch.object(jobs, 'offline_demucs', return_value=SimpleNamespace(model_bytes=0)), \
                patch.object(stems, 'separate_file', side_effect=separate) as separator:
            await jobs.prepare(plan.id, ReplacementActionRequest(expected_revision=1, request_id='a' * 32))
            prepared = await self.wait(plan.id)
            self.assertEqual(prepared.status, 'needs_review', prepared.error_code)
            self.assertIsNotNone(prepared.alignment)
            self.assertFalse(prepared.alignment_reviewed)
            self.assertIsNotNone(prepared.diagnostics)
            clean = next(item for item in prepared.artifacts if item.role == 'prepared_clean')
            self.assertIsNotNone(clean.processing)
            self.assertEqual(clean.processing.low_level_attenuation_db, 3.0)
            action = ReplacementRenderRequest(expected_revision=1, request_id='b' * 32, mix=ReplacementMixSettings())
            with self.assertRaises(jobs.ReplacementError):
                await jobs.render(plan.id, action)
            alignment = prepared.alignment
            self.assertIsNotNone(alignment)
            if alignment is None:
                self.fail('missing alignment')
            self.assertEqual(alignment.request.min_duration_ratio, .75)
            self.assertEqual(alignment.request.max_duration_ratio, 1.5)
            compressed = alignment.model_copy(update={'anchors': [WarpAnchor(source_sec=0.0, target_sec=0.0),
                WarpAnchor(source_sec=3.0, target_sec=2.22)]})
            with self.assertRaises(ValueError):
                AlignmentEditRequest(expected_revision=1, alignment=compressed,
                    reviewed_phrase_ids=[phrase.phrase_id for phrase in alignment.phrases])
            outside = alignment.model_copy(update={'anchors': [WarpAnchor(source_sec=0.0, target_sec=1.0),
                WarpAnchor(source_sec=3.0, target_sec=4.0)]})
            with self.assertRaises(jobs.ReplacementError) as bounds:
                await jobs.edit_alignment(plan.id, AlignmentEditRequest(expected_revision=1,
                    alignment=outside, reviewed_phrase_ids=[phrase.phrase_id for phrase in alignment.phrases]))
            self.assertEqual(bounds.exception.code, 'replacement_alignment_invalid')
            edited = alignment.model_copy(update={'anchors': [WarpAnchor(source_sec=0.0, target_sec=0.0),
                WarpAnchor(source_sec=3.0, target_sec=3.0)]})
            reviewed = await jobs.edit_alignment(plan.id, AlignmentEditRequest(expected_revision=1,
                alignment=edited, reviewed_phrase_ids=[phrase.phrase_id for phrase in alignment.phrases]))
            self.assertTrue(reviewed.alignment_reviewed)
            with patch.object(jobs, 'align_prepared_vocal', side_effect=AssertionError('Refreshing analysis replaced reviewed timing')):
                await jobs.prepare(plan.id, ReplacementActionRequest(expected_revision=reviewed.revision, request_id='e' * 32))
                refreshed = await self.wait(plan.id)
            self.assertEqual(refreshed.status, 'needs_review', refreshed.error_code)
            self.assertEqual(refreshed.alignment, reviewed.alignment)
            self.assertEqual(refreshed.reviewed_phrase_ids, reviewed.reviewed_phrase_ids)
            self.assertTrue(refreshed.alignment_reviewed)
            final_request = ReplacementRenderRequest(expected_revision=reviewed.revision, request_id='c' * 32,
                mix=ReplacementMixSettings())
            finish = jobs._finish
            failed_once = False
            async def publication_gap(identifier, status, error=None):
                nonlocal failed_once
                if status == 'done' and not failed_once:
                    failed_once = True
                    raise OSError('injected catalog-status publication gap')
                await finish(identifier, status, error)
            with patch.object(jobs, '_finish', side_effect=publication_gap):
                await jobs.render(plan.id, final_request)
                finished = await self.wait(plan.id)
            self.assertEqual(finished.status, 'done', finished.error_code)
            self.assertEqual(len(finished.renders), 1)
            self.assertIsNotNone(finished.renders[0].track_id)
            self.assertEqual((await jobs.render(plan.id, final_request)).renders, finished.renders)
            separator.assert_awaited_once()
            from app.replacement_contracts import ReplacementMixEditRequest
            changed = ReplacementMixSettings(vocal_level='manual', manual_gain_db=1.0)
            edited_mix = await jobs.edit_mix(plan.id, ReplacementMixEditRequest(expected_revision=finished.revision, mix=changed))
            with patch.object(jobs, '_prepare', side_effect=AssertionError('remix repeated preparation')), \
                    patch.object(jobs, '_convert', side_effect=AssertionError('remix repeated Seed')), \
                    patch.object(jobs, '_convert_rvc', side_effect=AssertionError('remix repeated RVC')):
                await jobs.render(plan.id, ReplacementRenderRequest(expected_revision=edited_mix.revision,
                    request_id='d' * 32, mix=changed), action='remix')
                remixed = await self.wait(plan.id)
            self.assertEqual(remixed.status, 'done', remixed.error_code)
            self.assertEqual(len(remixed.renders), 2)
            self.assertNotEqual(remixed.renders[0].master_artifact_id, remixed.renders[1].master_artifact_id)
            separator.assert_awaited_once()
        self.assertEqual([jobs._source(source).read_bytes() for source in self.sources], original)
        self.assertEqual(len(db.list_tracks()), 4)
        track_id = finished.renders[0].track_id
        row = db.get_track(track_id)
        master = next(item for item in finished.artifacts if item.id == finished.renders[0].master_artifact_id)
        params = json.loads(row['params_json'])
        self.assertTrue(db.delete_track(self.sources[0].source.track_id))
        inherited = jobs.publication_component(track_id, master.sha256, params)
        self.assertIsNotNone(inherited)
        self.assertEqual(inherited.music_evidence.restriction, 'noncommercial')
        self.assertIsNone(jobs.publication_component(self.sources[1].source.track_id, master.sha256, params))

    async def test_rvc_conversion_uses_callers_worker_and_never_standalone_admission(self) -> None:
        from app.replacement_contracts import (ConvertReplacementRequest, RvcRegisteredModel,
            ReplacementArtifact)
        from app import replacement_store as store
        from app.rvc_models import ResolvedRvcModel
        from app.music_dsp import ResolvedDspSource
        from app.music_analysis_contracts import ProductionMusicSource
        from types import SimpleNamespace
        import asyncio
        runtime = {'files': [{'role': 'engine_source', 'filename': f'fixture/{index}.py', 'sha256': '1' * 64}
            for index in range(9)], 'configuration_sha256': '2' * 64, 'environment_sha256': '4' * 64, 'runtime': 'Fixture runtime; no real inference'}
        model = RvcRegisteredModel.model_validate({'id': 'b' * 32, 'voice_id': 'a' * 32, 'label': 'Fixture',
            'evidence_ref': 'Private fixture manifest', 'rights_declaration': 'I control the fixture recording and model.',
            'trusted_local_checkpoint': True, 'pickle_execution_acknowledged': True, 'declared_terms_acknowledged': True,
            'weights_license': {'component_id': 'voice', 'name': 'Fixture', 'scope': 'model', 'status': 'unknown',
                'notes': 'Unknown', 'reviewed_at': '2026-10-17'}, 'checkpoint_sha256': '3' * 64,
            'observed_runtime': runtime, 'created_at': '2026-10-17T10:00:00Z', 'ready': True})
        plan = await jobs.create(ConvertReplacementRequest(mode='convert', title='RVC fixture', source=self.sources[0],
            target={'engine': 'rvc', 'registered_model_id': model.id}))
        source = store.StoredReplacementArtifact(plan_id=plan.id, public=ReplacementArtifact(id='c' * 32,
            evidence_asset_id='d' * 32, role='source_vocal', sha256=self.sources[0].audio_sha256,
            source_sha256s=[self.sources[0].audio_sha256], settings_sha256='e' * 64, duration_sec=3.0,
            sample_rate_hz=8000, channels=1), relative_path=f'replacements/{plan.id}/artifacts/' + 'c' * 32 + '.wav',
            duration_frames=24000, created_at=plan.updated_at)
        audio = ResolvedDspSource(jobs._source(self.sources[0]), ProductionMusicSource(asset_id='d' * 32,
            sha256=source.public.sha256), source.public.sha256, 8000, 1, 24000)
        root = jobs._workspace(plan.id, create=True) / 'fixture'
        root.mkdir()
        proc = AsyncMock(spec=asyncio.subprocess.Process)
        proc.wait.return_value = 0
        async def spawn(identifier, argv, cwd, env, stdout):
            self.assertEqual(argv[-1], 'explicit-no-index')
            sf.write(root / 'rvc-output.wav', np.zeros(24000, dtype=np.float32), 8000, subtype='FLOAT')
            return proc
        with patch.object(jobs, '_rvc_snapshot', AsyncMock(return_value=model)), \
                patch.object(jobs, '_cached_artifact', AsyncMock(return_value=None)), \
                patch.object(jobs, '_resolved', AsyncMock(return_value=audio)), \
                patch.object(jobs, '_attempt', return_value=SimpleNamespace(authorization=None)), \
                patch.object(jobs, '_phase'), patch.object(jobs, '_spawn', side_effect=spawn), \
                patch.object(jobs, '_drain', AsyncMock()) as drain, \
                patch.object(jobs, '_retained', AsyncMock(return_value=source)) as retained, \
                patch('app.optional_engines.prepare_rvc_command', return_value=(['fixture', 'explicit-no-index'], root)), \
                patch('app.optional_engines._run', side_effect=AssertionError('recursive admission')), \
                patch('app.rvc_models.resolve', return_value=ResolvedRvcModel(model, root / 'model.pth', None)), \
                patch('app.voice_authorization.recheck') as recheck:
            result = await jobs._convert_rvc('f' * 32, model.id, source, root)
        self.assertEqual(result, source)
        self.assertGreaterEqual(recheck.call_count, 2)
        drain.assert_awaited_once()
        self.assertEqual(retained.call_args.args[1], 'converted_dry')

    async def test_real_owned_child_cancellation_drains_and_preserves_sources(self) -> None:
        import os
        import sys
        plan = await jobs.create(self.request)
        original = [jobs._source(source).read_bytes() for source in self.sources]
        async def child(identifier: str) -> None:
            await jobs._command(identifier, [sys.executable, '-c', 'import time;time.sleep(60)'], 90)
        with patch.object(jobs, '_prepare', side_effect=child), patch.object(jobs, '_require_prepare_ready'):
            accepted = await jobs.prepare(plan.id, ReplacementActionRequest(expected_revision=1, request_id='a' * 32))
            identifier = accepted.active_attempt_id
            self.assertIsNotNone(identifier)
            for _ in range(300):
                if identifier in jobs._processes:
                    break
                await asyncio.sleep(.01)
            self.assertIn(identifier, jobs._processes)
            proc = jobs._processes[identifier]
            cancelled = await jobs.cancel(plan.id)
            self.assertEqual(cancelled.status, 'cancelled')
            self.assertFalse(jobs.work_busy())
            with self.assertRaises(ProcessLookupError):
                os.kill(proc.pid, 0)
        self.assertEqual([jobs._source(source).read_bytes() for source in self.sources], original)

    async def test_remix_missing_retained_conversion_never_invokes_a_model(self) -> None:
        from app.replacement_contracts import ConvertReplacementRequest, ReplacementArtifact
        from app import replacement_store as store
        from types import SimpleNamespace
        plan = await jobs.create(ConvertReplacementRequest(mode='convert', title='Fixture', source=self.sources[0],
            target={'engine': 'seed-vc', 'voice_id': 'a' * 32, 'model_revision_id': 'trained'}))
        source = store.StoredReplacementArtifact(plan_id=plan.id, public=ReplacementArtifact(id='c' * 32,
            evidence_asset_id='d' * 32, role='source_vocal', sha256=self.sources[0].audio_sha256,
            source_sha256s=[self.sources[0].audio_sha256], settings_sha256='e' * 64, duration_sec=3.0,
            sample_rate_hz=8000, channels=1), relative_path=f'replacements/{plan.id}/artifacts/' + 'c' * 32 + '.wav',
            duration_frames=24000, created_at=plan.updated_at)
        with patch.object(jobs, '_attempt', return_value=SimpleNamespace(action='remix', plan_id=plan.id)), \
                patch.object(jobs, '_convert', side_effect=AssertionError('remix invoked Seed')), \
                patch.object(jobs, '_convert_rvc', side_effect=AssertionError('remix invoked RVC')):
            with self.assertRaises(jobs.ReplacementError) as error:
                await jobs._conversion_dry('f' * 32, source, self.root)
        self.assertEqual(error.exception.code, 'replacement_artifact_missing')

    async def test_convert_pitch_reaches_dsp_and_cannot_silently_bypass_missing_native_support(self) -> None:
        from app.replacement_contracts import ConvertReplacementRequest, ReplacementArtifact, ReplacementFitSettings
        from app import replacement_store as store
        from app.music_dsp import MusicDspError, ResolvedDspSource
        from app.music_analysis_contracts import ProductionMusicSource
        from types import SimpleNamespace
        plan = await jobs.create(ConvertReplacementRequest(mode='convert', title='Fixture', source=self.sources[0],
            fit=ReplacementFitSettings(pitch_shift_semitones=7),
            target={'engine': 'seed-vc', 'voice_id': 'a' * 32, 'model_revision_id': 'trained'}))
        source = store.StoredReplacementArtifact(plan_id=plan.id, public=ReplacementArtifact(id='c' * 32,
            evidence_asset_id='d' * 32, role='source_vocal', sha256=self.sources[0].audio_sha256,
            source_sha256s=[self.sources[0].audio_sha256], settings_sha256='e' * 64, duration_sec=3.0,
            sample_rate_hz=8000, channels=1), relative_path=f'replacements/{plan.id}/artifacts/' + 'c' * 32 + '.wav',
            duration_frames=24000, created_at=plan.updated_at)
        audio = ResolvedDspSource(jobs._source(self.sources[0]), ProductionMusicSource(asset_id='d' * 32,
            sha256=source.public.sha256), source.public.sha256, 8000, 1, 24000)
        async def unavailable(request, resolved, root, run, **options):
            self.assertEqual(request.pitch_shift_semitones, 7.0)
            raise MusicDspError('dsp_stretch_unavailable')
        with patch.object(jobs, '_cached_artifact', AsyncMock(return_value=None)), \
                patch.object(jobs, '_resolved', AsyncMock(return_value=audio)), \
                patch.object(jobs, '_attempt', return_value=SimpleNamespace(action='render')), \
                patch.object(jobs, 'render_transform', side_effect=unavailable) as transform:
            with self.assertRaises(MusicDspError):
                await jobs._convert_performance('f' * 32, source, self.root, AsyncMock(), None)
        transform.assert_awaited_once()

    async def test_uncertain_drain_keeps_source_mutation_blocked_until_verified(self) -> None:
        import sys
        plan = await jobs.create(self.request)
        async def child(identifier: str) -> None:
            await jobs._command(identifier, [sys.executable, '-c', 'import time;time.sleep(60)'], 90)
        with patch.object(jobs, '_prepare', side_effect=child), patch.object(jobs, '_require_prepare_ready'):
            accepted = await jobs.prepare(plan.id, ReplacementActionRequest(expected_revision=1, request_id='a' * 32))
            for _ in range(300):
                if accepted.active_attempt_id in jobs._processes:
                    break
                await asyncio.sleep(.01)
            try:
                with patch.object(jobs, 'kill_process_tree', AsyncMock(side_effect=OSError('unverified test drain'))):
                    cancelled = await jobs.cancel(plan.id)
                    self.assertEqual(cancelled.error_code, 'replacement_cleanup_pending')
                    self.assertIsNotNone(cancelled.active_attempt_id)
                    self.assertTrue(jobs.work_busy())
                    with self.assertRaises(jobs.ReplacementError):
                        async with jobs.protect_track_removal(self.sources[0].source.track_id):
                            self.fail('mutation proceeded with an unverified writer')
            finally:
                await jobs.cancel(plan.id)
        self.assertFalse(jobs.work_busy())
