from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app import replacement_store as store
from app.replacement_contracts import ReplacementPlanResponse


class ReplacementStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.path = self.root / 'catalog.db'
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA foreign_keys=ON')
        self.connection.execute('CREATE TABLE tracks(id INTEGER PRIMARY KEY,audio_path TEXT NOT NULL)')
        self.connection.execute("INSERT INTO tracks VALUES(1,'retained-original.wav')")
        self.connection.commit()
        self.addCleanup(self.connection.close)

    def plan(self, *, identifier: str = 'a' * 32, revision: int = 1, title: str = 'Original') -> ReplacementPlanResponse:
        return ReplacementPlanResponse.model_validate_json(json.dumps({'id': identifier, 'revision': revision,
            'request': {'mode': 'prepared', 'title': title,
                'source': {'source': {'kind': 'version', 'track_id': 1, 'version_id': 'b' * 32}, 'audio_sha256': 'c' * 64},
                'prepared': {'source': {'kind': 'version', 'track_id': 2, 'version_id': 'd' * 32}, 'audio_sha256': 'e' * 64}},
            'updated_at': '2026-10-17T10:00:00Z'}))

    def test_migration_is_additive_idempotent_and_preserves_existing_tracks(self) -> None:
        store.migrate(self.connection)
        store.migrate(self.connection)
        self.assertEqual(tuple(self.connection.execute('SELECT * FROM tracks').fetchone()), (1, 'retained-original.wav'))
        self.assertEqual(self.connection.execute('SELECT version FROM replacement_schema').fetchone()[0], 1)
        names = {row[0] for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({'replacement_plans', 'replacement_revisions', 'replacement_attempts',
            'replacement_artifacts', 'replacement_renders'}.issubset(names))

    def test_future_schema_fails_closed_without_creating_current_tables(self) -> None:
        self.connection.execute('CREATE TABLE replacement_schema(version INTEGER NOT NULL)')
        self.connection.execute('INSERT INTO replacement_schema VALUES(99)')
        self.connection.commit()
        before = self.connection.iterdump()
        captured = list(before)
        with self.assertRaises(store.ReplacementStoreError) as error:
            store.migrate(self.connection)
        self.assertEqual(error.exception.code, 'replacement_storage_incompatible')
        self.assertEqual(list(self.connection.iterdump()), captured)

    def test_failed_ddl_rolls_back_on_copied_database(self) -> None:
        copy = sqlite3.connect(self.root / 'copy.db')
        self.addCleanup(copy.close)
        copy.row_factory = sqlite3.Row
        self.connection.backup(copy)
        before = list(copy.iterdump())
        def deny_index(action: int, name: str | None, table: str | None, database: str | None, source: str | None) -> int:
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_INDEX and name is not None and name.startswith('idx_replacement') else sqlite3.SQLITE_OK
        copy.set_authorizer(deny_index)
        with self.assertRaises(store.ReplacementStoreError):
            store.migrate(copy)
        copy.set_authorizer(None)
        self.assertEqual(list(copy.iterdump()), before)
        self.assertEqual(tuple(self.connection.execute('SELECT * FROM tracks').fetchone()), (1, 'retained-original.wav'))

    def test_two_connections_cannot_overwrite_a_revision_or_its_history(self) -> None:
        store.migrate(self.connection)
        original = store.create_plan(self.connection, self.plan())
        other = sqlite3.connect(self.path)
        other.row_factory = sqlite3.Row
        other.execute('PRAGMA foreign_keys=ON')
        self.addCleanup(other.close)
        self.assertEqual(store.get_plan(other, original.id).revision, 1)
        updated = store.replace_plan(self.connection, 1, self.plan(revision=2, title='Accepted edit'))
        self.assertEqual(updated.request.title, 'Accepted edit')
        with self.assertRaises(store.ReplacementStoreError) as error:
            store.replace_plan(other, 1, self.plan(revision=2, title='Stale edit'))
        self.assertEqual(error.exception.code, 'replacement_revision_conflict')
        self.assertEqual(error.exception.current_revision, 2)
        self.assertEqual(store.get_plan(other, original.id).request.title, 'Accepted edit')
        self.assertEqual(store.get_revision(other, original.id, 1).request.title, 'Original')
        self.assertEqual(other.execute('SELECT COUNT(*) FROM replacement_revisions').fetchone()[0], 2)
        with self.assertRaises(sqlite3.IntegrityError):
            other.execute("UPDATE replacement_revisions SET body_json='{}'")

    def test_database_constraints_reject_invalid_state_even_without_python_models(self) -> None:
        store.migrate(self.connection)
        plan = store.create_plan(self.connection, self.plan())
        for statement, parameters in (("UPDATE replacement_plans SET status='imagined' WHERE id=?", (plan.id,)),
                ('UPDATE replacement_plans SET revision=0 WHERE id=?', (plan.id,)),
                ("UPDATE replacement_plans SET request_sha256='bad' WHERE id=?", (plan.id,))):
            with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                self.connection.execute(statement, parameters)
            self.connection.rollback()

    def test_action_reconnect_reuses_owned_attempt_before_admission_and_revision_checks(self) -> None:
        from app.replacement_contracts import ReplacementActionRequest
        store.migrate(self.connection)
        plan = store.create_plan(self.connection, self.plan())
        request = ReplacementActionRequest(expected_revision=1, request_id='f' * 32)
        attempt = store.StoredReplacementAttempt(id='1' * 32, plan_id=plan.id, revision=1,
            action='prepare', request=request, phase='separating', created_at=plan.updated_at)
        accepted = store.claim_attempt(self.connection, attempt)
        self.assertEqual(store.get_plan(self.connection, plan.id).active_attempt_id, attempt.id)
        def denied() -> None:
            raise AssertionError('reattach re-ran setup/terms admission')
        reconnected = store.claim_attempt(self.connection, attempt.model_copy(update={'id': '2' * 32}), admit=denied)
        self.assertEqual(reconnected.id, accepted.id)
        finished = store.StoredReplacementAttempt.model_validate({**attempt.model_dump(), 'status': 'done'})
        state = ReplacementPlanResponse.model_validate({**store.get_plan(self.connection, plan.id).model_dump(),
            'status': 'needs_review', 'active_attempt_id': None})
        store.finish_attempt(self.connection, finished, state)
        store.replace_plan(self.connection, 1, self.plan(revision=2, title='Later edit'))
        self.assertEqual(store.claim_attempt(self.connection, attempt, admit=denied).status, 'done')

    def test_conflicting_action_ids_and_fresh_admission_failure_leave_no_queue_mutation(self) -> None:
        from app.replacement_contracts import ReplacementActionRequest
        store.migrate(self.connection)
        plan = store.create_plan(self.connection, self.plan())
        attempt = store.StoredReplacementAttempt(id='1' * 32, plan_id=plan.id, revision=1,
            action='prepare', request=ReplacementActionRequest(expected_revision=1, request_id='f' * 32),
            phase='separating', created_at=plan.updated_at)
        def denied() -> None:
            raise RuntimeError('setup is busy')
        with self.assertRaises(RuntimeError):
            store.claim_attempt(self.connection, attempt, admit=denied)
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM replacement_attempts').fetchone()[0], 0)
        self.assertIsNone(store.get_plan(self.connection, plan.id).active_attempt_id)
        store.claim_attempt(self.connection, attempt)
        conflict = attempt.model_copy(update={'revision': 2,
            'request': ReplacementActionRequest(expected_revision=2, request_id='f' * 32)})
        with self.assertRaises(store.ReplacementStoreError) as error:
            store.claim_attempt(self.connection, conflict)
        self.assertEqual(error.exception.code, 'replacement_request_conflict')
        sibling = attempt.model_copy(update={'id': '2' * 32,
            'request': ReplacementActionRequest(expected_revision=1, request_id='e' * 32)})
        with self.assertRaises(store.ReplacementStoreError) as busy:
            store.claim_attempt(self.connection, sibling)
        self.assertEqual(busy.exception.code, 'replacement_busy')
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM replacement_attempts').fetchone()[0], 1)

    def artifact(self, plan_id: str, identifier: str = '3' * 32):
        from app.audio_quality_contracts import AudioMetrics
        from app.replacement_contracts import ReplacementArtifact
        artifact = ReplacementArtifact(id=identifier, evidence_asset_id='4' * 32, role='master',
            sha256='5' * 64, source_sha256s=['c' * 64], settings_sha256='6' * 64,
            duration_sec=1.0, sample_rate_hz=48000, channels=2,
            audio_url=f'/api/voices/replace/plans/{plan_id}/artifacts/{identifier}/audio',
            measurement=AudioMetrics(duration_sec=1.0, source_sha256='5' * 64, samples_analyzed=96000))
        return store.StoredReplacementArtifact(plan_id=plan_id, public=artifact,
            relative_path=f'replacements/{plan_id}/artifacts/{identifier}.wav', duration_frames=48000,
            created_at='2026-10-17T10:00:00Z')

    def test_render_attachment_rolls_back_with_track_then_attaches_once_and_keeps_deletion_tombstone(self) -> None:
        from app.replacement_contracts import ReplacementMixSettings, ReplacementRender, ReplacementRenderRequest
        store.migrate(self.connection)
        plan = store.create_plan(self.connection, self.plan())
        artifact = store.add_artifact(self.connection, self.artifact(plan.id))
        request = ReplacementRenderRequest(expected_revision=1, request_id='f' * 32, mix=ReplacementMixSettings())
        attempt = store.StoredReplacementAttempt(id='2' * 32, plan_id=plan.id, revision=1,
            action='render', request=request, phase='publishing', created_at=plan.updated_at)
        store.claim_attempt(self.connection, attempt)
        render = store.create_render(self.connection, ReplacementRender(id=attempt.id, plan_id=plan.id, revision=1,
            request_id=request.request_id, status='running', master_artifact_id=artifact.public.id,
            settings_sha256='6' * 64, created_at=plan.updated_at), request)
        self.connection.execute('BEGIN IMMEDIATE')
        self.connection.execute("INSERT INTO tracks VALUES(2,'promoted-master.wav')")
        store.attach_render(self.connection, render.id, 2)
        self.assertTrue(self.connection.in_transaction, 'attachment committed its caller transaction')
        self.connection.rollback()
        self.assertIsNone(store.get_render(self.connection, render.id).track_id)
        self.assertIsNone(self.connection.execute('SELECT id FROM tracks WHERE id=2').fetchone())
        self.connection.execute('BEGIN IMMEDIATE')
        self.connection.execute("INSERT INTO tracks VALUES(2,'promoted-master.wav')")
        store.attach_render(self.connection, render.id, 2)
        self.connection.commit()
        self.assertEqual(store.get_render(self.connection, render.id).track_id, 2)
        self.connection.execute('BEGIN IMMEDIATE')
        self.connection.execute("INSERT INTO tracks VALUES(3,'duplicate-master.wav')")
        with self.assertRaises(store.ReplacementStoreError):
            store.attach_render(self.connection, render.id, 3)
        self.connection.rollback()
        self.connection.execute('DELETE FROM tracks WHERE id=2')
        self.connection.commit()
        tombstone = store.get_render(self.connection, render.id)
        self.assertEqual(tombstone.status, 'done')
        self.assertTrue(tombstone.track_deleted)
        self.assertIsNone(tombstone.track_id)

    def test_foreign_artifacts_and_mutated_artifact_metadata_are_rejected(self) -> None:
        from app.replacement_contracts import ReplacementMixSettings, ReplacementRender, ReplacementRenderRequest
        store.migrate(self.connection)
        first = store.create_plan(self.connection, self.plan())
        second = store.create_plan(self.connection, self.plan(identifier='7' * 32))
        artifact = store.add_artifact(self.connection, self.artifact(first.id))
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute('UPDATE replacement_artifacts SET duration_frames=1 WHERE id=?', (artifact.public.id,))
        self.connection.rollback()
        request = ReplacementRenderRequest(expected_revision=1, request_id='f' * 32, mix=ReplacementMixSettings())
        attempt = store.StoredReplacementAttempt(id='2' * 32, plan_id=second.id, revision=1,
            action='render', request=request, phase='publishing', created_at=second.updated_at)
        store.claim_attempt(self.connection, attempt)
        with self.assertRaises(store.ReplacementStoreError):
            store.create_render(self.connection, ReplacementRender(id=attempt.id, plan_id=second.id, revision=1,
                request_id=request.request_id, status='running', master_artifact_id=artifact.public.id,
                settings_sha256='6' * 64, created_at=second.updated_at), request)

    def test_source_filtered_summaries_apply_filter_before_limit_without_losing_older_plans(self) -> None:
        store.migrate(self.connection)
        target = store.create_plan(self.connection, self.plan())
        for index in range(101):
            body = self.plan(identifier=f'{index + 100:032x}').model_dump()
            body['request']['source']['source']['track_id'] = 200
            store.create_plan(self.connection, ReplacementPlanResponse.model_validate(body))
        global_page = store.list_plans(self.connection, limit=100)
        self.assertEqual(len(global_page.plans), 100)
        self.assertNotIn(target.id, [plan.id for plan in global_page.plans])
        scoped = store.list_plans(self.connection, limit=100, source=target.request.source.source)
        self.assertEqual([plan.id for plan in scoped.plans], [target.id])
        self.assertIsNone(scoped.next_cursor)

    def test_unversioned_partial_layout_is_not_silently_claimed_as_a_new_schema(self) -> None:
        self.connection.execute('CREATE TABLE replacement_renders(id TEXT PRIMARY KEY)')
        self.connection.commit()
        before = list(self.connection.iterdump())
        with self.assertRaises(store.ReplacementStoreError) as error:
            store.migrate(self.connection)
        self.assertEqual(error.exception.code, 'replacement_storage_incompatible')
        self.assertEqual(list(self.connection.iterdump()), before)

    def test_unverified_cleanup_keeps_owner_busy_until_a_verified_drain(self) -> None:
        from app.replacement_contracts import ReplacementActionRequest
        from app.video_process import WorkerIdentity
        store.migrate(self.connection)
        plan = store.create_plan(self.connection, self.plan())
        attempt = store.StoredReplacementAttempt(id='1' * 32, plan_id=plan.id, revision=1,
            action='prepare', request=ReplacementActionRequest(expected_revision=1, request_id='f' * 32),
            phase='separating', created_at=plan.updated_at)
        store.claim_attempt(self.connection, attempt)
        failed = store.StoredReplacementAttempt.model_validate({**attempt.model_dump(), 'status': 'failed',
            'cleanup_pending': True, 'error_code': 'replacement_cleanup_pending',
            'worker': WorkerIdentity(pid=12345678, token='2' * 32, receipt='/temporary-test/worker.json')})
        state = ReplacementPlanResponse.model_validate({**store.get_plan(self.connection, plan.id).model_dump(),
            'status': 'failed', 'error_code': 'replacement_cleanup_pending'})
        store.finish_attempt(self.connection, failed, state)
        self.assertEqual([item.id for item in store.active_attempts(self.connection)], [attempt.id])
        with self.assertRaises(store.ReplacementStoreError):
            store.delete_plan(self.connection, plan.id, plan.updated_at)
        drained = store.StoredReplacementAttempt.model_validate({**failed.model_dump(), 'status': 'cancelled',
            'cleanup_pending': False, 'worker': None, 'error_code': None})
        cancelled = ReplacementPlanResponse.model_validate({**state.model_dump(), 'status': 'cancelled',
            'active_attempt_id': None, 'error_code': None})
        store.finish_attempt(self.connection, drained, cancelled)
        self.assertEqual(store.active_attempts(self.connection), [])
        store.delete_plan(self.connection, plan.id, plan.updated_at)
        self.assertEqual(store.list_plans(self.connection).plans, [])
        self.assertEqual(tuple(self.connection.execute('SELECT * FROM tracks').fetchone()), (1, 'retained-original.wav'))

    def test_attempt_phase_updates_cannot_rebind_request_or_resurrect_a_finished_worker(self) -> None:
        from app.replacement_contracts import ReplacementActionRequest
        store.migrate(self.connection)
        plan = store.create_plan(self.connection, self.plan())
        attempt = store.StoredReplacementAttempt(id='1' * 32, plan_id=plan.id, revision=1,
            action='prepare', request=ReplacementActionRequest(expected_revision=1, request_id='f' * 32),
            phase='separating', created_at=plan.updated_at)
        store.claim_attempt(self.connection, attempt)
        running = store.StoredReplacementAttempt.model_validate({**attempt.model_dump(), 'status': 'running', 'phase': 'analyzing'})
        store.save_attempt(self.connection, running)
        self.assertEqual(store.get_attempt(self.connection, attempt.id).phase, 'analyzing')
        with self.assertRaises(store.ReplacementStoreError):
            store.save_attempt(self.connection, running.model_copy(update={'request':
                ReplacementActionRequest(expected_revision=1, request_id='e' * 32)}))
        done = store.StoredReplacementAttempt.model_validate({**running.model_dump(), 'status': 'done'})
        state = ReplacementPlanResponse.model_validate({**store.get_plan(self.connection, plan.id).model_dump(),
            'status': 'needs_review', 'active_attempt_id': None})
        store.finish_attempt(self.connection, done, state)
        with self.assertRaises(store.ReplacementStoreError):
            store.save_attempt(self.connection, running)

    def test_private_music_evidence_survives_artifact_reload_without_leaking_into_public_artifact(self) -> None:
        from app.music_evidence import capture_music_evidence
        store.migrate(self.connection)
        plan = store.create_plan(self.connection, self.plan())
        artifact = self.artifact(plan.id).model_copy(update={'music_evidence': capture_music_evidence('yue2')})
        store.add_artifact(self.connection, artifact)
        loaded = store.get_artifact(self.connection, plan.id, artifact.public.id)
        self.assertIsNotNone(loaded.music_evidence)
        self.assertEqual(loaded.music_evidence.restriction if loaded.music_evidence else None, 'noncommercial')
        self.assertNotIn('music_evidence', store.get_plan(self.connection, plan.id).artifacts[0].model_dump())
