from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch
from collections.abc import AsyncIterator

from app import db, music_analysis, music_artifacts, music_analysis_test as analysis_test_support, music_beats_runtime
from app.music_analysis_contracts import AnalysisEngine, AnalysisResult, MusicAnalysisJob, MusicSourceRef, VersionMusicSource
from app.music_beats import BeatAnalysisRecipe, BeatRuntimeIdentity, LICENSE_EVIDENCE_IDS
from app.music_artifact_contracts import ConfirmMusicArtifactCleanup, MusicArtifactCleanupRequest


@dataclass(frozen=True)
class Projection:
    job_id: str
    job_revision: int
    attempt: int
    source: MusicSourceRef
    result_id: str
    audio_sha256: str
    snapshot_name: str
    workspace: Path
    source_path: Path


class MusicArtifactsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.object(db, 'DATA_DIR', self.root))
        self.enterContext(patch.object(db, 'FILES_DIR', self.root / 'files'))
        self.enterContext(patch.object(db, 'DB_PATH', self.root / 'catalog.db'))
        self.enterContext(patch.object(db, 'get_db', return_value=None))
        self.enterContext(patch.object(music_artifacts, '_closing', False))
        self.enterContext(patch.object(music_artifacts, '_lock', asyncio.Lock()))
        self.source = self.root / 'files' / 'original.wav'
        self.source.parent.mkdir()
        self.source.write_bytes(b'original audio fixture')
        self.workspace = self.root / 'music-analysis' / ('a' * 32)
        self.workspace.mkdir(parents=True)
        (self.workspace / 'source.wav').write_bytes(self.source.read_bytes())
        (self.workspace / 'result.json').write_text('{}')
        self.projection = Projection('a' * 32, 4, 1, VersionMusicSource(track_id=1, version_id='b' * 32),
            'c' * 32, hashlib.sha256(self.source.read_bytes()).hexdigest(), 'source.wav', self.workspace, self.source)
        self.enterContext(patch.object(music_analysis, 'completed_workspace_cleanup_projection',
            new=AsyncMock(return_value=self.projection), create=True))

        @asynccontextmanager
        async def protected(identifier: str, revision: int) -> AsyncIterator[Projection]:
            self.assertEqual((identifier, revision), (self.projection.job_id, 4))
            yield self.projection

        self.enterContext(patch.object(music_analysis, 'protect_completed_workspace_cleanup', side_effect=protected, create=True))
        self.request = MusicArtifactCleanupRequest(analysis_job_ids=[self.projection.job_id])

    async def asyncTearDown(self) -> None:
        await music_artifacts.shutdown()

    async def test_review_does_not_delete_and_confirmation_removes_only_selected_redundant_copy(self) -> None:
        unrelated = self.workspace.parent / ('d' * 32)
        unrelated.mkdir()
        (unrelated / 'keep.wav').write_bytes(b'keep')
        plan = await music_artifacts.create_plan(self.request)
        self.assertEqual(plan.items[0].disposition, 'eligible')
        self.assertTrue(self.workspace.exists())
        receipt = await music_artifacts.confirm_plan(plan.id, ConfirmMusicArtifactCleanup(plan_sha256=plan.plan_sha256, confirm_cleanup=True))
        self.assertEqual(receipt.status, 'completed')
        self.assertFalse(self.workspace.exists())
        self.assertEqual(self.source.read_bytes(), b'original audio fixture')
        self.assertEqual((unrelated / 'keep.wav').read_bytes(), b'keep')
        self.assertEqual(music_artifacts.get_receipt(plan.id), receipt)
        self.assertEqual(music_artifacts.get_plan(plan.id).status, 'completed')

    async def test_changed_original_or_snapshot_never_reaches_removal(self) -> None:
        for path in (self.source, self.workspace / 'source.wav'):
            original = path.read_bytes()
            plan = await music_artifacts.create_plan(self.request)
            path.write_bytes(b'changed')
            with self.subTest(path=path.name):
                receipt = await music_artifacts.confirm_plan(plan.id, ConfirmMusicArtifactCleanup(plan_sha256=plan.plan_sha256, confirm_cleanup=True))
                self.assertEqual(receipt.status, 'blocked')
                self.assertTrue(self.workspace.exists())
                self.assertTrue((self.workspace / 'result.json').exists())
            path.write_bytes(original)

    def _optional_checkpoint(self) -> tuple[music_analysis.AnalysisWorkerRequest, AnalysisResult, Path]:
        # Synthetic bytes exercise the real bounded hash path; no model is acquired.
        checkpoint = self.workspace / 'beat-checkpoint.ckpt'
        with checkpoint.open('wb') as stream:
            remaining = music_beats_runtime.WEIGHTS_BYTES
            while remaining:
                packet = b'\x19' * min(65536, remaining)
                stream.write(packet)
                remaining -= len(packet)
        checksum = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        self.enterContext(patch.object(music_beats_runtime, 'WEIGHTS_SHA256', checksum))
        identity = BeatRuntimeIdentity(source_sha256=music_beats_runtime.SOURCE_SHA256,
            weights_sha256=checksum, python_sha256='f' * 64, python_version='3.12.13', packages=music_beats_runtime.PACKAGES)
        result = analysis_test_support.result(self.projection.source, self.projection.result_id)
        result = AnalysisResult.model_validate({**result.model_dump(), 'audio_sha256': self.projection.audio_sha256,
            'loudness': {**result.loudness.model_dump(), 'source_sha256': self.projection.audio_sha256},
            'weights_sha256': checksum, 'license_evidence_ids': list(LICENSE_EVIDENCE_IDS),
            'engines': [AnalysisEngine(task='beats', name='beat-this-small0', revision=identity.revision,
                configuration_sha256=identity.configuration_sha256, weights_sha256=checksum, precision='float32').model_dump()]})
        request = music_analysis.AnalysisWorkerRequest(job_id=self.projection.job_id, result_id=result.id,
            source=self.projection.source, snapshot_name=self.projection.snapshot_name, audio_sha256=self.projection.audio_sha256,
            configuration_sha256=result.configuration_sha256, ffmpeg_version='synthetic-fixture', ffmpeg_sha256='e' * 64,
            created_at=result.created_at, beats=BeatAnalysisRecipe(runtime_root=str(self.root / 'installed-runtime'), identity=identity))
        (self.workspace / 'request.json').write_text(request.model_dump_json())
        job = MusicAnalysisJob(id=self.projection.job_id, source=self.projection.source, status='done', phase='complete', result=result, created_at=result.created_at)
        self.enterContext(patch.object(music_analysis, 'get', return_value=job))
        return request, result, checkpoint

    async def test_completed_optional_analysis_checkpoint_is_reclaimable_without_touching_installed_models(self) -> None:
        _, _, checkpoint = self._optional_checkpoint()
        installed = self.root / 'installed-runtime/weights/small0.ckpt'; installed.parent.mkdir(parents=True)
        installed.write_bytes(b'installed model is outside cleanup')
        plan = await music_artifacts.create_plan(self.request)
        self.assertEqual(plan.items[0].disposition, 'eligible')
        self.assertGreaterEqual(plan.items[0].bytes, checkpoint.stat().st_size)
        receipt = await music_artifacts.confirm_plan(plan.id, ConfirmMusicArtifactCleanup(plan_sha256=plan.plan_sha256, confirm_cleanup=True))
        self.assertEqual(receipt.status, 'completed')
        self.assertEqual(installed.read_bytes(), b'installed model is outside cleanup')
        self.assertEqual(self.source.read_bytes(), b'original audio fixture')

    async def test_optional_checkpoint_metadata_hash_and_result_identity_must_all_match(self) -> None:
        request, result, checkpoint = self._optional_checkpoint()
        original = checkpoint.read_bytes()
        cases = [request.model_copy(update={'job_id': 'd' * 32}), request.model_copy(update={'audio_sha256': 'd' * 64}),
            request.model_copy(update={'result_id': 'd' * 32}), request.model_copy(update={'configuration_sha256': 'e' * 64}),
            request.model_copy(update={'beats': None})]
        for changed in cases:
            with self.subTest(request=changed.model_dump_json()):
                (self.workspace / 'request.json').write_text(changed.model_dump_json())
                self.assertEqual((await music_artifacts.create_plan(self.request)).items[0].disposition, 'blocked')
        (self.workspace / 'request.json').write_text(request.model_dump_json())
        checkpoint.write_bytes(b'\x20' + original[1:])
        self.assertEqual((await music_artifacts.create_plan(self.request)).items[0].disposition, 'blocked')
        checkpoint.write_bytes(original[:-1])
        self.assertEqual((await music_artifacts.create_plan(self.request)).items[0].disposition, 'blocked')
        checkpoint.write_bytes(original)
        job = MusicAnalysisJob(id=self.projection.job_id, source=self.projection.source, status='done', phase='complete',
            result=result.model_copy(update={'weights_sha256': 'a' * 64}), created_at=result.created_at)
        with patch.object(music_analysis, 'get', return_value=job):
            self.assertEqual((await music_artifacts.create_plan(self.request)).items[0].disposition, 'blocked')
        changed_engine = result.engines[0].model_copy(update={'configuration_sha256': 'a' * 64})
        job = job.model_copy(update={'result': result.model_copy(update={'engines': [changed_engine]})})
        with patch.object(music_analysis, 'get', return_value=job):
            self.assertEqual((await music_artifacts.create_plan(self.request)).items[0].disposition, 'blocked')

    async def test_window_leftovers_and_changed_checkpoint_preserve_the_entire_workspace(self) -> None:
        _, _, checkpoint = self._optional_checkpoint()
        for name in ('beats-input.f32', 'beats-request.json', 'beats-result.json'):
            extra = self.workspace / name; extra.write_bytes(b'leftover')
            self.assertEqual((await music_artifacts.create_plan(self.request)).items[0].disposition, 'blocked')
            extra.unlink()
        plan = await music_artifacts.create_plan(self.request)
        self.assertEqual(plan.items[0].disposition, 'eligible')
        with checkpoint.open('r+b') as stream:
            stream.write(b'\x20')
        receipt = await music_artifacts.confirm_plan(plan.id, ConfirmMusicArtifactCleanup(plan_sha256=plan.plan_sha256, confirm_cleanup=True))
        self.assertEqual(receipt.status, 'blocked')
        self.assertTrue((self.workspace / 'source.wav').exists())

    async def test_optional_checkpoint_cleanup_can_resume_a_verified_detach(self) -> None:
        self._optional_checkpoint()
        plan = await music_artifacts.create_plan(self.request)
        self.assertEqual(plan.items[0].disposition, 'eligible')
        confirmation = ConfirmMusicArtifactCleanup(plan_sha256=plan.plan_sha256, confirm_cleanup=True)
        def interrupted(directory: Path, proof: music_artifacts._Proof) -> None:
            (directory / 'beat-checkpoint.ckpt').unlink()
            raise OSError('synthetic interruption after removing the verified checkpoint')

        with patch.object(music_artifacts, '_remove_files', side_effect=interrupted):
            self.assertEqual((await music_artifacts.confirm_plan(plan.id, confirmation)).status, 'partial')
        self.assertEqual((await music_artifacts.confirm_plan(plan.id, confirmation)).status, 'completed')

    async def test_symlinks_unrecognized_files_and_unverified_owners_are_blocked(self) -> None:
        extra = self.workspace / 'user-recording.wav'
        extra.write_bytes(b'unique')
        plan = await music_artifacts.create_plan(self.request)
        self.assertEqual(plan.items[0].disposition, 'blocked')
        extra.unlink()
        extra.symlink_to(self.source)
        plan = await music_artifacts.create_plan(self.request)
        self.assertEqual(plan.items[0].disposition, 'blocked')
        extra.unlink()
        with patch.object(music_analysis, 'completed_workspace_cleanup_projection', new=AsyncMock(side_effect=music_analysis.MusicAnalysisError('worker_unverified'))):
            plan = await music_artifacts.create_plan(self.request)
        self.assertIn('worker_unverified', plan.items[0].reasons)
        self.assertTrue(self.workspace.exists())

    async def test_failed_delete_retains_explicit_tombstone_and_resumes_only_on_confirmation(self) -> None:
        plan = await music_artifacts.create_plan(self.request)
        confirm = ConfirmMusicArtifactCleanup(plan_sha256=plan.plan_sha256, confirm_cleanup=True)
        with patch.object(music_artifacts, '_remove_files', side_effect=OSError('private failure')):
            receipt = await music_artifacts.confirm_plan(plan.id, confirm)
        self.assertEqual(receipt.status, 'partial')
        self.assertFalse(self.workspace.exists())
        self.assertTrue((self.root / 'music-artifacts' / plan.id / 'staged' / self.projection.job_id).is_dir())
        receipt = await music_artifacts.confirm_plan(plan.id, confirm)
        self.assertEqual(receipt.status, 'completed')
        self.assertEqual(self.source.read_bytes(), b'original audio fixture')

    async def test_route_cancellation_does_not_abandon_confirmed_cleanup(self) -> None:
        plan = await music_artifacts.create_plan(self.request)
        confirm = ConfirmMusicArtifactCleanup(plan_sha256=plan.plan_sha256, confirm_cleanup=True)
        entered, release = asyncio.Event(), asyncio.Event()
        original = music_artifacts._verify

        async def verify(proof: music_artifacts._Proof, directory: Path, *, resumed: bool = False) -> music_analysis.CompletedAnalysisWorkspace:
            entered.set()
            await release.wait()
            return await original(proof, directory, resumed=resumed)

        with patch.object(music_artifacts, '_verify', side_effect=verify):
            request = asyncio.create_task(music_artifacts.confirm_plan(plan.id, confirm))
            await entered.wait()
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
            self.assertIn(plan.id, music_artifacts._executions)
            task = music_artifacts._executions[plan.id]
            release.set()
            receipt = await task
        self.assertEqual(receipt.status, 'completed')

    async def test_inventory_is_read_only_bounded_and_retains_training_models_and_originals(self) -> None:
        (self.root / 'models').mkdir()
        (self.root / 'models' / 'keep.pth').write_bytes(b'model')
        (self.root / 'music-lora-training').mkdir()
        (self.root / 'music-lora-training' / 'parent.ckpt').write_bytes(b'parent checkpoint')
        with sqlite3.connect(db.DB_PATH) as connection:
            connection.execute('CREATE TABLE music_training_jobs(payload_json TEXT)')
            connection.execute('INSERT INTO music_training_jobs VALUES(?)',
                (json.dumps({'request': {'yue_ar_source_job_id': 'e' * 32}}),))
        from app.module_catalog import ModuleEnvironment
        environment = ModuleEnvironment.for_root(self.root / 'runtime', data_dir=self.root)
        with patch('app.module_catalog.configured_environment', return_value=environment):
            inventory = await music_artifacts.inventory()
            training = next(item for item in inventory.groups if item.id == 'music-training')
            self.assertEqual(training.disposition, 'retained')
            self.assertIn('protected_checkpoint', training.reasons)
            self.assertIn('ar_training_parent', training.reasons)
            self.assertEqual(training.dependencies[0].id, 'e' * 32)
            self.assertFalse((self.root / 'music-artifacts').exists())
            with patch.object(music_artifacts, 'MAX_ENTRIES', 1):
                bounded = await music_artifacts.inventory()
            self.assertFalse(bounded.scan_complete)
        self.assertEqual(self.source.read_bytes(), b'original audio fixture')

    async def test_shutdown_cancels_hashing_with_a_durable_partial_receipt(self) -> None:
        plan = await music_artifacts.create_plan(self.request)
        entered = asyncio.Event()

        async def verify(proof: music_artifacts._Proof, directory: Path, *, resumed: bool = False) -> None:
            entered.set()
            await asyncio.Event().wait()

        with patch.object(music_artifacts, '_verify', side_effect=verify):
            request = asyncio.create_task(music_artifacts.confirm_plan(plan.id,
                ConfirmMusicArtifactCleanup(plan_sha256=plan.plan_sha256, confirm_cleanup=True)))
            await entered.wait()
            await music_artifacts.shutdown()
            receipt = await request
        self.assertEqual(receipt.status, 'blocked')
        self.assertEqual(music_artifacts.get_receipt(plan.id), receipt)
        self.assertTrue(self.workspace.exists())
        self.assertFalse(music_artifacts.work_busy())

    async def test_owner_lock_rechecks_signatures_after_expensive_hashes(self) -> None:
        plan = await music_artifacts.create_plan(self.request)

        @asynccontextmanager
        async def changed(identifier: str, revision: int) -> AsyncIterator[Projection]:
            (self.workspace / 'source.wav').write_bytes(b'changed after hashing')
            yield self.projection

        with patch.object(music_analysis, 'protect_completed_workspace_cleanup', side_effect=changed):
            receipt = await music_artifacts.confirm_plan(plan.id,
                ConfirmMusicArtifactCleanup(plan_sha256=plan.plan_sha256, confirm_cleanup=True))
        self.assertEqual(receipt.status, 'blocked')
        self.assertTrue(self.workspace.exists())

    async def test_shutdown_drains_a_started_removal_thread_and_keeps_resume_receipt(self) -> None:
        plan = await music_artifacts.create_plan(self.request)
        confirmation = ConfirmMusicArtifactCleanup(plan_sha256=plan.plan_sha256, confirm_cleanup=True)
        entered, release = threading.Event(), threading.Event()
        original = music_artifacts._remove_files

        def remove(directory: Path, proof: music_artifacts._Proof) -> None:
            entered.set()
            if not release.wait(5):
                raise TimeoutError('fixture removal was not released')
            original(directory, proof)

        with patch.object(music_artifacts, '_remove_files', side_effect=remove):
            request = asyncio.create_task(music_artifacts.confirm_plan(plan.id, confirmation))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                stopping = asyncio.create_task(music_artifacts.shutdown())
                await asyncio.sleep(0)
                self.assertFalse(stopping.done())
                release.set()
                await stopping
                receipt = await request
                self.assertEqual(receipt.status, 'partial')
                self.assertEqual(music_artifacts.get_receipt(plan.id), receipt)
            finally:
                release.set()
                await asyncio.gather(request, return_exceptions=True)
        await music_artifacts.recover()
        self.assertEqual((await music_artifacts.confirm_plan(plan.id, confirmation)).status, 'completed')
        self.assertEqual(self.source.read_bytes(), b'original audio fixture')

    async def test_wrong_review_hash_and_journal_disk_failure_cannot_remove_a_copy(self) -> None:
        plan = await music_artifacts.create_plan(self.request)
        with self.assertRaises(music_artifacts.MusicArtifactError):
            await music_artifacts.confirm_plan(plan.id, ConfirmMusicArtifactCleanup(plan_sha256='f' * 64, confirm_cleanup=True))
        with patch.object(music_artifacts, '_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                await music_artifacts.confirm_plan(plan.id, ConfirmMusicArtifactCleanup(plan_sha256=plan.plan_sha256, confirm_cleanup=True))
        self.assertTrue((self.workspace / 'source.wav').is_file())
        self.assertFalse(music_artifacts._executions)

    async def test_post_detach_journal_failure_keeps_the_whole_copy_and_durable_pending_receipt(self) -> None:
        plan = await music_artifacts.create_plan(self.request)
        confirmation = ConfirmMusicArtifactCleanup(plan_sha256=plan.plan_sha256, confirm_cleanup=True)
        writes = 0
        original = music_artifacts._write

        def disk_full(path: Path, document: music_artifacts.BaseModel) -> None:
            nonlocal writes
            writes += 1
            if writes > 1:
                raise OSError('disk full after detach')
            original(path, document)

        with patch.object(music_artifacts, '_write', side_effect=disk_full):
            with self.assertRaises(OSError):
                await music_artifacts.confirm_plan(plan.id, confirmation)
        stage = self.root / 'music-artifacts' / plan.id / 'staged' / self.projection.job_id
        self.assertEqual((stage / 'source.wav').read_bytes(), self.source.read_bytes())
        self.assertEqual(music_artifacts.get_receipt(plan.id).status, 'partial')
        from app.module_catalog import ModuleEnvironment
        with patch('app.module_catalog.configured_environment', return_value=ModuleEnvironment.for_root(self.root / 'runtime')):
            inventory = await music_artifacts.inventory()
        pending = next(item for item in inventory.groups if item.kind == 'cleanup_staging')
        self.assertIn(plan.id, [dependency.id for dependency in pending.dependencies if dependency.kind == 'cleanup_plan'])
        self.assertEqual((await music_artifacts.confirm_plan(plan.id, confirmation)).status, 'completed')

    async def test_existing_staging_destination_cannot_claim_the_original_workspace_was_removed(self) -> None:
        plan = await music_artifacts.create_plan(self.request)
        stage = self.root / 'music-artifacts' / plan.id / 'staged' / self.projection.job_id
        stage.mkdir(parents=True)
        receipt = await music_artifacts.confirm_plan(plan.id,
            ConfirmMusicArtifactCleanup(plan_sha256=plan.plan_sha256, confirm_cleanup=True))
        self.assertEqual(receipt.status, 'blocked')
        self.assertEqual(receipt.items[0].removed_bytes, 0)
        self.assertTrue((self.workspace / 'source.wav').is_file())
        self.assertTrue(stage.is_dir())


class RealAnalysisOwnerCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await analysis_test_support.MusicAnalysisTests.asyncSetUp(self)
        self.enterContext(patch.object(db, 'DATA_DIR', self.root.resolve()))
        self.enterContext(patch.object(music_artifacts, '_closing', False))
        self.enterContext(patch.object(music_artifacts, '_lock', asyncio.Lock()))
        media = db.FILES_DIR.resolve() / 'original.wav'
        media.parent.mkdir()
        media.write_bytes(b'owned original analysis source')
        self.media = media
        self.enterContext(patch('app.audio_versions.resolve_source', return_value=media))
        checksum = hashlib.sha256(media.read_bytes()).hexdigest()
        raw = analysis_test_support.result(self.source)
        raw = AnalysisResult.model_validate({**raw.model_dump(), 'audio_sha256': checksum,
            'loudness': {'duration_sec': raw.duration_sec, 'source_sha256': checksum}})
        self.job_id = 'e' * 32
        job = MusicAnalysisJob(id=self.job_id, source=self.source, created_at='2026-10-17T00:00:00Z')
        music_analysis._insert_job(job, self.request, 'f' * 64)
        music_analysis._change(job.id, status='running')
        music_analysis._publish(job.id, music_analysis._record(job.id).revision,
            music_analysis.cache_key(checksum, 'version', raw.configuration_sha256), raw)
        self.workspace = music_analysis._workspace(job.id)
        self.workspace.mkdir(parents=True)
        (self.workspace / 'source.wav').write_bytes(media.read_bytes())
        with sqlite3.connect(db.DB_PATH) as connection:
            connection.execute('UPDATE music_analysis_jobs SET snapshot_sha256=?,snapshot_name=? WHERE id=?', (checksum, 'source.wav', job.id))
        self.raw = raw

    async def asyncTearDown(self) -> None:
        await music_artifacts.shutdown()
        await analysis_test_support.MusicAnalysisTests.asyncTearDown(self)

    async def test_real_owner_cleanup_preserves_raw_result_and_manual_override_rows(self) -> None:
        from app.music_analysis_contracts import MusicAnalysisOverrides, KeyEstimate
        corrected = music_analysis.put_overrides(self.raw.id, MusicAnalysisOverrides(analysis_id=self.raw.id,
            expected_revision=0, key=KeyEstimate(status='known', tonic_pc=9, mode='minor', unknown_reason=None)))
        plan = await music_artifacts.create_plan(MusicArtifactCleanupRequest(analysis_job_ids=[self.job_id]))
        self.assertEqual(plan.items[0].disposition, 'eligible')
        receipt = await music_artifacts.confirm_plan(plan.id, ConfirmMusicArtifactCleanup(plan_sha256=plan.plan_sha256, confirm_cleanup=True))
        self.assertEqual(receipt.status, 'completed')
        self.assertFalse(self.workspace.exists())
        self.assertEqual(music_analysis.get(self.job_id).result, corrected)
        self.assertEqual(self.media.read_bytes(), b'owned original analysis source')
        with sqlite3.connect(db.DB_PATH) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM music_analysis_results').fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM music_analysis_overrides').fetchone()[0], 1)

    async def test_real_owner_allows_explicit_resume_after_workspace_was_detached(self) -> None:
        plan = await music_artifacts.create_plan(MusicArtifactCleanupRequest(analysis_job_ids=[self.job_id]))
        confirmation = ConfirmMusicArtifactCleanup(plan_sha256=plan.plan_sha256, confirm_cleanup=True)
        with patch.object(music_artifacts, '_remove_files', side_effect=OSError('interrupted')):
            self.assertEqual((await music_artifacts.confirm_plan(plan.id, confirmation)).status, 'partial')
        self.assertEqual((await music_artifacts.confirm_plan(plan.id, confirmation)).status, 'completed')
        self.assertEqual(music_analysis.get(self.job_id).result, self.raw)

    async def test_catalogued_source_copy_is_retained(self) -> None:
        with sqlite3.connect(db.DB_PATH) as connection:
            connection.execute('CREATE TABLE tracks(audio_path TEXT)')
            connection.execute('INSERT INTO tracks VALUES(?)', (str(self.workspace / 'source.wav'),))
        plan = await music_artifacts.create_plan(MusicArtifactCleanupRequest(analysis_job_ids=[self.job_id]))
        self.assertEqual(plan.items[0].reasons, ['referenced'])
        self.assertTrue(self.workspace.exists())


if __name__ == '__main__':
    unittest.main()
