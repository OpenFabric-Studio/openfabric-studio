"""Additive replacement persistence. No process, path resolver or job queue."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import hashlib
import json
import sqlite3
from typing import Literal, Self

from pydantic import Field, ValidationError, model_validator

from .replacement_contracts import (ReplacementErrorCode, ReplacementPlanResponse,
    ReplacementActionRequest, ReplacementArtifact, ReplacementPhase, ReplacementPlanSummary, ReplacementPlansResponse,
    ReplacementRender, ReplacementRenderRequest, ReplacementRevision,
    SelectedSource, replacement_request_adapter)
from .music_analysis_contracts import (MusicAnalysisContract, MusicAnalysisId, MusicAnalysisTimestamp, MusicSourceRef)
from .video_process import WorkerIdentity
from .artist_contracts import ArtistConsentVersion
from .export_provenance_contracts import ContentOrigin, MusicEvidence

SCHEMA_VERSION = 1
MAX_DOCUMENT_BYTES = 4 * 1024**2


class ReplacementStoreError(Exception):
    def __init__(self, code: ReplacementErrorCode, status: int = 409, *, current_revision: int | None = None) -> None:
        self.code, self.status, self.current_revision = code, status, current_revision
        super().__init__(code)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _sha(document: str) -> str:
    return hashlib.sha256(document.encode()).hexdigest()


class StoredReplacementAttempt(MusicAnalysisContract):
    id: MusicAnalysisId
    plan_id: MusicAnalysisId
    revision: ReplacementRevision
    action: Literal['prepare', 'preview', 'render', 'remix']
    request: ReplacementRenderRequest | ReplacementActionRequest
    status: Literal['queued', 'running', 'done', 'failed', 'cancelled', 'interrupted'] = 'queued'
    phase: ReplacementPhase
    cleanup_pending: bool = False
    worker: WorkerIdentity | None = None
    created_at: MusicAnalysisTimestamp
    finished_at: MusicAnalysisTimestamp | None = None
    error_code: ReplacementErrorCode | None = None
    authorization: ArtistConsentVersion | None = None
    impulse_response: SelectedSource | None = None

    @property
    def request_sha256(self) -> str:
        return _sha(_canonical({'action': self.action, 'request': self.request.model_dump(mode='json')}))

    @model_validator(mode='after')
    def coherent(self) -> Self:
        if self.request.expected_revision != self.revision:
            raise ValueError('replacement_attempt_revision_mismatch')
        if self.action != 'prepare' and not isinstance(self.request, ReplacementRenderRequest):
            raise ValueError('replacement_render_request_required')
        if self.action == 'prepare' and isinstance(self.request, ReplacementRenderRequest):
            raise ValueError('replacement_prepare_request_invalid')
        if self.worker is not None and len(self.worker.receipt) > 4096:
            raise ValueError('replacement_worker_receipt_invalid')
        if self.status == 'done' and (self.worker is not None or self.cleanup_pending or self.error_code is not None):
            raise ValueError('replacement_worker_not_drained')
        if self.status in {'failed', 'interrupted'} and self.error_code is None:
            raise ValueError('replacement_attempt_error_required')
        return self


class StoredReplacementArtifact(MusicAnalysisContract):
    plan_id: MusicAnalysisId
    public: ReplacementArtifact
    relative_path: str = Field(max_length=150,
        pattern=r'^replacements/[0-9a-f]{32}/artifacts/[0-9a-f]{32}\.wav$')
    duration_frames: int = Field(ge=1, le=1382400000)
    created_at: MusicAnalysisTimestamp
    music_evidence: MusicEvidence | None = None
    content_origin: ContentOrigin = 'unknown'

    @model_validator(mode='after')
    def identity(self) -> Self:
        expected_path = f'replacements/{self.plan_id}/artifacts/{self.public.id}.wav'
        expected_url = f'/api/voices/replace/plans/{self.plan_id}/artifacts/{self.public.id}/audio'
        if (self.relative_path != expected_path or self.public.audio_url not in (None, expected_url)
                or self.duration_frames != round(self.public.duration_sec * self.public.sample_rate_hz)):
            raise ValueError('replacement_artifact_identity_mismatch')
        return self


class _ArtifactBody(ReplacementArtifact):
    music_evidence: MusicEvidence | None = None
    content_origin: ContentOrigin = 'unknown'


_ID = "length(id)=32 AND id NOT GLOB '*[^0-9a-f]*'"
_HASH = "length(request_sha256)=64 AND request_sha256 NOT GLOB '*[^0-9a-f]*'"
_DDL = (
    'CREATE TABLE IF NOT EXISTS replacement_schema(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL CHECK(version=1))',
    f'''CREATE TABLE IF NOT EXISTS replacement_plans(
        id TEXT PRIMARY KEY CHECK({_ID}), revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
        status TEXT NOT NULL CHECK(status IN ('draft','queued','running','needs_review','done','failed','cancelled','interrupted')),
        phase TEXT NOT NULL CHECK(phase IN ('inspecting','separating','analyzing','cleaning','aligning','converting','mixing','measuring','publishing','complete')),
        active_attempt_id TEXT REFERENCES replacement_attempts(id) DEFERRABLE INITIALLY DEFERRED,
        request_json TEXT NOT NULL CHECK(json_valid(request_json)), request_sha256 TEXT NOT NULL CHECK({_HASH}),
        body_json TEXT NOT NULL CHECK(json_valid(body_json)), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        deleted_at TEXT,
        CHECK(json_extract(body_json,'$.id') IS id AND json_extract(body_json,'$.revision') IS revision
            AND json_extract(body_json,'$.status') IS status AND json_extract(body_json,'$.phase') IS phase
            AND json_extract(body_json,'$.active_attempt_id') IS active_attempt_id))''',
    '''CREATE TABLE IF NOT EXISTS replacement_revisions(
        plan_id TEXT NOT NULL REFERENCES replacement_plans(id), revision INTEGER NOT NULL CHECK(revision>=1),
        body_json TEXT NOT NULL CHECK(json_valid(body_json)), settings_sha256 TEXT NOT NULL
            CHECK(length(settings_sha256)=64 AND settings_sha256 NOT GLOB '*[^0-9a-f]*'),
        created_at TEXT NOT NULL, PRIMARY KEY(plan_id,revision),
        CHECK(json_extract(body_json,'$.id') IS plan_id AND json_extract(body_json,'$.revision') IS revision))''',
    f'''CREATE TABLE IF NOT EXISTS replacement_attempts(
        id TEXT PRIMARY KEY CHECK({_ID}), plan_id TEXT NOT NULL REFERENCES replacement_plans(id),
        revision INTEGER NOT NULL, action TEXT NOT NULL CHECK(action IN ('prepare','preview','render','remix')),
        request_id TEXT NOT NULL CHECK(length(request_id)=32 AND request_id NOT GLOB '*[^0-9a-f]*'),
        request_sha256 TEXT NOT NULL CHECK({_HASH}),
        status TEXT NOT NULL CHECK(status IN ('queued','running','done','failed','cancelled','interrupted')),
        cleanup_pending INTEGER NOT NULL DEFAULT 0 CHECK(cleanup_pending IN (0,1)),
        body_json TEXT NOT NULL CHECK(json_valid(body_json)), worker_json TEXT CHECK(worker_json IS NULL OR json_valid(worker_json)),
        created_at TEXT NOT NULL, finished_at TEXT,
        FOREIGN KEY(plan_id,revision) REFERENCES replacement_revisions(plan_id,revision), UNIQUE(plan_id,request_id))''',
    f'''CREATE TABLE IF NOT EXISTS replacement_artifacts(
        id TEXT PRIMARY KEY CHECK({_ID}), plan_id TEXT NOT NULL REFERENCES replacement_plans(id),
        role TEXT NOT NULL CHECK(role IN ('source_vocal','backing','prepared_dry','prepared_clean','aligned_dry','converted_dry','matched_vocal','preview','master','impulse_response')),
        sha256 TEXT NOT NULL CHECK(length(sha256)=64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
        settings_sha256 TEXT NOT NULL CHECK(length(settings_sha256)=64 AND settings_sha256 NOT GLOB '*[^0-9a-f]*'),
        relative_path TEXT NOT NULL CHECK(length(relative_path) BETWEEN 1 AND 1024 AND relative_path NOT LIKE '/%'
            AND instr(relative_path,'..')=0 AND instr(relative_path,':')=0 AND instr(relative_path,'\\')=0),
        duration_frames INTEGER NOT NULL CHECK(duration_frames BETWEEN 1 AND 1382400000),
        body_json TEXT NOT NULL CHECK(json_valid(body_json)), created_at TEXT NOT NULL,
        UNIQUE(plan_id,id), UNIQUE(plan_id,role,settings_sha256,sha256),
        CHECK(json_extract(body_json,'$.id') IS id AND json_extract(body_json,'$.role') IS role
            AND json_extract(body_json,'$.sha256') IS sha256 AND json_extract(body_json,'$.settings_sha256') IS settings_sha256))''',
    f'''CREATE TABLE IF NOT EXISTS replacement_renders(
        id TEXT PRIMARY KEY CHECK({_ID}), plan_id TEXT NOT NULL REFERENCES replacement_plans(id), revision INTEGER NOT NULL,
        request_id TEXT NOT NULL UNIQUE CHECK(length(request_id)=32 AND request_id NOT GLOB '*[^0-9a-f]*'),
        request_sha256 TEXT NOT NULL CHECK({_HASH}),
        status TEXT NOT NULL CHECK(status IN ('queued','running','done','failed','cancelled','interrupted')),
        master_artifact_id TEXT, track_id INTEGER UNIQUE REFERENCES tracks(id) ON DELETE SET NULL,
        track_deleted INTEGER NOT NULL DEFAULT 0 CHECK(track_deleted IN (0,1)),
        request_json TEXT NOT NULL CHECK(json_valid(request_json)),
        body_json TEXT NOT NULL CHECK(json_valid(body_json)), created_at TEXT NOT NULL,
        FOREIGN KEY(plan_id,revision) REFERENCES replacement_revisions(plan_id,revision),
        FOREIGN KEY(plan_id,master_artifact_id) REFERENCES replacement_artifacts(plan_id,id),
        CHECK(status!='done' OR (master_artifact_id IS NOT NULL AND (track_id IS NOT NULL OR track_deleted=1))),
        CHECK(track_deleted=0 OR (status='done' AND track_id IS NULL)),
        CHECK(json_extract(body_json,'$.id') IS id AND json_extract(body_json,'$.plan_id') IS plan_id
            AND json_extract(body_json,'$.revision') IS revision AND json_extract(body_json,'$.request_id') IS request_id
            AND json_extract(body_json,'$.status') IS status AND json_extract(body_json,'$.master_artifact_id') IS master_artifact_id
            AND json_extract(body_json,'$.track_id') IS track_id AND json_extract(body_json,'$.track_deleted') IS track_deleted))''',
    'CREATE INDEX IF NOT EXISTS idx_replacement_plans_created ON replacement_plans(created_at DESC,id)',
    'CREATE INDEX IF NOT EXISTS idx_replacement_artifacts_plan ON replacement_artifacts(plan_id,created_at,id)',
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_replacement_active_plan ON replacement_attempts(plan_id) WHERE status IN ('queued','running') OR cleanup_pending=1",
    '''CREATE TRIGGER IF NOT EXISTS replacement_revision_immutable_update BEFORE UPDATE ON replacement_revisions
        BEGIN SELECT RAISE(ABORT,'immutable_replacement_revision'); END''',
    '''CREATE TRIGGER IF NOT EXISTS replacement_revision_immutable_delete BEFORE DELETE ON replacement_revisions
        BEGIN SELECT RAISE(ABORT,'immutable_replacement_revision'); END''',
    '''CREATE TRIGGER IF NOT EXISTS replacement_artifact_immutable BEFORE UPDATE ON replacement_artifacts
        BEGIN SELECT RAISE(ABORT,'immutable_replacement_artifact'); END''',
    '''CREATE TRIGGER IF NOT EXISTS replacement_track_deleted BEFORE DELETE ON tracks BEGIN
        UPDATE replacement_renders SET track_id=NULL,track_deleted=1,
            body_json=json_set(body_json,'$.track_id',NULL,'$.track_deleted',json('true')) WHERE track_id=OLD.id;
        END''',
)


def _text(row: sqlite3.Row, name: str) -> str:
    value: object = row[name]
    if not isinstance(value, str) or len(value.encode()) > MAX_DOCUMENT_BYTES:
        raise ReplacementStoreError('replacement_storage_failed', 503)
    return value


def _version(connection: sqlite3.Connection) -> None:
    try:
        rows = connection.execute('SELECT version FROM replacement_schema LIMIT 2').fetchall()
    except sqlite3.Error as error:
        raise ReplacementStoreError('replacement_storage_incompatible', 503) from error
    if len(rows) != 1 or rows[0]['version'] != SCHEMA_VERSION:
        raise ReplacementStoreError('replacement_storage_incompatible', 503)


def migrate(connection: sqlite3.Connection) -> None:
    present = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='replacement_schema'").fetchone()
    tables = {row['name'] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'replacement_%'")}
    if present is None and tables:
        raise ReplacementStoreError('replacement_storage_incompatible', 503)
    if present is not None:
        _version(connection)
        expected = {
            'replacement_plans': {'id', 'revision', 'status', 'phase', 'active_attempt_id', 'request_json', 'request_sha256', 'body_json', 'created_at', 'updated_at', 'deleted_at'},
            'replacement_revisions': {'plan_id', 'revision', 'body_json', 'settings_sha256', 'created_at'},
            'replacement_attempts': {'id', 'plan_id', 'revision', 'action', 'request_id', 'request_sha256', 'status', 'cleanup_pending', 'body_json', 'worker_json', 'created_at', 'finished_at'},
            'replacement_artifacts': {'id', 'plan_id', 'role', 'sha256', 'settings_sha256', 'relative_path', 'duration_frames', 'body_json', 'created_at'},
            'replacement_renders': {'id', 'plan_id', 'revision', 'request_id', 'request_sha256', 'request_json', 'status', 'master_artifact_id', 'track_id', 'track_deleted', 'body_json', 'created_at'},
        }
        for table, columns in expected.items():
            if {row['name'] for row in connection.execute(f'PRAGMA table_info({table})')} != columns:
                raise ReplacementStoreError('replacement_storage_incompatible', 503)
    connection.execute('SAVEPOINT replacement_migration')
    try:
        for statement in _DDL:
            connection.execute(statement)
        connection.execute('INSERT OR IGNORE INTO replacement_schema(id,version) VALUES(1,1)')
        _version(connection)
        connection.execute('RELEASE SAVEPOINT replacement_migration')
    except (sqlite3.Error, ReplacementStoreError) as error:
        connection.execute('ROLLBACK TO SAVEPOINT replacement_migration')
        connection.execute('RELEASE SAVEPOINT replacement_migration')
        if isinstance(error, ReplacementStoreError):
            raise
        raise ReplacementStoreError('replacement_storage_failed', 503) from error


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    if connection.in_transaction:
        raise ReplacementStoreError('replacement_storage_failed', 503)
    _version(connection)
    connection.execute('BEGIN IMMEDIATE')
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def get_plan(connection: sqlite3.Connection, identifier: str) -> ReplacementPlanResponse:
    _version(connection)
    row = connection.execute('SELECT * FROM replacement_plans WHERE id=? AND deleted_at IS NULL', (identifier,)).fetchone()
    if row is None:
        raise ReplacementStoreError('replacement_plan_missing', 404)
    try:
        plan = ReplacementPlanResponse.model_validate_json(_text(row, 'body_json'))
        request = replacement_request_adapter.validate_json(_text(row, 'request_json'))
        if (plan.id != identifier or plan.revision != row['revision'] or plan.status != row['status']
                or plan.phase != row['phase'] or plan.active_attempt_id != row['active_attempt_id']
                or plan.request != request or _sha(_text(row, 'request_json')) != _text(row, 'request_sha256')):
            raise ValueError('replacement row identity mismatch')
        artifacts = connection.execute('SELECT * FROM replacement_artifacts WHERE plan_id=? ORDER BY rowid LIMIT 65', (identifier,)).fetchall()
        renders = connection.execute('SELECT * FROM replacement_renders WHERE plan_id=? ORDER BY rowid LIMIT 101', (identifier,)).fetchall()
        if len(artifacts) > 64 or len(renders) > 100:
            raise ValueError('replacement plan capacity exceeded')
        return ReplacementPlanResponse.model_validate({**plan.model_dump(),
            'artifacts': [_artifact(item).public for item in artifacts], 'renders': [_render(item) for item in renders]})
    except (ValidationError, ValueError) as error:
        raise ReplacementStoreError('replacement_storage_failed', 503) from error


def get_revision(connection: sqlite3.Connection, identifier: str, revision: int) -> ReplacementPlanResponse:
    _version(connection)
    row = connection.execute('SELECT * FROM replacement_revisions WHERE plan_id=? AND revision=?', (identifier, revision)).fetchone()
    if row is None:
        raise ReplacementStoreError('replacement_plan_missing', 404)
    try:
        body = _text(row, 'body_json')
        plan = ReplacementPlanResponse.model_validate_json(body)
        if plan.id != identifier or plan.revision != revision or _sha(body) != _text(row, 'settings_sha256'):
            raise ValueError('replacement revision identity mismatch')
        return plan
    except (ValidationError, ValueError) as error:
        raise ReplacementStoreError('replacement_storage_failed', 503) from error


def _insert_revision(connection: sqlite3.Connection, plan: ReplacementPlanResponse) -> None:
    body = _canonical(plan.model_dump(mode='json'))
    connection.execute('INSERT INTO replacement_revisions(plan_id,revision,body_json,settings_sha256,created_at) VALUES(?,?,?,?,?)',
        (plan.id, plan.revision, body, _sha(body), plan.updated_at))


def create_plan(connection: sqlite3.Connection, plan: ReplacementPlanResponse) -> ReplacementPlanResponse:
    if plan.revision != 1 or plan.status != 'draft' or plan.active_attempt_id is not None or plan.artifacts or plan.renders:
        raise ReplacementStoreError('replacement_invalid_request', 422)
    body = _canonical(plan.model_dump(mode='json'))
    request = _canonical(plan.request.model_dump(mode='json'))
    with _transaction(connection):
        connection.execute('INSERT INTO replacement_plans(id,revision,status,phase,active_attempt_id,request_json,request_sha256,body_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (plan.id, plan.revision, plan.status, plan.phase, plan.active_attempt_id, request, _sha(request), body, plan.updated_at, plan.updated_at))
        _insert_revision(connection, plan)
    return get_plan(connection, plan.id)


def replace_plan(connection: sqlite3.Connection, expected_revision: int, plan: ReplacementPlanResponse) -> ReplacementPlanResponse:
    if plan.revision != expected_revision + 1:
        raise ReplacementStoreError('replacement_invalid_request', 422)
    with _transaction(connection):
        current = get_plan(connection, plan.id)
        if current.revision != expected_revision:
            raise ReplacementStoreError('replacement_revision_conflict', current_revision=current.revision)
        if current.active_attempt_id is not None:
            raise ReplacementStoreError('replacement_busy')
        body = _canonical(plan.model_dump(mode='json'))
        request = _canonical(plan.request.model_dump(mode='json'))
        changed = connection.execute('UPDATE replacement_plans SET revision=?,status=?,phase=?,active_attempt_id=?,request_json=?,request_sha256=?,body_json=?,updated_at=? WHERE id=? AND revision=?',
            (plan.revision, plan.status, plan.phase, plan.active_attempt_id, request, _sha(request), body, plan.updated_at, plan.id, expected_revision))
        if changed.rowcount != 1:
            raise ReplacementStoreError('replacement_revision_conflict', current_revision=current.revision)
        _insert_revision(connection, plan)
    return get_plan(connection, plan.id)


def _attempt(row: sqlite3.Row) -> StoredReplacementAttempt:
    try:
        attempt = StoredReplacementAttempt.model_validate_json(_text(row, 'body_json'))
        if (attempt.id != _text(row, 'id') or attempt.plan_id != _text(row, 'plan_id')
                or attempt.revision != row['revision'] or attempt.action != _text(row, 'action')
                or attempt.request.request_id != _text(row, 'request_id')
                or attempt.request_sha256 != _text(row, 'request_sha256')
                or attempt.status != _text(row, 'status') or attempt.cleanup_pending != bool(row['cleanup_pending'])):
            raise ValueError('replacement attempt row mismatch')
        worker: object = row['worker_json']
        if worker is None:
            if attempt.worker is not None:
                raise ValueError('replacement worker mismatch')
        elif not isinstance(worker, str) or WorkerIdentity.model_validate_json(worker) != attempt.worker:
            raise ValueError('replacement worker mismatch')
        return attempt
    except (ValidationError, ValueError) as error:
        raise ReplacementStoreError('replacement_storage_failed', 503) from error


def get_attempt(connection: sqlite3.Connection, identifier: str) -> StoredReplacementAttempt:
    _version(connection)
    row = connection.execute('SELECT * FROM replacement_attempts WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise ReplacementStoreError('replacement_plan_missing', 404)
    return _attempt(row)


def active_attempts(connection: sqlite3.Connection) -> list[StoredReplacementAttempt]:
    _version(connection)
    rows = connection.execute("SELECT * FROM replacement_attempts WHERE status IN ('queued','running') OR cleanup_pending=1 OR worker_json IS NOT NULL ORDER BY rowid LIMIT 1001").fetchall()
    if len(rows) > 1000:
        raise ReplacementStoreError('replacement_storage_failed', 503)
    return [_attempt(row) for row in rows]


def delete_plan(connection: sqlite3.Connection, identifier: str, deleted_at: str) -> None:
    """Tombstone metadata only; retained media and render tracks remain intact."""
    with _transaction(connection):
        plan = get_plan(connection, identifier)
        if plan.active_attempt_id is not None:
            raise ReplacementStoreError('replacement_busy')
        connection.execute('UPDATE replacement_plans SET deleted_at=? WHERE id=? AND deleted_at IS NULL',
            (deleted_at, identifier))


def _write_state(connection: sqlite3.Connection, current: ReplacementPlanResponse, plan: ReplacementPlanResponse) -> None:
    if (current.id != plan.id or current.revision != plan.revision
            or current.request != plan.request or current.mix != plan.mix):
        raise ReplacementStoreError('replacement_revision_conflict', current_revision=current.revision)
    changed = connection.execute('UPDATE replacement_plans SET status=?,phase=?,active_attempt_id=?,body_json=?,updated_at=? WHERE id=? AND revision=? AND active_attempt_id IS ?',
        (plan.status, plan.phase, plan.active_attempt_id, _canonical(plan.model_dump(mode='json')),
            plan.updated_at, plan.id, current.revision, current.active_attempt_id))
    if changed.rowcount != 1:
        raise ReplacementStoreError('replacement_revision_conflict', current_revision=current.revision)


def save_state(connection: sqlite3.Connection, plan: ReplacementPlanResponse, *, expected_attempt_id: str) -> ReplacementPlanResponse:
    with _transaction(connection):
        current = get_plan(connection, plan.id)
        if current.active_attempt_id != expected_attempt_id or plan.active_attempt_id != expected_attempt_id:
            raise ReplacementStoreError('replacement_busy')
        _write_state(connection, current, plan)
    return get_plan(connection, plan.id)


def claim_attempt(connection: sqlite3.Connection, attempt: StoredReplacementAttempt, *,
                  admit: Callable[[], None] | None = None) -> StoredReplacementAttempt:
    with _transaction(connection):
        existing = connection.execute('SELECT * FROM replacement_attempts WHERE plan_id=? AND request_id=?',
            (attempt.plan_id, attempt.request.request_id)).fetchone()
        if existing is not None:
            owned = _attempt(existing)
            if owned.request_sha256 != attempt.request_sha256:
                raise ReplacementStoreError('replacement_request_conflict')
            return owned
        current = get_plan(connection, attempt.plan_id)
        if current.revision != attempt.revision:
            raise ReplacementStoreError('replacement_revision_conflict', current_revision=current.revision)
        if current.active_attempt_id is not None:
            raise ReplacementStoreError('replacement_busy')
        if attempt.status != 'queued' or attempt.worker is not None or attempt.cleanup_pending:
            raise ReplacementStoreError('replacement_invalid_request', 422)
        if admit is not None:
            admit()
        connection.execute('INSERT INTO replacement_attempts(id,plan_id,revision,action,request_id,request_sha256,status,cleanup_pending,body_json,worker_json,created_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (attempt.id, attempt.plan_id, attempt.revision, attempt.action, attempt.request.request_id,
                attempt.request_sha256, attempt.status, int(attempt.cleanup_pending), attempt.model_dump_json(),
                None, attempt.created_at, attempt.finished_at))
        plan = ReplacementPlanResponse.model_validate({**current.model_dump(), 'status': 'queued',
            'phase': attempt.phase, 'active_attempt_id': attempt.id, 'error_code': None, 'updated_at': attempt.created_at})
        _write_state(connection, current, plan)
    return get_attempt(connection, attempt.id)


def finish_attempt(connection: sqlite3.Connection, attempt: StoredReplacementAttempt,
                   plan: ReplacementPlanResponse) -> ReplacementPlanResponse:
    if attempt.status in {'queued', 'running'}:
        raise ReplacementStoreError('replacement_invalid_request', 422)
    if plan.active_attempt_id != (attempt.id if attempt.cleanup_pending or attempt.worker is not None else None):
        raise ReplacementStoreError('replacement_invalid_request', 422)
    with _transaction(connection):
        owned = get_attempt(connection, attempt.id)
        current = get_plan(connection, attempt.plan_id)
        if current.active_attempt_id != attempt.id:
            raise ReplacementStoreError('replacement_busy')
        if (owned.request_sha256 != attempt.request_sha256 or owned.plan_id != attempt.plan_id
                or owned.revision != attempt.revision):
            raise ReplacementStoreError('replacement_request_conflict')
        connection.execute('UPDATE replacement_attempts SET status=?,cleanup_pending=?,body_json=?,worker_json=?,finished_at=? WHERE id=?',
            (attempt.status, int(attempt.cleanup_pending), attempt.model_dump_json(),
                attempt.worker.model_dump_json() if attempt.worker is not None else None, attempt.finished_at, attempt.id))
        _write_state(connection, current, plan)
    return get_plan(connection, plan.id)


def save_attempt(connection: sqlite3.Connection, attempt: StoredReplacementAttempt) -> None:
    if attempt.status not in {'queued', 'running'}:
        raise ReplacementStoreError('replacement_invalid_request', 422)
    with _transaction(connection):
        current = get_attempt(connection, attempt.id)
        plan = get_plan(connection, attempt.plan_id)
        if (current.status not in {'queued', 'running'} or plan.active_attempt_id != attempt.id
                or current.plan_id != attempt.plan_id or current.revision != attempt.revision
                or current.request_sha256 != attempt.request_sha256):
            raise ReplacementStoreError('replacement_request_conflict')
        connection.execute('UPDATE replacement_attempts SET status=?,cleanup_pending=?,body_json=?,worker_json=?,finished_at=? WHERE id=?',
            (attempt.status, int(attempt.cleanup_pending), attempt.model_dump_json(),
                attempt.worker.model_dump_json() if attempt.worker else None, attempt.finished_at, attempt.id))
        state = ReplacementPlanResponse.model_validate({**plan.model_dump(), 'status': attempt.status,
            'phase': attempt.phase, 'updated_at': attempt.created_at})
        _write_state(connection, plan, state)


def _artifact(row: sqlite3.Row) -> StoredReplacementArtifact:
    try:
        body = _ArtifactBody.model_validate_json(_text(row, 'body_json'))
        public = ReplacementArtifact.model_validate(body.model_dump(exclude={'music_evidence', 'content_origin'}))
        if (public.id != _text(row, 'id') or public.role != _text(row, 'role')
                or public.sha256 != _text(row, 'sha256') or public.settings_sha256 != _text(row, 'settings_sha256')):
            raise ValueError('replacement artifact row mismatch')
        return StoredReplacementArtifact.model_validate({'plan_id': _text(row, 'plan_id'), 'public': public,
            'relative_path': _text(row, 'relative_path'), 'duration_frames': row['duration_frames'],
            'created_at': _text(row, 'created_at'), 'music_evidence': body.music_evidence, 'content_origin': body.content_origin})
    except (ValidationError, ValueError) as error:
        raise ReplacementStoreError('replacement_storage_failed', 503) from error


def get_artifact(connection: sqlite3.Connection, plan_id: str, identifier: str) -> StoredReplacementArtifact:
    _version(connection)
    row = connection.execute('SELECT * FROM replacement_artifacts WHERE plan_id=? AND id=?', (plan_id, identifier)).fetchone()
    if row is None:
        raise ReplacementStoreError('replacement_artifact_missing', 404)
    return _artifact(row)


def find_artifact(connection: sqlite3.Connection, plan_id: str, role: str, settings_sha256: str) -> StoredReplacementArtifact | None:
    _version(connection)
    row = connection.execute('SELECT * FROM replacement_artifacts WHERE plan_id=? AND role=? AND settings_sha256=? ORDER BY rowid LIMIT 1',
        (plan_id, role, settings_sha256)).fetchone()
    return _artifact(row) if row is not None else None


def add_artifact(connection: sqlite3.Connection, artifact: StoredReplacementArtifact) -> StoredReplacementArtifact:
    with _transaction(connection):
        get_plan(connection, artifact.plan_id)
        existing = connection.execute('SELECT * FROM replacement_artifacts WHERE plan_id=? AND role=? AND settings_sha256=? AND sha256=?',
            (artifact.plan_id, artifact.public.role, artifact.public.settings_sha256, artifact.public.sha256)).fetchone()
        if existing is not None:
            return _artifact(existing)
        count = connection.execute('SELECT COUNT(*) AS count FROM replacement_artifacts WHERE plan_id=?', (artifact.plan_id,)).fetchone()
        if count['count'] >= 64:
            raise ReplacementStoreError('replacement_invalid_request', 422)
        connection.execute('INSERT INTO replacement_artifacts(id,plan_id,role,sha256,settings_sha256,relative_path,duration_frames,body_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
            (artifact.public.id, artifact.plan_id, artifact.public.role, artifact.public.sha256, artifact.public.settings_sha256,
                artifact.relative_path, artifact.duration_frames,
                _ArtifactBody.model_validate({**artifact.public.model_dump(), 'music_evidence': artifact.music_evidence,
                    'content_origin': artifact.content_origin}).model_dump_json(), artifact.created_at))
    return get_artifact(connection, artifact.plan_id, artifact.public.id)


def _render_request(row: sqlite3.Row) -> ReplacementRenderRequest:
    try:
        document = _text(row, 'request_json')
        request = ReplacementRenderRequest.model_validate_json(document)
        if _sha(document) != _text(row, 'request_sha256'):
            raise ValueError('replacement render request mismatch')
        return request
    except (ValidationError, ValueError) as error:
        raise ReplacementStoreError('replacement_storage_failed', 503) from error


def _render(row: sqlite3.Row) -> ReplacementRender:
    try:
        render = ReplacementRender.model_validate_json(_text(row, 'body_json'))
        request = _render_request(row)
        if (render.id != _text(row, 'id') or render.plan_id != _text(row, 'plan_id') or render.revision != row['revision']
                or render.status != _text(row, 'status') or render.track_id != row['track_id']
                or render.track_deleted != bool(row['track_deleted']) or render.master_artifact_id != row['master_artifact_id']
                or render.request_id != _text(row, 'request_id') or request.request_id != render.request_id
                or request.expected_revision != render.revision):
            raise ValueError('replacement render row mismatch')
        return render
    except (ValidationError, ValueError) as error:
        raise ReplacementStoreError('replacement_storage_failed', 503) from error


def get_render(connection: sqlite3.Connection, identifier: str) -> ReplacementRender:
    _version(connection)
    row = connection.execute('SELECT * FROM replacement_renders WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise ReplacementStoreError('replacement_plan_missing', 404)
    return _render(row)


def render_request(connection: sqlite3.Connection, identifier: str) -> ReplacementRenderRequest:
    _version(connection)
    row = connection.execute('SELECT * FROM replacement_renders WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise ReplacementStoreError('replacement_plan_missing', 404)
    return _render_request(row)


def create_render(connection: sqlite3.Connection, render: ReplacementRender,
                  request: ReplacementRenderRequest) -> ReplacementRender:
    if render.request_id != request.request_id or render.revision != request.expected_revision or request.preview_window is not None:
        raise ReplacementStoreError('replacement_invalid_request', 422)
    document = _canonical(request.model_dump(mode='json'))
    with _transaction(connection):
        existing = connection.execute('SELECT * FROM replacement_renders WHERE request_id=?', (request.request_id,)).fetchone()
        if existing is not None:
            if (_text(existing, 'request_sha256') != _sha(document) or _text(existing, 'plan_id') != render.plan_id):
                raise ReplacementStoreError('replacement_request_conflict')
            return _render(existing)
        plan = get_plan(connection, render.plan_id)
        if plan.revision != render.revision:
            raise ReplacementStoreError('replacement_revision_conflict', current_revision=plan.revision)
        if plan.active_attempt_id != render.id or render.status not in {'queued', 'running'} or render.track_id is not None:
            raise ReplacementStoreError('replacement_busy')
        attempt = get_attempt(connection, render.id)
        if attempt.action not in {'render', 'remix'} or attempt.request != request:
            raise ReplacementStoreError('replacement_request_conflict')
        if render.master_artifact_id is not None:
            get_artifact(connection, render.plan_id, render.master_artifact_id)
        if len(plan.renders) >= 100:
            raise ReplacementStoreError('replacement_invalid_request', 422)
        connection.execute('INSERT INTO replacement_renders(id,plan_id,revision,request_id,request_sha256,request_json,status,master_artifact_id,track_id,track_deleted,body_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (render.id, render.plan_id, render.revision, request.request_id, _sha(document), document, render.status,
                render.master_artifact_id, render.track_id, int(render.track_deleted), render.model_dump_json(), render.created_at))
    return get_render(connection, render.id)


def attach_render(connection: sqlite3.Connection, identifier: str, track_id: int) -> None:
    """Attach within insert_track's transaction; never commit on its behalf."""
    if not connection.in_transaction or type(track_id) is not int or track_id < 1:
        raise ReplacementStoreError('replacement_storage_failed', 503)
    current = get_render(connection, identifier)
    plan = get_plan(connection, current.plan_id)
    if current.status not in {'queued', 'running'} or current.track_id is not None or current.master_artifact_id is None:
        raise ReplacementStoreError('replacement_request_conflict')
    if plan.revision != current.revision or plan.active_attempt_id is None:
        raise ReplacementStoreError('replacement_revision_conflict', current_revision=plan.revision)
    artifact = get_artifact(connection, current.plan_id, current.master_artifact_id)
    if (artifact.public.role != 'master' or artifact.public.measurement is None
            or artifact.public.settings_sha256 != current.settings_sha256):
        raise ReplacementStoreError('replacement_output_invalid')
    done = ReplacementRender.model_validate({**current.model_dump(), 'status': 'done', 'track_id': track_id, 'error_code': None})
    changed = connection.execute('UPDATE replacement_renders SET status=?,track_id=?,track_deleted=0,body_json=? WHERE id=? AND track_id IS NULL AND status IN (?,?)',
        ('done', track_id, done.model_dump_json(), identifier, 'queued', 'running'))
    if changed.rowcount != 1:
        raise ReplacementStoreError('replacement_request_conflict')


def update_render(connection: sqlite3.Connection, render: ReplacementRender) -> ReplacementRender:
    """Bind a measured master or terminal error; only attach_render publishes."""
    with _transaction(connection):
        current = get_render(connection, render.id)
        if (current.plan_id != render.plan_id or current.revision != render.revision
                or current.request_id != render.request_id or current.settings_sha256 != render.settings_sha256
                or current.status == 'done' or render.status == 'done' or render.track_id is not None
                or current.master_artifact_id not in (None, render.master_artifact_id)):
            raise ReplacementStoreError('replacement_request_conflict')
        if render.master_artifact_id is not None:
            artifact = get_artifact(connection, render.plan_id, render.master_artifact_id)
            if (artifact.public.role != 'master' or artifact.public.settings_sha256 != render.settings_sha256
                    or artifact.public.measurement is None):
                raise ReplacementStoreError('replacement_output_invalid')
        connection.execute('UPDATE replacement_renders SET status=?,master_artifact_id=?,body_json=? WHERE id=?',
            (render.status, render.master_artifact_id, render.model_dump_json(), render.id))
    return get_render(connection, render.id)


def list_plans(connection: sqlite3.Connection, *, limit: int = 100, cursor: str | None = None,
               source: MusicSourceRef | None = None) -> ReplacementPlansResponse:
    _version(connection)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ReplacementStoreError('replacement_invalid_request', 422)
    where = ['deleted_at IS NULL']
    parameters: list[object] = []
    if cursor is not None:
        marker = connection.execute('SELECT rowid FROM replacement_plans WHERE id=?', (cursor,)).fetchone()
        if marker is None:
            raise ReplacementStoreError('replacement_invalid_request', 422)
        where.append('rowid<?')
        parameters.append(marker['rowid'])
    if source is not None:
        where.append("json_extract(request_json,'$.source.source')=?")
        parameters.append(_canonical(source.model_dump(mode='json')))
    parameters.append(limit + 1)
    rows = connection.execute("SELECT id,revision,status,phase,updated_at,json_extract(request_json,'$.title') AS title,json_extract(request_json,'$.mode') AS mode,json_extract(body_json,'$.error_code') AS error_code FROM replacement_plans WHERE "
        + ' AND '.join(where) + ' ORDER BY rowid DESC LIMIT ?', parameters).fetchall()
    try:
        summaries = [ReplacementPlanSummary.model_validate({name: row[name] for name in
            ('id', 'revision', 'title', 'mode', 'status', 'phase', 'error_code', 'updated_at')}) for row in rows[:limit]]
    except ValidationError as error:
        raise ReplacementStoreError('replacement_storage_failed', 503) from error
    return ReplacementPlansResponse(plans=summaries, next_cursor=summaries[-1].id if len(rows) > limit else None)
