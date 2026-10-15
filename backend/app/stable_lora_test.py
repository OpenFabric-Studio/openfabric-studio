from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app import db, stable_lora, music_lora
from app.music_lora_contracts import MusicAdapter, MusicLoraError
from app.stable_lora_contracts import StableLoraSample, StableLoraTrainingRequest, StableLoraTrainingStatus
from app.video_process import WorkerIdentity


def checkpoint_bytes() -> bytes:
    header = json.dumps({'__metadata__': {'lora_config': json.dumps({'rank': 8, 'alpha': 8, 'adapter_type': 'lora', 'step': 3, 'base_model': 'sa3-sm-music'})},
        'model.transformer.layers.0.self_attn.to_qkv.parametrizations.weight.0.lora_A': {'dtype': 'F32', 'shape': [8, 1024], 'data_offsets': [0, 32768]},
        'model.transformer.layers.0.self_attn.to_qkv.parametrizations.weight.0.lora_B': {'dtype': 'F32', 'shape': [3072, 8], 'data_offsets': [32768, 131072]}}).encode()
    return struct.pack('<Q', len(header)) + header + bytes(131072)


class StableLoraContractTests(unittest.TestCase):
    def test_legacy_job_defaults_and_bounded_training_rate(self) -> None:
        from app.stable_lora_contracts import StableLoraProgress, StableLoraTrainingJob
        job = StableLoraTrainingJob.model_validate({'id': 'a' * 32, 'status': 'running', 'stage': 'training',
            'created_at': '2026-10-15T00:00:00Z', 'request': {'name': 'Test', 'samples': [{'track_id': 1, 'caption': 'Jazz'}]}})
        self.assertIsNone(job.started_at)
        self.assertIsNone(job.finished_at)
        self.assertEqual(job.progress.stage, 'training')
        self.assertEqual(StableLoraProgress(stage='training', seconds_per_step=12.5).seconds_per_step, 12.5)
        for value in (0, -1, float('inf'), float('nan'), True, 86401):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                StableLoraProgress(stage='training', seconds_per_step=value)

    def test_training_bounds_and_duplicate_tracks_are_checked(self) -> None:
        body = {'name': 'Test', 'samples': [{'track_id': 1, 'caption': 'Jazz'}]}
        for change in ({'name': ' '}, {'samples': []}, {'samples': [{'track_id': True, 'caption': 'Jazz'}]},
                       {'samples': [{'track_id': 1, 'caption': ' '}]}, {'max_steps': 0}, {'max_steps': True},
                       {'rank': 64}, {'learning_rate': float('nan')}, {'learning_rate': True}, {'seed': -1},
                       {'samples': [{'track_id': 1, 'caption': 'A'}, {'track_id': 1, 'caption': 'B'}]}, {'resume': True}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                StableLoraTrainingRequest.model_validate({**body, **change})


class StableLoraJobsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.adapter: MusicAdapter | None = None
        status = StableLoraTrainingStatus(supported=True, installed=True, small_weights_ready=True, medium_weights_ready=True)
        self.patches = [patch.object(db, '_db', None), patch.object(db, 'DATA_DIR', self.root),
            patch.object(db, 'DB_PATH', self.root / 'catalog.db'), patch.object(db, 'FILES_DIR', self.root / 'files'),
            patch.object(stable_lora, '_tasks', {}), patch.object(stable_lora, '_unverified', set()), patch.object(stable_lora, '_closing', False),
            patch.object(stable_lora, 'status', return_value=status),
            patch.object(stable_lora, 'register_file', side_effect=self.register), patch.object(stable_lora, 'find_by_job', side_effect=lambda _identifier: self.adapter),
            patch('app.work_busy.other_work_busy', new=AsyncMock(return_value=False)), patch('app.video_jobs.work_busy', return_value=False),
            patch('app.resource_admission.native_work_inflight', return_value=False)]
        for item in self.patches:
            item.start()
        self.connection = db.get_db()
        stable_lora.migrate(self.connection)
        self.source = db.FILES_DIR / 'upload/source.wav'
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b'preserve original')
        self.track_id = db.insert_track(model='upload', title='Source', lyrics='', seed=None, duration_ms=1000, wall_ms=None, params={}, audio_path=self.source, abc_path=None)
        self.worker = self.root / 'fake_worker.py'
        self.worker.write_text('import hashlib,json,sys\nfrom pathlib import Path\n'
            'run=Path(sys.argv[1]);spec=json.loads((run/"request.json").read_text())\n'
            f'data={checkpoint_bytes()!r}\n(run/"final.safetensors").write_bytes(data)\n'
            '(run/"dataset.json").write_text("{}")\n'
            'manifest=hashlib.sha256((run/"dataset.json").read_bytes()).hexdigest()\n'
            '(run/"completion.json").write_text(json.dumps({"job_id":run.name,"request_sha256":spec["request_sha256"],"checkpoint":"final.safetensors","sha256":hashlib.sha256(data).hexdigest(),"dataset_sha256":manifest,"variant":spec["request"]["variant"],"max_steps":spec["request"]["max_steps"]}))\n')

    async def asyncTearDown(self) -> None:
        await stable_lora.shutdown()
        self.connection.close()
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def body(self) -> StableLoraTrainingRequest:
        return StableLoraTrainingRequest(name='Test adapter', samples=[StableLoraSample(track_id=self.track_id, caption='Jazz')], rank=8, max_steps=3)

    def command(self, request: StableLoraTrainingRequest, run: Path, sources: list[Path]) -> tuple[list[str], Path, dict[str, str]]:
        run.mkdir(parents=True, exist_ok=True)
        (run / 'request.json').write_text(json.dumps({'request': request.model_dump(), 'request_sha256': stable_lora.fingerprint(request)}))
        return [sys.executable, str(self.worker), str(run)], run, dict(os.environ)

    def register(self, path: Path, *, name: str, engine: str, target: str, source_job_id: str | None = None) -> MusicAdapter:
        self.adapter = MusicAdapter(id='a' * 32, engine='stable_audio3', target='small-music', name=name,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), created_at='2026-10-14', source_job_id=source_job_id)
        return self.adapter

    async def finish(self) -> str:
        job = await stable_lora.submit(self.body())
        await stable_lora._tasks[job.id]
        return job.id

    async def test_completed_native_checkpoint_is_registered_once_and_sources_are_immutable(self) -> None:
        with patch.object(stable_lora, 'prepare_worker', side_effect=self.command):
            identifier = await self.finish()
        job = stable_lora.get(identifier)
        self.assertEqual(job.status, 'done')
        self.assertIsNotNone(job.adapter)
        self.assertEqual(self.source.read_bytes(), b'preserve original')
        await stable_lora.recover()
        self.assertEqual(stable_lora.get(identifier).adapter, self.adapter)

    async def test_native_progress_retains_latest_valid_step_across_polling_and_reload(self) -> None:
        from app.stable_lora_contracts import StableLoraTrainingJob
        identifier = 'b' * 32
        directory = stable_lora._directory(identifier, create=True)
        job = StableLoraTrainingJob(id=identifier, status='running', stage='training',
            created_at='2026-10-15T00:00:00Z', request=self.body())
        self.connection.execute('INSERT INTO stable_lora_jobs(id,payload_json,created_at) VALUES(?,?,?)',
            (identifier, job.model_dump_json(), job.created_at))
        self.connection.commit()
        (directory / 'stage.json').write_text('{"stage":"training"}')
        log = directory / 'worker.log'
        # Large logs, carriage-return bars, and reversed/bogus observations are native boundary inputs.
        log.write_bytes(b'x' * (256 * 1024) + b'\rStep 1, Epoch 0: 100%|#| 1/1 [00:20<00:00, 20.00s/it, train/loss=0.8]\r'
            b'Step 2, Epoch 1: 100%|#| 1/1 [00:04<00:00, 2.00it/s, train/loss=0.7]\r'
            b'Step 1, Epoch 0: 100%|#| 1/1 [00:20<00:00, 20.00s/it, train/loss=0.9]\r'
            b'Step 4, Epoch 2: 100%|#| 1/1 [00:20<00:00, 20.00s/it, train/loss=0.6]\r'
            b'Step 3, Epoch 2: 100%|#| 1/1 [00:20<00:00, 20.00s/it, train/loss=nan]\r'
            b'Step 3, Epoch 2: 100%|#| 1/1 [00:20<00:00, 0it/s, train/loss=0.2]\r'
            b'Step 3, Epoch 2: 100%|#| 1/1 [00:20<00:00, 1e99s/it, train/loss=0.2]\r'
            b'Step 3, Epoch 2: 100%|#| 1/1 [00:20<00:00, 10s/it, train/loss=inf]\r')
        os.utime(log, (1700000000, 1700000000))
        observed = stable_lora.get(identifier)
        self.assertEqual(observed.progress.step, 2)
        self.assertEqual(observed.progress.total_steps, 3)
        self.assertEqual(observed.progress.loss, .7)
        self.assertEqual(observed.progress.seconds_per_step, .5)
        with log.open('ab') as handle:
            handle.write(b'\n/private/path unrelated diagnostic\n')
        os.utime(log, (1700000100, 1700000100))
        self.assertEqual(stable_lora.get(identifier).progress.updated_at, observed.progress.updated_at)
        stored = self.connection.execute('SELECT payload_json FROM stable_lora_jobs WHERE id=?', (identifier,)).fetchone()
        persisted = StableLoraTrainingJob.model_validate_json(stored['payload_json'])
        self.assertEqual(persisted.progress, observed.progress)
        self.assertNotIn('/private/path', observed.model_dump_json())
        legacy = job.model_dump(exclude={'progress', 'started_at', 'finished_at'})
        legacy['status'] = 'failed'
        self.connection.execute('UPDATE stable_lora_jobs SET payload_json=? WHERE id=?', (json.dumps(legacy), identifier))
        self.connection.commit()
        recovered = stable_lora.get(identifier)
        self.assertEqual(recovered.progress.step, 2)
        self.assertEqual(recovered.progress.loss, .7)
        self.assertIsNone(recovered.finished_at)

    async def test_completed_run_persists_native_metrics_and_timing_without_polling(self) -> None:
        with self.worker.open('a') as worker:
            worker.write('print("Step 3, Epoch 2: 100%|#| 1/1 [00:05<00:00, 5.00s/it, train/loss=0.25]", flush=True)\n')
        with patch.object(stable_lora, 'prepare_worker', side_effect=self.command):
            identifier = await self.finish()
        job = stable_lora.get(identifier)
        self.assertEqual(job.status, 'done')
        self.assertEqual(job.progress.stage, 'done')
        self.assertEqual(job.progress.step, 3)
        self.assertEqual(job.progress.loss, .25)
        self.assertEqual(job.progress.seconds_per_step, 5)
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)
        self.assertEqual(stable_lora.get(identifier).finished_at, job.finished_at)

    async def test_native_initial_bar_marks_training_without_inventing_progress(self) -> None:
        from app.stable_lora_contracts import StableLoraTrainingJob
        identifier = 'b' * 32
        directory = stable_lora._directory(identifier, create=True)
        job = StableLoraTrainingJob(id=identifier, status='running', stage='loading',
            created_at='2026-10-15T00:00:00Z', request=self.body())
        self.connection.execute('INSERT INTO stable_lora_jobs(id,payload_json,created_at) VALUES(?,?,?)',
            (identifier, job.model_dump_json(), job.created_at))
        self.connection.commit()
        (directory / 'stage.json').write_text('{"stage":"loading"}')
        log = directory / 'worker.log'
        log.write_bytes(b'\rStep 0, Epoch 0:   0%|          | 0/1 [00:00<?, ?it/s]')
        observed = stable_lora.get(identifier)
        self.assertEqual(observed.stage, 'training')
        self.assertEqual(observed.progress.stage, 'training')
        self.assertIsNone(observed.progress.step)
        self.assertIsNone(observed.progress.loss)
        self.assertIsNone(observed.progress.seconds_per_step)
        with log.open('ab') as handle:
            handle.write(b'\nUnrelated diagnostic\n')
        self.assertEqual(stable_lora.get(identifier).progress.updated_at, observed.progress.updated_at)

    async def test_failed_run_persists_final_progress_without_polling(self) -> None:
        self.worker.write_text('import sys\nfrom pathlib import Path\n(Path(sys.argv[1])/"stage.json").write_text("invalid")\n'
            'print("Step 2, Epoch 1: 100%|#| 1/1 [00:05<00:00, 5.00s/it, train/loss=0.4]", flush=True)\nraise SystemExit(1)\n')
        with patch.object(stable_lora, 'prepare_worker', side_effect=self.command):
            identifier = await self.finish()
        job = stable_lora.get(identifier)
        self.assertEqual(job.status, 'failed')
        self.assertEqual(job.progress.step, 2)
        self.assertEqual(job.progress.loss, .4)
        self.assertIsNotNone(job.finished_at)

    async def test_final_checkpoint_registers_in_real_managed_adapter_library(self) -> None:
        with patch.object(stable_lora, 'prepare_worker', side_effect=self.command), \
             patch.object(stable_lora, 'register_file', side_effect=music_lora.register_file), \
             patch.object(stable_lora, 'find_by_job', side_effect=music_lora.find_by_job):
            identifier = await self.finish()
            job = stable_lora.get(identifier)
            self.assertEqual(job.status, 'done')
            self.assertIsNotNone(job.adapter)
            if job.adapter is None:
                self.fail('Expected registered adapter')
            self.assertEqual(music_lora.get(job.adapter.id).source_job_id, identifier)
            await stable_lora.recover()
            self.assertEqual(len(music_lora.list_adapters().adapters), 1)

    async def test_partial_checkpoint_without_completion_is_never_imported(self) -> None:
        self.worker.write_text(f'import sys\nfrom pathlib import Path\n(Path(sys.argv[1])/"partial.safetensors").write_bytes({checkpoint_bytes()!r})\n')
        with patch.object(stable_lora, 'prepare_worker', side_effect=self.command):
            identifier = await self.finish()
        self.assertEqual(stable_lora.get(identifier).status, 'failed')
        await stable_lora.recover()
        self.assertIsNone(self.adapter)
        self.assertTrue((stable_lora._directory(identifier) / 'partial.safetensors').is_file())

    async def test_recovery_retries_publication_without_rerunning_or_duplicate_import(self) -> None:
        with patch.object(stable_lora, 'prepare_worker', side_effect=self.command), patch.object(stable_lora, 'register_file', side_effect=OSError('interrupted publication')):
            identifier = await self.finish()
        self.assertEqual(stable_lora.get(identifier).status, 'failed')
        with patch.object(stable_lora, 'prepare_worker') as prepare:
            await stable_lora.recover()
            await stable_lora.recover()
        prepare.assert_not_called()
        self.assertEqual(stable_lora.get(identifier).status, 'done')

    async def test_changed_checkpoint_or_manifest_cannot_be_registered_during_recovery(self) -> None:
        with patch.object(stable_lora, 'prepare_worker', side_effect=self.command), patch.object(stable_lora, 'register_file', side_effect=OSError('interrupted publication')):
            identifier = await self.finish()
        (stable_lora._directory(identifier) / 'final.safetensors').write_bytes(checkpoint_bytes() + b'changed')
        await stable_lora.recover()
        self.assertIsNone(self.adapter)
        self.assertEqual(stable_lora.get(identifier).status, 'failed')

    async def test_cancel_before_start_does_not_launch_and_shutdown_prevents_submission(self) -> None:
        with patch.object(stable_lora, 'prepare_worker') as prepare:
            job = await stable_lora.submit(self.body())
            await stable_lora.cancel(job.id)
        prepare.assert_not_called()
        self.assertEqual(stable_lora.get(job.id).status, 'cancelled')
        await stable_lora.shutdown()
        with self.assertRaises(MusicLoraError):
            await stable_lora.submit(self.body())

    async def test_missing_or_escaped_track_source_refused_before_job_registration(self) -> None:
        outside = self.root / 'outside.wav'
        outside.write_bytes(b'preserve outside')
        self.source.unlink()
        self.source.symlink_to(outside)
        with self.assertRaises(MusicLoraError):
            await stable_lora.submit(self.body())
        self.assertEqual(stable_lora.list_jobs().jobs, [])
        self.assertEqual(outside.read_bytes(), b'preserve outside')

    async def test_other_training_job_blocks_registration(self) -> None:
        with patch.object(stable_lora, 'prepare_worker', side_effect=self.command):
            job = await stable_lora.submit(self.body())
            with self.assertRaises(MusicLoraError):
                await stable_lora.submit(self.body())
            await stable_lora.cancel(job.id)

    async def test_cancel_drains_descendants_and_keeps_checkpoint_artifacts(self) -> None:
        started = self.root / 'started'
        descendant = self.root / 'descendant.pid'
        self.worker.write_text('import subprocess,sys,time\nfrom pathlib import Path\n'
            f'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);Path({str(descendant)!r}).write_text(str(child.pid))\n'
            'run=Path(sys.argv[1]);(run/"audio").mkdir();(run/"audio/000.partial.wav").write_bytes(b"partial")\n'
            f'(run/"retained.safetensors").write_bytes({checkpoint_bytes()!r})\n'
            f'Path({str(started)!r}).write_text("ready")\ntime.sleep(60)\n')
        with patch.object(stable_lora, 'prepare_worker', side_effect=self.command):
            job = await stable_lora.submit(self.body())
            async with asyncio.timeout(10):
                while not started.is_file():
                    await asyncio.sleep(.01)
            await stable_lora.cancel(job.id)
        self.assertEqual(stable_lora.get(job.id).status, 'cancelled')
        self.assertFalse(stable_lora.work_busy())
        self.assertTrue((stable_lora._directory(job.id) / 'retained.safetensors').is_file())
        self.assertFalse((stable_lora._directory(job.id) / 'audio/000.partial.wav').exists())
        if sys.platform != 'win32':
            async with asyncio.timeout(5):
                while True:
                    proc = await asyncio.create_subprocess_exec('ps', '-o', 'stat=', '-p', descendant.read_text(), stdout=asyncio.subprocess.PIPE)
                    output, _ = await proc.communicate()
                    if not output.strip() or output.strip().startswith(b'Z'):
                        break
                    await asyncio.sleep(.02)

    async def test_invalid_worker_telemetry_cannot_prevent_cancellation_or_shutdown(self) -> None:
        started = self.root / 'started'
        self.worker.write_text('import sys,time\nfrom pathlib import Path\n'
            '(Path(sys.argv[1])/"stage.json").write_text("invalid progress")\n'
            'print("Step 2, Epoch 1: 100%|#| 1/1 [00:05<00:00, 5.00s/it, train/loss=0.4]", flush=True)\n'
            f'Path({str(started)!r}).write_text("ready")\ntime.sleep(60)\n')
        with patch.object(stable_lora, 'prepare_worker', side_effect=self.command):
            job = await stable_lora.submit(self.body())
            async with asyncio.timeout(10):
                while not started.is_file():
                    await asyncio.sleep(.01)
            try:
                await stable_lora.cancel(job.id)
                self.assertEqual(stable_lora.get(job.id).status, 'cancelled')
                self.assertEqual(stable_lora.get(job.id).progress.step, 2)
                self.assertIsNotNone(stable_lora.get(job.id).finished_at)
                self.assertFalse(stable_lora.work_busy())
            finally:
                from app.job_lifecycle import cancel_and_wait
                await cancel_and_wait(stable_lora._tasks.get(job.id))

    async def test_completed_history_reads_do_not_rescan_the_adapter_library(self) -> None:
        with patch.object(stable_lora, 'prepare_worker', side_effect=self.command):
            identifier = await self.finish()
        with patch.object(stable_lora, 'find_by_job') as find:
            self.assertEqual(stable_lora.get(identifier).status, 'done')
            self.assertEqual(stable_lora.list_jobs().jobs[0].status, 'done')
        find.assert_not_called()

    async def test_recovery_changed_checkpoint_marks_running_record_failed(self) -> None:
        with patch.object(stable_lora, 'prepare_worker', side_effect=self.command), patch.object(stable_lora, 'register_file', side_effect=OSError('interrupted publication')):
            identifier = await self.finish()
        job = stable_lora.get(identifier)
        job.status = 'running'
        stable_lora._store(job)
        (stable_lora._directory(identifier) / 'final.safetensors').write_bytes(checkpoint_bytes() + b'changed')
        await stable_lora.recover()
        self.assertEqual(stable_lora.get(identifier).status, 'failed')
        self.assertEqual(stable_lora.get(identifier).error_code, 'stable_lora_checkpoint_changed')

    async def test_corrupt_record_preserves_other_history_and_does_not_abort_recovery(self) -> None:
        with patch.object(stable_lora, 'prepare_worker', side_effect=self.command):
            identifier = await self.finish()
        self.connection.execute('INSERT INTO stable_lora_jobs(id,payload_json,created_at) VALUES(?,?,?)', ('c' * 32, 'invalid', '2026-01-01'))
        self.connection.commit()
        await stable_lora.recover()
        response = stable_lora.list_jobs()
        self.assertTrue(response.history_incomplete)
        self.assertEqual([job.id for job in response.jobs], [identifier])

    async def test_unknown_worker_remains_quarantined_after_recovery(self) -> None:
        with patch.object(stable_lora, 'prepare_worker', side_effect=self.command):
            job = await stable_lora.submit(self.body())
            await stable_lora.cancel(job.id)
        identity = WorkerIdentity(pid=12345, token='a' * 32, receipt=str(stable_lora._directory(job.id) / 'worker.json'))
        stable_lora._store_identity(job.id, identity)
        with patch.object(stable_lora, 'terminate_verified', new=AsyncMock(return_value=False)):
            await stable_lora.recover()
        self.assertTrue(stable_lora.work_busy())
        self.assertEqual(stable_lora.get(job.id).error_code, 'stable_lora_worker_unverified')
        with self.assertRaises(MusicLoraError):
            await stable_lora.submit(self.body())
