"""Stem exports preserve source identity and share owned export cleanup."""
from __future__ import annotations

import asyncio
import threading
import unittest
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import audio_exports as exports, db, stems
from app import audio_exports_test as export_test_support


class StemExportTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = export_test_support.AudioExportTests.asyncSetUp
    asyncTearDown = export_test_support.AudioExportTests.asyncTearDown

    async def test_failed_conditioning_drain_keeps_completed_exports_and_source(self) -> None:
        from app import music_conditioning
        from app.local_music_contracts import LocalMusicError
        source = self.make_source()
        job = await exports.create_stem_export(self.track_id, 'vocals', 'mp3')
        await asyncio.wait_for(exports._tasks[job.id], 15)
        retained = exports._directory(self.track_id, job.id)

        @asynccontextmanager
        async def blocked(track_id: int) -> AsyncIterator[None]:
            self.assertEqual(track_id, self.track_id)
            raise LocalMusicError('conditioning_source_busy', 409)
            yield

        with patch.object(music_conditioning, 'protect_stem_replacement', blocked):
            for guard in (exports.protect_stem_replacement, exports.protect_stem_exports_removal):
                with self.assertRaises(exports.AudioExportError) as caught:
                    async with guard(self.track_id):
                        self.fail('Unverified conditioning worker must block source replacement')
                self.assertEqual(caught.exception.code, 'conditioning_source_busy')
                self.assertTrue(source.is_file())
                self.assertTrue(retained.is_dir())
                self.assertNotIn(self.track_id, exports._stem_deleting)
        self.assertEqual(exports.get_stem_export(self.track_id, 'vocals', job.id).status, 'done')

    def make_source(self) -> Path:
        path = db.FILES_DIR / 'vocals.wav'
        path.write_bytes(self.source.read_bytes())
        db.update_track_stems(self.track_id, {'vocals': str(path)})
        return path

    async def test_rerun_waits_for_export_capture_and_keeps_its_source(self) -> None:
        source = db.stems_dir('ace_step', self.track_id) / 'vocals.wav'
        source.parent.mkdir(parents=True)
        source.write_bytes(self.source.read_bytes())
        db.update_track_stems(self.track_id, {'vocals': str(source)})
        entered, release = threading.Event(), threading.Event()
        original_digest = exports._digest
        def digest(path: Path) -> str:
            if path == source and not release.is_set():
                entered.set()
                if not release.wait(5):
                    raise TimeoutError('Fixture digest was not released')
            return original_digest(path)
        async def worker(track_id: int) -> None:
            raise AssertionError('A stem rerun must not launch during export')
        with patch.dict(stems._jobs, {}, clear=True), patch.object(exports, '_digest', side_effect=digest), \
             patch.object(stems, '_run', side_effect=worker) as run:
            creating = asyncio.create_task(exports.create_stem_export(self.track_id, 'vocals', 'mp3'))
            rerun: asyncio.Task[stems.StemJob] | None = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                rerun = asyncio.create_task(stems.start(self.track_id, force=True))
                await asyncio.sleep(0)
                self.assertFalse(rerun.done(), 'Rerun ignored the ongoing source capture')
                release.set()
                job = await creating
                with self.assertRaises(exports.AudioExportError) as caught:
                    await rerun
                self.assertEqual(caught.exception.code, 'stem_busy')
                run.assert_not_called()
                self.assertEqual(source.read_bytes(), self.source.read_bytes())
                await asyncio.wait_for(exports._tasks[job.id], 15)
                self.assertEqual(exports.get_stem_export(self.track_id, 'vocals', job.id).status, 'done')
            finally:
                release.set()
                await asyncio.gather(creating, *( [rerun] if rerun is not None else []), return_exceptions=True)
                await stems.cancel(self.track_id)

    async def test_waiting_starts_recheck_registry_and_publish_one_job(self) -> None:
        entered = asyncio.Event()
        async def worker(track_id: int) -> None:
            entered.set()
            await asyncio.Event().wait()
        with patch.dict(stems._jobs, {}, clear=True), patch.object(stems, '_run', side_effect=worker) as run:
            async with exports._lock:
                first = asyncio.create_task(stems.start(self.track_id))
                second = asyncio.create_task(stems.start(self.track_id))
                await asyncio.sleep(0)
                self.assertFalse(first.done())
                self.assertFalse(second.done())
            try:
                first_job, second_job = await asyncio.gather(first, second)
                await entered.wait()
                self.assertIs(first_job, second_job)
                run.assert_called_once_with(self.track_id)
            finally:
                await stems.cancel(self.track_id)

    async def test_start_refuses_removal_but_keeps_existing_noop_jobs(self) -> None:
        with patch.dict(stems._jobs, {}, clear=True), patch.object(stems, '_run', new=AsyncMock()):
            async with exports.protect_stem_exports_removal(self.track_id):
                with self.assertRaises(exports.AudioExportError) as caught:
                    await stems.start(self.track_id, force=True)
                self.assertEqual(caught.exception.code, 'stem_busy')
                for state in ('queued', 'done'):
                    existing = stems.StemJob(status='queued' if state == 'queued' else 'done')
                    stems._jobs[self.track_id] = existing
                    self.assertIs(await stems.start(self.track_id), existing)
            stems._jobs.clear()

    async def test_start_route_returns_stable_conflict_for_active_stem_export(self) -> None:
        import httpx
        from fastapi import FastAPI
        from app.api import routes_stems
        self.make_source()
        app = FastAPI()
        app.include_router(routes_stems.router)
        with patch.object(exports, '_capacity', asyncio.Semaphore(0)), patch.dict(stems._jobs, {}, clear=True), \
             patch.object(stems, '_run', new=AsyncMock()) as run:
            job = await exports.create_stem_export(self.track_id, 'vocals', 'mp3')
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
                    response = await client.post(f'/api/tracks/{self.track_id}/stems?force=true')
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json(), {'detail': 'stem_busy'})
                run.assert_not_called()
            finally:
                await exports.cancel_stem_export(self.track_id, 'vocals', job.id)
                await stems.cancel(self.track_id)

    async def test_delete_waiting_behind_start_keeps_worker_and_completed_export(self) -> None:
        from fastapi import HTTPException
        from app.api import routes_stems
        from app.job_lifecycle import cancel_and_wait
        self.make_source()
        export = await exports.create_stem_export(self.track_id, 'vocals', 'mp3')
        await asyncio.wait_for(exports._tasks[export.id], 15)
        async def worker(track_id: int) -> None:
            stems._jobs[track_id].status = 'running'
            await asyncio.Event().wait()
        with patch.dict(stems._jobs, {}, clear=True), patch.object(stems, '_run', side_effect=worker):
            async with exports._lock:
                starting = asyncio.create_task(stems.start(self.track_id, force=True))
                await asyncio.sleep(0)
                deleting = asyncio.create_task(routes_stems.delete_stems(self.track_id))
                await asyncio.sleep(0)
            job = await starting
            try:
                with self.assertRaises(HTTPException) as caught:
                    await deleting
                self.assertEqual(caught.exception.status_code, 409)
                self.assertEqual(caught.exception.detail, 'stem_busy')
                self.assertIs(stems._jobs[self.track_id], job)
                self.assertIsNotNone(job.task)
                if job.task is not None:
                    self.assertFalse(job.task.done())
                self.assertEqual(exports.get_stem_export(self.track_id, 'vocals', export.id).status, 'done')
                self.assertIsNotNone(db.get_track(self.track_id)['stems_json'])
            finally:
                await asyncio.gather(deleting, return_exceptions=True)
                await cancel_and_wait(job.task)

    async def test_advertised_stem_manifests_serve_exact_media_and_reject_wrong_owner(self) -> None:
        import httpx
        from fastapi import FastAPI
        from app.api import routes_audio_exports,routes_stem_exports
        from app import export_provenance
        from app.export_provenance_contracts import ExportProvenance
        self.make_source()
        job=await exports.create_stem_export(self.track_id,'vocals','mp3')
        await asyncio.wait_for(exports._tasks[job.id],15)
        done=exports.get_stem_export(self.track_id,'vocals',job.id)
        self.assertEqual(done.status,'done',done.error_code)
        app=FastAPI();app.include_router(routes_audio_exports.router);app.include_router(routes_stem_exports.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as client:
            for url in (done.provenance_url,done.manifest_url):
                if url is None:raise AssertionError('missing stem manifest URL')
                response=await client.get(url)
                self.assertEqual(response.status_code,200,response.text)
                self.assertIn(f'/stems/vocals/exports/{job.id}/',url)
                wrong=await client.get(url.replace('/stems/vocals/','/stems/drums/'))
                self.assertEqual(wrong.status_code,404)
            if done.provenance_url is None:raise AssertionError('missing stem JSON URL')
            proof=ExportProvenance.model_validate_json((await client.get(done.provenance_url)).content)
            self.assertEqual(proof.artifact_sha256,export_provenance.digest(exports.stem_export_file(self.track_id,'vocals',job.id)))
            self.assertEqual(proof.subject,'stem')
            media=exports.stem_export_file(self.track_id,'vocals',job.id)
            media.write_bytes(b'tampered')
            self.assertEqual((await client.get(done.provenance_url)).status_code,409)

    async def test_stem_mp3_uses_exact_stem_and_survives_restart(self) -> None:
        source = self.make_source()
        job = await exports.create_stem_export(self.track_id, 'vocals', 'mp3')
        await asyncio.wait_for(exports._tasks[job.id], 15)
        done = exports.get_stem_export(self.track_id, 'vocals', job.id)
        self.assertEqual(done.status, 'done')
        self.assertEqual(done.stem_name, 'vocals')
        self.assertNotIn('version_id', done.model_dump())
        self.assertEqual(exports.load_document(self.track_id, job.id).source_relative, str(source.relative_to(db.FILES_DIR)))
        self.assertTrue(exports.stem_export_file(self.track_id, 'vocals', job.id).is_file())
        self.assertEqual(exports.list_exports(self.track_id, self.version_id).exports, [])
        await exports.recover_exports()
        self.assertEqual(exports.get_stem_export(self.track_id, 'vocals', job.id).status, 'done')

    async def test_missing_unknown_and_escaped_sources_are_rejected(self) -> None:
        for name in ('vocals', '../../private'):
            with self.assertRaises(exports.AudioExportError):
                await exports.create_stem_export(self.track_id, name, 'mp3')
        db.update_track_stems(self.track_id, {'vocals': str(self.root.parent / 'outside.wav')})
        with self.assertRaises(exports.AudioExportError):
            await exports.create_stem_export(self.track_id, 'vocals', 'mp3')

    async def test_delete_stems_drains_encoding_and_prevents_new_exports(self) -> None:
        self.make_source()
        entered = asyncio.Event()
        async def pending(document: exports.ExportDocument) -> None:
            entered.set()
            try:
                await asyncio.Future()
            finally:
                document.status = 'cancelled'
                exports.save_document(document)
        with patch.object(exports, '_run', side_effect=pending):
            job = await exports.create_stem_export(self.track_id, 'vocals', 'mp3')
            await entered.wait()
            async with exports.protect_stem_exports_removal(self.track_id):
                with self.assertRaises(exports.AudioExportError):
                    await exports.create_stem_export(self.track_id, 'vocals', 'mp3')
                self.assertTrue(exports._tasks.get(job.id) is None)
        self.assertEqual(exports.list_stem_exports(self.track_id, 'vocals').exports, [])
