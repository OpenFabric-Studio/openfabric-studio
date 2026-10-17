from __future__ import annotations

import unittest
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, patch
import numpy as np
import soundfile as sf
from app import beat_song_mix
from app.beat_contracts import BeatScale
from app.beat_contracts_test import document_fixture, manifest_fixture
from app.beat_song_mix_contracts import BeatSongMixError, SongSourceInfo
from app.beat_song_mix_contracts_test import draft_fixture
from app.export_provenance_contracts import MusicEvidence


def info_fixture(index: int = 1) -> SongSourceInfo:
    source = draft_fixture().beat.source if index == 1 else draft_fixture().song.source
    return SongSourceInfo(source=source, sha256=('a' if index == 1 else 'b') * 64,
        source_bytes=768044, sample_rate_hz=48000, channels=2, frames=96000, duration_sec=2,
        track_id=index, version_id=('a' if index == 1 else 'b') * 32,
        music_evidence=MusicEvidence(), content_origin='unknown')


class BeatSongPlanTest(unittest.TestCase):
    def plan(self, draft=None, beat=None, song=None):
        return beat_song_mix.build_plan(draft or draft_fixture(), beat or info_fixture(), song or info_fixture(2),
            plan_id='c' * 32, revision=0, separator_ready=False, native_ready=False)

    def test_layer_is_available_without_separator_and_manual_uncertainty_is_visible(self) -> None:
        plan = self.plan()
        self.assertFalse(plan.needs_native)
        self.assertEqual([lane.role for lane in plan.lanes], ['mix', 'mix'])
        self.assertIn('key_unknown', plan.warnings)
        self.assertEqual(beat_song_mix.plan_digest(plan), plan.sha256)

    def test_drifting_map_is_retained_and_target_source_ratio_direction_is_correct(self) -> None:
        original = draft_fixture().model_dump()
        original['anchors'] = [{'source_sec': index / 2, 'target_sec': target}
            for index, target in enumerate((0, .50, 1.01, 1.53, 2.06))]
        original['song']['excerpt_end_sec'] = 2.06
        song = info_fixture(2).model_copy(update={'frames': 98880, 'duration_sec': 2.06})
        from app.beat_song_mix_contracts import BeatSongMixDraft
        plan = self.plan(BeatSongMixDraft.model_validate(original), song=song)
        self.assertTrue(plan.needs_native)
        self.assertAlmostEqual(plan.maximum_ratio, 1.06)
        self.assertEqual(plan.draft.anchors[-1].target_sec, 2.06)
        for ratio in (.75, 1.5):
            draft = draft_fixture().model_copy(update={'anchors': [
                plan.draft.anchors[0], plan.draft.anchors[-1].model_copy(update={'source_sec': 2, 'target_sec': ratio * 2})],
                'song': draft_fixture().song.model_copy(update={'excerpt_end_sec': ratio * 2})})
            result = self.plan(draft, song=song.model_copy(update={'frames': round(ratio * 96000), 'duration_sec': ratio * 2}))
            self.assertAlmostEqual(result.maximum_ratio, ratio)

    def test_endpoint_or_claimed_symbolic_lineage_mismatch_is_rejected(self) -> None:
        draft = draft_fixture().model_copy(update={'anchors': draft_fixture().anchors[:-1] + [
            draft_fixture().anchors[-1].model_copy(update={'source_sec': 1.9})]})
        with self.assertRaises(BeatSongMixError) as caught:
            self.plan(draft)
        self.assertEqual(caught.exception.code, 'beat_mix_alignment_invalid')

    def test_replace_drums_cannot_reenable_original_drums_through_explicit_controls(self) -> None:
        from app.beat_song_mix_contracts import BeatSongLaneControl
        draft = draft_fixture().model_copy(update={'mode': 'replace_drums', 'controls': [
            BeatSongLaneControl(lane_id=beat_song_mix.lane_id('song', 'drums'), muted=False)]})
        with self.assertRaises(BeatSongMixError) as caught:
            self.plan(draft)
        self.assertEqual(caught.exception.code, 'beat_mix_drums_required')

    def test_audio_loop_and_pitch_require_explicit_approximation(self) -> None:
        draft = draft_fixture().model_copy(update={'audio_pitch_semitones': 1})
        with self.assertRaises(BeatSongMixError) as caught:
            self.plan(draft)
        self.assertEqual(caught.exception.code, 'beat_mix_key_review_required')
        plan = self.plan(draft.model_copy(update={'audio_approximation': True}))
        self.assertIn('audio_pitch_approximation', plan.warnings)

    def test_symbolic_transpose_keeps_drums_cents_reference_and_scale_intervals(self) -> None:
        document = document_fixture(manifest_fixture())
        first = document.patterns[0].lanes[0]
        pitched = first.model_copy(update={'lane_id': 'pitched', 'role': 'pitched',
            'steps': [first.steps[0].model_copy(update={'note_midi': 60, 'cents': 17})]})
        pattern = document.patterns[0].model_copy(update={'lanes': [pitched, *document.patterns[0].lanes[1:]]})
        document = document.model_copy(update={'patterns': [pattern], 'scale': BeatScale(scale_id='custom',
            root_note_midi=60, degrees_cents=[0, 204, 386], reference_hz=432)})
        transposed = beat_song_mix.transpose_document(document, 2)
        self.assertEqual(transposed.patterns[0].lanes[0].steps[0].note_midi, 62)
        self.assertEqual(transposed.patterns[0].lanes[0].steps[0].cents, 17)
        self.assertEqual(transposed.patterns[0].lanes[1:], document.patterns[0].lanes[1:])
        self.assertEqual(transposed.scale.degrees_cents, [0, 204, 386])
        self.assertEqual(transposed.scale.reference_hz, 432)
        self.assertEqual(transposed.scale.root_note_midi, 62)
        self.assertEqual(document.patterns[0].lanes[0].steps[0].note_midi, 60)


class BeatSongOwnerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app import audio_versions, db, beat_song_mix_store, song_sources
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        for name, value in (('_db', None), ('DATA_DIR', self.root), ('FILES_DIR', self.root / 'files'), ('DB_PATH', self.root / 'db.sqlite')):
            self.enterContext(patch.object(db, name, value))
        for name, value in (('_tasks', {}), ('_processes', {}), ('_unverified', {}), ('_closing', False),
                ('_spawn_quarantine', set()), ('_lock', asyncio.Lock()), ('_semaphore', asyncio.Semaphore(1)), ('_retaining', 0)):
            self.enterContext(patch.object(beat_song_mix, name, value))
        self.connection = db.get_db()
        await song_sources.recover()
        beat_song_mix_store.migrate(self.connection)
        inputs = []
        for index in (1, 2):
            path = db.FILES_DIR / f'source-{index}.wav'
            path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(path, np.ones((9600, 2), dtype=np.float32) * .01 * index, 48000, subtype='FLOAT')
            track = db.insert_track(model='upload', title='Source', lyrics='', seed=None, duration_ms=200,
                wall_ms=None, params={}, audio_path=path, abc_path=None)
            version = audio_versions.retain_original(track, path)
            inputs.append({'source': {'kind': 'version', 'track_id': track, 'version_id': version.id},
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'excerpt_end_sec': .2})
        from app.beat_song_mix_contracts import BeatSongMixDraft
        self.draft = BeatSongMixDraft(title='Owned layer', beat=inputs[0], song=inputs[1], project_bpm=120,
            project_meter={'numerator': 4, 'denominator': 4}, anchors=[{'source_sec': 0, 'target_sec': 0}, {'source_sec': .2, 'target_sec': .2}])

    async def asyncTearDown(self) -> None:
        from app import song_sources
        await beat_song_mix.shutdown()
        await song_sources.shutdown()
        self.connection.close()

    async def finish(self, identifier):
        task = beat_song_mix._tasks.get(identifier)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), 30)
        return beat_song_mix.get_job(identifier)

    async def test_snapshot_before_enqueue_reload_and_retry_save_publish_once_after_source_deletion(self) -> None:
        from app import audio_versions, beat_projects, db
        from app.beat_song_mix_contracts import BeatSongMixStartRequest
        with patch('app.song_sources.track_music_evidence', return_value=MusicEvidence(restriction='noncommercial')):
            preview = await beat_song_mix.preview(self.draft)
            plan = preview.plan
            self.assertEqual(beat_song_mix.list_jobs(), [])
            request = BeatSongMixStartRequest(plan_id=plan.id, expected_revision=0, plan_sha256=plan.sha256, submission_key='test')
            job = await beat_song_mix.start(request)
        for item in (self.draft.beat, self.draft.song):
            audio_versions.resolve_source(item.source.track_id, item.source.version_id).unlink()
        self.assertEqual((await beat_song_mix.start(request)).id, job.id)
        with patch.object(beat_projects, 'publish_audio_project', side_effect=OSError('private failure')):
            saved = await self.finish(job.id)
        self.assertEqual(saved.status, 'saving', saved)
        with patch.object(beat_song_mix, '_execute', new=AsyncMock(side_effect=AssertionError('retry rerendered'))):
            done = await beat_song_mix.retry_save(job.id)
        self.assertEqual(done.status, 'done', done)
        self.assertEqual(done.manifest.music_evidence.restriction, 'noncommercial')
        self.assertIsNotNone(done.manifest.project_id)
        row = db.get_project(done.manifest.project_id)
        project = json.loads(row['data_json'])
        self.assertTrue(all(not clip['warpEnabled'] for lane in project['lanes'] for clip in lane['clips']))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM tracks WHERE model='beat_song'").fetchone()[0], 1)
        self.assertEqual((await beat_song_mix.retry_save(job.id)).manifest, done.manifest)
        from app.contracts import JsonObject
        from pydantic import TypeAdapter
        track = db.get_track(done.manifest.track_id)
        params = TypeAdapter(JsonObject).validate_json(track['params_json'])
        mix = next(item for item in done.manifest.artifacts if item.kind == 'mix')
        component = beat_song_mix.publication_component(done.manifest.track_id, mix.sha256, params)
        self.assertEqual(component.music_evidence.restriction, 'noncommercial')
        from app.export_provenance import track_component
        self.assertEqual(track_component(done.manifest.track_id, 'original', mix.sha256), component)
        self.assertIsNone(beat_song_mix.publication_component(done.manifest.track_id + 999, mix.sha256, params))
        self.assertIsNone(beat_song_mix.publication_component(done.manifest.track_id, '0' * 64, params))

    async def test_replace_drums_missing_setup_is_blocked_before_source_copies(self) -> None:
        from app.beat_song_mix_contracts import BeatSongMixStartRequest
        from app.voice_separation import VoiceSeparationError
        with patch.object(beat_song_mix, 'offline_demucs', side_effect=VoiceSeparationError('separation_unavailable')):
            preview = await beat_song_mix.preview(self.draft.model_copy(update={'mode': 'replace_drums'}))
            self.assertFalse(preview.plan.separator_ready)
            request = BeatSongMixStartRequest(plan_id=preview.plan.id, expected_revision=0,
                plan_sha256=preview.plan.sha256, submission_key='replace')
            with self.assertRaises(BeatSongMixError) as caught:
                await beat_song_mix.start(request)
        self.assertEqual(caught.exception.code, 'beat_mix_drums_required')
        self.assertEqual(self.connection.execute('SELECT count(*) FROM beat_song_mix_jobs').fetchone()[0], 0)

    async def test_real_owned_process_cancellation_drains_and_retains_source_bytes(self) -> None:
        import sys
        from app import song_sources, beat_song_mix_store
        from app.beat_song_mix_contracts import BeatSongMixStartRequest
        plan = (await beat_song_mix.preview(self.draft)).plan
        entered = asyncio.Event()
        processes = []

        async def wait(job_id):
            process = await beat_song_mix._spawn(job_id, [sys.executable, '-c', 'import time; time.sleep(60)'])
            processes.append(process)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await beat_song_mix._drain(job_id, process)
            raise AssertionError('cancel did not interrupt')

        with patch.object(beat_song_mix, '_execute', side_effect=wait):
            job = await beat_song_mix.start(BeatSongMixStartRequest(plan_id=plan.id, expected_revision=0,
                plan_sha256=plan.sha256, submission_key='cancel'))
            await asyncio.wait_for(entered.wait(), 10)
            cancelled = await beat_song_mix.cancel(job.id)
        self.assertEqual(cancelled.status, 'cancelled')
        self.assertTrue(all(process.returncode is not None for process in processes))
        self.assertFalse(beat_song_mix.work_busy())
        record = beat_song_mix_store.get_job(job.id)
        for source in record.sources:
            self.assertTrue((await song_sources.resolve_snapshot(song_sources.owned_root(job.id), source)).is_file())

    async def test_completed_manifest_is_adopted_after_restart_without_rendering_again(self) -> None:
        from app import beat_projects, beat_song_mix_store
        from app.beat_song_mix_contracts import BeatSongMixStartRequest
        plan = (await beat_song_mix.preview(self.draft)).plan
        with patch.object(beat_projects, 'publish_audio_project', side_effect=OSError('simulated restart boundary')):
            job = await beat_song_mix.start(BeatSongMixStartRequest(plan_id=plan.id, expected_revision=0,
                plan_sha256=plan.sha256, submission_key='recover'))
            saved = await self.finish(job.id)
        self.assertEqual(saved.status, 'saving')
        beat_song_mix_store.save_job(saved.model_copy(update={'status': 'running', 'manifest': None}))
        with patch.object(beat_song_mix, '_execute', new=AsyncMock(side_effect=AssertionError('recovery rerendered'))):
            await beat_song_mix.recover()
        done = beat_song_mix.get_job(job.id)
        self.assertEqual(done.status, 'done')
        self.assertIsNotNone(beat_song_mix.project(job.id).project_id)

    async def test_failed_spawn_without_returned_identity_stays_blocked_and_busy(self) -> None:
        from app.beat_song_mix_contracts import BeatSongMixStartRequest
        plan = (await beat_song_mix.preview(self.draft)).plan
        with patch.object(beat_song_mix, 'spawn_owned', side_effect=OSError('creation drain unverified')):
            job = await beat_song_mix.start(BeatSongMixStartRequest(plan_id=plan.id, expected_revision=0,
                plan_sha256=plan.sha256, submission_key='unknown-spawn'))
            blocked = await self.finish(job.id)
        self.assertEqual(blocked.status, 'blocked')
        self.assertTrue(beat_song_mix.work_busy())
        await beat_song_mix.recover()
        self.assertEqual(beat_song_mix.get_job(job.id).status, 'blocked')
        self.assertTrue(beat_song_mix.work_busy())
