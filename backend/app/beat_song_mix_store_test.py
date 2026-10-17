from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from app import beat_song_mix_store as store
from app.beat_song_mix import build_plan
from app.beat_song_mix_contracts import BeatSongMixError
from app.beat_song_mix_contracts_test import draft_fixture
from app.beat_song_mix_test import info_fixture


class BeatSongStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)
        self.connection.execute('CREATE TABLE tracks(id INTEGER PRIMARY KEY)')
        self.connection.execute('CREATE TABLE projects(id INTEGER PRIMARY KEY)')
        self.connection.execute('PRAGMA foreign_keys=ON')
        self.enterContext(patch.object(store, '_connection', return_value=self.connection))
        store.migrate(self.connection)
        self.plan = build_plan(draft_fixture(), info_fixture(), info_fixture(2), plan_id='c' * 32,
            revision=0, separator_ready=False, native_ready=False)

    def test_cas_immutable_history_and_failed_write_roll_back(self) -> None:
        store.save_plan(self.plan)
        from app.beat_song_mix import plan_digest
        updated = self.plan.model_copy(update={'revision': 1})
        updated = updated.model_copy(update={'sha256': plan_digest(updated)})
        store.save_plan(updated, expected_revision=0)
        self.assertEqual(store.get_plan(self.plan.id).revision, 1)
        self.assertEqual(store.get_plan(self.plan.id, 0), self.plan)
        with self.assertRaises(BeatSongMixError) as caught:
            store.save_plan(updated, expected_revision=0)
        self.assertEqual(caught.exception.code, 'beat_mix_revision_conflict')
        self.assertEqual(store.get_plan(self.plan.id).revision, 1)
        self.connection.execute("CREATE TRIGGER fail_revision BEFORE INSERT ON beat_song_mix_revisions BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            store.save_plan(updated.model_copy(update={'revision': 2}), expected_revision=1)
        self.assertEqual(store.get_plan(self.plan.id).revision, 1)

    def test_future_schema_is_refused_without_adding_tables(self) -> None:
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.execute('CREATE TABLE beat_song_mix_schema_version(id INTEGER PRIMARY KEY,version INTEGER)')
        connection.execute('INSERT INTO beat_song_mix_schema_version VALUES(1,99)')
        with self.assertRaises(BeatSongMixError):
            store.migrate(connection)
        names = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        self.assertEqual(names, [('beat_song_mix_schema_version',)])
