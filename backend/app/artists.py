"""Append-only artist permission evidence in the existing library catalog."""
from __future__ import annotations

import logging
import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from pydantic import ValidationError

from . import db
from .artist_contracts import (ArtistConsentRequest, ArtistConsentVersion, ArtistOperation,
    ArtistProfile, ArtistUse, CreateArtistRequest)

logger = logging.getLogger(__name__)
_PROFILE_QUERY = '''SELECT artist_profiles.*,
    (SELECT id FROM artist_consent_versions WHERE artist_id=artist_profiles.id
     ORDER BY revision DESC LIMIT 1) AS current_consent_version_id FROM artist_profiles'''


class ArtistError(Exception):
    def __init__(self, code: str, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def migrate(connection: sqlite3.Connection) -> None:
    """Additive startup migration; never reclassify or remove historical data."""
    connection.execute('''CREATE TABLE IF NOT EXISTS artist_profiles (
        id TEXT PRIMARY KEY CHECK(length(id)=32 AND id NOT GLOB '*[^0-9a-f]*'),
        name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
        voice_id TEXT UNIQUE CHECK(voice_id IS NULL OR (length(voice_id)=32 AND voice_id NOT GLOB '*[^0-9a-f]*')),
        created_at TEXT NOT NULL)''')
    connection.execute('''CREATE TABLE IF NOT EXISTS artist_consent_versions (
        id TEXT PRIMARY KEY CHECK(length(id)=32 AND id NOT GLOB '*[^0-9a-f]*'),
        artist_id TEXT NOT NULL REFERENCES artist_profiles(id),
        revision INTEGER NOT NULL CHECK(revision>=1),
        created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        UNIQUE(artist_id,revision))''')
    connection.execute('''CREATE TRIGGER IF NOT EXISTS artist_consent_no_update
        BEFORE UPDATE ON artist_consent_versions BEGIN SELECT RAISE(ABORT,'artist_consent_immutable'); END''')
    connection.execute('''CREATE TRIGGER IF NOT EXISTS artist_consent_no_delete
        BEFORE DELETE ON artist_consent_versions BEGIN SELECT RAISE(ABORT,'artist_consent_immutable'); END''')
    connection.execute('''CREATE TRIGGER IF NOT EXISTS artist_profile_no_delete
        BEFORE DELETE ON artist_profiles BEGIN SELECT RAISE(ABORT,'artist_profile_retained'); END''')
    connection.execute('''CREATE TRIGGER IF NOT EXISTS artist_voice_binding_immutable
        BEFORE UPDATE OF voice_id ON artist_profiles
        WHEN OLD.voice_id IS NOT NULL AND NEW.voice_id IS NOT OLD.voice_id
        BEGIN SELECT RAISE(ABORT,'artist_voice_binding_immutable'); END''')
    connection.execute('''CREATE TRIGGER IF NOT EXISTS artist_identity_immutable
        BEFORE UPDATE OF id,created_at ON artist_profiles
        BEGIN SELECT RAISE(ABORT,'artist_profile_identity_immutable'); END''')
    connection.commit()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _identifier(value: str, code: str) -> None:
    if re.fullmatch(r'[0-9a-f]{32}', value) is None:
        raise ArtistError(code, 404)


@contextmanager
def _write_connection() -> Iterator[sqlite3.Connection]:
    connection: sqlite3.Connection | None = None
    try:
        db.get_db()
        connection = sqlite3.connect(db.DB_PATH, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        with connection:
            # Shared catalog connections cannot own independent transactions.
            connection.execute('BEGIN IMMEDIATE')
            yield connection
    except sqlite3.Error as exc:
        logger.exception('Artist evidence write failed')
        raise ArtistError('artist_storage_failed', 503) from exc
    finally:
        if connection is not None:
            connection.close()


def _read_rows(query: str, parameters: tuple[str, ...] = ()) -> list[sqlite3.Row]:
    try:
        rows: list[sqlite3.Row] = db.get_db().execute(query, parameters).fetchall()
        return rows
    except sqlite3.Error as exc:
        logger.exception('Artist evidence read failed')
        raise ArtistError('artist_storage_failed', 503) from exc


def _profile(row: sqlite3.Row) -> ArtistProfile:
    values: dict[str, object] = {name: row[name] for name in ('id', 'name', 'voice_id', 'created_at', 'current_consent_version_id')}
    try:
        return ArtistProfile.model_validate(values)
    except ValidationError as exc:
        raise ArtistError('artist_metadata_invalid', 409) from exc


def _consent(row: sqlite3.Row) -> ArtistConsentVersion:
    payload: object = row['payload_json']
    try:
        if not isinstance(payload, str):
            raise ValueError('invalid_consent_payload')
        result = ArtistConsentVersion.model_validate_json(payload)
        if result.id != row['id'] or result.artist_id != row['artist_id'] or result.revision != row['revision'] or result.created_at != row['created_at']:
            raise ValueError('invalid_consent_identity')
        return result
    except (ValidationError, ValueError) as exc:
        raise ArtistError('artist_metadata_invalid', 409) from exc


def get_artist(artist_id: str) -> ArtistProfile:
    _identifier(artist_id, 'artist_missing')
    rows = _read_rows(_PROFILE_QUERY + ' WHERE artist_profiles.id=?', (artist_id,))
    if not rows:
        raise ArtistError('artist_missing', 404)
    return _profile(rows[0])


def list_artists() -> list[ArtistProfile]:
    rows = _read_rows(_PROFILE_QUERY + ' ORDER BY created_at,id')
    return [_profile(row) for row in rows]


def create_artist(request: CreateArtistRequest) -> ArtistProfile:
    request = CreateArtistRequest.model_validate_json(request.model_dump_json())
    identifier, created_at = uuid.uuid4().hex, _now().isoformat()
    with _write_connection() as connection:
        if request.voice_id is not None and connection.execute('SELECT id FROM artist_profiles WHERE voice_id=?', (request.voice_id,)).fetchone() is not None:
            raise ArtistError('artist_voice_already_bound')
        connection.execute('INSERT INTO artist_profiles(id,name,voice_id,created_at) VALUES(?,?,?,?)',
            (identifier, request.name, request.voice_id, created_at))
    return get_artist(identifier)


def bind_voice(artist_id: str, voice_id: str) -> ArtistProfile:
    _identifier(artist_id, 'artist_missing')
    _identifier(voice_id, 'artist_voice_invalid')
    with _write_connection() as connection:
        row = connection.execute('SELECT voice_id FROM artist_profiles WHERE id=?', (artist_id,)).fetchone()
        if row is None:
            raise ArtistError('artist_missing', 404)
        current: object = row['voice_id']
        if current is not None and current != voice_id:
            raise ArtistError('artist_voice_binding_immutable')
        existing = connection.execute('SELECT id FROM artist_profiles WHERE voice_id=? AND id!=?', (voice_id, artist_id)).fetchone()
        if existing is not None:
            raise ArtistError('artist_voice_already_bound')
        connection.execute('UPDATE artist_profiles SET voice_id=? WHERE id=?', (voice_id, artist_id))
    return get_artist(artist_id)


def list_consents(artist_id: str) -> list[ArtistConsentVersion]:
    get_artist(artist_id)
    rows = _read_rows('SELECT * FROM artist_consent_versions WHERE artist_id=? ORDER BY revision', (artist_id,))
    return [_consent(row) for row in rows]


def current_consent(artist_id: str) -> ArtistConsentVersion | None:
    get_artist(artist_id)
    rows = _read_rows('SELECT * FROM artist_consent_versions WHERE artist_id=? ORDER BY revision DESC LIMIT 1', (artist_id,))
    return _consent(rows[0]) if rows else None


def add_consent_revision(artist_id: str, request: ArtistConsentRequest) -> ArtistConsentVersion:
    _identifier(artist_id, 'artist_missing')
    request = ArtistConsentRequest.model_validate_json(request.model_dump_json())
    with _write_connection() as connection:
        if connection.execute('SELECT id FROM artist_profiles WHERE id=?', (artist_id,)).fetchone() is None:
            raise ArtistError('artist_missing', 404)
        history = [_consent(row) for row in connection.execute('SELECT * FROM artist_consent_versions WHERE artist_id=? ORDER BY revision', (artist_id,)).fetchall()]
        revoked_revision = max((row.revision for row in history if row.status == 'revoked'), default=0)
        if request.status == 'active' and revoked_revision:
            for prior in history:
                if prior.revision > revoked_revision or not prior.signed_evidence_ref:
                    continue
                same_digest = bool(request.signed_evidence_sha256 and request.signed_evidence_sha256 == prior.signed_evidence_sha256)
                changed_digest = bool(request.signed_evidence_sha256 and prior.signed_evidence_sha256 and request.signed_evidence_sha256 != prior.signed_evidence_sha256)
                if same_digest or (request.signed_evidence_ref == prior.signed_evidence_ref and not changed_digest):
                    raise ArtistError('artist_new_consent_evidence_required')
        version = ArtistConsentVersion(**request.model_dump(), id=uuid.uuid4().hex, artist_id=artist_id,
            revision=(history[-1].revision + 1 if history else 1), created_at=_now().isoformat())
        connection.execute('INSERT INTO artist_consent_versions(id,artist_id,revision,created_at,payload_json) VALUES(?,?,?,?,?)',
            (version.id, artist_id, version.revision, version.created_at, version.model_dump_json()))
    return version


def _authorized(consent: ArtistConsentVersion | None, operation: ArtistOperation, use: ArtistUse,
                territory: str | None) -> ArtistConsentVersion:
    if consent is None:
        raise ArtistError('artist_consent_required', 403)
    if consent.status == 'needs_review':
        raise ArtistError('artist_consent_needs_review', 403)
    if consent.status == 'revoked':
        raise ArtistError('artist_consent_revoked', 403)
    now = _now()
    if consent.starts_at is not None and now < datetime.fromisoformat(consent.starts_at):
        raise ArtistError('artist_consent_not_started', 403)
    if consent.ends_at is not None and now >= datetime.fromisoformat(consent.ends_at):
        raise ArtistError('artist_consent_expired', 403)
    if consent.authorized_operators:
        raise ArtistError('artist_operator_authentication_required', 403)
    if operation not in consent.allowed_operations:
        raise ArtistError('artist_operation_not_allowed', 403)
    if use not in consent.allowed_uses:
        raise ArtistError('artist_use_not_allowed', 403)
    if consent.territories and (territory is None or territory.strip().upper() not in consent.territories):
        raise ArtistError('artist_territory_not_allowed', 403)
    return consent


def require_authorized(artist_id: str, consent_version_id: str, operation: ArtistOperation, *,
                       use: ArtistUse = 'production', territory: str | None = None) -> ArtistConsentVersion:
    consent = current_consent(artist_id)
    if consent is None:
        raise ArtistError('artist_consent_required', 403)
    if consent.id != consent_version_id:
        raise ArtistError('artist_consent_stale', 403)
    return _authorized(consent, operation, use, territory)


def require_authorized_voice(voice_id: str, operation: ArtistOperation, *,
                             use: ArtistUse = 'production', territory: str | None = None) -> ArtistConsentVersion | None:
    """Unregistered historical voices stay legacy; bindings cannot bypass revocation."""
    _identifier(voice_id, 'artist_voice_invalid')
    rows = _read_rows('SELECT id FROM artist_profiles WHERE voice_id=?', (voice_id,))
    if not rows:
        return None
    artist_id: object = rows[0]['id']
    if not isinstance(artist_id, str):
        raise ArtistError('artist_metadata_invalid')
    return _authorized(current_consent(artist_id), operation, use, territory)
