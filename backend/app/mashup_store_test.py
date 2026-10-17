from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from app import db, mashup_store
from app.mashup_contracts import MashupError, MashupJob, MashupProgress
from app.mashup_plan_test import state_fixture


class MashupStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, value in (('_db', None), ('DATA_DIR', self.root), ('FILES_DIR', self.root / 'files'),
                ('DB_PATH', self.root / 'catalog.db')):
            self.enterContext(patch.object(db, name, value))
        self.connection = db.get_db()
        mashup_store.migrate(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def create(self):
        state = state_fixture()
        job = MashupJob(id='b' * 32, mashup_id=state.id, mashup_revision=0, kind='preparation',
            status='queued', progress=MashupProgress(phase='queued'), created_at=state.created_at)
        return mashup_store.create(state, job, 'submission', 'c' * 64)

    def test_cas_preserves_immutable_revisions_and_duplicate_identity(self) -> None:
        state = self.create()
        self.assertEqual(self.create().id, state.id)
        changed = mashup_store.update(state.id, 0, state.model_copy(update={'title': 'New title'}))
        self.assertEqual(changed.revision, 1)
        self.assertEqual(mashup_store.revision(state.id, 0).title, 'Pair')
        with self.assertRaises(MashupError) as caught:
            mashup_store.update(state.id, 0, state)
        self.assertEqual(caught.exception.code, 'mashup_revision_conflict')
        self.assertEqual(mashup_store.get(state.id).title, 'New title')
        with self.assertRaises(MashupError) as caught:
            mashup_store.submission('submission', 'd' * 64)
        self.assertEqual(caught.exception.code, 'mashup_submission_conflict')

    def test_future_schema_does_not_mutate_or_commit(self) -> None:
        connection = sqlite3.connect(':memory:')
        try:
            connection.execute('CREATE TABLE mashup_schema_version(id INTEGER PRIMARY KEY,version INTEGER NOT NULL)')
            connection.execute('INSERT INTO mashup_schema_version VALUES(1,99)')
            before = connection.execute('SELECT name FROM sqlite_master ORDER BY name').fetchall()
            with self.assertRaises(MashupError):
                mashup_store.migrate(connection)
            self.assertTrue(connection.in_transaction)
            self.assertEqual(before, connection.execute('SELECT name FROM sqlite_master ORDER BY name').fetchall())
        finally:
            connection.close()

    def test_revision_failure_rolls_back_document_and_job_receipt(self) -> None:
        state = self.create()
        self.connection.execute("CREATE TRIGGER fail_revision BEFORE INSERT ON mashup_revisions WHEN NEW.revision=1 BEGIN SELECT RAISE(ABORT,'injected'); END")
        self.connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            mashup_store.update(state.id, 0, state.model_copy(update={'title': 'Lost'}))
        self.assertEqual(mashup_store.get(state.id).revision, 0)
        self.assertEqual(mashup_store.get_job('b' * 32).job.status, 'queued')

    def test_two_connections_cannot_save_the_same_expected_revision(self) -> None:
        state = self.create()
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
                    mashup_store.update(state.id, 0, state.model_copy(update={'title': title}))
                    return 'saved'
                except MashupError as error:
                    return error.code
            finally:
                connection.close()
        with patch.object(mashup_store, '_connection', side_effect=lambda: connections[threading.get_ident()]), \
                ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(writer, ('First', 'Second')))
        self.assertCountEqual(results, ['saved', 'mashup_revision_conflict'])
        self.assertEqual(self.connection.execute('SELECT count(*) FROM mashup_revisions').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
