from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app import db, music_training, music_lora, module_catalog
from app.music_lora_contracts import MusicAdapter, MusicLoraError
from app.music_training_contracts import MusicLoraTrainingSample, MusicLoraTrainingRequest, MusicLoraTrainingStatus, MusicLoraTrainingJob, YueLoraPairSettings
from app.video_process import WorkerIdentity


BASE_SHA = '33765adbf9813c9a50318218760b2fd819a319862460a04884607581961c6fee'


def checkpoint_bytes() -> bytes:
    header = json.dumps({'__metadata__': {'openfabric_yue2_conversion': json.dumps({'base_sha256': BASE_SHA})},
        'layers.0.nar_self_attn.q_proj.lora_A': {'dtype': 'F32', 'shape': [8, 2048], 'data_offsets': [0, 65536]},
        'layers.0.nar_self_attn.q_proj.lora_B': {'dtype': 'F32', 'shape': [2048, 8], 'data_offsets': [65536, 131072]}}).encode()
    return struct.pack('<Q', len(header)) + header + bytes(131072)


def paired_checkpoint(target: str, *, stage: str | None = None, base: str = BASE_SHA) -> bytes:
    data = checkpoint_bytes()
    length = struct.unpack('<Q', data[:8])[0]
    header = json.loads(data[8:8 + length].replace(b'nar_', b'' if target == 'ar' else b'nar_'))
    header['__metadata__']['openfabric_yue2_conversion'] = json.dumps({'base_sha256': base, 'stage': stage or target})
    encoded = json.dumps(header).encode()
    return struct.pack('<Q', len(encoded)) + encoded + data[8 + length:]


class MusicTrainingJobsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.adapter: MusicAdapter | None = None
        status = MusicLoraTrainingStatus(engine='yue2', supported=True, installed=True, weights_ready=True, paired_ready=True, device='mps', validation='verified')
        self.patches = [patch.object(db, '_db', None), patch.object(db, 'DATA_DIR', self.root),
            patch.object(db, 'DB_PATH', self.root / 'catalog.db'), patch.object(db, 'FILES_DIR', self.root / 'files'),
            patch.object(music_training, '_tasks', {}), patch.object(music_training, '_unverified', set()), patch.object(music_training, '_closing', False),
            patch.object(music_training, 'status', return_value=status),
            patch.object(music_training, 'register_file', side_effect=self.register), patch.object(music_training, 'find_by_job', side_effect=lambda _identifier: self.adapter),
            patch('app.work_busy.other_work_busy', new=AsyncMock(return_value=False)), patch('app.video_jobs.work_busy', return_value=False),
            patch('app.resource_admission.native_work_inflight', return_value=False)]
        for item in self.patches:
            item.start()
        self.connection = db.get_db()
        music_training.migrate(self.connection)
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
            '(run/"completion.json").write_text(json.dumps({"job_id":run.name,"request_sha256":spec["request_sha256"],"checkpoint":"final.safetensors","sha256":hashlib.sha256(data).hexdigest(),"dataset_sha256":manifest,"engine":spec["request"]["engine"],"base_sha256":"33765adbf9813c9a50318218760b2fd819a319862460a04884607581961c6fee","max_steps":spec["request"]["max_steps"]}))\n')

    async def asyncTearDown(self) -> None:
        await music_training.shutdown()
        self.connection.close()
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def body(self) -> MusicLoraTrainingRequest:
        return MusicLoraTrainingRequest(engine='yue2', name='Test adapter', samples=[MusicLoraTrainingSample(track_id=self.track_id, caption='Jazz')], rank=8, max_steps=3)

    def pair_body(self) -> MusicLoraTrainingRequest:
        return self.body().model_copy(update={'yue_pair': YueLoraPairSettings(planner_steps=2)})

    async def test_removed_engine_history_is_ignored_without_mutating_saved_records(self) -> None:
        identifier = 'c' * 32
        directory = music_training._directory(identifier, create=True)
        sentinel = directory / '.music-training-package-original'
        sentinel.mkdir()
        (sentinel / 'keep.txt').write_text('retained original')
        saved = {'id': identifier, 'status': 'running', 'created_at': '2026-10-15',
            'request': {'engine': 'heartmula', 'name': 'Retired training', 'samples': [{'track_id': 1, 'caption': 'Piano'}]}}
        payload = json.dumps(saved)
        self.connection.execute('INSERT INTO music_training_jobs(id,payload_json,created_at) VALUES(?,?,?)',
            (identifier, payload, saved['created_at']))
        self.connection.commit()
        self.assertEqual(music_training.list_jobs().jobs, [])
        self.assertFalse(music_training.list_jobs().history_incomplete)
        self.assertFalse(music_training._planner_child_exists('d' * 32))
        with self.assertRaises(MusicLoraError) as caught:
            music_training.get(identifier)
        self.assertEqual(caught.exception.code, 'music_training_job_not_found')
        await music_training.recover()
        self.assertFalse(music_training.work_busy())
        self.assertEqual(self.connection.execute('SELECT payload_json FROM music_training_jobs WHERE id=?', (identifier,)).fetchone()['payload_json'], payload)
        self.assertEqual((sentinel / 'keep.txt').read_text(), 'retained original')

    async def test_retired_jobs_do_not_consume_the_supported_history_limit(self) -> None:
        payload = json.dumps({'request': {'engine': 'heartmula'}})
        self.connection.executemany('INSERT INTO music_training_jobs(id,payload_json,created_at) VALUES(?,?,?)',
            [(f'{index:032x}', payload, '2026-10-16') for index in range(1000)])
        supported = MusicLoraTrainingJob(id='f' * 32, status='cancelled', created_at='2026-10-15', request=self.body())
        self.connection.execute('INSERT INTO music_training_jobs(id,payload_json,created_at) VALUES(?,?,?)',
            (supported.id, supported.model_dump_json(), supported.created_at))
        self.connection.commit()
        history = music_training.list_jobs()
        self.assertEqual(history.jobs, [supported])
        self.assertFalse(history.history_incomplete)

    async def test_retired_worker_blocks_admission_until_verified_termination_without_mutating_artifacts(self) -> None:
        identifier = 'e' * 32
        directory = music_training._directory(identifier, create=True)
        artifact = directory / 'completion.json'
        artifact.write_bytes(b'retained original completion')
        payload = json.dumps({'id': identifier, 'status': 'running', 'request': {'engine': 'heartmula'}})
        identity = WorkerIdentity(pid=12345, token='a' * 32, receipt=str(directory / 'worker.json'))
        self.connection.execute('INSERT INTO music_training_jobs(id,payload_json,worker_json,created_at) VALUES(?,?,?,?)',
            (identifier, payload, identity.model_dump_json(), '2026-10-15'))
        self.connection.commit()
        with patch.object(music_training, 'prepare_worker') as prepare, patch.object(music_training, '_publish') as publish, \
                patch.object(music_training, '_cleanup') as cleanup:
            with patch.object(music_training, 'terminate_verified', new=AsyncMock(return_value=False)) as terminate:
                await music_training.recover()
                terminate.assert_awaited_once_with(identity)
            self.assertTrue(music_training.work_busy())
            with self.assertRaises(MusicLoraError) as caught:
                await music_training.submit(self.body())
            self.assertEqual(caught.exception.code, 'music_training_busy')
            retained = self.connection.execute('SELECT payload_json,worker_json FROM music_training_jobs WHERE id=?', (identifier,)).fetchone()
            self.assertEqual((retained['payload_json'], retained['worker_json']), (payload, identity.model_dump_json()))
            with patch.object(music_training, 'terminate_verified', new=AsyncMock(return_value=True)) as terminate:
                await music_training.recover()
                terminate.assert_awaited_once_with(identity)
            prepare.assert_not_called()
            publish.assert_not_called()
            cleanup.assert_not_called()
        self.assertFalse(music_training.work_busy())
        retained = self.connection.execute('SELECT payload_json,worker_json FROM music_training_jobs WHERE id=?', (identifier,)).fetchone()
        self.assertEqual((retained['payload_json'], retained['worker_json']), (payload, None))
        self.assertEqual(artifact.read_bytes(), b'retained original completion')

    def pair_job(self) -> MusicLoraTrainingJob:
        identifier = 'd' * 32
        directory = music_training._directory(identifier, create=True)
        job = MusicLoraTrainingJob(id=identifier, status='running', created_at='2026-10-16', request=self.pair_body())
        self.connection.execute('INSERT INTO music_training_jobs(id,payload_json,created_at) VALUES(?,?,?)',
            (job.id, job.model_dump_json(), job.created_at))
        self.connection.commit()
        (directory / 'dataset.json').write_text('{}')
        artifacts = []
        for target in ('ar', 'nar'):
            data = paired_checkpoint(target)
            (directory / (target + '.safetensors')).write_bytes(data)
            artifacts.append({'target': target, 'checkpoint': target + '.safetensors', 'sha256': hashlib.sha256(data).hexdigest()})
        (directory / 'completion.json').write_text(json.dumps({'job_id': job.id, 'engine': 'yue2',
            'request_sha256': music_training.fingerprint(job.request), 'dataset_sha256': hashlib.sha256(b'{}').hexdigest(),
            'base_sha256': BASE_SHA, 'max_steps': 3, 'planner_steps': 2, 'artifacts': artifacts}))
        return job

    def pair_worker(self) -> None:
        self.worker.write_text('import hashlib,json,sys\nfrom pathlib import Path\n'
            'run=Path(sys.argv[1]);spec=json.loads((run/"request.json").read_text());artifacts=[]\n'
            f'data={ {target: paired_checkpoint(target) for target in ("ar", "nar")}!r}\n'
            'for target,content in data.items():\n'
            ' (run/(target+".safetensors")).write_bytes(content);artifacts.append({"target":target,"checkpoint":target+".safetensors","sha256":hashlib.sha256(content).hexdigest()})\n'
            '(run/"dataset.json").write_text("{}")\n'
            f'(run/"completion.json").write_text(json.dumps({{"job_id":run.name,"engine":"yue2","request_sha256":spec["request_sha256"],"dataset_sha256":hashlib.sha256(b"{{}}").hexdigest(),"base_sha256":{BASE_SHA!r},"max_steps":3,"planner_steps":2,"artifacts":artifacts}}))\n')

    async def test_pair_publication_recovers_only_the_missing_target_after_interruption(self) -> None:
        job = self.pair_job()
        registered: list[str] = []
        def interrupt(path: Path, *, name: str, engine: str, target: str, source_job_id: str | None = None) -> MusicAdapter:
            registered.append(target)
            if target == 'nar':
                raise OSError('interrupted second publication')
            return music_lora.register_file(path, name=name, engine=engine, target=target, source_job_id=source_job_id)
        with patch.object(music_training, 'register_file', side_effect=interrupt), \
                patch.object(music_training, 'find_by_job', side_effect=music_lora.find_by_job):
            with self.assertRaises(OSError):
                music_training._publish(job)
        self.assertEqual(registered, ['ar', 'nar'])
        self.assertEqual([adapter.target for adapter in music_lora.list_adapters().adapters], ['ar'])
        job.status = 'failed'
        music_training._store(job)
        with patch.object(music_training, 'register_file', side_effect=music_lora.register_file), \
                patch.object(music_training, 'find_by_job', side_effect=music_lora.find_by_job), patch.object(music_training, 'prepare_worker') as prepare:
            await music_training.recover()
            await music_training.recover()
        prepare.assert_not_called()
        result = music_training.get(job.id)
        self.assertEqual(result.status, 'done')
        self.assertEqual({adapter.target for adapter in result.adapters}, {'ar', 'nar'})
        self.assertEqual(result.adapter, next(adapter for adapter in result.adapters if adapter.target == 'nar'))
        self.assertEqual(result.progress.target, 'nar')
        self.assertEqual(len(music_lora.list_adapters().adapters), 2)

    async def test_pair_validates_both_files_and_target_metadata_before_any_registration(self) -> None:
        job = self.pair_job()
        directory = music_training._directory(job.id)
        receipt_path = directory / 'completion.json'
        receipt = json.loads(receipt_path.read_bytes())
        for kind in ('missing', 'duplicate', 'wrong-hash', 'swapped', 'wrong-stage', 'wrong-base', 'invalid-native', 'truncated-header', 'wrong-planner'):
            changed = json.loads(json.dumps(receipt))
            for target in ('ar', 'nar'):
                (directory / (target + '.safetensors')).write_bytes(paired_checkpoint(target))
            if kind == 'missing':
                (directory / 'nar.safetensors').unlink()
            elif kind == 'duplicate':
                changed['artifacts'][1] = changed['artifacts'][0]
            elif kind == 'wrong-hash':
                changed['artifacts'][1]['sha256'] = '0' * 64
            elif kind == 'swapped':
                for item in changed['artifacts']:
                    item['target'] = 'nar' if item['target'] == 'ar' else 'ar'
            elif kind in ('wrong-stage', 'wrong-base', 'invalid-native', 'truncated-header'):
                content = (paired_checkpoint('nar', stage='ar') if kind == 'wrong-stage' else
                    paired_checkpoint('nar', base='0' * 64) if kind == 'wrong-base' else b'123' if kind == 'truncated-header' else
                    paired_checkpoint('ar', stage='nar'))
                (directory / 'nar.safetensors').write_bytes(content)
                changed['artifacts'][1]['sha256'] = hashlib.sha256(content).hexdigest()
            else:
                changed['planner_steps'] = 3
            receipt_path.write_text(json.dumps(changed))
            with self.subTest(kind=kind), patch.object(music_training, 'register_file') as register:
                with self.assertRaises((MusicLoraError, ValidationError, OSError)):
                    music_training._publish(job)
                register.assert_not_called()

    async def test_pair_progress_resets_once_for_nar_and_survives_reload(self) -> None:
        job = self.pair_job()
        path = music_training._directory(job.id) / 'stage.json'
        path.write_text(json.dumps({'stage': 'training', 'step': 1, 'total_steps': 3}))
        self.assertEqual(music_training.get(job.id).progress.stage, 'preparing')
        path.write_text(json.dumps({'stage': 'training', 'target': 'ar', 'step': 2, 'total_steps': 2}))
        self.assertEqual(music_training.get(job.id).progress.target, 'ar')
        path.write_text(json.dumps({'stage': 'loading', 'target': 'nar', 'step': None, 'total_steps': 3}))
        self.assertEqual(music_training.get(job.id).progress.target, 'nar')
        path.write_text(json.dumps({'stage': 'training', 'target': 'nar', 'step': 1, 'total_steps': 3}))
        observed = music_training.get(job.id).progress
        self.assertEqual(observed.step, 1)
        for report in ({'stage': 'training', 'target': 'ar', 'step': 2, 'total_steps': 2},
                {'stage': 'training', 'target': 'nar', 'step': 0, 'total_steps': 3},
                {'stage': 'training', 'target': None, 'step': 2, 'total_steps': 3},
                {'stage': 'training', 'target': 'nar', 'step': 2, 'total_steps': 2}):
            path.write_text(json.dumps(report))
            self.assertEqual(music_training.get(job.id).progress, observed)
        saved = self.connection.execute('SELECT payload_json FROM music_training_jobs WHERE id=?', (job.id,)).fetchone()
        self.assertEqual(MusicLoraTrainingJob.model_validate_json(saved['payload_json']).progress, observed)

    async def test_pair_failed_publication_keeps_the_first_managed_adapter_link(self) -> None:
        self.pair_worker()
        def interrupt(path: Path, *, name: str, engine: str, target: str, source_job_id: str | None = None) -> MusicAdapter:
            if target == 'nar':
                raise OSError('interrupted second publication')
            return music_lora.register_file(path, name=name, engine=engine, target=target, source_job_id=source_job_id)
        with patch.object(music_training, 'prepare_worker', side_effect=self.command), \
                patch.object(music_training, 'register_file', side_effect=interrupt), \
                patch.object(music_training, 'find_by_job', side_effect=music_lora.find_by_job):
            job = await music_training.submit(self.pair_body())
            await music_training._tasks[job.id]
        result = music_training.get(job.id)
        self.assertEqual(result.status, 'failed')
        self.assertEqual({adapter.target for adapter in result.adapters}, {'ar'})
        self.assertIsNone(result.adapter)

    async def test_recovery_interruption_keeps_the_first_managed_adapter_link(self) -> None:
        job = self.pair_job()
        def interrupt(path: Path, *, name: str, engine: str, target: str, source_job_id: str | None = None) -> MusicAdapter:
            if target == 'nar':
                raise OSError('interrupted recovery publication')
            return music_lora.register_file(path, name=name, engine=engine, target=target, source_job_id=source_job_id)
        with patch.object(music_training, 'register_file', side_effect=interrupt), \
                patch.object(music_training, 'find_by_job', side_effect=music_lora.find_by_job):
            await music_training.recover()
        result = music_training.get(job.id)
        self.assertEqual(result.status, 'failed')
        self.assertEqual({adapter.target for adapter in result.adapters}, {'ar'})
        self.assertIsNone(result.adapter)

    async def test_pair_readiness_fails_closed_without_affecting_legacy_training(self) -> None:
        current = MusicLoraTrainingStatus(engine='yue2', supported=True, installed=True, weights_ready=True)
        with patch.object(music_training, 'status', return_value=current):
            music_training._require_ready(self.body())
            with self.assertRaises(MusicLoraError) as caught:
                music_training._require_ready(self.pair_body())
        self.assertEqual(caught.exception.code, 'yue_lora_score_unprepared')

    async def test_completed_pair_keeps_both_adapters_and_the_nar_compatibility_alias(self) -> None:
        self.pair_worker()
        with patch.object(music_training, 'prepare_worker', side_effect=self.command), \
                patch.object(music_training, 'register_file', side_effect=music_lora.register_file), \
                patch.object(music_training, 'find_by_job', side_effect=music_lora.find_by_job):
            job = await music_training.submit(self.pair_body())
            await music_training._tasks[job.id]
        result = music_training.get(job.id)
        self.assertEqual(result.status, 'done')
        self.assertEqual([adapter.target for adapter in result.adapters], ['ar', 'nar'])
        self.assertEqual(result.adapter, result.adapters[1])
        self.assertEqual((result.progress.target, result.progress.step, result.progress.total_steps), ('nar', 3, 3))

    async def test_recovery_without_pair_receipt_never_accepts_first_adapter_as_finished(self) -> None:
        job = self.pair_job()
        directory = music_training._directory(job.id)
        ar = music_lora.register_file(directory / 'ar.safetensors', name=job.request.name, engine='yue2', target='ar', source_job_id=job.id)
        job.adapters = [ar]
        music_training._store(job)
        (directory / 'completion.json').unlink()
        with patch.object(music_training, 'register_file', side_effect=music_lora.register_file), \
                patch.object(music_training, 'find_by_job', side_effect=music_lora.find_by_job), patch.object(music_training, 'prepare_worker') as prepare:
            await music_training.recover()
        prepare.assert_not_called()
        self.assertEqual(music_training.get(job.id).status, 'failed')
        self.assertEqual(music_training.get(job.id).adapters, [ar])
        self.assertEqual(len(music_lora.list_adapters().adapters), 1)

    async def test_pair_cancel_during_second_publication_never_marks_partial_pair_done(self) -> None:
        self.pair_worker()
        started, release = threading.Event(), threading.Event()
        def interrupt(path: Path, *, name: str, engine: str, target: str, source_job_id: str | None = None) -> MusicAdapter:
            if target == 'nar':
                started.set()
                if not release.wait(10):
                    raise TimeoutError('test publication was not released')
                raise OSError('second publication interrupted')
            return music_lora.register_file(path, name=name, engine=engine, target=target, source_job_id=source_job_id)
        with patch.object(music_training, 'prepare_worker', side_effect=self.command), \
                patch.object(music_training, 'register_file', side_effect=interrupt), \
                patch.object(music_training, 'find_by_job', side_effect=music_lora.find_by_job):
            job = await music_training.submit(self.pair_body())
            async with asyncio.timeout(10):
                while not started.is_set():
                    await asyncio.sleep(.01)
            cancel = asyncio.create_task(music_training.cancel(job.id))
            await asyncio.sleep(.02)
            self.assertFalse(cancel.done())
            release.set()
            await cancel
        result = music_training.get(job.id)
        self.assertEqual(result.status, 'cancelled')
        self.assertEqual({adapter.target for adapter in result.adapters}, {'ar'})
        self.assertIsNone(result.adapter)
        with patch.object(music_training, 'register_file', side_effect=music_lora.register_file), \
                patch.object(music_training, 'find_by_job', side_effect=music_lora.find_by_job), patch.object(music_training, 'prepare_worker') as prepare:
            await music_training.recover()
        prepare.assert_not_called()
        self.assertEqual(music_training.get(job.id).status, 'done')

    def command(self, request: MusicLoraTrainingRequest, run: Path, sources: list[Path]) -> tuple[list[str], Path, dict[str, str]]:
        run.mkdir(parents=True, exist_ok=True)
        (run / 'request.json').write_text(json.dumps({'request': request.model_dump(), 'request_sha256': music_training.fingerprint(request)}))
        return [sys.executable, str(self.worker), str(run)], run, dict(os.environ)

    def register(self, path: Path, *, name: str, engine: str, target: str, source_job_id: str | None = None) -> MusicAdapter:
        self.adapter = MusicAdapter(id='a' * 32, engine='yue2', target='nar', name=name,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), created_at='2026-10-14', source_job_id=source_job_id)
        return self.adapter

    async def finish(self) -> str:
        job = await music_training.submit(self.body())
        await music_training._tasks[job.id]
        return job.id

    async def test_completed_native_checkpoint_is_registered_once_and_sources_are_immutable(self) -> None:
        with patch.object(music_training, 'prepare_worker', side_effect=self.command):
            identifier = await self.finish()
        job = music_training.get(identifier)
        self.assertEqual(job.status, 'done')
        self.assertIsNotNone(job.adapter)
        self.assertEqual(self.source.read_bytes(), b'preserve original')
        await music_training.recover()
        self.assertEqual(music_training.get(identifier).adapter, self.adapter)

    async def test_admission_includes_pcm_latents_and_retained_optimizer_checkpoints(self) -> None:
        parameters = 28 * 8 * 32768
        native_bytes = 28 * 8 * 38912 * 4
        pcm = 13 * 48000 * 2 * 2
        latents = (12 * 25 + 1) * 64 * 2
        required = 2 * pcm + 2 * latents + parameters * 10 + 2 * 1024**2 + 2 * native_bytes + 64 * 1024**2
        usage = module_catalog.shutil.disk_usage(self.root)
        with patch.object(module_catalog.shutil, 'disk_usage', return_value=usage._replace(free=required - 1)), \
                patch.object(music_training, '_run', new=AsyncMock()), self.assertRaises(MusicLoraError) as caught:
            await music_training.submit(self.body())
        self.assertEqual(caught.exception.code, 'insufficient_disk')
        self.assertEqual(music_training.list_jobs().jobs, [])
        self.assertFalse((self.root / 'music-lora-training').exists())
        self.assertEqual(self.source.read_bytes(), b'preserve original')

    async def test_multiple_sources_need_additional_decoded_and_cached_space(self) -> None:
        second = db.insert_track(model='upload', title='Second', lyrics='', seed=None, duration_ms=1000,
            wall_ms=None, params={}, audio_path=self.source, abc_path=None)
        parameters = 28 * 8 * 32768
        single = 2 * 13 * 48000 * 4 + 2 * 301 * 64 * 2 + parameters * 10 + 2 * 1024**2 + 2 * 28 * 8 * 38912 * 4 + 64 * 1024**2
        usage = module_catalog.shutil.disk_usage(self.root)
        request = self.body().model_copy(update={'samples': [*self.body().samples, MusicLoraTrainingSample(track_id=second, caption='Piano')]})
        with patch.object(module_catalog.shutil, 'disk_usage', return_value=usage._replace(free=single)), \
                patch.object(music_training, '_run', new=AsyncMock()), self.assertRaises(MusicLoraError) as caught:
            await music_training.submit(request)
        self.assertEqual(caught.exception.code, 'insufficient_disk')

    async def test_long_runs_reserve_every_retained_checkpoint_and_optimizer_state(self) -> None:
        usage = module_catalog.shutil.disk_usage(self.root)
        with patch.object(module_catalog.shutil, 'disk_usage', return_value=usage._replace(free=220 * 1024**2)), \
                patch.object(music_training, '_run', new=AsyncMock()), self.assertRaises(MusicLoraError) as caught:
            await music_training.submit(self.body().model_copy(update={'max_steps': 50}))
        self.assertEqual(caught.exception.code, 'insufficient_disk')

    async def test_paired_runs_reserve_both_adapter_phases(self) -> None:
        usage = module_catalog.shutil.disk_usage(self.root)
        with patch.object(module_catalog.shutil, 'disk_usage', return_value=usage._replace(free=220 * 1024**2)), \
                patch.object(music_training, '_run', new=AsyncMock()), self.assertRaises(MusicLoraError) as caught:
            await music_training.submit(self.pair_body())
        self.assertEqual(caught.exception.code, 'insufficient_disk')

    async def test_portable_package_does_not_reserve_local_training_checkpoints(self) -> None:
        usage = module_catalog.shutil.disk_usage(self.root)
        required = self.source.stat().st_size * 2 + 64 * 1024**2
        with patch.object(module_catalog.shutil, 'disk_usage', return_value=usage._replace(free=required)), \
                patch.object(music_training, '_run', new=AsyncMock()):
            job = await music_training.submit(self.body(), kind='nvidia-package')
            await music_training._tasks[job.id]
        self.assertEqual(job.kind, 'nvidia-package')

    async def test_space_is_rechecked_before_training_worker_preparation(self) -> None:
        usage = module_catalog.shutil.disk_usage(self.root)
        with patch.object(music_training, 'prepare_worker', side_effect=self.command) as prepare:
            job = await music_training.submit(self.body())
            with patch.object(module_catalog.shutil, 'disk_usage', return_value=usage._replace(free=1)):
                await music_training._tasks[job.id]
        self.assertEqual(music_training.get(job.id).error_code, 'insufficient_disk')
        prepare.assert_not_called()
        self.assertEqual(self.source.read_bytes(), b'preserve original')

    async def test_publication_reserves_the_actual_adapter_copy_and_recovers_without_training(self) -> None:
        usage = module_catalog.shutil.disk_usage(self.root)
        def command(request: MusicLoraTrainingRequest, run: Path, sources: list[Path]) -> tuple[list[str], Path, dict[str, str]]:
            result = self.command(request, run, sources)
            disk.return_value = usage._replace(free=64 * 1024**2 + 1)
            return result
        with patch.object(module_catalog.shutil, 'disk_usage', return_value=usage) as disk, \
                patch.object(music_training, 'prepare_worker', side_effect=command):
            identifier = await self.finish()
        job = music_training.get(identifier)
        self.assertEqual(job.error_code, 'insufficient_disk')
        self.assertIsNone(job.adapter)
        self.assertTrue((music_training._directory(identifier) / 'final.safetensors').is_file())
        with patch.object(music_training, 'prepare_worker') as prepare:
            await music_training.recover()
        prepare.assert_not_called()
        self.assertEqual(music_training.get(identifier).status, 'done')
        self.assertEqual(self.source.read_bytes(), b'preserve original')

    async def test_disk_full_adapter_publication_has_a_stable_recoverable_error(self) -> None:
        with patch.object(music_training, 'prepare_worker', side_effect=self.command), \
                patch.object(music_training, 'register_file', side_effect=OSError(errno.ENOSPC, 'disk full')):
            identifier = await self.finish()
        job = music_training.get(identifier)
        self.assertEqual(job.error_code, 'insufficient_disk')
        self.assertTrue((music_training._directory(identifier) / 'completion.json').is_file())
        self.assertEqual(self.source.read_bytes(), b'preserve original')
        await music_training.recover()
        self.assertEqual(music_training.get(identifier).status, 'done')

    async def test_final_checkpoint_registers_in_real_managed_adapter_library(self) -> None:
        with patch.object(music_training, 'prepare_worker', side_effect=self.command), \
             patch.object(music_training, 'register_file', side_effect=music_lora.register_file), \
             patch.object(music_training, 'find_by_job', side_effect=music_lora.find_by_job):
            identifier = await self.finish()
            job = music_training.get(identifier)
            self.assertEqual(job.status, 'done')
            self.assertIsNotNone(job.adapter)
            if job.adapter is None:
                self.fail('Expected registered adapter')
            self.assertEqual(music_lora.get(job.adapter.id).source_job_id, identifier)
            await music_training.recover()
            self.assertEqual(len(music_lora.list_adapters().adapters), 1)

    async def test_partial_checkpoint_without_completion_is_never_imported(self) -> None:
        self.worker.write_text(f'import sys\nfrom pathlib import Path\n(Path(sys.argv[1])/"partial.safetensors").write_bytes({checkpoint_bytes()!r})\n')
        with patch.object(music_training, 'prepare_worker', side_effect=self.command):
            identifier = await self.finish()
        self.assertEqual(music_training.get(identifier).status, 'failed')
        await music_training.recover()
        self.assertIsNone(self.adapter)
        self.assertTrue((music_training._directory(identifier) / 'partial.safetensors').is_file())

    async def test_recovery_retries_publication_without_rerunning_or_duplicate_import(self) -> None:
        with patch.object(music_training, 'prepare_worker', side_effect=self.command), patch.object(music_training, 'register_file', side_effect=OSError('interrupted publication')):
            identifier = await self.finish()
        self.assertEqual(music_training.get(identifier).status, 'failed')
        with patch.object(music_training, 'prepare_worker') as prepare:
            await music_training.recover()
            await music_training.recover()
        prepare.assert_not_called()
        self.assertEqual(music_training.get(identifier).status, 'done')

    async def test_changed_checkpoint_or_manifest_cannot_be_registered_during_recovery(self) -> None:
        with patch.object(music_training, 'prepare_worker', side_effect=self.command), patch.object(music_training, 'register_file', side_effect=OSError('interrupted publication')):
            identifier = await self.finish()
        (music_training._directory(identifier) / 'final.safetensors').write_bytes(checkpoint_bytes() + b'changed')
        await music_training.recover()
        self.assertIsNone(self.adapter)
        self.assertEqual(music_training.get(identifier).status, 'failed')

    async def test_cancel_before_start_does_not_launch_and_shutdown_prevents_submission(self) -> None:
        with patch.object(music_training, 'prepare_worker') as prepare:
            job = await music_training.submit(self.body())
            await music_training.cancel(job.id)
        prepare.assert_not_called()
        self.assertEqual(music_training.get(job.id).status, 'cancelled')
        await music_training.shutdown()
        with self.assertRaises(MusicLoraError):
            await music_training.submit(self.body())

    async def test_missing_or_escaped_track_source_refused_before_job_registration(self) -> None:
        outside = self.root / 'outside.wav'
        outside.write_bytes(b'preserve outside')
        self.source.unlink()
        self.source.symlink_to(outside)
        with self.assertRaises(MusicLoraError):
            await music_training.submit(self.body())
        self.assertEqual(music_training.list_jobs().jobs, [])
        self.assertEqual(outside.read_bytes(), b'preserve outside')

    async def test_other_training_job_blocks_registration(self) -> None:
        with patch.object(music_training, 'prepare_worker', side_effect=self.command):
            job = await music_training.submit(self.body())
            with self.assertRaises(MusicLoraError):
                await music_training.submit(self.body())
            await music_training.cancel(job.id)

    async def test_cancel_drains_descendants_and_keeps_checkpoint_artifacts(self) -> None:
        started = self.root / 'started'
        descendant = self.root / 'descendant.pid'
        self.worker.write_text('import subprocess,sys,time\nfrom pathlib import Path\n'
            f'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);Path({str(descendant)!r}).write_text(str(child.pid))\n'
            'run=Path(sys.argv[1]);(run/"audio").mkdir();(run/"audio/000.partial.wav").write_bytes(b"partial")\n'
            f'(run/"retained.safetensors").write_bytes({checkpoint_bytes()!r})\n'
            f'Path({str(started)!r}).write_text("ready")\ntime.sleep(60)\n')
        with patch.object(music_training, 'prepare_worker', side_effect=self.command):
            job = await music_training.submit(self.body())
            async with asyncio.timeout(10):
                while not started.is_file():
                    await asyncio.sleep(.01)
            await music_training.cancel(job.id)
        self.assertEqual(music_training.get(job.id).status, 'cancelled')
        self.assertFalse(music_training.work_busy())
        self.assertTrue((music_training._directory(job.id) / 'retained.safetensors').is_file())
        self.assertFalse((music_training._directory(job.id) / 'audio/000.partial.wav').exists())
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
            f'Path({str(started)!r}).write_text("ready")\ntime.sleep(60)\n')
        with patch.object(music_training, 'prepare_worker', side_effect=self.command):
            job = await music_training.submit(self.body())
            async with asyncio.timeout(10):
                while not started.is_file():
                    await asyncio.sleep(.01)
            try:
                await music_training.cancel(job.id)
                self.assertEqual(music_training.get(job.id).status, 'cancelled')
                self.assertFalse(music_training.work_busy())
            finally:
                from app.job_lifecycle import cancel_and_wait
                await cancel_and_wait(music_training._tasks.get(job.id))

    async def test_completed_history_reads_do_not_rescan_the_adapter_library(self) -> None:
        with patch.object(music_training, 'prepare_worker', side_effect=self.command):
            identifier = await self.finish()
        with patch.object(music_training, 'find_by_job') as find:
            self.assertEqual(music_training.get(identifier).status, 'done')
            self.assertEqual(music_training.list_jobs().jobs[0].status, 'done')
        find.assert_not_called()

    async def test_recovery_changed_checkpoint_marks_running_record_failed(self) -> None:
        with patch.object(music_training, 'prepare_worker', side_effect=self.command), patch.object(music_training, 'register_file', side_effect=OSError('interrupted publication')):
            identifier = await self.finish()
        job = music_training.get(identifier)
        job.status = 'running'
        music_training._store(job)
        (music_training._directory(identifier) / 'final.safetensors').write_bytes(checkpoint_bytes() + b'changed')
        await music_training.recover()
        self.assertEqual(music_training.get(identifier).status, 'failed')
        self.assertEqual(music_training.get(identifier).error_code, 'music_training_checkpoint_changed')

    async def test_corrupt_record_preserves_other_history_and_does_not_abort_recovery(self) -> None:
        with patch.object(music_training, 'prepare_worker', side_effect=self.command):
            identifier = await self.finish()
        self.connection.execute('INSERT INTO music_training_jobs(id,payload_json,created_at) VALUES(?,?,?)', ('c' * 32, 'invalid', '2026-01-01'))
        self.connection.commit()
        await music_training.recover()
        response = music_training.list_jobs()
        self.assertTrue(response.history_incomplete)
        self.assertEqual([job.id for job in response.jobs], [identifier])

    async def test_unknown_worker_remains_quarantined_after_recovery(self) -> None:
        with patch.object(music_training, 'prepare_worker', side_effect=self.command):
            job = await music_training.submit(self.body())
            await music_training.cancel(job.id)
        identity = WorkerIdentity(pid=12345, token='a' * 32, receipt=str(music_training._directory(job.id) / 'worker.json'))
        music_training._store_identity(job.id, identity)
        with patch.object(music_training, 'terminate_verified', new=AsyncMock(return_value=False)):
            await music_training.recover()
        self.assertTrue(music_training.work_busy())
        self.assertEqual(music_training.get(job.id).error_code, 'music_training_worker_unverified')
        with self.assertRaises(MusicLoraError):
            await music_training.submit(self.body())

    async def test_cancel_during_publication_preserves_the_completed_adapter_link(self) -> None:
        started, release = threading.Event(), threading.Event()
        original = music_training.register_file
        def blocking_register(path: Path, *, name: str, engine: str, target: str, source_job_id: str | None = None) -> MusicAdapter:
            started.set()
            if not release.wait(10):
                raise TimeoutError('test publication was not released')
            return original(path, name=name, engine=engine, target=target, source_job_id=source_job_id)
        with patch.object(music_training, 'prepare_worker', side_effect=self.command), patch.object(music_training, 'register_file', side_effect=blocking_register):
            job = await music_training.submit(self.body())
            async with asyncio.timeout(10):
                while not started.is_set():
                    await asyncio.sleep(.01)
            cancel = asyncio.create_task(music_training.cancel(job.id))
            await asyncio.sleep(.02)
            self.assertFalse(cancel.done())
            release.set()
            await cancel
        result = music_training.get(job.id)
        self.assertEqual(result.status, 'done')
        self.assertIsNotNone(result.adapter)
        await music_training.recover()
        self.assertEqual(music_training.get(job.id).adapter, result.adapter)

    async def test_portable_package_needs_no_local_trainer_and_recovers_without_registration(self) -> None:
        self.worker.write_text('import hashlib,json,sys\nfrom pathlib import Path\n'
            'run=Path(sys.argv[1]);spec=json.loads((run/"request.json").read_text())\n'
            '(run/"package.zip").write_bytes(b"portable training archive")\n'
            '(run/"package-completion.json").write_text(json.dumps({"job_id":run.name,"engine":spec["request"]["engine"],"request_sha256":spec["request_sha256"],"archive":"package.zip","sha256":hashlib.sha256((run/"package.zip").read_bytes()).hexdigest(),"bytes":(run/"package.zip").stat().st_size}))\n')
        with patch.object(music_training, 'prepare_package_worker', side_effect=self.command), patch.object(music_training, '_require_ready') as ready, patch.object(music_training, 'register_file') as register:
            job = await music_training.submit(self.body(), kind='nvidia-package')
            await music_training._tasks[job.id]
            result = music_training.get(job.id)
            self.assertEqual(result.status, 'done')
            self.assertEqual(result.kind, 'nvidia-package')
            self.assertIsNotNone(result.package_sha256)
            self.assertIsNone(result.adapter)
            result.status = 'interrupted'
            result.package_sha256 = None
            music_training._store(result)
            await music_training.recover()
            self.assertEqual(music_training.get(job.id).status, 'done')
            self.assertEqual(music_training.package_path(job.id).read_bytes(), b'portable training archive')
        ready.assert_not_called()
        register.assert_not_called()
        self.assertEqual(self.source.read_bytes(), b'preserve original')

    async def test_portable_package_changed_archive_is_not_downloadable(self) -> None:
        with patch.object(music_training, 'prepare_worker', side_effect=self.command):
            identifier = await self.finish()
        with self.assertRaises(MusicLoraError):
            music_training.package_path(identifier)
        job = music_training.get(identifier)
        job.kind = 'nvidia-package'
        job.package_sha256 = 'a' * 64
        music_training._store(job)
        (music_training._directory(identifier) / 'package.zip').write_bytes(b'changed')
        with self.assertRaises(MusicLoraError):
            music_training.package_path(identifier)

    async def test_training_routes_reject_cross_origin_and_invalid_requests(self) -> None:
        import httpx
        from fastapi import FastAPI
        from app.api.routes_music_training import router
        app = FastAPI()
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:9000') as client:
            response = await client.post('/api/music-lora/training/packages', json=self.body().model_dump(), headers={'Origin': 'https://outside.example'})
            self.assertEqual(response.status_code, 403)
            response = await client.post('/api/music-lora/training/packages', json={'engine': 'yue2', 'sources': ['/private/secret']})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()['detail'], 'music_training_request_invalid')
            response = await client.post('/api/music-lora/training/packages', content=b' ' * (2 * 1024 * 1024 + 1), headers={'Content-Type': 'application/json'})
            self.assertEqual(response.status_code, 413)
            response = await client.get('/api/music-lora/training/jobs/' + '0' * 32 + '/package')
            self.assertEqual(response.status_code, 404)

    async def test_cancelled_package_removes_private_staging_after_process_drain(self) -> None:
        started = self.root / 'package-started'
        self.worker.write_text('import sys,time\nfrom pathlib import Path\n'
            'run=Path(sys.argv[1]);private=run/".music-training-package-test";private.mkdir();(private/"audio.wav").write_bytes(b"private copy")\n'
            '(run/".package.zip.test.part").write_bytes(b"partial archive")\n'
            f'Path({str(started)!r}).write_text("ready")\ntime.sleep(60)\n')
        with patch.object(music_training, 'prepare_package_worker', side_effect=self.command):
            job = await music_training.submit(self.body(), kind='nvidia-package')
            async with asyncio.timeout(10):
                while not started.is_file():
                    await asyncio.sleep(.01)
            await music_training.cancel(job.id)
        directory = music_training._directory(job.id)
        self.assertEqual(music_training.get(job.id).status, 'cancelled')
        self.assertEqual(list(directory.glob('.music-training-package-*')), [])
        self.assertEqual(list(directory.glob('.package.zip.*.part')), [])
        self.assertEqual(self.source.read_bytes(), b'preserve original')

    async def test_completed_history_retains_the_actual_final_loss_and_update(self) -> None:
        original = self.worker.read_text()
        self.worker.write_text(original + '(run/"stage.json").write_text(json.dumps({"stage":"training","step":3,"total_steps":3,"loss":0.125,"seconds_per_step":12.5,"updated_at":"2026-10-15T19:17:27Z"}))\n')
        with patch.object(music_training, 'prepare_worker', side_effect=self.command):
            identifier = await self.finish()
        progress = music_training.get(identifier).progress
        self.assertEqual(progress.stage, 'done')
        self.assertEqual(progress.loss, .125)
        self.assertEqual(progress.seconds_per_step, 12.5)
        self.assertIsNotNone(music_training.get(identifier).started_at)
        self.assertIsNotNone(music_training.get(identifier).finished_at)
        self.assertEqual(progress.updated_at, '2026-10-15T19:17:27Z')
        await music_training.recover()
        self.assertEqual(music_training.get(identifier).progress, progress)

    async def test_polling_persists_progress_and_rejects_regressive_or_mismatched_steps(self) -> None:
        from app.music_training_contracts import MusicLoraTrainingJob
        identifier = 'b' * 32
        directory = music_training._directory(identifier, create=True)
        job = MusicLoraTrainingJob(id=identifier, status='running', created_at='2026-10-15T00:00:00Z', request=self.body())
        self.connection.execute('INSERT INTO music_training_jobs(id,payload_json,created_at) VALUES(?,?,?)',
            (identifier, job.model_dump_json(), job.created_at))
        self.connection.commit()
        path = directory / 'stage.json'
        report = {'stage': 'training', 'step': 2, 'total_steps': 3, 'loss': .4, 'seconds_per_step': 12.5,
            'updated_at': '2026-10-15T19:17:27Z'}
        path.write_text(json.dumps(report))
        observed = music_training.get(identifier).progress
        stored = self.connection.execute('SELECT payload_json FROM music_training_jobs WHERE id=?', (identifier,)).fetchone()
        self.assertEqual(MusicLoraTrainingJob.model_validate_json(stored['payload_json']).progress, observed)
        for change in ({'step': 1}, {'total_steps': 4}, {'seconds_per_step': -1}, {'step': 4, 'total_steps': None}):
            path.write_text(json.dumps({**report, **change}))
            self.assertEqual(music_training.get(identifier).progress, observed)

    async def test_cancellation_keeps_the_latest_actual_optimizer_progress(self) -> None:
        started = self.root / 'optimizer-started'
        self.worker.write_text('import json,sys,time\nfrom pathlib import Path\n'
            '(Path(sys.argv[1])/"stage.json").write_text(json.dumps({"stage":"training","step":2,"total_steps":3,"loss":0.42,"updated_at":"2026-10-15T19:17:27Z"}))\n'
            f'Path({str(started)!r}).write_text("ready")\ntime.sleep(60)\n')
        with patch.object(music_training, 'prepare_worker', side_effect=self.command):
            job = await music_training.submit(self.body())
            async with asyncio.timeout(10):
                while not started.is_file():
                    await asyncio.sleep(.01)
            await music_training.cancel(job.id)
        result = music_training.get(job.id)
        self.assertEqual(result.status, 'cancelled')
        self.assertEqual((result.progress.stage, result.progress.step, result.progress.loss), ('training', 2, .42))
        self.assertIsNotNone(result.started_at)
        self.assertIsNotNone(result.finished_at)
