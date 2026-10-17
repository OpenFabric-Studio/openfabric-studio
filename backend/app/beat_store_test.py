"""Beat revisions use isolated SQLite transactions and retain old snapshots."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from app import beat_store, db
from app.beat_contracts import BeatError, BeatHumanizeRequest, BeatVariationRequest
from app.beat_contracts_test import document_fixture


class BeatStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(db, '_db', None))
        self.enterContext(patch.object(db, 'DATA_DIR', self.root))
        self.enterContext(patch.object(db, 'FILES_DIR', self.root / 'files'))
        self.enterContext(patch.object(db, 'DB_PATH', self.root / 'catalog.db'))
        self.connection = db.get_db()
        beat_store.migrate(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def test_server_identity_revision_cas_and_old_snapshot_survive_updates(self) -> None:
        original = document_fixture().model_copy(update={'revision': 700})
        created = beat_store.create(original)
        self.assertNotEqual(created.id, original.id)
        self.assertEqual(created.revision, 0)
        changed = beat_store.update(created.id, 0, created.model_copy(update={'title': 'Edited', 'id': 'f' * 32, 'revision': 900}))
        self.assertEqual((changed.id, changed.revision, changed.title), (created.id, 1, 'Edited'))
        self.assertEqual(beat_store.get_revision(created.id, 0).title, original.title)
        with self.assertRaises(BeatError) as stale:
            beat_store.update(created.id, 0, created)
        self.assertEqual(stale.exception.code, 'beat_revision_conflict')
        self.assertEqual(beat_store.get(created.id), changed)

    def test_future_schema_refuses_without_mutation_or_committing_callers_transaction(self) -> None:
        connection = sqlite3.connect(':memory:')
        try:
            connection.execute('CREATE TABLE beat_schema_version(id INTEGER PRIMARY KEY,version INTEGER NOT NULL)')
            connection.execute('INSERT INTO beat_schema_version VALUES(1,99)')
            before = connection.execute("SELECT name FROM sqlite_master ORDER BY name").fetchall()
            with self.assertRaises(BeatError) as caught:
                beat_store.migrate(connection)
            self.assertEqual(caught.exception.code, 'beat_storage_unavailable')
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master ORDER BY name").fetchall(), before)
            self.assertTrue(connection.in_transaction)
            self.assertEqual(connection.execute('SELECT version FROM beat_schema_version').fetchone()[0], 99)
        finally:
            connection.close()

    def test_restore_is_a_new_revision_and_archive_retains_history(self) -> None:
        created = beat_store.create(document_fixture())
        beat_store.update(created.id, 0, created.model_copy(update={'title': 'Changed'}))
        restored = beat_store.restore(created.id, 1, 0)
        self.assertEqual((restored.title, restored.revision), (created.title, 2))
        self.assertEqual([item.revision for item in beat_store.revisions(created.id)], [2, 1, 0])
        beat_store.archive(created.id, 2)
        self.assertEqual(beat_store.list_documents(), [])
        self.assertEqual(beat_store.get_revision(created.id, 1).title, 'Changed')
        with self.assertRaises(BeatError):
            beat_store.get(created.id)

    def test_two_connections_cannot_both_update_one_expected_revision(self) -> None:
        created = beat_store.create(document_fixture())
        connections: dict[int, sqlite3.Connection] = {}
        barrier = threading.Barrier(2)
        def writer(title: str) -> str:
            connection = sqlite3.connect(db.DB_PATH, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            connections[threading.get_ident()] = connection
            try:
                barrier.wait(5)
                try:
                    beat_store.update(created.id, 0, created.model_copy(update={'title': title}))
                    return 'updated'
                except BeatError as error:
                    return error.code
            finally:
                connection.close()
        with patch.object(beat_store, '_connection', side_effect=lambda: connections[threading.get_ident()]), \
             ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(writer, ('One', 'Two')))
        self.assertCountEqual(results, ['updated', 'beat_revision_conflict'])
        self.assertEqual(len(beat_store.revisions(created.id)), 2)

    def test_humanize_and_variation_write_explicit_undoable_steps(self) -> None:
        created = beat_store.create(document_fixture())
        humanized = beat_store.humanize(created.id, BeatHumanizeRequest(expected_revision=0, pattern_id='A',
            lane_ids=['lane-0'], timing_steps=0.1, velocity=12))
        before = created.patterns[0].lanes[0].steps[0]
        after = humanized.patterns[0].lanes[0].steps[0]
        self.assertLessEqual(abs(after.offset_steps - before.offset_steps), 0.1)
        self.assertLessEqual(abs(after.velocity - before.velocity), 12)
        self.assertNotEqual(after, before)
        varied = beat_store.variation(created.id, BeatVariationRequest(expected_revision=1, pattern_id='A', seed=190))
        self.assertEqual((varied.revision, varied.seed), (2, 190))
        self.assertEqual(beat_store.restore(created.id, 2, 0).patterns, created.patterns)


if __name__ == '__main__':
    unittest.main()
