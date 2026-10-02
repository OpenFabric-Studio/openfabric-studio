"""Constrained SQLite persistence for immutable track audio versions."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .audio_version_contracts import AudioVersion
from .contracts import JobStatus


@dataclass(frozen=True)
class StoredAudioVersion:
    public: AudioVersion
    path: Path | None
    worker_track_id: int | None
    captured_source: Path | None = None


def migrate(connection: sqlite3.Connection) -> None:
    """Idempotent additive migration; existing track rows are left intact."""
    connection.execute('''CREATE TABLE IF NOT EXISTS audio_versions (
        id TEXT PRIMARY KEY CHECK(length(id)=32 AND id NOT GLOB '*[^0-9a-f]*'),
        track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK(kind IN ('original','voice')),
        voice_id TEXT CHECK(voice_id IS NULL OR (length(voice_id)=32 AND voice_id NOT GLOB '*[^0-9a-f]*')),
        voice_name TEXT,
        status TEXT NOT NULL CHECK(status IN ('queued','running','done','failed','cancelled')),
        created_at TEXT NOT NULL,
        audio_path TEXT,
        error_code TEXT NOT NULL DEFAULT '',
        duration_ms REAL CHECK(duration_ms IS NULL OR duration_ms>=0),
        source_version_id TEXT REFERENCES audio_versions(id) ON DELETE SET NULL,
        worker_track_id INTEGER REFERENCES tracks(id) ON DELETE SET NULL,
        captured_source_path TEXT,
        CHECK((kind='original' AND voice_id IS NULL AND source_version_id IS NULL) OR kind='voice')
    )''')
    columns = {row['name'] for row in connection.execute('PRAGMA table_info(audio_versions)').fetchall()}
    if 'captured_source_path' not in columns:
        connection.execute('ALTER TABLE audio_versions ADD COLUMN captured_source_path TEXT')
    connection.execute('CREATE INDEX IF NOT EXISTS idx_audio_versions_track ON audio_versions(track_id,created_at,id)')
    connection.execute('CREATE INDEX IF NOT EXISTS idx_audio_versions_worker ON audio_versions(worker_track_id)')
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_audio_versions_original ON audio_versions(track_id) WHERE kind='original'")
    connection.execute('''CREATE TABLE IF NOT EXISTS audio_version_schema (
        id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL CHECK(version=1))''')
    connection.execute('INSERT OR IGNORE INTO audio_version_schema(id,version) VALUES(1,1)')
    connection.commit()


def _parse(row: sqlite3.Row) -> StoredAudioVersion:
    values: dict[str, object] = {name: row[name] for name in (
        'id', 'track_id', 'kind', 'voice_id', 'voice_name', 'status', 'created_at', 'error_code',
        'duration_ms', 'source_version_id')}
    public = AudioVersion.model_validate(values)
    raw_path: object = row['audio_path']
    raw_worker: object = row['worker_track_id']
    raw_source: object = row['captured_source_path']
    if raw_path is not None and not isinstance(raw_path, str):
        raise ValueError('invalid_version_path')
    if raw_worker is not None and (not isinstance(raw_worker, int) or isinstance(raw_worker, bool) or raw_worker <= 0):
        raise ValueError('invalid_version_worker')
    if raw_source is not None and not isinstance(raw_source, str):
        raise ValueError('invalid_version_source')
    return StoredAudioVersion(public, Path(raw_path) if raw_path is not None else None, raw_worker,
                              Path(raw_source) if raw_source is not None else None)


def get(connection: sqlite3.Connection, version_id: str) -> StoredAudioVersion | None:
    row = connection.execute('SELECT * FROM audio_versions WHERE id=?', (version_id,)).fetchone()
    return _parse(row) if row is not None else None


def list_for_track(connection: sqlite3.Connection, track_id: int) -> list[StoredAudioVersion]:
    rows = connection.execute("SELECT * FROM audio_versions WHERE track_id=? ORDER BY CASE kind WHEN 'original' THEN 0 ELSE 1 END,created_at,id", (track_id,)).fetchall()
    return [_parse(row) for row in rows]


def list_active(connection: sqlite3.Connection) -> list[StoredAudioVersion]:
    return [_parse(row) for row in connection.execute("SELECT * FROM audio_versions WHERE status IN ('queued','running')").fetchall()]


def insert(connection: sqlite3.Connection, record: StoredAudioVersion) -> None:
    version = record.public
    connection.execute('''INSERT INTO audio_versions
        (id,track_id,kind,voice_id,voice_name,status,created_at,audio_path,error_code,duration_ms,source_version_id,worker_track_id,captured_source_path)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
        version.id, version.track_id, version.kind, version.voice_id, version.voice_name, version.status,
        version.created_at, str(record.path) if record.path is not None else None, version.error_code,
        version.duration_ms, version.source_version_id, record.worker_track_id,
        str(record.captured_source) if record.captured_source is not None else None))
    connection.commit()


def set_state(connection: sqlite3.Connection, version_id: str, status: JobStatus, error_code: str) -> None:
    connection.execute('UPDATE audio_versions SET status=?,error_code=? WHERE id=?', (status,error_code,version_id))
    connection.commit()


def remove(connection: sqlite3.Connection, version_id: str) -> None:
    connection.execute('DELETE FROM audio_versions WHERE id=?', (version_id,))
    connection.commit()
