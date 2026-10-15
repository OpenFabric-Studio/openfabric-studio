"""Queue storage preserves request history across races and bounded responses."""
from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from app import artist_production, db, music_queue_store as store
from app.local_music_contracts import LocalMusicRequest
from app.music_queue_contracts import (
    MusicQueueBatch, MusicQueueBrief, MusicQueueEngineSelection, MusicQueueError,
    MusicQueueHeartRequest, MusicQueueItem, MusicQueueItemStatus, MusicQueueModelPlan,
    MusicQueueReadiness, MusicQueueSnapshot,
)
from app.track_view import track_response


def batch(status: MusicQueueItemStatus = 'queued', count: int = 1) -> MusicQueueBatch:
    identifier = uuid4().hex
    selection = MusicQueueEngineSelection(engine='heartmula')
    readiness = MusicQueueReadiness(engine='heartmula', supported=True, installed=True,
        weights_ready=True, managed_status='one_shot', ready=True)
    return MusicQueueBatch(id=identifier, created_at='2026-10-14T10:00:00Z',
        brief=MusicQueueBrief(description='Warm jazz', duration_seconds=30),
        selections=[selection], models=[MusicQueueModelPlan(selection=selection,
            readiness=readiness, compatible=True, duration_mode='maximum', effective_duration_seconds=30)],
        preview_fingerprint='a' * 64, items=[MusicQueueItem(id=uuid4().hex, batch_id=identifier,
            engine='heartmula', variation_index=1, resolved_seed=7, status=status,
            payload=MusicQueueHeartRequest(engine='heartmula', request=LocalMusicRequest(
                engine='heartmula', prompt='Warm jazz', duration_seconds=30, seed=7))) for _ in range(count)])


class MusicQueueStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(temporary) / 'catalog.db'
        self.connection = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('PRAGMA foreign_keys=ON')
        self.connection.execute('''CREATE TABLE tracks (
            id INTEGER PRIMARY KEY, short_id INTEGER, is_favorite INTEGER DEFAULT 0,
            model TEXT DEFAULT 'heartmula', created_at TEXT DEFAULT '2026-10-14',
            title TEXT DEFAULT 'Original', lyrics TEXT DEFAULT '', seed INTEGER DEFAULT 7,
            duration_ms REAL, wall_ms REAL, params_json TEXT DEFAULT '{}',
            audio_path TEXT DEFAULT '/fixture.wav', abc_path TEXT, stems_json TEXT, midi_json TEXT)''')
        self.enterContext(patch.object(db, 'get_db', return_value=self.connection))
        self.enterContext(patch.object(db, 'DB_PATH', self.path))

    def enqueue(self, value: MusicQueueBatch | None = None, *, key: str = 'first', revision: int = 0) -> MusicQueueSnapshot:
        return store.enqueue(value or batch(), submission_key=key, request_hash='b' * 64, revision=revision)

    def seed(self, values: list[MusicQueueBatch]) -> None:
        store.snapshot()
        self.connection.execute('BEGIN')
        try:
            for ordinal, value in enumerate(values, start=1):
                self.connection.execute('''INSERT INTO music_queue_briefs
                    (id,ordinal,submission_key,request_hash,body_json) VALUES(?,?,?,?,?)''',
                    (value.id, ordinal, f'seed-{ordinal}', hashlib.sha256(str(ordinal).encode()).hexdigest(),
                     value.model_copy(update={'items': []}).model_dump_json()))
                self.connection.executemany('''INSERT INTO music_queue_items
                    (id,batch_id,ordinal,status,body_json) VALUES(?,?,?,?,?)''',
                    [(item.id, value.id, index, item.status, item.model_dump_json())
                     for index, item in enumerate(value.items, start=1)])
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def test_initializes_additive_schema_once_without_writing_every_read(self) -> None:
        initial = store.snapshot()
        self.assertEqual(initial, MusicQueueSnapshot(revision=0, status='idle'))
        self.assertEqual(self.connection.execute('SELECT schema_version FROM music_queue_control').fetchone()[0], 1)
        with patch.object(store, 'transaction', side_effect=AssertionError('GET attempted a write transaction')):
            self.assertEqual(store.snapshot(), initial)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM tracks').fetchone()[0], 0)

    def test_interrupted_schema_creation_rolls_back_and_can_retry(self) -> None:
        migrate = store._migrate
        def interrupted(connection: sqlite3.Connection) -> None:
            migrate(connection)
            raise RuntimeError('synthetic migration interruption')
        with patch.object(store, '_migrate', new=interrupted), self.assertRaises(RuntimeError):
            store.snapshot()
        self.assertIsNone(self.connection.execute("SELECT 1 FROM sqlite_master WHERE name='music_queue_control'").fetchone())
        self.assertEqual(store.snapshot(), MusicQueueSnapshot(revision=0, status='idle'))

    def test_submission_is_idempotent_before_revision_check_and_conflicts_on_changed_hash(self) -> None:
        original = self.enqueue()
        advanced = store.mutate(lambda state: setattr(state, 'status', 'paused'), revision=1)
        duplicate = self.enqueue(batch(), revision=0)
        self.assertEqual(duplicate, advanced)
        self.assertEqual(duplicate.batches[0].id, original.batches[0].id)
        self.assertEqual(store.lookup_submission('first', 'b' * 64), advanced)
        self.assertIsNone(store.lookup_submission('missing', 'b' * 64))
        with self.assertRaises(MusicQueueError) as raised:
            store.enqueue(batch(), submission_key='first', request_hash='c' * 64, revision=0)
        self.assertEqual(raised.exception.code, 'music_queue_submission_conflict')
        with self.assertRaises(MusicQueueError):
            store.lookup_submission('first', 'c' * 64)

    def test_stale_enqueue_and_control_do_not_change_rows(self) -> None:
        original = self.enqueue()
        with self.assertRaises(MusicQueueError) as raised:
            self.enqueue(key='second', revision=0)
        self.assertEqual(raised.exception.code, 'music_queue_revision_conflict')
        with self.assertRaises(MusicQueueError):
            store.mutate(lambda state: setattr(state, 'status', 'running'), revision=0)
        self.assertEqual(store.snapshot(), original)

    def test_failed_enqueue_rolls_back_all_rows_and_revision(self) -> None:
        store.snapshot()
        self.connection.execute('''CREATE TRIGGER reject_second_item BEFORE INSERT ON music_queue_items
            WHEN NEW.ordinal=2 BEGIN SELECT RAISE(ABORT,'synthetic failure'); END''')
        with self.assertRaises(sqlite3.IntegrityError):
            self.enqueue(batch(count=2))
        self.assertEqual(store.snapshot(), MusicQueueSnapshot(revision=0, status='idle'))
        self.assertEqual(self.connection.execute('SELECT count(*) FROM music_queue_items').fetchone()[0], 0)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM music_queue_briefs').fetchone()[0], 0)

    def test_callback_failure_rolls_back(self) -> None:
        original = self.enqueue()
        def change(state: MusicQueueSnapshot) -> None:
            state.batches[0].items[0].status = 'running'
            raise RuntimeError('synthetic callback failure')
        with self.assertRaisesRegex(RuntimeError, 'synthetic callback failure'):
            store.mutate(change)
        self.assertEqual(store.snapshot(), original)

    def test_same_revision_controls_race_on_fresh_transaction_state(self) -> None:
        self.enqueue()
        start = threading.Barrier(2)
        def change() -> str:
            start.wait(timeout=5)
            try:
                store.mutate(lambda state: setattr(state, 'status', 'paused'), revision=1)
                return 'saved'
            except MusicQueueError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: change(), range(2)))
        self.assertCountEqual(outcomes, ['saved', 'music_queue_revision_conflict'])
        self.assertEqual(store.snapshot().revision, 2)

    def test_duplicate_submission_race_inserts_one_complete_batch(self) -> None:
        start = threading.Barrier(2)
        def submit() -> MusicQueueSnapshot:
            start.wait(timeout=5)
            return self.enqueue(batch(count=2))
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: submit(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0].revision, 1)
        self.assertEqual(len(results[0].batches), 1)
        self.assertEqual(len(results[0].batches[0].items), 2)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM music_queue_briefs').fetchone()[0], 1)

    def test_shared_connection_commit_cannot_publish_partial_independent_enqueue(self) -> None:
        store.snapshot()
        inserted, release = threading.Event(), threading.Event()
        original_transaction = artist_production.transaction
        def pause() -> int:
            inserted.set()
            if not release.wait(timeout=5):
                raise RuntimeError('test write barrier timed out')
            return 0
        @contextmanager
        def isolated() -> Iterator[sqlite3.Connection]:
            with original_transaction() as connection:
                connection.create_function('pause_queue_write', 0, pause)
                yield connection
        self.connection.execute('''CREATE TRIGGER pause_insert AFTER INSERT ON music_queue_items
            BEGIN SELECT pause_queue_write(); END''')
        with patch.object(store, 'transaction', new=isolated), ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(self.enqueue)
            try:
                self.assertTrue(inserted.wait(timeout=5))
                self.connection.commit()
                self.assertEqual(self.connection.execute('SELECT count(*) FROM music_queue_briefs').fetchone()[0], 0)
                self.assertEqual(self.connection.execute('SELECT revision FROM music_queue_control').fetchone()[0], 0)
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=5).revision, 1)

    def test_immutable_brief_payload_seed_and_identity_reject_changes(self) -> None:
        original = self.enqueue()
        changes = [lambda state: setattr(state.batches[0].brief, 'description', 'Changed intent'),
            lambda state: setattr(state.batches[0].items[0].payload.request, 'prompt', 'Changed prompt'),
            lambda state: setattr(state.batches[0].items[0], 'resolved_seed', 9),
            lambda state: setattr(state.batches[0].items[0], 'attempt', 2)]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(MusicQueueError):
                store.mutate(change)
            self.assertEqual(store.snapshot(), original)

    def test_reorder_persists_without_unique_rank_collision_and_rolls_back_failure(self) -> None:
        first = self.enqueue()
        second = self.enqueue(key='second', revision=first.revision)
        reordered = store.mutate(lambda state: state.batches.reverse(), revision=second.revision)
        self.assertEqual([value.id for value in store.snapshot().batches], [value.id for value in reversed(second.batches)])
        self.connection.execute('''CREATE TRIGGER reject_control BEFORE UPDATE ON music_queue_control
            BEGIN SELECT RAISE(ABORT,'synthetic control failure'); END''')
        with self.assertRaises(sqlite3.IntegrityError):
            store.mutate(lambda state: state.batches.reverse())
        self.assertEqual(store.snapshot(), reordered)

    def test_bounded_history_keeps_all_unfinished_and_does_not_delete_hidden_rows(self) -> None:
        old = batch('queued')
        completed = [batch('done') for _ in range(1001)]
        self.seed([old, *completed])
        current = store.snapshot()
        self.assertEqual(len(current.batches), 1000)
        self.assertTrue(current.history_incomplete)
        self.assertEqual(current.batches[0].id, old.id)
        self.assertNotIn(completed[0].id, [value.id for value in current.batches])
        self.assertEqual(current.batches[-1].id, completed[-1].id)
        store.mutate(lambda state: setattr(state, 'status', 'paused'))
        self.assertEqual(self.connection.execute('SELECT count(*) FROM music_queue_briefs').fetchone()[0], 1002)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM music_queue_items').fetchone()[0], 1002)
        self.assertIsNotNone(store.lookup_submission('seed-2', hashlib.sha256(b'2').hexdigest()))

    def test_outstanding_limit_is_transactional_for_enqueue_and_requeue(self) -> None:
        failed = batch('failed')
        self.seed([failed, batch(count=1000)])
        before = store.snapshot()
        with self.assertRaises(MusicQueueError) as raised:
            self.enqueue(key='overflow')
        self.assertEqual(raised.exception.code, 'music_queue_capacity_exceeded')
        def exceed(state: MusicQueueSnapshot) -> None:
            state.batches[0].items[0].status = 'queued'
        with self.assertRaises(MusicQueueError):
            store.mutate(exceed)
        self.assertEqual(store.snapshot(), before)
        self.assertEqual(before.revision, 0)

    def test_concurrent_requeues_cannot_exceed_outstanding_limit(self) -> None:
        first, second = batch('failed'), batch('failed')
        self.seed([batch(count=999), first, second])
        start = threading.Barrier(2)
        def requeue(identifier: str) -> str:
            start.wait(timeout=5)
            def change(state: MusicQueueSnapshot) -> None:
                for value in state.batches:
                    if value.id == identifier:
                        value.items[0].status = 'queued'
            try:
                store.mutate(change)
                return 'saved'
            except MusicQueueError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(requeue, (first.id, second.id)))
        self.assertCountEqual(outcomes, ['saved', 'music_queue_capacity_exceeded'])
        self.assertEqual(store.snapshot().revision, 1)

    def test_retry_appends_attempt_preserving_original_and_enforces_per_batch_bound(self) -> None:
        initial = self.enqueue(batch('failed'))
        original = initial.batches[0].items[0]
        retry = original.model_copy(deep=True, update={'id': uuid4().hex, 'status': 'queued',
            'attempt': 2, 'retry_of_item_id': original.id})
        result = store.mutate(lambda state: state.batches[0].items.append(retry))
        self.assertEqual(result.batches[0].items, [original, retry])
        self.assertEqual(store.snapshot(), result)
        self.connection.execute('DELETE FROM music_queue_items')
        self.connection.execute('DELETE FROM music_queue_briefs')
        self.seed([batch('failed', count=1000)])
        def overflow(state: MusicQueueSnapshot) -> None:
            item = state.batches[0].items[-1]
            state.batches[0].items.append(item.model_copy(update={'id': uuid4().hex,
                'attempt': 2, 'retry_of_item_id': item.id, 'status': 'queued'}))
        with self.assertRaises(MusicQueueError):
            store.mutate(overflow)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM music_queue_items').fetchone()[0], 1000)

    def test_live_track_hydration_keeps_favorite_changes_and_deleted_output_history(self) -> None:
        self.enqueue()
        self.connection.execute('INSERT INTO tracks(id,short_id) VALUES(1,1)')
        row = self.connection.execute('SELECT * FROM tracks WHERE id=1').fetchone()
        self.assertIsNotNone(row)
        saved = track_response(row)
        def complete(state: MusicQueueSnapshot) -> None:
            item = state.batches[0].items[0]
            item.status, item.tracks = 'done', [saved]
        completed = store.mutate(complete)
        self.assertFalse(completed.batches[0].items[0].tracks[0].is_favorite)
        self.connection.execute("UPDATE tracks SET is_favorite=1,title='Renamed' WHERE id=1")
        current = store.snapshot()
        self.assertTrue(current.batches[0].items[0].tracks[0].is_favorite)
        self.assertEqual(current.batches[0].items[0].tracks[0].title, 'Renamed')
        self.connection.execute('DELETE FROM tracks WHERE id=1')
        self.assertEqual(store.snapshot().batches[0].items[0].tracks, [])
        store.mutate(lambda state: setattr(state.batches[0].items[0], 'error_code', 'retained_metadata'))
        body = self.connection.execute('SELECT body_json,track_id FROM music_queue_items').fetchone()
        self.assertIsNone(body['track_id'])
        retained = MusicQueueItem.model_validate_json(body['body_json'])
        self.assertEqual(retained.tracks[0].title, 'Original')
        self.assertEqual(retained.payload, completed.batches[0].items[0].payload)
        self.assertEqual(store.snapshot().batches[0].items[0].tracks, [])
        self.connection.execute("INSERT INTO tracks(id,short_id,title) VALUES(1,2,'Unrelated reused ID')")
        store.mutate(lambda state: setattr(state.batches[0].items[0], 'tracks', [saved]))
        self.assertEqual(store.snapshot().batches[0].items[0].tracks, [])

    def test_invalid_mutations_cannot_remove_history_or_leave_dangling_active_item(self) -> None:
        original = self.enqueue()
        for change in (lambda state: state.batches.clear(),
                       lambda state: state.batches[0].items.clear(),
                       lambda state: setattr(state, 'active_item_id', uuid4().hex)):
            with self.subTest(change=change), self.assertRaises(MusicQueueError):
                store.mutate(change)
            self.assertEqual(store.snapshot(), original)

    def test_bump_false_persists_without_fabricating_revision(self) -> None:
        self.enqueue()
        result = store.mutate(lambda state: setattr(state, 'status', 'paused'), bump=False)
        self.assertEqual(result.revision, 1)
        self.assertEqual(store.snapshot(), result)
