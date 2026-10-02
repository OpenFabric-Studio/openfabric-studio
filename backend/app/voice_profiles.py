"""SQLite-backed speech voice profiles with consent and reference audio.

UX pattern inspired by LocalAI voice profiles (MIT); implementation is OpenFabric-native.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .voice_profile_contracts import PatchSpeechVoiceProfileRequest, SpeechVoiceProfile

_ID = re.compile(r"^[0-9a-f]{32}$")
_ALLOWED_EXT = frozenset({"wav", "flac"})
_LOCK = threading.RLock()

# Overridable in tests.
PROFILES_ROOT = DATA_DIR / "voice-profiles"


class VoiceProfileError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def profiles_root() -> Path:
    root = PROFILES_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def _db_path() -> Path:
    return profiles_root() / "profiles.db"


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(_db_path(), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
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


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _row_to_profile(row: sqlite3.Row) -> SpeechVoiceProfile:
    hints_raw = row["engine_hints"]
    hints: dict[str, Any] | None = None
    if isinstance(hints_raw, str) and hints_raw:
        parsed = json.loads(hints_raw)
        if isinstance(parsed, dict):
            hints = parsed
    return SpeechVoiceProfile(
        id=str(row["id"]),
        name=str(row["name"]),
        consent_confirmed=bool(row["consent_confirmed"]),
        reference_audio_path=str(row["reference_audio_path"]),
        notes=str(row["notes"] or ""),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        engine_hints=hints,
    )


def list_profiles() -> list[SpeechVoiceProfile]:
    with _LOCK, closing(_connect()) as connection:
        _ensure_schema(connection)
        rows = connection.execute(
            "SELECT * FROM voice_profiles ORDER BY created_at DESC, id DESC"
        ).fetchall()
        return [_row_to_profile(row) for row in rows]


def get_profile(profile_id: str) -> SpeechVoiceProfile:
    if not _ID.fullmatch(profile_id):
        raise VoiceProfileError("invalid_profile_id", 404)
    with _LOCK, closing(_connect()) as connection:
        _ensure_schema(connection)
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
    engine_hints: dict[str, Any] | None = None,
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

    profile_id = uuid.uuid4().hex
    stamp = _now()
    directory = profiles_root() / profile_id
    directory.mkdir(parents=True, exist_ok=False)
    audio_path = directory / f"reference.{ext}"
    try:
        audio_path.write_bytes(audio_bytes)
        hints_json = json.dumps(engine_hints) if engine_hints is not None else None
        with _LOCK, closing(_connect()) as connection:
            _ensure_schema(connection)
            connection.execute(
                """
                INSERT INTO voice_profiles (
                    id, name, consent_confirmed, reference_audio_path, notes,
                    created_at, updated_at, engine_hints
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    cleaned[:120],
                    str(audio_path),
                    (notes or "")[:2000],
                    stamp,
                    stamp,
                    hints_json,
                ),
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM voice_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            assert row is not None
            return _row_to_profile(row)
    except Exception:
        # Best-effort cleanup of the new directory on failure.
        try:
            if audio_path.exists():
                audio_path.unlink()
            if directory.exists():
                directory.rmdir()
        except OSError:
            pass
        raise


def patch_profile(profile_id: str, body: PatchSpeechVoiceProfileRequest) -> SpeechVoiceProfile:
    if not _ID.fullmatch(profile_id):
        raise VoiceProfileError("invalid_profile_id", 404)
    if body.name is None and body.notes is None and body.consent_confirmed is None:
        raise VoiceProfileError("nothing_to_patch")
    with _LOCK, closing(_connect()) as connection:
        _ensure_schema(connection)
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
        connection.execute(
            """
            UPDATE voice_profiles
            SET name = ?, notes = ?, consent_confirmed = ?, updated_at = ?
            WHERE id = ?
            """,
            (name[:120], notes[:2000], consent, stamp, profile_id),
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
    with _LOCK, closing(_connect()) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            "SELECT reference_audio_path FROM voice_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            raise VoiceProfileError("profile_not_found", 404)
        connection.execute("DELETE FROM voice_profiles WHERE id = ?", (profile_id,))
        connection.commit()
    directory = profiles_root() / profile_id
    if directory.is_dir():
        for child in directory.iterdir():
            if child.is_file() or child.is_symlink():
                child.unlink(missing_ok=True)
        try:
            directory.rmdir()
        except OSError:
            pass
