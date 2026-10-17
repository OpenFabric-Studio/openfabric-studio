"""Additive SQLite mashup revisions and durable ownership receipts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import sqlite3

from pydantic import TypeAdapter

from . import db
from .beat_contracts import BeatId, canonical_json
from .mashup_contracts import MashupError, MashupJob, MashupState
from .video_process import WorkerIdentity


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def migrate(connection: sqlite3.Connection) -> None:
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mashup_schema_version'").fetchone()
    if exists is not None:
        row = connection.execute('SELECT version FROM mashup_schema_version WHERE id=1').fetchone()
        if row is not None and row[0] > 1:
            raise MashupError('mashup_storage_unavailable')
    connection.execute('SAVEPOINT mashup_migration')
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS mashup_schema_version(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL CHECK(version>=1))')
        connection.execute('CREATE TABLE IF NOT EXISTS mashup_documents(id TEXT PRIMARY KEY CHECK(length(id)=32),revision INTEGER NOT NULL CHECK(revision>=0),body_json TEXT NOT NULL CHECK(json_valid(body_json)),updated_at TEXT NOT NULL)')
        connection.execute('CREATE TABLE IF NOT EXISTS mashup_revisions(mashup_id TEXT NOT NULL REFERENCES mashup_documents(id),revision INTEGER NOT NULL CHECK(revision>=0),body_json TEXT NOT NULL CHECK(json_valid(body_json)),body_sha256 TEXT NOT NULL CHECK(length(body_sha256)=64),PRIMARY KEY(mashup_id,revision))')
        connection.execute("CREATE TABLE IF NOT EXISTS mashup_jobs(id TEXT PRIMARY KEY CHECK(length(id)=32),mashup_id TEXT NOT NULL REFERENCES mashup_documents(id),mashup_revision INTEGER NOT NULL,kind TEXT NOT NULL CHECK(kind IN('preparation','preview','full')),status TEXT NOT NULL CHECK(status IN('queued','running','cancelling','done','failed','cancelled','interrupted','blocked','saving')),payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)),worker_identity_json TEXT CHECK(worker_identity_json IS NULL OR json_valid(worker_identity_json)),track_id INTEGER UNIQUE REFERENCES tracks(id) ON DELETE SET NULL,project_id INTEGER UNIQUE REFERENCES projects(id) ON DELETE SET NULL,created_at TEXT NOT NULL,FOREIGN KEY(mashup_id,mashup_revision) REFERENCES mashup_revisions(mashup_id,revision))")
        connection.execute('CREATE INDEX IF NOT EXISTS mashup_jobs_state ON mashup_jobs(status,created_at)')
        connection.execute('CREATE INDEX IF NOT EXISTS mashup_jobs_document ON mashup_jobs(mashup_id,mashup_revision,created_at)')
        connection.execute('CREATE TABLE IF NOT EXISTS mashup_submissions(submission_key TEXT PRIMARY KEY,request_hash TEXT NOT NULL CHECK(length(request_hash)=64),mashup_id TEXT NOT NULL REFERENCES mashup_documents(id),job_id TEXT NOT NULL REFERENCES mashup_jobs(id))')
        connection.execute('INSERT OR IGNORE INTO mashup_schema_version(id,version) VALUES(1,1)')
        connection.execute('RELEASE mashup_migration')
    except BaseException:
        connection.execute('ROLLBACK TO mashup_migration')
        connection.execute('RELEASE mashup_migration')
        raise


def _connection() -> sqlite3.Connection:
    return db.get_db()


def identifier(value: str) -> str:
    return TypeAdapter(BeatId).validate_python(value)


def _body(state: MashupState) -> str:
    content = canonical_json(state)
    if len(content) > 8 * 1024 * 1024:
        raise MashupError('mashup_budget_exceeded')
    return content.decode('ascii')


def _revision(connection: sqlite3.Connection, state: MashupState) -> None:
    content = _body(state)
    connection.execute('INSERT INTO mashup_revisions(mashup_id,revision,body_json,body_sha256) VALUES(?,?,?,?)',
        (state.id, state.revision, content, hashlib.sha256(content.encode('ascii')).hexdigest()))


def get(mashup_id: str) -> MashupState:
    row = _connection().execute('SELECT body_json FROM mashup_documents WHERE id=?', (identifier(mashup_id),)).fetchone()
    if row is None:
        raise MashupError('mashup_not_found')
    state = MashupState.model_validate_json(row['body_json'])
    active = _connection().execute("SELECT id FROM mashup_jobs WHERE mashup_id=? AND status IN('queued','running','cancelling','blocked') ORDER BY created_at DESC LIMIT 1", (mashup_id,)).fetchone()
    return state.model_copy(update={'active_job_id': None if active is None else active['id']})


def revision(mashup_id: str, expected_revision: int) -> MashupState:
    row = _connection().execute('SELECT body_json,body_sha256 FROM mashup_revisions WHERE mashup_id=? AND revision=?',
        (identifier(mashup_id), expected_revision)).fetchone()
    if row is None:
        raise MashupError('mashup_not_found')
    body: str = row['body_json']
    if hashlib.sha256(body.encode('ascii')).hexdigest() != row['body_sha256']:
        raise MashupError('mashup_storage_unavailable')
    return MashupState.model_validate_json(body)


def list_documents() -> list[MashupState]:
    rows = _connection().execute('SELECT id FROM mashup_documents ORDER BY updated_at DESC,rowid DESC LIMIT 100').fetchall()
    return [get(row['id']) for row in rows]


@dataclass(frozen=True)
class StoredJob:
    job: MashupJob
    snapshot: MashupState
    identity: WorkerIdentity | None
    track_id: int | None
    project_id: int | None


def get_job(job_id: str) -> StoredJob:
    row = _connection().execute('SELECT * FROM mashup_jobs WHERE id=?', (identifier(job_id),)).fetchone()
    if row is None:
        raise MashupError('mashup_job_not_found')
    job = MashupJob.model_validate_json(row['payload_json'])
    snapshot = MashupState.model_validate_json(row['snapshot_json'])
    if job.id != job_id or job.mashup_id != snapshot.id or job.mashup_revision != snapshot.revision:
        raise MashupError('mashup_storage_unavailable')
    return StoredJob(job=job, snapshot=snapshot,
        identity=None if row['worker_identity_json'] is None else WorkerIdentity.model_validate_json(row['worker_identity_json']),
        track_id=row['track_id'], project_id=row['project_id'])


def list_jobs(mashup_id: str | None = None, *, active_only: bool = False) -> list[MashupJob]:
    clauses: list[str] = []
    values: list[str] = []
    if mashup_id is not None:
        clauses.append('mashup_id=?')
        values.append(identifier(mashup_id))
    if active_only:
        clauses.append("status IN('queued','running','cancelling','blocked','saving')")
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    rows = _connection().execute('SELECT payload_json FROM mashup_jobs' + where + ' ORDER BY created_at DESC,rowid DESC LIMIT 100', values).fetchall()
    return [MashupJob.model_validate_json(row['payload_json']) for row in rows]


def submission(key: str, checksum: str) -> StoredJob | None:
    row = _connection().execute('SELECT request_hash,job_id FROM mashup_submissions WHERE submission_key=?', (key,)).fetchone()
    if row is None:
        return None
    if row['request_hash'] != checksum:
        raise MashupError('mashup_submission_conflict')
    return get_job(row['job_id'])


def _insert_job(connection: sqlite3.Connection, state: MashupState, job: MashupJob, key: str, checksum: str) -> None:
    if job.mashup_id != state.id or job.mashup_revision != state.revision:
        raise MashupError('mashup_invalid')
    connection.execute('INSERT INTO mashup_jobs(id,mashup_id,mashup_revision,kind,status,payload_json,snapshot_json,created_at) VALUES(?,?,?,?,?,?,?,?)',
        (job.id, state.id, state.revision, job.kind, job.status, job.model_dump_json(), _body(state), job.created_at))
    connection.execute('INSERT INTO mashup_submissions(submission_key,request_hash,mashup_id,job_id) VALUES(?,?,?,?)',
        (key, checksum, state.id, job.id))


def create(state: MashupState, job: MashupJob, key: str, checksum: str) -> MashupState:
    connection = _connection()
    connection.execute('BEGIN IMMEDIATE')
    try:
        existing = submission(key, checksum)
        if existing is not None:
            connection.commit()
            return get(existing.job.mashup_id)
        if state.revision != 0:
            raise MashupError('mashup_invalid')
        connection.execute('INSERT INTO mashup_documents(id,revision,body_json,updated_at) VALUES(?,?,?,?)',
            (state.id, 0, _body(state), state.updated_at))
        _revision(connection, state)
        _insert_job(connection, state, job, key, checksum)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return get(state.id)


def update(mashup_id: str, expected_revision: int, state: MashupState) -> MashupState:
    state = MashupState.model_validate({**state.model_dump(), 'id': identifier(mashup_id),
        'revision': expected_revision + 1, 'updated_at': now(), 'active_job_id': None})
    connection = _connection()
    connection.execute('BEGIN IMMEDIATE')
    try:
        changed = connection.execute('UPDATE mashup_documents SET revision=?,body_json=?,updated_at=? WHERE id=? AND revision=?',
            (state.revision, _body(state), state.updated_at, state.id, expected_revision))
        if changed.rowcount != 1:
            raise MashupError('mashup_revision_conflict')
        _revision(connection, state)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return get(state.id)


def insert_job(state: MashupState, job: MashupJob, key: str, checksum: str) -> MashupJob:
    connection = _connection()
    connection.execute('BEGIN IMMEDIATE')
    try:
        existing = submission(key, checksum)
        if existing is not None:
            connection.commit()
            return existing.job
        current = get(state.id)
        if current.revision != state.revision:
            raise MashupError('mashup_revision_conflict')
        if current.active_job_id is not None:
            raise MashupError('mashup_busy')
        _insert_job(connection, state, job, key, checksum)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return job


def save_job(job: MashupJob) -> MashupJob:
    job = MashupJob.model_validate_json(job.model_dump_json())
    connection = _connection()
    connection.execute('UPDATE mashup_jobs SET status=?,payload_json=? WHERE id=?',
        (job.status, job.model_dump_json(), job.id))
    connection.commit()
    return job


def identity(job_id: str, worker: WorkerIdentity | None) -> None:
    connection = _connection()
    connection.execute('UPDATE mashup_jobs SET worker_identity_json=? WHERE id=?',
        (None if worker is None else worker.model_dump_json(), identifier(job_id)))
    connection.commit()


def attach_project(job_id: str, project_id: int) -> None:
    connection = _connection()
    changed = connection.execute('UPDATE mashup_jobs SET project_id=? WHERE id=? AND (project_id IS NULL OR project_id=?)',
        (project_id, identifier(job_id), project_id))
    if changed.rowcount != 1:
        connection.rollback()
        raise MashupError('mashup_save_failed')
    connection.commit()
