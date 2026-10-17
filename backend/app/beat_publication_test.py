from __future__ import annotations

from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from app import db


class BeatPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)
        self.connection.execute('CREATE TABLE tracks(id INTEGER PRIMARY KEY,model TEXT,created_at TEXT,title TEXT,lyrics TEXT,seed INTEGER,duration_ms REAL,wall_ms REAL,params_json TEXT,audio_path TEXT,abc_path TEXT,short_id INTEGER UNIQUE)')
        self.connection.execute('CREATE TABLE beat_render_jobs(id TEXT PRIMARY KEY,track_id INTEGER UNIQUE REFERENCES tracks(id))')
        db.ensure_track_codes(self.connection)
        self.enterContext(patch.object(db, 'get_db', return_value=self.connection))

    def publish(self, identifier: str) -> int:
        return db.insert_track(model='beat', title='Beat fixture', lyrics='', seed=1, duration_ms=1000,
            wall_ms=None, params={}, audio_path=Path('/private/test-beat.wav'), abc_path=None, beat_job_id=identifier)

    def test_track_and_job_attachment_commit_together_and_duplicate_rolls_back(self) -> None:
        identifier = 'a' * 32
        self.connection.execute('INSERT INTO beat_render_jobs(id) VALUES(?)', (identifier,))
        self.connection.commit()
        track = self.publish(identifier)
        self.assertEqual(self.connection.execute('SELECT track_id FROM beat_render_jobs WHERE id=?', (identifier,)).fetchone()[0], track)
        with self.assertRaises(ValueError):
            self.publish(identifier)
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM tracks').fetchone()[0], 1)
        self.assertEqual(self.connection.execute('SELECT last_short_id FROM track_code').fetchone()[0], 1)

    def test_missing_job_cannot_leave_an_orphan_track_or_number(self) -> None:
        with self.assertRaises(ValueError):
            self.publish('b' * 32)
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM tracks').fetchone()[0], 0)
        self.assertEqual(self.connection.execute('SELECT last_short_id FROM track_code').fetchone()[0], 0)

    def test_song_mix_publication_attachment_and_short_number_are_atomic(self) -> None:
        self.connection.execute('CREATE TABLE beat_song_mix_jobs(id TEXT PRIMARY KEY,track_id INTEGER UNIQUE REFERENCES tracks(id))')
        self.connection.execute('INSERT INTO beat_song_mix_jobs(id) VALUES(?)', ('c' * 32,))
        self.connection.commit()
        def publish(identifier: str) -> int:
            return db.insert_track(model='beat_song', title='Song mix', lyrics='', seed=None, duration_ms=1000,
                wall_ms=None, params={}, audio_path=Path('/private/fixture.wav'), abc_path=None,
                beat_song_mix_job_id=identifier)
        saved = publish('c' * 32)
        self.assertEqual(self.connection.execute('SELECT track_id FROM beat_song_mix_jobs').fetchone()[0], saved)
        for identifier in ('c' * 32, 'd' * 32):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                publish(identifier)
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM tracks').fetchone()[0], 1)
        self.assertEqual(self.connection.execute('SELECT last_short_id FROM track_code').fetchone()[0], 1)

    def test_replacement_attachment_failure_rolls_back_track_and_number(self) -> None:
        from app.replacement_store import ReplacementStoreError
        def fail(connection: sqlite3.Connection, identifier: str, track_id: int) -> None:
            self.assertIs(connection, self.connection)
            self.assertEqual(identifier, 'f' * 32)
            self.assertTrue(connection.in_transaction)
            self.assertIsNotNone(connection.execute('SELECT id FROM tracks WHERE id=?', (track_id,)).fetchone())
            raise ReplacementStoreError('replacement_request_conflict')
        with patch('app.replacement_store.attach_render', side_effect=fail):
            with self.assertRaises(ReplacementStoreError):
                db.insert_track(model='upload', title='Replacement', lyrics='', seed=None, duration_ms=1000,
                    wall_ms=None, params={}, audio_path=Path('/private/fixture.wav'), abc_path=None,
                    replacement_render_id='f' * 32)
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM tracks').fetchone()[0], 0)
        self.assertEqual(self.connection.execute('SELECT last_short_id FROM track_code').fetchone()[0], 0)
