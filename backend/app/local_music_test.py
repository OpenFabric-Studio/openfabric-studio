"""Local music owns worker sessions and retains normal library tracks."""
from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
import wave
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
import httpx
from pydantic import ValidationError

from app import db, local_music
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
    def test_validates_model_limits_and_lyrics_before_admission(self) -> None:
        valid = {'engine': 'heartmula', 'prompt': 'Jazz', 'duration_seconds': 95}
        for update in ({'prompt': '  '}, {'engine': 'unknown'}, {'duration_seconds': 241}, {'seed': -1}, {'variant': 'medium'},
                       {'seed': True}, {'adapter': {'id': 'a' * 32, 'strength': 1}}, {'engine': 'stable_audio3', 'lyrics': 'Sing this'},
                       {'engine': 'stable_audio3', 'duration_seconds': 121},
                       {'output_path': '/tmp/owned-by-user.wav'}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                LocalMusicRequest.model_validate({**valid, **update})
        request = LocalMusicRequest(engine='heartmula', prompt='Jazz', lyrics='Hello world', duration_seconds=95)
        self.assertEqual(request.engine, 'heartmula')
        LocalMusicRequest(engine='stable_audio3', prompt='Jazz', variant='medium', duration_seconds=380)


class LocalMusicJobsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        statuses = LocalMusicEngines(engines=[LocalMusicEngineStatus(id=engine, installed=True,
            weights_ready=True, supported=True, runtime='test', detail='') for engine in ('heartmula', 'stable_audio3')])
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
        self.temporary.cleanup()

    def body(self, engine: str = 'heartmula') -> LocalMusicRequest:
        return LocalMusicRequest.model_validate({'engine': engine, 'prompt': 'Warm jazz', 'title': 'Test song', 'duration_seconds': 95})

    def command(self, request: LocalMusicRequest, output: Path) -> tuple[list[str], Path, dict[str, str]]:
        return [sys.executable, str(self.worker), str(output)], self.root, dict(os.environ)

    async def submit_and_finish(self, engine: str = 'heartmula') -> str:
        job = await local_music.submit(self.body(engine))
        await local_music._tasks[job.id]
        return job.id

    async def test_each_engine_publishes_once_as_a_normal_track(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            for engine in ('heartmula', 'stable_audio3'):
                identifier = await self.submit_and_finish(engine)
                final = local_music.get(identifier)
                self.assertEqual(final.status, 'done')
                self.assertIsNotNone(final.track)
                if final.track is None:
                    self.fail('Expected saved track')
                self.assertEqual(final.track.model, engine)
                self.assertEqual(final.track.params['local_music_job_id'], identifier)
            await local_music.recover()
            await local_music.recover()
        self.assertEqual(len(db.list_tracks()), 2)
        self.assertFalse(local_music.work_busy())

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
            for engine in ('heartmula', 'stable_audio3'):
                await self.submit_and_finish(engine)
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

    async def test_rename_failure_keeps_validated_pending_audio_for_recovery(self) -> None:
        original = Path.replace
        def interrupted(path: Path, target: str | os.PathLike[str]) -> Path:
            if path.name == 'output.wav':
                raise OSError('interrupted rename')
            return original(path, target)
        with patch.object(local_music, 'prepare_worker', side_effect=self.command), patch.object(Path, 'replace', new=interrupted):
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

    async def test_recovery_rename_failure_preserves_newly_validated_output(self) -> None:
        with patch.object(local_music, 'prepare_worker', side_effect=self.command):
            job = await local_music.submit(self.body())
            await local_music.cancel(job.id)
        job.status = 'running'
        local_music._store(job)
        local_music._artifact(job.id, 'output.wav', create=True).write_bytes(audio_bytes())
        original = Path.replace
        def interrupted(path: Path, target: str | os.PathLike[str]) -> Path:
            if path.name == 'output.wav':
                raise OSError('interrupted recovery rename')
            return original(path, target)
        with patch.object(Path, 'replace', new=interrupted):
            await local_music.recover()
        self.assertTrue(local_music._pending(job).is_file())
        await local_music.recover()
        self.assertEqual(local_music.get(job.id).status, 'done')
        self.assertEqual(len(db.list_tracks()), 1)

    async def test_track_attachment_failure_rolls_back_new_track(self) -> None:
        output = self.root / 'test.wav'
        output.write_bytes(audio_bytes())
        with self.assertRaises(ValueError):
            db.insert_track(model='heartmula', title='Test', lyrics='', seed=0, duration_ms=100,
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
