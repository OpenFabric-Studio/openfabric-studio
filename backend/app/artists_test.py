"""Consent is append-only evidence, not an authenticated checkbox."""
from __future__ import annotations

import sqlite3
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app import artists, db
from app.artist_contracts import ArtistConsentRequest, ArtistOperation, ArtistUse, CreateArtistRequest


class ArtistConsentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.object(db, 'DATA_DIR', self.root))
        self.enterContext(patch.object(db, 'DB_PATH', self.root / 'catalog.db'))
        self.enterContext(patch.object(db, 'FILES_DIR', self.root / 'files'))
        self.enterContext(patch.object(db, '_db', None))
        self.connection = db.get_db()
        artists.migrate(self.connection)
        self.addCleanup(self.close_database)
        self.now = datetime(2026, 10, 14, tzinfo=timezone.utc)
        self.enterContext(patch.object(artists, '_now', return_value=self.now))
        self.voice_id = 'a' * 32
        self.profile = artists.create_artist(CreateArtistRequest(name='Singer', voice_id=self.voice_id))

    def close_database(self) -> None:
        if db._db is not None:
            db._db.close()
            db._db = None

    def active_request(self, *, evidence: str = 'agreement:one') -> ArtistConsentRequest:
        return ArtistConsentRequest(status='active', signed_evidence_ref=evidence,
            signed_evidence_sha256='1' * 64, operator_label='Local operator',
            human_declaration='I reviewed the signed evidence and declare that it authorizes these operations and uses for this artist.',
            allowed_operations=['train', 'convert', 'export'], allowed_uses=['evaluation', 'production', 'commercial'])

    def assert_blocked(self, code: str, operation: ArtistOperation = 'convert') -> None:
        with self.assertRaises(artists.ArtistError) as caught:
            artists.require_authorized_voice(self.voice_id, operation)
        self.assertEqual(caught.exception.code, code)

    def test_migration_is_additive_idempotent_and_profiles_survive_reopen(self) -> None:
        before = self.connection.execute('SELECT COUNT(*) FROM tracks').fetchone()[0]
        artists.migrate(self.connection)
        self.close_database()
        artists.migrate(db.get_db())
        self.assertEqual(artists.get_artist(self.profile.id), self.profile)
        self.assertEqual(artists.list_artists(), [self.profile])
        self.assertEqual(db.get_db().execute('SELECT COUNT(*) FROM tracks').fetchone()[0], before)

    def test_concurrent_cold_open_waits_for_completed_migrations(self) -> None:
        self.close_database()
        entered, release, second_started, second_returned = (threading.Event() for _ in range(4))
        migrate = artists.migrate
        def paused_migration(connection: sqlite3.Connection) -> None:
            entered.set()
            if not release.wait(10):
                raise AssertionError('Fixture migration was not released')
            migrate(connection)
        def second_open() -> sqlite3.Connection:
            second_started.set()
            connection = db.get_db()
            second_returned.set()
            return connection
        with patch.object(db, 'DB_PATH', self.root / 'cold.db'), patch.object(artists, 'migrate', side_effect=paused_migration):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(db.get_db)
                try:
                    self.assertTrue(entered.wait(10))
                    second = executor.submit(second_open)
                    self.assertTrue(second_started.wait(10))
                    returned_early = second_returned.wait(.1)
                finally:
                    release.set()
                connection = first.result()
                self.assertIs(second.result(), connection)
                self.assertFalse(returned_early, 'Another worker received a partly migrated catalog')
                self.assertEqual(connection.execute('SELECT count(*) FROM artist_profiles').fetchone()[0], 0)

    def test_core_migration_failure_discards_partial_connection_before_retry(self) -> None:
        self.close_database()
        with patch.object(db, 'DB_PATH', self.root / 'failed-core.db'):
            with patch.object(db, 'ensure_track_codes', side_effect=sqlite3.OperationalError('synthetic disk full')):
                with self.assertRaises(sqlite3.OperationalError):
                    db.get_db()
            self.assertIsNone(db._db)
            self.assertEqual(db.get_db().execute('SELECT count(*) FROM artist_profiles').fetchone()[0], 0)

    def test_unbound_legacy_voices_pass_but_registered_artists_require_evidence(self) -> None:
        self.assertIsNone(artists.require_authorized_voice('b' * 32, 'convert'))
        self.assert_blocked('artist_consent_required')
        review = artists.add_consent_revision(self.profile.id, ArtistConsentRequest(status='needs_review'))
        self.assertEqual(review.status, 'needs_review')
        self.assert_blocked('artist_consent_needs_review')

    def test_concurrent_authorization_reads_preserve_current_consent_identity(self) -> None:
        consent = artists.add_consent_revision(self.profile.id, self.active_request())
        barrier = threading.Barrier(4)
        def read_permission() -> None:
            for _ in range(100):
                barrier.wait(timeout=10)
                try:
                    self.assertEqual(artists.require_authorized_voice(self.voice_id, 'convert'), consent)
                except BaseException:
                    barrier.abort()
                    raise
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(read_permission) for _ in range(4)]
            for future in futures:
                future.result()

    def test_active_consent_requires_signed_evidence_and_human_attestation(self) -> None:
        valid = self.active_request().model_dump()
        invalid_fields: tuple[dict[str, object], ...] = (
            {'signed_evidence_ref': None}, {'operator_label': ''}, {'human_declaration': ''},
            {'human_declaration': True}, {'allowed_operations': []}, {'allowed_uses': []},
            {'signed_evidence_sha256': 'not-a-digest'}, {'actor_authenticated': True})
        for fields in invalid_fields:
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                ArtistConsentRequest.model_validate({**valid, **fields})
        granted = artists.add_consent_revision(self.profile.id, self.active_request())
        self.assertFalse(granted.actor_authenticated)
        self.assertIsNone(granted.authenticated_actor_id)
        self.assertEqual(artists.require_authorized_voice(self.voice_id, 'convert'), granted)
        self.assertEqual(artists.get_artist(self.profile.id).current_consent_version_id, granted.id)

    def test_operations_uses_and_territories_fail_closed(self) -> None:
        request = ArtistConsentRequest.model_validate({**self.active_request().model_dump(),
            'allowed_operations': ['train'], 'allowed_uses': ['evaluation'], 'territories': ['ES']})
        consent = artists.add_consent_revision(self.profile.id, request)
        cases: tuple[tuple[ArtistOperation, ArtistUse, str | None, str], ...] = (
            ('convert', 'evaluation', 'ES', 'artist_operation_not_allowed'),
            ('train', 'commercial', 'ES', 'artist_use_not_allowed'),
            ('train', 'evaluation', None, 'artist_territory_not_allowed'),
            ('train', 'evaluation', 'US', 'artist_territory_not_allowed'),
        )
        for operation, use, territory, code in cases:
            with self.subTest(operation=operation, use=use, territory=territory), self.assertRaises(artists.ArtistError) as caught:
                artists.require_authorized(self.profile.id, consent.id, operation, use=use, territory=territory)
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(artists.require_authorized(self.profile.id, consent.id, 'train', use='evaluation', territory='ES'), consent)

    def test_named_operator_restrictions_are_normalized_and_preserved_after_restart(self) -> None:
        request = ArtistConsentRequest.model_validate({**self.active_request().model_dump(),
            'authorized_operators': ['  Alex  Studio  ', 'Mari\u0301a López']})
        consent = artists.add_consent_revision(self.profile.id, request)
        self.assertEqual(consent.model_dump().get('authorized_operators'), ['Alex Studio', 'María López'])
        self.close_database()
        self.assertEqual(artists.current_consent(self.profile.id), consent)
        self.assertEqual(artists.list_consents(self.profile.id), [consent])
        self.assert_blocked('artist_operator_authentication_required')

    def test_a_declared_operator_label_cannot_satisfy_named_operator_restrictions(self) -> None:
        request = ArtistConsentRequest.model_validate({**self.active_request().model_dump(),
            'authorized_operators': ['Local operator']})
        consent = artists.add_consent_revision(self.profile.id, request)
        self.assertFalse(consent.actor_authenticated)
        self.assertIsNone(consent.authenticated_actor_id)
        operations: tuple[ArtistOperation, ...] = ('train', 'convert', 'export')
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(artists.ArtistError) as caught:
                    artists.require_authorized(self.profile.id, consent.id, operation)
                self.assertEqual((caught.exception.code, caught.exception.status_code), ('artist_operator_authentication_required', 403))
        self.assert_blocked('artist_operator_authentication_required')

    def test_operator_lists_reject_malformed_names_bounds_and_normalized_duplicates(self) -> None:
        invalid: tuple[object, ...] = (None, 'Local operator', {'name': 'Local operator'}, [False], [12],
            [''], ['   '], ['operator\nname'], ['operator\u200bname'], ['x' * 121],
            [f'operator {index}' for index in range(101)], ['Operator', 'Operator'],
            [' Operator  Name ', 'Operator Name'], ['Operator', 'operator'], ['Café', 'Cafe\u0301'])
        for operators in invalid:
            with self.subTest(operators=operators), self.assertRaises(ValidationError):
                ArtistConsentRequest.model_validate({'authorized_operators': operators})

    def test_legacy_consent_without_an_operator_list_retains_declaration_only_behavior(self) -> None:
        consent = artists.add_consent_revision(self.profile.id, self.active_request())
        self.assertEqual(consent.model_dump().get('authorized_operators'), [])
        payload = consent.model_dump()
        payload.pop('authorized_operators')
        self.connection.execute('DROP TRIGGER artist_consent_no_update')
        self.connection.execute('UPDATE artist_consent_versions SET payload_json=? WHERE id=?', (json.dumps(payload), consent.id))
        self.connection.commit()
        self.close_database()
        reopened = artists.require_authorized_voice(self.voice_id, 'convert')
        self.assertIsNotNone(reopened)
        self.assertEqual(reopened, consent)
        self.assertFalse(consent.actor_authenticated)
        self.assertIsNone(consent.authenticated_actor_id)

    def test_validity_interval_rejects_future_expired_and_naive_dates(self) -> None:
        valid = self.active_request().model_dump()
        for fields, code in (({'starts_at': '2026-10-15T00:00:00Z'}, 'artist_consent_not_started'),
                             ({'ends_at': '2026-10-14T00:00:00Z'}, 'artist_consent_expired')):
            with self.subTest(fields=fields):
                artists.add_consent_revision(self.profile.id, ArtistConsentRequest.model_validate({**valid, **fields}))
                self.assert_blocked(code)
        for fields in ({'starts_at': '2026-10-13'}, {'ends_at': '2026-10-15T00:00:00'},
                       {'starts_at': '2026-10-15T00:00:00Z', 'ends_at': '2026-10-14T00:00:00Z'}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                ArtistConsentRequest.model_validate({**valid, **fields})
        request = ArtistConsentRequest.model_validate({**valid, 'starts_at': '2026-10-14T01:00:00+01:00',
            'ends_at': '2026-10-15T00:00:00Z'})
        consent = artists.add_consent_revision(self.profile.id, request)
        self.assertEqual(artists.require_authorized_voice(self.voice_id, 'convert'), consent)

    def test_revocation_preserves_history_and_cannot_reuse_revoked_evidence(self) -> None:
        granted = artists.add_consent_revision(self.profile.id, self.active_request())
        revoked = artists.add_consent_revision(self.profile.id, ArtistConsentRequest(status='revoked',
            operator_label='Local operator', human_declaration='The artist withdrew this permission.'))
        self.assert_blocked('artist_consent_revoked')
        with self.assertRaises(artists.ArtistError) as stale:
            artists.require_authorized(self.profile.id, granted.id, 'convert')
        self.assertEqual(stale.exception.code, 'artist_consent_stale')
        with self.assertRaises(artists.ArtistError) as reused:
            artists.add_consent_revision(self.profile.id, self.active_request())
        self.assertEqual(reused.exception.code, 'artist_new_consent_evidence_required')
        artists.add_consent_revision(self.profile.id, ArtistConsentRequest(status='needs_review'))
        with self.assertRaises(artists.ArtistError):
            artists.add_consent_revision(self.profile.id, self.active_request(evidence='agreement:copied'))
        new_request = ArtistConsentRequest.model_validate({**self.active_request(evidence='agreement:two').model_dump(),
            'signed_evidence_sha256': '2' * 64})
        renewed = artists.add_consent_revision(self.profile.id, new_request)
        self.assertEqual(artists.require_authorized_voice(self.voice_id, 'convert'), renewed)
        self.assertEqual(artists.list_consents(self.profile.id)[:2], [granted, revoked])
        self.assertEqual(granted.status, 'active')
        self.assertEqual(revoked.status, 'revoked')

    def test_new_revision_invalidates_an_exact_prior_consent_reference(self) -> None:
        first = artists.add_consent_revision(self.profile.id, self.active_request())
        second = artists.add_consent_revision(self.profile.id, self.active_request(evidence='agreement:updated'))
        with self.assertRaises(artists.ArtistError) as caught:
            artists.require_authorized(self.profile.id, first.id, 'train')
        self.assertEqual(caught.exception.code, 'artist_consent_stale')
        self.assertEqual(artists.require_authorized(self.profile.id, second.id, 'train'), second)

    def test_binding_is_unique_and_cannot_detach_or_rebind(self) -> None:
        unbound = artists.create_artist(CreateArtistRequest(name='Another singer'))
        bound = artists.bind_voice(unbound.id, 'b' * 32)
        self.assertEqual(bound.voice_id, 'b' * 32)
        self.assertEqual(artists.bind_voice(unbound.id, 'b' * 32), bound)
        with self.assertRaises(artists.ArtistError) as rebound:
            artists.bind_voice(unbound.id, 'c' * 32)
        self.assertEqual(rebound.exception.code, 'artist_voice_binding_immutable')
        with self.assertRaises(artists.ArtistError) as duplicate:
            artists.create_artist(CreateArtistRequest(name='Imposter', voice_id=self.voice_id))
        self.assertEqual(duplicate.exception.code, 'artist_voice_already_bound')
        for sql in ("UPDATE artist_profiles SET voice_id=NULL WHERE id=?", "DELETE FROM artist_profiles WHERE id=?"):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                self.connection.execute(sql, (self.profile.id,))
            self.connection.rollback()
        self.assert_blocked('artist_consent_required')

    def test_consent_rows_cannot_be_updated_or_deleted(self) -> None:
        consent = artists.add_consent_revision(self.profile.id, self.active_request())
        for sql in ("UPDATE artist_consent_versions SET payload_json='{}' WHERE id=?",
                    'DELETE FROM artist_consent_versions WHERE id=?'):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                self.connection.execute(sql, (consent.id,))
            self.connection.rollback()
        self.assertEqual(artists.current_consent(self.profile.id), consent)

    def test_concurrent_appends_get_distinct_ordered_revisions(self) -> None:
        def append(index: int) -> int:
            result = artists.add_consent_revision(self.profile.id, self.active_request(evidence=f'agreement:{index}'))
            return result.revision
        with ThreadPoolExecutor(max_workers=4) as executor:
            revisions = list(executor.map(append, range(8)))
        self.assertEqual(sorted(revisions), list(range(1, 9)))
        self.assertEqual([row.revision for row in artists.list_consents(self.profile.id)], list(range(1, 9)))

    def test_missing_artist_and_invalid_identifiers_are_stable_errors(self) -> None:
        for identifier in ('missing', 'c' * 32):
            with self.subTest(identifier=identifier), self.assertRaises(artists.ArtistError) as caught:
                artists.get_artist(identifier)
            self.assertEqual(caught.exception.code, 'artist_missing')
        with self.assertRaises(artists.ArtistError) as caught:
            artists.require_authorized_voice('../voice', 'convert')
        self.assertEqual(caught.exception.code, 'artist_voice_invalid')

    def test_missing_schema_reports_a_stable_storage_error(self) -> None:
        self.connection.execute('DROP TABLE artist_consent_versions')
        self.connection.execute('DROP TABLE artist_profiles')
        self.connection.commit()
        for action in (lambda: artists.get_artist(self.profile.id), artists.list_artists,
                       lambda: artists.require_authorized_voice(self.voice_id, 'convert')):
            with self.subTest(action=action):
                with self.assertRaises(artists.ArtistError) as caught, self.assertLogs('app.artists', level='ERROR'):
                    action()
                self.assertEqual(caught.exception.code, 'artist_storage_failed')

    def test_unsigned_blank_evidence_and_duplicate_scopes_are_rejected(self) -> None:
        for fields in ({'signed_evidence_ref': '   '}, {'allowed_operations': ['train', 'train']},
                       {'allowed_uses': ['evaluation', 'evaluation']}, {'territories': ['es', 'ES']}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                ArtistConsentRequest.model_validate(fields)

    def test_invalid_stored_identity_or_actor_cannot_authorize_a_voice(self) -> None:
        consent = artists.add_consent_revision(self.profile.id, self.active_request())
        self.connection.execute('DROP TRIGGER artist_consent_no_update')
        for field, value in (('actor_authenticated', True), ('artist_id', 'b' * 32)):
            corrupted = {**consent.model_dump(), field: value}
            self.connection.execute('UPDATE artist_consent_versions SET payload_json=? WHERE id=?',
                (json.dumps(corrupted), consent.id))
            self.connection.commit()
            self.assert_blocked('artist_metadata_invalid')


if __name__ == '__main__':
    unittest.main()
