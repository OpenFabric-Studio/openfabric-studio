"""Append-only distributor accounting; generation jobs never create revenue."""
from __future__ import annotations

import calendar
from collections.abc import Iterator
from contextlib import contextmanager
import csv
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import io
import json
import logging
import re
import sqlite3
from typing import Literal, TypeVar
import uuid

from pydantic import BaseModel, TypeAdapter, ValidationError

from . import db
from .artist_production import production_provenance
from .royalty_contracts import (Agreement, AgreementCreate, Adjustment, AdjustmentCreate, Amount,
    CsvImportCommit, CsvImportRequest, ImportIssue, ImportPreview, ImportResult, Payment,
    PaymentCreate, ReconciliationTotal, Release, ReleaseCreate, RevenueRow, RoyaltyStatement,
    StatementAdjustment, StatementEntry, StatementPayment)

logger = logging.getLogger(__name__)
MAX_REPORT_BYTES = 1_000_000
MAX_REPORT_ROWS = 5000
_amount = TypeAdapter[str](Amount)
_Model = TypeVar('_Model', bound=BaseModel)
_Table = Literal['royalty_agreements', 'royalty_releases', 'royalty_imports', 'royalty_adjustments', 'royalty_payments']


class RoyaltyError(Exception):
    def __init__(self, code: str, status_code: int = 409) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def migrate(connection: sqlite3.Connection) -> None:
    """Add the local ledger without changing existing tracks or agreements."""
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS royalty_agreements (
            id TEXT PRIMARY KEY, artist_id TEXT NOT NULL REFERENCES artist_profiles(id),
            evidence_version TEXT NOT NULL, body_json TEXT NOT NULL CHECK(json_valid(body_json)),
            UNIQUE(artist_id,evidence_version));
        CREATE TABLE IF NOT EXISTS royalty_releases (
            id TEXT PRIMARY KEY, agreement_id TEXT NOT NULL REFERENCES royalty_agreements(id),
            platform TEXT NOT NULL, master_id TEXT NOT NULL,
            body_json TEXT NOT NULL CHECK(json_valid(body_json)), UNIQUE(agreement_id,platform,master_id));
        CREATE TABLE IF NOT EXISTS royalty_imports (
            id TEXT PRIMARY KEY, distributor TEXT NOT NULL, content_sha256 TEXT NOT NULL,
            input_sha256 TEXT NOT NULL, preview_hash TEXT NOT NULL,
            body_json TEXT NOT NULL CHECK(json_valid(body_json)), UNIQUE(distributor,content_sha256));
        CREATE TABLE IF NOT EXISTS royalty_entries (
            id TEXT PRIMARY KEY, import_id TEXT NOT NULL REFERENCES royalty_imports(id),
            release_id TEXT NOT NULL REFERENCES royalty_releases(id),
            agreement_id TEXT NOT NULL REFERENCES royalty_agreements(id),
            distributor TEXT NOT NULL, row_fingerprint TEXT NOT NULL, row_number INTEGER NOT NULL CHECK(row_number>=2),
            body_json TEXT NOT NULL CHECK(json_valid(body_json)), UNIQUE(distributor,row_fingerprint));
        CREATE INDEX IF NOT EXISTS royalty_entries_agreement ON royalty_entries(agreement_id);
        CREATE TABLE IF NOT EXISTS royalty_adjustments (
            id TEXT PRIMARY KEY, agreement_id TEXT NOT NULL REFERENCES royalty_agreements(id),
            release_id TEXT NOT NULL REFERENCES royalty_releases(id), idempotency_key TEXT NOT NULL UNIQUE,
            body_json TEXT NOT NULL CHECK(json_valid(body_json)));
        CREATE TABLE IF NOT EXISTS royalty_payments (
            id TEXT PRIMARY KEY, agreement_id TEXT NOT NULL REFERENCES royalty_agreements(id),
            idempotency_key TEXT NOT NULL UNIQUE, body_json TEXT NOT NULL CHECK(json_valid(body_json)));
        CREATE TABLE IF NOT EXISTS royalty_schema (
            id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL CHECK(version=1));
        INSERT OR IGNORE INTO royalty_schema VALUES(1,1);
    ''')
    for table in ('royalty_agreements', 'royalty_releases', 'royalty_imports', 'royalty_entries', 'royalty_adjustments', 'royalty_payments'):
        for operation in ('UPDATE', 'DELETE'):
            connection.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()}
                BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'royalty_evidence_immutable'); END''')
    connection.commit()


@contextmanager
def _connection(*, write: bool = False) -> Iterator[sqlite3.Connection]:
    connection: sqlite3.Connection | None = None
    try:
        db.get_db()
        connection = sqlite3.connect(db.DB_PATH, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        yield connection
        connection.commit()
    except sqlite3.Error as exc:
        if connection is not None:
            connection.rollback()
        logger.exception('Royalty ledger storage failed')
        raise RoyaltyError('royalty_storage_failed', 503) from exc
    except BaseException:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _body(row: sqlite3.Row, model: type[_Model]) -> _Model:
    payload: object = row['body_json']
    if not isinstance(payload, str):
        raise RoyaltyError('royalty_metadata_invalid')
    try:
        return model.model_validate_json(payload)
    except ValidationError as exc:
        raise RoyaltyError('royalty_metadata_invalid') from exc


def _get(connection: sqlite3.Connection, table: _Table, identifier: str, model: type[_Model], code: str) -> _Model:
    row = connection.execute(f'SELECT body_json FROM {table} WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise RoyaltyError(code, 404)
    return _body(row, model)


def create_agreement(request: AgreementCreate) -> Agreement:
    with _connection(write=True) as connection:
        previous = connection.execute('SELECT body_json FROM royalty_agreements WHERE artist_id=? AND evidence_version=?',
                                      (request.artist_id, request.evidence_version)).fetchone()
        if previous is not None:
            agreement = _body(previous, Agreement)
            if AgreementCreate.model_validate(agreement.model_dump(include=set(AgreementCreate.model_fields))) != request:
                raise RoyaltyError('agreement_version_conflict')
            return agreement
        if connection.execute('SELECT 1 FROM artist_profiles WHERE id=?', (request.artist_id,)).fetchone() is None:
            raise RoyaltyError('artist_missing', 404)
        agreement = Agreement(id=uuid.uuid4().hex, created_at=_now(), **request.model_dump())
        connection.execute('INSERT INTO royalty_agreements VALUES(?,?,?,?)',
                           (agreement.id, agreement.artist_id, agreement.evidence_version, agreement.model_dump_json()))
        return agreement


def list_agreements() -> list[Agreement]:
    with _connection() as connection:
        return [_body(row, Agreement) for row in connection.execute('SELECT body_json FROM royalty_agreements ORDER BY rowid')]


def create_release(request: ReleaseCreate) -> Release:
    provenance = production_provenance(request.audio_version_id, require_release=True)
    with _connection(write=True) as connection:
        agreement = _get(connection, 'royalty_agreements', request.agreement_id, Agreement, 'agreement_missing')
        if (provenance is None or provenance.run_id != request.run_id or provenance.artist_id != agreement.artist_id
                or provenance.audio_version_id != request.audio_version_id or not provenance.gate.approved_for_release
                or not any(asset.id == provenance.graph_root_id and asset.sha256 == request.master_sha256 for asset in provenance.assets)):
            raise RoyaltyError('release_provenance_mismatch')
        previous = connection.execute('SELECT body_json FROM royalty_releases WHERE agreement_id=? AND platform=? AND master_id=?',
                                      (request.agreement_id, request.platform, request.master_id)).fetchone()
        if previous is not None:
            release = _body(previous, Release)
            if ReleaseCreate.model_validate(release.model_dump(include=set(ReleaseCreate.model_fields))) != request:
                raise RoyaltyError('release_identity_conflict')
            return release
        release = Release(id=uuid.uuid4().hex, created_at=_now(), **request.model_dump())
        connection.execute('INSERT INTO royalty_releases VALUES(?,?,?,?,?)',
                           (release.id, release.agreement_id, release.platform, release.master_id, release.model_dump_json()))
        return release


def list_releases() -> list[Release]:
    with _connection() as connection:
        return [_body(row, Release) for row in connection.execute('SELECT body_json FROM royalty_releases ORDER BY rowid')]


def _decimal(value: Decimal) -> str:
    if value == 0:
        return '0'
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _period(value: str) -> tuple[str, str]:
    if re.fullmatch(r'[0-9]{4}-[0-9]{2}', value):
        start = date.fromisoformat(value + '-01')
        return start.isoformat(), start.replace(day=calendar.monthrange(start.year, start.month)[1]).isoformat()
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise ValueError('invalid_period')
    day = date.fromisoformat(value).isoformat()
    return day, day


def _eligible(release: Release, agreement: Agreement, start: str, end: str) -> None:
    if (not release.eligible or end < release.release_date or start < agreement.eligibility_start
            or agreement.eligibility_end is not None and end > agreement.eligibility_end):
        raise RoyaltyError('outside_eligibility')


def _parse_row(connection: sqlite3.Connection, request: CsvImportRequest, columns: list[str], values: list[str], number: int) -> RevenueRow:
    if len(values) != len(columns) or any(len(value) > 20_000 for value in values):
        raise RoyaltyError('invalid_csv_row')
    fields = dict(zip(columns, values))
    mapping = request.mapping
    release = _get(connection, 'royalty_releases', fields[mapping.release_id].strip(), Release, 'release_missing')
    agreement = _get(connection, 'royalty_agreements', release.agreement_id, Agreement, 'agreement_missing')
    currency = fields[mapping.currency].strip()
    if currency != agreement.currency:
        raise RoyaltyError('currency_mismatch')
    try:
        amount = Decimal(_amount.validate_python(fields[mapping.amount].strip()))
    except ValidationError as exc:
        raise RoyaltyError('invalid_amount') from exc
    try:
        start, end = _period(fields[mapping.period].strip())
    except ValueError as exc:
        raise RoyaltyError('invalid_period') from exc
    _eligible(release, agreement, start, end)
    category = fields[mapping.deduction_category].strip() or None if mapping.deduction_category else None
    if category is not None and (category not in agreement.permitted_deduction_categories or amount >= 0):
        raise RoyaltyError('deduction_not_permitted')
    source_id = fields[mapping.row_id].strip() or None if mapping.row_id else None
    fingerprint = _hash(['source_id', source_id]) if source_id is not None else _hash(
        [release.id, start, end, _decimal(amount), currency, category])
    base = amount if category is None or agreement.basis == 'contract_net' else Decimal('0')
    try:
        return RevenueRow(row_number=number, release_id=release.id, agreement_id=agreement.id, period_start=start, period_end=end,
            amount=_decimal(amount), currency=currency, source_row_id=source_id, deduction_category=category,
            kind='deduction' if category is not None else 'refund' if amount < 0 else 'revenue',
            royalty_base=_decimal(base), royalty_due=_decimal(base * Decimal(agreement.percentage) / Decimal('100')),
            row_fingerprint=fingerprint)
    except ValidationError as exc:
        raise RoyaltyError('invalid_csv_row') from exc


def _totals(rows: list[RevenueRow]) -> list[ReconciliationTotal]:
    groups: dict[str, list[RevenueRow]] = {}
    for row in rows:
        groups.setdefault(row.agreement_id, []).append(row)
    totals: list[ReconciliationTotal] = []
    for agreement_id, group in sorted(groups.items()):
        revenue = sum((Decimal(row.amount) for row in group if row.kind != 'deduction'), Decimal('0'))
        deductions = sum((Decimal(row.amount) for row in group if row.kind == 'deduction'), Decimal('0'))
        totals.append(ReconciliationTotal(agreement_id=agreement_id, currency=group[0].currency, row_count=len(group),
            revenue=_decimal(revenue), deductions=_decimal(deductions), input_total=_decimal(revenue + deductions),
            royalty_base=_decimal(sum((Decimal(row.royalty_base) for row in group), Decimal('0'))),
            royalty_due=_decimal(sum((Decimal(row.royalty_due) for row in group), Decimal('0')))))
    return totals


def _input_hash(request: CsvImportRequest, content_hash: str) -> str:
    return _hash([request.distributor, content_hash, request.mapping.model_dump()])


def _preview(connection: sqlite3.Connection, request: CsvImportRequest) -> ImportPreview:
    content = request.csv_text.encode('utf-8')
    content_hash = hashlib.sha256(content).hexdigest()
    existing = connection.execute('SELECT body_json FROM royalty_imports WHERE distributor=? AND content_sha256=?',
                                  (request.distributor, content_hash)).fetchone()
    prior = _body(existing, ImportResult) if existing is not None else None
    rows: list[RevenueRow] = []
    errors: list[ImportIssue] = []
    count = 0
    fingerprints: set[str] = set()
    evidence: dict[str, tuple[str, str]] = {}
    if len(content) > MAX_REPORT_BYTES:
        errors.append(ImportIssue(row_number=0, code='report_too_large'))
    else:
        reader = csv.reader(io.StringIO(request.csv_text.removeprefix('\ufeff')), strict=True)
        try:
            columns = next(reader, [])
            mapped = [value for value in request.mapping.model_dump().values() if isinstance(value, str)]
            if not columns or len(columns) > 64 or len(set(columns)) != len(columns) or not all(value in columns for value in mapped):
                errors.append(ImportIssue(row_number=0, code='invalid_csv_headers'))
            else:
                for number, values in enumerate(reader, 2):
                    if not values:
                        continue
                    count += 1
                    if count > MAX_REPORT_ROWS:
                        errors.append(ImportIssue(row_number=number, code='too_many_rows'))
                        break
                    try:
                        row = _parse_row(connection, request, columns, values, number)
                        release = _get(connection, 'royalty_releases', row.release_id, Release, 'release_missing')
                        agreement = _get(connection, 'royalty_agreements', row.agreement_id, Agreement, 'agreement_missing')
                        evidence[release.id] = (release.model_dump_json(), agreement.model_dump_json())
                        if row.row_fingerprint in fingerprints or connection.execute(
                                'SELECT 1 FROM royalty_entries WHERE distributor=? AND row_fingerprint=?',
                                (request.distributor, row.row_fingerprint)).fetchone() is not None:
                            raise RoyaltyError('duplicate_row')
                        fingerprints.add(row.row_fingerprint)
                        rows.append(row)
                    except RoyaltyError as exc:
                        errors.append(ImportIssue(row_number=number, code=exc.code))
        except (csv.Error, UnicodeError):
            errors.append(ImportIssue(row_number=count + 2, code='invalid_csv'))
    if not rows and not errors:
        errors.append(ImportIssue(row_number=0, code='empty_report'))
    totals = _totals(rows)
    preview_hash = _hash([_input_hash(request, content_hash), [row.model_dump() for row in rows],
                          [issue.model_dump() for issue in errors], evidence, prior.id if prior else None])
    return ImportPreview(content_sha256=content_hash, preview_hash=preview_hash,
        duplicate_content=prior is not None, existing_import_id=prior.id if prior else None,
        rows=rows, errors=errors, totals=totals, input_row_count=count, valid_row_count=len(rows))


def preview_import(request: CsvImportRequest) -> ImportPreview:
    with _connection() as connection, localcontext() as context:
        context.prec = 80
        return _preview(connection, request)


def commit_import(command: CsvImportCommit) -> ImportResult:
    request = command.request
    content_hash = hashlib.sha256(request.csv_text.encode('utf-8')).hexdigest()
    input_hash = _input_hash(request, content_hash)
    with _connection(write=True) as connection, localcontext() as context:
        context.prec = 80
        existing = connection.execute('SELECT * FROM royalty_imports WHERE distributor=? AND content_sha256=?',
                                      (request.distributor, content_hash)).fetchone()
        if existing is not None and existing['input_sha256'] == input_hash and existing['preview_hash'] == command.preview_hash:
            return _body(existing, ImportResult)
        preview = _preview(connection, request)
        if preview.preview_hash != command.preview_hash:
            raise RoyaltyError('preview_changed')
        if existing is not None:
            if existing['input_sha256'] != input_hash:
                raise RoyaltyError('duplicate_import_conflict')
            return _body(existing, ImportResult)
        if preview.errors or not preview.rows:
            raise RoyaltyError('import_has_errors')
        imported = ImportResult(id=uuid.uuid4().hex, distributor=request.distributor, content_sha256=content_hash,
            preview_hash=preview.preview_hash, row_count=len(preview.rows), totals=preview.totals, created_at=_now())
        connection.execute('INSERT INTO royalty_imports VALUES(?,?,?,?,?,?)',
                           (imported.id, request.distributor, content_hash, input_hash, preview.preview_hash, imported.model_dump_json()))
        for row in preview.rows:
            entry = StatementEntry(id=uuid.uuid4().hex, import_id=imported.id, **row.model_dump())
            connection.execute('INSERT INTO royalty_entries VALUES(?,?,?,?,?,?,?,?)',
                (entry.id, imported.id, row.release_id, row.agreement_id, request.distributor,
                 row.row_fingerprint, row.row_number, entry.model_dump_json()))
        return imported


def create_adjustment(request: AdjustmentCreate) -> Adjustment:
    with _connection(write=True) as connection:
        release = _get(connection, 'royalty_releases', request.release_id, Release, 'release_missing')
        agreement = _get(connection, 'royalty_agreements', release.agreement_id, Agreement, 'agreement_missing')
        _eligible(release, agreement, request.occurred_on, request.occurred_on)
        if request.currency != agreement.currency:
            raise RoyaltyError('currency_mismatch')
        previous = connection.execute('SELECT body_json FROM royalty_adjustments WHERE idempotency_key=?', (request.idempotency_key,)).fetchone()
        if previous is not None:
            saved = _body(previous, Adjustment)
            if AdjustmentCreate.model_validate(saved.model_dump(include=set(AdjustmentCreate.model_fields))) != request:
                raise RoyaltyError('idempotency_conflict')
            return saved
        adjustment = Adjustment(id=uuid.uuid4().hex, agreement_id=agreement.id, created_at=_now(), **request.model_dump())
        connection.execute('INSERT INTO royalty_adjustments VALUES(?,?,?,?,?)',
                           (adjustment.id, agreement.id, release.id, request.idempotency_key, adjustment.model_dump_json()))
        return adjustment


def record_payment(request: PaymentCreate) -> Payment:
    with _connection(write=True) as connection:
        agreement = _get(connection, 'royalty_agreements', request.agreement_id, Agreement, 'agreement_missing')
        if request.currency != agreement.currency:
            raise RoyaltyError('currency_mismatch')
        previous = connection.execute('SELECT body_json FROM royalty_payments WHERE idempotency_key=?', (request.idempotency_key,)).fetchone()
        if previous is not None:
            saved = _body(previous, Payment)
            if PaymentCreate.model_validate(saved.model_dump(include=set(PaymentCreate.model_fields))) != request:
                raise RoyaltyError('idempotency_conflict')
            return saved
        payment = Payment(id=uuid.uuid4().hex, created_at=_now(), **request.model_dump())
        connection.execute('INSERT INTO royalty_payments VALUES(?,?,?,?)',
                           (payment.id, agreement.id, request.idempotency_key, payment.model_dump_json()))
        return payment


def statement(agreement_id: str) -> RoyaltyStatement:
    with _connection() as connection, localcontext() as context:
        context.prec = 80
        agreement = _get(connection, 'royalty_agreements', agreement_id, Agreement, 'agreement_missing')
        releases = [_body(row, Release) for row in connection.execute(
            'SELECT body_json FROM royalty_releases WHERE agreement_id=? ORDER BY rowid', (agreement_id,))]
        entries = [_body(row, StatementEntry) for row in connection.execute(
            'SELECT body_json FROM royalty_entries WHERE agreement_id=? ORDER BY rowid', (agreement_id,))]
        adjustments = [_body(row, Adjustment) for row in connection.execute(
            'SELECT body_json FROM royalty_adjustments WHERE agreement_id=? ORDER BY rowid', (agreement_id,))]
        payments = [_body(row, Payment) for row in connection.execute(
            'SELECT body_json FROM royalty_payments WHERE agreement_id=? ORDER BY rowid', (agreement_id,))]
        accrued = sum((Decimal(row.royalty_due) for row in entries), Decimal('0'))
        adjusted = sum((Decimal(row.amount) for row in adjustments), Decimal('0'))
        paid = sum((Decimal(row.amount) for row in payments), Decimal('0'))
        return RoyaltyStatement(agreement_id=agreement.id, artist_id=agreement.artist_id,
            agreement_evidence_version=agreement.evidence_version, percentage=agreement.percentage,
            basis=agreement.basis, currency=agreement.currency, eligibility_start=agreement.eligibility_start,
            eligibility_end=agreement.eligibility_end, releases=[release for release in releases if release.eligible],
            import_ids=list(dict.fromkeys(row.import_id for row in entries)), entries=entries,
            adjustments=[StatementAdjustment.model_validate(row.model_dump(include=set(StatementAdjustment.model_fields))) for row in adjustments],
            payments=[StatementPayment.model_validate(row.model_dump(include=set(StatementPayment.model_fields))) for row in payments],
            royalty_accrued=_decimal(accrued), adjustments_total=_decimal(adjusted), payments_total=_decimal(paid),
            balance_due=_decimal(accrued + adjusted - paid))
