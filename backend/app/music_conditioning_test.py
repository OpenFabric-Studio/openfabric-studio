"""Private conditioning copies and exact source identity without model imports."""
from __future__ import annotations

import hashlib
import asyncio
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

import numpy as np
import soundfile as sf

from app import music_conditioning as conditioning
from app.local_music_contracts import LocalMusicError, LocalMusicRequest
from app.music_conditioning_contracts import StableAudioConditioning, StableInpaintConditioning
from app.music_analysis_contracts import VersionMusicSource
from app.module_catalog import ModuleEnvironment
from scripts.music_analysis_worker import run_command


class MusicConditioningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.workspace = self.root / 'job'
        self.workspace.mkdir()
        self.original = self.root / 'original.wav'
        samples = np.full((240000, 2), .15, dtype=np.float32)
        samples[:, 1] *= -1
        sf.write(self.original, samples, 48000, subtype='FLOAT')
        self.source = VersionMusicSource(track_id=1, version_id='a' * 32)
        self.checksum = hashlib.sha256(self.original.read_bytes()).hexdigest()
        self.enterContext(patch.object(conditioning, '_resolve', return_value=conditioning.ConditioningSourceProjection(
            self.original, 1, None)))
        self.enterContext(patch.object(conditioning, 'available_bytes', return_value=10**12))
        self.enterContext(patch.object(conditioning, '_sources', {}))
        self.enterContext(patch.object(conditioning, '_deleting', set()))
        self.enterContext(patch.object(conditioning, '_replacing', set()))
        self.proof = conditioning.ConditioningRuntimeProof('b' * 64, 'c' * 64)

    def request(self, *, mode: str = 'audio_to_audio', **changes: object) -> LocalMusicRequest:
        options = {'mode': mode, 'source': self.source.model_dump(), 'source_sha256': self.checksum,
            **({'noise_level': .6} if mode == 'audio_to_audio' else {'start_sec': 1.0, 'end_sec': 2.0})}
        return LocalMusicRequest.model_validate({'engine': 'stable_audio3', 'prompt': 'Instrumental variation',
            'duration_seconds': 5, 'conditioning': {**options, **changes}})

    async def test_snapshot_is_independent_and_canonical_input_is_hash_bound(self) -> None:
        before = self.original.read_bytes()
        snapshot = await conditioning.prepare(self.request(), self.workspace, run=run_command, proof=self.proof)
        retained = self.workspace / snapshot.original_name
        prepared = self.workspace / 'conditioning.wav'
        self.assertEqual(retained.read_bytes(), before)
        self.assertNotEqual(retained.stat().st_ino, self.original.stat().st_ino)
        self.assertEqual(hashlib.sha256(prepared.read_bytes()).hexdigest(), snapshot.recipe.prepared_sha256)
        info = sf.info(prepared)
        self.assertEqual((info.samplerate, info.channels, info.frames, info.subtype), (44100, 2, 220500, 'PCM_16'))
        self.assertEqual(snapshot.recipe.output_semantics, 'generated_full_mix')
        self.assertEqual(self.original.read_bytes(), before)

    async def test_queued_input_prepares_from_its_copy_after_library_removal(self) -> None:
        from app.queued_music_source_contracts import QueuedMusicSource
        from app.beat_song_mix_contracts import SongSourceSnapshot
        from app.export_provenance_contracts import MusicEvidence
        saved = self.root / 'queue-retained.wav'
        self.original.rename(saved)
        retained = QueuedMusicSource(owner_id='e' * 32, snapshot=SongSourceSnapshot(source=self.source,
            sha256=self.checksum, source_bytes=saved.stat().st_size, sample_rate_hz=48000, channels=2,
            frames=240000, duration_sec=5, track_id=1, version_id=self.source.version_id,
            music_evidence=MusicEvidence(restriction='noncommercial'), content_origin='unknown',
            source_id='f' * 32, raw_filename='raw.wav'))
        with patch('app.queued_music_sources.resolve', new=AsyncMock(return_value=saved)) as resolve, \
                patch.object(conditioning, '_resolve', side_effect=AssertionError('deleted source must not be resolved')):
            result = await conditioning.prepare(self.request(), self.workspace, run=run_command,
                proof=self.proof, retained_input=retained)
        self.assertGreaterEqual(resolve.await_count, 2)
        self.assertEqual((self.workspace / result.original_name).read_bytes(), saved.read_bytes())
        self.assertEqual(result.source_music_evidence.restriction, 'noncommercial')

    async def test_changed_source_and_existing_snapshot_fail_without_overwrite(self) -> None:
        with self.assertRaises(LocalMusicError) as error:
            await conditioning.prepare(self.request(source_sha256='f' * 64), self.workspace,
                run=run_command, proof=self.proof)
        self.assertEqual(error.exception.code, 'conditioning_source_changed')
        retained = self.workspace / 'conditioning-original.wav'
        retained.write_bytes(b'previous retained copy')
        with self.assertRaises(LocalMusicError):
            await conditioning.prepare(self.request(), self.workspace, run=run_command, proof=self.proof)
        self.assertEqual(retained.read_bytes(), b'previous retained copy')

    async def test_inpaint_rejects_unrepresentable_source_duration_or_latent_region(self) -> None:
        for options, code in (({'start_sec': 1.0, 'end_sec': 1.001, 'crossfade_sec': 0.0}, 'conditioning_invalid_region'),):
            with self.subTest(options=options), self.assertRaises(LocalMusicError) as error:
                await conditioning.prepare(self.request(mode='inpaint', **options), self.workspace,
                    run=run_command, proof=self.proof)
            self.assertEqual(error.exception.code, code)
        sf.write(self.original, np.zeros((240000, 2), dtype=np.float64), 48000, subtype='DOUBLE')
        self.checksum = hashlib.sha256(self.original.read_bytes()).hexdigest()
        with self.assertRaises(LocalMusicError) as error:
            await conditioning.prepare(self.request(mode='inpaint'), self.workspace, run=run_command, proof=self.proof)
        self.assertEqual(error.exception.code, 'conditioning_inpaint_format_unsupported')

    async def test_inpaint_recipe_binds_original_frame_mask_not_native_latent_cells(self) -> None:
        snapshot = await conditioning.prepare(self.request(mode='inpaint'), self.workspace, run=run_command, proof=self.proof)
        self.assertEqual((snapshot.recipe.region_start_frame, snapshot.recipe.region_end_frame,
            snapshot.recipe.crossfade_frames), (48000, 96000, 960))
        self.assertEqual(snapshot.recipe.output_semantics, 'original_pcm_outside_region')

    async def test_original_removal_guard_drains_only_original_readers_and_blocks_new_reads(self) -> None:
        await conditioning.register('d' * 32, self.source)
        async def cancel(identifier: str) -> None:
            conditioning.release(identifier)
        with patch('app.local_music.cancel', side_effect=cancel) as stop:
            async with conditioning.protect_source_removal(1):
                stop.assert_awaited_once_with('d' * 32)
                with self.assertRaises(LocalMusicError):
                    await conditioning.register('e' * 32, self.source)
        await conditioning.register('e' * 32, self.source)
        conditioning.release('e' * 32)

    async def test_pcm16_preparation_measures_resampling_overshoot_before_quantization(self) -> None:
        phase = np.arange(240000, dtype=np.float64) / 48000
        square = np.asarray(.99 * np.sign(np.sin(2 * np.pi * 440 * phase)), dtype=np.float32)
        sf.write(self.original, np.column_stack((square, -square)), 48000, subtype='FLOAT')
        self.checksum = hashlib.sha256(self.original.read_bytes()).hexdigest()
        snapshot = await conditioning.prepare(self.request(), self.workspace, run=run_command, proof=self.proof)
        prepared, _ = sf.read(self.workspace / 'conditioning.wav', dtype='float32', always_2d=True)
        self.assertLess(snapshot.recipe.prepared_gain_db, 0)
        self.assertLess(float(np.max(np.abs(prepared))), .991)

    async def test_source_inspection_returns_current_version_hash_without_models_or_writes(self) -> None:
        before = self.original.read_bytes()
        with patch.object(conditioning, 'verify_runtime', new=AsyncMock()) as runtime:
            result = await conditioning.inspect_source(self.source)
        self.assertEqual(result.source, self.source)
        self.assertEqual((result.source_sha256, result.source_rate_hz, result.source_channels,
            result.source_frames, result.duration_sec), (self.checksum, 48000, 2, 240000, 5.0))
        self.assertEqual(self.original.read_bytes(), before)
        self.assertEqual(list(self.workspace.iterdir()), [])
        runtime.assert_not_awaited()

    async def test_source_inspection_rejects_changed_digest_and_header_hash_races(self) -> None:
        from app.music_analysis_contracts import StemMusicSource
        stem = StemMusicSource(track_id=1, stem='vocals', sha256='f' * 64)
        with self.assertRaises(LocalMusicError) as changed:
            await conditioning.inspect_source(stem)
        self.assertEqual(changed.exception.code, 'conditioning_source_changed')
        real_hash = conditioning.hash_file
        async def mutate(path: Path, *, max_bytes: int | None = None) -> str:
            sf.write(path, np.zeros((240000, 2), dtype=np.float32), 48000, subtype='FLOAT')
            return await real_hash(path, max_bytes=max_bytes)
        with patch.object(conditioning, 'hash_file', side_effect=mutate), self.assertRaises(LocalMusicError) as race:
            await conditioning.inspect_source(self.source)
        self.assertEqual(race.exception.code, 'conditioning_source_changed')

    async def test_read_only_inspection_accepts_long_source_while_generation_remains_bounded(self) -> None:
        sf.write(self.original, np.zeros((8000 * 400, 1), dtype=np.float32), 8000, subtype='PCM_16')
        inspected = await conditioning.inspect_source(self.source)
        self.assertEqual(inspected.duration_sec, 400)
        with self.assertRaises(LocalMusicError) as bounded:
            await conditioning.preview(self.request(source_sha256=inspected.source_sha256))
        self.assertEqual(bounded.exception.code, 'conditioning_source_invalid')

    async def test_preview_maps_a_changed_hash_read_to_a_stable_source_error(self) -> None:
        from app.video_projects import VideoProjectError
        with patch.object(conditioning, 'hash_file', new=AsyncMock(side_effect=VideoProjectError('source_changed'))):
            with self.assertRaises(LocalMusicError) as changed:
                await conditioning.preview(self.request())
        self.assertEqual(changed.exception.code, 'conditioning_source_changed')

    async def test_cancelled_source_inspection_drains_header_reader_before_returning(self) -> None:
        entered, release = threading.Event(), threading.Event()
        original = conditioning._facts
        def blocked(path: Path, *, max_seconds: int = 380) -> conditioning._Facts:
            entered.set(); release.wait(5)
            return original(path, max_seconds=max_seconds)
        with patch.object(conditioning, '_facts', side_effect=blocked):
            task = asyncio.create_task(conditioning.inspect_source(self.source))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_runtime_hash_race_returns_stable_runtime_changed_code(self) -> None:
        from app.video_projects import VideoProjectError
        environment = ModuleEnvironment.for_root(self.root / 'managed', platform='darwin', architecture='arm64')
        script = environment.paths['stable_audio3'] / 'optimized/mlx/scripts/sa3_mlx.py'
        script.parent.mkdir(parents=True); script.write_text('pass\n')
        with patch.object(conditioning, 'configured_environment', return_value=environment), patch.object(conditioning, 'environment_verified', return_value=True), patch.object(conditioning, 'hash_file', new=AsyncMock(side_effect=VideoProjectError('source_changed'))):
            with self.assertRaises(LocalMusicError) as changed:
                await conditioning.verify_runtime('small-music')
        self.assertEqual(changed.exception.code, 'conditioning_runtime_changed')
