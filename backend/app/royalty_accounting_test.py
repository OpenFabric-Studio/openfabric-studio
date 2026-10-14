"""Synthetic distributor rows; never inspect real reports or transfer money."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
from typing import Literal
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app import artists, db, royalty_accounting as ledger
from app.artist_contracts import CreateArtistRequest
from app.artist_production_contracts import ModelFileIdentity, ProductionGate, ProductionProvenance, SourceAsset, VoiceModelSnapshot
from app.royalty_contracts import AgreementCreate, AdjustmentCreate, CsvImportCommit, CsvImportRequest, CsvMapping, ImportResult, PaymentCreate, ReleaseCreate


class RoyaltyAccountingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.object(db, 'DATA_DIR', self.root))
        self.enterContext(patch.object(db, 'DB_PATH', self.root / 'ledger.db'))
        self.enterContext(patch.object(db, '_db', None))
        self.connection = db.get_db()
        ledger.migrate(self.connection)
        self.addCleanup(self.close_db)
        self.artist = artists.create_artist(CreateArtistRequest(name='Synthetic artist', voice_id='a' * 32))
        roles: tuple[Literal['checkpoint', 'configuration', 'voice_reference'], ...] = ('checkpoint', 'configuration', 'voice_reference')
        files = [ModelFileIdentity(role=role, name=role, sha256='1' * 64) for role in roles]
        self.provenance = ProductionProvenance(run_id='b' * 32, project_id=1, artist_id=self.artist.id,
            consent_version_id='c' * 32, audio_version_id='d' * 32, graph_root_id='e' * 32,
            graph_sha256='2' * 64, assets=[SourceAsset(id='e' * 32, sha256='3' * 64, role='master',
                origin='unknown', provider='openfabric', created_at='2026-10-01', classification_basis='app_workflow', reference_only=False)],
            model=VoiceModelSnapshot(voice_id='a' * 32, model_revision_id='synthetic', files=files, diffusion_steps=30),
            gate=ProductionGate(technical_eligible=True, approved_for_release=True))
        self.enterContext(patch.object(ledger, 'production_provenance', return_value=self.provenance))
        self.agreement = ledger.create_agreement(self.agreement_request())
        self.release = ledger.create_release(ReleaseCreate(agreement_id=self.agreement.id, run_id='b' * 32,
            audio_version_id='d' * 32, platform='Synthetic distributor', master_id='master-1', master_sha256='3' * 64,
            release_date='2026-01-01', eligible=True, isrc=None, upc=None))

    def close_db(self) -> None:
        if db._db is not None:
            db._db.close()
            db._db = None

    def agreement_request(self, *, basis: str = 'contract_net', percentage: str = '12.5', version: str = 'signed-v1') -> AgreementCreate:
        return AgreementCreate.model_validate({'artist_id': self.artist.id, 'percentage': percentage,
            'basis': basis, 'currency': 'EUR', 'permitted_deduction_categories': ['distribution_fee'],
            'eligibility_start': '2026-01-01', 'eligibility_end': None,
            'evidence_ref': '/private/signed-contract.pdf', 'evidence_version': version})

    def request(self, rows: str, *, distributor: str = 'synthetic') -> CsvImportRequest:
        return CsvImportRequest(distributor=distributor,
            csv_text='release,period,amount,currency,row,category\n' + rows,
            mapping=CsvMapping(release_id='release', period='period', amount='amount', currency='currency',
                row_id='row', deduction_category='category'))

    def commit(self, request: CsvImportRequest) -> ImportResult:
        preview = ledger.preview_import(request)
        self.assertEqual(preview.errors, [])
        return ledger.commit_import(CsvImportCommit(request=request, preview_hash=preview.preview_hash))

    def test_decimal_refunds_deductions_statement_and_payment_are_separate(self) -> None:
        request = self.request(f'{self.release.id},2026-10,100.10,EUR,sale-1,\n'
                               f'{self.release.id},2026-10,-10.10,EUR,refund-1,\n'
                               f'{self.release.id},2026-10,-4,EUR,fee-1,distribution_fee\n')
        preview = ledger.preview_import(request)
        self.assertEqual(preview.totals[0].revenue, '90')
        self.assertEqual(preview.totals[0].deductions, '-4')
        self.assertEqual(preview.totals[0].royalty_base, '86')
        self.assertEqual(preview.totals[0].royalty_due, '10.75')
        imported = self.commit(request)
        statement = ledger.statement(self.agreement.id)
        self.assertEqual(statement.royalty_accrued, '10.75')
        self.assertEqual(statement.payments_total, '0')
        self.assertEqual(statement.import_ids, [imported.id])
        self.assertEqual([row.kind for row in statement.entries], ['revenue', 'refund', 'deduction'])
        ledger.create_adjustment(AdjustmentCreate(release_id=self.release.id, amount='-0.05', currency='EUR',
            occurred_on='2026-10-31', kind='correction', evidence_ref='/private/correction', idempotency_key='adjust-1'))
        ledger.record_payment(PaymentCreate(agreement_id=self.agreement.id, amount='5', currency='EUR',
            occurred_on='2026-11-01', evidence_ref='/private/bank', idempotency_key='payment-1'))
        statement = ledger.statement(self.agreement.id)
        self.assertEqual((statement.royalty_accrued, statement.adjustments_total, statement.payments_total, statement.balance_due),
                         ('10.75', '-0.05', '5', '5.7'))
        self.assertNotIn('/private', statement.model_dump_json())
        self.assertIsNone(statement.authenticated_actor)

    def test_missing_rate_floats_invalid_dates_and_unknown_fields_fail(self) -> None:
        valid = self.agreement_request().model_dump()
        missing = dict(valid)
        missing.pop('percentage')
        for values in (missing, {**valid, 'percentage': 12.5}, {**valid, 'percentage': '100.01'},
                       {**valid, 'percentage': 'NaN'}, {**valid, 'eligibility_start': '2026-02-30'},
                       {**valid, 'eligibility_end': '2025-12-31'}, {**valid, 'guaranteed_revenue': True}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                AgreementCreate.model_validate(values)

    def test_preview_reports_invalid_currency_period_release_amount_and_deduction(self) -> None:
        cases = (('EUR', '2026-10', '1e2', '', self.release.id, 'invalid_amount'),
                 ('USD', '2026-10', '10', '', self.release.id, 'currency_mismatch'),
                 ('EUR', '2025-12', '10', '', self.release.id, 'outside_eligibility'),
                 ('EUR', '2026-10', '-2', 'unagreed_fee', self.release.id, 'deduction_not_permitted'),
                 ('EUR', '2026-10', '10', '', 'f' * 32, 'release_missing'))
        for currency, period, amount, category, release_id, code in cases:
            request = self.request(f'{release_id},{period},{amount},{currency},row-1,{category}\n')
            preview = ledger.preview_import(request)
            self.assertIn(code, [error.code for error in preview.errors])
            with self.assertRaises(ledger.RoyaltyError):
                ledger.commit_import(CsvImportCommit(request=request, preview_hash=preview.preview_hash))
        self.assertEqual(ledger.statement(self.agreement.id).royalty_accrued, '0')

    def test_content_and_distributor_row_duplicates_are_idempotent_and_not_double_counted(self) -> None:
        request = self.request(f'{self.release.id},2026-10,80,EUR,row-1,\n')
        preview = ledger.preview_import(request)
        command = CsvImportCommit(request=request, preview_hash=preview.preview_hash)
        first = ledger.commit_import(command)
        self.assertEqual(ledger.commit_import(command).id, first.id)
        repeated = ledger.preview_import(request)
        self.assertTrue(repeated.duplicate_content)
        self.assertEqual(ledger.commit_import(CsvImportCommit(request=request, preview_hash=repeated.preview_hash)).id, first.id)
        altered = self.request(f'{self.release.id},2026-11,80,EUR,row-1,\n')
        duplicate = ledger.preview_import(altered)
        self.assertIn('duplicate_row', [error.code for error in duplicate.errors])
        self.assertEqual(ledger.statement(self.agreement.id).royalty_accrued, '10')
        self.assertFalse(ledger.preview_import(self.request(f'{self.release.id},2026-11,80,EUR,row-1,\n', distributor='other')).errors)

    def test_preview_hash_rejects_edited_content_and_unknown_hash(self) -> None:
        original = self.request(f'{self.release.id},2026-10,80,EUR,row-1,\n')
        preview = ledger.preview_import(original)
        changed = self.request(f'{self.release.id},2026-10,800,EUR,row-1,\n')
        with self.assertRaises(ledger.RoyaltyError) as caught:
            ledger.commit_import(CsvImportCommit(request=changed, preview_hash=preview.preview_hash))
        self.assertEqual(caught.exception.code, 'preview_changed')
        self.assertEqual(ledger.statement(self.agreement.id).royalty_accrued, '0')

    def test_commit_rolls_back_all_rows_after_storage_failure(self) -> None:
        self.connection.execute("CREATE TRIGGER fail_second BEFORE INSERT ON royalty_entries WHEN NEW.row_number=3 BEGIN SELECT RAISE(ABORT,'synthetic_failure'); END")
        self.connection.commit()
        request = self.request(f'{self.release.id},2026-10,80,EUR,row-1,\n{self.release.id},2026-10,40,EUR,row-2,\n')
        with self.assertLogs(ledger.logger, level='ERROR'), self.assertRaises(ledger.RoyaltyError):
            self.commit(request)
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM royalty_imports').fetchone()[0], 0)
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM royalty_entries').fetchone()[0], 0)

    def test_concurrent_repeated_commit_has_one_import_and_survives_reopen(self) -> None:
        request = self.request(f'{self.release.id},2026-10,0.1,EUR,row-1,\n')
        command = CsvImportCommit(request=request, preview_hash=ledger.preview_import(request).preview_hash)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(ledger.commit_import, [command, command]))
        self.assertEqual(results[0].id, results[1].id)
        ledger.migrate(self.connection)
        self.close_db()
        ledger.migrate(db.get_db())
        self.assertEqual(ledger.statement(self.agreement.id).royalty_accrued, '0.0125')

    def test_gross_basis_ignores_deductions_without_rounding(self) -> None:
        agreement = ledger.create_agreement(self.agreement_request(basis='gross', percentage='33.333333', version='signed-v2'))
        release = ledger.create_release(ReleaseCreate(agreement_id=agreement.id, run_id='b' * 32,
            audio_version_id='d' * 32, platform='Synthetic distributor', master_id='master-2', master_sha256='3' * 64,
            release_date='2026-01-01', eligible=True, isrc=None, upc=None))
        result = ledger.preview_import(self.request(f'{release.id},2026-10,0.01,EUR,sale-2,\n'
                                                    f'{release.id},2026-10,-0.005,EUR,fee-2,distribution_fee\n'))
        self.assertEqual(result.totals[0].royalty_base, '0.01')
        self.assertEqual(result.totals[0].royalty_due, '0.0033333333')

    def test_release_requires_matching_release_approved_lineage(self) -> None:
        request = ReleaseCreate(agreement_id=self.agreement.id, run_id='b' * 32, audio_version_id='d' * 32,
            platform='synthetic', master_id='master-3', master_sha256='4' * 64, release_date='2026-01-01', eligible=True, isrc=None, upc=None)
        with self.assertRaises(ledger.RoyaltyError) as caught:
            ledger.create_release(request)
        self.assertEqual(caught.exception.code, 'release_provenance_mismatch')
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute('DELETE FROM royalty_agreements WHERE id=?', (self.agreement.id,))

    def test_report_bounds_headers_malformed_csv_and_duplicate_rows_fail_closed(self) -> None:
        valid = self.request(f'{self.release.id},2026-10,1,EUR,row-1,\n')
        cases = (('release,release,amount,currency,row,category\n', 'invalid_csv_headers'),
                 ('release,period,amount,currency,row,category\n"unterminated', 'invalid_csv'),
                 (valid.csv_text + f'{self.release.id},2026-10,1,EUR,row-1,\n', 'duplicate_row'),
                 ('é' * 600000, 'report_too_large'))
        for text, code in cases:
            with self.subTest(code=code):
                preview = ledger.preview_import(CsvImportRequest(distributor='synthetic', csv_text=text, mapping=valid.mapping))
                self.assertIn(code, [issue.code for issue in preview.errors])
        many = self.request(''.join(f'{self.release.id},2026-10,1,EUR,row-{number},\n' for number in range(5001)))
        preview = ledger.preview_import(many)
        self.assertIn('too_many_rows', [issue.code for issue in preview.errors])
        self.assertEqual(preview.valid_row_count, 5000)
        with self.assertRaises(ledger.RoyaltyError):
            ledger.commit_import(CsvImportCommit(request=many, preview_hash=preview.preview_hash))
        self.assertEqual(ledger.statement(self.agreement.id).royalty_accrued, '0')

    def test_absent_row_ids_use_normalized_duplicate_identity(self) -> None:
        request = CsvImportRequest(distributor='synthetic', csv_text=f'release,date,amount,currency\n{self.release.id},2026-10-01,1.00,EUR\n',
            mapping=CsvMapping(release_id='release', period='date', amount='amount', currency='currency'))
        self.commit(request)
        changed = request.model_copy(update={'csv_text': request.csv_text.replace('1.00', '1')})
        self.assertIn('duplicate_row', [issue.code for issue in ledger.preview_import(changed).errors])

    def test_paid_and_adjustment_records_need_evidence_and_are_idempotent(self) -> None:
        adjustment = AdjustmentCreate(release_id=self.release.id, amount='-1', currency='EUR', occurred_on='2026-10-14',
            kind='refund', evidence_ref='private:refund', idempotency_key='adjust-1')
        payment = PaymentCreate(agreement_id=self.agreement.id, amount='10', currency='EUR', occurred_on='2026-10-14',
            evidence_ref='private:receipt', idempotency_key='payment-1')
        for request in (adjustment, payment):
            with self.assertRaises(ValidationError):
                type(request).model_validate({**request.model_dump(), 'evidence_ref': '   '})
        self.assertEqual(ledger.create_adjustment(adjustment), ledger.create_adjustment(adjustment))
        self.assertEqual(ledger.record_payment(payment), ledger.record_payment(payment))
        with self.assertRaises(ledger.RoyaltyError) as caught:
            ledger.record_payment(payment.model_copy(update={'amount': '20'}))
        self.assertEqual(caught.exception.code, 'idempotency_conflict')

    def test_monthly_period_is_not_silently_prorated_across_agreement_dates(self) -> None:
        agreement = ledger.create_agreement(AgreementCreate.model_validate({**self.agreement_request(version='partial-v1').model_dump(),
            'eligibility_start': '2026-10-15', 'eligibility_end': '2026-10-31'}))
        release = ledger.create_release(ReleaseCreate(agreement_id=agreement.id, run_id='b' * 32,
            audio_version_id='d' * 32, platform='synthetic', master_id='partial-master', master_sha256='3' * 64,
            release_date='2026-10-15', eligible=True))
        self.assertIn('outside_eligibility', [issue.code for issue in ledger.preview_import(
            self.request(f'{release.id},2026-10,1,EUR,row-1,\n')).errors])
        self.assertEqual(ledger.preview_import(self.request(f'{release.id},2026-10-15,1,EUR,row-1,\n')).errors, [])

    def test_storage_open_failure_returns_stable_error(self) -> None:
        with patch.object(db, 'DB_PATH', self.root), self.assertLogs(ledger.logger, level='ERROR'), self.assertRaises(ledger.RoyaltyError) as caught:
            ledger.list_agreements()
        self.assertEqual(caught.exception.code, 'royalty_storage_failed')
