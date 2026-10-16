"""Independent SQLite transactions for immutable music plans and mutable work."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import re
import sqlite3

from pydantic import ValidationError

from . import db
from .artist_production import transaction
from .music_queue_contracts import MusicQueueBatch, MusicQueueError, MusicQueueItem, MusicQueueSnapshot
from .track_view import track_response

_TERMINAL_SQL = "'done','failed','cancelled','interrupted','blocked'"
_ITEM_MUTABLE = {'child_job_id', 'status', 'tracks', 'error_code'}
_MAX_ITEMS = 1000
_MAX_BATCHES = 1000
# Preserve retired plans verbatim; they must never enter executable queue state.
_RETIRED_BATCH_SQL = """(EXISTS(SELECT 1 FROM json_each(b.body_json,'$.selections') s
    WHERE json_extract(s.value,'$.engine')='heartmula') OR EXISTS(
    SELECT 1 FROM music_queue_items i WHERE i.batch_id=b.id
    AND json_extract(i.body_json,'$.engine')='heartmula'))"""


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    db.get_db()
    connection = sqlite3.connect(db.DB_PATH, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    try:
        yield connection
    finally:
        connection.close()


def _text(row: sqlite3.Row, name: str) -> str:
    value: object = row[name]
    if not isinstance(value, str):
        raise MusicQueueError('music_queue_invalid_state', 503)
    return value


def _integer(row: sqlite3.Row, name: str) -> int:
    value: object = row[name]
    if not isinstance(value, int):
        raise MusicQueueError('music_queue_invalid_state', 503)
    return value


def _migrate(connection: sqlite3.Connection) -> None:
    statements = (
        '''CREATE TABLE IF NOT EXISTS music_queue_control (
            id INTEGER PRIMARY KEY CHECK(id=1), schema_version INTEGER NOT NULL CHECK(schema_version=1),
            revision INTEGER NOT NULL CHECK(revision BETWEEN 0 AND 9007199254740991),
            status TEXT NOT NULL CHECK(status IN ('idle','running','pausing','paused','blocked')),
            active_item_id TEXT REFERENCES music_queue_items(id) DEFERRABLE INITIALLY DEFERRED,
            error_code TEXT NOT NULL DEFAULT '' CHECK(length(error_code)<=120))''',
        '''CREATE TABLE IF NOT EXISTS music_queue_briefs (
            id TEXT PRIMARY KEY CHECK(length(id)=32 AND id NOT GLOB '*[^0-9a-f]*'),
            ordinal INTEGER NOT NULL UNIQUE CHECK(ordinal>0),
            submission_key TEXT NOT NULL UNIQUE CHECK(length(submission_key) BETWEEN 1 AND 120
                AND submission_key NOT GLOB '*[^A-Za-z0-9_-]*'),
            request_hash TEXT NOT NULL CHECK(length(request_hash)=64 AND request_hash NOT GLOB '*[^0-9a-f]*'),
            body_json TEXT NOT NULL CHECK(json_valid(body_json) AND json_extract(body_json,'$.id')=id))''',
        f'''CREATE TABLE IF NOT EXISTS music_queue_items (
            id TEXT PRIMARY KEY CHECK(length(id)=32 AND id NOT GLOB '*[^0-9a-f]*'),
            batch_id TEXT NOT NULL REFERENCES music_queue_briefs(id),
            ordinal INTEGER NOT NULL CHECK(ordinal>0),
            status TEXT NOT NULL CHECK(status IN ('queued','loading','submitting','running','saving','unloading',{_TERMINAL_SQL})),
            track_id INTEGER UNIQUE REFERENCES tracks(id) ON DELETE SET NULL,
            body_json TEXT NOT NULL CHECK(json_valid(body_json)
                AND json_extract(body_json,'$.id')=id AND json_extract(body_json,'$.batch_id')=batch_id
                AND json_extract(body_json,'$.status')=status),
            UNIQUE(batch_id,ordinal))''',
        'CREATE INDEX IF NOT EXISTS idx_music_queue_items_status ON music_queue_items(status,batch_id)',
        '''CREATE TRIGGER IF NOT EXISTS music_queue_brief_identity BEFORE UPDATE ON music_queue_briefs
            WHEN NEW.id IS NOT OLD.id OR NEW.submission_key IS NOT OLD.submission_key
                OR NEW.request_hash IS NOT OLD.request_hash OR NEW.body_json IS NOT OLD.body_json
            BEGIN SELECT RAISE(ABORT,'immutable_music_queue_brief'); END''',
        '''CREATE TRIGGER IF NOT EXISTS music_queue_item_identity BEFORE UPDATE ON music_queue_items
            WHEN NEW.id IS NOT OLD.id OR NEW.batch_id IS NOT OLD.batch_id OR NEW.ordinal IS NOT OLD.ordinal
                OR json_extract(NEW.body_json,'$.engine') IS NOT json_extract(OLD.body_json,'$.engine')
                OR json_extract(NEW.body_json,'$.variation_index') IS NOT json_extract(OLD.body_json,'$.variation_index')
                OR json_extract(NEW.body_json,'$.payload') IS NOT json_extract(OLD.body_json,'$.payload')
                OR json_extract(NEW.body_json,'$.adapters') IS NOT json_extract(OLD.body_json,'$.adapters')
                OR json_extract(NEW.body_json,'$.resolved_seed') IS NOT json_extract(OLD.body_json,'$.resolved_seed')
                OR json_extract(NEW.body_json,'$.attempt') IS NOT json_extract(OLD.body_json,'$.attempt')
                OR json_extract(NEW.body_json,'$.retry_of_item_id') IS NOT json_extract(OLD.body_json,'$.retry_of_item_id')
            BEGIN SELECT RAISE(ABORT,'immutable_music_queue_item'); END''',
        "INSERT OR IGNORE INTO music_queue_control(id,schema_version,revision,status) VALUES(1,1,0,'idle')",
    )
    for statement in statements:
        connection.execute(statement)


def _ensure_schema() -> None:
    with _connection() as connection:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='music_queue_control'").fetchone()
        if exists is not None:
            row = connection.execute('SELECT schema_version FROM music_queue_control WHERE id=1').fetchone()
            if row is None or _integer(row, 'schema_version') != 1:
                raise MusicQueueError('music_queue_schema_unsupported', 503)
            return
    with transaction() as connection:
        _migrate(connection)


def _snapshot(connection: sqlite3.Connection) -> MusicQueueSnapshot:
    control = connection.execute('SELECT * FROM music_queue_control WHERE id=1').fetchone()
    if control is None:
        raise MusicQueueError('music_queue_invalid_state', 503)
    unfinished = f'''EXISTS(SELECT 1 FROM music_queue_items i WHERE i.batch_id=b.id
        AND (i.status NOT IN ({_TERMINAL_SQL}) OR i.id=?))'''
    pending = connection.execute(f'''SELECT b.* FROM music_queue_briefs b WHERE NOT {_RETIRED_BATCH_SQL} AND {unfinished}
        ORDER BY b.ordinal LIMIT ?''', (control['active_item_id'], _MAX_BATCHES + 1)).fetchall()
    if len(pending) > _MAX_BATCHES:
        raise MusicQueueError('music_queue_capacity_exceeded')
    remaining = _MAX_BATCHES - len(pending)
    completed = connection.execute(f'''SELECT b.* FROM music_queue_briefs b WHERE NOT {_RETIRED_BATCH_SQL} AND NOT {unfinished}
        ORDER BY b.ordinal DESC LIMIT ?''', (control['active_item_id'], remaining + 1)).fetchall()
    rows = sorted([*pending, *completed[:remaining]], key=lambda row: _integer(row, 'ordinal'))
    batches: list[MusicQueueBatch] = []
    for row in rows:
        value = MusicQueueBatch.model_validate_json(_text(row, 'body_json'))
        for item_row in connection.execute('SELECT * FROM music_queue_items WHERE batch_id=? ORDER BY ordinal', (value.id,)):
            item = MusicQueueItem.model_validate_json(_text(item_row, 'body_json'))
            item.tracks = []
            if item_row['track_id'] is not None:
                track = connection.execute('SELECT * FROM tracks WHERE id=?', (item_row['track_id'],)).fetchone()
                if track is not None:
                    item.tracks = [track_response(track)]
            value.items.append(item)
        batches.append(value)
    retired = connection.execute(f'SELECT 1 FROM music_queue_briefs b WHERE {_RETIRED_BATCH_SQL} LIMIT 1').fetchone() is not None
    retired_active = connection.execute(f'''SELECT 1 FROM music_queue_items a JOIN music_queue_briefs b ON b.id=a.batch_id
        WHERE a.id=? AND {_RETIRED_BATCH_SQL}''', (control['active_item_id'],)).fetchone() is not None
    return MusicQueueSnapshot.model_validate({'revision': control['revision'], 'status': 'paused' if retired_active else control['status'],
        'active_item_id': None if retired_active else control['active_item_id'], 'error_code': '' if retired_active else control['error_code'],
        'history_incomplete': retired or len(completed) > remaining, 'batches': batches})


def snapshot() -> MusicQueueSnapshot:
    """Read a consistent, bounded view without changing the durable history."""
    _ensure_schema()
    with _connection() as connection:
        connection.execute('BEGIN')
        return _snapshot(connection)


def _submission(connection: sqlite3.Connection, submission_key: str, request_hash: str) -> bool:
    row = connection.execute('SELECT request_hash FROM music_queue_briefs WHERE submission_key=?', (submission_key,)).fetchone()
    if row is None:
        return False
    if _text(row, 'request_hash') != request_hash:
        raise MusicQueueError('music_queue_submission_conflict')
    return True


def lookup_submission(submission_key: str, request_hash: str) -> MusicQueueSnapshot | None:
    _ensure_schema()
    with _connection() as connection:
        connection.execute('BEGIN')
        return _snapshot(connection) if _submission(connection, submission_key, request_hash) else None


def _revision(connection: sqlite3.Connection, revision: int | None) -> int:
    row = connection.execute('SELECT revision FROM music_queue_control WHERE id=1').fetchone()
    if row is None:
        raise MusicQueueError('music_queue_invalid_state', 503)
    current = _integer(row, 'revision')
    if revision is not None and (type(revision) is not int or revision != current):
        raise MusicQueueError('music_queue_revision_conflict')
    return current


def _capacity(connection: sqlite3.Connection) -> None:
    row = connection.execute(f'''SELECT count(*) AS count FROM music_queue_items a JOIN music_queue_briefs b ON b.id=a.batch_id
        WHERE a.status NOT IN ({_TERMINAL_SQL}) AND NOT {_RETIRED_BATCH_SQL}''').fetchone()
    if row is None or _integer(row, 'count') > _MAX_ITEMS:
        raise MusicQueueError('music_queue_capacity_exceeded')


def _body(value: MusicQueueBatch) -> str:
    return value.model_copy(update={'items': []}).model_dump_json()


def _track_id(connection: sqlite3.Connection, item: MusicQueueItem) -> int | None:
    if len(item.tracks) > 1:
        raise MusicQueueError('music_queue_invalid_state')
    if not item.tracks:
        return None
    identifier = item.tracks[0].id
    return identifier if connection.execute('SELECT 1 FROM tracks WHERE id=?', (identifier,)).fetchone() is not None else None


def _insert_item(connection: sqlite3.Connection, item: MusicQueueItem, ordinal: int) -> None:
    connection.execute('''INSERT INTO music_queue_items(id,batch_id,ordinal,status,track_id,body_json)
        VALUES(?,?,?,?,?,?)''', (item.id, item.batch_id, ordinal, item.status,
        _track_id(connection, item), item.model_dump_json()))


def enqueue(batch: MusicQueueBatch, *, submission_key: str, request_hash: str, revision: int) -> MusicQueueSnapshot:
    """Idempotency, complete plan insertion, capacity and revision share one commit."""
    _ensure_schema()
    with transaction() as connection:
        if _submission(connection, submission_key, request_hash):
            return _snapshot(connection)
        current = _revision(connection, revision)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', submission_key) or not re.fullmatch(r'[0-9a-f]{64}', request_hash):
            raise MusicQueueError('music_queue_invalid_state')
        if len(batch.items) > _MAX_ITEMS:
            raise MusicQueueError('music_queue_attempt_limit')
        try:
            value = MusicQueueBatch.model_validate(batch.model_dump())
        except ValidationError as error:
            raise MusicQueueError('music_queue_invalid_state') from error
        if not value.items or len({item.id for item in value.items}) != len(value.items) or any(item.batch_id != value.id for item in value.items):
            raise MusicQueueError('music_queue_invalid_state')
        row = connection.execute('SELECT COALESCE(MAX(ordinal),0)+1 AS ordinal FROM music_queue_briefs').fetchone()
        if row is None:
            raise MusicQueueError('music_queue_invalid_state', 503)
        connection.execute('INSERT INTO music_queue_briefs(id,ordinal,submission_key,request_hash,body_json) VALUES(?,?,?,?,?)',
            (value.id, _integer(row, 'ordinal'), submission_key, request_hash, _body(value)))
        for ordinal, item in enumerate(value.items, start=1):
            _insert_item(connection, item, ordinal)
        _capacity(connection)
        connection.execute('UPDATE music_queue_control SET revision=? WHERE id=1', (current + 1,))
        return _snapshot(connection)


def _update_item(connection: sqlite3.Connection, item: MusicQueueItem, previous: MusicQueueItem) -> None:
    row = connection.execute('SELECT body_json,track_id FROM music_queue_items WHERE id=?', (item.id,)).fetchone()
    if row is None:
        raise MusicQueueError('music_queue_invalid_state')
    retained = MusicQueueItem.model_validate_json(_text(row, 'body_json'))
    track_id: int | None
    if retained.tracks:
        if item.tracks and [track.id for track in item.tracks] != [track.id for track in retained.tracks]:
            raise MusicQueueError('music_queue_immutable_plan')
        if previous.tracks and not item.tracks:
            raise MusicQueueError('music_queue_immutable_plan')
        track_id = _integer(row, 'track_id') if row['track_id'] is not None else None
        item = item.model_copy(update={'tracks': retained.tracks})
    else:
        track_id = _track_id(connection, item)
    connection.execute('UPDATE music_queue_items SET status=?,track_id=?,body_json=? WHERE id=?',
        (item.status, track_id, item.model_dump_json(), item.id))


def _persist(connection: sqlite3.Connection, state: MusicQueueSnapshot, previous: MusicQueueSnapshot) -> None:
    originals = {value.id: value for value in previous.batches}
    if len({value.id for value in state.batches}) != len(state.batches) or set(originals) != {value.id for value in state.batches}:
        raise MusicQueueError('music_queue_invalid_state')
    for value in state.batches:
        before = originals[value.id]
        if _body(value) != _body(before):
            raise MusicQueueError('music_queue_immutable_plan')
        old_items = {item.id: item for item in before.items}
        if len({item.id for item in value.items}) != len(value.items) or [item.id for item in value.items if item.id in old_items] != [item.id for item in before.items]:
            raise MusicQueueError('music_queue_invalid_state')
        for ordinal, item in enumerate(value.items, start=1):
            if item.batch_id != value.id:
                raise MusicQueueError('music_queue_invalid_state')
            old = old_items.get(item.id)
            if old is not None:
                if item.model_dump(exclude=_ITEM_MUTABLE) != old.model_dump(exclude=_ITEM_MUTABLE):
                    raise MusicQueueError('music_queue_immutable_plan')
                if item != old:
                    _update_item(connection, item, old)
            else:
                anchor = old_items.get(item.retry_of_item_id or '')
                if anchor is None or item.attempt != anchor.attempt + 1 or item.status != 'queued' or item.child_job_id is not None or item.tracks or item.error_code:
                    raise MusicQueueError('music_queue_invalid_state')
                if item.model_dump(exclude=_ITEM_MUTABLE | {'id', 'attempt', 'retry_of_item_id'}) != anchor.model_dump(exclude=_ITEM_MUTABLE | {'id', 'attempt', 'retry_of_item_id'}):
                    raise MusicQueueError('music_queue_immutable_plan')
                if ordinal <= len(before.items):
                    raise MusicQueueError('music_queue_invalid_state')
                _insert_item(connection, item, ordinal)
    if [value.id for value in state.batches] != [value.id for value in previous.batches]:
        rows = connection.execute('SELECT id,ordinal FROM music_queue_briefs ORDER BY ordinal').fetchall()
        slots = [_integer(row, 'ordinal') for row in rows if _text(row, 'id') in originals]
        temporary = max((_integer(row, 'ordinal') for row in rows), default=0)
        # Vacate unique ranks before assigning the permutation, retaining invisible history slots.
        for index, value in enumerate(state.batches, start=1):
            connection.execute('UPDATE music_queue_briefs SET ordinal=? WHERE id=?', (temporary + index, value.id))
        for ordinal, value in zip(slots, state.batches, strict=True):
            connection.execute('UPDATE music_queue_briefs SET ordinal=? WHERE id=?', (ordinal, value.id))
    _capacity(connection)
    connection.execute('UPDATE music_queue_control SET revision=?,status=?,active_item_id=?,error_code=? WHERE id=1',
        (state.revision, state.status, state.active_item_id, state.error_code))


def mutate(change: Callable[[MusicQueueSnapshot], None], *, revision: int | None = None, bump: bool = True) -> MusicQueueSnapshot:
    """Apply one synchronous read-modify-write operation to fresh transaction state."""
    _ensure_schema()
    with transaction() as connection:
        current = _revision(connection, revision)
        previous = _snapshot(connection)
        state = previous.model_copy(deep=True)
        change(state)
        if any(len(value.items) > _MAX_ITEMS for value in state.batches):
            raise MusicQueueError('music_queue_attempt_limit')
        if state.revision != current or state.history_incomplete != previous.history_incomplete:
            raise MusicQueueError('music_queue_invalid_state')
        state.revision = current + int(bump)
        try:
            state = MusicQueueSnapshot.model_validate(state.model_dump())
        except ValidationError as error:
            raise MusicQueueError('music_queue_invalid_state') from error
        if state.active_item_id is not None and not any(item.id == state.active_item_id for value in state.batches for item in value.items):
            raise MusicQueueError('music_queue_invalid_state')
        _persist(connection, state, previous)
        return _snapshot(connection)
