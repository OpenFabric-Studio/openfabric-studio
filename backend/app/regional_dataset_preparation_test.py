"""Real CPU snapshots/crops and cancellation, isolated from native models and libraries."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from collections.abc import Callable
from unittest.mock import AsyncMock, patch

import numpy as np
import soundfile as sf

from app import db, lora_datasets, regional_datasets as service, regional_dataset_store as store
from app import regional_evaluation as evaluation
from app.atomic_files import write_object
from app.regional_dataset_contracts import (RegionalDatasetError, RegionalDatasetInspectionRequest,
    RegionalDatasetReview, RegionalDatasetReviewRequest, RegionalManifestSelection, RegionalPreparationRequest,
    RegionalPreparedSelection, RegionalTrainingSubmission, RegionalPreparedExcerpt)
from app.stable_lora_contracts import StableLoraTrainingRequest
from app.stable_lora_contracts import StableLoraTrainingJob
from app.music_lora_contracts import MusicAdapter
from app.local_music_contracts import LocalMusicJob, LocalMusicRequest
from app.regional_evaluation_contracts import RegionalEvaluationRequest, RegionalTrainingReceipt, RegionalListeningReview
from app.stable_lora_runtime import fingerprint
from app.regional_dataset_contracts_test import recording
from app.regional_acoustic_contracts import RegionalAcousticScanRequest, RegionalAcousticReview, RegionalAcousticDecision


class RegionalPreparationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.library = self.root / 'library'; self.library.mkdir()
        self.engine = self.root / 'engine'; self.dataset = self.engine / 'datasets/fixture'; self.dataset.mkdir(parents=True)
        self.connection = sqlite3.connect(':memory:'); self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close); store.migrate(self.connection)
        self.enterContext(patch.object(db, 'get_db', return_value=self.connection))
        self.enterContext(patch.object(db, 'FILES_DIR', self.library / 'files'))
        self.enterContext(patch.object(lora_datasets, 'ACE_STEP_DIR', self.engine))
        self.enterContext(patch.object(lora_datasets, 'DATA_DIR', self.library))
        self.enterContext(patch.object(service, 'available_bytes', return_value=100 * 1024**3))
        self.enterContext(patch.object(service, '_tasks', {}))
        self.enterContext(patch.object(service, '_acoustic_ids', set()))
        self.enterContext(patch.object(service, '_acoustic_unverified', set()))
        self.enterContext(patch.object(service, '_acoustic_processes', {}))
        self.enterContext(patch.object(service, '_closing', False))
        self.enterContext(patch.object(evaluation, '_closing', False))
        self.paths: dict[str, Path] = {}
        samples = []
        for index, identifier in enumerate(('a', 'b', 'c')):
            path = self.dataset / f'{identifier}.wav'
            signal = np.asarray(.2 * np.sin(np.arange(48000) * (index + 1) * .03), dtype=np.float32)
            sf.write(path, signal, 48000, subtype='PCM_16'); self.paths[identifier] = path
            samples.append({'id': identifier, 'audio_path': str(path), 'caption': f'Fixture phrase {identifier}', 'labeled': True})
        write_object(self.dataset / 'dataset.json', {'metadata': {'name': 'Regional fixture'}, 'samples': samples})
        self.entry = lora_datasets.list_datasets().datasets[0]

    async def asyncTearDown(self) -> None:
        await service.shutdown()

    async def saved_review(self) -> RegionalDatasetReview:
        rows = []
        for identifier in self.paths:
            source = {'dataset_id': self.entry.id, 'revision': self.entry.revision, 'sample_id': identifier}
            info = await service.inspect_recording(RegionalDatasetInspectionRequest(recording_id=identifier * 32, source=source))
            row = recording(identifier, source=source, source_sha256=info.source_sha256,
                original_recording_sha256=info.source_sha256, performer_group_sha256=identifier * 64,
                session_group_sha256=identifier * 64)
            row['rights'] = [{**row['rights'][0], 'source_sha256': info.source_sha256}]
            rows.append(row)
        return await service.save_review(RegionalDatasetReviewRequest.model_validate({'dataset_id': self.entry.id,
            'dataset_revision': self.entry.revision, 'expected_revision': 0, 'tradition': 'User-declared fixture',
            'recordings': rows, 'excerpts': [{'id': value * 32, 'recording_id': value * 32,
                'start_frame': 4800, 'end_frame': 14400} for value in self.paths]}))

    async def acoustic_fixture(self) -> RegionalDatasetReview:
        from app.regional_acoustic_helper_test import phrase
        for key, path in self.paths.items():
            sf.write(path, phrase(8 if key == 'c' else 4) * (-.23 if key == 'b' else 1), 8000, subtype='FLOAT')
        reviewed = await self.saved_review()
        excerpts = [item.model_copy(update={'start_frame': 0, 'end_frame': 96000}) for item in reviewed.request.excerpts]
        return await service.save_review(reviewed.request.model_copy(update={'expected_revision': reviewed.revision, 'excerpts': excerpts}))

    async def test_acoustic_subjob_groups_candidates_and_retains_reviewed_distinct_decisions(self) -> None:
        reviewed = await self.acoustic_fixture()
        request = RegionalAcousticScanRequest(dataset_id=self.entry.id, review_revision=reviewed.revision,
            review_sha256=reviewed.sha256, submission_key='0' * 32)
        job = await service.start_acoustic_scan(request)
        await asyncio.wait_for(service._tasks[job.id], 15)
        done = service.get_acoustic_scan(job.id)
        self.assertEqual(done.status, 'done'); self.assertIsNotNone(done.result)
        assert done.result is not None
        self.assertEqual(len(done.result.report.candidates), 1)
        self.assertEqual((await service.start_acoustic_scan(request)).id, job.id)
        (service._workspace(job.id) / 'acoustic-worker.json').write_bytes(b'{')
        self.assertEqual((await service.cancel_acoustic_scan(job.id)).status, 'done')
        self.assertFalse(service.work_busy(), 'terminal completed jobs already verified their worker drain')
        candidate = done.result.report.candidates[0]
        pinned = RegionalAcousticReview(scan_id=job.id, scan_sha256=done.result.sha256)
        reviewed = await service.save_review(reviewed.request.model_copy(update={'expected_revision': reviewed.revision, 'acoustic_review': pinned}))
        selection = RegionalManifestSelection(dataset_id=self.entry.id, review_revision=reviewed.revision, review_sha256=reviewed.sha256)
        forecast = await service.preview(selection)
        self.assertEqual(forecast.split.acoustic_status, 'screened')
        self.assertEqual(forecast.split.independent_groups, 2)
        self.assertEqual(forecast.split.recordings[0].cluster_sha256, forecast.split.recordings[1].cluster_sha256)
        decision = RegionalAcousticDecision(candidate_id=candidate.id, decision='distinct_after_review', reviewed_by='Fixture reviewer',
            reviewed_at='2026-10-17', note='Synthetic false-positive review fixture; no identity assertion.')
        reviewed = await service.save_review(reviewed.request.model_copy(update={'expected_revision': reviewed.revision,
            'acoustic_review': pinned.model_copy(update={'decisions': [decision]})}))
        preview = await service.preview(selection.model_copy(update={'review_revision': reviewed.revision, 'review_sha256': reviewed.sha256}))
        self.assertEqual(preview.split.independent_groups, 3)
        self.assertFalse(any(row.source_redistribution_permitted for row in preview.split.recordings))
        rows = list(reviewed.request.recordings)
        rows[1] = rows[1].model_copy(update={'original_recording_sha256': rows[0].original_recording_sha256})
        reviewed = await service.save_review(reviewed.request.model_copy(update={'expected_revision': reviewed.revision, 'recordings': rows}))
        protected = await service.preview(selection.model_copy(update={'review_revision': reviewed.revision, 'review_sha256': reviewed.sha256}))
        self.assertEqual(protected.split.independent_groups, 2, 'a distinct acoustic decision cannot break declared recording identity')
        preparation = await service.start(RegionalPreparationRequest(dataset_id=self.entry.id, review_revision=reviewed.revision,
            review_sha256=reviewed.sha256, submission_key='1' * 32))
        await asyncio.wait_for(service._tasks[preparation.id], 10)
        retained = service.get_manifest(preparation.id)
        self.assertEqual(retained.manifest.acoustic_review, reviewed.request.acoustic_review)
        assert retained.manifest.acoustic_review is not None
        self.assertEqual(retained.manifest.acoustic_review.decisions, [decision])
        await service.save_review(reviewed.request.model_copy(update={'expected_revision': reviewed.revision, 'acoustic_review': None}))
        self.assertEqual(service.get_manifest(preparation.id), retained, 'later decisions cannot rewrite the immutable manifest')
        from pydantic import ValidationError
        for replacement in (None, pinned.model_copy(update={'scan_sha256': 'f' * 64}),
                pinned.model_copy(update={'decisions': [decision.model_copy(update={'candidate_id': 'f' * 64})]})):
            with self.subTest(replacement=replacement), self.assertRaises(ValidationError):
                type(retained.manifest).model_validate({**retained.manifest.model_dump(), 'acoustic_review': replacement})

    async def test_acoustic_holdout_edges_publish_immutable_evidence_and_auditions_survive_source_removal(self) -> None:
        reviewed = await self.acoustic_fixture()
        scan = await service.start_acoustic_scan(RegionalAcousticScanRequest(dataset_id=self.entry.id,
            review_revision=reviewed.revision, review_sha256=reviewed.sha256, submission_key='0' * 32))
        await asyncio.wait_for(service._tasks[scan.id], 15)
        result = service.get_acoustic_scan(scan.id).result; assert result is not None
        pinned = RegionalAcousticReview(scan_id=scan.id, scan_sha256=result.sha256)
        rows = list(reviewed.request.recordings); rows[0] = rows[0].model_copy(update={'evaluation_only': True})
        reviewed = await service.save_review(reviewed.request.model_copy(update={'expected_revision': reviewed.revision,
            'recordings': rows, 'acoustic_review': pinned}))
        preparation = await service.start(RegionalPreparationRequest(dataset_id=self.entry.id, review_revision=reviewed.revision,
            review_sha256=reviewed.sha256, submission_key='1' * 32))
        await asyncio.wait_for(service._tasks[preparation.id], 10)
        manifest = service.get_manifest(preparation.id)
        self.assertEqual(manifest.manifest.acoustic_report, result)
        splits = {item.recording_id: item.split for item in manifest.manifest.split.recordings}
        self.assertEqual((splits['a' * 32], splits['b' * 32], splits['c' * 32]), ('holdout', 'holdout', 'train'))
        candidate = result.report.candidates[0]
        self.paths['a'].unlink(); self.paths['b'].unlink()
        audition = await service.acoustic_audition(scan.id, candidate.id, 'left')
        self.assertEqual(hashlib.sha256(audition.read_bytes()).hexdigest(), candidate.left_audition.sha256)
        self.assertNotIn(str(self.root), service.get_acoustic_scan(scan.id).model_dump_json())
        old = audition.read_bytes(); audition.write_bytes(old[:-1] + bytes([old[-1] ^ 1]))
        with self.assertRaises(RegionalDatasetError):
            await service.acoustic_audition(scan.id, candidate.id, 'left')

    async def test_acoustic_unknown_worker_keeps_owner_busy_until_explicit_verified_drain(self) -> None:
        from app.regional_acoustic_helper import AcousticProcessIdentity
        reviewed = await self.saved_review()
        request = RegionalAcousticScanRequest(dataset_id=self.entry.id, review_revision=reviewed.revision,
            review_sha256=reviewed.sha256, submission_key='0' * 32)
        job, _ = store.create_acoustic_scan(self.connection, request)
        store.set_acoustic_identity(self.connection, job.id, AcousticProcessIdentity(pid=123456, token='e' * 32))
        with patch.object(service, 'terminate_verified', new=AsyncMock(return_value=False)):
            await service.recover()
        self.assertTrue(service.work_busy())
        self.assertEqual(service.get_acoustic_scan(job.id).error_code, 'regional_dataset_worker_unverified')
        with self.assertRaises(RegionalDatasetError) as busy:
            await service.start_acoustic_scan(request.model_copy(update={'submission_key': '1' * 32}))
        self.assertEqual(busy.exception.code, 'regional_dataset_busy')
        with patch.object(service, 'terminate_verified', new=AsyncMock(return_value=True)):
            self.assertEqual((await service.cancel_acoustic_scan(job.id)).status, 'cancelled')
        self.assertFalse(service.work_busy()); self.assertIsNone(store.acoustic_identity(self.connection, job.id))

    async def test_acoustic_ambiguous_spawn_receipt_cannot_release_source_ownership(self) -> None:
        from app.video_process import WorkerReceipt
        reviewed = await self.acoustic_fixture()
        async def ambiguous(argv: list[str], *, receipt_path: Path, stdout: int | None, stderr: int | None,
                            on_identity: object) -> asyncio.subprocess.Process:
            write_object(receipt_path, WorkerReceipt(pid=123456, token='e' * 32).model_dump(mode='json'))
            raise OSError('fixture creation drain failed before on_identity')
        stopped = self.enterContext(patch.object(service, 'terminate_verified', new=AsyncMock(return_value=False)))
        with patch.object(service, 'spawn_owned', side_effect=ambiguous):
            scan = await service.start_acoustic_scan(RegionalAcousticScanRequest(dataset_id=self.entry.id,
                review_revision=reviewed.revision, review_sha256=reviewed.sha256, submission_key='0' * 32))
            await asyncio.wait_for(service._tasks[scan.id], 10)
        self.assertEqual(service.get_acoustic_scan(scan.id).error_code, 'regional_dataset_worker_unverified')
        self.assertTrue(service.work_busy())
        self.assertIsNotNone(store.acoustic_identity(self.connection, scan.id))
        service._acoustic_unverified.clear()
        await service.recover()
        self.assertTrue(service.work_busy())
        stopped.return_value = True
        self.assertEqual((await service.cancel_acoustic_scan(scan.id)).status, 'cancelled')
        self.assertFalse(service.work_busy())

    async def test_acoustic_corrupt_spawn_receipt_retains_quarantine_across_restart(self) -> None:
        from app.video_process import WorkerReceipt
        reviewed = await self.acoustic_fixture()
        async def ambiguous(argv: list[str], *, receipt_path: Path, stdout: int | None, stderr: int | None,
                            on_identity: object) -> asyncio.subprocess.Process:
            receipt_path.write_bytes(b'{')
            raise OSError('fixture creation drain failed with incomplete receipt')
        with patch.object(service, 'spawn_owned', side_effect=ambiguous):
            scan = await service.start_acoustic_scan(RegionalAcousticScanRequest(dataset_id=self.entry.id,
                review_revision=reviewed.revision, review_sha256=reviewed.sha256, submission_key='0' * 32))
            await asyncio.wait_for(service._tasks[scan.id], 10)
        self.assertEqual(service.get_acoustic_scan(scan.id).error_code, 'regional_dataset_worker_unverified')
        self.assertTrue(service.work_busy())
        self.assertIsNone(store.acoustic_identity(self.connection, scan.id))
        service._acoustic_unverified.clear()
        await service.recover()
        self.assertTrue(service.work_busy(), 'persisted quarantine survives missing identity and restart')
        self.assertEqual((await service.cancel_acoustic_scan(scan.id)).error_code, 'regional_dataset_worker_unverified')
        receipt = service._workspace(scan.id) / 'acoustic-worker.json'
        write_object(receipt, WorkerReceipt(pid=123456, token='e' * 32).model_dump(mode='json'))
        with patch.object(service, 'terminate_verified', new=AsyncMock(return_value=True)):
            self.assertEqual((await service.cancel_acoustic_scan(scan.id)).status, 'cancelled')
        self.assertFalse(service.work_busy())

    async def test_acoustic_failure_without_a_spawn_receipt_does_not_quarantine(self) -> None:
        reviewed = await self.acoustic_fixture()
        with patch.object(service, 'spawn_owned', new=AsyncMock(side_effect=OSError('fixture failed before process creation'))):
            scan = await service.start_acoustic_scan(RegionalAcousticScanRequest(dataset_id=self.entry.id,
                review_revision=reviewed.revision, review_sha256=reviewed.sha256, submission_key='0' * 32))
            await asyncio.wait_for(service._tasks[scan.id], 10)
        self.assertEqual(service.get_acoustic_scan(scan.id).error_code, 'regional_dataset_storage_unavailable')
        self.assertFalse(service.work_busy())
        self.assertIsNone(store.acoustic_identity(self.connection, scan.id))
        await service.recover()
        self.assertFalse(service.work_busy())

    async def test_acoustic_source_removed_before_start_saves_a_terminal_failure(self) -> None:
        reviewed = await self.saved_review()
        self.paths['a'].unlink()
        scan = await service.start_acoustic_scan(RegionalAcousticScanRequest(dataset_id=self.entry.id,
            review_revision=reviewed.revision, review_sha256=reviewed.sha256, submission_key='0' * 32))
        await asyncio.wait_for(service._tasks[scan.id], 10)
        failed = service.get_acoustic_scan(scan.id)
        self.assertEqual(failed.status, 'failed')
        self.assertEqual(failed.error_code, 'regional_dataset_source_missing')
        self.assertFalse(service.work_busy())

    async def test_acoustic_low_disk_stops_before_native_decode_and_source_change_cannot_publish(self) -> None:
        reviewed = await self.acoustic_fixture()
        request = RegionalAcousticScanRequest(dataset_id=self.entry.id, review_revision=reviewed.revision,
            review_sha256=reviewed.sha256, submission_key='0' * 32)
        with patch.object(service, 'available_bytes', return_value=service._RESERVE), \
                patch.object(service, '_execute_acoustic_worker', new=AsyncMock()) as execute:
            scan = await service.start_acoustic_scan(request)
            await asyncio.wait_for(service._tasks[scan.id], 10)
        self.assertEqual(service.get_acoustic_scan(scan.id).error_code, 'insufficient_disk'); execute.assert_not_awaited()
        native = service._execute_acoustic_worker
        async def changed(identifier: str, worker: object) -> object:
            report = await native(identifier, worker)
            path = self.paths['a']; path.write_bytes(path.read_bytes() + b'changed fixture')
            return report
        with patch.object(service, '_execute_acoustic_worker', side_effect=changed):
            scan = await service.start_acoustic_scan(request.model_copy(update={'submission_key': '1' * 32}))
            await asyncio.wait_for(service._tasks[scan.id], 15)
        self.assertEqual(service.get_acoustic_scan(scan.id).status, 'failed')
        self.assertIsNone(service.get_acoustic_scan(scan.id).result)

    async def test_acoustic_cancellation_drains_worker_and_restart_does_not_rescan(self) -> None:
        reviewed = await self.acoustic_fixture()
        request = RegionalAcousticScanRequest(dataset_id=self.entry.id, review_revision=reviewed.revision,
            review_sha256=reviewed.sha256, submission_key='0' * 32)
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked(identifier: str, worker: object) -> object:
            entered.set()
            try:
                await asyncio.Future()
            finally:
                await release.wait()
        with patch.object(service, '_execute_acoustic_worker', side_effect=blocked):
            job = await service.start_acoustic_scan(request)
            await asyncio.wait_for(entered.wait(), 5)
            cancellation = asyncio.create_task(service.cancel_acoustic_scan(job.id))
            await asyncio.sleep(.01); self.assertFalse(cancellation.done()); self.assertTrue(service.work_busy())
            release.set(); result = await asyncio.wait_for(cancellation, 5)
        self.assertEqual(result.status, 'cancelled'); self.assertIsNone(result.result)
        interrupted, _ = store.create_acoustic_scan(self.connection, request.model_copy(update={'submission_key': '1' * 32}))
        await service.recover()
        self.assertEqual(service.get_acoustic_scan(interrupted.id).status, 'interrupted')
        self.assertFalse(service.work_busy())

    async def test_acoustic_cancel_before_worker_first_turn_releases_owner_registry(self) -> None:
        reviewed = await self.saved_review()
        job = await service.start_acoustic_scan(RegionalAcousticScanRequest(dataset_id=self.entry.id,
            review_revision=reviewed.revision, review_sha256=reviewed.sha256, submission_key='0' * 32))
        cancelled = await service.cancel_acoustic_scan(job.id)
        self.assertEqual(cancelled.status, 'cancelled')
        self.assertNotIn(job.id, service._tasks)
        self.assertFalse(service.work_busy())

    async def test_acoustic_real_cancel_terminates_a_supervised_descendant_before_releasing_inputs(self) -> None:
        import sys
        import subprocess
        from app.video_process import WorkerIdentity
        if sys.platform == 'win32':
            self.skipTest('POSIX descendant observation; Windows job ownership has separate tests')
        reviewed = await self.acoustic_fixture()
        child_pid = self.root / 'child.pid'
        real_spawn = service.spawn_owned
        processes: list[asyncio.subprocess.Process] = []
        async def spawn(argv: list[str], *, receipt_path: Path, stdout: int | None, stderr: int | None,
                        on_identity: Callable[[WorkerIdentity], None]) -> asyncio.subprocess.Process:
            code = ('import subprocess,sys,time; from pathlib import Path; '
                'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(120)"]); '
                'Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(120)')
            process = await real_spawn([sys.executable, '-c', code, str(child_pid)], receipt_path=receipt_path,
                stdout=stdout, stderr=stderr, on_identity=on_identity)
            processes.append(process)
            return process
        with patch.object(service, 'spawn_owned', side_effect=spawn):
            job = await service.start_acoustic_scan(RegionalAcousticScanRequest(dataset_id=self.entry.id,
                review_revision=reviewed.revision, review_sha256=reviewed.sha256, submission_key='0' * 32))
            async with asyncio.timeout(5):
                while not child_pid.exists():
                    await asyncio.sleep(.01)
            cancelled = await asyncio.wait_for(service.cancel_acoustic_scan(job.id), 5)
        self.assertEqual(cancelled.status, 'cancelled'); self.assertFalse(service.work_busy())
        self.assertTrue(processes); self.assertIsNotNone(processes[0].returncode)
        observed = subprocess.run(['ps', '-p', child_pid.read_text(), '-o', 'stat='], capture_output=True, text=True, check=False)
        self.assertFalse(observed.stdout.strip(), 'descendant must exit before the dataset owner becomes idle')
        self.assertTrue(all(path.exists() for path in self.paths.values()))

    async def test_stale_acoustic_source_spans_and_unknown_decisions_cannot_be_saved(self) -> None:
        reviewed = await self.acoustic_fixture()
        job = await service.start_acoustic_scan(RegionalAcousticScanRequest(dataset_id=self.entry.id,
            review_revision=reviewed.revision, review_sha256=reviewed.sha256, submission_key='0' * 32))
        await asyncio.wait_for(service._tasks[job.id], 15)
        result = service.get_acoustic_scan(job.id).result
        assert result is not None
        pinned = RegionalAcousticReview(scan_id=job.id, scan_sha256=result.sha256)
        changed = reviewed.request.excerpts[0].model_copy(update={'start_frame': 1})
        for changes in ({'excerpts': [changed, *reviewed.request.excerpts[1:]]},
                {'acoustic_review': pinned.model_copy(update={'scan_sha256': 'f' * 64})},
                {'acoustic_review': pinned.model_copy(update={'decisions': [RegionalAcousticDecision(candidate_id='f' * 64,
                    decision='distinct_after_review', reviewed_by='Fixture', reviewed_at='2026-10-17', note='Not a real candidate.')]})}):
            with self.subTest(changes=changes), self.assertRaises(RegionalDatasetError):
                await service.save_review(reviewed.request.model_copy(update={'expected_revision': reviewed.revision,
                    'acoustic_review': pinned, **changes}))

    async def test_prepares_independent_originals_and_only_training_excerpts_can_resolve_for_trainers(self) -> None:
        original = {key: value.read_bytes() for key, value in self.paths.items()}
        reviewed = await self.saved_review()
        selection = RegionalManifestSelection(dataset_id=self.entry.id, review_revision=reviewed.revision, review_sha256=reviewed.sha256)
        forecast = await service.preview(selection)
        self.assertTrue(forecast.can_prepare)
        self.assertTrue(forecast.split.evaluation_sufficient)
        request = RegionalPreparationRequest(**selection.model_dump(), submission_key='e' * 32)
        job = await service.start(request)
        await asyncio.wait_for(service._tasks[job.id], 5)
        done = service.get(job.id)
        self.assertEqual(done.status, 'done')
        self.assertIsNotNone(done.result)
        manifest = store.get_manifest(self.connection, job.id)
        import json
        legacy = manifest.manifest.model_dump(mode='json')
        self.assertNotIn('acoustic_review', legacy)
        self.assertNotIn('acoustic_report', legacy)
        legacy_sha = hashlib.sha256(json.dumps(legacy, sort_keys=True, separators=(',', ':'),
            ensure_ascii=True, allow_nan=False).encode('ascii')).hexdigest()
        self.assertEqual(manifest.sha256, legacy_sha)
        self.assertEqual(store.canonical_sha256(type(manifest.manifest).model_validate_json(json.dumps(legacy))), legacy_sha)
        for excerpt in manifest.manifest.excerpts:
            if excerpt.split == 'holdout':
                with self.assertRaises(RegionalDatasetError) as heldout:
                    service.training_excerpt_source(job.id, manifest.sha256, excerpt.id)
                self.assertEqual(heldout.exception.code, 'regional_dataset_holdout')
            else:
                retained = service.training_excerpt_source(job.id, manifest.sha256, excerpt.id)
                self.assertEqual(retained.sha256, hashlib.sha256(retained.path.read_bytes()).hexdigest())
                self.assertEqual(sf.info(retained.path).frames, 9600)
        for key, path in self.paths.items():
            self.assertEqual(path.read_bytes(), original[key])
            copy = service._workspace(job.id) / 'sources' / f'{key * 32}.wav'
            self.assertEqual(copy.read_bytes(), original[key]); self.assertNotEqual(copy.stat().st_ino, path.stat().st_ino)

    async def test_cancellation_drains_crop_reader_and_retains_the_original_snapshot(self) -> None:
        reviewed = await self.saved_review()
        entered, release = threading.Event(), threading.Event()
        crop = service._crop_excerpt
        def blocked(source: Path, output: Path, excerpt: RegionalPreparedExcerpt, signature: tuple[int, int, int, int, int]) -> None:
            entered.set(); release.wait(5)
            crop(source, output, excerpt, signature)
        request = RegionalPreparationRequest(dataset_id=self.entry.id, review_revision=reviewed.revision,
            review_sha256=reviewed.sha256, submission_key='f' * 32)
        with patch.object(service, '_crop_excerpt', side_effect=blocked):
            job = await service.start(request)
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            cancellation = asyncio.create_task(service.cancel(job.id))
            await asyncio.sleep(0); self.assertFalse(cancellation.done())
            release.set(); cancelled = await asyncio.wait_for(cancellation, 5)
        self.assertEqual(cancelled.status, 'cancelled')
        self.assertTrue(list((service._workspace(job.id) / 'sources').glob('*.wav')))
        self.assertIsNone(cancelled.result)

    async def test_snapshot_changed_and_restored_during_crop_cannot_publish_wrong_provenance(self) -> None:
        reviewed = await self.saved_review()
        crop = service._crop_excerpt
        def tampered(source: Path, output: Path, excerpt: RegionalPreparedExcerpt, signature: tuple[int, int, int, int, int]) -> None:
            original = source.read_bytes(); changed = bytearray(original); changed[len(changed) // 5] ^= 1
            source.write_bytes(changed)
            try:
                crop(source, output, excerpt, signature)
            finally:
                source.write_bytes(original)
        with patch.object(service, '_crop_excerpt', side_effect=tampered):
            job = await service.start(RegionalPreparationRequest(dataset_id=self.entry.id, review_revision=reviewed.revision,
                review_sha256=reviewed.sha256, submission_key='f' * 32))
            await asyncio.wait_for(service._tasks[job.id], 5)
        self.assertEqual(service.get(job.id).status, 'failed')
        self.assertEqual(service.get(job.id).error_code, 'regional_dataset_source_changed')

    async def test_retry_save_uses_retained_audio_after_failed_database_publication(self) -> None:
        reviewed = await self.saved_review()
        request = RegionalPreparationRequest(dataset_id=self.entry.id, review_revision=reviewed.revision,
            review_sha256=reviewed.sha256, submission_key='e' * 32)
        with patch.object(store, 'publish_manifest', side_effect=sqlite3.OperationalError('fixture write failure')):
            job = await service.start(request)
            await asyncio.wait_for(service._tasks[job.id], 5)
        failed = service.get(job.id)
        self.assertEqual(failed.status, 'failed'); self.assertTrue(failed.can_retry_save)
        with patch.object(service, '_crop_excerpt', side_effect=AssertionError('completed crops must be reused')):
            await service.retry_save(job.id)
            await asyncio.wait_for(service._tasks[job.id], 5)
        self.assertEqual(service.get(job.id).status, 'done')
        self.assertEqual((await service.start(request)).id, job.id)

    async def test_manifest_training_selection_excludes_holdout_and_rejects_added_sources(self) -> None:
        reviewed = await self.saved_review()
        job = await service.start(RegionalPreparationRequest(dataset_id=self.entry.id, review_revision=reviewed.revision,
            review_sha256=reviewed.sha256, submission_key='e' * 32))
        await asyncio.wait_for(service._tasks[job.id], 5)
        manifest = store.get_manifest(self.connection, job.id)
        selection = RegionalPreparedSelection(manifest_id=job.id, manifest_sha256=manifest.sha256)
        selected = service.training_selection(selection)
        self.assertEqual(len(selected.samples), sum(item.split == 'train' for item in manifest.manifest.excerpts))
        self.assertGreater(selected.held_out_excerpts, 0)
        request = StableLoraTrainingRequest(name='Fixture only', samples=selected.samples, max_steps=1, rank=8)
        with patch('app.stable_lora.submit', new=AsyncMock()) as submitted:
            await service.submit_training(RegionalTrainingSubmission(**selection.model_dump(), request=request))
            submitted.assert_awaited_once_with(request)
            heldout = next(item for item in manifest.manifest.excerpts if item.split == 'holdout')
            invalid = request.model_copy(update={'samples': [*request.samples, request.samples[0].model_copy(update={
                'regional_manifest': request.samples[0].regional_manifest.model_copy(update={'excerpt_id': heldout.id})})]})
            with self.assertRaises(RegionalDatasetError) as refused:
                await service.submit_training(RegionalTrainingSubmission(**selection.model_dump(), request=invalid))
            self.assertEqual(refused.exception.code, 'regional_dataset_holdout')
            self.assertEqual(submitted.await_count, 1)

    async def test_retry_save_refuses_tampered_retained_audio_and_preserves_original(self) -> None:
        reviewed = await self.saved_review()
        request = RegionalPreparationRequest(dataset_id=self.entry.id, review_revision=reviewed.revision,
            review_sha256=reviewed.sha256, submission_key='e' * 32)
        with patch.object(store, 'publish_manifest', side_effect=sqlite3.OperationalError('fixture write failure')):
            job = await service.start(request)
            await asyncio.wait_for(service._tasks[job.id], 5)
        crop = next((service._workspace(job.id) / 'excerpts').glob('[a-f0-9]' * 32 + '.wav'))
        changed = bytearray(crop.read_bytes()); changed[-1] ^= 1; crop.write_bytes(changed)
        await service.retry_save(job.id)
        await asyncio.wait_for(service._tasks[job.id], 5)
        self.assertEqual(service.get(job.id).status, 'failed')
        self.assertEqual(service.get(job.id).error_code, 'regional_dataset_source_changed')
        self.assertTrue(crop.exists()); self.assertTrue(all(path.exists() for path in self.paths.values()))

    async def test_disk_forecast_refuses_before_copy_and_existing_submission_survives_review_change(self) -> None:
        reviewed = await self.saved_review()
        request = RegionalPreparationRequest(dataset_id=self.entry.id, review_revision=reviewed.revision,
            review_sha256=reviewed.sha256, submission_key='e' * 32)
        with patch.object(service, 'available_bytes', return_value=1), patch.object(service, 'copy_verified') as copied:
            job = await service.start(request)
            await asyncio.wait_for(service._tasks[job.id], 5)
        self.assertEqual(service.get(job.id).error_code, 'insufficient_disk'); copied.assert_not_called()
        await service.save_review(reviewed.request.model_copy(update={'expected_revision': reviewed.revision}))
        self.assertEqual((await service.start(request)).id, job.id)

    async def test_recovery_accounts_for_all_active_preparations_beyond_history_limit(self) -> None:
        reviewed = await self.saved_review()
        identifiers = []
        for number in range(103):
            job, _created = store.create_preparation(self.connection, RegionalPreparationRequest(dataset_id=self.entry.id,
                review_revision=reviewed.revision, review_sha256=reviewed.sha256, submission_key=f'{number:032x}'), 6)
            identifiers.append(job.id)
        await service.recover()
        self.assertEqual(len(service.list_preparations().jobs), 100)
        self.assertTrue(all(service.get(identifier).status == 'interrupted' for identifier in identifiers))
        self.assertFalse(service.work_busy())

    async def test_comparison_uses_same_seed_and_existing_generator_without_repeating_submitted_arm(self) -> None:
        from app import regional_evaluation as evaluation
        reviewed = await self.saved_review()
        prepared = await service.start(RegionalPreparationRequest(dataset_id=self.entry.id, review_revision=reviewed.revision,
            review_sha256=reviewed.sha256, submission_key='e' * 32))
        await asyncio.wait_for(service._tasks[prepared.id], 5)
        manifest = service.get_manifest(prepared.id)
        selected = service.training_selection(RegionalPreparedSelection(manifest_id=prepared.id, manifest_sha256=manifest.sha256))
        adapter = MusicAdapter(id='f' * 32, engine='stable_audio3', target='small-music', name='Fixture adapter',
            sha256='e' * 64, created_at='2026-10-17T00:00:00Z', source_job_id='a' * 32)
        trained = StableLoraTrainingJob(id='a' * 32, status='done', created_at='2026-10-17T00:00:00Z',
            request=StableLoraTrainingRequest(name='Fixture adapter', samples=selected.samples), adapter=adapter)
        receipt = RegionalTrainingReceipt(job_id=trained.id, request_sha256=fingerprint(trained.request), dataset_sha256='d' * 64, identity_status='unavailable')
        request = RegionalEvaluationRequest(manifest_id=prepared.id, manifest_sha256=manifest.sha256,
            submission_key='b' * 32, adapter_id=adapter.id, adapter_sha256=adapter.sha256,
            request={'engine': 'stable_audio3', 'prompt': 'Reviewed fixture phrase', 'duration_seconds': 5,
                'seed': 42, 'adapter': {'id': adapter.id, 'strength': 1}})
        jobs: dict[str, LocalMusicJob] = {}
        async def submitted(value: LocalMusicRequest) -> LocalMusicJob:
            identifier = ('c' if value.adapter is None else 'd') * 32
            result = LocalMusicJob(id=identifier, status='queued', created_at='2026-10-17T00:00:00Z', request=value,
                adapter=adapter if value.adapter is not None else None)
            jobs[identifier] = result
            return result
        with patch('app.music_lora.get', return_value=adapter), patch('app.stable_lora.get', return_value=trained), \
                patch.object(evaluation, 'training_receipt', new=AsyncMock(return_value=receipt)), \
                patch('app.local_music.submit', new=AsyncMock(side_effect=submitted)) as native, \
                patch('app.local_music.get', side_effect=lambda identifier: jobs[identifier]), \
                patch.object(evaluation, 'available_bytes', return_value=100 * 1024**3):
            plan = await evaluation.create(request)
            self.assertEqual((await evaluation.create(request)).sha256, plan.sha256)
            await evaluation.submit_arm(plan.spec.id, 'base')
            await evaluation.submit_arm(plan.spec.id, 'base')
            await evaluation.submit_arm(plan.spec.id, 'adapter')
            self.assertEqual(native.await_count, 2)
            self.connection.execute('CREATE TABLE IF NOT EXISTS local_music_jobs(id TEXT PRIMARY KEY,payload_json TEXT NOT NULL)')
            for identifier, child in jobs.items():
                self.connection.execute('INSERT INTO local_music_jobs(id,payload_json) VALUES(?,?)', (identifier, child.model_dump_json()))
            self.connection.execute("UPDATE regional_comparison_arms SET job_id=NULL,submitting=1 WHERE comparison_id=? AND arm='base'", (plan.spec.id,))
            self.connection.commit()
            await evaluation.recover()
            restored = next(item for item in evaluation.get(plan.spec.id).arms if item.arm == 'base')
            self.assertEqual(restored.job.id, 'c' * 32); self.assertFalse(restored.submission_unverified)
            self.assertEqual(native.await_count, 2)
            base, adapted = [call.args[0] for call in native.await_args_list]
            self.assertEqual(base.seed, adapted.seed); self.assertEqual(base.prompt, adapted.prompt)
            self.assertIsNone(base.adapter); self.assertEqual(adapted.adapter.id, adapter.id)
            self.assertEqual(plan.spec.quality_status, 'unverified')
            self.assertIn('fad', plan.spec.unavailable_metrics)
            from app.local_music import CompletedLocalMusicAudio
            for identifier in jobs:
                jobs[identifier] = jobs[identifier].model_copy(update={'status': 'done'})
            with patch('app.local_music.completed_audio', return_value=CompletedLocalMusicAudio(
                    path=self.paths['a'], sha256=hashlib.sha256(self.paths['a'].read_bytes()).hexdigest(), track_id=1)):
                # Reuse a verified partial retained when the previous request was interrupted.
                target = evaluation._audio_path(plan.spec.id, 'base', create=True)
                target.with_suffix('.partial.wav').write_bytes(self.paths['a'].read_bytes())
                await evaluation.collect(plan.spec.id, 'base')
                state = await evaluation.collect(plan.spec.id, 'adapter')
            hashes = {item.arm: item.output_sha256 for item in state.arms}
            self.paths['a'].unlink()
            self.assertTrue((await evaluation.audio(plan.spec.id, 'base')).exists())
            review = RegionalListeningReview(comparison_sha256=plan.sha256, base_output_sha256=hashes['base'],
                adapter_output_sha256=hashes['adapter'], reviewer_reference='fixture-listener', reviewed_at='2026-10-17',
                tradition_scope='Fixture scope only', preference='neither', timbre=2, phrasing=2, rhythm=2,
                coherence=2, artifacts=2, cultural_appropriateness=2)
            state = await evaluation.review(plan.spec.id, review)
            self.assertEqual(state.listener_reviews, 1)
            self.assertEqual((await evaluation.review(plan.spec.id, review)).listener_reviews, 1)
            self.assertEqual(state.plan.spec.quality_status, 'unverified')
            target.write_bytes(b'changed retained comparison')
            with self.assertRaises(RegionalDatasetError):
                await evaluation.review(plan.spec.id, review.model_copy(update={'reviewer_reference': 'another-listener'}))
