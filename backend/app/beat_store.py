"""CAS beat documents and immutable revisions in the existing SQLite library."""
from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
import uuid

from pydantic import TypeAdapter, ValidationError

from . import db
from .beat_compile import event_fraction
from .beat_contracts import (BeatCreateRequest, BeatDocument, BeatDocumentSummary, BeatError,
    BeatHumanizeRequest, BeatId, BeatLane, BeatMeter, BeatPattern, BeatRevisionInfo, BeatStep,
    BeatVariationRequest, PatternPlacement, canonical_json, document_sha256, manifest_sha256)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def migrate(connection: sqlite3.Connection) -> None:
    """Atomic additive migration; never commit a caller's surrounding transaction."""
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='beat_schema_version'").fetchone()
    if exists is not None:
        row = connection.execute('SELECT version FROM beat_schema_version WHERE id=1').fetchone()
        if row is not None and row[0] > 1:
            raise BeatError('beat_storage_unavailable')
    connection.execute('SAVEPOINT beat_migration')
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS beat_schema_version (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL CHECK(version>=1))')
        connection.execute("CREATE TABLE IF NOT EXISTS beat_documents (id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK(revision>=0), body_json TEXT NOT NULL CHECK(json_valid(body_json)), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)))")
        connection.execute('CREATE TABLE IF NOT EXISTS beat_revisions (beat_id TEXT NOT NULL REFERENCES beat_documents(id), revision INTEGER NOT NULL CHECK(revision>=0), body_json TEXT NOT NULL CHECK(json_valid(body_json)), body_sha256 TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(beat_id,revision))')
        connection.execute('CREATE TABLE IF NOT EXISTS beat_render_jobs (id TEXT PRIMARY KEY, beat_id TEXT NOT NULL REFERENCES beat_documents(id), beat_revision INTEGER NOT NULL, request_hash TEXT NOT NULL, payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), document_json TEXT NOT NULL CHECK(json_valid(document_json)), kit_json TEXT NOT NULL CHECK(json_valid(kit_json)), worker_identity_json TEXT CHECK(worker_identity_json IS NULL OR json_valid(worker_identity_json)), created_at TEXT NOT NULL, track_id INTEGER UNIQUE REFERENCES tracks(id) ON DELETE SET NULL, FOREIGN KEY(beat_id,beat_revision) REFERENCES beat_revisions(beat_id,revision))')
        connection.execute('CREATE INDEX IF NOT EXISTS beat_render_jobs_beat ON beat_render_jobs(beat_id,created_at)')
        connection.execute('CREATE TABLE IF NOT EXISTS beat_submissions (submission_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES beat_render_jobs(id))')
        connection.execute('INSERT OR IGNORE INTO beat_schema_version(id,version) VALUES(1,1)')
        connection.execute('RELEASE beat_migration')
    except BaseException:
        connection.execute('ROLLBACK TO beat_migration')
        connection.execute('RELEASE beat_migration')
        raise


def _connection() -> sqlite3.Connection:
    return db.get_db()


def _identifier(value: str) -> str:
    try:
        return TypeAdapter(BeatId).validate_python(value)
    except ValidationError as exc:
        raise BeatError('beat_pattern_invalid') from exc


def _owned(document: BeatDocument, identifier: str, revision: int) -> BeatDocument:
    try:
        return BeatDocument.model_validate({**document.model_dump(), 'id': identifier, 'revision': revision})
    except ValidationError as exc:
        raise BeatError('beat_pattern_invalid') from exc


def _revision(connection: sqlite3.Connection, document: BeatDocument, timestamp: str) -> None:
    connection.execute('INSERT INTO beat_revisions(beat_id,revision,body_json,body_sha256,created_at) VALUES(?,?,?,?,?)',
        (document.id, document.revision, canonical_json(document).decode('ascii'), document_sha256(document), timestamp))


def create(document: BeatDocument) -> BeatDocument:
    document = _owned(document, uuid.uuid4().hex, 0)
    connection, timestamp = _connection(), now()
    connection.execute('BEGIN IMMEDIATE')
    try:
        connection.execute('INSERT INTO beat_documents(id,revision,body_json,created_at,updated_at) VALUES(?,?,?,?,?)',
            (document.id, 0, canonical_json(document).decode('ascii'), timestamp, timestamp))
        _revision(connection, document, timestamp)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return document


def create_document(request: BeatCreateRequest) -> BeatDocument:
    from .beat_render import load_kit
    from .beat_compile import compile_events
    if request.document is not None:
        candidate = request.document
        kit = load_kit(candidate.kit_id, candidate.kit_manifest_sha256)
        compile_events(candidate, 48000, kit)
        return create(candidate)
    kit = load_kit(request.kit_id)
    lanes: list[BeatLane] = []
    for index in range(8):
        instrument = kit.instruments[index % len(kit.instruments)]
        indexes = [0, 8] if index == 0 else [4, 12] if index == 1 else list(range(0, 16, 2)) if index == 2 else []
        lanes.append(BeatLane(lane_id=f'lane-{index}', label=instrument.label, instrument_id=instrument.instrument_id,
            role=instrument.role, steps=[BeatStep(index=step, velocity=100 if index < 2 else 70) for step in indexes]))
    candidate = BeatDocument(id=uuid.uuid4().hex, revision=0, title=request.title, bpm=request.bpm, seed=request.seed,
        kit_id=kit.kit_id, kit_manifest_sha256=manifest_sha256(kit),
        patterns=[BeatPattern(id='A', meter=BeatMeter(numerator=4, denominator=4), lanes=lanes)],
        chain=[PatternPlacement(pattern_id='A')])
    compile_events(candidate, 48000, kit)
    return create(candidate)


def get(identifier: str) -> BeatDocument:
    row = _connection().execute('SELECT body_json FROM beat_documents WHERE id=? AND archived=0', (_identifier(identifier),)).fetchone()
    if row is None:
        raise BeatError('beat_not_found')
    return BeatDocument.model_validate_json(row['body_json'])


def get_revision(identifier: str, revision: int) -> BeatDocument:
    row = _connection().execute('SELECT body_json,body_sha256 FROM beat_revisions WHERE beat_id=? AND revision=?',
        (_identifier(identifier), revision)).fetchone()
    if row is None:
        raise BeatError('beat_not_found')
    document = BeatDocument.model_validate_json(row['body_json'])
    if document_sha256(document) != row['body_sha256'] or document.id != identifier or document.revision != revision:
        raise BeatError('beat_storage_unavailable')
    return document


def list_documents() -> list[BeatDocumentSummary]:
    rows = _connection().execute('SELECT id,revision,body_json,updated_at FROM beat_documents WHERE archived=0 ORDER BY updated_at DESC,rowid DESC LIMIT 1000').fetchall()
    result: list[BeatDocumentSummary] = []
    for row in rows:
        document = BeatDocument.model_validate_json(row['body_json'])
        result.append(BeatDocumentSummary(id=document.id, revision=document.revision, title=document.title,
            bpm=document.bpm, updated_at=row['updated_at']))
    return result


def revisions(identifier: str) -> list[BeatRevisionInfo]:
    rows = _connection().execute('SELECT revision,body_json,body_sha256,created_at FROM beat_revisions WHERE beat_id=? ORDER BY revision DESC LIMIT 1000', (_identifier(identifier),)).fetchall()
    return [BeatRevisionInfo(revision=row['revision'], document_sha256=row['body_sha256'],
        title=BeatDocument.model_validate_json(row['body_json']).title, created_at=row['created_at']) for row in rows]


def update(identifier: str, expected_revision: int, document: BeatDocument) -> BeatDocument:
    identifier = _identifier(identifier)
    document = _owned(document, identifier, expected_revision + 1)
    connection, timestamp = _connection(), now()
    connection.execute('BEGIN IMMEDIATE')
    try:
        changed = connection.execute('UPDATE beat_documents SET revision=revision+1,body_json=?,updated_at=? WHERE id=? AND revision=? AND archived=0',
            (canonical_json(document).decode('ascii'), timestamp, identifier, expected_revision))
        if changed.rowcount != 1:
            raise BeatError('beat_revision_conflict')
        _revision(connection, document, timestamp)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return document


def restore(identifier: str, expected_revision: int, revision: int) -> BeatDocument:
    return update(identifier, expected_revision, get_revision(identifier, revision))


def archive(identifier: str, expected_revision: int) -> None:
    connection = _connection()
    connection.execute('BEGIN IMMEDIATE')
    try:
        changed = connection.execute('UPDATE beat_documents SET archived=1,updated_at=? WHERE id=? AND revision=? AND archived=0',
            (now(), _identifier(identifier), expected_revision))
        if changed.rowcount != 1:
            raise BeatError('beat_revision_conflict')
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _pattern(document: BeatDocument, pattern_id: str) -> BeatPattern:
    for pattern in document.patterns:
        if pattern.id == pattern_id:
            return pattern
    raise BeatError('beat_pattern_invalid')


def humanize(identifier: str, request: BeatHumanizeRequest) -> BeatDocument:
    document = get(identifier)
    pattern = _pattern(document, request.pattern_id)
    known = {lane.lane_id for lane in pattern.lanes}
    if len(set(request.lane_ids)) != len(request.lane_ids) or not set(request.lane_ids) <= known:
        raise BeatError('beat_pattern_invalid')
    lanes: list[BeatLane] = []
    for lane in pattern.lanes:
        steps: list[BeatStep] = []
        for step in lane.steps:
            coordinate = (pattern.id, lane.lane_id, 0, step.index)
            timing = (event_fraction(document.seed, coordinate, 'timing') * 2 - 1) * request.timing_steps
            velocity = round((event_fraction(document.seed, coordinate, 'velocity') * 2 - 1) * request.velocity)
            steps.append(step.model_copy(update={'offset_steps': max(-0.5, min(0.5, step.offset_steps + timing)),
                'velocity': max(1, min(127, step.velocity + velocity))}) if lane.lane_id in request.lane_ids else step)
        lanes.append(lane.model_copy(update={'steps': steps}))
    changed = pattern.model_copy(update={'lanes': lanes})
    return update(identifier, request.expected_revision, document.model_copy(update={'patterns': [
        changed if item.id == changed.id else item for item in document.patterns]}))


def variation(identifier: str, request: BeatVariationRequest) -> BeatDocument:
    document = get(identifier)
    pattern = _pattern(document, request.pattern_id)
    lanes: list[BeatLane] = []
    count = pattern.bars * pattern.grid_size(pattern.meter, pattern.steps_per_quarter)
    for lane in pattern.lanes:
        existing = {step.index: step for step in lane.steps}
        steps = [existing.get(index, BeatStep(index=index, velocity=75)) for index in range(count)
            if index in existing or event_fraction(request.seed, (pattern.id, lane.lane_id, 0, index), 'probability') < 0.12]
        lanes.append(lane.model_copy(update={'steps': steps}))
    changed = pattern.model_copy(update={'lanes': lanes})
    return update(identifier, request.expected_revision, document.model_copy(update={'seed': request.seed,
        'patterns': [changed if item.id == changed.id else item for item in document.patterns]}))
