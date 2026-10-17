"""Immutable regional review/manifest revisions beside the existing dataset catalog."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator

from pydantic import BaseModel, ValidationError

from .regional_dataset_contracts import (RegionalDatasetError, RegionalDatasetManifest, RegionalDatasetReview,
    RegionalDatasetReviewRequest, RegionalPreparationJob, RegionalPreparationRequest, RegionalPreparedManifest)
from .regional_acoustic_contracts import RegionalAcousticJob, RegionalAcousticScanRequest
from .regional_acoustic_helper import AcousticProcessIdentity


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_sha256(model: BaseModel) -> str:
    return hashlib.sha256(json.dumps(model.model_dump(mode='json'), sort_keys=True, separators=(',', ':'),
        ensure_ascii=True, allow_nan=False).encode('ascii')).hexdigest()


def migrate(connection: sqlite3.Connection) -> None:
    """Additive and transaction-preserving; unknown future schema is never rewritten."""
    connection.execute('SAVEPOINT regional_dataset_migration')
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS regional_dataset_schema(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL CHECK(version>=1))')
        row = connection.execute('SELECT version FROM regional_dataset_schema WHERE id=1').fetchone()
        if row is not None and row[0] != 1:
            raise RegionalDatasetError('regional_dataset_storage_unavailable', 503)
        connection.execute('CREATE TABLE IF NOT EXISTS regional_dataset_reviews(dataset_id TEXT NOT NULL,revision INTEGER NOT NULL CHECK(revision>=1),review_sha256 TEXT NOT NULL,body_json TEXT NOT NULL CHECK(json_valid(body_json)),PRIMARY KEY(dataset_id,revision))')
        connection.execute('CREATE TABLE IF NOT EXISTS regional_dataset_heads(dataset_id TEXT PRIMARY KEY,revision INTEGER NOT NULL CHECK(revision>=1),FOREIGN KEY(dataset_id,revision) REFERENCES regional_dataset_reviews(dataset_id,revision))')
        connection.execute('CREATE TABLE IF NOT EXISTS regional_dataset_preparations(id TEXT PRIMARY KEY,dataset_id TEXT NOT NULL,submission_key TEXT UNIQUE NOT NULL,request_sha256 TEXT NOT NULL,body_json TEXT NOT NULL CHECK(json_valid(body_json)),created_at TEXT NOT NULL)')
        connection.execute('CREATE TABLE IF NOT EXISTS regional_dataset_manifests(id TEXT PRIMARY KEY REFERENCES regional_dataset_preparations(id),dataset_id TEXT NOT NULL,review_revision INTEGER NOT NULL,manifest_sha256 TEXT NOT NULL,body_json TEXT NOT NULL CHECK(json_valid(body_json)),FOREIGN KEY(dataset_id,review_revision) REFERENCES regional_dataset_reviews(dataset_id,revision))')
        connection.execute('CREATE INDEX IF NOT EXISTS regional_dataset_preparations_created ON regional_dataset_preparations(created_at)')
        connection.execute('CREATE INDEX IF NOT EXISTS regional_dataset_preparations_dataset ON regional_dataset_preparations(dataset_id,created_at)')
        connection.execute('CREATE TABLE IF NOT EXISTS regional_acoustic_scans(id TEXT PRIMARY KEY,dataset_id TEXT NOT NULL,submission_key TEXT UNIQUE NOT NULL,request_sha256 TEXT NOT NULL,body_json TEXT NOT NULL CHECK(json_valid(body_json)),worker_json TEXT CHECK(worker_json IS NULL OR json_valid(worker_json)),created_at TEXT NOT NULL)')
        connection.execute('CREATE INDEX IF NOT EXISTS regional_acoustic_scans_dataset ON regional_acoustic_scans(dataset_id,created_at)')
        connection.execute('CREATE TABLE IF NOT EXISTS regional_comparisons(id TEXT PRIMARY KEY,manifest_id TEXT NOT NULL REFERENCES regional_dataset_manifests(id),submission_key TEXT UNIQUE NOT NULL,request_sha256 TEXT NOT NULL,spec_sha256 TEXT NOT NULL,body_json TEXT NOT NULL CHECK(json_valid(body_json)),created_at TEXT NOT NULL)')
        connection.execute("CREATE TABLE IF NOT EXISTS regional_comparison_arms(comparison_id TEXT NOT NULL REFERENCES regional_comparisons(id),arm TEXT NOT NULL CHECK(arm IN ('base','adapter')),submitting INTEGER NOT NULL DEFAULT 0 CHECK(submitting IN (0,1)),job_id TEXT,output_sha256 TEXT,PRIMARY KEY(comparison_id,arm))")
        connection.execute('CREATE TABLE IF NOT EXISTS regional_listening_reviews(comparison_id TEXT NOT NULL REFERENCES regional_comparisons(id),reviewer_reference TEXT NOT NULL,body_json TEXT NOT NULL CHECK(json_valid(body_json)),PRIMARY KEY(comparison_id,reviewer_reference))')
        connection.execute("CREATE TABLE IF NOT EXISTS regional_blind_assignments(comparison_id TEXT NOT NULL REFERENCES regional_comparisons(id),reviewer_reference TEXT NOT NULL,assignment_sha256 TEXT NOT NULL,body_json TEXT NOT NULL CHECK(json_valid(body_json)),PRIMARY KEY(comparison_id,reviewer_reference))")
        connection.execute("CREATE TABLE IF NOT EXISTS regional_blind_reviews(comparison_id TEXT NOT NULL REFERENCES regional_comparisons(id),reviewer_reference TEXT NOT NULL,body_json TEXT NOT NULL CHECK(json_valid(body_json)),PRIMARY KEY(comparison_id,reviewer_reference))")
        connection.execute('CREATE INDEX IF NOT EXISTS regional_comparisons_manifest ON regional_comparisons(manifest_id,created_at)')
        connection.execute('INSERT OR IGNORE INTO regional_dataset_schema(id,version) VALUES(1,1)')
        connection.execute('RELEASE regional_dataset_migration')
    except BaseException:
        connection.execute('ROLLBACK TO regional_dataset_migration'); connection.execute('RELEASE regional_dataset_migration')
        raise


def get_review(connection: sqlite3.Connection, dataset_id: str, revision: int | None = None) -> RegionalDatasetReview:
    row = connection.execute('SELECT r.* FROM regional_dataset_reviews r JOIN regional_dataset_heads h ON h.dataset_id=r.dataset_id WHERE r.dataset_id=? AND r.revision=?',
        (dataset_id, revision)).fetchone() if revision is not None else connection.execute(
            'SELECT r.* FROM regional_dataset_reviews r JOIN regional_dataset_heads h ON h.dataset_id=r.dataset_id AND h.revision=r.revision WHERE r.dataset_id=?', (dataset_id,)).fetchone()
    if row is None:
        raise RegionalDatasetError('regional_dataset_not_found', 404)
    try:
        result = RegionalDatasetReview.model_validate_json(row['body_json'])
        if (result.request.dataset_id != dataset_id or result.revision != row['revision']
                or result.sha256 != row['review_sha256'] or canonical_sha256(result.request) != result.sha256):
            raise ValueError('corrupt_review')
        return result
    except (ValueError, ValidationError) as error:
        raise RegionalDatasetError('regional_dataset_storage_unavailable', 503) from error


def save_review(connection: sqlite3.Connection, request: RegionalDatasetReviewRequest) -> RegionalDatasetReview:
    connection.execute('BEGIN IMMEDIATE')
    try:
        current = connection.execute('SELECT revision FROM regional_dataset_heads WHERE dataset_id=?', (request.dataset_id,)).fetchone()
        previous = current['revision'] if current is not None else 0
        if previous != request.expected_revision:
            raise RegionalDatasetError('regional_dataset_revision_conflict')
        result = RegionalDatasetReview(revision=previous + 1, sha256=canonical_sha256(request), created_at=now(), request=request)
        connection.execute('INSERT INTO regional_dataset_reviews(dataset_id,revision,review_sha256,body_json) VALUES(?,?,?,?)',
            (request.dataset_id, result.revision, result.sha256, result.model_dump_json()))
        connection.execute('INSERT INTO regional_dataset_heads(dataset_id,revision) VALUES(?,?) ON CONFLICT(dataset_id) DO UPDATE SET revision=excluded.revision', (request.dataset_id, result.revision))
        connection.commit()
        return result
    except BaseException:
        connection.rollback(); raise


def get_preparation(connection: sqlite3.Connection, identifier: str) -> RegionalPreparationJob:
    row = connection.execute('SELECT * FROM regional_dataset_preparations WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise RegionalDatasetError('regional_dataset_not_found', 404)
    try:
        job = RegionalPreparationJob.model_validate_json(row['body_json'])
        if (job.id != identifier or job.request.dataset_id != row['dataset_id'] or canonical_sha256(job.request) != row['request_sha256']
                or job.request.submission_key != row['submission_key'] or job.created_at != row['created_at']):
            raise ValueError('corrupt_preparation')
        if job.result is not None and (job.result.manifest.id != job.id or canonical_sha256(job.result.manifest) != job.result.sha256):
            raise ValueError('corrupt_manifest')
        return job
    except (ValueError, ValidationError) as error:
        raise RegionalDatasetError('regional_dataset_storage_unavailable', 503) from error


def create_preparation(connection: sqlite3.Connection, request: RegionalPreparationRequest, total_items: int) -> tuple[RegionalPreparationJob, bool]:
    connection.execute('BEGIN IMMEDIATE')
    try:
        fingerprint = canonical_sha256(request)
        existing = connection.execute('SELECT id,request_sha256 FROM regional_dataset_preparations WHERE submission_key=?', (request.submission_key,)).fetchone()
        if existing is not None:
            if existing['request_sha256'] != fingerprint:
                raise RegionalDatasetError('regional_dataset_revision_conflict')
            result = get_preparation(connection, existing['id'])
            connection.commit(); return result, False
        review = get_review(connection, request.dataset_id)
        if review.revision != request.review_revision or review.sha256 != request.review_sha256:
            raise RegionalDatasetError('regional_dataset_revision_conflict')
        result = RegionalPreparationJob(id=uuid.uuid4().hex, request=request, status='queued', created_at=now(), total_items=total_items)
        connection.execute('INSERT INTO regional_dataset_preparations(id,dataset_id,submission_key,request_sha256,body_json,created_at) VALUES(?,?,?,?,?,?)',
            (result.id, request.dataset_id, request.submission_key, fingerprint, result.model_dump_json(), result.created_at))
        connection.commit(); return result, True
    except BaseException:
        connection.rollback(); raise


def update_preparation(connection: sqlite3.Connection, job: RegionalPreparationJob, *, retry_save: bool = False) -> None:
    connection.execute('BEGIN IMMEDIATE')
    try:
        previous = get_preparation(connection, job.id)
        if (previous.request != job.request or previous.created_at != job.created_at
                or previous.status in ('done', 'failed', 'cancelled', 'interrupted') and previous.status != job.status
                    and not (retry_save and previous.status in ('failed', 'cancelled', 'interrupted') and job.status == 'running')):
            raise RegionalDatasetError('regional_dataset_storage_unavailable', 503)
        validated = RegionalPreparationJob.model_validate_json(job.model_dump_json())
        connection.execute('UPDATE regional_dataset_preparations SET body_json=? WHERE id=?', (validated.model_dump_json(), job.id))
        connection.commit()
    except BaseException:
        connection.rollback(); raise


def publish_manifest(connection: sqlite3.Connection, job: RegionalPreparationJob, manifest: RegionalDatasetManifest) -> RegionalPreparationJob:
    if (manifest.id != job.id or manifest.dataset_id != job.request.dataset_id
            or manifest.review_revision != job.request.review_revision or manifest.review_sha256 != job.request.review_sha256):
        raise RegionalDatasetError('regional_dataset_invalid', 422)
    result = RegionalPreparedManifest(sha256=canonical_sha256(manifest), manifest=manifest)
    complete = RegionalPreparationJob.model_validate({**job.model_dump(), 'status': 'done', 'phase': 'complete',
        'completed_items': job.total_items, 'result': result.model_dump(), 'error_code': None})
    connection.execute('BEGIN IMMEDIATE')
    try:
        stored = get_preparation(connection, job.id)
        if stored.request != job.request:
            raise RegionalDatasetError('regional_dataset_storage_unavailable', 503)
        connection.execute('INSERT INTO regional_dataset_manifests(id,dataset_id,review_revision,manifest_sha256,body_json) VALUES(?,?,?,?,?)',
            (job.id, manifest.dataset_id, manifest.review_revision, result.sha256, manifest.model_dump_json()))
        connection.execute('UPDATE regional_dataset_preparations SET body_json=? WHERE id=?', (complete.model_dump_json(), job.id))
        connection.commit(); return complete
    except BaseException:
        connection.rollback(); raise


def get_manifest(connection: sqlite3.Connection, identifier: str) -> RegionalPreparedManifest:
    job = get_preparation(connection, identifier)
    row = connection.execute('SELECT * FROM regional_dataset_manifests WHERE id=?', (identifier,)).fetchone()
    if row is None or job.status != 'done' or job.result is None:
        raise RegionalDatasetError('regional_dataset_not_found', 404)
    manifest = RegionalDatasetManifest.model_validate_json(row['body_json'])
    result = RegionalPreparedManifest(sha256=row['manifest_sha256'], manifest=manifest)
    if result != job.result or canonical_sha256(manifest) != result.sha256:
        raise RegionalDatasetError('regional_dataset_storage_unavailable', 503)
    return result


def list_preparations(connection: sqlite3.Connection, dataset_id: str | None = None) -> list[RegionalPreparationJob]:
    rows = connection.execute('SELECT id FROM regional_dataset_preparations ORDER BY created_at DESC LIMIT 100').fetchall() if dataset_id is None else connection.execute(
        'SELECT id FROM regional_dataset_preparations WHERE dataset_id=? ORDER BY created_at DESC LIMIT 100', (dataset_id,)).fetchall()
    return [get_preparation(connection, row['id']) for row in rows]


def active_preparation_ids(connection: sqlite3.Connection) -> Iterator[str]:
    for row in connection.execute("SELECT id FROM regional_dataset_preparations WHERE json_extract(body_json,'$.status') IN ('queued','running')"):
        value: object = row['id']
        if not isinstance(value, str):
            raise RegionalDatasetError('regional_dataset_storage_unavailable', 503)
        yield value


def get_acoustic_scan(connection: sqlite3.Connection, identifier: str) -> RegionalAcousticJob:
    row = connection.execute('SELECT * FROM regional_acoustic_scans WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise RegionalDatasetError('regional_dataset_not_found', 404)
    try:
        job = RegionalAcousticJob.model_validate_json(row['body_json'])
        if (job.id != identifier or job.request.dataset_id != row['dataset_id'] or job.request.submission_key != row['submission_key']
                or canonical_sha256(job.request) != row['request_sha256'] or job.created_at != row['created_at']
                or job.result is not None and canonical_sha256(job.result.report) != job.result.sha256):
            raise ValueError('corrupt_acoustic_scan')
        return job
    except (ValueError, ValidationError) as error:
        raise RegionalDatasetError('regional_dataset_storage_unavailable', 503) from error


def create_acoustic_scan(connection: sqlite3.Connection, request: RegionalAcousticScanRequest) -> tuple[RegionalAcousticJob, bool]:
    connection.execute('BEGIN IMMEDIATE')
    try:
        checksum = canonical_sha256(request)
        old = connection.execute('SELECT id,request_sha256 FROM regional_acoustic_scans WHERE submission_key=?', (request.submission_key,)).fetchone()
        if old is not None:
            if old['request_sha256'] != checksum:
                raise RegionalDatasetError('regional_dataset_revision_conflict')
            result = get_acoustic_scan(connection, old['id']); connection.commit(); return result, False
        review = get_review(connection, request.dataset_id)
        if review.revision != request.review_revision or review.sha256 != request.review_sha256:
            raise RegionalDatasetError('regional_dataset_revision_conflict')
        result = RegionalAcousticJob(id=uuid.uuid4().hex, request=request, status='queued', created_at=now())
        connection.execute('INSERT INTO regional_acoustic_scans(id,dataset_id,submission_key,request_sha256,body_json,created_at) VALUES(?,?,?,?,?,?)',
            (result.id, request.dataset_id, request.submission_key, checksum, result.model_dump_json(), result.created_at))
        connection.commit(); return result, True
    except BaseException:
        connection.rollback(); raise


def update_acoustic_scan(connection: sqlite3.Connection, job: RegionalAcousticJob, *, verified_stop: bool = False) -> None:
    connection.execute('BEGIN IMMEDIATE')
    try:
        previous = get_acoustic_scan(connection, job.id)
        validated = RegionalAcousticJob.model_validate_json(job.model_dump_json())
        if (previous.request != job.request or previous.created_at != job.created_at
                or previous.status not in ('queued', 'running') and previous != job and not (
                    verified_stop and previous.error_code == 'regional_dataset_worker_unverified' and job.status == 'cancelled')):
            raise RegionalDatasetError('regional_dataset_storage_unavailable', 503)
        connection.execute('UPDATE regional_acoustic_scans SET body_json=? WHERE id=?', (validated.model_dump_json(), job.id))
        connection.commit()
    except BaseException:
        connection.rollback(); raise


def acoustic_identity(connection: sqlite3.Connection, identifier: str) -> AcousticProcessIdentity | None:
    row = connection.execute('SELECT worker_json FROM regional_acoustic_scans WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise RegionalDatasetError('regional_dataset_not_found', 404)
    try:
        return AcousticProcessIdentity.model_validate_json(row['worker_json']) if row['worker_json'] is not None else None
    except (ValueError, ValidationError) as error:
        raise RegionalDatasetError('regional_dataset_worker_unverified') from error


def set_acoustic_identity(connection: sqlite3.Connection, identifier: str, identity: AcousticProcessIdentity | None) -> None:
    cursor = connection.execute('UPDATE regional_acoustic_scans SET worker_json=? WHERE id=?',
        (identity.model_dump_json() if identity is not None else None, identifier))
    if cursor.rowcount != 1:
        connection.rollback(); raise RegionalDatasetError('regional_dataset_not_found', 404)
    connection.commit()


def acoustic_scan_ids(connection: sqlite3.Connection, dataset_id: str | None = None, *, active: bool = False) -> list[str]:
    if active:
        rows = connection.execute("SELECT id FROM regional_acoustic_scans WHERE json_extract(body_json,'$.status') IN ('queued','running') OR worker_json IS NOT NULL OR json_extract(body_json,'$.error_code')='regional_dataset_worker_unverified'").fetchall()
    else:
        rows = connection.execute('SELECT id FROM regional_acoustic_scans ORDER BY created_at DESC LIMIT 100').fetchall() if dataset_id is None else connection.execute(
            'SELECT id FROM regional_acoustic_scans WHERE dataset_id=? ORDER BY created_at DESC LIMIT 100', (dataset_id,)).fetchall()
    identifiers: list[str] = []
    for row in rows:
        value: object = row['id']
        if not isinstance(value, str):
            raise RegionalDatasetError('regional_dataset_storage_unavailable', 503)
        identifiers.append(value)
    return identifiers
