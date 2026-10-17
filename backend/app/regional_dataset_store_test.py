"""Revision CAS and idempotent preparation survive errors without losing reviews."""
from __future__ import annotations

import sqlite3
import unittest

from app import regional_dataset_store as store
from app.regional_dataset_contracts import RegionalDatasetError, RegionalPreparationRequest
from app.regional_datasets_test import review
from app.regional_acoustic_contracts import RegionalAcousticScanRequest


class RegionalDatasetStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)
        store.migrate(self.connection)

    def test_legacy_review_hashes_do_not_change_when_acoustic_evidence_is_absent(self) -> None:
        import hashlib
        import json
        request = review('a', 'b')
        body = request.model_dump(mode='json'); body.pop('acoustic_review', None)
        previous = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'),
            ensure_ascii=True, allow_nan=False).encode('ascii')).hexdigest()
        self.assertEqual(store.canonical_sha256(request), previous)
        saved = store.save_review(self.connection, request)
        raw = saved.model_dump(mode='json'); raw['request'] = body
        self.connection.execute('UPDATE regional_dataset_reviews SET body_json=?', (json.dumps(raw),)); self.connection.commit()
        self.assertEqual(store.get_review(self.connection, request.dataset_id).sha256, previous)

    def test_scan_submissions_bind_saved_review_and_replay_the_exact_job(self) -> None:
        saved = store.save_review(self.connection, review('a', 'b'))
        request = RegionalAcousticScanRequest(dataset_id=saved.request.dataset_id, review_revision=saved.revision,
            review_sha256=saved.sha256, submission_key='f' * 32)
        first, created = store.create_acoustic_scan(self.connection, request)
        again, repeated = store.create_acoustic_scan(self.connection, request)
        self.assertTrue(created); self.assertFalse(repeated); self.assertEqual(first, again)
        with self.assertRaises(RegionalDatasetError):
            store.create_acoustic_scan(self.connection, request.model_copy(update={'review_sha256': '0' * 64}))
        self.connection.execute('UPDATE regional_acoustic_scans SET request_sha256=?', ('0' * 64,)); self.connection.commit()
        with self.assertRaises(RegionalDatasetError):
            store.get_acoustic_scan(self.connection, first.id)

    def test_reviews_are_cas_revisions_and_old_content_remains_readable(self) -> None:
        initial = store.save_review(self.connection, review('a', 'b'))
        self.assertEqual(initial.revision, 1)
        changed = initial.request.model_copy(update={'expected_revision': 1, 'tradition': 'Edited declaration'})
        saved = store.save_review(self.connection, changed)
        self.assertEqual(saved.revision, 2)
        with self.assertRaises(RegionalDatasetError) as stale:
            store.save_review(self.connection, changed)
        self.assertEqual(stale.exception.code, 'regional_dataset_revision_conflict')
        self.assertEqual(store.get_review(self.connection, initial.request.dataset_id, 1), initial)
        self.assertEqual(store.get_review(self.connection, initial.request.dataset_id), saved)

    def test_repeated_submission_keeps_one_job_and_changed_selection_is_rejected(self) -> None:
        saved = store.save_review(self.connection, review('a', 'b'))
        request = RegionalPreparationRequest(dataset_id=saved.request.dataset_id, review_revision=saved.revision,
            review_sha256=saved.sha256, submission_key='e' * 32)
        first, created = store.create_preparation(self.connection, request, 4)
        second, repeated = store.create_preparation(self.connection, request, 4)
        self.assertTrue(created); self.assertFalse(repeated)
        self.assertEqual(first, second)
        with self.assertRaises(RegionalDatasetError):
            store.create_preparation(self.connection, request.model_copy(update={'review_sha256': 'f' * 64}), 4)

    def test_corrupt_review_is_retained_and_reported_without_becoming_a_valid_manifest(self) -> None:
        saved = store.save_review(self.connection, review('a', 'b'))
        self.connection.execute('UPDATE regional_dataset_reviews SET review_sha256=?', ('f' * 64,)); self.connection.commit()
        with self.assertRaises(RegionalDatasetError) as damaged:
            store.get_review(self.connection, saved.request.dataset_id)
        self.assertEqual(damaged.exception.code, 'regional_dataset_storage_unavailable')
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM regional_dataset_reviews').fetchone()[0], 1)
