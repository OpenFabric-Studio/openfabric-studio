"""Durable generation snapshots and independently managed preset copies."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from pydantic import TypeAdapter

from app import db
from app.contracts import JsonObject


class GenerationLibraryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.stack = ExitStack()
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, value in (("_db", None), ("DATA_DIR", self.root),
                            ("DB_PATH", self.root / "catalog.db"), ("FILES_DIR", self.root / "files")):
            self.stack.enter_context(patch.object(db, name, value))
        self.clients: list[httpx.AsyncClient] = []

    async def asyncTearDown(self) -> None:
        for client in self.clients:
            await client.aclose()
        if db._db is not None:
            db._db.close()
            db._db = None
        self.stack.close()

    def insert(self, model: str = "ace_step", title: str = "Song", params: dict[str, object] | None = None) -> int:
        return db.insert_track(model=model, title=title, lyrics="These lyrics", seed=42,
                               duration_ms=2000, wall_ms=100, params=TypeAdapter(JsonObject).validate_python(params or {}),
                               audio_path=self.root / "song.wav", abc_path=None)

    def client(self) -> httpx.AsyncClient:
        from app.api.routes_generation_library import router
        app = FastAPI()
        app.include_router(router)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.clients.append(client)
        return client

    async def test_presets_are_paginated_without_losing_management_access(self) -> None:
        from app import generation_library as library
        from app.generation_contracts import CreateGenerationPresetRequest, YueGenerationSettings
        connection = db.get_db()
        for index in range(105):
            library.create_preset(connection, CreateGenerationPresetRequest(name=f'Preset {index:03}', settings=YueGenerationSettings(engine='yue2', style='jazz')))
        client = self.client()
        first = (await client.get('/api/generation/presets')).json()
        second = (await client.get('/api/generation/presets?offset=100')).json()
        self.assertEqual((len(first['data']), len(second['data']), first['total'], second['total']), (100, 5, 105, 105))
        self.assertEqual(len({row['id'] for row in first['data'] + second['data']}), 105)
        self.assertEqual((await client.get('/api/generation/presets?limit=1001')).status_code, 422)

    async def test_tracks_capture_history_but_imports_and_editor_outputs_do_not(self) -> None:
        generated = self.insert(params={"prompt": "Jazz", "audio_duration": 60})
        self.insert("yue2", "YuE", {"style": "Folk", "cot": "melody"})
        self.insert("upload")
        self.insert("editor")
        connection = db.get_db()
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("generation_history", tables, "Generated-track history must persist in SQLite")
        client = self.client()
        response = await client.get("/api/generation/history")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["retention_limit"], 100)
        self.assertEqual({row["track_id"] for row in body["data"]}, {generated, generated + 1})
        ace = next(row for row in body["data"] if row["engine"] == "ace_step")
        self.assertEqual(ace["settings"]["customPrompt"], "Jazz")
        self.assertEqual(ace["lyrics"], "These lyrics")
        self.assertEqual(ace["settings"]["seed"], 42)

    async def test_history_and_presets_survive_track_deletion_retention_and_restart(self) -> None:
        client = self.client()
        self.root.joinpath("song.wav").write_bytes(b"audio")
        track_id = self.insert()
        entry = (await client.get("/api/generation/history")).json()["data"][0]
        saved = await client.post("/api/generation/presets", json={
            "name": "Permanent", "settings": entry["settings"], "source_history_id": entry["id"],
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        connection = db.get_db()
        connection.execute("DELETE FROM tracks WHERE id=?", (track_id,))
        connection.commit()
        orphan = (await client.get("/api/generation/history")).json()["data"][0]
        self.assertIsNone(orphan["track_id"])
        settings = (await client.get("/api/generation/settings")).json()
        limited = await client.put("/api/generation/settings", json={"history_limit": 1, "revision": settings["revision"]})
        self.assertEqual(limited.status_code, 200, limited.text)
        self.insert(title="Newer")
        self.insert(title="Newest")
        self.assertEqual((await client.get("/api/generation/history")).json()["total"], 1)
        db._db.close()
        db._db = None
        presets = (await client.get("/api/generation/presets")).json()["data"]
        self.assertEqual(presets[0]["name"], "Permanent")
        self.assertEqual(presets[0]["settings"], entry["settings"])
        self.assertTrue(self.root.joinpath("song.wav").exists())
        history_id = (await client.get("/api/generation/history")).json()["data"][0]["id"]
        removed = await client.delete(f"/api/generation/history/{history_id}")
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual((await client.get("/api/generation/history")).json()["total"], 0)
        self.assertEqual(len((await client.get("/api/generation/presets")).json()["data"]), 1)
        self.assertEqual(len(db.list_tracks()), 2)

    async def test_history_search_and_engine_filter_and_private_params_redaction(self) -> None:
        self.insert(title="Hidden file", params={"prompt": "Bright jazz", "ctx_audio": "/secret/private.wav",
                                                "lora_path": "/secret/adapter", "api_key": "SECRET"})
        self.insert("yue2", "Other", {"style": "Quiet folk", "abc": "ABC melody"})
        client = self.client()
        response = await client.get("/api/generation/history?engine=ace_step&search=jazz")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 1)
        entry = response.json()["data"][0]
        self.assertTrue(entry["reference_requires_reupload"])
        self.assertTrue(entry["settings"]["loraRequiresReselection"])
        self.assertNotIn("/secret", response.text)
        self.assertNotIn("SECRET", response.text)
        self.assertEqual((await client.get("/api/generation/history?search=These%20lyrics")).json()["total"], 2)
        self.assertEqual((await client.get("/api/generation/history?search=%25")).json()["total"], 0)

    async def test_presets_have_no_silent_overwrites_and_revision_protected_management(self) -> None:
        client = self.client()
        body = {"name": "  My Favorite  ", "settings": {"engine": "ace_step", "customPrompt": "First"}}
        created = await client.post("/api/generation/presets", json=body)
        self.assertEqual(created.status_code, 200, created.text)
        preset = created.json()
        self.assertEqual(preset["name"], "My Favorite")
        duplicate_name = await client.post("/api/generation/presets", json={**body, "name": "my favorite"})
        self.assertEqual(duplicate_name.status_code, 409, duplicate_name.text)
        preset_id = preset["id"]
        edited = await client.put(f"/api/generation/presets/{preset_id}", json={
            "name": "Renamed", "settings": {"engine": "ace_step", "customPrompt": "Edited"}, "revision": 1,
        })
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(edited.json()["revision"], 2)
        stale = await client.put(f"/api/generation/presets/{preset_id}", json={
            "name": "Lost update", "settings": body["settings"], "revision": 1,
        })
        self.assertEqual(stale.status_code, 409, stale.text)
        stale_delete = await client.delete(f"/api/generation/presets/{preset_id}?revision=1")
        self.assertEqual(stale_delete.status_code, 409, stale_delete.text)
        copied = await client.post(f"/api/generation/presets/{preset_id}/duplicate", json={"name": "Copy", "revision": 2})
        self.assertEqual(copied.status_code, 200, copied.text)
        self.assertEqual(copied.json()["settings"], edited.json()["settings"])
        self.assertNotEqual(copied.json()["id"], preset_id)
        deleted = await client.delete(f"/api/generation/presets/{preset_id}?revision=2")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual((await client.get("/api/generation/presets")).json()["data"][0]["name"], "Copy")

    async def test_import_is_idempotent_and_preserves_conflicting_legacy_names(self) -> None:
        client = self.client()
        await client.post("/api/generation/presets", json={"name": "Favorite", "settings": {"engine": "ace_step"}})
        request = {"presets": [{"legacy_key": "acestep:1", "name": "Favorite",
                                "settings": {"engine": "ace_step", "customPrompt": "Legacy", "inferenceSteps": None}},
                               {"legacy_key": "yue2:1", "name": "YuE", "settings": {"engine": "yue2"}}]}
        first = await client.post("/api/generation/presets/import", json=request)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["imported"], 2)
        self.assertNotEqual(first.json()["data"][0]["name"], "Favorite")
        self.assertIsNone(first.json()["data"][0]["settings"]["inferenceSteps"])
        second = await client.post("/api/generation/presets/import", json=request)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["imported"], 0)
        self.assertEqual([row["id"] for row in second.json()["data"]], [row["id"] for row in first.json()["data"]])
        self.assertEqual(len((await client.get("/api/generation/presets")).json()["data"]), 3)

    async def test_settings_and_untrusted_snapshots_are_bounded_and_strict(self) -> None:
        client = self.client()
        for limit in (0, 10001, 1.5, True, "100"):
            with self.subTest(limit=limit):
                response = await client.put("/api/generation/settings", json={"history_limit": limit, "revision": 1})
                self.assertEqual(response.status_code, 422, response.text)
        updated = await client.put("/api/generation/settings", json={"history_limit": 2, "revision": 1})
        self.assertEqual(updated.status_code, 200, updated.text)
        stale = await client.put("/api/generation/settings", json={"history_limit": 3, "revision": 1})
        self.assertEqual(stale.status_code, 409, stale.text)
        for settings in ({"engine": "other"}, {"engine": "ace_step", "batchSize": 9},
                         {"engine": "ace_step", "duration": float("inf")},
                         {"engine": "yue2", "semantic": {"top_p": 1.1}},
                         {"engine": "ace_step", "ctx_audio": "/secret"},
                         {"engine": "ace_step", "instrumental": "true"}):
            with self.subTest(settings=settings):
                response = await client.post("/api/generation/presets", content=json.dumps({"name": "Bad", "settings": settings}),
                                             headers={"Content-Type": "application/json"})
                self.assertEqual(response.status_code, 422, response.text)
        invalid_filter = await client.get("/api/generation/history?engine=upload")
        self.assertEqual(invalid_filter.status_code, 422, invalid_filter.text)

    async def test_yue_presets_reject_native_invalid_sampling_and_unsafe_work_limits(self) -> None:
        client = self.client()
        invalid_settings = ({"engine": "yue2", "seed": -1}, {"engine": "yue2", "cfgScale": 21},
                         {"engine": "yue2", "numInferenceSteps": 257},
                         {"engine": "yue2", "semantic": {"temperature": 6}},
                         {"engine": "yue2", "semantic": {"top_p": 0}},
                         {"engine": "yue2", "semantic": {"top_k": 0}},
                         {"engine": "yue2", "semantic": {"repetition_penalty": 0}},
                         {"engine": "yue2", "semantic": {"penalty_window": 0}},
                         {"engine": "yue2", "semantic": {"max_tokens": 9001}},
                         {"engine": "yue2", "abcSampling": {"max_tokens": 4097}},
                         {"engine": "yue2", "semantic": {"min_tokens": 20, "max_tokens": 10}},
                         {"engine": "yue2", "semantic": {"max_tokens": 0}},
                         {"engine": "yue2", "semantic": {"top_k": 100001}},
                         {"engine": "yue2", "semantic": {"penalty_window": 9001}},
                         {"engine": "yue2", "abcSampling": {"penalty_window": 4097}})
        for index, settings in enumerate(invalid_settings):
            with self.subTest(settings=settings):
                response = await client.post("/api/generation/presets", json={"name": f"Invalid {index}", "settings": settings})
                self.assertEqual(response.status_code, 422, response.text)

    async def test_failed_history_capture_rolls_back_track_and_short_number_together(self) -> None:
        connection = db.get_db()
        connection.execute("""CREATE TRIGGER fail_history BEFORE INSERT ON generation_history
            BEGIN SELECT RAISE(ABORT,'test_history_failure'); END""")
        connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert()
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM tracks").fetchone()[0], 0)
        self.assertEqual(connection.execute("SELECT last_short_id FROM track_code").fetchone()[0], 0)
        connection.execute("DROP TRIGGER fail_history")
        connection.commit()
        track = db.get_track(self.insert())
        self.assertEqual(track["short_id"], 1)

    async def test_yue_job_and_completed_track_are_attached_in_one_write_transaction(self) -> None:
        connection = db.get_db()
        connection.execute("INSERT INTO yue_jobs(id,payload_json,created_at) VALUES(?,?,?)", ('a' * 32, '{}', 'now'))
        connection.commit()
        track_id = db.insert_track(model='yue2', title='YuE', lyrics='Lyrics', seed=1,
                                   duration_ms=None, wall_ms=None, params={}, audio_path=self.root / 'yue.wav',
                                   abc_path=None, yue_job_id='a' * 32)
        self.assertEqual(connection.execute("SELECT track_id FROM yue_jobs WHERE id=?", ('a' * 32,)).fetchone()[0], track_id)
        with self.assertRaises(ValueError):
            db.insert_track(model='yue2', title='Duplicate', lyrics='Lyrics', seed=2,
                            duration_ms=None, wall_ms=None, params={}, audio_path=self.root / 'other.wav',
                            abc_path=None, yue_job_id='a' * 32)
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM tracks').fetchone()[0], 1)
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM generation_history').fetchone()[0], 1)

    async def test_invalid_legacy_fields_do_not_prevent_audio_catalog_capture(self) -> None:
        self.insert("yue2", params={"cot": "unsupported", "semantic_top_p": 3,
                                    "cfg_scale": -1, "num_inference_steps": 20000})
        client = self.client()
        settings = (await client.get("/api/generation/history")).json()["data"][0]["settings"]
        self.assertEqual(settings["cot"], "off")
        self.assertIsNone(settings["semantic"]["top_p"])
        self.assertIsNone(settings["cfgScale"])
        self.assertIsNone(settings["numInferenceSteps"])

    async def test_form_snapshot_preserves_voice_reference_and_unset_sampler_options(self) -> None:
        self.insert(params={"prompt": "Native prompt", "_generation_settings": {
            "engine": "ace_step", "customPrompt": "Form prompt", "voiceId": 'a' * 32,
            "useRefAudio": True, "styleReferenceRequiresReupload": True,
            "sourceReferenceName": "source.wav", "styleReferenceName": "style.flac",
            "loraName": "Adapter", "loraRequiresReselection": True, "loraScale": 0.8,
            "inferenceSteps": None, "useCotCaption": False,
        }})
        entry = (await self.client().get('/api/generation/history')).json()['data'][0]
        settings = entry['settings']
        self.assertEqual(settings['customPrompt'], 'Form prompt')
        self.assertEqual(settings['voiceId'], 'a' * 32)
        self.assertEqual(settings['sourceReferenceName'], 'source.wav')
        self.assertEqual(settings['styleReferenceName'], 'style.flac')
        self.assertIsNone(settings['inferenceSteps'])
        self.assertFalse(settings['useCotCaption'])
        self.assertEqual(settings['loraScale'], 0.8)
        self.assertTrue(entry['reference_requires_reupload'])

    async def test_voice_replacement_worker_does_not_become_a_generated_history_entry(self) -> None:
        source = self.insert('upload')
        connection = db.get_db()
        connection.execute("""INSERT INTO audio_versions(id,track_id,kind,voice_id,status,created_at)
            VALUES(?,?,'voice',?,'queued','now')""", ('b' * 32, source, 'c' * 32))
        connection.commit()
        db.insert_track(model='ace_step', title='Voice replacement', lyrics='', seed=None,
                        duration_ms=None, wall_ms=None, params={}, audio_path=self.root / 'voice.wav',
                        abc_path=None, audio_version_id='b' * 32)
        self.assertEqual((await self.client().get('/api/generation/history')).json()['total'], 0)

    async def test_interrupted_schema_migration_rolls_back_and_retries_on_next_open(self) -> None:
        from app import generation_library as library
        original = library.capture_track
        legacy = sqlite3.connect(db.DB_PATH)
        legacy.execute("""CREATE TABLE tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,model TEXT NOT NULL,created_at TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',lyrics TEXT NOT NULL DEFAULT '',seed INTEGER,duration_ms REAL,
            wall_ms REAL,params_json TEXT NOT NULL DEFAULT '{}',audio_path TEXT NOT NULL,abc_path TEXT)""")
        legacy.execute("INSERT INTO tracks(model,created_at,title,audio_path) VALUES('yue2','now','Keep','/old/song.wav')")
        legacy.commit()
        legacy.close()
        with patch.object(library, 'capture_track', side_effect=OSError('injected migration interruption')):
            with self.assertRaises(OSError):
                db.get_db()
        with patch.object(library, 'capture_track', wraps=original):
            restored = db.get_db()
        tables = {row[0] for row in restored.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn('generation_history', tables, 'An interrupted migration must retry on next open')
        self.assertEqual(restored.execute('SELECT COUNT(*) FROM generation_history').fetchone()[0], 1)
        self.assertEqual(restored.execute('SELECT title FROM tracks').fetchone()[0], 'Keep')

    async def test_concurrent_preset_updates_have_one_winner_and_one_revision_conflict(self) -> None:
        from app import generation_library as library
        from app.generation_contracts import UpdateGenerationPresetRequest
        client = self.client()
        response = await client.post("/api/generation/presets", json={"name": "Original", "settings": {"engine": "ace_step"}})
        self.assertEqual(response.status_code, 200, response.text)
        preset_id = response.json()["id"]

        def update(name: str) -> str:
            connection = sqlite3.connect(db.DB_PATH, timeout=5)
            connection.row_factory = sqlite3.Row
            try:
                request = UpdateGenerationPresetRequest.model_validate({"name": name, "settings": {"engine": "ace_step"}, "revision": 1})
                library.update_preset(connection, preset_id, request)
                return "updated"
            except library.GenerationLibraryError as exc:
                return exc.code
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(update, ("First", "Second")))
        self.assertCountEqual(outcomes, ["updated", "generation_revision_conflict"])
        stored = (await client.get("/api/generation/presets")).json()["data"][0]
        self.assertEqual(stored["revision"], 2)

    async def test_import_failure_rolls_back_entire_batch_and_deleted_presets_do_not_reappear(self) -> None:
        client = self.client()
        request = {"presets": [{"legacy_key": "same", "name": "One", "settings": {"engine": "ace_step"}},
                               {"legacy_key": "same", "name": "Different", "settings": {"engine": "ace_step"}}]}
        conflict = await client.post("/api/generation/presets/import", json=request)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual((await client.get("/api/generation/presets")).json()["data"], [])
        request["presets"] = request["presets"][:1]
        imported = await client.post("/api/generation/presets/import", json=request)
        self.assertEqual(imported.status_code, 200, imported.text)
        preset_id = imported.json()["data"][0]["id"]
        deleted = await client.delete(f"/api/generation/presets/{preset_id}?revision=1")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        repeated = await client.post("/api/generation/presets/import", json=request)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json(), {"data": [], "imported": 0})

    async def test_legacy_catalog_seed_is_once_and_preserves_original_rows(self) -> None:
        connection = sqlite3.connect(db.DB_PATH)
        connection.execute("""CREATE TABLE tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,model TEXT NOT NULL,created_at TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',lyrics TEXT NOT NULL DEFAULT '',seed INTEGER,duration_ms REAL,
            wall_ms REAL,params_json TEXT NOT NULL DEFAULT '{}',audio_path TEXT NOT NULL,abc_path TEXT)""")
        for index in range(105):
            connection.execute("INSERT INTO tracks(model,created_at,title,params_json,audio_path) VALUES(?,?,?,?,?)",
                               ("yue2", "2025-01-01T00:00:00Z", f"Legacy {index}", '{"style":"Folk"}', "/old/song.wav"))
        connection.commit()
        connection.close()
        client = self.client()
        history = (await client.get("/api/generation/history")).json()
        self.assertEqual(history["total"], 100)
        self.assertEqual(history["data"][0]["title"], "Legacy 104")
        self.assertEqual(len(db.list_tracks()), 105)
        await client.delete(f'/api/generation/history/{history["data"][0]["id"]}')
        db._db.close()
        db._db = None
        self.assertEqual((await client.get("/api/generation/history")).json()["total"], 99)
        row = db.get_db().execute("SELECT * FROM tracks WHERE id=1").fetchone()
        self.assertEqual(row["params_json"], '{"style":"Folk"}')
        self.assertEqual(row["audio_path"], "/old/song.wav")
