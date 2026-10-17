from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import wave
from unittest.mock import AsyncMock, patch

from app import db, music_analysis
from app.audio_quality_contracts import AudioMetrics
from app.music_analysis_contracts import AnalysisResult, KeyEstimate, MusicAnalysisJob, MusicAnalysisOverrides, MusicAnalysisRequest, VersionMusicSource
from app import music_beats_runtime
from app.music_beats import BEATS_REVISION, LICENSE_EVIDENCE_IDS, BeatAnalysisRecipe, BeatRuntimeIdentity
from app.music_analysis_contracts import AnalysisEngine


def result(source: VersionMusicSource, identifier: str = 'b' * 32) -> AnalysisResult:
    return AnalysisResult(id=identifier, source=source, audio_sha256='c' * 64,
        sample_rate_hz=8000, channels=1, duration_frames=24000, duration_sec=3.0,
        analysis_sample_rate_hz=8000, hop_sec=.128, analyzer_revision='numpy-summary-v1',
        configuration_sha256='d' * 64, loudness=AudioMetrics(duration_sec=3.0, source_sha256='c' * 64),
        created_at='2026-10-17T00:00:00Z')


class MusicAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / 'catalog.db'
        connection = sqlite3.connect(self.path)
        connection.execute('CREATE TABLE legacy(id INTEGER PRIMARY KEY, value TEXT)')
        connection.execute("INSERT INTO legacy VALUES(1,'preserved')")
        music_analysis.migrate(connection)
        connection.close()
        self.enterContext(patch.object(db, 'DB_PATH', self.path))
        self.enterContext(patch.object(db, 'FILES_DIR', self.root / 'files'))
        self.enterContext(patch.object(music_analysis, '_root', return_value=self.root / 'analysis'))
        self.enterContext(patch.object(db, 'get_db', return_value=None))
        self.source = VersionMusicSource(track_id=1, version_id='a' * 32)
        self.request = MusicAnalysisRequest(source=self.source)
        self.cache_key = music_analysis.cache_key('c' * 64, 'version', 'd' * 64)
        await music_analysis.shutdown()
        await music_analysis.recover()

    async def asyncTearDown(self) -> None:
        await music_analysis.shutdown()
        music_analysis._unverified.clear()
        music_analysis._sources.clear()
        music_analysis._processes.clear()

    async def test_missing_optional_model_fails_before_job_or_probe_and_baseline_stays_available(self) -> None:
        file = self.root / 'input.wav'; file.write_bytes(b'preserved original')
        request = MusicAnalysisRequest(source=self.source, profile='music-beats-v1')
        with patch.object(music_analysis, '_resolve_source', return_value=file), \
                patch.object(music_beats_runtime, 'recipe', side_effect=music_beats_runtime.BeatRuntimeError('analysis_weights_missing')), \
                patch.object(music_analysis, 'run_owned_command', new=AsyncMock()) as probe, \
                patch.object(music_analysis, '_semaphore', asyncio.Semaphore(0)):
            for work in (music_analysis.preview, music_analysis.start):
                with self.assertRaises(music_analysis.MusicAnalysisError) as error:
                    await work(request)
                self.assertEqual(error.exception.code, 'analysis_weights_missing')
            self.assertEqual(music_analysis.list_jobs(), [])
            probe.assert_not_awaited()
            baseline = await music_analysis.start(self.request)
            self.assertEqual(baseline.status, 'queued')
            await music_analysis.cancel(baseline.id)
        self.assertEqual(file.read_bytes(), b'preserved original')

    async def test_active_optional_submission_reattaches_before_fresh_model_admission(self) -> None:
        file = self.root / 'input.wav'; file.write_bytes(b'original')
        identity = BeatRuntimeIdentity(source_sha256='a' * 64, weights_sha256='b' * 64,
            python_sha256='c' * 64, python_version='3.12.13', packages={str(n): '1' for n in range(8)})
        request = MusicAnalysisRequest(source=self.source, profile='music-beats-v1')
        with patch.object(music_analysis, '_resolve_source', return_value=file), \
                patch.object(music_beats_runtime, 'recipe', return_value=(self.root, identity)) as ready, \
                patch.object(music_analysis, '_semaphore', asyncio.Semaphore(0)):
            first = await music_analysis.start(request)
            ready.side_effect = music_beats_runtime.BeatRuntimeError('analysis_weights_missing')
            attached = await music_analysis.start(request)
            self.assertEqual(first.id, attached.id)
            self.assertEqual(ready.call_count, 1)
            await music_analysis.cancel(first.id)
            with self.assertRaises(music_analysis.MusicAnalysisError) as error:
                await music_analysis.retry(first.id)
            self.assertEqual(error.exception.code, 'analysis_weights_missing')
            self.assertEqual(music_analysis.get(first.id).status, 'cancelled')

    def test_optional_model_identity_changes_analysis_configuration(self) -> None:
        identity = BeatRuntimeIdentity(source_sha256='a' * 64, weights_sha256='b' * 64,
            python_sha256='c' * 64, python_version='3.12.13', packages={str(n): '1' for n in range(8)})
        baseline = music_analysis.configuration_digest('ffmpeg fixture')
        selected = music_analysis.configuration_digest('ffmpeg fixture', beats=identity)
        other = music_analysis.configuration_digest('ffmpeg fixture', beats=identity.model_copy(update={'weights_sha256': 'd' * 64}))
        self.assertNotEqual(baseline, selected)
        self.assertNotEqual(selected, other)

    def test_worker_receipt_cannot_substitute_foreign_model_or_drop_declaration_identity(self) -> None:
        identity = BeatRuntimeIdentity(source_sha256='a' * 64, weights_sha256='b' * 64,
            python_sha256='c' * 64, python_version='3.12.13', packages={str(n): '1' for n in range(8)})
        raw = result(self.source)
        body = raw.model_dump()
        body.update(analysis_sample_rate_hz=22050, weights_sha256=identity.weights_sha256,
            engines=[AnalysisEngine(task='beats', name='beat-this-small0', revision=BEATS_REVISION,
                configuration_sha256=identity.configuration_sha256, weights_sha256=identity.weights_sha256).model_dump()],
            license_evidence_ids=list(LICENSE_EVIDENCE_IDS),
            loudness=AudioMetrics(duration_sec=3.0, source_sha256=raw.audio_sha256, samples_analyzed=24000).model_dump())
        raw = AnalysisResult.model_validate(body)
        request = music_analysis.AnalysisWorkerRequest(job_id='a' * 32, result_id=raw.id,
            source=self.source, snapshot_name='source.wav', audio_sha256=raw.audio_sha256,
            configuration_sha256=raw.configuration_sha256, ffmpeg_version='fixture', ffmpeg_sha256='f' * 64,
            created_at=raw.created_at, beats=BeatAnalysisRecipe(runtime_root=str(self.root), identity=identity))
        for changes in ({}, {'weights_sha256': 'f' * 64}, {'license_evidence_ids': []}, {'engines': []}):
            changed = AnalysisResult.model_validate({**raw.model_dump(), **changes})
            content = music_analysis.AnalysisWorkerOutcome(result=changed).model_dump_json().encode()
            receipt = music_analysis.AnalysisWorkerReceipt(result_sha256=hashlib.sha256(content).hexdigest())
            if not changes:
                self.assertEqual(music_analysis._validate_outcome(request, receipt, content), raw)
            else:
                with self.subTest(changes=changes), self.assertRaises(music_analysis.MusicAnalysisError):
                    music_analysis._validate_outcome(request, receipt, content)

    async def test_migration_is_additive_and_constraints_reject_bad_rows(self) -> None:
        connection = sqlite3.connect(self.path)
        music_analysis.migrate(connection)
        self.assertEqual(connection.execute('SELECT value FROM legacy').fetchone()[0], 'preserved')
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO music_analysis_results VALUES(?,?,?,?,?,?,?)",
                ('bad', '0' * 64, 1, '0' * 64, '0' * 64, '{}', '2026-10-17T00:00:00Z'))
        connection.close()

    async def test_duplicate_submission_claims_once_and_queued_cancel_never_runs_worker(self) -> None:
        source_file = self.root / 'files' / 'source.wav'
        source_file.parent.mkdir()
        source_file.write_bytes(b'original bytes')
        gate = asyncio.Semaphore(0)
        with patch.object(music_analysis, '_resolve_source', return_value=source_file), \
                patch.object(music_analysis, '_semaphore', gate), \
                patch.object(music_analysis, '_execute_worker', new=AsyncMock()) as worker:
            first, second = await asyncio.gather(music_analysis.start(self.request), music_analysis.start(self.request))
            self.assertEqual(first.id, second.id)
            cancelled = await music_analysis.cancel(first.id)
            self.assertEqual(cancelled.status, 'cancelled')
            self.assertFalse(music_analysis.work_busy())
            worker.assert_not_awaited()
        self.assertEqual(source_file.read_bytes(), b'original bytes')

    async def test_publish_cannot_resurrect_cancelled_revision(self) -> None:
        job = MusicAnalysisJob(id='a' * 32, source=self.source, created_at='2026-10-17T00:00:00Z')
        music_analysis._insert_job(job, self.request, 'e' * 64)
        running = music_analysis._change(job.id, status='running', phase='features')
        old_revision = music_analysis._record(job.id).revision
        music_analysis._change(job.id, status='cancelled', cancellation_requested=True)
        self.assertFalse(music_analysis._publish(running.id, old_revision, self.cache_key, result(self.source)))
        self.assertEqual(music_analysis.get(job.id).status, 'cancelled')

    async def test_override_is_cas_validated_and_machine_result_stays_immutable(self) -> None:
        job = MusicAnalysisJob(id='a' * 32, source=self.source, created_at='2026-10-17T00:00:00Z')
        music_analysis._insert_job(job, self.request, 'e' * 64)
        music_analysis._change(job.id, status='running', phase='features')
        raw = result(self.source)
        self.assertTrue(music_analysis._publish(job.id, music_analysis._record(job.id).revision, self.cache_key, raw))
        changed = music_analysis.put_overrides(raw.id, MusicAnalysisOverrides(analysis_id=raw.id, expected_revision=0,
            key=KeyEstimate(status='known', tonic_pc=9, mode='minor', unknown_reason=None)))
        self.assertEqual((changed.override_revision, changed.key.method), (1, 'manual'))
        with self.assertRaises(music_analysis.MusicAnalysisError) as conflict:
            music_analysis.put_overrides(raw.id, MusicAnalysisOverrides(analysis_id=raw.id, expected_revision=0, beats_sec=[.5]))
        self.assertEqual(conflict.exception.code, 'analysis_revision_conflict')
        with self.assertRaises(music_analysis.MusicAnalysisError):
            music_analysis.put_overrides(raw.id, MusicAnalysisOverrides(analysis_id=raw.id, expected_revision=1, beats_sec=[4.0]))
        self.assertEqual(music_analysis.get(job.id).result, changed)
        with sqlite3.connect(self.path) as connection:
            encoded = connection.execute('SELECT result_json FROM music_analysis_results').fetchone()[0]
        self.assertEqual(AnalysisResult.model_validate_json(encoded), raw)

    async def test_content_cache_rebinds_source_without_leaking_prior_track(self) -> None:
        first = result(self.source)
        job = MusicAnalysisJob(id='a' * 32, source=self.source, created_at='2026-10-17T00:00:00Z')
        music_analysis._insert_job(job, self.request, 'e' * 64)
        music_analysis._change(job.id, status='running')
        music_analysis._publish(job.id, music_analysis._record(job.id).revision, self.cache_key, first)
        other = VersionMusicSource(track_id=987, version_id='e' * 32)
        cached = music_analysis._cached(self.cache_key, other)
        self.assertIsNotNone(cached)
        self.assertEqual(cached.source if cached else None, other)
        self.assertNotIn('"track_id":1,', cached.model_dump_json() if cached else '')

    async def test_cached_tracks_have_independent_analysis_ids_and_manual_corrections(self) -> None:
        first = result(self.source)
        job = MusicAnalysisJob(id='a' * 32, source=self.source, created_at='2026-10-17T00:00:00Z')
        music_analysis._insert_job(job, self.request, 'e' * 64)
        music_analysis._change(job.id, status='running')
        music_analysis._publish(job.id, music_analysis._record(job.id).revision, self.cache_key, first)
        other_source = VersionMusicSource(track_id=2, version_id='2' * 32)
        second_job = MusicAnalysisJob(id='3' * 32, source=other_source, created_at='2026-10-17T00:00:00Z')
        music_analysis._insert_job(second_job, MusicAnalysisRequest(source=other_source), '4' * 64)
        music_analysis._change(second_job.id, status='running')
        second = music_analysis._cached(self.cache_key, other_source)
        self.assertIsNotNone(second)
        if second is None:
            self.fail('missing raw cache')
        self.assertNotEqual(second.id, first.id)
        music_analysis._publish(second_job.id, music_analysis._record(second_job.id).revision, self.cache_key, second)
        corrected = music_analysis.put_overrides(second.id, MusicAnalysisOverrides(analysis_id=second.id,
            expected_revision=0, key=KeyEstimate(status='known', tonic_pc=9, mode='minor', unknown_reason=None)))
        self.assertEqual(corrected.source, other_source)
        original = music_analysis.get(job.id).result
        self.assertEqual(original, first)
        self.assertEqual(music_analysis.get(second_job.id).result, corrected)

    async def test_source_resolver_rejects_symlink_escape_and_digest_mismatch(self) -> None:
        root = self.root / 'files'
        root.mkdir()
        original = self.root / 'outside.wav'
        original.write_bytes(b'original')
        link = root / 'escape.wav'
        link.symlink_to(original)
        with patch('app.audio_versions.resolve_source', return_value=link):
            with self.assertRaises(music_analysis.MusicAnalysisError) as error:
                music_analysis._resolve_source(self.source)
        self.assertEqual(error.exception.code, 'source_missing')
        self.assertEqual(original.read_bytes(), b'original')

    async def test_recovery_never_kills_unverified_identity_and_retains_quarantine(self) -> None:
        job = MusicAnalysisJob(id='a' * 32, source=self.source, created_at='2026-10-17T00:00:00Z')
        music_analysis._insert_job(job, self.request, 'e' * 64)
        music_analysis._change(job.id, status='running')
        folder = music_analysis._workspace(job.id)
        folder.mkdir(parents=True)
        identity = music_analysis.WorkerIdentity(pid=123, token='f' * 32, receipt=str(folder / 'worker-1.json'))
        music_analysis._save_identity(job.id, identity)
        with patch.object(music_analysis, 'terminate_verified', new=AsyncMock(return_value=False)):
            await music_analysis.recover()
        self.assertTrue(music_analysis.work_busy())
        self.assertTrue(folder.exists())
        self.assertEqual(music_analysis.get(job.id).error_code, 'worker_unverified')

    async def test_cache_identity_changes_with_role_runtime_and_source(self) -> None:
        base = music_analysis.cache_key('a' * 64, 'version', 'b' * 64)
        self.assertNotEqual(base, music_analysis.cache_key('c' * 64, 'version', 'b' * 64))
        self.assertNotEqual(base, music_analysis.cache_key('a' * 64, 'stem:vocals', 'b' * 64))
        self.assertNotEqual(base, music_analysis.cache_key('a' * 64, 'version', 'd' * 64))

    async def test_immediate_cancel_and_shutdown_release_jobs_that_never_entered_runner(self) -> None:
        source = self.root / 'source.wav'
        source.write_bytes(b'original')
        with patch.object(music_analysis, '_resolve_source', return_value=source):
            first = await music_analysis.start(self.request)
            cancelled = await music_analysis.cancel(first.id)
            self.assertEqual(cancelled.status, 'cancelled')
            self.assertFalse(music_analysis.work_busy())
            second = await music_analysis.start(self.request)
            await music_analysis.shutdown()
            self.assertEqual(music_analysis.get(second.id).status, 'cancelled')
            self.assertFalse(music_analysis.work_busy())

    async def test_source_removal_blocks_new_submissions_and_drains_existing_snapshot(self) -> None:
        source = self.root / 'source.wav'
        source.write_bytes(b'original')
        with patch.object(music_analysis, '_resolve_source', return_value=source):
            existing = await music_analysis.start(self.request)
            async with music_analysis.protect_track_removal(1):
                self.assertEqual(music_analysis.get(existing.id).status, 'cancelled')
                with self.assertRaises(music_analysis.MusicAnalysisError) as error:
                    await music_analysis.start(self.request)
                self.assertEqual(error.exception.code, 'analysis_busy')
                self.assertNotIn(1, music_analysis.active_track_ids())
        self.assertEqual(source.read_bytes(), b'original')

    async def test_complete_short_owned_worker_survives_polling_and_reuses_raw_cache(self) -> None:
        source = self.root / 'source.wav'
        with wave.open(str(source), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(bytes(16000))
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        with patch.object(music_analysis, '_resolve_source', return_value=source):
            job = await music_analysis.start(self.request)
            async with asyncio.timeout(15):
                while music_analysis.get(job.id).status in {'queued', 'running'}:
                    await asyncio.sleep(.01)
            finished = music_analysis.get(job.id)
            self.assertEqual(finished.status, 'done', finished.model_dump_json())
            self.assertEqual(finished.result.audio_sha256 if finished.result else None, before)
            with patch.object(music_analysis, '_execute_worker', side_effect=AssertionError('cache reran worker')):
                cached = await music_analysis.start(self.request)
                async with asyncio.timeout(5):
                    while music_analysis.get(cached.id).status in {'queued', 'running'}:
                        await asyncio.sleep(.01)
                self.assertEqual(music_analysis.get(cached.id).status, 'done')
            self.assertFalse(music_analysis.work_busy())
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    async def test_disk_preview_is_read_only_and_refuses_before_snapshot_copy(self) -> None:
        source = self.root / 'source.wav'
        source.write_bytes(b'original')
        probe = b'{"streams":[{"sample_rate":"48000","channels":2,"duration":"10"}]}'
        from subprocess import CompletedProcess
        from collections import namedtuple
        usage = namedtuple('usage', 'total used free')(100, 99, 1)
        with patch.object(music_analysis, '_resolve_source', return_value=source), \
                patch.object(music_analysis, 'run_owned_command', new=AsyncMock(return_value=CompletedProcess([], 0, probe))), \
                patch.object(music_analysis.shutil, 'disk_usage', return_value=usage), \
                patch.object(music_analysis, 'copy_verified', new=AsyncMock()) as copy:
            estimate = await music_analysis.preview(self.request)
            self.assertFalse(estimate.can_start)
            self.assertEqual(estimate.source_bytes, 8)
            self.assertEqual(estimate.retained_bytes, 8)
            self.assertGreater(estimate.required_free_bytes, music_analysis.DISK_RESERVE)
            self.assertEqual(music_analysis.list_jobs(), [])
            job = await music_analysis.start(self.request)
            task = music_analysis._tasks[job.id]
            await task
            self.assertEqual(music_analysis.get(job.id).error_code, 'insufficient_disk')
            copy.assert_not_awaited()

    async def test_setup_admission_blocks_new_work_but_preserves_existing_submission(self) -> None:
        from fastapi import HTTPException
        source = self.root / 'source.wav'
        source.write_bytes(b'original')
        with patch.object(music_analysis, '_resolve_source', return_value=source), \
                patch('app.resource_admission.require_setup_idle', side_effect=HTTPException(409, 'module_setup_busy')):
            with self.assertRaises(HTTPException):
                await music_analysis.start(self.request)
            self.assertEqual(music_analysis.list_jobs(), [])
        gate = asyncio.Semaphore(0)
        with patch.object(music_analysis, '_resolve_source', return_value=source), patch.object(music_analysis, '_semaphore', gate):
            existing = await music_analysis.start(self.request)
            with patch('app.resource_admission.require_setup_idle', side_effect=HTTPException(409, 'module_setup_busy')):
                self.assertEqual((await music_analysis.start(self.request)).id, existing.id)
            await music_analysis.cancel(existing.id)
            with patch('app.resource_admission.require_setup_idle', side_effect=HTTPException(409, 'module_setup_busy')):
                with self.assertRaises(HTTPException):
                    await music_analysis.retry(existing.id)
            self.assertEqual(music_analysis.get(existing.id).status, 'cancelled')

    async def test_retry_refuses_sibling_active_claim_without_changing_either_job(self) -> None:
        source = self.root / 'source.wav'
        source.write_bytes(b'original')
        with patch.object(music_analysis, '_resolve_source', return_value=source), \
                patch.object(music_analysis, '_semaphore', asyncio.Semaphore(0)):
            first = await music_analysis.start(self.request)
            await music_analysis.cancel(first.id)
            second = await music_analysis.start(self.request)
            with self.assertRaises(music_analysis.MusicAnalysisError) as error:
                await music_analysis.retry(first.id)
            self.assertEqual(error.exception.code, 'analysis_busy')
            self.assertEqual(music_analysis.get(first.id).status, 'cancelled')
            self.assertEqual(music_analysis.get(second.id).status, 'queued')
            await music_analysis.cancel(second.id)

    async def test_source_history_filter_runs_before_limit_and_keeps_older_recipes(self) -> None:
        target = MusicAnalysisJob(id='a' * 32, source=self.source, created_at='2026-10-17T00:00:00Z')
        music_analysis._insert_job(target, self.request, 'e' * 64)
        music_analysis._change(target.id, status='cancelled')
        for index in range(101):
            other_source = VersionMusicSource(track_id=index + 2, version_id=f'{index + 1:032x}')
            other = MusicAnalysisJob(id=f'{index + 1:032x}', source=other_source, created_at='2026-10-17T00:00:00Z')
            music_analysis._insert_job(other, MusicAnalysisRequest(source=other_source), f'{index + 1:064x}')
            music_analysis._change(other.id, status='cancelled')
        self.assertEqual(len(music_analysis.list_jobs()), 100)
        self.assertNotIn(target.id, [job.id for job in music_analysis.list_jobs()])
        self.assertEqual([job.id for job in music_analysis.list_jobs(self.source)], [target.id])

    async def test_corrupt_cache_metadata_is_never_reused(self) -> None:
        raw = result(self.source)
        job = MusicAnalysisJob(id='a' * 32, source=self.source, created_at='2026-10-17T00:00:00Z')
        music_analysis._insert_job(job, self.request, 'e' * 64)
        music_analysis._change(job.id, status='running')
        music_analysis._publish(job.id, music_analysis._record(job.id).revision, self.cache_key, raw)
        with sqlite3.connect(self.path) as connection:
            connection.execute('UPDATE music_analysis_results SET source_sha256=?', ('0' * 64,))
        with self.assertRaises(music_analysis.MusicAnalysisError) as error:
            music_analysis._cached(self.cache_key, self.source)
        self.assertEqual(error.exception.code, 'analysis_storage_unavailable')

    async def test_cancel_drains_worker_and_its_sleeping_child_before_releasing_source(self) -> None:
        if sys.platform == 'win32':
            self.skipTest('POSIX process observation; Windows job ownership has dedicated tests')
        from app.video_process import spawn_owned
        from collections.abc import Callable
        source = self.root / 'source.wav'
        with wave.open(str(source), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(bytes(16000))
        marker = self.root / 'child.json'
        script = ('import json, pathlib, subprocess, sys, time; '
            'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
            f'pathlib.Path({str(marker)!r}).write_text(json.dumps({{"pid":child.pid}})); time.sleep(60)')

        async def launch(argv: list[str], *, receipt_path: Path, stdout: int, stderr: int,
                         on_identity: Callable[[music_analysis.WorkerIdentity], None]) -> asyncio.subprocess.Process:
            return await spawn_owned([sys.executable, '-c', script], receipt_path=receipt_path,
                stdout=stdout, stderr=stderr, on_identity=on_identity)

        with patch.object(music_analysis, '_resolve_source', return_value=source), \
                patch.object(music_analysis, 'spawn_owned', side_effect=launch):
            job = await music_analysis.start(self.request)
            async with asyncio.timeout(10):
                while not marker.exists():
                    await asyncio.sleep(.01)
            child = json.loads(marker.read_text())['pid']
            os.kill(child, 0)
            cancelled = await music_analysis.cancel(job.id)
            self.assertEqual(cancelled.status, 'cancelled')
            self.assertFalse(music_analysis.work_busy())
            self.assertNotIn(1, music_analysis.active_track_ids())
            # A reparented zombie is already exited; it cannot read/write media.
            probe = await asyncio.create_subprocess_exec('ps', '-p', str(child), '-o', 'stat=',
                stdout=asyncio.subprocess.PIPE)
            state, _ = await probe.communicate()
            self.assertTrue(not state.strip() or state.strip().startswith(b'Z'), state)
        self.assertEqual(len(source.read_bytes()), 16044)

    async def test_publication_rejects_source_replacement_after_worker_completed(self) -> None:
        source = self.root / 'source.wav'
        with wave.open(str(source), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(bytes(16000))
        before = source.read_bytes()

        async def replaced(identifier: str, request: music_analysis.AnalysisWorkerRequest) -> AnalysisResult:
            source.write_bytes(b'externally replaced')
            return AnalysisResult(id=request.result_id, source=request.source, audio_sha256=request.audio_sha256,
                sample_rate_hz=8000, channels=1, duration_frames=8000, duration_sec=1.0,
                analysis_sample_rate_hz=22050, hop_sec=.1, analyzer_revision=music_analysis.ANALYZER_REVISION,
                configuration_sha256=request.configuration_sha256, created_at=request.created_at,
                loudness=AudioMetrics(duration_sec=1.0, source_sha256=request.audio_sha256))

        with patch.object(music_analysis, '_resolve_source', return_value=source), \
                patch.object(music_analysis, '_execute_worker', side_effect=replaced):
            job = await music_analysis.start(self.request)
            await music_analysis._tasks[job.id]
        failed = music_analysis.get(job.id)
        self.assertEqual((failed.status, failed.error_code, failed.result), ('failed', 'source_changed', None))
        self.assertEqual(source.read_bytes(), b'externally replaced')
        self.assertEqual((music_analysis._workspace(job.id) / 'source.wav').read_bytes(), before)

    async def test_shutdown_closes_admission_until_recovery_and_deadline_covers_snapshot(self) -> None:
        source = self.root / 'source.wav'
        source.write_bytes(b'original')
        with patch.object(music_analysis, '_resolve_source', return_value=source):
            await music_analysis.shutdown()
            with self.assertRaises(music_analysis.MusicAnalysisError):
                await music_analysis.start(self.request)
            await music_analysis.recover()

            async def blocked(identifier: str) -> tuple[Path, str]:
                await asyncio.sleep(60)
                return source, 'c' * 64

            with patch.object(music_analysis, '_snapshot', side_effect=blocked), patch.object(music_analysis, 'JOB_TIMEOUT', .02):
                job = await music_analysis.start(self.request)
                await music_analysis._tasks[job.id]
            self.assertEqual(music_analysis.get(job.id).error_code, 'analysis_timeout')
            await music_analysis.shutdown()
            with self.assertRaises(music_analysis.MusicAnalysisError):
                await music_analysis.retry(job.id)
        self.assertEqual(source.read_bytes(), b'original')

    async def test_completed_private_outcome_survives_crash_before_catalog_publication(self) -> None:
        source = self.root / 'source.wav'
        with wave.open(str(source), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(bytes(16000))
        with patch.object(music_analysis, '_resolve_source', return_value=source):
            first = await music_analysis.start(self.request)
            await music_analysis._tasks[first.id]
            complete = music_analysis.get(first.id)
            self.assertEqual(complete.status, 'done')
            pending = MusicAnalysisJob.model_validate({**complete.model_dump(), 'status': 'running',
                'phase': 'publishing', 'result': None, 'finished_at': None})
            with sqlite3.connect(self.path) as connection:
                connection.execute("UPDATE music_analysis_jobs SET status='running',result_id=NULL,job_json=? WHERE id=?",
                    (pending.model_dump_json(), pending.id))
                connection.execute('DELETE FROM music_analysis_results')
            await music_analysis.recover()
            self.assertEqual(music_analysis.get(first.id).status, 'interrupted')
            with patch.object(music_analysis, '_execute_worker', side_effect=AssertionError('completed work reran')):
                retried = await music_analysis.retry(first.id)
                await music_analysis._tasks[retried.id]
            recovered = music_analysis.get(first.id)
            self.assertEqual(recovered.status, 'done', recovered.model_dump_json())
            self.assertEqual(recovered.result.audio_sha256 if recovered.result else None,
                complete.result.audio_sha256 if complete.result else None)
