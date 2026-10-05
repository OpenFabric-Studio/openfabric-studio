"""Deleting or serving historical tracks must respect file ownership."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app import db
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
