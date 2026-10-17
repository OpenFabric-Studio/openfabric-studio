"""Transactional, digest-bound acknowledgements for new music operations.

Existing running jobs/history do not consult this admission gate. Unknown source
rights remain unknown even when all known model requirements are acknowledged.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import sqlite3

from . import db
from .module_licenses import declarations
from .music_terms_contracts import (
    MusicTermsAcceptRequest, MusicTermsEngine, MusicTermsOperation, MusicTermsRequirement,
    MusicTermsSettingsRequest, MusicTermsSettingsResponse, MusicTermsSnapshot,
)

_MAX_REVISION = 9_007_199_254_740_991
_TRANSCRIPTION = frozenset({'sheetsage2_orig', 'muscriptor_small_f32'})


class MusicTermsError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


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


def _schema_version(connection: sqlite3.Connection) -> int | None:
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='music_terms_schema'").fetchone()
    if exists is None:
        return None
    row = connection.execute('SELECT version FROM music_terms_schema WHERE id=1').fetchone()
    if row is None or row['version'] != 1:
        raise MusicTermsError('music_terms_unavailable', 503)
    return 1


def _ensure_schema() -> None:
    with _connection() as connection:
        if _schema_version(connection) is not None:
            return
        try:
            connection.execute('BEGIN IMMEDIATE')
            if _schema_version(connection) is None:
                connection.execute('''CREATE TABLE music_terms_schema (
                    id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL CHECK(version=1))''')
                connection.execute('''CREATE TABLE music_terms_policy (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    revision INTEGER NOT NULL CHECK(revision BETWEEN 0 AND 9007199254740991),
                    noncommercial_enabled INTEGER NOT NULL CHECK(noncommercial_enabled IN (0,1)))''')
                connection.execute('''CREATE TABLE music_terms_acknowledgements (
                    engine TEXT NOT NULL CHECK(engine IN ('ace_step','yue2','stable_audio3')),
                    operation TEXT NOT NULL CHECK(operation IN ('setup','generation','training','transcription')),
                    component_id TEXT NOT NULL CHECK(length(component_id) BETWEEN 1 AND 120),
                    terms_digest TEXT NOT NULL CHECK(length(terms_digest)=64 AND terms_digest NOT GLOB '*[^0-9a-f]*'),
                    accepted_at TEXT NOT NULL, PRIMARY KEY(engine,operation,component_id,terms_digest))''')
                connection.execute('INSERT INTO music_terms_policy VALUES(1,0,0)')
                connection.execute('INSERT INTO music_terms_schema VALUES(1,1)')
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


@contextmanager
def _transaction(*, write: bool = False) -> Iterator[sqlite3.Connection]:
    try:
        _ensure_schema()
        with _connection() as connection:
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
    except sqlite3.Error as error:
        raise MusicTermsError('music_terms_unavailable', 503) from error


def _settings(connection: sqlite3.Connection) -> MusicTermsSettingsResponse:
    row = connection.execute('SELECT revision,noncommercial_enabled FROM music_terms_policy WHERE id=1').fetchone()
    if row is None:
        raise MusicTermsError('music_terms_unavailable', 503)
    revision: object = row['revision']
    enabled: object = row['noncommercial_enabled']
    if not isinstance(revision, int) or not 0 <= revision <= _MAX_REVISION or enabled not in (0, 1):
        raise MusicTermsError('music_terms_unavailable', 503)
    return MusicTermsSettingsResponse(revision=revision, noncommercial_enabled=enabled == 1)


def _snapshot(connection: sqlite3.Connection, engine: MusicTermsEngine,
              operation: MusicTermsOperation) -> MusicTermsSnapshot:
    settings = _settings(connection)
    requirements: list[MusicTermsRequirement] = []
    if engine == 'yue2' and operation == 'training':
        from .music_training_setup import training_license_declarations
        components = training_license_declarations(engine)
    else:
        components = declarations(engine)
    for declaration in components:
        if engine == 'yue2' and operation not in ('setup', 'training'):
            is_transcription = declaration.component_id in _TRANSCRIPTION
            if is_transcription != (operation == 'transcription'):
                continue
        if declaration.restriction not in ('noncommercial', 'conditional'):
            continue
        if declaration.source_url is None:
            raise MusicTermsError('music_terms_unavailable', 503)
        serialized = json.dumps(declaration.model_dump(mode='json'), sort_keys=True,
            separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        digest = hashlib.sha256(serialized.encode('utf-8')).hexdigest()
        accepted = connection.execute('''SELECT 1 FROM music_terms_acknowledgements
            WHERE engine=? AND operation=? AND component_id=? AND terms_digest=?''',
            (engine, operation, declaration.component_id, digest)).fetchone() is not None
        requirements.append(MusicTermsRequirement(component_id=declaration.component_id, name=declaration.name,
            scope=declaration.scope, restriction=declaration.restriction, source_url=declaration.source_url,
            terms_digest=digest, acknowledged=accepted))
    allowed = all(item.acknowledged for item in requirements) and (
        settings.noncommercial_enabled or not any(item.restriction == 'noncommercial' for item in requirements))
    return MusicTermsSnapshot(engine=engine, operation=operation, revision=settings.revision,
        noncommercial_enabled=settings.noncommercial_enabled, requirements=requirements, allowed=allowed)


def snapshot(engine: MusicTermsEngine, operation: MusicTermsOperation) -> MusicTermsSnapshot:
    with _transaction() as connection:
        return _snapshot(connection, engine, operation)


def _advance(connection: sqlite3.Connection, expected_revision: int, enabled: bool) -> None:
    if expected_revision >= _MAX_REVISION:
        raise MusicTermsError('music_terms_unavailable', 503)
    updated = connection.execute('''UPDATE music_terms_policy SET revision=revision+1,noncommercial_enabled=?
        WHERE id=1 AND revision=?''', (int(enabled), expected_revision))
    if updated.rowcount != 1:
        raise MusicTermsError('music_terms_revision_conflict')


def accept(body: MusicTermsAcceptRequest) -> MusicTermsSnapshot:
    with _transaction(write=True) as connection:
        current = _snapshot(connection, body.engine, body.operation)
        if current.revision != body.expected_revision:
            raise MusicTermsError('music_terms_revision_conflict')
        if set(body.terms_digests) != {item.terms_digest for item in current.requirements}:
            raise MusicTermsError('music_terms_changed')
        enabled = current.noncommercial_enabled or body.allow_noncommercial
        if not enabled and any(item.restriction == 'noncommercial' for item in current.requirements):
            raise MusicTermsError('music_noncommercial_disabled')
        stamp = datetime.now(timezone.utc).isoformat()
        for item in current.requirements:
            connection.execute('''INSERT OR IGNORE INTO music_terms_acknowledgements
                (engine,operation,component_id,terms_digest,accepted_at) VALUES(?,?,?,?,?)''',
                (body.engine, body.operation, item.component_id, item.terms_digest, stamp))
        _advance(connection, current.revision, enabled)
        return _snapshot(connection, body.engine, body.operation)


def update_settings(body: MusicTermsSettingsRequest) -> MusicTermsSettingsResponse:
    with _transaction(write=True) as connection:
        _advance(connection, body.expected_revision, body.noncommercial_enabled)
        return _settings(connection)


def require(engine: MusicTermsEngine, operation: MusicTermsOperation) -> None:
    current = snapshot(engine, operation)
    if any(item.restriction == 'noncommercial' for item in current.requirements) and not current.noncommercial_enabled:
        raise MusicTermsError('music_noncommercial_disabled')
    if not current.allowed:
        raise MusicTermsError('music_terms_required')
