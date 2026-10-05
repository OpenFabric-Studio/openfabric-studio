"""SQLite-backed speech voice profiles with consent and reference audio.

UX pattern inspired by LocalAI voice profiles (MIT); implementation is OpenFabric-native.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from pydantic import TypeAdapter, ValidationError

from .config import DATA_DIR
from .voice_profile_contracts import EngineHintMap, PatchSpeechVoiceProfileRequest, SpeechVoiceProfile

_ID = re.compile(r"^[0-9a-f]{32}$")
_ALLOWED_EXT = frozenset({"wav", "flac"})
_LOCK = threading.RLock()
_STARTER_ID = re.compile(r"^vctk-p[0-9]{3}$")
_HINT_ADAPTER: TypeAdapter[EngineHintMap] = TypeAdapter(EngineHintMap)
_LOG = logging.getLogger(__name__)

# Overridable in tests.
PROFILES_ROOT = DATA_DIR / "voice-profiles"


class VoiceProfileError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def profiles_root() -> Path:
    root = PROFILES_ROOT
    if root.is_symlink():
        raise VoiceProfileError("profile_storage_unavailable", 503)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _db_path() -> Path:
    root = profiles_root()
    path = root / "profiles.db"
    if path.is_symlink() or path.resolve().parent != root.resolve():
        raise VoiceProfileError("profile_storage_unavailable", 503)
    return path


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(_db_path(), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == 2:
        return
    if version not in (0, 1):
        raise VoiceProfileError("profile_storage_unavailable", 503)
    # SQLite protects the entire schema upgrade, including concurrent processes.
    connection.execute("BEGIN IMMEDIATE")
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 2:
            connection.commit()
            return
        if version not in (0, 1):
            raise VoiceProfileError("profile_storage_unavailable", 503)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                consent_confirmed INTEGER NOT NULL CHECK (consent_confirmed IN (0, 1)),
                reference_audio_path TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                engine_hints TEXT
            )
            """
        )
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(voice_profiles)")}
        if "starter_voice_id" not in columns:
            connection.execute(
                """ALTER TABLE voice_profiles ADD COLUMN starter_voice_id TEXT
                CHECK (starter_voice_id IS NULL OR
                    (length(starter_voice_id) = 9 AND starter_voice_id GLOB 'vctk-p[0-9][0-9][0-9]'))"""
            )
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS voice_profiles_starter_voice_id
            ON voice_profiles(starter_voice_id) WHERE starter_voice_id IS NOT NULL"""
        )
        if "reference_transcript" not in columns:
            connection.execute("ALTER TABLE voice_profiles ADD COLUMN reference_transcript TEXT NOT NULL DEFAULT ''")
            connection.execute("UPDATE voice_profiles SET reference_transcript = notes")
        if "reference_language" not in columns:
            connection.execute("ALTER TABLE voice_profiles ADD COLUMN reference_language TEXT NOT NULL DEFAULT 'en'")
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


@contextmanager
def _profile_store() -> Iterator[sqlite3.Connection]:
    try:
        with _LOCK, closing(_connect()) as connection:
            _ensure_schema(connection)
            yield connection
    except (OSError, sqlite3.Error, ValidationError) as exc:
        _LOG.warning("Speech profile storage unavailable: %s", exc)
        raise VoiceProfileError("profile_storage_unavailable", 503) from exc


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _row_to_profile(row: sqlite3.Row) -> SpeechVoiceProfile:
    hints_raw = row["engine_hints"]
    hints: EngineHintMap | None = None
    if isinstance(hints_raw, str) and hints_raw:
        hints = _HINT_ADAPTER.validate_json(hints_raw)
    return SpeechVoiceProfile(
        id=str(row["id"]),
        name=str(row["name"]),
        consent_confirmed=bool(row["consent_confirmed"]),
        reference_audio_path=str(row["reference_audio_path"]),
        notes=str(row["notes"] or ""),
        reference_transcript=str(row["reference_transcript"] or ""),
        reference_language=str(row["reference_language"] or "en"),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        engine_hints=hints,
        starter_voice_id=str(row["starter_voice_id"]) if row["starter_voice_id"] is not None else None,
    )


def list_profiles() -> list[SpeechVoiceProfile]:
    with _profile_store() as connection:
        rows = connection.execute(
            "SELECT * FROM voice_profiles ORDER BY created_at DESC, id DESC"
        ).fetchall()
        return [_row_to_profile(row) for row in rows]


def get_profile(profile_id: str) -> SpeechVoiceProfile:
    if not _ID.fullmatch(profile_id):
        raise VoiceProfileError("invalid_profile_id", 404)
    with _profile_store() as connection:
        row = connection.execute(
            "SELECT * FROM voice_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if row is None:
            raise VoiceProfileError("profile_not_found", 404)
        return _row_to_profile(row)


def create_profile(
    *,
    name: str,
    consent_confirmed: bool,
    audio_bytes: bytes,
    filename: str,
    notes: str = "",
    engine_hints: EngineHintMap | None = None,
    starter_voice_id: str | None = None,
    reference_transcript: str = "",
    reference_language: str = "en",
) -> SpeechVoiceProfile:
    cleaned = (name or "").strip()
    if not cleaned:
        raise VoiceProfileError("name_required")
    if not consent_confirmed:
        raise VoiceProfileError("consent_required")
    if not audio_bytes:
        raise VoiceProfileError("audio_required")
    safe_name = Path(filename.replace("\\", "/")).name
    ext = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    if ext not in _ALLOWED_EXT:
        raise VoiceProfileError("unsupported_audio_type")
    if starter_voice_id is not None and not _STARTER_ID.fullmatch(starter_voice_id):
        raise VoiceProfileError("invalid_starter_voice_id")

    directory: Path | None = None
    audio_path: Path | None = None
    committed = False
    try:
        with _profile_store() as connection:
            # Serializes lookup + new copy + insert across independent workers too.
            connection.execute("BEGIN IMMEDIATE")
            if starter_voice_id is not None:
                existing = connection.execute(
                    "SELECT * FROM voice_profiles WHERE starter_voice_id = ?", (starter_voice_id,)
                ).fetchone()
                if existing is not None:
                    return _row_to_profile(existing)
            profile_id = uuid.uuid4().hex
            stamp = _now()
            new_directory = profiles_root() / profile_id
            new_directory.mkdir(parents=True, exist_ok=False)
            directory = new_directory
            audio_path = directory / f"reference.{ext}"
            audio_path.write_bytes(audio_bytes)
            hints_json = json.dumps(engine_hints) if engine_hints is not None else None
            connection.execute(
                """
                INSERT INTO voice_profiles (
                    id, name, consent_confirmed, reference_audio_path, notes,
                    created_at, updated_at, engine_hints, starter_voice_id, reference_transcript, reference_language
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    cleaned[:120],
                    str(audio_path),
                    (notes or "")[:2000],
                    stamp,
                    stamp,
                    hints_json,
                    starter_voice_id,
                    reference_transcript.strip(),
                    reference_language,
                ),
            )
            row = connection.execute(
                "SELECT * FROM voice_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            assert row is not None
            profile = _row_to_profile(row)
            connection.commit()
            committed = True
            return profile
    except Exception:
        # Best-effort cleanup of the new directory on failure.
        try:
            if not committed and audio_path is not None and audio_path.exists():
                audio_path.unlink()
            if not committed and directory is not None and directory.exists():
                directory.rmdir()
        except OSError:
            pass
        raise


def patch_profile(profile_id: str, body: PatchSpeechVoiceProfileRequest) -> SpeechVoiceProfile:
    if not _ID.fullmatch(profile_id):
        raise VoiceProfileError("invalid_profile_id", 404)
    if all(value is None for value in (body.name, body.notes, body.consent_confirmed, body.reference_transcript, body.reference_language)):
        raise VoiceProfileError("nothing_to_patch")
    with _profile_store() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM voice_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if row is None:
            raise VoiceProfileError("profile_not_found", 404)
        name = body.name.strip() if body.name is not None else str(row["name"])
        if not name:
            raise VoiceProfileError("name_required")
        notes = body.notes if body.notes is not None else str(row["notes"] or "")
        consent = (
            int(bool(body.consent_confirmed))
            if body.consent_confirmed is not None
            else int(row["consent_confirmed"])
        )
        stamp = _now()
        transcript = body.reference_transcript if body.reference_transcript is not None else str(row["reference_transcript"] or "")
        language = body.reference_language if body.reference_language is not None else str(row["reference_language"] or "en")
        connection.execute(
            """
            UPDATE voice_profiles
            SET name = ?, notes = ?, consent_confirmed = ?, updated_at = ?, reference_transcript = ?, reference_language = ?
            WHERE id = ?
            """,
            (name[:120], notes[:2000], consent, stamp, transcript.strip(), language, profile_id),
        )
        connection.commit()
        updated = connection.execute(
            "SELECT * FROM voice_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        assert updated is not None
        return _row_to_profile(updated)


def delete_profile(profile_id: str) -> None:
    if not _ID.fullmatch(profile_id):
        raise VoiceProfileError("invalid_profile_id", 404)
    with _profile_store() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT reference_audio_path FROM voice_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            raise VoiceProfileError("profile_not_found", 404)
        directory = profiles_root() / profile_id
        if directory.is_symlink() or directory.resolve().parent != profiles_root().resolve():
            raise VoiceProfileError("profile_storage_unavailable", 503)
        connection.execute("DELETE FROM voice_profiles WHERE id = ?", (profile_id,))
        connection.commit()
    if directory.is_dir():
        for child in directory.iterdir():
            if child.is_file() or child.is_symlink():
                child.unlink(missing_ok=True)
        try:
            directory.rmdir()
        except OSError:
            pass
