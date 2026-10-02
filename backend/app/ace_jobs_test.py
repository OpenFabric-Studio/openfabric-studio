"""Durable ACE batches: browser-independent save, retry and restart recovery."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from pydantic import TypeAdapter
from fastapi import HTTPException, UploadFile
from app.contracts import JsonObject
from app import db, ace_jobs
from app import audio_versions

class AceJobsTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_and_style_audio_are_forwarded_independently(self) -> None:
        requests: list[bytes] = []
        async def engine(request: httpx.Request) -> httpx.Response:
            requests.append(await request.aread())
            return httpx.Response(200, json={'code': 200, 'data': {'task_id': 'reference-job', 'status': 'queued', 'queue_position': 0}})
        client = httpx.AsyncClient(transport=httpx.MockTransport(engine))
        source = UploadFile(filename='source.wav', file=io.BytesIO(b'source-fixture'))
        style = UploadFile(filename='style.flac', file=io.BytesIO(b'style-fixture'))
        with patch.object(ace_jobs, '_engine_running', return_value=True), \
             patch.object(ace_jobs.httpx, 'AsyncClient', return_value=client), \
             patch.object(ace_jobs, 'launch'):
            released = await ace_jobs.submit({'lyrics': 'test', 'use_cot_caption': False}, 'Reference', None, source, style_audio=style)
        self.assertEqual(released.task_id, 'reference-job')
        self.assertIn(b'name="ctx_audio"', requests[0])
        self.assertIn(b'source-fixture', requests[0])
        self.assertIn(b'name="ref_audio"', requests[0])
        self.assertIn(b'style-fixture', requests[0])
        self.assertIn(b'name="use_cot_caption"\r\n\r\nfalse', requests[0])

    async def test_internal_generation_snapshot_does_not_reach_native_engine(self) -> None:
        requests: list[JsonObject] = []
        async def engine(request: httpx.Request) -> httpx.Response:
            requests.append(TypeAdapter(JsonObject).validate_json(await request.aread()))
            return httpx.Response(200, json={'code': 200, 'data': {'task_id': 'snapshot-job', 'status': 'queued', 'queue_position': 0}})
        client = httpx.AsyncClient(transport=httpx.MockTransport(engine))
        with patch.object(ace_jobs, '_engine_running', return_value=True), \
             patch.object(ace_jobs.httpx, 'AsyncClient', return_value=client), \
             patch.object(ace_jobs, 'launch'):
            await ace_jobs.submit({'lyrics': 'test', '_generation_settings': {'engine': 'ace_step'}}, 'Snapshot', None, None)
        self.assertNotIn('_generation_settings', requests[0])

    async def test_deletion_reserves_failed_batch_against_concurrent_save_retry(self) -> None:
        self.create_job()
        results = [{'file':'/v1/audio?path=one.wav'}, {'file':'/v1/audio?path=two.wav'}]
        calls = 0
        async def download(client: httpx.AsyncClient, endpoint: str, destination: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('injected second candidate failure')
            destination.write_bytes(b'private model-boundary fixture')
        entered, release = asyncio.Event(), asyncio.Event()
        original_guard = audio_versions.protect_track_versions_removal
        @asynccontextmanager
        async def held_guard(track_id: int) -> AsyncIterator[None]:
            entered.set()
            await release.wait()
            async with original_guard(track_id):
                yield
        deletion: asyncio.Task[None] | None = None
        try:
            with patch.object(ace_jobs, '_download', side_effect=download), \
                 patch.object(audio_versions, 'protect_track_versions_removal', side_effect=held_guard):
                with self.assertLogs('app.ace_jobs', level='ERROR'):
                    async with httpx.AsyncClient() as client:
                        await ace_jobs.save_results(client, 'job1', results)
                self.assertEqual(len(db.list_tracks()), 1)
                self.assertEqual(ace_jobs.get_job('job1').status, 'failed')
                deletion = asyncio.create_task(ace_jobs.delete('job1'))
                await entered.wait()
                rejected = False
                try:
                    await ace_jobs.retry_save('job1')
                except HTTPException as exc:
                    self.assertEqual(exc.status_code, 409)
                    rejected = True
                await asyncio.gather(*list(ace_jobs._tasks.values()))
                release.set()
                await deletion
                self.assertTrue(rejected, 'Retry was accepted after batch deletion reserved its track list')
            self.assertEqual(db.list_tracks(), [])
            self.assertEqual(db.get_db().execute('SELECT * FROM ace_jobs').fetchall(), [])
            self.assertFalse(ace_jobs._tasks)
        finally:
            release.set()
            if deletion is not None:
                await asyncio.gather(deletion, return_exceptions=True)
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch.object(db, '_db', None), patch.object(db, 'DATA_DIR', root), patch.object(db, 'DB_PATH', root/'catalog.db'), patch.object(db, 'FILES_DIR', root/'files')]
        for p in self.patches: p.start()
        self.connection = db.get_db()
        ace_jobs.ensure_schema()

    async def asyncTearDown(self):
        await ace_jobs.shutdown()
        self.connection.close()
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def create_job(self, task_id='job1', voice_id=None):
        ace_jobs.record_job(task_id, {'batch_size': 2, 'audio_format': 'wav', 'lyrics': 'test'}, 'Batch', voice_id)

    async def test_admission_reports_persisted_work_without_a_live_monitor(self):
        self.assertFalse(ace_jobs.work_busy())
        self.create_job()
        self.assertTrue(ace_jobs.work_busy())
        job = ace_jobs.get_job('job1')
        job.status = 'done'
        ace_jobs._store(job)
        self.assertFalse(ace_jobs.work_busy())

    async def test_all_candidates_saved_before_voice_starts_and_retry_is_idempotent(self):
        self.create_job(voice_id='a'*32)
        results = [{'file':'/v1/audio?path=one.wav'}, {'file':'/v1/audio?path=two.wav'}]
        seen = []
        def start_voice(voice_id, track_id):
            seen.append((voice_id, track_id, len(db.list_tracks())))
            return {'status': 'queued'}
        async def download(client, url, dest): dest.write_bytes(b'audio')
        with patch.object(ace_jobs, '_download', side_effect=download), patch.object(ace_jobs, 'start_apply', side_effect=start_voice):
            async with httpx.AsyncClient() as client:
                await ace_jobs.save_results(client, 'job1', results)
                await ace_jobs.save_results(client, 'job1', results)
        self.assertEqual(len(db.list_tracks()), 2)
        self.assertEqual(len(seen), 2)
        self.assertTrue(all(item[2] == 2 for item in seen))
        self.assertEqual(ace_jobs.get_job('job1').status, 'done')
        self.assertEqual(len(ace_jobs.get_job('job1').tracks), 2)

    async def test_partial_save_retry_and_restart_do_not_duplicate_first_candidate(self):
        self.create_job()
        calls = 0
        async def download(client, url, dest):
            nonlocal calls
            calls += 1
            if calls == 2: raise OSError('private filesystem path')
            dest.write_bytes(b'audio')
        results = [{'file':'/v1/audio?path=one.wav'}, {'file':'/v1/audio?path=two.wav'}]
        with patch.object(ace_jobs, '_download', side_effect=download):
            async with httpx.AsyncClient() as client:
                await ace_jobs.save_results(client, 'job1', results)
                failed = ace_jobs.get_job('job1')
                self.assertEqual(failed.status, 'failed')
                self.assertEqual(failed.error_code, 'save_failed')
                self.assertNotIn('private', failed.error)
                self.assertEqual(len(failed.tracks), 1)
                await ace_jobs.save_results(client, 'job1', results)
        self.assertEqual(len(db.list_tracks()), 2)
        self.assertEqual(calls, 3)
        self.assertEqual(ace_jobs.get_job('job1').status, 'done')

    async def test_engine_cannot_redirect_download_to_an_external_url(self):
        for path in ('https://attacker.invalid/song.wav', '//attacker.invalid/a', '/other?path=a', '/v1/audio?path=a&other=b'):
            with self.subTest(path=path), self.assertRaises(ValueError): ace_jobs.audio_endpoint(path)
        self.assertEqual(ace_jobs.audio_endpoint('/v1/audio?path=a%2Fb.wav'), '/v1/audio?path=a%2Fb.wav')

    async def test_backend_monitor_saves_without_a_browser_request(self):
        self.create_job()
        async def engine(client, endpoint, payload):
            return [{'task_id':'job1', 'status':1, 'result':json.dumps([{'file':'/v1/audio?path=a.wav'},{'file':'/v1/audio?path=b.wav'}])}]
        async def download(client, url, dest): dest.write_bytes(b'audio')
        with patch.object(ace_jobs, '_engine_json', side_effect=engine), patch.object(ace_jobs, '_download', side_effect=download), patch.object(ace_jobs, '_engine_running', return_value=True):
            ace_jobs.launch('job1')
            task = ace_jobs._tasks['job1']
            await asyncio.wait_for(task, 2)
        self.assertEqual(len(db.list_tracks()), 2)
        self.assertEqual(ace_jobs.get_job('job1').status, 'done')

    async def test_shutdown_keeps_completed_candidates_and_marks_work_interrupted(self):
        self.create_job()
        blocked = asyncio.Event()
        async def engine(client, endpoint, payload):
            blocked.set()
            await asyncio.Future()
        with patch.object(ace_jobs, '_engine_json', side_effect=engine), patch.object(ace_jobs, '_engine_running', return_value=True):
            ace_jobs.launch('job1')
            await asyncio.wait_for(blocked.wait(), 2)
            await ace_jobs.shutdown()
        self.assertEqual(ace_jobs.get_job('job1').error_code, 'interrupted')
        self.assertFalse(ace_jobs._tasks)

    async def test_retry_after_connection_reopen_recovers_persisted_candidates(self):
        self.create_job()
        results = [{'file': '/v1/audio?path=one.wav'}, {'file': '/v1/audio?path=two.wav'}]
        calls = 0
        async def download(client, url, dest):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('disk error')
            dest.write_bytes(b'audio')
        with patch.object(ace_jobs, '_download', side_effect=download):
            async with httpx.AsyncClient() as client:
                await ace_jobs.save_results(client, 'job1', results)
            self.connection.close()
            db._db = None
            self.connection = db.get_db()
            ace_jobs.ensure_schema()
            await ace_jobs.retry_save('job1')
            await asyncio.wait_for(ace_jobs._tasks['job1'], 2)
        self.assertEqual(len(db.list_tracks()), 2)
        self.assertEqual(ace_jobs.get_job('job1').status, 'done')

    async def test_local_api_rejects_non_object_params_without_calling_engine(self):
        from app.main import app
        with patch.object(ace_jobs, 'submit') as upstream:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.post('/api/ace-jobs', data={'params': '[]'})
                self.assertEqual(response.status_code, 422)
                upstream.assert_not_called()
                response = await client.post('/api/ace-jobs/query', json={'task_id_list': 3})
                self.assertEqual(response.status_code, 422)

    async def test_cancel_does_not_overwrite_a_batch_that_finishes_during_engine_request(self):
        self.create_job()
        async def finish(client, endpoint, payload):
            job = ace_jobs.get_job('job1')
            job.status = 'done'
            ace_jobs._store(job)
            return {'status': 'succeeded'}
        with patch.object(ace_jobs, '_engine_running', return_value=True), patch.object(ace_jobs, '_engine_json', side_effect=finish):
            self.assertEqual((await ace_jobs.cancel('job1')).status, 'done')

    async def test_legacy_adoption_preserves_saved_candidates_and_is_idempotent(self):
        dest = db.model_dir('ace_step') / 'old.wav'
        dest.write_bytes(b'old audio')
        track_id = db.insert_track(model='ace_step', title='Old batch', lyrics='', seed=None, duration_ms=None, wall_ms=None, params={}, audio_path=dest, abc_path=None)
        with patch.object(ace_jobs, 'launch') as launch:
            first = ace_jobs.adopt('old-job', {'audio_format': 'wav', 'batch_size': 2}, 'Old batch', None, [track_id])
            second = ace_jobs.adopt('old-job', {}, 'Ignored', None, [])
        self.assertEqual(first.tracks[0].id, track_id)
        self.assertEqual(second.tracks[0].id, track_id)
        self.assertEqual(len(ace_jobs.list_jobs()), 1)
        launch.assert_called_once_with('old-job')

    async def test_legacy_adoption_rejects_missing_tracks_before_recording_a_job(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            ace_jobs.adopt('old-job', {}, 'Old batch', None, [100])
        self.assertEqual(ace_jobs.list_jobs(), [])

    async def test_schema_read_does_not_commit_an_existing_track_transaction(self):
        self.connection.execute('BEGIN IMMEDIATE')
        self.connection.execute("INSERT INTO projects(created_at, updated_at, name, data_json) VALUES('now','now','marker','{}')")
        ace_jobs.ensure_schema()
        self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()
        self.assertEqual(db.list_projects(), [])

    async def test_oversized_engine_metadata_does_not_corrupt_saved_tracks(self):
        self.create_job()
        results = [{'file': '/v1/audio?path=one.wav', 'metas': {'duration': 1e308}, 'seed_value': '9'*100}]
        async def download(client, url, dest): dest.write_bytes(b'audio')
        with patch.object(ace_jobs, '_download', side_effect=download):
            async with httpx.AsyncClient() as client:
                await ace_jobs.save_results(client, 'job1', results)
        job = ace_jobs.get_job('job1')
        self.assertEqual(job.status, 'done')
        self.assertIsNone(job.tracks[0].duration_ms)
        self.assertIsNone(job.tracks[0].seed)

    async def test_restart_reuses_completed_audio_when_interrupted_before_catalog_insert(self):
        class Interrupted(BaseException):
            pass
        self.create_job()
        results = [{'file': '/v1/audio?path=one.wav'}]
        async def download(client, url, dest): dest.write_bytes(b'complete audio')
        with patch.object(ace_jobs, '_download', side_effect=download), patch.object(db, 'insert_track', side_effect=Interrupted):
            async with httpx.AsyncClient() as client:
                with self.assertRaises(Interrupted):
                    await ace_jobs.save_results(client, 'job1', results)
        self.assertEqual(len(list(db.model_dir('ace_step').glob('*.wav'))), 1)
        self.connection.close()
        db._db = None
        self.connection = db.get_db()
        with patch.object(ace_jobs, '_download', side_effect=AssertionError('Engine audio disappeared')):
            ace_jobs.recover()
            await asyncio.wait_for(ace_jobs._tasks['job1'], 2)
        self.assertEqual(len(db.list_tracks()), 1)
        self.assertEqual(ace_jobs.get_job('job1').status, 'done')

    async def test_delete_cleans_unregistered_files_for_all_returned_candidates(self):
        ace_jobs.record_job('job1', {'batch_size': 1, 'audio_format': 'wav'}, 'Batch', None)
        results = [{'file': '/v1/audio?path=one.wav'}, {'file': '/v1/audio?path=two.wav'}]
        insert = db.insert_track
        calls = 0
        def fail_second(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('Database full')
            return insert(**kwargs)
        async def download(client, url, dest): dest.write_bytes(b'audio')
        with patch.object(ace_jobs, '_download', side_effect=download), patch.object(db, 'insert_track', side_effect=fail_second):
            async with httpx.AsyncClient() as client:
                await ace_jobs.save_results(client, 'job1', results)
        self.assertEqual(len(list(db.model_dir('ace_step').glob('*.wav'))), 2)
        await ace_jobs.delete('job1')
        self.assertEqual(list(db.model_dir('ace_step').glob('*')), [])
