"""Reviewed voice deletion never promises erasure of retained or unknown copies."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app import audio_version_store, db, voice_build, voice_retention as retention
from app.voice_retention_contracts import ConfirmVoiceDeletionRequest


class VoiceRetentionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temporary)
        self.voices = self.root / 'voices'
        self.voice_id = 'a' * 32
        self.voice = self.voices / self.voice_id
        (self.voice / 'recordings').mkdir(parents=True)
        (self.voice / 'recordings' / 'take.wav').write_bytes(b'original recording')
        (self.voice / 'voice.json').write_text('{"id":"' + self.voice_id + '","name":"Fixture"}')
        database = self.root / 'catalog.db'
        self.connection = sqlite3.connect(database, check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)
        self.connection.execute('CREATE TABLE tracks(id INTEGER PRIMARY KEY,params_json TEXT NOT NULL,audio_path TEXT,abc_path TEXT)')
        audio_version_store.migrate(self.connection)
        retention.migrate(self.connection)
        self.enterContext(patch.object(db, 'get_db', return_value=self.connection))
        self.enterContext(patch.object(db, 'DB_PATH', database))
        self.enterContext(patch.object(db, 'DATA_DIR', self.root))
        self.enterContext(patch.object(retention, 'DATA_DIR', self.root))
        self.enterContext(patch.object(db, 'FILES_DIR', self.root / 'files'))
        self.enterContext(patch.object(voice_build, 'VOICES_DIR', self.voices))
        self.drain = self.enterContext(patch.object(voice_build, 'release_voice', new=AsyncMock()))
        self.enterContext(patch('app.voice_comparisons.release_voice', new=AsyncMock()))

    def confirmation(self, plan: retention.VoiceDeletionPlan) -> ConfirmVoiceDeletionRequest:
        return ConfirmVoiceDeletionRequest(plan_sha256=plan.plan_sha256, confirm_voice_id=self.voice_id, delete_voice_assets=True)

    async def test_plan_freezes_voice_and_exposes_no_absolute_paths(self) -> None:
        plan = retention.plan_deletion(self.voice_id)
        self.assertEqual(plan.status, 'pending')
        self.assertTrue(any(item.kind == 'recording' for item in plan.items))
        self.assertNotIn(str(self.root), plan.model_dump_json())
        with self.assertRaises(HTTPException) as raised:
            retention.require_not_frozen(self.voice_id)
        self.assertEqual(raised.exception.detail, 'voice_retention_frozen')
        self.assertTrue((self.voice / 'recordings' / 'take.wav').is_file())

    async def test_receipt_write_rolls_back_if_plan_update_fails(self) -> None:
        plan = retention.plan_deletion(self.voice_id)
        original = retention.get_plan(plan.id)
        self.connection.execute("CREATE TRIGGER reject_plan_update BEFORE UPDATE ON voice_deletion_plans BEGIN SELECT RAISE(ABORT,'injected update failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            retention._record(plan.model_copy(update={'status': 'executing'}), plan.items)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM voice_deletion_receipts').fetchone()[0], 0)
        self.assertEqual(retention.get_plan(plan.id), original)

    async def test_confirmation_is_bound_to_reviewed_plan(self) -> None:
        plan = retention.plan_deletion(self.voice_id)
        invalid = ConfirmVoiceDeletionRequest(plan_sha256='0' * 64, confirm_voice_id=self.voice_id, delete_voice_assets=True)
        with self.assertRaises(HTTPException):
            await retention.execute_deletion(plan.id, invalid)
        self.drain.assert_not_awaited()
        self.assertTrue(self.voice.is_dir())

    async def test_deletes_owned_assets_and_retains_tracks_and_shared_weights(self) -> None:
        self.connection.execute('INSERT INTO tracks(id,params_json) VALUES(1,?)', ('{"voice_id":"' + self.voice_id + '"}',))
        shared = self.root / 'models' / 'seed-vc' / 'shared.pth'
        shared.parent.mkdir(parents=True)
        shared.write_bytes(b'shared model')
        plan = retention.plan_deletion(self.voice_id)
        receipt = await retention.execute_deletion(plan.id, self.confirmation(plan))
        self.assertEqual(receipt.status, 'deleted')
        self.assertFalse(self.voice.exists())
        self.assertTrue(shared.is_file())
        self.assertEqual(self.connection.execute('SELECT count(*) FROM tracks').fetchone()[0], 1)
        self.assertTrue(any(item.kind == 'derived_track' and item.status == 'retained' for item in receipt.items))
        self.assertTrue(any(item.kind == 'backup' and item.status == 'external' for item in receipt.items))
        self.assertTrue(retention.verify_receipt(receipt))
        self.assertEqual(retention.get_receipt(plan.id).receipt_sha256, receipt.receipt_sha256)

    async def test_apply_copies_outside_voice_folder_remain_pending_in_receipt(self) -> None:
        self.connection.execute('INSERT INTO tracks(id,params_json) VALUES(1,?)', ('{"voice_id":"' + self.voice_id + '"}',))
        work = self.voices / '_apply' / 'work_1'
        work.mkdir(parents=True)
        copied = work / 'source.wav'
        copied.write_bytes(b'fixture copy requiring separate retention review')
        plan = retention.plan_deletion(self.voice_id)
        self.assertTrue(any(item.relative_path == '_apply/work_1/source.wav' and item.status == 'pending' for item in plan.items))
        receipt = await retention.execute_deletion(plan.id, self.confirmation(plan))
        self.assertTrue(copied.is_file())
        self.assertTrue(receipt.voice_assets_deleted)
        self.assertFalse(receipt.full_erasure_verified)
        self.assertTrue(any(item.kind == 'working_copy' and item.status == 'pending' for item in receipt.items))

    async def test_changed_inventory_requires_another_review(self) -> None:
        plan = retention.plan_deletion(self.voice_id)
        (self.voice / 'recordings' / 'take.wav').write_bytes(b'changed recording')
        with self.assertRaises(HTTPException) as raised:
            await retention.execute_deletion(plan.id, self.confirmation(plan))
        self.assertEqual(raised.exception.detail, 'voice_retention_plan_changed')
        self.assertTrue(self.voice.is_dir())
        reviewed = retention.plan_deletion(self.voice_id)
        self.assertNotEqual(reviewed.plan_sha256, plan.plan_sha256)
        completed = await retention.execute_deletion(reviewed.id, self.confirmation(reviewed))
        self.assertEqual(completed.status, 'deleted')

    async def test_symlink_escape_is_retained_and_never_followed(self) -> None:
        outside = self.root / 'outside.wav'
        outside.write_bytes(b'outside original')
        (self.voice / 'recordings' / 'linked.wav').symlink_to(outside)
        plan = retention.plan_deletion(self.voice_id)
        with self.assertLogs('app.voice_retention', level='ERROR'):
            receipt = await retention.execute_deletion(plan.id, self.confirmation(plan))
        self.assertEqual(receipt.status, 'executing')
        self.assertTrue(outside.is_file())
        self.assertTrue(any(item.error_code == 'voice_retention_unsafe_path' for item in receipt.items))

    async def test_redirected_voice_root_is_rejected_before_inventory(self) -> None:
        outside = self.root / 'outside-voices'
        self.voices.rename(outside)
        self.voices.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(HTTPException) as raised:
            retention.plan_deletion(self.voice_id)
        self.assertEqual(raised.exception.detail, 'voice_retention_unsafe_path')
        self.assertTrue((outside / self.voice_id / 'recordings' / 'take.wav').is_file())

    async def test_partial_failure_has_durable_receipt_and_retry_reconciles_absence(self) -> None:
        plan = retention.plan_deletion(self.voice_id)
        original = Path.unlink
        def unlink(path: Path, missing_ok: bool = False) -> None:
            if path.name == 'take.wav':
                raise PermissionError('synthetic permission failure')
            original(path, missing_ok=missing_ok)
        with patch.object(Path, 'unlink', unlink), self.assertLogs('app.voice_retention', level='ERROR'):
            receipt = await retention.execute_deletion(plan.id, self.confirmation(plan))
        self.assertEqual(receipt.status, 'executing')
        self.assertTrue(any(item.error_code == 'voice_retention_delete_failed' for item in receipt.items))
        self.assertTrue((self.voice / 'voice.json').is_file(), 'Pending deletion vanished from the voice library')
        self.assertTrue(retention.verify_receipt(retention.get_receipt(plan.id)))
        (self.voice / 'recordings' / 'take.wav').unlink()
        retention.reconcile_deletions()
        completed = await retention.execute_deletion(plan.id, self.confirmation(plan))
        self.assertEqual(completed.status, 'deleted')
        self.assertTrue(completed.previous_receipt_sha256)
        self.assertTrue(retention.verify_receipt(completed))

    async def test_cancellation_keeps_intent_and_freeze_for_explicit_retry(self) -> None:
        plan = retention.plan_deletion(self.voice_id)
        started = asyncio.Event()
        async def interrupted(_voice_id: str) -> None:
            started.set()
            await asyncio.Future[None]()
        with patch.object(voice_build, 'release_voice', new=interrupted):
            task = asyncio.create_task(retention.execute_deletion(plan.id, self.confirmation(plan)))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(retention.get_plan(plan.id).status, 'executing')
        with self.assertRaises(HTTPException):
            retention.require_not_frozen(self.voice_id)
        self.assertTrue(self.voice.is_dir())
        receipt = await retention.execute_deletion(plan.id, self.confirmation(plan))
        self.assertEqual(receipt.status, 'deleted')

    async def test_receipt_hash_detects_modified_claim(self) -> None:
        plan = retention.plan_deletion(self.voice_id)
        receipt = await retention.execute_deletion(plan.id, self.confirmation(plan))
        receipt.items[0].status = 'retained'
        self.assertFalse(retention.verify_receipt(receipt))

    async def test_catalog_reference_keeps_a_shared_voice_artifact(self) -> None:
        recording = self.voice / 'recordings' / 'take.wav'
        self.connection.execute('INSERT INTO tracks(id,params_json,audio_path) VALUES(1,?,?)', ('{}', str(recording)))
        plan = retention.plan_deletion(self.voice_id)
        with self.assertLogs('app.voice_retention', level='ERROR'):
            receipt = await retention.execute_deletion(plan.id, self.confirmation(plan))
        self.assertTrue(recording.is_file())
        self.assertEqual(receipt.status, 'executing')
        self.assertTrue(any(item.relative_path and item.relative_path.endswith('take.wav') and item.status == 'retained' for item in receipt.items))

    async def test_drain_failure_does_not_remove_files(self) -> None:
        plan = retention.plan_deletion(self.voice_id)
        with patch.object(voice_build, 'release_voice', new=AsyncMock(side_effect=RuntimeError('synthetic drain failure'))), self.assertLogs('app.voice_retention', level='ERROR'):
            receipt = await retention.execute_deletion(plan.id, self.confirmation(plan))
        self.assertTrue(self.voice.is_dir())
        self.assertEqual(receipt.status, 'executing')
        self.assertTrue(any(item.error_code == 'voice_retention_cleanup_failed' for item in receipt.items))

    async def test_plan_tampering_is_rejected_and_migration_preserves_rows(self) -> None:
        plan = retention.plan_deletion(self.voice_id)
        retention.migrate(self.connection)
        self.assertEqual(retention.get_plan(plan.id).plan_sha256, plan.plan_sha256)
        plan.items[0].action = 'retain'
        self.connection.execute('UPDATE voice_deletion_plans SET plan_json=? WHERE id=?', (plan.model_dump_json(), plan.id))
        self.connection.commit()
        with self.assertRaises(HTTPException) as raised:
            await retention.execute_deletion(plan.id, self.confirmation(plan))
        self.assertEqual(raised.exception.detail, 'voice_retention_plan_invalid')

    async def test_receipt_verifies_all_ancestors_and_sanitizes_malformed_history(self) -> None:
        plan = retention.plan_deletion(self.voice_id)
        for _ in range(3):
            retention._record(plan, plan.items)
        original = self.connection.execute('SELECT receipt_json FROM voice_deletion_receipts WHERE sequence=1').fetchone()[0]
        altered = retention.VoiceDeletionReceipt.model_validate_json(original).model_copy(update={'created_at': 'changed oldest receipt'})
        for sequence, payload in ((1, altered.model_dump_json()), (2, '{malformed')):
            with self.subTest(sequence=sequence):
                saved = self.connection.execute('SELECT receipt_json FROM voice_deletion_receipts WHERE sequence=?', (sequence,)).fetchone()[0]
                self.connection.execute('UPDATE voice_deletion_receipts SET receipt_json=? WHERE sequence=?', (payload, sequence))
                self.connection.commit()
                with self.assertRaises(HTTPException) as raised:
                    retention.get_receipt(plan.id)
                self.assertEqual(raised.exception.detail, 'voice_retention_receipt_invalid')
                self.connection.execute('UPDATE voice_deletion_receipts SET receipt_json=? WHERE sequence=?', (saved, sequence))
                self.connection.commit()
        self.assertEqual(retention.get_receipt(plan.id).sequence, 3)
        self.assertTrue((self.voice / 'recordings' / 'take.wav').is_file())

    async def test_receipt_binds_voice_sequence_and_initial_chain_link(self) -> None:
        plan = retention.plan_deletion(self.voice_id)
        original = retention._record(plan, plan.items)
        for field, value in (('voice_id', 'b' * 32), ('sequence', 2), ('previous_receipt_sha256', 'f' * 64)):
            with self.subTest(field=field):
                changed = original.model_copy(update={field: value})
                changed.receipt_sha256 = retention._digest(retention._JSON.validate_python(changed.model_dump(mode='json', exclude={'receipt_sha256'})))
                self.assertTrue(retention.verify_receipt(changed))
                self.connection.execute('UPDATE voice_deletion_receipts SET receipt_json=? WHERE sequence=1', (changed.model_dump_json(),))
                self.connection.commit()
                with self.assertRaises(HTTPException) as raised:
                    retention.get_receipt(plan.id)
                self.assertEqual(raised.exception.detail, 'voice_retention_receipt_invalid')
        self.connection.execute('UPDATE voice_deletion_receipts SET receipt_json=? WHERE sequence=1', (original.model_dump_json(),))
        self.connection.commit()
        self.assertEqual(retention.get_receipt(plan.id).receipt_sha256, original.receipt_sha256)

    async def test_receipt_history_read_is_bounded(self) -> None:
        plan = retention.plan_deletion(self.voice_id)
        original = retention._record(plan, [])
        previous = original.receipt_sha256
        rows: list[tuple[str, int, str]] = []
        for sequence in range(2, 4098):
            receipt = original.model_copy(update={'sequence': sequence, 'previous_receipt_sha256': previous})
            receipt.receipt_sha256 = retention._digest(retention._JSON.validate_python(receipt.model_dump(mode='json', exclude={'receipt_sha256'})))
            rows.append((plan.id, sequence, receipt.model_dump_json()))
            previous = receipt.receipt_sha256
        self.connection.executemany('INSERT INTO voice_deletion_receipts VALUES(?,?,?)', rows)
        self.connection.commit()
        with self.assertRaises(HTTPException) as raised:
            retention.get_receipt(plan.id)
        self.assertEqual(raised.exception.detail, 'voice_retention_receipt_invalid')

    async def test_confirmation_reserves_terminal_receipt_capacity_before_mutation(self) -> None:
        plan = retention.plan_deletion(self.voice_id)
        initial = retention._record(plan, plan.items)
        stored_plan = self.connection.execute('SELECT plan_json FROM voice_deletion_plans WHERE id=?', (plan.id,)).fetchone()[0]
        for sequence in (4095, 4096):
            with self.subTest(sequence=sequence):
                boundary = initial.model_copy(update={'sequence': sequence, 'previous_receipt_sha256': 'b' * 64})
                boundary.receipt_sha256 = retention._digest(retention._JSON.validate_python(boundary.model_dump(mode='json', exclude={'receipt_sha256'})))
                self.assertTrue(retention.verify_receipt(boundary))
                failure: Exception | None = None
                with patch.object(retention, 'get_receipt', return_value=boundary):
                    try:
                        await retention.execute_deletion(plan.id, self.confirmation(plan))
                    except Exception as error:
                        failure = error
                self.assertEqual(self.connection.execute('SELECT count(*) FROM voice_deletion_receipts').fetchone()[0], 1)
                self.assertEqual(self.connection.execute('SELECT plan_json FROM voice_deletion_plans WHERE id=?', (plan.id,)).fetchone()[0], stored_plan)
                self.assertTrue((self.voice / 'recordings' / 'take.wav').is_file())
                self.assertTrue((self.voice / 'voice.json').is_file())
                self.drain.assert_not_awaited()
                if not isinstance(failure, HTTPException):
                    self.fail('Confirmation must reject exhausted receipt capacity with a stable HTTP error')
                self.assertEqual(failure.detail, 'voice_retention_receipt_capacity_exceeded')
                if sequence == 4096:
                    with patch.object(retention, 'get_receipt', return_value=boundary), self.assertRaises(HTTPException) as raised:
                        retention._record(plan, plan.items)
                    self.assertEqual(raised.exception.detail, 'voice_retention_receipt_capacity_exceeded')
                    self.assertEqual(self.connection.execute('SELECT count(*) FROM voice_deletion_receipts').fetchone()[0], 1)


class VoiceRetentionTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        for name, value in (('_db', None), ('DATA_DIR', self.root), ('DB_PATH', self.root / 'catalog.db'), ('FILES_DIR', self.root / 'files')):
            self.enterContext(patch.object(db, name, value))
        self.enterContext(patch.object(retention, 'DATA_DIR', self.root))
        self.enterContext(patch.object(voice_build, 'VOICES_DIR', self.root / 'voices'))
        self.voice_id = 'a' * 32
        voice = self.root / 'voices' / self.voice_id
        voice.mkdir(parents=True)
        (voice / 'voice.json').write_text('{"id":"' + self.voice_id + '","name":"Fixture"}')
        self.connection = db.get_db()
        self.addCleanup(self.connection.close)
        self.plan = retention.plan_deletion(self.voice_id)

    def _assert_failed_track_is_rolled_back(self, write: Callable[[], object]) -> None:
        from app import generation_library
        started = threading.Event()
        futures: list[Future[object]] = []
        def retain() -> object:
            started.set()
            return write()
        with ThreadPoolExecutor(max_workers=1) as executor:
            def fail_snapshot(_connection: sqlite3.Connection, _row: sqlite3.Row) -> None:
                futures.append(executor.submit(retain))
                self.assertTrue(started.wait(5))
                with self.assertRaises(FutureTimeout):
                    futures[0].result(timeout=.25)
                raise ValueError('injected track publication failure')
            with patch.object(generation_library, 'capture_track', side_effect=fail_snapshot):
                with self.assertRaisesRegex(ValueError, 'injected track publication failure'):
                    db.insert_track(model='ace_step', title='Fixture', lyrics='', seed=1, duration_ms=1000,
                        wall_ms=None, params={}, audio_path=self.root / 'files' / 'fixture.wav', abc_path=None)
            self.assertEqual(len(futures), 1)
            futures[0].result(timeout=5)
        self.assertEqual(db.list_tracks(), [])
        self.assertEqual(retention.get_plan(self.plan.id).id, self.plan.id)

    def test_plan_save_cannot_commit_a_concurrent_failed_track(self) -> None:
        self._assert_failed_track_is_rolled_back(lambda: retention.plan_deletion(self.voice_id))
        self.assertEqual(self.connection.execute('SELECT count(*) FROM voice_deletion_plans').fetchone()[0], 1)

    def test_receipt_save_cannot_commit_a_concurrent_failed_track(self) -> None:
        self._assert_failed_track_is_rolled_back(lambda: retention._record(self.plan, self.plan.items))
        self.assertEqual(retention.get_receipt(self.plan.id).sequence, 1)
