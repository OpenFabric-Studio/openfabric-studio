"""Favorites survive catalog migration, reload, and typed HTTP mutations."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app import db
from app.api.routes_tracks import router


class TrackFavoritesStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, value in (
            ("_db", None),
            ("DATA_DIR", root),
            ("DB_PATH", root / "catalog.db"),
            ("FILES_DIR", root / "files"),
        ):
            self.stack.enter_context(patch.object(db, name, value))
        self.addCleanup(self.close_db)

    def close_db(self) -> None:
        if db._db is not None:
            db._db.close()
            db._db = None

    def insert_track(self, title: str = "Song") -> int:
        return db.insert_track(
            model="upload", title=title, lyrics="original lyrics", seed=42,
            duration_ms=2000, wall_ms=100, params={"source": "test"},
            audio_path=db.FILES_DIR / "song.wav", abc_path=None,
        )

    def test_migration_preserves_existing_track_data_and_ids(self) -> None:
        legacy = sqlite3.connect(db.DB_PATH)
        legacy.execute("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT NOT NULL,
                created_at TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
                lyrics TEXT NOT NULL DEFAULT '', seed INTEGER, duration_ms REAL,
                wall_ms REAL, params_json TEXT NOT NULL DEFAULT '{}',
                audio_path TEXT NOT NULL, abc_path TEXT, short_id INTEGER
            )
        """)
        legacy.execute(
            "INSERT INTO tracks (id, short_id, model, created_at, title, lyrics, params_json, audio_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (73, 18, "upload", "2025-01-01T00:00:00Z", "Keep me", "Lyrics", '{"saved":true}', "/old/song.wav"),
        )
        legacy.commit()
        legacy.close()

        connection = db.get_db()
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(tracks)")}
        self.assertIn("is_favorite", columns)
        row = connection.execute("SELECT * FROM tracks WHERE id = 73").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(
            (row["id"], row["short_id"], row["title"], row["lyrics"], row["params_json"], row["audio_path"], row["is_favorite"]),
            (73, 18, "Keep me", "Lyrics", '{"saved":true}', "/old/song.wav", 0),
        )
        self.assertGreater(self.insert_track(), 73)
        self.close_db()
        self.assertEqual(db.get_db().execute("SELECT COUNT(*) FROM tracks").fetchone()[0], 2)

    def test_new_tracks_default_to_unfavorited_and_database_rejects_invalid_flags(self) -> None:
        track_id = self.insert_track()
        row = db.get_track(track_id)
        self.assertIsNotNone(row)
        self.assertIn("is_favorite", row.keys())
        self.assertEqual(row["is_favorite"], 0)
        for flag in (2, -1, None, "yes"):
            with self.subTest(flag=flag), self.assertRaises(sqlite3.IntegrityError):
                db.get_db().execute("UPDATE tracks SET is_favorite = ? WHERE id = ?", (flag, track_id))
            db.get_db().rollback()
        self.assertEqual(db.get_track(track_id)["is_favorite"], 0)

    def test_toggling_is_idempotent_and_persists_after_reopening(self) -> None:
        track_id = self.insert_track()
        self.assertTrue(db.set_track_favorite(track_id, True))
        self.assertTrue(db.set_track_favorite(track_id, True))
        self.close_db()
        row = db.get_track(track_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["is_favorite"], 1)
        self.assertTrue(db.set_track_favorite(track_id, False))
        self.close_db()
        self.assertEqual(db.get_track(track_id)["is_favorite"], 0)
        self.assertFalse(db.set_track_favorite(track_id + 1, True))


class TrackFavoritesApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.stack = ExitStack()
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, value in (
            ("_db", None), ("DATA_DIR", root), ("DB_PATH", root / "catalog.db"),
            ("FILES_DIR", root / "files"),
        ):
            self.stack.enter_context(patch.object(db, name, value))
        self.connection = db.get_db()
        app = FastAPI()
        app.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.connection.close()
        self.stack.close()

    async def upload(self) -> int:
        response = await self.client.post(
            "/api/tracks/upload", files={"audio": ("song.wav", b"RIFFtest", "audio/wav")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(response.json().get("is_favorite"), False)
        return response.json()["id"]

    async def test_favorite_responses_and_list_refresh_contain_boolean_state(self) -> None:
        track_id = await self.upload()
        saved = await self.client.put(f"/api/tracks/{track_id}/favorite", json={"is_favorite": True})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertIs(saved.json()["is_favorite"], True)
        self.assertEqual(saved.json()["id"], track_id)
        tracks = await self.client.get("/api/tracks?model=upload")
        self.assertIs(tracks.json()["data"][0]["is_favorite"], True)
        renamed = await self.client.put(f"/api/tracks/{track_id}", json={"title": "Renamed"})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertIs(renamed.json()["is_favorite"], True)
        cleared = await self.client.put(f"/api/tracks/{track_id}/favorite", json={"is_favorite": False})
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertIs(cleared.json()["is_favorite"], False)
        tracks = await self.client.get("/api/tracks")
        self.assertIs(tracks.json()["data"][0]["is_favorite"], False)

    async def test_missing_track_returns_404(self) -> None:
        response = await self.client.put("/api/tracks/999/favorite", json={"is_favorite": True})
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json(), {"detail": "track not found"})

    async def test_non_boolean_and_missing_favorite_values_are_rejected_before_update(self) -> None:
        track_id = await self.upload()
        for body in ({}, {"is_favorite": 1}, {"is_favorite": 0}, {"is_favorite": "true"}, {"is_favorite": None}):
            with self.subTest(body=body):
                response = await self.client.put(f"/api/tracks/{track_id}/favorite", json=body)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(db.get_track(track_id)["is_favorite"], 0)

    async def test_invalid_track_ids_are_rejected_before_database_access(self) -> None:
        for track_id in ("0", "-1", "1.5", "9007199254740992", "9" * 100):
            with self.subTest(track_id=track_id), patch.object(db, "set_track_favorite") as update:
                response = await self.client.put(f"/api/tracks/{track_id}/favorite", json={"is_favorite": True})
                self.assertEqual(response.status_code, 422, response.text)
                update.assert_not_called()
