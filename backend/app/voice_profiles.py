"""SQLite-backed speech voice profiles with consent and reference audio.

UX pattern inspired by LocalAI voice profiles (MIT); implementation is OpenFabric-native.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import tempfile
import threading
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal

import numpy as np
import soundfile as sf
from pydantic import TypeAdapter, ValidationError

from .config import DATA_DIR
from .voice_profile_contracts import CloudSpeechConfiguration, CreateCloudSpeechVoiceProfileRequest, EngineHintMap, PatchSpeechVoiceProfileRequest, SpeechVoiceProfile

_ID = re.compile(r"^[0-9a-f]{32}$")
_ALLOWED_EXT = frozenset({"wav", "flac"})
_LOCK = threading.RLock()
_STARTER_ID = re.compile(r"^vctk-p[0-9]{3}$")
_HINT_ADAPTER: TypeAdapter[EngineHintMap] = TypeAdapter(EngineHintMap)
_LOG = logging.getLogger(__name__)

# References are short clips: bound encoded bytes and decoded work separately.
MAX_REFERENCE_AUDIO_BYTES = 32 * 1024 * 1024
MAX_REFERENCE_SECONDS = 120
MAX_REFERENCE_CHANNELS = 8
MAX_REFERENCE_SAMPLE_RATE = 192000
_DECODE_CHUNK_FRAMES = 65536

# Overridable in tests.
PROFILES_ROOT = DATA_DIR / "voice-profiles"


class VoiceProfileError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


class _SequentialSoundFile(sf.SoundFile):
    # SoundFile seeks after every read; unknown-length FLAC cannot seek to EOF.
    def seekable(self) -> bool:
        return False


def _validate_wav_chunks(audio: bytes) -> None:
    """libsndfile clamps truncated PCM, so verify declared RIFF chunks too."""
    if len(audio) < 12 or audio[:4] not in (b"RIFF", b"RIFX", b"RF64") or audio[8:12] != b"WAVE":
        raise ValueError("invalid WAVE header")
    order: Literal["big", "little"] = "big" if audio[:4] == b"RIFX" else "little"
    end = int.from_bytes(audio[4:8], order) + 8
    extended_sizes: dict[bytes, int] = {}
    if audio[:4] == b"RF64":
        if len(audio) < 48 or audio[12:16] != b"ds64":
            raise ValueError("missing RF64 sizes")
        size = int.from_bytes(audio[16:20], "little")
        entries = int.from_bytes(audio[44:48], "little")
        if size < 28 + entries * 12 or 20 + size > len(audio):
            raise ValueError("truncated RF64 sizes")
        end = int.from_bytes(audio[20:28], "little") + 8
        extended_sizes[b"data"] = int.from_bytes(audio[28:36], "little")
        for position in range(48, 48 + entries * 12, 12):
            extended_sizes[audio[position:position + 4]] = int.from_bytes(audio[position + 4:position + 12], "little")
    if end < 12 or end > len(audio):
        raise ValueError("truncated RIFF")
    position = 12
    pcm_alignment = 0
    while position < end:
        if position + 8 > end:
            raise ValueError("truncated chunk header")
        tag = audio[position:position + 4]
        size = int.from_bytes(audio[position + 4:position + 8], order)
        if size == 0xffffffff:
            if tag not in extended_sizes:
                raise ValueError("missing RF64 chunk size")
            size = extended_sizes[tag]
        start = position + 8
        stop = start + size
        if stop > end:
            raise ValueError("truncated chunk")
        if tag == b"fmt ":
            if size < 16:
                raise ValueError("truncated format")
            encoding = int.from_bytes(audio[start:start + 2], order)
            if encoding == 0xfffe and size >= 40:
                encoding = int.from_bytes(audio[start + 24:start + 26], order)
            if encoding in (1, 3):
                pcm_alignment = int.from_bytes(audio[start + 12:start + 14], order)
                channels = int.from_bytes(audio[start + 2:start + 4], order)
                bits = int.from_bytes(audio[start + 14:start + 16], order)
                if channels == 0 or bits == 0 or bits % 8 or pcm_alignment != channels * (bits // 8):
                    raise ValueError("invalid PCM alignment")
        if tag == b"data" and pcm_alignment and size % pcm_alignment:
            raise ValueError("incomplete PCM frame")
        # Python's wave writer omits the final odd data chunk's padding byte.
        position = stop + (size % 2 if stop < end else 0)


def _validate_reference_audio(audio: bytes, extension: str) -> None:
    if len(audio) > MAX_REFERENCE_AUDIO_BYTES:
        raise VoiceProfileError("audio_too_large", 413)
    try:
        if extension == "wav":
            _validate_wav_chunks(audio)
        with tempfile.TemporaryDirectory(prefix="openfabric-reference-") as temporary:
            path = Path(temporary) / f"reference.{extension}"
            path.write_bytes(audio)
            info = sf.info(path)
            formats = {"WAV", "WAVEX", "RF64"} if extension == "wav" else {"FLAC"}
            if info.format not in formats or info.frames <= 0 or info.samplerate <= 0 or info.channels <= 0:
                raise ValueError("invalid audio metadata")
            limit = MAX_REFERENCE_SECONDS * info.samplerate
            if info.channels > MAX_REFERENCE_CHANNELS or info.samplerate > MAX_REFERENCE_SAMPLE_RATE:
                raise VoiceProfileError("audio_decode_limit")
            if extension == "flac":
                if len(audio) < 42 or audio[:4] != b"fLaC" or audio[4] & 0x7f or audio[5:8] != b"\0\0\x22":
                    raise ValueError("invalid FLAC stream info")
                packed = int.from_bytes(audio[18:26], "big")
                declared = packed & ((1 << 36) - 1)
                # Downstream speech readers allocate from the original frame count.
                if declared == 0:
                    raise ValueError("unknown FLAC frame count")
                if declared > limit:
                    raise VoiceProfileError("audio_decode_limit")
                # Ignore the untrusted frame count only in the temporary decoding copy.
                path.write_bytes(audio[:18] + (packed >> 36 << 36).to_bytes(8, "big") + audio[26:])
                with _SequentialSoundFile(path) as source:
                    decoded = 0
                    while True:
                        samples = source.read(min(_DECODE_CHUNK_FRAMES, limit - decoded + 1), dtype="float32", always_2d=True)
                        if samples.shape[1] != info.channels or not np.isfinite(samples).all():
                            raise ValueError("invalid decoded audio")
                        decoded += len(samples)
                        if decoded > limit:
                            raise VoiceProfileError("audio_decode_limit")
                        if len(samples) == 0:
                            break
                    if decoded != declared:
                        raise ValueError("incomplete decoded audio")
            else:
                if info.frames > limit:
                    raise VoiceProfileError("audio_decode_limit")
                for start in range(0, info.frames, _DECODE_CHUNK_FRAMES):
                    count = min(_DECODE_CHUNK_FRAMES, info.frames - start)
                    samples, rate = sf.read(path, start=start, frames=count, dtype="float32", always_2d=True)
                    if (rate != info.samplerate or samples.shape != (count, info.channels)
                            or not np.isfinite(samples).all()):
                        raise ValueError("incomplete or invalid decoded audio")
    except OSError as exc:
        _LOG.warning("Speech reference validation storage unavailable: %s", exc)
        raise VoiceProfileError("profile_storage_unavailable", 503) from exc
    except (RuntimeError, ValueError) as exc:
        _LOG.warning("Invalid speech reference audio: %s", exc)
        raise VoiceProfileError("invalid_audio") from exc


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
    if version == 3:
        return
    if version not in (0, 1, 2):
        raise VoiceProfileError("profile_storage_unavailable", 503)
    # SQLite protects the entire schema upgrade, including concurrent processes.
    connection.execute("BEGIN IMMEDIATE")
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 3:
            connection.commit()
            return
        if version not in (0, 1, 2):
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
        if "renderer" not in columns:
            connection.execute("ALTER TABLE voice_profiles ADD COLUMN renderer TEXT NOT NULL DEFAULT 'local' CHECK(renderer IN ('local','openrouter'))")
        if "cloud_json" not in columns:
            connection.execute("ALTER TABLE voice_profiles ADD COLUMN cloud_json TEXT")
        connection.execute("PRAGMA user_version = 3")
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
        renderer=row["renderer"],
        cloud=CloudSpeechConfiguration.model_validate_json(str(row["cloud_json"])) if row["cloud_json"] else None,
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
    _validate_reference_audio(audio_bytes, ext)

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


def create_cloud_profile(body: CreateCloudSpeechVoiceProfileRequest) -> SpeechVoiceProfile:
    """Create a provider preset without storing or pretending to own a recording."""
    from .cloud_speech import validate_configuration
    cloud = CloudSpeechConfiguration(model=body.model, voice=body.voice)
    validate_configuration(cloud)
    name = body.name.strip()
    if not name:
        raise VoiceProfileError("name_required")
    directory: Path | None = None
    committed = False
    try:
        with _profile_store() as connection:
            connection.execute("BEGIN IMMEDIATE")
            identifier, stamp = uuid.uuid4().hex, _now()
            directory = profiles_root() / identifier
            directory.mkdir(exist_ok=False)
            connection.execute("""INSERT INTO voice_profiles
                (id,name,consent_confirmed,reference_audio_path,notes,created_at,updated_at,renderer,cloud_json)
                VALUES(?,?,1,'',?,?,?,'openrouter',?)""",
                (identifier, name, body.notes, stamp, stamp, cloud.model_dump_json()))
            row = connection.execute("SELECT * FROM voice_profiles WHERE id=?", (identifier,)).fetchone()
            assert row is not None
            profile = _row_to_profile(row)
            connection.commit()
            committed = True
            return profile
    finally:
        if not committed and directory is not None:
            directory.rmdir()


def patch_profile(profile_id: str, body: PatchSpeechVoiceProfileRequest) -> SpeechVoiceProfile:
    if not _ID.fullmatch(profile_id):
        raise VoiceProfileError("invalid_profile_id", 404)
    if all(value is None for value in (body.name, body.notes, body.consent_confirmed, body.reference_transcript, body.reference_language, body.renderer, body.cloud)):
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
        renderer = body.renderer or str(row["renderer"])
        cloud = body.cloud or (CloudSpeechConfiguration.model_validate_json(str(row["cloud_json"])) if row["cloud_json"] else None)
        if renderer == "openrouter":
            if cloud is None:
                raise VoiceProfileError("cloud_speech_configuration_required")
            revoking = row["renderer"] == "openrouter" and cloud.clone_reference and not cloud.reference_transfer_confirmed and body.cloud is not None
            if cloud.clone_reference and not cloud.reference_transfer_confirmed and not revoking:
                raise VoiceProfileError("cloud_reference_permission_required", 403)
            if cloud.clone_reference and not str(row["reference_audio_path"]):
                raise VoiceProfileError("audio_required")
            from .cloud_speech import validate_configuration
            if not revoking:
                validate_configuration(cloud)
        elif not str(row["reference_audio_path"]):
            raise VoiceProfileError("audio_required")
        stamp = _now()
        transcript = body.reference_transcript if body.reference_transcript is not None else str(row["reference_transcript"] or "")
        language = body.reference_language if body.reference_language is not None else str(row["reference_language"] or "en")
        connection.execute(
            """
            UPDATE voice_profiles
            SET name = ?, notes = ?, consent_confirmed = ?, updated_at = ?, reference_transcript = ?, reference_language = ?, renderer = ?, cloud_json = ?
            WHERE id = ?
            """,
            (name[:120], notes[:2000], consent, stamp, transcript.strip(), language, renderer, cloud.model_dump_json() if cloud else None, profile_id),
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
