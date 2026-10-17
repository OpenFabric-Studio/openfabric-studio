"""Local music owns worker sessions and retains normal library tracks."""
from __future__ import annotations

import asyncio
import errno
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import wave
import numpy as np
import soundfile as sf
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
import httpx
from pydantic import ValidationError

from app import db, local_music
from app import module_catalog
from app.local_music_contracts import LocalMusicEngineStatus, LocalMusicEngines, LocalMusicError, LocalMusicRequest
from app.video_process import WorkerIdentity
from app.contracts import TracksResponse
from app.api.routes_tracks import router as tracks_router


def audio_bytes() -> bytes:
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b'\0\0' * 2400)
    return output.getvalue()


class LocalMusicRequestTests(unittest.TestCase):
    def test_saved_requests_remain_manual_and_auto_is_rejected(self) -> None:
        request = LocalMusicRequest(engine='stable_audio3', prompt='Jazz', duration_seconds=95)
        self.assertEqual(request.model_dump().get('settings_mode'), 'manual')
        for mode in ('auto', 'unknown'):
            with self.subTest(mode=mode), self.assertRaises(ValidationError):
                LocalMusicRequest.model_validate({'engine': 'stable_audio3', 'prompt': 'Jazz',
                    'duration_seconds': 95, 'settings_mode': mode})

    def test_engine_duration_limits_remain_distinct_above_four_minutes(self) -> None:
        LocalMusicRequest(engine='stable_audio3', prompt='Jazz', variant='medium', duration_seconds=380)
        for update in ({'engine': 'stable_audio3', 'duration_seconds': 121},
                       {'engine': 'stable_audio3', 'variant': 'medium', 'duration_seconds': 381}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                LocalMusicRequest.model_validate({'prompt': 'Jazz', **update})

    def test_validates_model_limits_and_lyrics_before_admission(self) -> None:
        valid = {'engine': 'stable_audio3', 'prompt': 'Jazz', 'duration_seconds': 95}
        for update in ({'prompt': '  '}, {'engine': 'unknown'}, {'duration_seconds': 601}, {'seed': -1}, {'variant': 'unknown'},
                       {'seed': True}, {'adapter': {'id': 'a' * 32, 'strength': 3}}, {'engine': 'stable_audio3', 'lyrics': 'Sing this'},
                       {'engine': 'stable_audio3', 'duration_seconds': 121},
                       {'output_path': '/tmp/owned-by-user.wav'}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                LocalMusicRequest.model_validate({**valid, **update})
        request = LocalMusicRequest(engine='stable_audio3', prompt='Jazz', duration_seconds=95)
        self.assertEqual(request.engine, 'stable_audio3')
        LocalMusicRequest(engine='stable_audio3', prompt='Jazz', variant='medium', duration_seconds=380)


class LocalMusicJobsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        statuses = LocalMusicEngines(engines=[LocalMusicEngineStatus(id=engine, installed=True,
            weights_ready=True, small_weights_ready=True, medium_weights_ready=True,
            supported=True, runtime='test', detail='') for engine in ('stable_audio3',)])
        self.patches = [patch.object(db, '_db', None), patch.object(db, 'DATA_DIR', self.root),
            patch.object(db, 'DB_PATH', self.root / 'catalog.db'), patch.object(db, 'FILES_DIR', self.root / 'files'),
            patch.object(local_music, '_closing', False), patch.object(local_music, '_tasks', {}),
            patch.object(local_music, '_unverified', set()), patch.object(local_music, 'engine_status', return_value=statuses),
            patch('app.work_busy.other_work_busy', new=AsyncMock(return_value=False)),
            patch('app.video_jobs.work_busy', return_value=False), patch('app.resource_admission.native_work_inflight', return_value=False)]
        for item in self.patches:
            item.start()
        self.connection = db.get_db()
        self.worker = self.root / 'worker.py'
        self.worker.write_text('import sys,wave\nwith wave.open(sys.argv[1],"wb") as audio:\n audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(24000);audio.writeframes(b"\\0\\0"*2400)\n')

    async def asyncTearDown(self) -> None:
        await local_music.shutdown()
        self.connection.close()
        for item in reversed(self.patches):
            item.stop()

    async def test_float_inpaint_output_preserves_overshoot_and_rejects_nonfinite(self) -> None:
        path = self.root / 'inpaint.wav'
        samples = np.full((8000, 2), 1.5, dtype=np.float32)
        sf.write(path, samples, 8000, subtype='FLOAT')
        self.assertEqual(local_music._validate_audio(path), 1000.0)
        samples[100, 0] = float('nan')
        sf.write(path, samples, 8000, subtype='FLOAT')
        with self.assertRaises(LocalMusicError):
            local_music._validate_audio(path)

    async def test_conditioned_owner_retains_real_version_and_splices_before_publication(self) -> None:
        from app import audio_versions, music_conditioning
        from app.music_conditioning import ConditioningRuntimeProof
        import hashlib
        original = db.FILES_DIR / 'upload' / 'original.wav'
        original.parent.mkdir(parents=True)
        samples = np.full((240000, 2), .15, dtype=np.float32)
        samples[100, 0] = 1.5
        sf.write(original, samples, 48000, subtype='FLOAT')
        original_bytes = original.read_bytes()
        track = db.insert_track(model='upload', title='Original', lyrics='', seed=None, duration_ms=5000,
            wall_ms=None, params={}, audio_path=original, abc_path=None)
        version = audio_versions.retain_original(track, original)
        source = {'kind': 'version', 'track_id': track, 'version_id': version.id}
        self.worker.write_text('import argparse,shutil\np=argparse.ArgumentParser()\np.add_argument("--init-audio");p.add_argument("--init-noise-level");p.add_argument("--inpaint-range");p.add_argument("--seconds");p.add_argument("--out")\na=p.parse_args()\nshutil.copyfile(a.init_audio,a.out)\n')
        proof = ConditioningRuntimeProof(hashlib.sha256(self.worker.read_bytes()).hexdigest(), 'c' * 64)

        def command(request: LocalMusicRequest, output: Path, *, conditioning=None):
            self.assertIsNotNone(conditioning)
            recipe = conditioning.recipe
            prepared = output.parent / 'conditioning.wav'
            raw = output.parent / 'raw.wav'
            options = request.conditioning
            argv = [str(self.worker), '--init-audio', str(prepared), '--seconds', '5', '--out', str(raw)]
            if options.mode == 'audio_to_audio':
                argv.extend(['--init-noise-level', str(options.noise_level)])
            else:
                argv.extend(['--inpaint-range', f'{options.start_sec:.17g},{options.end_sec:.17g}'])
            descriptor = {'mode': recipe.mode, 'source_wav': str(prepared), 'source_sha256': recipe.prepared_sha256,
                'source_frames': recipe.prepared_frames, 'script_sha256': recipe.source_script_sha256,
                'noise_level': recipe.noise_level, 'start_sec': options.start_sec if options.mode == 'inpaint' else None,
                'end_sec': options.end_sec if options.mode == 'inpaint' else None}
            specification = output.parent / 'request.json'
            specification.write_text(json.dumps({'argv': argv, 'cwd': str(self.root), 'raw_output': str(raw),
                'output': str(output), 'ffmpeg': module_catalog.shutil.which('ffmpeg'), 'conditioning': descriptor}))
            worker = Path(__file__).resolve().parents[1] / 'scripts/local_music_worker.py'
            return [sys.executable, str(worker), '--request', str(specification)], self.root, dict(os.environ)

        for mode in ('audio_to_audio', 'inpaint'):
            options = {'mode': mode, 'source': source, 'source_sha256': hashlib.sha256(original_bytes).hexdigest(),
                **({'noise_level': .6} if mode == 'audio_to_audio' else {'start_sec': 1.0, 'end_sec': 2.0})}
            request = LocalMusicRequest.model_validate({'engine': 'stable_audio3', 'prompt': 'Instrumental',
                'duration_seconds': 5, 'conditioning': options})
            with patch.object(music_conditioning, 'verify_runtime', new=AsyncMock(return_value=proof)), \
                    patch.object(local_music, 'prepare_worker', side_effect=command):
                job = await local_music.submit(request)
                await local_music._tasks[job.id]
            final = local_music.get(job.id)
            self.assertEqual((final.status, final.error_code), ('done', None))
            self.assertIsNotNone(final.conditioning_recipe)
            self.assertIsNotNone(final.track)
            self.assertTrue(local_music._artifact(job.id, 'conditioning-original.wav').is_file())
            self.assertIn('_music_conditioning', final.track.params)
            output, rate = sf.read(local_music._output(final), dtype='float32', always_2d=True)
            if mode == 'inpaint':
                self.assertEqual((len(output), rate), (240000, 48000))
                np.testing.assert_array_equal(output[:48000], samples[:48000])
                np.testing.assert_array_equal(output[96000:], samples[96000:])
            else:
                self.assertEqual((len(output), rate), (220500, 44100))
            self.assertEqual(original.read_bytes(), original_bytes)

    async def test_conditioned_recovery_never_publishes_unattested_native_output(self) -> None:
        request = LocalMusicRequest.model_validate({'engine': 'stable_audio3', 'prompt': 'Piano',
            'duration_seconds': 5, 'conditioning': {'mode': 'inpaint', 'source': {'kind': 'version',
                'track_id': 1, 'version_id': 'b' * 32}, 'source_sha256': 'c' * 64, 'start_sec': 1.0, 'end_sec': 2.0}})
        job = local_music._StoredJob(id='a' * 32, status='running', created_at='2026-10-17', request=request)
        self.connection.execute('INSERT INTO local_music_jobs(id,payload_json,created_at) VALUES(?,?,?)',
            (job.id, job.model_dump_json(), job.created_at)); self.connection.commit()
        pending = local_music._artifact(job.id, 'output.wav', create=True)
        pending.write_bytes(audio_bytes())
        before = pending.read_bytes()
        await local_music.recover()
        self.assertEqual(local_music.get(job.id).error_code, 'conditioning_snapshot_invalid')
        self.assertEqual(pending.read_bytes(), before)
        self.assertEqual(db.list_tracks(), [])

    async def test_original_guard_cancels_preparation_before_gpu_and_retains_source(self) -> None:
        from app import music_conditioning
        from app.music_conditioning import ConditioningRuntimeProof, ConditioningSourceProjection
        from app.music_analysis_contracts import VersionMusicSource
        original = self.root / 'source.wav'; original.write_bytes(audio_bytes())
        request = LocalMusicRequest.model_validate({'engine': 'stable_audio3', 'prompt': 'Piano',
            'duration_seconds': 5, 'conditioning': {'mode': 'audio_to_audio', 'source': {'kind': 'version',
                'track_id': 1, 'version_id': 'b' * 32}, 'source_sha256': 'c' * 64, 'noise_level': .6}})
        started, drained = asyncio.Event(), asyncio.Event()
        async def prepare(*args, **kwargs):
            started.set()
            try:
                await asyncio.sleep(60)
            finally:
                drained.set()
        with patch.object(music_conditioning, '_resolve', return_value=ConditioningSourceProjection(original, 1, None)), \
                patch.object(music_conditioning, 'verify_runtime', new=AsyncMock(return_value=ConditioningRuntimeProof('d' * 64, 'e' * 64))), \
                patch.object(music_conditioning, 'prepare', side_effect=prepare):
            job = await local_music.submit(request)
            await asyncio.wait_for(started.wait(), 2)
            from app.stems import gpu_lock
            self.assertFalse(gpu_lock.locked())
            async with music_conditioning.protect_source_removal(1):
                self.assertTrue(drained.is_set())
                self.assertEqual(local_music.get(job.id).status, 'cancelled')
        self.assertEqual(original.read_bytes(), audio_bytes())
        self.temporary.cleanup()

    def body(self, engine: str = 'stable_audio3') -> LocalMusicRequest:
        return LocalMusicRequest.model_validate({'engine': engine, 'prompt': 'Warm jazz', 'title': 'Test song', 'duration_seconds': 95})

    def command(self, request: LocalMusicRequest, output: Path) -> tuple[list[str], Path, dict[str, str]]:
        return [sys.executable, str(self.worker), str(output)], self.root, dict(os.environ)

    async def submit_and_finish(self, engine: str = 'stable_audio3') -> str:
        job = await local_music.submit(self.body(engine))
        await local_music._tasks[job.id]
        return job.id

    async def test_each_engine_publishes_once_as_a_normal_track(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            for engine in ('stable_audio3',):
                identifier = await self.submit_and_finish(engine)
                final = local_music.get(identifier)
                self.assertEqual(final.status, 'done')
                self.assertIsNotNone(final.track)
                if final.track is None:
                    self.fail('Expected saved track')
                self.assertEqual(final.track.model, engine)
                self.assertEqual(final.track.params['local_music_job_id'], identifier)
                self.assertNotIn('settings_mode', final.track.params)
                self.assertIn('_music_evidence', final.track.params)
                self.assertIn('publisher_declared', str(final.track.params['_music_evidence']))
            await local_music.recover()
            await local_music.recover()
        self.assertEqual(len(db.list_tracks()), 1)
        self.assertFalse(local_music.work_busy())

    async def test_completed_audio_projects_original_receipt_without_hashing_current_voice_version(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            identifier = await self.submit_and_finish()
        original = local_music._output(local_music.get(identifier))
        alternate = self.root / 'files/voice.wav'; alternate.write_bytes(audio_bytes())
        self.connection.execute('UPDATE tracks SET audio_path=?', (str(alternate),)); self.connection.commit()
        with patch.object(local_music, '_digest', side_effect=AssertionError('metadata projection must not hash audio')):
            projected = local_music.completed_audio(identifier)
        self.assertEqual(projected.path, original.resolve())
        self.assertEqual(projected.sha256, local_music._digest(original))
        self.assertEqual(projected.track_id, local_music.get(identifier).track.id)
        self.connection.execute('UPDATE local_music_jobs SET track_id=NULL WHERE id=?', (identifier,)); self.connection.commit()
        with self.assertRaises(LocalMusicError):
            local_music.completed_audio(identifier)
        self.assertTrue(original.exists()); self.assertTrue(alternate.exists())

    async def test_insufficient_pcm_space_rejects_before_creating_a_job(self) -> None:
        required = (95 + 10) * 192000 * 2 * 4 * 2 + 64 * 1024**2
        usage = module_catalog.shutil.disk_usage(self.root)
        with patch.object(module_catalog.shutil, 'disk_usage', return_value=usage._replace(free=required - 1)), \
                patch.object(local_music, 'prepare_worker', side_effect=self.command) as prepare, self.assertRaises(LocalMusicError) as caught:
            await local_music.submit(self.body())
        self.assertEqual(caught.exception.code, 'insufficient_disk')
        prepare.assert_not_called()
        self.assertEqual(local_music.list_jobs().jobs, [])

    async def test_space_is_rechecked_before_starting_the_worker(self) -> None:
        usage = module_catalog.shutil.disk_usage(self.root)
        with patch.object(local_music, 'prepare_worker', side_effect=self.command) as prepare:
            job = await local_music.submit(self.body())
            with patch.object(module_catalog.shutil, 'disk_usage', return_value=usage._replace(free=1)):
                await local_music._tasks[job.id]
        self.assertEqual(local_music.get(job.id).error_code, 'insufficient_disk')
        prepare.assert_not_called()
        self.assertEqual(db.list_tracks(), [])

    async def test_publication_refuses_an_existing_output_and_retains_complete_pending_audio(self) -> None:
        def command(request: LocalMusicRequest, output: Path) -> tuple[list[str], Path, dict[str, str]]:
            local_music._output(local_music.get(output.parent.name)).write_bytes(b'preserve existing output')
            return self.command(request, output)
        with patch.object(local_music, 'prepare_worker', side_effect=command):
            identifier = await self.submit_and_finish()
        job = local_music.get(identifier)
        self.assertEqual(job.status, 'failed')
        self.assertEqual(job.error_code, 'local_music_output_exists')
        self.assertEqual(local_music._output(job).read_bytes(), b'preserve existing output')
        self.assertEqual(local_music._pending(job).read_bytes(), audio_bytes())
        self.assertEqual(db.list_tracks(), [])

    async def test_racing_output_is_never_replaced_during_publication(self) -> None:
        original = os.link
        def race(source: str | os.PathLike[str], target: str | os.PathLike[str], *, follow_symlinks: bool = True) -> None:
            Path(target).write_bytes(b'concurrent user data')
            original(source, target, follow_symlinks=follow_symlinks)
        with patch.object(local_music, 'prepare_worker', side_effect=self.command), patch.object(local_music.os, 'link', side_effect=race):
            identifier = await self.submit_and_finish()
        job = local_music.get(identifier)
        self.assertEqual(job.error_code, 'local_music_output_exists')
        self.assertEqual(local_music._output(job).read_bytes(), b'concurrent user data')
        self.assertEqual(local_music._pending(job).read_bytes(), audio_bytes())
        self.assertEqual(db.list_tracks(), [])

    async def test_audio_changed_during_publication_is_not_registered(self) -> None:
        original = os.link
        def changed(source: str | os.PathLike[str], target: str | os.PathLike[str], *, follow_symlinks: bool = True) -> None:
            Path(source).write_bytes(audio_bytes() + b'changed after hashing')
            original(source, target, follow_symlinks=follow_symlinks)
        with patch.object(local_music, 'prepare_worker', side_effect=self.command), patch.object(local_music.os, 'link', side_effect=changed):
            identifier = await self.submit_and_finish()
        job = local_music.get(identifier)
        self.assertEqual(job.error_code, 'local_music_output_changed')
        self.assertTrue(local_music._pending(job).is_file())
        self.assertEqual(db.list_tracks(), [])

    async def test_disk_full_during_publication_retains_complete_audio_for_recovery(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command), \
                patch.object(local_music.os, 'link', side_effect=OSError(errno.ENOSPC, 'disk full')):
            identifier = await self.submit_and_finish()
        job = local_music.get(identifier)
        self.assertEqual(job.error_code, 'insufficient_disk')
        self.assertEqual(local_music._pending(job).read_bytes(), audio_bytes())
        self.assertEqual(db.list_tracks(), [])
        await local_music.recover()
        self.assertEqual(local_music.get(identifier).status, 'done')

    async def test_catalog_space_is_rechecked_before_track_registration(self) -> None:
        linked = False
        original = os.link
        def publish(source: str | os.PathLike[str], target: str | os.PathLike[str], *, follow_symlinks: bool = True) -> None:
            nonlocal linked
            original(source, target, follow_symlinks=follow_symlinks)
            linked = True
        def available(path: Path) -> int:
            return 1 if linked and path == self.root else 100 * 1024**3
        with patch.object(local_music, 'prepare_worker', side_effect=self.command), \
                patch.object(local_music, 'available_bytes', side_effect=available), \
                patch.object(local_music.os, 'link', side_effect=publish):
            identifier = await self.submit_and_finish()
        job = local_music.get(identifier)
        self.assertEqual(job.error_code, 'insufficient_disk')
        self.assertEqual(local_music._pending(job).read_bytes(), audio_bytes())
        self.assertEqual(db.list_tracks(), [])
        await local_music.recover()
        self.assertEqual(local_music.get(identifier).status, 'done')

    async def test_recovery_publication_refuses_a_racing_output(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            job = await local_music.submit(self.body())
            await local_music.cancel(job.id)
        pending = local_music._artifact(job.id, 'output.wav', create=True)
        pending.write_bytes(audio_bytes())
        job.status = 'failed'
        local_music._store(job)
        self.connection.execute('UPDATE local_music_jobs SET output_sha256=? WHERE id=?', (local_music._digest(pending), job.id))
        self.connection.commit()
        original = os.link
        def race(source: str | os.PathLike[str], target: str | os.PathLike[str], *, follow_symlinks: bool = True) -> None:
            Path(target).write_bytes(b'recovery race winner')
            original(source, target, follow_symlinks=follow_symlinks)
        with patch.object(local_music.os, 'link', side_effect=race):
            await local_music.recover()
        job = local_music.get(job.id)
        self.assertEqual(job.error_code, 'local_music_output_exists')
        self.assertEqual(local_music._output(job).read_bytes(), b'recovery race winner')
        self.assertEqual(pending.read_bytes(), audio_bytes())
        self.assertEqual(db.list_tracks(), [])


    async def test_legacy_persisted_request_without_mode_remains_manual(self) -> None:
        with patch('secrets.randbelow') as random_seed, patch.object(local_music, 'prepare_worker', side_effect=self.command):
            job = await local_music.submit(self.body())
            await local_music.cancel(job.id)
            payload = json.loads(job.model_dump_json())
            payload['request'].pop('settings_mode', None)
            self.connection.execute('UPDATE local_music_jobs SET payload_json=? WHERE id=?', (json.dumps(payload), job.id))
            self.connection.commit()
            loaded = local_music.get(job.id)
        random_seed.assert_not_called()
        self.assertEqual(loaded.request.settings_mode, 'manual')
        self.assertEqual(loaded.request.seed, 0)

    async def test_audio_publication_accepts_medium_output_and_remains_bounded(self) -> None:
        output = self.root / 'long.wav'
        for seconds in (380, 391):
            with wave.open(str(output), 'wb') as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(8000)
                audio.writeframes(b'\0\0' * (8000 * seconds))
            if seconds == 380:
                self.assertEqual(local_music._validate_audio(output), 380 * 1000)
            else:
                with self.assertRaises(LocalMusicError) as caught:
                    local_music._validate_audio(output)
                self.assertEqual(caught.exception.code, 'invalid_music_audio')

    async def test_missing_selected_stable_generation_weights_rejected_before_job_creation(self) -> None:
        status = LocalMusicEngineStatus.model_validate({'id': 'stable_audio3', 'installed': True,
            'weights_ready': True, 'small_weights_ready': True, 'medium_weights_ready': False,
            'supported': True, 'runtime': 'test', 'detail': ''})
        with patch.object(local_music, 'engine_status', return_value=LocalMusicEngines(engines=[status])):
            with self.assertRaises(LocalMusicError) as caught:
                await local_music.submit(LocalMusicRequest(engine='stable_audio3', prompt='Piano', variant='medium', duration_seconds=30))
        self.assertEqual(caught.exception.code, 'local_music_weights_missing')
        self.assertFalse(local_music._tasks)
        self.assertEqual(local_music.list_jobs().jobs, [])


    async def test_worker_failure_receipt_rejects_arbitrary_oversized_and_symlinked_codes(self) -> None:
        for content in ('{"code":"private internal detail"}', '{"code":"untrusted_worker_detail"}' + ' ' * 1024,
                        '{"code":"untrusted_worker_detail","detail":"private internal detail"}', 'not json'):
            with self.subTest(content=content[:80]):
                self.worker.write_text('import sys\nfrom pathlib import Path\n'
                    f'Path(sys.argv[1]).parent.joinpath("generation-error.json").write_text({content!r})\n'
                    'raise SystemExit(1)\n')
                with patch.object(local_music, 'prepare_worker', side_effect=self.command):
                    identifier = await self.submit_and_finish()
                self.assertEqual(local_music.get(identifier).error_code, 'local_music_generation_failed')
        outside = self.root / 'outside-error.json'
        outside.write_text('{"code":"untrusted_worker_detail"}')
        self.worker.write_text('raise SystemExit(1)\n')
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            job = await local_music.submit(self.body())
            local_music._artifact(job.id, 'generation-error.json', create=True).symlink_to(outside)
            await local_music._tasks[job.id]
        self.assertEqual(local_music.get(job.id).error_code, 'local_music_generation_failed')
        self.assertEqual(outside.read_text(), '{"code":"untrusted_worker_detail"}')

    async def test_selected_weights_removed_after_submission_fail_before_worker_launch(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=AssertionError('missing weights must not launch')) as prepare:
            job = await local_music.submit(self.body('stable_audio3'))
            status = LocalMusicEngineStatus(id='stable_audio3', installed=True, weights_ready=True,
                small_weights_ready=False, medium_weights_ready=True, supported=True, runtime='test', detail='')
            task = local_music._tasks[job.id]
            with patch.object(local_music, 'engine_status', return_value=LocalMusicEngines(engines=[status])):
                await task
        prepare.assert_not_called()
        self.assertEqual(local_music.get(job.id).status, 'failed')
        self.assertEqual(local_music.get(job.id).error_code, 'local_music_weights_missing')

    async def test_queue_owner_can_submit_and_drain_a_real_cpu_child_worker(self) -> None:
        from app import music_queue_ownership as ownership
        with patch.object(ownership, '_owner', None), patch('app.support_controls._idle_state', new=AsyncMock(return_value='idle')):
            owner = await ownership.reserve()
            with self.assertRaises(HTTPException) as caught:
                await local_music.submit(self.body())
            self.assertEqual(caught.exception.detail, 'music_queue_busy')
            with owner.context(), patch.object(local_music, 'prepare_worker', side_effect=self.command):
                job = await local_music.submit(self.body())
                await asyncio.wait_for(local_music._tasks[job.id], 5)
            self.assertEqual(local_music.get(job.id).status, 'done')
            self.assertFalse(local_music.work_busy())
            self.assertTrue(await owner.release())

    async def test_stable_adapter_provenance_survives_completed_job(self) -> None:
        from app import music_lora
        from app.music_lora_test import adapter_bytes
        import io
        adapter = music_lora.import_stream(io.BytesIO(adapter_bytes()), filename='style.safetensors',
            engine='stable_audio3', target='small-music', name='Style')
        request = self.body('stable_audio3')
        request = LocalMusicRequest.model_validate({**request.model_dump(), 'adapter': {'id': adapter.id, 'strength': 0.4}})
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            job = await local_music.submit(request)
            await local_music._tasks[job.id]
        final = local_music.get(job.id)
        self.assertIsNotNone(final.track)
        if final.track is None:
            self.fail('Expected track')
        self.assertEqual(final.track.params['adapter'], {'id': adapter.id, 'strength': 0.4})
        self.assertEqual(final.track.params['_music_adapters'], [{**adapter.model_dump(mode='json'), 'strength': 0.4}])

    async def test_queued_stable_adapter_manifest_must_match_accepted_snapshot(self) -> None:
        from app import music_lora
        from app.music_lora_test import adapter_bytes
        from app.music_lora_contracts import MusicAdapterSelection
        import io
        import json
        adapter = music_lora.import_stream(io.BytesIO(adapter_bytes()), filename='style.safetensors',
            engine='stable_audio3', target='small-music', name='Original style')
        request = LocalMusicRequest(engine='stable_audio3', prompt='Piano', duration_seconds=30,
            adapter=MusicAdapterSelection(id=adapter.id))
        with patch.object(local_music, 'prepare_worker', side_effect=self.command) as prepare:
            job = await local_music.submit(request)
            manifest = self.root / 'music-lora' / adapter.id / 'manifest.json'
            value = json.loads(manifest.read_text()); value['name'] = 'Replaced style'
            manifest.write_text(json.dumps(value))
            await local_music._tasks[job.id]
        self.assertEqual(local_music.get(job.id).error_code, 'music_lora_adapter_changed')
        prepare.assert_not_called()

    async def test_saved_local_models_can_be_filtered_and_played_via_track_routes(self) -> None:
        app = FastAPI()
        app.include_router(tracks_router)
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            await self.submit_and_finish()
        legacy = self.root / 'files' / 'retired.wav'
        legacy.write_bytes(audio_bytes())
        db.insert_track(model='heartmula', title='Old song', lyrics='', seed=0, duration_ms=100,
            wall_ms=None, params={}, audio_path=legacy, abc_path=None)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:9000') as client:
            for engine in ('heartmula', 'stable_audio3'):
                response = await client.get('/api/tracks', params={'model': engine})
                self.assertEqual(response.status_code, 200)
                result = TracksResponse.model_validate_json(response.content)
                self.assertEqual(len(result.data), 1)
                self.assertEqual(result.data[0].model, engine)
                audio = await client.get(result.data[0].audio_url)
                self.assertEqual(audio.status_code, 200)
                self.assertEqual(audio.content, audio_bytes())

    async def test_retired_job_history_and_files_are_preserved_without_hiding_stable_jobs(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            stable = await self.submit_and_finish()
        retired_id = 'd' * 32
        payload = json.loads(local_music.get(stable).model_dump_json())
        payload.update(id=retired_id, status='queued', track=None)
        payload['request'].update(engine='heartmula', settings_mode='auto', duration_seconds=600)
        raw = json.dumps(payload)
        self.connection.execute('INSERT INTO local_music_jobs(id,payload_json,created_at) VALUES(?,?,?)',
            (retired_id, raw, '2026-10-16'))
        self.connection.commit()
        pending = local_music._artifact(retired_id, 'output.wav', create=True)
        pending.write_bytes(audio_bytes())
        await local_music.recover()
        history = local_music.list_jobs()
        self.assertEqual([job.id for job in history.jobs], [stable])
        self.assertFalse(history.history_incomplete)
        self.assertFalse(local_music.work_busy())
        self.assertEqual(pending.read_bytes(), audio_bytes())
        self.assertEqual(self.connection.execute('SELECT payload_json FROM local_music_jobs WHERE id=?',
            (retired_id,)).fetchone()[0], raw)

    async def test_retired_records_do_not_consume_the_supported_history_limit(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            stable = await self.submit_and_finish()
        payload = json.loads(local_music.get(stable).model_dump_json())
        payload['request']['engine'] = 'heartmula'
        records: list[tuple[str, str, str]] = []
        for index in range(1001):
            identifier = f'{index:032x}'
            records.append((identifier, json.dumps({**payload, 'id': identifier}), '2099-01-01'))
        self.connection.executemany('INSERT INTO local_music_jobs(id,payload_json,created_at) VALUES(?,?,?)', records)
        self.connection.commit()
        history = local_music.list_jobs()
        self.assertEqual([job.id for job in history.jobs], [stable])
        self.assertFalse(history.history_incomplete)

    async def test_invalid_and_truncated_audio_never_enters_library(self) -> None:
        for content in (b'not audio', audio_bytes()[:-8]):
            self.worker.write_text(f'import sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_bytes({content!r})\n')
            with patch.object(local_music, 'prepare_worker', side_effect=self.command):
                identifier = await self.submit_and_finish()
            self.assertEqual(local_music.get(identifier).status, 'failed')
            self.assertEqual(local_music.get(identifier).error_code, 'invalid_music_audio')
            self.assertEqual(db.list_tracks(), [])

    async def test_cancel_before_start_removes_queued_job_and_does_not_spawn(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command), patch.object(local_music, 'spawn_owned', new=AsyncMock()) as spawn:
            job = await local_music.submit(self.body())
            await local_music.cancel(job.id)
        spawn.assert_not_awaited()
        self.assertEqual(local_music.get(job.id).status, 'cancelled')
        self.assertFalse(local_music.work_busy())

    async def test_cancellation_drains_worker_descendants_before_releasing_gpu(self) -> None:
        started = self.root / 'started'
        descendant = self.root / 'descendant.pid'
        self.worker.write_text('import os,sys,subprocess,time\nfrom pathlib import Path\n'
            f'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);Path({str(descendant)!r}).write_text(str(child.pid))\n'
            f'Path({str(started)!r}).write_text("started")\nPath(sys.argv[1]).write_bytes(b"partial")\ntime.sleep(60)\n')
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            job = await local_music.submit(self.body())
            async with asyncio.timeout(10):
                while not started.exists():
                    await asyncio.sleep(.01)
            await local_music.cancel(job.id)
        self.assertEqual(local_music.get(job.id).status, 'cancelled')
        self.assertFalse(local_music.work_busy())
        from app.stems import gpu_lock
        self.assertFalse(gpu_lock.locked())
        self.assertFalse(local_music._pending(job).exists())
        if sys.platform != 'win32':
            pid = int(descendant.read_text())
            async with asyncio.timeout(5):
                while True:
                    inspector = await asyncio.create_subprocess_exec('ps', '-o', 'stat=', '-p', str(pid), stdout=asyncio.subprocess.PIPE)
                    output, _ = await inspector.communicate()
                    if not output.strip() or output.strip().startswith(b'Z'):
                        break
                    await asyncio.sleep(.02)

    async def test_corrupt_job_metadata_cannot_block_worker_cancellation(self) -> None:
        started = self.root / 'started'
        self.worker.write_text('import time\nfrom pathlib import Path\n'
            f'Path({str(started)!r}).write_text("started")\ntime.sleep(60)\n')
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            job = await local_music.submit(self.body())
            owner = local_music._tasks[job.id]
            async with asyncio.timeout(5):
                while not started.is_file():
                    await asyncio.sleep(.01)
            original = self.connection.execute('SELECT payload_json FROM local_music_jobs WHERE id=?', (job.id,)).fetchone()['payload_json']
            self.connection.execute('UPDATE local_music_jobs SET payload_json=? WHERE id=?', ('corrupt', job.id)); self.connection.commit()
            try:
                with self.assertRaises(LocalMusicError):
                    await local_music.cancel(job.id)
                drained = owner.done() and not local_music.work_busy()
            finally:
                self.connection.execute('UPDATE local_music_jobs SET payload_json=? WHERE id=?', (original, job.id)); self.connection.commit()
                await local_music.cancel(job.id)
        self.assertTrue(drained, 'Cancellation must drain the owner before reading damaged metadata')

    async def test_busy_engine_refuses_second_request_without_job_or_worker(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            job = await local_music.submit(self.body())
            with self.assertRaises(LocalMusicError) as caught:
                await local_music.submit(self.body('stable_audio3'))
            self.assertEqual(caught.exception.code, 'local_music_busy')
            await local_music.cancel(job.id)
        self.assertEqual(len(local_music.list_jobs().jobs), 1)

    async def test_recovery_salvages_retained_valid_audio_without_rerunning(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command), patch.object(db, 'insert_track', side_effect=OSError('interrupted catalog write')):
            identifier = await self.submit_and_finish()
        self.assertEqual(local_music.get(identifier).status, 'failed')
        with patch.object(local_music, 'prepare_worker') as prepare:
            await local_music.recover()
            await local_music.recover()
        prepare.assert_not_called()
        self.assertEqual(local_music.get(identifier).status, 'done')
        self.assertEqual(len(db.list_tracks()), 1)

    async def test_publication_failure_keeps_validated_pending_audio_for_recovery(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command), \
                patch.object(local_music.os, 'link', side_effect=OSError('interrupted publication')):
            identifier = await self.submit_and_finish()
        job = local_music.get(identifier)
        self.assertEqual(job.status, 'failed')
        self.assertTrue(local_music._pending(job).is_file())
        await local_music.recover()
        self.assertEqual(local_music.get(identifier).status, 'done')
        self.assertEqual(len(db.list_tracks()), 1)

    async def test_publication_failure_before_digest_preserves_complete_output(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command), patch.object(local_music, '_digest', side_effect=OSError('interrupted checksum read')):
            identifier = await self.submit_and_finish()
        job = local_music.get(identifier)
        self.assertEqual(job.error_code, 'local_music_save_failed')
        self.assertTrue(local_music._pending(job).is_file())
        await local_music.recover()
        self.assertEqual(local_music.get(identifier).status, 'done')
        self.assertEqual(len(db.list_tracks()), 1)

    async def test_recovery_adopts_complete_worker_output_before_publication_receipt(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            job = await local_music.submit(self.body())
            await local_music.cancel(job.id)
        job.status = 'running'
        local_music._store(job)
        local_music._artifact(job.id, 'output.wav', create=True).write_bytes(audio_bytes())
        await local_music.recover()
        self.assertEqual(local_music.get(job.id).status, 'done')
        self.assertEqual(len(db.list_tracks()), 1)

    async def test_recovery_publication_failure_preserves_newly_validated_output(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            job = await local_music.submit(self.body())
            await local_music.cancel(job.id)
        job.status = 'running'
        local_music._store(job)
        local_music._artifact(job.id, 'output.wav', create=True).write_bytes(audio_bytes())
        with patch.object(local_music.os, 'link', side_effect=OSError('interrupted recovery publication')):
            await local_music.recover()
        self.assertTrue(local_music._pending(job).is_file())
        await local_music.recover()
        self.assertEqual(local_music.get(job.id).status, 'done')
        self.assertEqual(len(db.list_tracks()), 1)

    async def test_track_attachment_failure_rolls_back_new_track(self) -> None:
        output = self.root / 'test.wav'
        output.write_bytes(audio_bytes())
        with self.assertRaises(ValueError):
            db.insert_track(model='stable_audio3', title='Test', lyrics='', seed=0, duration_ms=100,
                wall_ms=None, params={}, audio_path=output, abc_path=None, local_music_job_id='missing')
        self.assertEqual(db.list_tracks(), [])

    async def test_native_generation_is_blocked_while_local_music_owns_resources(self) -> None:
        from app.resource_admission import reserve_native, ResourceBusyError
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            job = await local_music.submit(self.body())
            with self.assertRaises(ResourceBusyError):
                await reserve_native(lambda: False, model_id='ace_step')
            await local_music.cancel(job.id)

    async def test_shutdown_drains_pending_jobs_and_prevents_new_work(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            job = await local_music.submit(self.body())
            await local_music.shutdown()
        self.assertFalse(local_music.work_busy())
        self.assertEqual(local_music.get(job.id).status, 'cancelled')
        with self.assertRaises(LocalMusicError) as caught:
            await local_music.submit(self.body())
        self.assertEqual(caught.exception.code, 'local_music_shutting_down')

    async def test_changed_retained_audio_is_never_salvaged(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command), patch.object(db, 'insert_track', side_effect=OSError('interrupted catalog write')):
            identifier = await self.submit_and_finish()
        job = local_music.get(identifier)
        local_music._output(job).write_bytes(audio_bytes() + b'changed')
        await local_music.recover()
        self.assertEqual(local_music.get(identifier).status, 'failed')
        self.assertEqual(db.list_tracks(), [])

    async def test_running_record_without_output_is_interrupted_without_rerun(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            job = await local_music.submit(self.body())
            await local_music.cancel(job.id)
        job.status = 'running'
        local_music._store(job)
        with patch.object(local_music, 'prepare_worker') as prepare:
            await local_music.recover()
        prepare.assert_not_called()
        self.assertEqual(local_music.get(job.id).status, 'interrupted')

    async def test_unverified_worker_blocks_new_generation_across_recovery(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            job = await local_music.submit(self.body())
            await local_music.cancel(job.id)
        identity = WorkerIdentity(pid=12345, token='a' * 32, receipt=str(local_music._artifact(job.id, 'worker.json')))
        local_music._store_identity(job.id, identity)
        with patch.object(local_music, 'terminate_verified', new=AsyncMock(return_value=False)):
            await local_music.recover()
            await local_music.recover()
        self.assertTrue(local_music.work_busy())
        self.assertEqual(local_music.get(job.id).error_code, 'local_music_worker_unverified')
        with self.assertRaises(LocalMusicError):
            await local_music.submit(self.body())

    async def test_recovery_preserves_outside_files_behind_symlink(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            job = await local_music.submit(self.body())
            await local_music.cancel(job.id)
        job.status = 'running'
        local_music._store(job)
        target = self.root / 'outside.wav'
        target.write_bytes(b'preserve')
        local_music._artifact(job.id, 'output.wav', create=True).symlink_to(target)
        await local_music.recover()
        self.assertEqual(target.read_bytes(), b'preserve')
        self.assertEqual(local_music.get(job.id).status, 'failed')
        self.assertEqual(db.list_tracks(), [])

    async def test_deleted_completed_track_is_not_resurrected_by_recovery(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            identifier = await self.submit_and_finish()
        track = local_music.get(identifier).track
        if track is None:
            self.fail('Expected track')
        db.delete_track(track.id)
        await local_music.recover()
        self.assertEqual(db.list_tracks(), [])

    async def test_corrupt_record_is_reported_without_hiding_other_history(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            identifier = await self.submit_and_finish()
        self.connection.execute('INSERT INTO local_music_jobs(id,payload_json,created_at) VALUES(?,?,?)', ('a' * 32, 'bad', '2026-01-01'))
        self.connection.commit()
        await local_music.recover()
        history = local_music.list_jobs()
        self.assertTrue(history.history_incomplete)
        self.assertEqual([job.id for job in history.jobs], [identifier])
