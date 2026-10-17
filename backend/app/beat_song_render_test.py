from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import numpy as np
import soundfile as sf

from app import audio_versions, beat_song_render, db, song_sources
from app.beat_song_mix import build_plan
from app.beat_song_mix_contracts import BeatSongMixDraft, BeatSongMixJob, BeatSongMixStartRequest
from app.music_analysis_contracts import VersionMusicSource
from scripts.music_analysis_worker import run_command


class BeatSongRenderTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        for name, value in (('_db', None), ('DATA_DIR', self.root), ('FILES_DIR', self.root / 'files'), ('DB_PATH', self.root / 'db.sqlite')):
            self.enterContext(patch.object(db, name, value))
        self.connection = db.get_db()
        await song_sources.recover()
        self.addAsyncCleanup(self.close)
        self.sources = []
        self.samples = []
        self.job_id = 'c' * 32
        for index in (1, 2):
            phase = np.arange(96000, dtype=np.float32) / 48000
            samples = np.column_stack((.03 * np.sin(2 * np.pi * index * 100 * phase),
                .04 * np.sin(2 * np.pi * index * 100 * phase)))
            path = db.FILES_DIR / f'source-{index}.wav'
            path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(path, samples, 48000, subtype='FLOAT')
            track = db.insert_track(model='upload', title='Source', lyrics='', seed=None, duration_ms=2000,
                wall_ms=None, params={}, audio_path=path, abc_path=None)
            version = audio_versions.retain_original(track, path)
            source = VersionMusicSource(track_id=track, version_id=version.id)
            snapshot = await song_sources.retain_source(source, song_sources.owned_root(self.job_id, create=True),
                source_id=f'{index:032x}', expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            self.sources.append(snapshot)
            self.samples.append(samples)
        draft = BeatSongMixDraft(title='Layer', beat={'source': self.sources[0].source.model_dump(),
            'sha256': self.sources[0].sha256, 'excerpt_end_sec': 2}, song={'source': self.sources[1].source.model_dump(),
            'sha256': self.sources[1].sha256, 'excerpt_end_sec': 2}, project_bpm=120,
            project_meter={'numerator': 4, 'denominator': 4}, anchors=[{'source_sec': 0, 'target_sec': 0}, {'source_sec': 2, 'target_sec': 2}])
        self.plan = build_plan(draft, self.sources[0], self.sources[1], plan_id='d' * 32,
            revision=0, separator_ready=False, native_ready=False)

    async def close(self) -> None:
        await song_sources.shutdown()
        self.connection.close()

    def job(self, mode='full') -> BeatSongMixJob:
        return BeatSongMixJob(id=self.job_id, plan=self.plan,
            request=BeatSongMixStartRequest(plan_id=self.plan.id, expected_revision=0, plan_sha256=self.plan.sha256,
                submission_key='mix', mode=mode, preview_start_sec=.5 if mode == 'preview' else None,
                preview_end_sec=1.5 if mode == 'preview' else None), status='running',
            progress={'phase': 'warping'}, created_at=self.plan.created_at)

    async def test_real_layer_uses_retained_sources_and_aligned_processed_stems(self) -> None:
        for source in self.sources:
            audio_versions.resolve_source(source.track_id, source.version_id).unlink()
        separator = AsyncMock(side_effect=AssertionError('layer started separation'))
        output = beat_song_render.job_dir(self.job_id, create=True) / 'output'
        manifest = await beat_song_render.render(self.job(), self.sources, output, run_command,
            native=None, separate=separator)
        mix = next(item for item in manifest.artifacts if item.kind == 'mix')
        samples, rate = sf.read(output / mix.filename, dtype='float32', always_2d=True)
        np.testing.assert_allclose(samples, self.samples[0] + self.samples[1], atol=2e-8)
        processed = [item for item in manifest.artifacts if item.kind == 'processed']
        self.assertEqual((rate, samples.shape, len(processed)), (48000, (96000, 2), 2))
        self.assertTrue(all(sf.info(output / item.filename).frames == 96000 for item in processed))
        self.assertNotIn(str(self.root), manifest.model_dump_json())
        separator.assert_not_awaited()
        self.assertEqual(beat_song_render.validate_manifest(self.job_id), manifest)

    async def test_preview_is_crop_of_same_full_master_and_dry_inputs_are_retained(self) -> None:
        output = beat_song_render.job_dir(self.job_id, create=True) / 'output'
        manifest = await beat_song_render.render(self.job('preview'), self.sources, output, run_command,
            native=None, separate=AsyncMock())
        mix = next(item for item in manifest.artifacts if item.kind == 'mix')
        samples, _ = sf.read(output / mix.filename, dtype='float32', always_2d=True)
        np.testing.assert_allclose(samples, (self.samples[0] + self.samples[1])[24000:72000], atol=2e-8)
        self.assertEqual(manifest.frames, 48000)
        self.assertEqual(len([item for item in manifest.artifacts if item.kind == 'dry']), 2)

    async def test_replace_drums_uses_fresh_stems_and_omits_only_the_actual_drum_lane(self) -> None:
        draft = self.plan.draft.model_copy(update={'mode': 'replace_drums'})
        self.plan = build_plan(draft, self.sources[0], self.sources[1], plan_id=self.plan.id,
            revision=0, separator_ready=True, native_ready=False)
        async def separate(source, output):
            output.mkdir()
            samples, rate = sf.read(source, dtype='float32', always_2d=True)
            result = {}
            for stem in ('vocals', 'drums', 'bass', 'other'):
                path = output / (stem + '.wav')
                sf.write(path, samples * .25, rate, subtype='FLOAT')
                result[stem] = path
            return result
        separator = AsyncMock(side_effect=separate)
        output = beat_song_render.job_dir(self.job_id, create=True) / 'output'
        manifest = await beat_song_render.render(self.job(), self.sources, output, run_command,
            native=None, separate=separator)
        mix = next(item for item in manifest.artifacts if item.kind == 'mix')
        samples, _ = sf.read(output / mix.filename, dtype='float32', always_2d=True)
        np.testing.assert_allclose(samples, self.samples[0] + .75 * self.samples[1], atol=3e-8)
        self.assertEqual(separator.await_count, 1)
        self.assertEqual(len(manifest.lanes), 5)
        drum = next(lane for lane in self.plan.lanes if lane.source == 'song' and lane.role == 'drum')
        item = next(item for item in manifest.artifacts if item.kind == 'processed' and item.lane_id == drum.id)
        self.assertEqual(float(np.max(np.abs(sf.read(output / item.filename)[0]))), 0)
        self.assertFalse(manifest.music_evidence.lineage_complete)

    async def test_piecewise_clicks_land_at_every_reviewed_anchor_without_claiming_native_quality(self) -> None:
        from app.music_dsp import ResolvedDspSource
        from app.music_dsp_contracts import DspEngineIdentity, WarpAnchor
        path = self.root / 'clicks.wav'
        samples = np.zeros((96001, 2), dtype=np.float32)
        samples[[0, 24000, 48000, 72000, 95999]] = .5
        sf.write(path, samples, 48000, subtype='FLOAT')
        source = ResolvedDspSource(path, self.sources[0].source, hashlib.sha256(path.read_bytes()).hexdigest(), 48000, 2, len(samples))
        anchors = [WarpAnchor(source_sec=index / 2, target_sec=target)
            for index, target in enumerate((0, .5, 1.01, 1.53, 2.06))]

        async def mapping_kernel(source, output, *, sample_rate_hz, channels, input_frames, output_frames, anchors, pitch_shift_semitones, run):
            # Test double checks the actual adapter's frame map contract. This
            # is not evidence about Rubber Band quality or latency.
            data, rate = sf.read(source, dtype='float32', always_2d=True)
            coordinates = np.interp(np.arange(output_frames), [item[1] for item in anchors], [item[0] for item in anchors])
            rendered = np.column_stack([np.interp(coordinates, np.arange(len(data)), data[:, channel]) for channel in range(channels)])
            sf.write(output, rendered, rate, subtype='FLOAT')
            return DspEngineIdentity(name='test-frame-map', revision='test', configuration_sha256='a' * 64)

        work = self.root / 'mapped'
        work.mkdir()
        output = await beat_song_render.fit_lane(source, anchors, 2.06, 2, 0, 0, work, run_command, mapping_kernel)
        actual, _ = sf.read(output, dtype='float32', always_2d=True)
        for target in (.5, 1.01, 1.53):
            frame = round(target * 48000)
            self.assertEqual(int(np.argmax(actual[frame - 3:frame + 4, 0])) + frame - 3, frame)
        self.assertEqual(len(actual), round(2.06 * 48000))
        self.assertGreater(int(np.argmax(actual[73000:74000, 0])) + 73000, 72000)

    def test_long_mapped_windows_keep_context_bar_and_native_source_budget(self) -> None:
        from app.music_dsp_contracts import WarpAnchor
        anchors = [WarpAnchor(source_sec=0, target_sec=0), WarpAnchor(source_sec=40, target_sec=42),
            WarpAnchor(source_sec=80, target_sec=80)]
        windows = beat_song_render.warp_windows(anchors, 2)
        self.assertGreater(len(windows), 1)
        for index, window in enumerate(windows):
            self.assertLessEqual(window.anchors[-1].source_sec - window.anchors[0].source_sec, 30 + 1e-9)
            if index:
                self.assertAlmostEqual(window.core_start - window.anchors[0].target_sec, 2)
            if index + 1 < len(windows):
                self.assertAlmostEqual(window.anchors[-1].target_sec - window.core_end, 2)

    async def test_real_beat_render_repeats_use_private_sampler_assets_without_extra_catalog_rows(self) -> None:
        import asyncio
        from app import beat_jobs, beat_store, beat_render
        from app.beat_contracts import BeatCreateRequest, BeatJobRequest, BeatStep
        from app.music_conditioning_contracts import BeatRenderConditioningSource
        for name, value in (('_tasks', {}), ('_processes', {}), ('_unverified', {}), ('_closing', False),
                ('_lock', asyncio.Lock()), ('_semaphore', asyncio.Semaphore(1))):
            self.enterContext(patch.object(beat_jobs, name, value))
        self.addAsyncCleanup(beat_jobs.shutdown)
        document = beat_store.create_document(BeatCreateRequest(bpm=240))
        lanes = document.patterns[0].lanes
        lanes[3].steps[:] = [BeatStep(index=15, velocity=100)]
        document = beat_store.update(document.id, 0, document)
        started = await beat_jobs.start(BeatJobRequest(beat_id=document.id, expected_revision=document.revision,
            submission_key='7' * 32, mode='full'))
        task = beat_jobs._tasks.get(started.id)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), 20)
        done = beat_jobs.get(started.id)
        self.assertEqual(done.status, 'done')
        mix = next(item for item in done.manifest.artifacts if item.kind == 'mix')
        source = BeatRenderConditioningSource(kind='beat_render', beat_id=document.id,
            beat_revision=document.revision, render_id=done.id, sha256=mix.sha256)
        snapshot = await song_sources.retain_source(source, song_sources.owned_root(self.job_id),
            source_id='3' * 32, expected_sha256=mix.sha256)
        draft = self.plan.draft.model_copy(update={'beat': self.plan.draft.beat.model_copy(update={
            'source': source, 'sha256': mix.sha256, 'excerpt_end_sec': 1}), 'beat_repeats': 2})
        self.plan = build_plan(draft, snapshot, self.sources[1], plan_id=self.plan.id, revision=0,
            separator_ready=False, native_ready=False)
        await beat_song_render.retain_kit(self.plan, song_sources.owned_root(self.job_id))
        kit = beat_render.load_kit(document.kit_id, document.kit_manifest_sha256)
        for instrument in kit.instruments:
            for region in instrument.regions:
                beat_render.resolve_sample(kit, region.asset_id).unlink(missing_ok=True)
        beat_song_render.job_dir(self.job_id, create=True)
        before = self.connection.execute('SELECT count(*) FROM tracks').fetchone()[0]
        with patch.object(beat_render, 'load_kit', side_effect=AssertionError('queued sampler reread global kit')):
            prepared = await beat_song_render.prepare(self.job(), [snapshot, self.sources[1]], run_command, AsyncMock())
        self.assertEqual(len(prepared), 9)
        self.assertTrue(all(sf.info(path).frames == 96000 for path in prepared.values()))
        self.assertEqual(self.connection.execute('SELECT count(*) FROM tracks').fetchone()[0], before)
        self.assertIn('authored_repeats_recompiled', self.plan.warnings)
