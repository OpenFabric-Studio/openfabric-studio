"""Short track numbers: at most 6 digits, then the free numbers are reused."""
from __future__ import annotations

import sqlite3
import unittest

from app.db import SHORT_ID_MAX, TrackNumbersFull, ensure_track_codes, next_short_id, take_short_id


def _tracks() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        """
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            short_id INTEGER
        )
        """
    )
    return db


class NextNumberTests(unittest.TestCase):
    def test_counts_up_and_wraps_past_six_digits(self):
        self.assertEqual(next_short_id(0, set()), 1)
        self.assertEqual(next_short_id(61, set()), 62)
        self.assertEqual(next_short_id(SHORT_ID_MAX, set()), 1)
        self.assertEqual(next_short_id(SHORT_ID_MAX, {1}), 2)
        self.assertEqual(next_short_id(5, {6}), 7)

    def test_a_full_list_is_rejected(self):
        with self.assertRaises(TrackNumbersFull):
            next_short_id(SHORT_ID_MAX, set(range(1, SHORT_ID_MAX + 1)))


class StoredNumberTests(unittest.TestCase):
    def test_existing_rows_keep_their_id_when_it_fits(self):
        db = _tracks()
        db.execute("INSERT INTO tracks (id) VALUES (61)")
        db.execute("INSERT INTO tracks (id) VALUES (1000001)")
        ensure_track_codes(db)
        rows = {row["id"]: row["short_id"] for row in db.execute("SELECT id, short_id FROM tracks")}
        self.assertEqual(rows[61], 61)
        self.assertEqual(rows[1000001], 62)
        self.assertEqual(db.execute("SELECT last_short_id FROM track_code").fetchone()["last_short_id"], 62)

    def test_the_next_save_continues_then_starts_over(self):
        db = _tracks()
        db.execute("INSERT INTO tracks (id, short_id) VALUES (1, 999998)")
        ensure_track_codes(db)
        db.execute("BEGIN IMMEDIATE")
        first = take_short_id(db)
        db.execute("INSERT INTO tracks (short_id) VALUES (?)", (first,))
        second = take_short_id(db)
        db.execute("INSERT INTO tracks (short_id) VALUES (?)", (second,))
        db.commit()
        self.assertEqual(first, 999999)
        self.assertEqual(second, 1)
        db.execute("BEGIN IMMEDIATE")
        third = take_short_id(db)
        db.commit()
        self.assertEqual(third, 2)
