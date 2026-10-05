"""Shared track storage for both models: one SQLite DB (distinguished by a
`model` column) plus generated files split into a subfolder per model under
DATA_DIR/files. Centralizing this here (instead of relying on each model's
own, separate storage - YuE2's built-in history.db, ACE-Step's ephemeral temp
dir) is what makes "one project, one place for everything it generated" true.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

from .config import DATA_DIR
from .contracts import JsonObject

DB_PATH = DATA_DIR / "aicollector.db"
FILES_DIR = DATA_DIR / "files"
VIDEOS_DIR = DATA_DIR / "videos"

_db: Optional[sqlite3.Connection] = None
# Shown on the track list. After 999999 the next track takes the smallest
# free number again, so two saved tracks never share a number.
SHORT_ID_MAX = 999_999


class TrackNumbersFull(Exception):
    """Every number from 1 through 999999 is already on a saved track."""


def next_short_id(last: int, used: set[int]) -> int:
    """The next free track number after last, wrapping to 1 past 999999."""
    if len(used) >= SHORT_ID_MAX:
        raise TrackNumbersFull()
    try:
        cursor = int(last)
    except (TypeError, ValueError):
        cursor = 0
    if cursor < 0:
        cursor = 0
    for _ in range(SHORT_ID_MAX):
        cursor = 1 if cursor >= SHORT_ID_MAX else cursor + 1
        if cursor not in used:
            return cursor
    raise TrackNumbersFull()


def ensure_track_codes(db: sqlite3.Connection) -> None:
    """Give every saved track a short number and remember the last one issued."""
    cols = {row["name"] for row in db.execute("PRAGMA table_info(tracks)").fetchall()}
    if "short_id" not in cols:
        db.execute("ALTER TABLE tracks ADD COLUMN short_id INTEGER")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tracks_short_id ON tracks(short_id)")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS track_code (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_short_id INTEGER NOT NULL
        )
        """
    )
    rows = db.execute("SELECT id, short_id FROM tracks ORDER BY id").fetchall()
    used = {int(row["short_id"]) for row in rows if row["short_id"]}
    last = max(used) if used else 0
    changed = False
    for row in rows:
        if row["short_id"]:
            continue
        preferred = int(row["id"])
        if 1 <= preferred <= SHORT_ID_MAX and preferred not in used:
            chosen = preferred
        else:
            chosen = next_short_id(last, used)
        db.execute("UPDATE tracks SET short_id = ? WHERE id = ?", (chosen, row["id"]))
        used.add(chosen)
        last = max(last, chosen)
        changed = True
    stored = db.execute("SELECT last_short_id FROM track_code WHERE id = 1").fetchone()
    if stored is None:
        db.execute("INSERT INTO track_code (id, last_short_id) VALUES (1, ?)", (last,))
        changed = True
    if changed:
        db.commit()


def take_short_id(db: sqlite3.Connection) -> int:
    """Reserve the next track number. Caller holds the write transaction."""
    row = db.execute("SELECT last_short_id FROM track_code WHERE id = 1").fetchone()
    last = int(row["last_short_id"]) if row else 0
    used = {int(item[0]) for item in db.execute("SELECT short_id FROM tracks WHERE short_id IS NOT NULL")}
    chosen = next_short_id(last, used)
    db.execute(
        "INSERT INTO track_code (id, last_short_id) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET last_short_id = excluded.last_short_id",
        (chosen,),
    )
    return chosen


def get_db() -> sqlite3.Connection:
    global _db
    if _db is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _db = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.execute("PRAGMA journal_mode = WAL;")
        _db.execute("PRAGMA synchronous = NORMAL;")
        _db.execute("PRAGMA foreign_keys = ON;")
        _db.execute("PRAGMA busy_timeout = 5000;")
        _db.execute(
            """
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT NOT NULL,
                created_at TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                lyrics TEXT NOT NULL DEFAULT '',
                seed INTEGER,
                duration_ms REAL,
                wall_ms REAL,
                is_favorite INTEGER NOT NULL DEFAULT 0 CHECK(is_favorite IN (0, 1)),
                params_json TEXT NOT NULL DEFAULT '{}',
                audio_path TEXT NOT NULL,
                abc_path TEXT
            )
            """
        )
        _db.commit()
        cols = {r["name"] for r in _db.execute("PRAGMA table_info(tracks)").fetchall()}
        if "stems_json" not in cols:
            _db.execute("ALTER TABLE tracks ADD COLUMN stems_json TEXT")
            _db.commit()
        if "mix_settings_json" not in cols:
            _db.execute("ALTER TABLE tracks ADD COLUMN mix_settings_json TEXT")
            _db.commit()
        if "midi_json" not in cols:
            _db.execute("ALTER TABLE tracks ADD COLUMN midi_json TEXT")
            _db.commit()
        if "is_favorite" not in cols:
            _db.execute("ALTER TABLE tracks ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0 CHECK(is_favorite IN (0, 1))")
            _db.commit()
        _db.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT 'Untitled project',
                data_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        _db.commit()
        _db.execute("CREATE INDEX IF NOT EXISTS idx_tracks_model_id ON tracks(model, id DESC);")
        _db.execute("CREATE INDEX IF NOT EXISTS idx_tracks_created ON tracks(created_at DESC);")
        _db.execute("CREATE INDEX IF NOT EXISTS idx_projects_updated ON projects(updated_at DESC);")
        _db.commit()
        ensure_track_codes(_db)
        _db.execute("""
            CREATE TABLE IF NOT EXISTS ace_jobs (
                task_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                results_json TEXT,
                created_at TEXT NOT NULL
            )
        """)
        _db.execute("""
            CREATE TABLE IF NOT EXISTS ace_candidates (
                task_id TEXT NOT NULL REFERENCES ace_jobs(task_id) ON DELETE CASCADE,
                candidate_index INTEGER NOT NULL CHECK(candidate_index >= 0),
                track_id INTEGER REFERENCES tracks(id) ON DELETE SET NULL,
                voice_started INTEGER NOT NULL DEFAULT 0 CHECK(voice_started IN (0,1)),
                PRIMARY KEY(task_id, candidate_index)
            )
        """)
        _db.commit()
        try:
            from .audio_version_store import migrate as migrate_audio_versions
            migrate_audio_versions(_db)
            from .generation_library import migrate as migrate_generation_library
            migrate_generation_library(_db)
            from .yue_jobs import migrate as migrate_yue_jobs
            migrate_yue_jobs(_db)
        except BaseException:
            # Do not retain a partly initialized connection after a migration
            # interruption. Additive migrations are safe to retry on next open.
            _db.close()
            _db = None
            raise
    return _db


def videos_dir() -> Path:
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    return VIDEOS_DIR


def model_dir(model: str) -> Path:
    d = FILES_DIR / model
    d.mkdir(parents=True, exist_ok=True)
    return d


def stems_dir(model: str, track_id: int) -> Path:
    return FILES_DIR / model / "stems" / str(track_id)


def midi_dir(model: str, track_id: int) -> Path:
    return FILES_DIR / model / "midi" / str(track_id)


def insert_track(
    *,
    model: str,
    title: str,
    lyrics: str,
    seed: Optional[int],
    duration_ms: Optional[float],
    wall_ms: Optional[float],
    params: JsonObject,
    audio_path: Path,
    abc_path: Optional[Path],
    ace_candidate: tuple[str, int] | None = None,
    audio_version_id: str | None = None,
    yue_job_id: str | None = None,
) -> int:
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        short_id = take_short_id(db)
        cur = db.execute(
            "INSERT INTO tracks (model, created_at, title, lyrics, seed, duration_ms, wall_ms, params_json, audio_path, abc_path, short_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                model,
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                title,
                lyrics,
                seed,
                duration_ms,
                wall_ms,
                json.dumps(params, ensure_ascii=False),
                str(audio_path),
                str(abc_path) if abc_path else None,
                short_id,
            ),
        )
        if cur.lastrowid is None:
            raise RuntimeError("Track insert did not return an ID")
        if ace_candidate is not None:
            db.execute(
                "INSERT INTO ace_candidates (task_id, candidate_index, track_id) VALUES (?, ?, ?)",
                (*ace_candidate, cur.lastrowid),
            )
        if yue_job_id is not None:
            attached_yue = db.execute('UPDATE yue_jobs SET track_id=? WHERE id=? AND track_id IS NULL',
                                      (cur.lastrowid, yue_job_id))
            if attached_yue.rowcount != 1:
                raise ValueError('invalid_yue_job_attachment')
        if audio_version_id is not None:
            attached = db.execute("UPDATE audio_versions SET worker_track_id=? WHERE id=? AND kind='voice' AND worker_track_id IS NULL",
                                  (cur.lastrowid, audio_version_id))
            if attached.rowcount != 1:
                raise ValueError('invalid_audio_version_attachment')
        elif model in ('ace_step', 'yue2'):
            from .generation_library import capture_track
            history_row = db.execute('SELECT * FROM tracks WHERE id=?', (cur.lastrowid,)).fetchone()
            if history_row is None:
                raise RuntimeError('Track insert could not be read for generation history')
            capture_track(db, history_row)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return cur.lastrowid


def list_tracks(model: Optional[str] = None) -> list[sqlite3.Row]:
    db = get_db()
    visible = 'NOT EXISTS (SELECT 1 FROM audio_versions av WHERE av.worker_track_id=tracks.id AND av.track_id!=tracks.id)'
    if model:
        return db.execute(f"SELECT * FROM tracks WHERE model = ? AND {visible} ORDER BY id DESC", (model,)).fetchall()
    return db.execute(f"SELECT * FROM tracks WHERE {visible} ORDER BY id DESC").fetchall()


def get_track(track_id: int) -> Optional[sqlite3.Row]:
    db = get_db()
    return db.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()


def update_track_audio(track_id: int, audio_path: Path) -> bool:
    db = get_db()
    if not get_track(track_id):
        return False
    db.execute("UPDATE tracks SET audio_path = ? WHERE id = ?", (str(audio_path), track_id))
    db.commit()
    return True


def update_track_title(track_id: int, title: str) -> bool:
    db = get_db()
    if not get_track(track_id):
        return False
    db.execute("UPDATE tracks SET title = ? WHERE id = ?", (title, track_id))
    db.commit()
    return True


def set_track_favorite(track_id: int, is_favorite: bool) -> bool:
    connection = get_db()
    cursor = connection.execute(
        "UPDATE tracks SET is_favorite = ? WHERE id = ?", (int(is_favorite), track_id),
    )
    connection.commit()
    return cursor.rowcount == 1


def update_track_stems(track_id: int, stems: Optional[dict[str, str]]) -> None:
    db = get_db()
    db.execute(
        "UPDATE tracks SET stems_json = ? WHERE id = ?",
        (json.dumps(stems, ensure_ascii=False) if stems else None, track_id),
    )
    db.commit()


def get_mix_settings(track_id: int) -> Optional[dict]:
    row = get_track(track_id)
    return json.loads(row["mix_settings_json"]) if row and row["mix_settings_json"] else None


def update_track_mix_settings(track_id: int, settings: Optional[dict]) -> None:
    db = get_db()
    db.execute(
        "UPDATE tracks SET mix_settings_json = ? WHERE id = ?",
        (json.dumps(settings, ensure_ascii=False) if settings else None, track_id),
    )
    db.commit()


def delete_track_stems(track_id: int) -> bool:
    row = get_track(track_id)
    if not row or not row["stems_json"]:
        return False
    update_track_stems(track_id, None)
    from .audio_versions import remove_unshared_track_directory
    remove_unshared_track_directory(None, stems_dir(row["model"], track_id))
    return True


def get_track_midi(track_id: int) -> dict[str, str]:
    row = get_track(track_id)
    return json.loads(row["midi_json"]) if row and row["midi_json"] else {}


def set_track_midi_entry(track_id: int, source: str, path: Optional[Path]) -> None:
    """Add or drop one source's .mid without touching the others - each source
    (full mix, and each stem) is transcribed by its own job."""
    current = get_track_midi(track_id)
    if path is None:
        current.pop(source, None)
    else:
        current[source] = str(path)
    db = get_db()
    db.execute(
        "UPDATE tracks SET midi_json = ? WHERE id = ?",
        (json.dumps(current, ensure_ascii=False) if current else None, track_id),
    )
    db.commit()


def delete_track_midi(track_id: int) -> bool:
    row = get_track(track_id)
    if not row or not row["midi_json"]:
        return False
    db = get_db()
    db.execute("UPDATE tracks SET midi_json = NULL WHERE id = ?", (track_id,))
    db.commit()
    from .audio_versions import remove_unshared_track_directory
    remove_unshared_track_directory(None, midi_dir(row["model"], track_id))
    return True


def insert_project(*, name: str, data: dict) -> int:
    db = get_db()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cur = db.execute(
        "INSERT INTO projects (created_at, updated_at, name, data_json) VALUES (?, ?, ?, ?)",
        (now, now, name, json.dumps(data, ensure_ascii=False)),
    )
    db.commit()
    return cur.lastrowid


def list_projects() -> list[sqlite3.Row]:
    db = get_db()
    return db.execute("SELECT id, created_at, updated_at, name FROM projects ORDER BY updated_at DESC").fetchall()


def get_project(project_id: int) -> Optional[sqlite3.Row]:
    db = get_db()
    return db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()


def update_project(project_id: int, *, name: Optional[str], data: Optional[dict]) -> None:
    db = get_db()
    row = get_project(project_id)
    if not row:
        return
    new_name = name if name is not None else row["name"]
    new_data_json = json.dumps(data, ensure_ascii=False) if data is not None else row["data_json"]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    db.execute(
        "UPDATE projects SET name = ?, data_json = ?, updated_at = ? WHERE id = ?",
        (new_name, new_data_json, now, project_id),
    )
    db.commit()


def delete_project(project_id: int) -> bool:
    db = get_db()
    if not get_project(project_id):
        return False
    db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    db.commit()
    return True


def delete_track(track_id: int) -> bool:
    db = get_db()
    row = get_track(track_id)
    if not row:
        return False
    from .audio_versions import remove_track_files, remove_unshared_track_file, remove_unshared_track_directory
    remove_track_files(track_id)
    for field in ("audio_path", "abc_path"):
        value: object = row[field]
        if isinstance(value, str) and value:
            try:
                remove_unshared_track_file(track_id, Path(value))
            except OSError:
                logging.getLogger(__name__).exception('Could not clean an artifact for track %s', track_id)
    model: object = row["model"]
    if isinstance(model, str):
        for field, folder in (("stems_json", stems_dir(model, track_id)),
                              ("midi_json", midi_dir(model, track_id))):
            if row[field]:
                remove_unshared_track_directory(track_id, folder)
    db.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
    db.commit()
    return True
