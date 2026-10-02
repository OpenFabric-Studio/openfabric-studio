"""Reloaded feeds keep tracks with active backend version/export jobs mounted."""
from __future__ import annotations

import asyncio
import tempfile
import wave
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app import audio_exports, audio_version_store as store, db, track_activity, voice_build
from app.audio_encoding import AudioEncodingSettings
from app.audio_version_contracts import AudioVersion


class TrackActivityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.object(db, "FILES_DIR", self.root / "files"))
        self.enterContext(patch.object(db, "DB_PATH", self.root / "catalog.db"))
        self.enterContext(patch.object(db, "_db", None))
        self.enterContext(patch.object(audio_exports, "_running", {}))
        self.enterContext(patch.object(audio_exports, "_tasks", {}))
        self.enterContext(patch.object(audio_exports, "_lock", asyncio.Lock()))
        self.enterContext(patch.object(audio_exports, "_capacity", asyncio.Semaphore(2)))
        self.enterContext(patch.object(voice_build, "VOICES_DIR", self.root / "voices"))
        self.enterContext(patch.object(voice_build, "_applies", {}))
        self.track_id = db.insert_track(model="upload", title="Track", lyrics="", seed=None,
            duration_ms=None, wall_ms=None, params={}, audio_path=self.root / 'source.wav', abc_path=None)
        version = AudioVersion(id='a' * 32, track_id=self.track_id, kind='voice', voice_id='b' * 32,
            created_at='2026-10-01', status='running')
        store.insert(db.get_db(), store.StoredAudioVersion(version, None, self.track_id))

    async def asyncTearDown(self) -> None:
        if db._db is not None:
            db._db.close()

    async def test_reconciled_voice_completion_and_active_export_are_reported(self) -> None:
        # No live apply or receipt owns this persisted running row: reconcile
        # must freeze it as interrupted, rather than keep it pinned forever.
        self.assertEqual(track_activity.get_activity().track_ids, [])
        self.assertEqual(store.get(db.get_db(), 'a' * 32).public.status, 'failed')
        export = audio_exports.ExportDocument(id='c' * 32, track_id=self.track_id, version_id='a' * 32,
            format='mp3', status='running', created_at='2026-10-01', settings=AudioEncodingSettings(),
            source_relative='source.wav', source_sha256='d' * 64)
        audio_exports._running[export.id] = export
        self.assertEqual(track_activity.get_activity().track_ids, [self.track_id])
        export.status = 'done'
        self.assertEqual(track_activity.get_activity().track_ids, [])
        from app.api.routes_tracks import router
        app = FastAPI()
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get('/api/tracks/activity')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'track_ids': []})

    def test_owned_voice_job_remains_active_until_its_receipt_completes(self) -> None:
        job = voice_build.ApplyJob(voice_id='b' * 32, track_id=self.track_id, status='running', audio_version_id='a' * 32)
        voice_build._applies[self.track_id] = job
        self.assertEqual(track_activity.get_activity().track_ids, [self.track_id])
        receipt = voice_build.voices_root() / '_apply' / f'{self.track_id}.json'
        receipt.parent.mkdir()
        receipt.write_text('{"status":"done","audio_version_id":"' + 'a' * 32 + '"}')
        voice_build._applies.pop(self.track_id)
        self.assertEqual(track_activity.get_activity().track_ids, [])
        self.assertEqual(store.get(db.get_db(), 'a' * 32).public.status, 'done')

    async def test_real_export_is_active_until_cpu_encoder_finishes(self) -> None:
        source = db.FILES_DIR / 'source.wav'
        source.parent.mkdir()
        with wave.open(str(source), 'wb') as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(48000)
            writer.writeframes(b'\x11\x01' * 4800)
        db.get_db().execute("UPDATE audio_versions SET audio_path=?,status='done' WHERE id=?", (str(source), 'a' * 32))
        db.get_db().commit()
        exported = await audio_exports.create_export(self.track_id, 'a' * 32, 'flac')
        self.assertEqual(track_activity.get_activity().track_ids, [self.track_id])
        await audio_exports._tasks[exported.id]
        self.assertEqual(audio_exports.get_export(self.track_id, 'a' * 32, exported.id).status, 'done')
        self.assertEqual(track_activity.get_activity().track_ids, [])
