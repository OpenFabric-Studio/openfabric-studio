"""Explicit local royalty terms; decimal values are strings on the wire."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, ConfigDict, Field, StrictBool, model_validator

from .contracts import Contract

Identifier = Annotated[str, Field(strict=True, pattern=r'^[0-9a-f]{32}$')]
Sha256 = Annotated[str, Field(strict=True, pattern=r'^[0-9a-f]{64}$')]
PublicId = Annotated[str, Field(strict=True, min_length=1, max_length=120, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]*$')]
Currency = Annotated[str, Field(strict=True, pattern=r'^[A-Z]{3}$')]
Category = Annotated[str, Field(strict=True, max_length=64, pattern=r'^[a-z][a-z0-9_]*$')]
Amount = Annotated[str, Field(strict=True, max_length=21, pattern=r'^-?(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?$')]
DecimalString = Annotated[str, Field(strict=True, max_length=80, pattern=r'^-?(0|[1-9][0-9]*)(\.[0-9]+)?$')]


def _date(value: str) -> str:
    date.fromisoformat(value)
    return value


def _percentage(value: str) -> str:
    if not Decimal('0') <= Decimal(value) <= Decimal('100'):
        raise ValueError('percentage_out_of_range')
    return value


DateString = Annotated[str, Field(strict=True, pattern=r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$'), AfterValidator(_date)]
Percentage = Annotated[Amount, AfterValidator(_percentage)]


class RoyaltyContract(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class AgreementCreate(RoyaltyContract):
    artist_id: Identifier
    percentage: Percentage
    basis: Literal['gross', 'contract_net']
    currency: Currency
    permitted_deduction_categories: list[Category] = Field(max_length=32)
    eligibility_start: DateString
    eligibility_end: DateString | None
    evidence_ref: str = Field(strict=True, min_length=1, max_length=500)
    evidence_version: PublicId

    @model_validator(mode='after')
    def validate_terms(self) -> AgreementCreate:
        if self.eligibility_end is not None and self.eligibility_end < self.eligibility_start:
            raise ValueError('invalid_eligibility_dates')
        if len(set(self.permitted_deduction_categories)) != len(self.permitted_deduction_categories):
            raise ValueError('duplicate_deduction_category')
        if not self.evidence_ref.strip():
            raise ValueError('agreement_evidence_required')
        return self


class Agreement(AgreementCreate):
    id: Identifier
    created_at: str
    authenticated_actor: None = None


class AgreementsResponse(RoyaltyContract):
    agreements: list[Agreement]


class ReleaseCreate(RoyaltyContract):
    agreement_id: Identifier
    run_id: Identifier
    audio_version_id: Identifier
    platform: str = Field(strict=True, min_length=1, max_length=80, pattern=r'^[A-Za-z0-9][A-Za-z0-9 ._-]*$')
    master_id: PublicId
    master_sha256: Sha256
    isrc: Annotated[str, Field(strict=True, pattern=r'^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$')] | None = None
    upc: Annotated[str, Field(strict=True, pattern=r'^[0-9]{12,14}$')] | None = None
    release_date: DateString
    eligible: StrictBool


class Release(ReleaseCreate):
    id: Identifier
    created_at: str
    authenticated_actor: None = None


class ReleasesResponse(RoyaltyContract):
    releases: list[Release]


class CsvMapping(RoyaltyContract):
    release_id: str = Field(strict=True, min_length=1, max_length=120)
    period: str = Field(strict=True, min_length=1, max_length=120)
    amount: str = Field(strict=True, min_length=1, max_length=120)
    currency: str = Field(strict=True, min_length=1, max_length=120)
    row_id: str | None = Field(default=None, min_length=1, max_length=120)
    deduction_category: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode='after')
    def distinct_columns(self) -> CsvMapping:
        columns = [value for value in (self.release_id, self.period, self.amount, self.currency,
                                      self.row_id, self.deduction_category) if value is not None]
        if len(set(columns)) != len(columns):
            raise ValueError('duplicate_csv_mapping')
        return self


class CsvImportRequest(RoyaltyContract):
    distributor: PublicId
    csv_text: str = Field(strict=True, min_length=1, max_length=1_000_000)
    mapping: CsvMapping


class CsvImportCommit(RoyaltyContract):
    request: CsvImportRequest
    preview_hash: Sha256


class ImportIssue(RoyaltyContract):
    row_number: int = Field(ge=0)
    code: str


class RevenueRow(RoyaltyContract):
    row_number: int = Field(ge=2)
    release_id: Identifier
    agreement_id: Identifier
    period_start: DateString
    period_end: DateString
    amount: Amount
    currency: Currency
    source_row_id: PublicId | None = None
    deduction_category: Category | None = None
    kind: Literal['revenue', 'refund', 'deduction']
    royalty_base: DecimalString
    royalty_due: DecimalString
    row_fingerprint: Sha256


class ReconciliationTotal(RoyaltyContract):
    agreement_id: Identifier
    currency: Currency
    row_count: int = Field(ge=0)
    revenue: DecimalString
    deductions: DecimalString
    input_total: DecimalString
    royalty_base: DecimalString
    royalty_due: DecimalString


class ImportPreview(RoyaltyContract):
    content_sha256: Sha256
    preview_hash: Sha256
    duplicate_content: bool
    existing_import_id: Identifier | None = None
    rows: list[RevenueRow] = Field(max_length=5000)
    errors: list[ImportIssue] = Field(max_length=5001)
    totals: list[ReconciliationTotal]
    input_row_count: int = Field(ge=0)
    valid_row_count: int = Field(ge=0)


class ImportResult(RoyaltyContract):
    id: Identifier
    distributor: PublicId
    content_sha256: Sha256
    preview_hash: Sha256
    row_count: int = Field(ge=1, le=5000)
    totals: list[ReconciliationTotal]
    created_at: str
    authenticated_actor: None = None


class AdjustmentCreate(RoyaltyContract):
    release_id: Identifier
    amount: Amount
    currency: Currency
    occurred_on: DateString
    kind: Literal['correction', 'refund', 'contract_adjustment']
    evidence_ref: str = Field(strict=True, min_length=1, max_length=500)
    idempotency_key: PublicId

    @model_validator(mode='after')
    def signed_adjustment(self) -> AdjustmentCreate:
        if Decimal(self.amount) == 0 or self.kind == 'refund' and Decimal(self.amount) >= 0:
            raise ValueError('invalid_adjustment_amount')
        if not self.evidence_ref.strip():
            raise ValueError('adjustment_evidence_required')
        return self


class Adjustment(AdjustmentCreate):
    id: Identifier
    agreement_id: Identifier
    created_at: str
    authenticated_actor: None = None


class PaymentCreate(RoyaltyContract):
    agreement_id: Identifier
    amount: Amount
    currency: Currency
    occurred_on: DateString
    evidence_ref: str = Field(strict=True, min_length=1, max_length=500)
    idempotency_key: PublicId

    @model_validator(mode='after')
    def positive_payment(self) -> PaymentCreate:
        if Decimal(self.amount) <= 0:
            raise ValueError('positive_payment_required')
        if not self.evidence_ref.strip():
            raise ValueError('payment_evidence_required')
        return self


class Payment(PaymentCreate):
    id: Identifier
    created_at: str
    authenticated_actor: None = None


class StatementEntry(RevenueRow):
    id: Identifier
    import_id: Identifier


class StatementAdjustment(RoyaltyContract):
    id: Identifier
    release_id: Identifier
    amount: Amount
    currency: Currency
    occurred_on: DateString
    kind: Literal['correction', 'refund', 'contract_adjustment']


class StatementPayment(RoyaltyContract):
    id: Identifier
    amount: Amount
    currency: Currency
    occurred_on: DateString


class RoyaltyStatement(RoyaltyContract):
    schema_version: Literal[1] = 1
    agreement_id: Identifier
    artist_id: Identifier
    agreement_evidence_version: PublicId
    percentage: Percentage
    basis: Literal['gross', 'contract_net']
    currency: Currency
    eligibility_start: DateString
    eligibility_end: DateString | None
    releases: list[Release]
    import_ids: list[Identifier]
    entries: list[StatementEntry]
    adjustments: list[StatementAdjustment]
    payments: list[StatementPayment]
    royalty_accrued: DecimalString
    adjustments_total: DecimalString
    payments_total: DecimalString
    balance_due: DecimalString
    authenticated_actor: None = None
    actor_context: Literal['unauthenticated_local_operator'] = 'unauthenticated_local_operator'


ROYALTY_CLIENT_MODELS: list[type[Contract]] = [AgreementCreate, Agreement, AgreementsResponse,
    ReleaseCreate, Release, ReleasesResponse, CsvMapping, CsvImportRequest, CsvImportCommit,
    ImportIssue, RevenueRow, ReconciliationTotal, ImportPreview, ImportResult, AdjustmentCreate,
    Adjustment, PaymentCreate, Payment, StatementEntry, StatementAdjustment, StatementPayment, RoyaltyStatement]
