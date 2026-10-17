from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import numpy as np
import soundfile as sf

from app import audio_versions, beat_projects, db, mashup_render, mashup_store, mashups
from app.mashup_contracts import (MashupCreateRequest, MashupError, MashupGeneratePlanRequest,
    MashupJobRequest, MashupSourceInput, MashupSourcePreviewRequest, MashupTiming, MashupTimingUpdate)
from app.voice_separation import OfflineDemucs, VoiceSeparationError
from app.export_provenance_contracts import MusicEvidence
from app.video_process import WorkerIdentity


class MashupsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        for name, value in (('_db', None), ('DATA_DIR', self.root), ('FILES_DIR', self.root / 'files'),
                ('DB_PATH', self.root / 'catalog.db')):
            self.enterContext(patch.object(db, name, value))
        for name, value in (('_tasks', {}), ('_processes', {}), ('_unverified', {}), ('_closing', False),
                ('_lock', asyncio.Lock()), ('_semaphore', asyncio.Semaphore(1))):
            self.enterContext(patch.object(mashups, name, value))
        self.connection = db.get_db()
        mashup_store.migrate(self.connection)
        self.ready = OfflineDemucs(Path(sys.executable), self.root, 'htdemucs', (), b'')
        self.enterContext(patch.object(mashups, 'offline_demucs', return_value=self.ready))
        self.enterContext(patch.object(mashups, '_separate', side_effect=self.separate))
        self.sources = [self.source(1), self.source(2)]
        self.request = MashupCreateRequest(title='Pair', submission_key='create',
            source_a=self.sources[0], source_b=self.sources[1])

    async def asyncTearDown(self) -> None:
        await mashups.shutdown()
        self.connection.close()

    def source(self, index: int) -> MashupSourceInput:
        path = db.FILES_DIR / f'source-{index}.wav'
        path.parent.mkdir(parents=True, exist_ok=True)
        phase = np.arange(96000, dtype=np.float32) / 48000
        signal = .08 * np.sin(2 * np.pi * (110 * index) * phase)
        sf.write(path, np.column_stack((signal, -signal)), 48000, subtype='FLOAT')
        track = db.insert_track(model='upload', title=f'Source {index}', lyrics='', seed=None,
            duration_ms=2000, wall_ms=None, params={}, audio_path=path, abc_path=None)
        version = audio_versions.retain_original(track, path)
        return MashupSourceInput(source={'kind': 'version', 'track_id': track, 'version_id': version.id},
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), excerpt_end_sec=2)

    async def separate(self, job_id: str, audio: Path, output: Path, ready: OfflineDemucs) -> dict[str, Path]:
        output.mkdir(parents=True, exist_ok=True)
        samples, rate = sf.read(audio, dtype='float32', always_2d=True)
        result = {}
        for stem in ('vocals', 'drums', 'bass', 'other'):
            path = output / (stem + '.wav')
            sf.write(path, samples * .25, rate, subtype='FLOAT')
            result[stem] = path
        return result

    async def finished(self, job_id: str):
        task = mashups._tasks.get(job_id)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), 30)
        return mashups.get_job(job_id)

    async def planned(self):
        created = await mashups.create(self.request)
        if created.active_job_id is None:
            self.fail('missing preparation job')
        prepared = await self.finished(created.active_job_id)
        self.assertEqual(prepared.status, 'done', prepared)
        state = mashup_store.get(created.id)
        for side in ('a', 'b'):
            state = mashups.update_timing(state.id, MashupTimingUpdate.model_validate({'expected_revision': state.revision,
                'source': side, 'timing': MashupTiming(bpm=120, meter={'numerator': 4, 'denominator': 4},
                    downbeat_frames=[0, 96000], sections=[{'id': 'S', 'start_frame': 0, 'end_frame': 96000, 'bars': 1}]).model_dump()}))
        return mashups.generate_plan(state.id, MashupGeneratePlanRequest(expected_revision=state.revision,
            orientation='a_vocals_b_backing', target_bpm=120, meter={'numerator': 4, 'denominator': 4}))

    async def test_preparation_retains_inputs_reload_and_duplicate_do_not_resubmit(self) -> None:
        created = await mashups.create(self.request)
        self.assertEqual((await mashups.create(self.request)).id, created.id)
        if created.active_job_id is None:
            self.fail('missing preparation job')
        self.assertEqual((await self.finished(created.active_job_id)).status, 'done')
        state = mashup_store.get(created.id)
        self.assertEqual(state.status, 'ready')
        self.assertEqual(len(state.source_a.stems), 4)
        original = mashup_render.source_dir(state.id, 'a') / state.source_a.raw_filename
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), self.sources[0].sha256)
        with patch.object(mashups, '_prepare_source', new=AsyncMock(side_effect=AssertionError('reload resubmitted'))):
            await mashups.recover()
        self.assertEqual(mashup_store.get(state.id).source_a, state.source_a)
        self.assertNotIn(str(self.root), state.model_dump_json())

    async def test_full_render_publishes_once_and_keeps_old_revision_and_four_editable_lanes(self) -> None:
        with patch.object(mashups, 'track_music_evidence', return_value=MusicEvidence(restriction='noncommercial')):
            state = await self.planned()
        request = MashupJobRequest(expected_revision=state.revision, submission_key='full')
        job = await mashups.start_render(state.id, request)
        self.assertEqual((await mashups.start_render(state.id, request)).id, job.id)
        mashup_store.update(state.id, state.revision, state.model_copy(update={'title': 'Newer draft'}))
        # Retained sources, not mutable library paths, drive the render.
        for source in self.sources:
            original = audio_versions.resolve_source(source.source.track_id, source.source.version_id)
            original.unlink()
        with patch.object(beat_projects, 'publish_audio_project', side_effect=OSError('private simulated failure')):
            failed = await self.finished(job.id)
            self.assertEqual(failed.status, 'saving', failed)
            await mashups.recover()
            self.assertEqual(mashups.get_job(job.id).status, 'saving')
        with patch.object(mashups, '_execute_render', new=AsyncMock(side_effect=AssertionError('publication rerendered'))):
            finished = await mashups.retry_save(job.id)
        self.assertEqual(finished.status, 'done', finished)
        manifest = finished.manifest
        if manifest is None or manifest.track_id is None or manifest.project_id is None:
            self.fail('missing publication')
        row = db.get_project(manifest.project_id)
        if row is None:
            self.fail('missing editor project')
        project = json.loads(row['data_json'])
        self.assertEqual(len(project['lanes']), 4)
        self.assertTrue(all(lane['clips'] for lane in project['lanes']))
        self.assertEqual(project['musicProvenance']['workflow_revision'], state.revision)
        self.assertEqual(manifest.music_evidence.restriction, 'noncommercial')
        track = db.get_track(manifest.track_id)
        if track is None:
            self.fail('missing track')
        self.assertEqual(json.loads(track['params_json'])['_music_evidence']['restriction'], 'noncommercial')
        from app.contracts import JsonObject
        from pydantic import TypeAdapter
        params = TypeAdapter(JsonObject).validate_json(track['params_json'])
        mix = next(item for item in manifest.artifacts if item.kind == 'mix')
        component = mashups.publication_component(manifest.track_id, mix.sha256, params)
        self.assertIsNotNone(component)
        self.assertEqual(component.music_evidence.restriction, 'noncommercial')
        from app.export_provenance import track_component
        self.assertEqual(track_component(manifest.track_id, 'original', mix.sha256), component)
        self.assertIsNone(mashups.publication_component(manifest.track_id + 999, mix.sha256, params))
        self.assertIsNone(mashups.publication_component(manifest.track_id, '0' * 64, params))
        self.assertIsNone(mashups.publication_component(manifest.track_id, mix.sha256, {**params, 'mashup_revision': state.revision + 1}))
        self.assertEqual(mashup_store.get(state.id).title, 'Newer draft')
        with patch.object(mashups, '_execute_render', new=AsyncMock(side_effect=AssertionError('retry rerendered'))):
            repeated = await mashups.retry_save(job.id)
        self.assertEqual(repeated.manifest, manifest)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM tracks WHERE model='mashup'").fetchone()[0], 1)

    async def test_missing_separator_refuses_before_retaining_or_starting_models(self) -> None:
        with patch.object(mashups, 'offline_demucs', side_effect=VoiceSeparationError('separation_unavailable')), \
                patch.object(mashups, '_separate', new=AsyncMock()) as separator:
            with self.assertRaises(MashupError) as caught:
                await mashups.create(self.request)
            self.assertEqual(caught.exception.code, 'mashup_separation_needed')
            separator.assert_not_awaited()
        self.assertEqual(self.connection.execute('SELECT count(*) FROM mashup_jobs').fetchone()[0], 0)

    async def test_queued_cancel_preserves_both_raw_inputs_and_becomes_terminal(self) -> None:
        created = await mashups.create(self.request)
        if created.active_job_id is None:
            self.fail('missing preparation')
        cancelled = await mashups.cancel(created.active_job_id)
        self.assertEqual(cancelled.status, 'cancelled')
        self.assertEqual(mashup_store.get(created.id).status, 'cancelled')
        for side, source in (('a', created.source_a), ('b', created.source_b)):
            raw = mashup_render.source_dir(created.id, side) / source.raw_filename
            self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(), source.input.sha256)
        self.assertFalse(mashups.work_busy())

    async def test_real_process_cancel_drains_and_retry_reuses_verified_source_receipt(self) -> None:
        created = await mashups.create(self.request)
        if created.active_job_id is None:
            self.fail('missing preparation')
        blocked = asyncio.Event()
        real_read = mashups.read_owned_output
        async def hold(process, *, max_bytes, timeout):
            blocked.set()
            await asyncio.Event().wait()
            return await real_read(process, max_bytes=max_bytes, timeout=timeout)
        with patch.object(mashups, 'read_owned_output', side_effect=hold):
            await asyncio.wait_for(blocked.wait(), 10)
            process = mashups._processes[created.active_job_id]
            cancelled = await mashups.cancel(created.active_job_id)
        self.assertEqual(cancelled.status, 'cancelled')
        self.assertIsNotNone(process.returncode)
        self.assertEqual(mashups._processes, {})
        self.assertFalse(mashups.work_busy())
        self.assertFalse(any(path.name.startswith('prepare-') for path in mashup_render.job_dir(created.active_job_id).iterdir()))
        retried = await mashups.retry_preparation(created.id)
        if retried.active_job_id is None:
            self.fail('missing retry')
        complete = await self.finished(retried.active_job_id)
        self.assertEqual(complete.status, 'done', complete)

    async def test_recovery_adopts_completed_preparation_after_state_commit_without_restart(self) -> None:
        created = await mashups.create(self.request)
        if created.active_job_id is None:
            self.fail('missing preparation')
        complete = await self.finished(created.active_job_id)
        mashup_store.save_job(complete.model_copy(update={'status': 'running'}))
        with patch.object(mashups, '_prepare_source', new=AsyncMock(side_effect=AssertionError('recovery launched model'))):
            await mashups.recover()
        self.assertEqual(mashups.get_job(complete.id).status, 'done')

    async def test_source_receipt_cannot_replace_captured_restrictions(self) -> None:
        with patch.object(mashups, 'track_music_evidence', return_value=MusicEvidence(restriction='noncommercial')):
            created = await mashups.create(self.request)
        if created.active_job_id is None:
            self.fail('missing preparation')
        await self.finished(created.active_job_id)
        state = mashup_store.get(created.id)
        source = state.source_a
        receipt = mashup_render.source_dir(state.id, 'a') / 'source.json'
        forged = source.model_copy(update={'music_evidence': MusicEvidence()})
        mashup_render.write_model(receipt, forged)
        with self.assertRaises(MashupError) as caught:
            await mashups._verified_source(state.id, source)
        self.assertEqual(caught.exception.code, 'mashup_source_changed')

    async def test_actual_render_rechecks_disk_after_queueing(self) -> None:
        state = await self.planned()
        await mashups._semaphore.acquire()
        try:
            job = await mashups.start_render(state.id, MashupJobRequest(expected_revision=state.revision, submission_key='disk'))
            with patch.object(mashup_render, 'free_bytes', return_value=0), \
                    patch.object(mashup_render, 'render', new=AsyncMock()) as render:
                mashups._semaphore.release()
                complete = await self.finished(job.id)
            self.assertEqual(complete.status, 'failed')
            self.assertEqual(complete.error.code if complete.error else None, 'insufficient_disk')
            render.assert_not_awaited()
        finally:
            if mashups._semaphore.locked():
                mashups._semaphore.release()

    async def test_wrong_source_hash_fails_without_an_accepted_job_or_partial_library_copy(self) -> None:
        bad = self.request.model_copy(update={'source_a': self.request.source_a.model_copy(update={'sha256': '0' * 64})})
        with self.assertRaises(MashupError) as caught:
            await mashups.create(bad)
        self.assertEqual(caught.exception.code, 'mashup_source_changed')
        self.assertFalse(mashups.work_busy())
        self.assertEqual(self.connection.execute('SELECT count(*) FROM mashup_jobs').fetchone()[0], 0)
        documents = mashup_render.root() / 'documents'
        self.assertEqual(list(documents.iterdir()), [])

    async def test_separator_timeout_is_typed_and_keeps_retained_inputs(self) -> None:
        with patch.object(mashups, '_separate', new=AsyncMock(side_effect=TimeoutError('private runtime detail'))):
            created = await mashups.create(self.request)
            if created.active_job_id is None:
                self.fail('missing preparation')
            complete = await self.finished(created.active_job_id)
        self.assertEqual(complete.status, 'failed')
        self.assertEqual(complete.error.code if complete.error else None, 'mashup_separation_failed')
        self.assertNotIn('private runtime', complete.model_dump_json())
        self.assertTrue((mashup_render.source_dir(created.id, 'a') / created.source_a.raw_filename).is_file())

    async def test_readonly_source_forecast_reports_actual_facts_and_unknown_readiness_without_starting(self) -> None:
        request = MashupSourcePreviewRequest(source=self.sources[0].source, excerpt_end_sec=2)
        with patch.object(mashups, 'offline_demucs', side_effect=VoiceSeparationError('separation_unavailable')), \
                patch.object(mashup_render, 'free_bytes', return_value=0), \
                patch.object(mashups, '_separate', new=AsyncMock()) as separate:
            preview = await mashups.source_preview(request)
        self.assertEqual((preview.sha256, preview.duration_sec, preview.channels), (self.sources[0].sha256, 2, 2))
        self.assertFalse(preview.separator_ready)
        self.assertFalse(preview.can_start)
        self.assertGreater(preview.required_free_bytes, preview.retained_bytes)
        separate.assert_not_awaited()
        self.assertFalse(mashups.work_busy())
        self.assertEqual(self.connection.execute('SELECT count(*) FROM mashup_jobs').fetchone()[0], 0)

    async def test_foreign_worker_receipt_blocks_new_work_until_exit_is_verified(self) -> None:
        created = await mashups.create(self.request)
        if created.active_job_id is None:
            self.fail('missing preparation')
        job = await mashups.cancel(created.active_job_id)
        mashup_store.save_job(job.model_copy(update={'status': 'running'}))
        identity = WorkerIdentity(pid=12345, token='f' * 32, receipt=str(self.root / 'foreign.json'))
        mashup_store.identity(job.id, identity)
        with patch.object(mashups, 'terminate_verified', new=AsyncMock(return_value=False)):
            await mashups.recover()
            self.assertEqual(mashups.get_job(job.id).status, 'blocked')
            self.assertTrue(mashups.work_busy())
            with self.assertRaises(MashupError) as caught:
                await mashups.create(self.request.model_copy(update={'submission_key': 'another'}))
            self.assertEqual(caught.exception.code, 'mashup_worker_unverified')
            await mashups.shutdown()
            self.assertTrue(mashups.work_busy())
        with patch.object(mashups, 'terminate_verified', new=AsyncMock(return_value=True)):
            await mashups.recover()
        self.assertFalse(mashups.work_busy())

    async def test_two_individually_affordable_sources_cannot_overbook_combined_preparation(self) -> None:
        requirements = []
        for source in self.sources:
            path = audio_versions.resolve_source(source.source.track_id, source.source.version_id)
            _, _, required = mashup_render.preparation_budget(path.stat().st_size, 2, model_bytes=self.ready.model_bytes)
            requirements.append(required)
        with patch.object(mashup_render, 'free_bytes', return_value=max(requirements)), \
                patch.object(mashups, '_separate', new=AsyncMock()) as separate:
            for source in self.sources:
                preview = await mashups.source_preview(MashupSourcePreviewRequest(source=source.source, excerpt_end_sec=2))
                self.assertTrue(preview.can_start)
                self.assertTrue(preview.separator_ready)
            with self.assertRaises(MashupError) as caught:
                await mashups.create(self.request)
        self.assertEqual(caught.exception.code, 'insufficient_disk')
        separate.assert_not_awaited()
        self.assertEqual(self.connection.execute('SELECT count(*) FROM mashup_jobs').fetchone()[0], 0)
        self.assertFalse(mashups.work_busy())


if __name__ == '__main__':
    unittest.main()
