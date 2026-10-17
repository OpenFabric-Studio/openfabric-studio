"""Deleting or serving historical tracks must respect file ownership."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException

from app import audio_exports, db, midi, stems
from app.api import routes_tracks


class TrackStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        for name, value in [
            ("DATA_DIR", self.root / "library"),
            ("DB_PATH", self.root / "library" / "catalog.db"),
            ("FILES_DIR", self.root / "library" / "files"),
            ("_db", None),
        ]:
            self.enterContext(patch.object(db, name, value))
        db.get_db()

    async def asyncTearDown(self) -> None:
        if db._db is not None:
            db._db.close()
            db._db = None

    def track(self, audio: Path, abc: Path | None = None) -> int:
        return db.insert_track(
            model="upload", title="Fixture", lyrics="", seed=None,
            duration_ms=None, wall_ms=None, params={}, audio_path=audio, abc_path=abc,
        )

    def audio(self, name: str = "audio.wav") -> Path:
        path = db.model_dir("upload") / name
        path.write_bytes(b"owned audio fixture")
        return path

    async def test_generic_save_cannot_forge_server_recorded_music_evidence(self) -> None:
        application = FastAPI()
        application.include_router(routes_tracks.router)
        params = {'bpm': 99, '_music_evidence': {'lineage_complete': True, 'public_attributions': ['forged proof']},
            '_music_adapters': [{'id': 'a' * 32, 'sha256': 'b' * 64}]}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(application), base_url='http://127.0.0.1:9000') as client:
            response = await client.post('/api/tracks', data={'model': 'editor', 'params': json.dumps(params)},
                files={'audio': ('fixture.wav', b'owned fixture', 'audio/wav')})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['params'], {'bpm': 99})
        row = db.get_track(response.json()['id'])
        self.assertIsNotNone(row)
        self.assertNotIn('forged proof', row['params_json'] if row is not None else '')

    async def test_failed_conditioning_drain_preserves_track_and_audio(self) -> None:
        from app import music_conditioning
        from app.local_music_contracts import LocalMusicError
        audio = self.audio()
        identifier = self.track(audio)

        @asynccontextmanager
        async def blocked(track_id: int) -> AsyncIterator[None]:
            self.assertEqual(track_id, identifier)
            raise LocalMusicError('conditioning_source_busy', 409)
            yield

        with patch.object(music_conditioning, 'protect_source_removal', blocked):
            with self.assertRaises(HTTPException) as caught:
                await routes_tracks.delete_track(identifier)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail, 'conditioning_source_busy')
        self.assertIsNotNone(db.get_track(identifier))
        self.assertTrue(audio.is_file())

    async def test_delete_keeps_audio_used_by_another_historical_track(self) -> None:
        audio = self.audio()
        first, remaining = self.track(audio), self.track(audio)
        self.assertTrue(db.delete_track(first))
        self.assertIsNotNone(db.get_track(remaining))
        self.assertEqual(audio.read_bytes(), b"owned audio fixture")

    async def test_delete_keeps_abc_used_by_another_track(self) -> None:
        first_audio, second_audio = self.audio(), self.audio("other.wav")
        abc = first_audio.with_suffix(".abc")
        abc.write_text("X:1\nK:C\nC", encoding="utf-8")
        first, remaining = self.track(first_audio, abc), self.track(second_audio, abc)
        self.assertTrue(db.delete_track(first))
        self.assertIsNotNone(db.get_track(remaining))
        self.assertTrue(abc.is_file())
        self.assertFalse(first_audio.exists())

    async def test_delete_preserves_catalog_paths_outside_the_library(self) -> None:
        audio, abc = self.root / "private.wav", self.root / "private.abc"
        audio.write_bytes(b"private audio")
        abc.write_text("private score", encoding="utf-8")
        self.assertTrue(db.delete_track(self.track(audio, abc)))
        self.assertEqual(audio.read_bytes(), b"private audio")
        self.assertEqual(abc.read_text(), "private score")

    async def test_delete_removes_unshared_owned_files(self) -> None:
        audio = self.audio()
        abc = audio.with_suffix(".abc")
        abc.write_text("owned score", encoding="utf-8")
        self.assertTrue(db.delete_track(self.track(audio, abc)))
        self.assertFalse(audio.exists())
        self.assertFalse(abc.exists())

    async def test_delete_rejects_queued_and_running_stem_and_midi_jobs(self) -> None:
        self.enterContext(patch.dict(stems._jobs, {}, clear=True))
        self.enterContext(patch.dict(midi._jobs, {}, clear=True))
        for kind in ("stems", "midi"):
            for state in ("queued", "running"):
                with self.subTest(kind=kind, state=state):
                    audio = self.audio(f"{kind}-{state}.wav")
                    identifier = self.track(audio)
                    entered = asyncio.Event()
                    async def worker(track_id: int, source: str = "full") -> None:
                        job = stems._jobs[track_id] if kind == "stems" else midi._jobs[(track_id, source)]
                        job.status = "running" if state == "running" else "queued"
                        entered.set()
                        await asyncio.Event().wait()
                    with patch.object(stems, "_run", side_effect=worker), patch.object(midi, "_run", side_effect=worker):
                        if kind == "stems":
                            await stems.start(identifier)
                        else:
                            await midi.start(identifier, "full")
                        await entered.wait()
                        try:
                            with self.assertRaises(HTTPException) as caught:
                                await routes_tracks.delete_track(identifier)
                            self.assertEqual(caught.exception.status_code, 409)
                            self.assertEqual(caught.exception.detail, "job_busy")
                            self.assertIsNotNone(db.get_track(identifier))
                            self.assertTrue(audio.is_file())
                        finally:
                            if kind == "stems":
                                await stems.cancel(identifier)
                            else:
                                await midi.cancel(identifier, "full")

    async def test_delete_rechecks_midi_started_while_exports_drain(self) -> None:
        audio = self.audio()
        identifier = self.track(audio)
        entered, release = asyncio.Event(), asyncio.Event()
        original = audio_exports.protect_track_exports_removal
        @asynccontextmanager
        async def held_guard(track_id: int) -> AsyncIterator[None]:
            entered.set()
            await release.wait()
            async with original(track_id):
                yield
        async def worker(track_id: int, source: str) -> None:
            await asyncio.Event().wait()
        with patch.dict(midi._jobs, {}, clear=True), patch.object(midi, "_run", side_effect=worker), \
             patch.object(audio_exports, "protect_track_exports_removal", side_effect=held_guard):
            deletion = asyncio.create_task(routes_tracks.delete_track(identifier))
            await entered.wait()
            try:
                await midi.start(identifier, 'full')
                release.set()
                with self.assertRaises(HTTPException) as caught:
                    await deletion
                self.assertEqual(caught.exception.status_code, 409)
                self.assertIsNotNone(db.get_track(identifier))
                self.assertTrue(audio.is_file())
            finally:
                release.set()
                await asyncio.gather(deletion, return_exceptions=True)
                await midi.cancel(identifier, 'full')

    async def test_delete_preserves_shared_stem_and_midi_artifacts(self) -> None:
        for kind in ("stems", "midi"):
            with self.subTest(kind=kind):
                owner = self.track(self.audio(f"owner-{kind}.wav"))
                folder = db.stems_dir("upload", owner) if kind == "stems" else db.midi_dir("upload", owner)
                folder.mkdir(parents=True)
                shared = folder / ("vocals.wav" if kind == "stems" else "full.mid")
                shared.write_bytes(b"shared artifact")
                private = folder / "unshared.tmp"
                private.write_bytes(b"private artifact")
                if kind == "stems":
                    db.update_track_stems(owner, {"vocals": str(shared)})
                else:
                    db.set_track_midi_entry(owner, "full", shared)
                remaining = self.track(shared, shared)
                self.assertTrue(db.delete_track(owner))
                self.assertIsNotNone(db.get_track(remaining))
                self.assertEqual(shared.read_bytes(), b"shared artifact")
                self.assertFalse(private.exists())

    async def test_delete_preserves_other_track_stem_catalog_reference(self) -> None:
        shared = self.audio()
        owner = self.track(shared)
        remaining = self.track(self.audio("remaining.wav"))
        db.update_track_stems(remaining, {"vocals": str(shared)})
        self.assertTrue(db.delete_track(owner))
        self.assertEqual(shared.read_bytes(), b"owned audio fixture")

    async def test_reset_stems_preserves_shared_files_and_clears_only_owners_catalog(self) -> None:
        owner = self.track(self.audio())
        folder = db.stems_dir("upload", owner)
        folder.mkdir(parents=True)
        shared = folder / "vocals.wav"
        shared.write_bytes(b"shared stem")
        db.update_track_stems(owner, {"vocals": str(shared)})
        remaining = self.track(self.audio("remaining.wav"))
        db.update_track_stems(remaining, {"vocals": str(shared)})
        self.assertTrue(db.delete_track_stems(owner))
        self.assertIsNone(db.get_track(owner)["stems_json"])
        self.assertIsNotNone(db.get_track(remaining)["stems_json"])
        self.assertEqual(shared.read_bytes(), b"shared stem")

    async def test_reset_midi_keeps_own_primary_audio_reference(self) -> None:
        audio = self.audio()
        owner = self.track(audio)
        folder = db.midi_dir("upload", owner)
        folder.mkdir(parents=True)
        shared = folder / "full.mid"
        shared.write_bytes(b"shared MIDI")
        db.get_db().execute("UPDATE tracks SET abc_path=? WHERE id=?", (str(shared), owner))
        db.set_track_midi_entry(owner, "full", shared)
        self.assertTrue(db.delete_track_midi(owner))
        self.assertIsNone(db.get_track(owner)["midi_json"])
        self.assertEqual(shared.read_bytes(), b"shared MIDI")

    async def test_delete_keeps_another_tracks_version_source(self) -> None:
        shared = self.audio()
        owner = self.track(shared)
        remaining = self.track(self.audio("remaining.wav"))
        db.get_db().execute(
            "INSERT INTO audio_versions(id,track_id,kind,status,created_at,audio_path) VALUES(?,?,?,?,?,?)",
            ("a" * 32, remaining, "original", "done", "2026-10-05T00:00:00Z", str(shared)),
        )
        db.get_db().commit()
        self.assertTrue(db.delete_track(owner))
        self.assertEqual(shared.read_bytes(), b"owned audio fixture")

    async def test_legacy_serving_refuses_external_and_escaped_symlink_files(self) -> None:
        private = self.root / "private.wav"
        private.write_bytes(b"private external bytes")
        link = db.model_dir("upload") / "escaped.wav"
        link.symlink_to(private)
        app = FastAPI()
        app.include_router(routes_tracks.router)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for path in [private, link]:
                identifier = self.track(path, path)
                for endpoint in ["audio", "abc"]:
                    response = await client.get(f"/api/tracks/{identifier}/{endpoint}")
                    self.assertEqual(response.status_code, 404)
                    self.assertNotIn(b"private external bytes", response.content)

    async def test_legacy_audio_serves_contained_owned_file(self) -> None:
        audio = self.audio()
        identifier = self.track(audio)
        app = FastAPI()
        app.include_router(routes_tracks.router)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/tracks/{identifier}/audio")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, audio.read_bytes())
