"""Additive immutable plans and owner receipts using the existing SQLite DB."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import sqlite3

from pydantic import TypeAdapter

from . import db
from .beat_contracts import BeatId, canonical_json
from .beat_song_mix_contracts import BeatSongMixError, BeatSongMixJob, BeatSongMixPlan, SongSourceSnapshot
from .video_process import WorkerIdentity

_snapshots = TypeAdapter(list[SongSourceSnapshot])
_id = TypeAdapter(BeatId)


def migrate(connection: sqlite3.Connection) -> None:
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='beat_song_mix_schema_version'").fetchone()
    if exists is not None:
        row = connection.execute('SELECT version FROM beat_song_mix_schema_version WHERE id=1').fetchone()
        if row is not None and row[0] > 1:
            raise BeatSongMixError('song_storage_unavailable')
    connection.execute('SAVEPOINT beat_song_mix_migration')
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS beat_song_mix_schema_version(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL CHECK(version>=1))')
        connection.execute('CREATE TABLE IF NOT EXISTS beat_song_mix_plans(id TEXT PRIMARY KEY CHECK(length(id)=32),revision INTEGER NOT NULL CHECK(revision>=0),body_json TEXT NOT NULL CHECK(json_valid(body_json)))')
        connection.execute('CREATE TABLE IF NOT EXISTS beat_song_mix_revisions(plan_id TEXT NOT NULL REFERENCES beat_song_mix_plans(id),revision INTEGER NOT NULL CHECK(revision>=0),body_json TEXT NOT NULL CHECK(json_valid(body_json)),sha256 TEXT NOT NULL CHECK(length(sha256)=64),PRIMARY KEY(plan_id,revision))')
        connection.execute("CREATE TABLE IF NOT EXISTS beat_song_mix_jobs(id TEXT PRIMARY KEY CHECK(length(id)=32),plan_id TEXT NOT NULL,plan_revision INTEGER NOT NULL,status TEXT NOT NULL CHECK(status IN('queued','running','cancelling','cancelled','done','failed','interrupted','blocked','saving')),payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),sources_json TEXT NOT NULL CHECK(json_valid(sources_json)),worker_identity_json TEXT CHECK(worker_identity_json IS NULL OR json_valid(worker_identity_json)),track_id INTEGER UNIQUE REFERENCES tracks(id) ON DELETE SET NULL,project_id INTEGER UNIQUE REFERENCES projects(id) ON DELETE SET NULL,created_at TEXT NOT NULL,FOREIGN KEY(plan_id,plan_revision) REFERENCES beat_song_mix_revisions(plan_id,revision))")
        connection.execute('CREATE INDEX IF NOT EXISTS beat_song_mix_jobs_state ON beat_song_mix_jobs(status,created_at)')
        connection.execute('CREATE INDEX IF NOT EXISTS beat_song_mix_jobs_plan ON beat_song_mix_jobs(plan_id,created_at)')
        connection.execute('CREATE TABLE IF NOT EXISTS beat_song_mix_submissions(submission_key TEXT PRIMARY KEY,request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),job_id TEXT NOT NULL REFERENCES beat_song_mix_jobs(id))')
        connection.execute('INSERT OR IGNORE INTO beat_song_mix_schema_version VALUES(1,1)')
        connection.execute('RELEASE beat_song_mix_migration')
    except BaseException:
        connection.execute('ROLLBACK TO beat_song_mix_migration')
        connection.execute('RELEASE beat_song_mix_migration')
        raise


def _connection() -> sqlite3.Connection:
    return db.get_db()


def _body(plan: BeatSongMixPlan) -> str:
    content = canonical_json(plan)
    if len(content) > 8 * 1024**2:
        raise BeatSongMixError('beat_mix_budget_exceeded')
    return content.decode('ascii')


def save_plan(plan: BeatSongMixPlan, *, expected_revision: int | None = None) -> BeatSongMixPlan:
    connection = _connection()
    connection.execute('BEGIN IMMEDIATE')
    try:
        body = _body(plan)
        if expected_revision is None:
            if plan.revision != 0:
                raise BeatSongMixError('beat_mix_revision_conflict')
            connection.execute('INSERT INTO beat_song_mix_plans VALUES(?,?,?)', (plan.id, plan.revision, body))
        else:
            if plan.revision != expected_revision + 1:
                raise BeatSongMixError('beat_mix_revision_conflict')
            cursor = connection.execute('UPDATE beat_song_mix_plans SET revision=?,body_json=? WHERE id=? AND revision=?',
                (plan.revision, body, plan.id, expected_revision))
            if cursor.rowcount != 1:
                raise BeatSongMixError('beat_mix_revision_conflict')
        connection.execute('INSERT INTO beat_song_mix_revisions VALUES(?,?,?,?)',
            (plan.id, plan.revision, body, hashlib.sha256(body.encode('ascii')).hexdigest()))
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return plan


def get_plan(plan_id: str, revision: int | None = None) -> BeatSongMixPlan:
    identifier = _id.validate_python(plan_id)
    row = (_connection().execute('SELECT body_json FROM beat_song_mix_plans WHERE id=?', (identifier,)).fetchone()
        if revision is None else _connection().execute('SELECT body_json,sha256 FROM beat_song_mix_revisions WHERE plan_id=? AND revision=?',
            (identifier, revision)).fetchone())
    if row is None:
        raise BeatSongMixError('beat_mix_not_found')
    body: str = row['body_json']
    if revision is not None and hashlib.sha256(body.encode('ascii')).hexdigest() != row['sha256']:
        raise BeatSongMixError('song_storage_unavailable')
    plan = BeatSongMixPlan.model_validate_json(body)
    if plan.id != identifier or revision is not None and plan.revision != revision:
        raise BeatSongMixError('song_storage_unavailable')
    return plan


@dataclass(frozen=True)
class StoredJob:
    job: BeatSongMixJob
    sources: list[SongSourceSnapshot]
    identity: WorkerIdentity | None
    track_id: int | None
    project_id: int | None


def get_job(job_id: str) -> StoredJob:
    row = _connection().execute('SELECT * FROM beat_song_mix_jobs WHERE id=?', (_id.validate_python(job_id),)).fetchone()
    if row is None:
        raise BeatSongMixError('beat_mix_not_found')
    job = BeatSongMixJob.model_validate_json(row['payload_json'])
    snapshots = _snapshots.validate_json(row['sources_json'])
    if (job.id != job_id or job.plan.id != row['plan_id'] or job.plan.revision != row['plan_revision']
            or len(snapshots) != 2 or [item.sha256 for item in snapshots] != [job.plan.beat.sha256, job.plan.song.sha256]):
        raise BeatSongMixError('song_storage_unavailable')
    return StoredJob(job, snapshots, None if row['worker_identity_json'] is None else
        WorkerIdentity.model_validate_json(row['worker_identity_json']), row['track_id'], row['project_id'])


def submission(key: str, checksum: str) -> StoredJob | None:
    row = _connection().execute('SELECT * FROM beat_song_mix_submissions WHERE submission_key=?', (key,)).fetchone()
    if row is None:
        return None
    if row['request_sha256'] != checksum:
        raise BeatSongMixError('beat_mix_submission_conflict')
    return get_job(row['job_id'])


def insert_job(job: BeatSongMixJob, sources: list[SongSourceSnapshot], checksum: str) -> BeatSongMixJob:
    connection = _connection()
    connection.execute('BEGIN IMMEDIATE')
    try:
        previous = submission(job.request.submission_key, checksum)
        if previous is not None:
            connection.commit()
            return previous.job
        current = get_plan(job.plan.id)
        if current.revision != job.plan.revision or current.sha256 != job.plan.sha256:
            raise BeatSongMixError('beat_mix_revision_conflict')
        if connection.execute('SELECT count(*) FROM beat_song_mix_jobs').fetchone()[0] >= 1000:
            raise BeatSongMixError('beat_mix_budget_exceeded')
        connection.execute('INSERT INTO beat_song_mix_jobs(id,plan_id,plan_revision,status,payload_json,sources_json,created_at) VALUES(?,?,?,?,?,?,?)',
            (job.id, job.plan.id, job.plan.revision, job.status, job.model_dump_json(), _snapshots.dump_json(sources).decode(), job.created_at))
        connection.execute('INSERT INTO beat_song_mix_submissions VALUES(?,?,?)', (job.request.submission_key, checksum, job.id))
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return job


def list_jobs(*, active_only: bool = False) -> list[BeatSongMixJob]:
    where = " WHERE status IN('queued','running','cancelling','blocked','saving')" if active_only else ''
    rows = _connection().execute('SELECT payload_json FROM beat_song_mix_jobs' + where +
        (' ORDER BY created_at DESC,rowid DESC LIMIT 1000' if active_only else ' ORDER BY created_at DESC,rowid DESC LIMIT 100')).fetchall()
    return [BeatSongMixJob.model_validate_json(row['payload_json']) for row in rows]


def save_job(job: BeatSongMixJob) -> BeatSongMixJob:
    record = get_job(job.id)
    if record.job.plan != job.plan or record.job.request != job.request:
        raise BeatSongMixError('song_storage_unavailable')
    connection = _connection()
    connection.execute('UPDATE beat_song_mix_jobs SET status=?,payload_json=? WHERE id=?', (job.status, job.model_dump_json(), job.id))
    connection.commit()
    return job


def identity(job_id: str, value: WorkerIdentity | None) -> None:
    connection = _connection()
    cursor = connection.execute('UPDATE beat_song_mix_jobs SET worker_identity_json=? WHERE id=?',
        (None if value is None else value.model_dump_json(), _id.validate_python(job_id)))
    if cursor.rowcount != 1:
        raise BeatSongMixError('beat_mix_not_found')
    connection.commit()


def attach_project(job_id: str, project_id: int) -> None:
    connection = _connection()
    cursor = connection.execute('UPDATE beat_song_mix_jobs SET project_id=? WHERE id=? AND (project_id IS NULL OR project_id=?)',
        (project_id, _id.validate_python(job_id), project_id))
    if cursor.rowcount != 1:
        raise BeatSongMixError('song_save_failed')
    connection.commit()
