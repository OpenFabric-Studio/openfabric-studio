"""Immutable version regressions; private catalog and mocked model boundary."""
from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from app import audio_versions, db, voice_build
from app.audio_version_contracts import AudioVersion, CreateAudioVersionRequest
from app.api import routes_audio_versions, routes_tracks
from app.gpu_lease import gpu_lease
from app.voice_progress import VoiceProgressTracker


class AudioVersionContractTests(unittest.TestCase):
    def test_ids_and_finite_durations_are_validated(self) -> None:
        for voice_id in ['../voice', 'A' * 32, 'a' * 31]:
            with self.assertRaises(ValidationError):
                CreateAudioVersionRequest(voice_id=voice_id)
        with self.assertRaises(ValidationError):
            AudioVersion(id='a' * 32, track_id=1, kind='original', status='done', created_at='now', duration_ms=float('inf'))


class AudioVersionsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        for name, value in [('DATA_DIR', self.root), ('DB_PATH', self.root / 'catalog.db'),
                            ('FILES_DIR', self.root / 'files'), ('_db', None)]:
            self.enterContext(patch.object(db, name, value))
        self.enterContext(patch.object(voice_build, 'VOICES_DIR', self.root / 'voices'))
        self.enterContext(patch.object(voice_build, 'SEED_VC_DIR', self.root / 'engine'))
        self.enterContext(patch.dict(voice_build._applies, {}, clear=True))
        self.voice_id = 'a' * 32
        voice = self.root / 'voices' / self.voice_id
        voice.mkdir(parents=True)
        meta = voice_build.new_voice_meta(self.voice_id, 'Singer')
        meta['status'] = 'ready'
        for filename, field in [('model.pth', 'checkpoint'), ('config.yml', 'config'), ('reference.wav', 'reference')]:
            path = voice / filename
            path.write_bytes(b'fixture')
            meta[field] = str(path)
        voice_build.write_meta(voice, meta)
        self.original = self.root / 'files' / 'ace_step' / 'song.wav'
        self.original.parent.mkdir(parents=True)
        self.original.write_bytes(b'original audio fixture')
        self.track_id = db.insert_track(model='ace_step', title='Song', lyrics='', seed=1,
            duration_ms=1000, wall_ms=None, params={}, audio_path=self.original, abc_path=None)

    async def asyncTearDown(self) -> None:
        await voice_build.shutdown()
        if db._db is not None:
            db._db.close()

    async def test_progress_follows_actual_holder_and_freezes_after_cancellation(self) -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        async def wait(job: voice_build.ApplyJob) -> None:
            voice_build._apply_phase(job, 'waiting')
            entered.set()
            await release.wait()
        lock = asyncio.Lock()
        with patch.object(voice_build, 'gpu_lock', lock), patch.object(voice_build, '_apply_inner', side_effect=wait):
            async with gpu_lease(lock, 'voice_training', 'Training Singer'):
                version = audio_versions.start_version(self.track_id, self.voice_id)
                await asyncio.wait_for(entered.wait(), timeout=3)
                progress = audio_versions.list_versions(self.track_id).versions[-1].job_progress
                self.assertIsNotNone(progress)
                if progress is not None:
                    self.assertEqual(progress.phase, 'waiting_gpu')
                    self.assertEqual(progress.queue_reason, 'voice_training')
                    self.assertEqual(progress.queue_label, 'Training Singer')
                    self.assertIsNone(progress.estimated_phase_remaining_sec)
            async with gpu_lease(lock, 'voice_conversion', 'Other Track'):
                progress = audio_versions.list_versions(self.track_id).versions[-1].job_progress
                if progress is not None:
                    self.assertEqual(progress.queue_reason, 'voice_conversion')
                    self.assertEqual(progress.queue_label, 'Other Track')
            cancelled = await audio_versions.cancel_version(self.track_id, version.id)
        progress = cancelled.job_progress
        self.assertIsNotNone(progress)
        if progress is not None:
            self.assertEqual(progress.status, 'cancelled')
            self.assertEqual(progress.queue_reason, '')
            self.assertIsNotNone(progress.finished_at)
            reloaded = audio_versions.list_versions(self.track_id).versions[-1].job_progress
            self.assertEqual(reloaded, progress)

    async def test_retry_creates_new_timing_and_different_job_identity(self) -> None:
        entered = asyncio.Event()
        async def wait(job: voice_build.ApplyJob) -> None:
            entered.set()
            await asyncio.Event().wait()
        with patch.object(voice_build, '_apply_inner', side_effect=wait):
            version = audio_versions.start_version(self.track_id, self.voice_id)
            await asyncio.wait_for(entered.wait(), timeout=3)
            cancelled = await audio_versions.cancel_version(self.track_id, version.id)
            entered.clear()
            retried = await audio_versions.retry_version(self.track_id, version.id)
            await asyncio.wait_for(entered.wait(), timeout=3)
            self.assertIsNotNone(cancelled.job_progress)
            self.assertIsNotNone(retried.job_progress)
            if cancelled.job_progress is not None and retried.job_progress is not None:
                self.assertNotEqual(cancelled.job_progress.job_id, retried.job_progress.job_id)
                self.assertIsNone(retried.job_progress.finished_at)
                self.assertGreaterEqual(retried.job_progress.queued_at, cancelled.job_progress.queued_at)

    async def test_interrupted_receipt_freezes_once_and_wrong_version_progress_is_hidden(self) -> None:
        entered = asyncio.Event()
        async def wait(job: voice_build.ApplyJob) -> None:
            entered.set()
            await asyncio.Event().wait()
        with patch.object(voice_build, '_apply_inner', side_effect=wait):
            version = audio_versions.start_version(self.track_id, self.voice_id)
            await asyncio.wait_for(entered.wait(), timeout=3)
            worker = next(iter(voice_build._applies))
            await audio_versions.cancel_version(self.track_id, version.id)
        progress = VoiceProgressTracker.create('apply', '', now=10)
        progress.start(now=11)
        progress.phase('converting', total=8, unit='chunks', current=3, now=12)
        from app.atomic_files import write_object
        receipt = voice_build._apply_file(worker)
        write_object(receipt, {'status': 'running', 'voice_id': self.voice_id, 'audio_version_id': version.id,
                               'job_progress': progress.progress.model_dump(mode='json')})
        from app import audio_version_store
        audio_version_store.set_state(db.get_db(), version.id, 'running', '')
        recovered = audio_versions.list_versions(self.track_id).versions[-1]
        self.assertEqual(recovered.status, 'failed')
        self.assertEqual(recovered.error_code, 'interrupted')
        self.assertIsNotNone(recovered.job_progress)
        if recovered.job_progress is not None:
            self.assertEqual(recovered.job_progress.status, 'failed')
            self.assertEqual(recovered.job_progress.finished_at, 12, 'Unknown offline time was counted as processing')
            self.assertEqual(audio_versions.list_versions(self.track_id).versions[-1].job_progress, recovered.job_progress)
        write_object(receipt, {'status': 'done', 'audio_version_id': 'c' * 32,
                               'job_progress': progress.progress.model_dump(mode='json')})
        self.assertIsNone(audio_versions.list_versions(self.track_id).versions[-1].job_progress)

    async def test_legacy_or_damaged_optional_progress_keeps_status_without_inventing_timing(self) -> None:
        for optional in [None, {'queued_at': 'bad'}]:
            receipt = audio_versions._ApplyReceipt.model_validate({'status': 'done', 'job_progress': optional})
            self.assertEqual(receipt.status, 'done')
            self.assertIsNone(receipt.job_progress)

    async def test_original_snapshot_survives_default_path_changes(self) -> None:
        versions = audio_versions
        original = versions.retain_original(self.track_id, self.original)
        captured = versions.resolve_source(self.track_id, original.id)
        self.assertNotEqual(captured, self.original)
        self.original.write_bytes(b'changed default')
        self.assertEqual(captured.read_bytes(), b'original audio fixture')
        self.assertEqual(versions.list_versions(self.track_id).versions[0].kind, 'original')

    async def test_multiple_voices_always_convert_original_and_keep_every_output(self) -> None:
        versions = audio_versions
        observed: list[bytes] = []
        async def convert(job: voice_build.ApplyJob) -> None:
            source, destination = versions.application_paths(job.track_id, job.audio_version_id)
            observed.append(source.read_bytes())
            destination.write_bytes(f'voice output {len(observed)}'.encode())
            db.update_track_audio(job.track_id, destination)
            job.status = 'done'
            voice_build._write_apply(job)
        with patch.object(voice_build, '_apply_inner', side_effect=convert):
            first = versions.start_version(self.track_id, self.voice_id)
            await asyncio.gather(*(job.task for job in voice_build._applies.values() if job.task is not None))
            second = versions.start_version(self.track_id, self.voice_id)
            await asyncio.gather(*(job.task for job in voice_build._applies.values() if job.task is not None))
        self.assertEqual(observed, [b'original audio fixture'] * 2)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(versions.resolve_source(self.track_id, first.id).read_bytes(), b'voice output 1')
        self.assertEqual(versions.resolve_source(self.track_id, second.id).read_bytes(), b'voice output 2')
        self.assertEqual(len(versions.list_versions(self.track_id).versions), 3)
        self.assertEqual([row['id'] for row in db.list_tracks()], [self.track_id], 'Private conversion tracks leaked into the library')

    async def test_unready_voice_rejected_without_new_versions_or_child_tracks(self) -> None:
        versions = audio_versions
        with self.assertRaises(HTTPException) as error:
            versions.start_version(self.track_id, 'b' * 32)
        self.assertEqual(error.exception.detail, 'voice_missing')
        self.assertEqual(len(db.list_tracks()), 1)
        self.assertFalse((self.root / 'files' / '_versions').exists())

    async def test_missing_original_is_honest_and_latest_voice_is_not_reconverted(self) -> None:
        versions = audio_versions
        voiced = self.original.with_name('song.voiced.wav')
        voiced.write_bytes(b'existing voice')
        self.original.unlink()
        db.update_track_audio(self.track_id, voiced)
        listed = versions.list_versions(self.track_id)
        self.assertFalse(listed.original_available)
        self.assertEqual([version.kind for version in listed.versions], ['voice'])
        with self.assertRaises(HTTPException) as error:
            versions.start_version(self.track_id, self.voice_id)
        self.assertEqual(error.exception.detail, 'original_audio_missing')
        self.assertEqual(voiced.read_bytes(), b'existing voice')

    async def test_path_escape_and_wrong_track_version_are_rejected(self) -> None:
        versions = audio_versions
        original = versions.retain_original(self.track_id, self.original)
        with self.assertRaises(HTTPException):
            versions.resolve_source(self.track_id + 1, original.id)
        outside = self.root / 'outside.wav'
        outside.write_bytes(b'private')
        stored = versions.resolve_source(self.track_id, original.id)
        stored.unlink()
        stored.symlink_to(outside)
        with self.assertRaises(HTTPException):
            versions.resolve_source(self.track_id, original.id)

    async def test_initial_apply_uses_snapshot_after_default_export_changes(self) -> None:
        versions = audio_versions
        original = versions.retain_original(self.track_id, self.original)
        export = self.original.with_suffix('.mp3')
        export.write_bytes(b'lossy default export')
        db.update_track_audio(self.track_id, export)
        observed: list[bytes] = []
        async def convert(job: voice_build.ApplyJob) -> None:
            source, destination = versions.application_paths(job.track_id, job.audio_version_id)
            observed.append(source.read_bytes())
            destination.write_bytes(b'converted')
            db.update_track_audio(job.track_id, destination)
            job.status = 'done'
            voice_build._write_apply(job)
        with patch.object(voice_build, '_apply_inner', side_effect=convert):
            voice_build.start_apply(self.voice_id, self.track_id)
            await asyncio.gather(*(job.task for job in voice_build._applies.values() if job.task is not None))
        listed = versions.list_versions(self.track_id)
        self.assertEqual(observed, [b'original audio fixture'])
        self.assertEqual(len(listed.versions), 2)
        self.assertEqual(listed.versions[1].source_version_id, original.id)

    async def test_cancel_retry_uses_original_and_keeps_completed_sibling(self) -> None:
        versions = audio_versions
        async def complete(job: voice_build.ApplyJob) -> None:
            source, destination = versions.application_paths(job.track_id, job.audio_version_id)
            self.assertEqual(source.read_bytes(), b'original audio fixture')
            destination.write_bytes(b'completed sibling')
            db.update_track_audio(job.track_id, destination)
            job.status = 'done'
            voice_build._write_apply(job)
        with patch.object(voice_build, '_apply_inner', side_effect=complete):
            completed = versions.start_version(self.track_id, self.voice_id)
            await asyncio.gather(*(job.task for job in voice_build._applies.values() if job.task is not None))
        entered = asyncio.Event()
        async def hold(job: voice_build.ApplyJob) -> None:
            entered.set()
            await asyncio.Event().wait()
        with patch.object(voice_build, '_apply_inner', side_effect=hold):
            pending = versions.start_version(self.track_id, self.voice_id)
            await entered.wait()
            cancelled = await versions.cancel_version(self.track_id, pending.id)
        self.assertEqual(cancelled.status, 'cancelled')
        self.assertIsNone(cancelled.audio_url)
        self.assertEqual(versions.resolve_source(self.track_id, completed.id).read_bytes(), b'completed sibling')
        with patch.object(voice_build, '_apply_inner', side_effect=complete):
            retried = await versions.retry_version(self.track_id, pending.id)
            await asyncio.gather(*(job.task for job in voice_build._applies.values() if job.task is not None))
        self.assertEqual(retried.id, pending.id)
        self.assertEqual(versions.resolve_source(self.track_id, completed.id).read_bytes(), b'completed sibling')
        self.assertEqual(versions.resolve_source(self.track_id, pending.id).read_bytes(), b'completed sibling')

    async def test_queued_cancellation_never_starts_model_and_keeps_original(self) -> None:
        versions = audio_versions
        with patch.object(voice_build, '_apply_inner') as boundary:
            version = versions.start_version(self.track_id, self.voice_id)
            cancelled = await versions.cancel_version(self.track_id, version.id)
        boundary.assert_not_called()
        self.assertEqual(cancelled.status, 'cancelled')
        self.assertTrue(versions.list_versions(self.track_id).original_available)

    async def test_start_failure_rolls_back_worker_but_preserves_original(self) -> None:
        versions = audio_versions
        with patch.object(voice_build, '_write_apply', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                versions.start_version(self.track_id, self.voice_id)
        self.assertEqual(len(db.get_db().execute('SELECT * FROM tracks').fetchall()), 1)
        self.assertEqual([version.kind for version in versions.list_versions(self.track_id).versions], ['original'])
        self.assertFalse(voice_build._applies)

    async def test_worker_attachment_failure_rolls_back_child_insert_atomically(self) -> None:
        connection = db.get_db()
        def deny_worker_update(action: int, table: str | None, column: str | None,
                               database: str | None, trigger: str | None) -> int:
            if action == sqlite3.SQLITE_UPDATE and table == 'audio_versions' and column == 'worker_track_id':
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        connection.set_authorizer(deny_worker_update)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                audio_versions.start_version(self.track_id, self.voice_id)
        finally:
            connection.set_authorizer(None)
        self.assertEqual(len(connection.execute('SELECT * FROM tracks').fetchall()), 1,
                         'Failed attachment left a private worker track in the catalog')
        self.assertEqual([item.kind for item in audio_versions.list_versions(self.track_id).versions], ['original'])

    async def test_recovery_marks_interrupted_without_starting_another_model(self) -> None:
        versions = audio_versions
        with patch.object(voice_build, '_apply_inner') as boundary:
            version = versions.start_version(self.track_id, self.voice_id)
            jobs = list(voice_build._applies.values())
            # Model a process crash: task cannot run, persisted status remains queued.
            for job in jobs:
                if job.task is not None:
                    job.task.cancel()
            await asyncio.gather(*(job.task for job in jobs if job.task is not None), return_exceptions=True)
            voice_build._applies.clear()
            await versions.recover()
        boundary.assert_not_called()
        recovered = versions.list_versions(self.track_id).versions[-1]
        self.assertEqual(recovered.status, 'failed')
        self.assertEqual(recovered.error_code, 'interrupted')
        self.assertEqual(recovered.id, version.id)
        self.assertTrue(versions.list_versions(self.track_id).original_available)

    async def test_legacy_sibling_recovery_and_ambiguous_originals(self) -> None:
        versions = audio_versions
        voiced = self.original.with_name('song.voiced.wav')
        voiced.write_bytes(b'old voice')
        db.update_track_audio(self.track_id, voiced)
        listed = versions.list_versions(self.track_id)
        self.assertTrue(listed.original_available)
        self.assertEqual([item.kind for item in listed.versions], ['original', 'voice'])
        other = self.original.with_name('ambiguous.voiced.wav')
        other.write_bytes(b'old voice')
        other.with_name('ambiguous.wav').write_bytes(b'wav')
        other.with_name('ambiguous.mp3').write_bytes(b'mp3')
        second = db.insert_track(model='ace_step', title='', lyrics='', seed=None, duration_ms=None,
            wall_ms=None, params={}, audio_path=other, abc_path=None)
        self.assertFalse(versions.list_versions(second).original_available)

    async def test_delete_drains_workers_then_removes_all_owned_versions(self) -> None:
        versions = audio_versions
        entered, cleaned = asyncio.Event(), asyncio.Event()
        async def convert(job: voice_build.ApplyJob) -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(.01)
                cleaned.set()
        with patch.object(voice_build, '_apply_inner', side_effect=convert):
            versions.start_version(self.track_id, self.voice_id)
            await entered.wait()
            with self.assertRaises(HTTPException):
                db.delete_track(self.track_id)
            async with versions.protect_track_versions_removal(self.track_id):
                self.assertTrue(cleaned.is_set())
                with self.assertRaises(HTTPException):
                    versions.start_version(self.track_id, self.voice_id)
                self.assertTrue(db.delete_track(self.track_id))
        self.assertEqual(db.get_db().execute('SELECT * FROM tracks').fetchall(), [])
        self.assertEqual(db.get_db().execute('SELECT * FROM audio_versions').fetchall(), [])
        self.assertFalse(self.original.exists())
        self.assertEqual(list((self.root / 'files' / '_versions').rglob('*.*')), [])

    async def test_replacement_original_source_belongs_to_independent_track(self) -> None:
        versions = audio_versions
        original_track = db.insert_track(model='upload', title='', lyrics='', seed=None, duration_ms=None,
            wall_ms=None, params={'source':'voice_replacement_source'}, audio_path=self.original, abc_path=None)
        result = self.original.with_name('replacement.voiced.wav')
        result.write_bytes(b'voice result')
        replacement = db.insert_track(model='upload', title='', lyrics='', seed=None, duration_ms=None, wall_ms=None,
            params={'source':'voice_replacement','source_track_id':original_track,'voice_id':self.voice_id},
            audio_path=result, abc_path=None)
        self.assertTrue(versions.list_versions(replacement).original_available)
        async with versions.protect_track_versions_removal(replacement):
            db.delete_track(replacement)
        self.assertEqual(self.original.read_bytes(), b'original audio fixture')
        self.assertIsNotNone(db.get_track(original_track))

    async def test_unconverted_replacement_placeholder_is_not_a_completed_voice_version(self) -> None:
        versions = audio_versions
        source_track = db.insert_track(model='upload', title='', lyrics='', seed=None, duration_ms=None,
            wall_ms=None, params={'source':'voice_replacement_source'}, audio_path=self.original, abc_path=None)
        result = self.original.with_name('pending.voiced.wav')
        result.write_bytes(b'unconverted dry placeholder')
        replacement = db.insert_track(model='upload', title='', lyrics='', seed=None, duration_ms=None, wall_ms=None,
            params={'source':'voice_replacement','source_track_id':source_track,'voice_id':self.voice_id},
            audio_path=result, abc_path=None)
        listed = versions.list_versions(replacement)
        self.assertTrue(listed.original_available)
        self.assertEqual([item.kind for item in listed.versions], ['original'])

    async def test_deleting_converted_replacement_cleans_its_old_placeholder(self) -> None:
        source_track = db.insert_track(model='upload', title='', lyrics='', seed=None, duration_ms=None,
            wall_ms=None, params={'source':'voice_replacement_source'}, audio_path=self.original, abc_path=None)
        placeholder = self.original.with_name('staged.voiced.wav')
        placeholder.write_bytes(b'dry placeholder')
        replacement = db.insert_track(model='upload', title='', lyrics='', seed=None, duration_ms=None, wall_ms=None,
            params={'source':'voice_replacement','source_track_id':source_track,'voice_id':self.voice_id},
            audio_path=placeholder, abc_path=None)
        async def complete(job: voice_build.ApplyJob) -> None:
            _source, destination = audio_versions.application_paths(job.track_id, job.audio_version_id)
            destination.write_bytes(b'completed')
            db.update_track_audio(job.track_id, destination)
            job.status = 'done'
            voice_build._write_apply(job)
        with patch.object(voice_build, '_apply_inner', side_effect=complete):
            voice_build.start_apply(self.voice_id, replacement)
            await asyncio.gather(*(job.task for job in voice_build._applies.values() if job.task is not None))
        async with audio_versions.protect_track_versions_removal(replacement):
            db.delete_track(replacement)
        self.assertFalse(placeholder.exists(), 'A retired replacement placeholder leaked after deletion')
        self.assertEqual(self.original.read_bytes(), b'original audio fixture')
        self.assertIsNotNone(db.get_track(source_track))

    async def test_original_snapshot_write_failure_rolls_back_partial(self) -> None:
        versions = audio_versions
        with patch('app.audio_versions.shutil.copyfileobj', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                versions.retain_original(self.track_id, self.original)
        self.assertEqual(db.get_db().execute('SELECT * FROM audio_versions').fetchall(), [])
        self.assertEqual(list((self.root / 'files' / '_versions').rglob('*.*')), [])
        self.assertEqual(self.original.read_bytes(), b'original audio fixture')

    async def test_api_create_cancel_retry_download_and_invalid_ids(self) -> None:
        app = FastAPI()
        app.include_router(routes_audio_versions.router)
        app.include_router(routes_tracks.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            listed = await client.get(f'/api/tracks/{self.track_id}/versions')
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertTrue(listed.json()['original_available'])
            async def hold(job: voice_build.ApplyJob) -> None:
                await asyncio.Event().wait()
            with patch.object(voice_build, '_apply_inner', side_effect=hold):
                created = await client.post(f'/api/tracks/{self.track_id}/versions', json={'voice_id':self.voice_id})
                self.assertEqual(created.status_code, 200, created.text)
                version = AudioVersion.model_validate(created.json())
                self.assertIsNone(version.audio_url)
                unavailable = await client.get(f'/api/tracks/{self.track_id}/versions/{version.id}/audio')
                self.assertEqual(unavailable.status_code, 409)
                cancelled = await client.post(f'/api/tracks/{self.track_id}/versions/{version.id}/cancel')
                self.assertEqual(cancelled.json()['status'], 'cancelled')
            async def complete(job: voice_build.ApplyJob) -> None:
                _source, destination = audio_versions.application_paths(job.track_id, job.audio_version_id)
                destination.write_bytes(b'completed voice bytes')
                db.update_track_audio(job.track_id, destination)
                job.status = 'done'
                voice_build._write_apply(job)
            with patch.object(voice_build, '_apply_inner', side_effect=complete):
                retried = await client.post(f'/api/tracks/{self.track_id}/versions/{version.id}/retry')
                self.assertEqual(retried.status_code, 200, retried.text)
                await asyncio.gather(*(job.task for job in voice_build._applies.values() if job.task is not None))
            download = await client.get(f'/api/tracks/{self.track_id}/versions/{version.id}/audio')
            self.assertEqual(download.status_code, 200, download.text)
            self.assertEqual(download.content, b'completed voice bytes')
            self.assertIn('filename=', download.headers['content-disposition'])
            invalid = await client.get(f'/api/tracks/{self.track_id}/versions/not-an-id/audio')
            self.assertEqual(invalid.status_code, 422)
            wrong = await client.get(f'/api/tracks/{self.track_id + 1}/versions/{version.id}/audio')
            self.assertEqual(wrong.status_code, 404)

    async def test_api_storage_failure_uses_stable_error_without_private_details(self) -> None:
        app = FastAPI()
        app.include_router(routes_audio_versions.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch.object(audio_versions, 'start_version', side_effect=OSError('private catalog path')), self.assertLogs('app.api.routes_audio_versions', level='ERROR'):
                response = await client.post(f'/api/tracks/{self.track_id}/versions', json={'voice_id':self.voice_id})
            self.assertEqual(response.status_code, 500, response.text)
            self.assertEqual(response.json()['detail'], 'audio_version_storage_failed')
            self.assertNotIn('private catalog', response.text)

    async def test_cancelled_cancel_caller_still_drains_worker(self) -> None:
        entered, finishing, finished, release = (asyncio.Event() for _ in range(4))
        async def conversion(job: voice_build.ApplyJob) -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                finishing.set()
                await release.wait()
                finished.set()
        with patch.object(voice_build, '_apply_inner', side_effect=conversion):
            version = audio_versions.start_version(self.track_id, self.voice_id)
            await entered.wait()
            cancel = asyncio.create_task(audio_versions.cancel_version(self.track_id, version.id))
            await finishing.wait()
            cancel.cancel()
            await asyncio.sleep(0)
            self.assertFalse(cancel.done())
            release.set()
            await asyncio.gather(cancel, return_exceptions=True)
        self.assertTrue(finished.is_set())
        self.assertFalse(voice_build._applies)
        self.assertEqual(audio_versions.list_versions(self.track_id).versions[-1].status, 'cancelled')
