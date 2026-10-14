"""Immutable source graph and hash-chained usage in the existing SQLite catalog."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import db, export_provenance
from .artist_production_contracts import (AssetRole, ModelUsageEvent, ModelUsageLedger, ProductionGate,
    ProductionProvenance, ProductionRun, ReleaseReview, ReleaseReviewRequest, RightsReview,
    RightsReviewRequest, SourceAsset, SourceDeclaration, UsageEventInput)
from .artist_production_contracts import AmendSourceRequest, SourceDeclarationRevision


class ProductionError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def checksum(value: object) -> str:
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def migrate(connection: sqlite3.Connection) -> None:
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS production_assets (
            id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, relative_path TEXT NOT NULL,
            body_json TEXT NOT NULL CHECK(json_valid(body_json)));
        CREATE INDEX IF NOT EXISTS idx_production_assets_sha ON production_assets(sha256);
        CREATE TABLE IF NOT EXISTS production_edges (
            child_id TEXT NOT NULL REFERENCES production_assets(id),
            parent_id TEXT NOT NULL REFERENCES production_assets(id),
            PRIMARY KEY(child_id,parent_id), CHECK(child_id<>parent_id));
        CREATE TABLE IF NOT EXISTS production_rights_reviews (
            id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES production_assets(id),
            sequence INTEGER NOT NULL UNIQUE, body_json TEXT NOT NULL CHECK(json_valid(body_json)));
        CREATE TABLE IF NOT EXISTS production_source_declarations (
            id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES production_assets(id),
            sequence INTEGER NOT NULL UNIQUE, body_json TEXT NOT NULL CHECK(json_valid(body_json)));
        CREATE TABLE IF NOT EXISTS production_runs (
            id TEXT PRIMARY KEY, track_id INTEGER, version_id TEXT UNIQUE,
            body_json TEXT NOT NULL CHECK(json_valid(body_json)));
        CREATE TABLE IF NOT EXISTS production_release_reviews (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES production_runs(id),
            sequence INTEGER NOT NULL UNIQUE, body_json TEXT NOT NULL CHECK(json_valid(body_json)));
        CREATE TABLE IF NOT EXISTS model_usage_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
            idempotency_key TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL,
            body_json TEXT NOT NULL CHECK(json_valid(body_json)));
        CREATE TABLE IF NOT EXISTS artist_production_schema (
            id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL CHECK(version=1));
        INSERT OR IGNORE INTO artist_production_schema VALUES(1,1);
    ''')
    for table in ('production_assets', 'production_edges', 'production_rights_reviews',
                  'production_source_declarations', 'production_release_reviews', 'model_usage_events'):
        for operation in ('UPDATE', 'DELETE'):
            connection.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()}
                BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'immutable_production_evidence'); END''')
    connection.execute('''CREATE TRIGGER IF NOT EXISTS production_run_identity_no_update
        BEFORE UPDATE ON production_runs WHEN NEW.id IS NOT OLD.id
        OR json_extract(NEW.body_json,'$.request') IS NOT json_extract(OLD.body_json,'$.request')
        OR json_extract(NEW.body_json,'$.model') IS NOT json_extract(OLD.body_json,'$.model')
        OR json_extract(NEW.body_json,'$.range_profile') IS NOT json_extract(OLD.body_json,'$.range_profile')
        OR json_extract(NEW.body_json,'$.project_id') IS NOT json_extract(OLD.body_json,'$.project_id')
        OR json_extract(NEW.body_json,'$.created_at') IS NOT json_extract(OLD.body_json,'$.created_at')
        OR (OLD.track_id IS NOT NULL AND NEW.track_id IS NOT OLD.track_id)
        OR (OLD.version_id IS NOT NULL AND NEW.version_id IS NOT OLD.version_id)
        OR (json_extract(OLD.body_json,'$.output_asset_id') IS NOT NULL
            AND json_extract(NEW.body_json,'$.output_asset_id') IS NOT json_extract(OLD.body_json,'$.output_asset_id'))
        BEGIN SELECT RAISE(ABORT,'immutable_production_identity'); END''')
    connection.commit()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    db.get_db()
    connection = sqlite3.connect(db.DB_PATH, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    try:
        connection.execute('BEGIN IMMEDIATE')
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _body(row: sqlite3.Row) -> str:
    value: object = row['body_json']
    if not isinstance(value, str):
        raise ProductionError('invalid_production_evidence')
    return value


def owned_file(path: Path) -> Path:
    if db.FILES_DIR.is_symlink():
        raise ProductionError('invalid_storage')
    root = db.FILES_DIR.resolve()
    if path.is_symlink() or path.resolve() != path or not path.is_relative_to(root) or not path.is_file():
        raise ProductionError('artifact_missing')
    return path


def asset_path(identifier: str) -> Path:
    row = db.get_db().execute('SELECT relative_path FROM production_assets WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise ProductionError('lineage_missing')
    relative: object = row['relative_path']
    if not isinstance(relative, str) or Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ProductionError('artifact_missing')
    return owned_file(db.FILES_DIR.resolve() / relative)


def get_asset(identifier: str) -> SourceAsset:
    row = db.get_db().execute('SELECT body_json FROM production_assets WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise ProductionError('lineage_missing')
    asset = SourceAsset.model_validate_json(_body(row))
    asset.reviews = [RightsReview.model_validate_json(_body(item)) for item in db.get_db().execute(
        'SELECT body_json FROM production_rights_reviews WHERE asset_id=? ORDER BY sequence', (identifier,))]
    asset.declaration_revisions = [SourceDeclarationRevision.model_validate_json(_body(item)) for item in db.get_db().execute(
        'SELECT body_json FROM production_source_declarations WHERE asset_id=? ORDER BY sequence', (identifier,))]
    if asset.declaration is not None:
        original = SourceDeclarationRevision(id=asset.id, created_at=asset.created_at, declaration=asset.declaration, reviewer_label='initial local declaration')
        asset.declaration_revisions.insert(0, original)
    if len(asset.declaration_revisions) > 1:
        asset.declaration = asset.declaration_revisions[-1].declaration
    return asset


def list_assets() -> list[SourceAsset]:
    return [get_asset(str(row['id'])) for row in db.get_db().execute('SELECT id FROM production_assets ORDER BY rowid DESC')]


def _insert_asset(connection: sqlite3.Connection, asset: SourceAsset, path: Path) -> None:
    connection.execute('INSERT INTO production_assets VALUES(?,?,?,?)',
        (asset.id, asset.sha256, str(path.relative_to(db.FILES_DIR.resolve())), asset.model_dump_json()))
    for parent in asset.parent_ids:
        connection.execute('INSERT INTO production_edges VALUES(?,?)', (asset.id, parent))


def _known_generated(sha256: str) -> bool:
    # Existing workflow classification is immutable even if the same bytes are uploaded again.
    # A failed/cancelled job can retain converted bytes; status does not change their origin.
    for row in db.get_db().execute("""SELECT audio_path FROM tracks WHERE model IN ('ace_step','yue2','openrouter')
        UNION SELECT v.audio_path FROM audio_versions v JOIN tracks t ON t.id=v.track_id
        WHERE v.kind='voice' OR t.model IN ('ace_step','yue2','openrouter')"""):
        raw: object = row['audio_path']
        if isinstance(raw, str):
            try:
                if export_provenance.digest(owned_file(Path(raw))) == sha256:
                    return True
            except (OSError, ProductionError, export_provenance.ProvenanceError):
                continue
    return False


def register_source(path: Path, declaration: SourceDeclaration) -> SourceAsset:
    path = owned_file(path)
    sha256 = export_provenance.digest(path)
    known_generated = _known_generated(sha256)
    with transaction() as connection:
        existing = connection.execute('SELECT body_json FROM production_assets WHERE sha256=?', (sha256,)).fetchall()
        for row in existing:
            asset = SourceAsset.model_validate_json(_body(row))
            if asset.origin != declaration.origin or asset.provider != declaration.provider:
                raise ProductionError('source_origin_conflict')
            if asset.role == declaration.role:
                try:
                    if export_provenance.digest(asset_path(asset.id)) == sha256:
                        return get_asset(asset.id)
                except (OSError, ProductionError, export_provenance.ProvenanceError):
                    pass
        if known_generated and declaration.origin != 'ai_generated':
            raise ProductionError('source_origin_conflict')
        reference_only = declaration.origin in {'ai_generated', 'unknown'} or declaration.role == 'reference'
        asset = SourceAsset(id=uuid.uuid4().hex, sha256=sha256, role=declaration.role, origin=declaration.origin,
            provider=declaration.provider, created_at=now(), declaration=declaration,
            classification_basis='user_declaration', reference_only=reference_only)
        _insert_asset(connection, asset, path)
    return asset


def review_asset(identifier: str, request: RightsReviewRequest) -> SourceAsset:
    with transaction() as connection:
        asset = get_asset(identifier)
        if not asset.declaration_revisions:
            raise ProductionError('source_declaration_required')
        current_revision = asset.declaration_revisions[-1].id
        if request.declaration_revision_id != current_revision and (request.declaration_revision_id is not None or len(asset.declaration_revisions) != 1):
            raise ProductionError('source_evidence_changed')
        if len(asset.reviews) >= 1000:
            raise ProductionError('source_evidence_capacity_exceeded')
        review = RightsReview(id=uuid.uuid4().hex, created_at=now(),
            **request.model_copy(update={'declaration_revision_id': current_revision}).model_dump())
        sequence = connection.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM production_rights_reviews').fetchone()[0]
        connection.execute('INSERT INTO production_rights_reviews VALUES(?,?,?,?)',
            (review.id, identifier, sequence, review.model_dump_json()))
    return get_asset(identifier)


def amend_source(identifier: str, request: AmendSourceRequest) -> SourceAsset:
    asset = get_asset(identifier)
    declaration = request.declaration
    if asset.declaration is None or declaration.origin != asset.origin or declaration.provider != asset.provider or declaration.role != asset.role or declaration.original_filename != asset.declaration_revisions[0].declaration.original_filename:
        raise ProductionError('source_origin_conflict')
    if len(asset.declaration_revisions) >= 1000 or len(asset.reviews) >= 1000:
        raise ProductionError('source_evidence_capacity_exceeded')
    revision = SourceDeclarationRevision(id=uuid.uuid4().hex, created_at=now(), **request.model_dump())
    pending = RightsReview(id=uuid.uuid4().hex, created_at=now(), status='unknown', reviewer_label=request.reviewer_label,
        declaration_revision_id=revision.id, evidence_ref=declaration.evidence_ref.strip() or 'evidence_missing', note='Source evidence amended; fresh review required.')
    with transaction() as connection:
        sequence = connection.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM production_source_declarations').fetchone()[0]
        connection.execute('INSERT INTO production_source_declarations VALUES(?,?,?,?)',
            (revision.id, identifier, sequence, revision.model_dump_json()))
        review_sequence = connection.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM production_rights_reviews').fetchone()[0]
        connection.execute('INSERT INTO production_rights_reviews VALUES(?,?,?,?)',
            (pending.id, identifier, review_sequence, pending.model_dump_json()))
    return get_asset(identifier)


def create_transform(path: Path, role: AssetRole, parents: list[str], transformation: str) -> SourceAsset:
    if role not in {'stem', 'voice_converted', 'master', 'export'} or not 1 <= len(parents) <= 32 or len(set(parents)) != len(parents):
        raise ProductionError('invalid_transform')
    path = owned_file(path)
    for identifier in parents:
        get_asset(identifier)
    asset = SourceAsset(id=uuid.uuid4().hex, sha256=export_provenance.digest(path), role=role,
        origin='ai_generated' if role == 'voice_converted' else 'unknown', provider='openfabric',
        created_at=now(), classification_basis='app_workflow', reference_only=False,
        parent_ids=parents, transformation=transformation)
    with transaction() as connection:
        for row in connection.execute('SELECT body_json FROM production_assets WHERE relative_path=? AND sha256=?',
                (str(path.relative_to(db.FILES_DIR.resolve())), asset.sha256)):
            previous = SourceAsset.model_validate_json(_body(row))
            if previous.role == role and previous.parent_ids == parents and previous.transformation == transformation:
                return previous
        _insert_asset(connection, asset, path)
    return asset


def graph(identifier: str) -> list[SourceAsset]:
    found: dict[str, SourceAsset] = {}
    visiting: set[str] = set()
    def visit(current: str) -> None:
        if current in visiting:
            raise ProductionError('lineage_cycle')
        if current in found:
            return
        if len(found) + len(visiting) >= 512:
            raise ProductionError('lineage_capacity_exceeded')
        visiting.add(current)
        asset = get_asset(current)
        edges = {str(row['parent_id']) for row in db.get_db().execute(
            'SELECT parent_id FROM production_edges WHERE child_id=?', (current,))}
        if edges != set(asset.parent_ids):
            raise ProductionError('lineage_inconsistent')
        for parent in asset.parent_ids:
            visit(parent)
        visiting.remove(current)
        found[current] = asset
    visit(identifier)
    return list(found.values())


def validate_graph(identifier: str) -> ProductionGate:
    blockers: set[str] = set()
    try:
        assets = graph(identifier)
    except ProductionError as exc:
        return ProductionGate(blockers=[exc.code])
    for asset in assets:
        try:
            if export_provenance.digest(asset_path(asset.id)) != asset.sha256:
                blockers.add('artifact_changed')
        except (OSError, ProductionError, export_provenance.ProvenanceError):
            blockers.add('artifact_missing')
        if asset.reference_only:
            blockers.add('reference_only_source')
        if not asset.parent_ids:
            if asset.origin not in {'human_recorded', 'licensed_third_party'}:
                blockers.add('source_origin_unknown_or_generated')
            evidence = asset.declaration
            if evidence is None or not evidence.rights_holder.strip() or not evidence.evidence_ref.strip() or not evidence.permission_scope.strip():
                blockers.add('source_rights_missing')
            if evidence is None or 'production' not in evidence.permitted_uses or 'production' in evidence.blocked_uses:
                blockers.add('source_use_blocked')
            if not asset.reviews or asset.reviews[-1].status != 'approved' or not asset.declaration_revisions or asset.reviews[-1].declaration_revision_id != asset.declaration_revisions[-1].id:
                blockers.add('source_review_required')
    return ProductionGate(technical_eligible=not blockers, blockers=sorted(blockers))


def save_run(run: ProductionRun) -> None:
    with transaction() as connection:
        connection.execute('''INSERT INTO production_runs VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            track_id=excluded.track_id,version_id=excluded.version_id,body_json=excluded.body_json''',
            (run.id, run.track_id, run.audio_version_id, run.model_dump_json()))


def get_run(identifier: str) -> ProductionRun:
    row = db.get_db().execute('SELECT body_json FROM production_runs WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise ProductionError('production_run_missing')
    run = ProductionRun.model_validate_json(_body(row))
    run.release_reviews = [ReleaseReview.model_validate_json(_body(item)) for item in db.get_db().execute(
        'SELECT body_json FROM production_release_reviews WHERE run_id=? ORDER BY sequence', (identifier,))]
    return run


def list_runs() -> list[ProductionRun]:
    return [get_run(str(row['id'])) for row in db.get_db().execute('SELECT id FROM production_runs ORDER BY rowid DESC')]


def run_for_version(version_id: str) -> ProductionRun | None:
    row = db.get_db().execute('SELECT id FROM production_runs WHERE version_id=?', (version_id,)).fetchone()
    return get_run(str(row['id'])) if row else None


def review_release(identifier: str, request: ReleaseReviewRequest) -> ProductionRun:
    run = get_run(identifier)
    if run.status != 'done' or run.output_asset_id is None:
        raise ProductionError('production_output_missing')
    review = ReleaseReview(id=uuid.uuid4().hex, created_at=now(), **request.model_dump())
    with transaction() as connection:
        sequence = connection.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM production_release_reviews').fetchone()[0]
        connection.execute('INSERT INTO production_release_reviews VALUES(?,?,?,?)',
            (review.id, identifier, sequence, review.model_dump_json()))
    return get_run(identifier)


def production_provenance(version_id: str, *, require_release: bool = False) -> ProductionProvenance | None:
    from . import artists
    run = run_for_version(version_id)
    if run is None:
        if require_release:
            raise ProductionError('strict_lineage_missing')
        return None
    if run.status != 'done' or run.output_asset_id is None or run.audio_version_id is None:
        raise ProductionError('production_output_missing')
    artists.require_authorized(run.request.artist_id, run.request.consent_version_id, 'export', use='commercial' if require_release else 'production')
    gate = validate_graph(run.output_asset_id)
    if not gate.technical_eligible:
        raise ProductionError(gate.blockers[0])
    assets = graph(run.output_asset_id)
    legal_blockers: set[str] = set()
    if not run.model.dependency_inventory_complete:
        legal_blockers.add('dependency_inventory_incomplete')
    for asset in assets:
        if not asset.parent_ids and (asset.declaration is None or 'release' not in asset.declaration.permitted_uses or 'release' in asset.declaration.blocked_uses):
            legal_blockers.add('source_release_permission_missing')
    review = run.release_reviews[-1] if run.release_reviews else None
    if review is None:
        legal_blockers.add('human_release_review_required')
    else:
        for name in ('composition_rights', 'master_rights', 'voice_rights', 'dependency_rights'):
            if getattr(review, name) != 'approved':
                legal_blockers.add(name + '_needs_review')
    gate.approved_for_release = not legal_blockers
    gate.blockers.extend(sorted(legal_blockers))
    if require_release and not gate.approved_for_release:
        raise ProductionError(gate.blockers[0])
    return ProductionProvenance(run_id=run.id, project_id=run.project_id, artist_id=run.request.artist_id,
        consent_version_id=run.request.consent_version_id, audio_version_id=run.audio_version_id,
        graph_root_id=run.output_asset_id, graph_sha256=checksum([asset.model_dump(mode='json') for asset in assets]),
        assets=assets, model=run.model, gate=gate, release_reviews=run.release_reviews,
        range_assessment=run.range_assessment)


def append_usage(request: UsageEventInput) -> ModelUsageEvent:
    with transaction() as connection:
        previous = connection.execute('SELECT body_json FROM model_usage_events WHERE idempotency_key=?', (request.idempotency_key,)).fetchone()
        if previous is not None:
            existing = ModelUsageEvent.model_validate_json(_body(previous))
            if existing.model_dump(include=set(UsageEventInput.model_fields)) != request.model_dump():
                raise ProductionError('usage_idempotency_conflict')
            return existing
        head = connection.execute('SELECT sha256 FROM model_usage_events ORDER BY sequence DESC LIMIT 1').fetchone()
        previous_sha256 = str(head['sha256']) if head else '0' * 64
        event = ModelUsageEvent(id=uuid.uuid4().hex, created_at=now(), previous_sha256=previous_sha256,
            sha256='0' * 64, **request.model_dump())
        event.sha256 = checksum(event.model_dump(mode='json', exclude={'sha256'}))
        connection.execute('INSERT INTO model_usage_events(id,idempotency_key,sha256,body_json) VALUES(?,?,?,?)',
            (event.id, event.idempotency_key, event.sha256, event.model_dump_json()))
    return event


def usage_ledger() -> ModelUsageLedger:
    events = [ModelUsageEvent.model_validate_json(_body(row)) for row in db.get_db().execute('SELECT body_json FROM model_usage_events ORDER BY sequence')]
    head = '0' * 64
    valid = True
    for event in events:
        valid = valid and event.previous_sha256 == head and event.sha256 == checksum(event.model_dump(mode='json', exclude={'sha256'}))
        head = event.sha256
    return ModelUsageLedger(events=events, chain_valid=valid, head_sha256=head)


def usage_event(idempotency_key: str) -> ModelUsageEvent | None:
    row = db.get_db().execute('SELECT body_json FROM model_usage_events WHERE idempotency_key=?', (idempotency_key,)).fetchone()
    return ModelUsageEvent.model_validate_json(_body(row)) if row is not None else None


def verify_ledger() -> bool:
    return usage_ledger().chain_valid
