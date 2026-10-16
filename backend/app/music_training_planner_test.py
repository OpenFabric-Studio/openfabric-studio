from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import wave

from app import db, music_lora, music_training
from app.music_lora_contracts import MusicLoraError
from app.music_training_contracts import MusicLoraTrainingJob, MusicLoraTrainingRequest, MusicLoraTrainingStatus, YueLoraPairSettings
from app.music_training_test import BASE_SHA, paired_checkpoint

SCORE = 'X:1\nM:4/4\nL:1/8\nK:C\nCDEF GABc|\n'


class MissingPlannerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.run = music_training._run
        ready = MusicLoraTrainingStatus(engine='yue2', supported=True, installed=True, weights_ready=True, paired_ready=True)
        self.patches = [patch.object(db, '_db', None), patch.object(db, 'DATA_DIR', self.root),
            patch.object(db, 'DB_PATH', self.root / 'catalog.db'), patch.object(db, 'FILES_DIR', self.root / 'files'),
            patch.object(music_training, '_tasks', {}), patch.object(music_training, '_unverified', set()),
            patch.object(music_training, '_closing', False), patch.object(music_training, 'status', return_value=ready),
            patch.object(music_training, '_run', new=AsyncMock()),
            patch('app.work_busy.other_work_busy', new=AsyncMock(return_value=False)),
            patch('app.video_jobs.work_busy', return_value=False), patch('app.resource_admission.native_work_inflight', return_value=False)]
        for item in self.patches:
            item.start()
        self.connection = db.get_db()
        music_training.migrate(self.connection)
        self.original = db.FILES_DIR / 'upload/source.wav'
        self.original.parent.mkdir(parents=True)
        self.original.write_bytes(b'original recording preserved')
        track = db.insert_track(model='upload', title='Source', lyrics='', seed=None, duration_ms=6000,
            wall_ms=None, params={}, audio_path=self.original, abc_path=None)
        request = MusicLoraTrainingRequest(engine='yue2', name='Existing acoustic', samples=[{'track_id': track, 'caption': 'Warm piano'}],
            rank=8, seed=19, segment_seconds=6, max_steps=3)
        self.parent = MusicLoraTrainingJob(id='d' * 32, status='done', created_at='2026-10-16', request=request)
        self.directory = music_training._directory(self.parent.id, create=True)
        (self.directory / 'audio').mkdir()
        self.audio = self.directory / 'audio/000.wav'
        with wave.open(str(self.audio), 'wb') as output:
            output.setnchannels(2);output.setsampwidth(2);output.setframerate(48000)
            output.writeframes(b'\0\0\0\0' * 48000 * 6)
        (self.directory / 'request.json').write_text(json.dumps({'request': request.model_dump(mode='json', exclude_none=True),
            'request_sha256': music_training.fingerprint(request), 'sources': [str(self.original)]}))
        self.manifest = {'request_sha256': music_training.fingerprint(request), 'samples': [{
            'sample': '000.wav', 'track_id': track, 'source_sha256': hashlib.sha256(self.original.read_bytes()).hexdigest(),
            'audio_sha256': hashlib.sha256(self.audio.read_bytes()).hexdigest(), 'caption': 'Warm piano', 'lyrics': '',
            'instrumental': False, 'original_duration_seconds': 6.0, 'offset_seconds': 0, 'segment_seconds': 6}]}
        (self.directory / 'dataset.json').write_text(json.dumps(self.manifest))
        checkpoint = self.directory / 'adapter.safetensors'
        checkpoint.write_bytes(paired_checkpoint('nar'))
        self.parent.adapter = music_lora.register_file(checkpoint, name=request.name, engine='yue2', target='nar', source_job_id=self.parent.id)
        self.nar = self.parent.adapter
        (self.directory / 'completion.json').write_text(json.dumps({'job_id': self.parent.id, 'engine': 'yue2',
            'request_sha256': music_training.fingerprint(request), 'dataset_sha256': hashlib.sha256((self.directory / 'dataset.json').read_bytes()).hexdigest(),
            'base_sha256': BASE_SHA, 'max_steps': 3, 'checkpoint': checkpoint.name, 'sha256': hashlib.sha256(checkpoint.read_bytes()).hexdigest()}))
        self.connection.execute('INSERT INTO music_training_jobs(id,payload_json,created_at) VALUES(?,?,?)',
            (self.parent.id, self.parent.model_dump_json(), self.parent.created_at))
        self.connection.commit()

    async def asyncTearDown(self) -> None:
        await music_training.shutdown()
        self.connection.close()
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    async def test_capability_and_submission_reuse_the_verified_saved_excerpt(self) -> None:
        self.assertTrue(music_training.get(self.parent.id).can_train_missing_ar)
        self.original.unlink()
        with patch.object(music_training, '_run', new=AsyncMock()):
            child = await music_training.submit_planner(self.parent.id, YueLoraPairSettings(planner_steps=7, planner_learning_rate=.00002))
            await music_training._tasks[child.id]
        self.assertEqual(child.request.samples, self.parent.request.samples)
        self.assertEqual(child.request.name, self.parent.request.name)
        self.assertEqual((child.request.rank, child.request.seed, child.request.segment_seconds), (8, 19, 6))
        self.assertEqual((child.request.max_steps, child.request.learning_rate), (7, .00002))
        self.assertEqual(child.request.yue_ar_source_job_id, self.parent.id)
        self.assertFalse(music_training.get(self.parent.id).can_train_missing_ar)
        self.assertEqual(music_training._sources(child.request), [self.audio])
        with self.assertRaisesRegex(MusicLoraError, 'music_training_planner_exists'):
            await music_training.submit_planner(self.parent.id, YueLoraPairSettings())

    async def test_capability_is_a_cheap_hint_and_start_performs_integrity_checks(self) -> None:
        with patch('app.yue_planner_source.verify', side_effect=AssertionError('full validation during polling')), \
                patch('app.yue_planner_source.digest', side_effect=AssertionError('hashing during polling')), \
                patch('app.music_lora.resolve', side_effect=AssertionError('adapter hashing during polling')), \
                patch('app.music_lora.validate_file', side_effect=AssertionError('native validation during polling')), \
                patch('app.yue_planner_source.validate_prepared_audio', side_effect=AssertionError('audio validation during polling')):
            self.assertTrue(music_training.get(self.parent.id).can_train_missing_ar)
        self.audio.write_bytes(b'changed')
        self.assertTrue(music_training.get(self.parent.id).can_train_missing_ar)
        with self.assertRaisesRegex(MusicLoraError, 'music_training_planner_source_unavailable'):
            await music_training.submit_planner(self.parent.id, YueLoraPairSettings())

    async def test_changed_or_missing_parent_evidence_refuses_training(self) -> None:
        originals = {name: (self.directory / name).read_bytes() for name in ('dataset.json', 'request.json', 'completion.json', 'audio/000.wav', 'adapter.safetensors')}
        for failure in ('audio', 'caption', 'lyrics', 'segment', 'request', 'dataset-binding', 'receipt', 'checkpoint', 'missing', 'symlink'):
            with self.subTest(failure=failure):
                for name, data in originals.items():
                    path = self.directory / name
                    if path.is_symlink(): path.unlink()
                    path.write_bytes(data)
                if failure in ('caption', 'lyrics', 'segment', 'dataset-binding'):
                    manifest = copy.deepcopy(self.manifest)
                    if failure == 'dataset-binding': manifest['request_sha256'] = '0' * 64
                    else: manifest['samples'][0][{'caption': 'caption', 'lyrics': 'lyrics', 'segment': 'segment_seconds'}[failure]] = 12 if failure == 'segment' else 'changed'
                    (self.directory / 'dataset.json').write_text(json.dumps(manifest))
                    receipt = json.loads(originals['completion.json']);receipt['dataset_sha256'] = hashlib.sha256((self.directory / 'dataset.json').read_bytes()).hexdigest()
                    (self.directory / 'completion.json').write_text(json.dumps(receipt))
                elif failure == 'request':
                    snapshot = json.loads(originals['request.json']);snapshot['request']['seed'] = 20
                    (self.directory / 'request.json').write_text(json.dumps(snapshot))
                elif failure == 'receipt':
                    receipt = json.loads(originals['completion.json']);receipt['job_id'] = 'e' * 32
                    (self.directory / 'completion.json').write_text(json.dumps(receipt))
                elif failure == 'checkpoint': (self.directory / 'adapter.safetensors').write_bytes(b'broken')
                elif failure == 'missing': self.audio.unlink()
                elif failure == 'symlink': self.audio.unlink();self.audio.symlink_to(self.original)
                else: self.audio.write_bytes(b'changed')
                self.assertEqual(music_training.get(self.parent.id).can_train_missing_ar, failure in ('audio', 'checkpoint'))
                with self.assertRaisesRegex(MusicLoraError, 'music_training_planner_source_unavailable'):
                    await music_training.submit_planner(self.parent.id, YueLoraPairSettings())
        self.assertEqual(self.connection.execute('SELECT count(*) FROM music_training_jobs').fetchone()[0], 1)

    async def test_legacy_cached_excerpt_without_optional_provenance_can_complete_ar(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        for field in ('original_duration_seconds', 'offset_seconds', 'segment_seconds'):
            del manifest['samples'][0][field]
        (self.directory / 'dataset.json').write_text(json.dumps(manifest))
        receipt = json.loads((self.directory / 'completion.json').read_bytes())
        receipt['dataset_sha256'] = hashlib.sha256((self.directory / 'dataset.json').read_bytes()).hexdigest()
        (self.directory / 'completion.json').write_text(json.dumps(receipt))
        self.assertTrue(music_training.get(self.parent.id).can_train_missing_ar)
        child = await music_training.submit_planner(self.parent.id, YueLoraPairSettings())
        await music_training._tasks[child.id]
        self.assertEqual(music_training._sources(child.request), [self.audio])

    async def test_direct_request_cannot_change_the_saved_training_source_or_settings(self) -> None:
        base = self.parent.request.model_dump(mode='json')
        for changed in ({'name': 'Replacement'}, {'seed': 20}, {'rank': 16}, {'segment_seconds': 12},
                {'samples': [{'track_id': self.parent.request.samples[0].track_id, 'caption': 'Different'}]}):
            request = MusicLoraTrainingRequest.model_validate({**base, **changed, 'yue_ar_source_job_id': self.parent.id})
            with self.subTest(changed=changed), self.assertRaisesRegex(MusicLoraError, 'music_training_planner_request_invalid'):
                await music_training.submit(request)

    async def test_failed_unpublished_child_can_retry_but_registered_ar_cannot(self) -> None:
        with patch.object(music_training, '_run', new=AsyncMock()):
            child = await music_training.submit_planner(self.parent.id, YueLoraPairSettings())
            await music_training._tasks[child.id]
            music_training._tasks.pop(child.id)
            child.status = 'failed';music_training._store(child)
            self.assertTrue(music_training.get(self.parent.id).can_train_missing_ar)
            ar_path = self.directory / 'saved-ar.safetensors';ar_path.write_bytes(paired_checkpoint('ar'))
            music_lora.register_file(ar_path, name=child.request.name, engine='yue2', target='ar', source_job_id=child.id)
            self.assertFalse(music_training.get(self.parent.id).can_train_missing_ar)
            with self.assertRaisesRegex(MusicLoraError, 'music_training_planner_exists'):
                await music_training.submit_planner(self.parent.id, YueLoraPairSettings())

    def worker(self, request: MusicLoraTrainingRequest, directory: Path, sources: list[Path], **kwargs: object) -> tuple[list[str], Path, dict[str, str]]:
        self.assertEqual(sources, [self.audio])
        self.assertIn('prepared_audio', kwargs)
        (directory / 'request.json').write_text(json.dumps({'request': request.model_dump(mode='json'), 'request_sha256': music_training.fingerprint(request)}))
        script = self.root / 'fake-ar-worker.py'
        script.write_text('import hashlib,json,shutil,sys\nfrom pathlib import Path\n'
            'run=Path(sys.argv[1]);audio=Path(sys.argv[2]);spec=json.loads((run/"request.json").read_text())\n'
            '(run/"audio").mkdir();shutil.copyfile(audio,run/"audio/000.wav")\n'
            f'manifest={self.manifest!r}\n'
            'manifest["request_sha256"]=spec["request_sha256"];manifest["samples"][0]["source_sha256"]=hashlib.sha256(audio.read_bytes()).hexdigest()\n'
            f'(run/"audio/000.abc").write_text({SCORE!r});manifest["samples"][0].update({{"score":"000.abc","score_sha256":hashlib.sha256((run/"audio/000.abc").read_bytes()).hexdigest()}})\n'
            '(run/"dataset.json").write_text(json.dumps(manifest))\n'
            f'data={paired_checkpoint("ar")!r}\n'
            '(run/"adapter-ar.safetensors").write_bytes(data)\n'
            f'(run/"completion.json").write_text(json.dumps({{"job_id":run.name,"engine":"yue2","request_sha256":spec["request_sha256"],"dataset_sha256":hashlib.sha256((run/"dataset.json").read_bytes()).hexdigest(),"base_sha256":{BASE_SHA!r},"max_steps":spec["request"]["max_steps"],"checkpoint":"adapter-ar.safetensors","sha256":hashlib.sha256(data).hexdigest()}}))\n')
        return [sys.executable, str(script), str(directory), str(self.audio)], directory, dict(os.environ)

    async def test_ar_publication_and_recovery_preserve_and_link_existing_nar(self) -> None:
        original = self.audio.read_bytes()
        nar_checkpoint = (self.directory / 'adapter.safetensors').read_bytes()
        with patch.object(music_training, '_run', side_effect=self.run), patch.object(music_training, 'prepare_worker', side_effect=self.worker), \
                patch.object(music_training, 'register_file', side_effect=OSError('publication interrupted')):
            child = await music_training.submit_planner(self.parent.id, YueLoraPairSettings(planner_steps=2))
            await music_training._tasks[child.id]
        self.assertEqual(music_training.get(child.id).status, 'failed')
        self.assertFalse(music_training.get(self.parent.id).can_train_missing_ar)
        with patch.object(music_training, 'prepare_worker') as prepare:
            await music_training.recover()
            await music_training.recover()
        prepare.assert_not_called()
        result = music_training.get(child.id)
        self.assertEqual(result.status, 'done')
        self.assertIsNotNone(result.adapter)
        self.assertEqual(result.adapter.target, 'ar')
        self.assertEqual([adapter.target for adapter in result.adapters], ['ar', 'nar'])
        self.assertEqual(result.adapters[1], self.nar)
        self.assertEqual(result.progress.target, 'ar')
        self.assertEqual(len(music_lora.list_adapters().adapters), 2)
        self.assertEqual(self.audio.read_bytes(), original)
        self.assertEqual((self.directory / 'adapter.safetensors').read_bytes(), nar_checkpoint)

    async def test_ar_publication_rejects_changed_source_and_wrong_target_before_registration(self) -> None:
        with patch.object(music_training, '_run', new=AsyncMock()):
            child = await music_training.submit_planner(self.parent.id, YueLoraPairSettings(planner_steps=2))
            await music_training._tasks[child.id]
        directory = music_training._directory(child.id)
        import subprocess
        argv, cwd, environment = self.worker(child.request, directory, [self.audio], prepared_audio=())
        subprocess.run(argv, cwd=cwd, env=environment, check=True)
        for failure in ('target', 'caption', 'child-audio'):
            with self.subTest(failure=failure):
                data = paired_checkpoint('nar' if failure == 'target' else 'ar')
                (directory / 'adapter-ar.safetensors').write_bytes(data)
                manifest = copy.deepcopy(self.manifest)
                manifest['request_sha256'] = music_training.fingerprint(child.request)
                manifest['samples'][0]['source_sha256'] = manifest['samples'][0]['audio_sha256']
                manifest['samples'][0].update({'score': '000.abc', 'score_sha256': hashlib.sha256(SCORE.encode()).hexdigest()})
                if failure == 'caption': manifest['samples'][0]['caption'] = 'changed'
                (directory / 'dataset.json').write_text(json.dumps(manifest))
                if failure == 'child-audio': (directory / 'audio/000.wav').write_bytes(b'changed')
                receipt = json.loads((directory / 'completion.json').read_bytes())
                receipt.update({'sha256': hashlib.sha256(data).hexdigest(), 'dataset_sha256': hashlib.sha256((directory / 'dataset.json').read_bytes()).hexdigest()})
                (directory / 'completion.json').write_text(json.dumps(receipt))
                with patch.object(music_training, 'register_file') as register, self.assertRaises((MusicLoraError, ValueError)):
                    music_training._publish(child)
                register.assert_not_called()

    async def test_ar_publication_requires_usable_bound_scores_before_registration(self) -> None:
        child = await music_training.submit_planner(self.parent.id, YueLoraPairSettings())
        await music_training._tasks[child.id]
        directory = music_training._directory(child.id)
        import subprocess
        argv, cwd, environment = self.worker(child.request, directory, [self.audio], prepared_audio=())
        subprocess.run(argv, cwd=cwd, env=environment, check=True)
        original_manifest = json.loads((directory / 'dataset.json').read_bytes())
        for failure in ('missing', 'hash', 'all-rest', 'path'):
            with self.subTest(failure=failure):
                manifest = copy.deepcopy(original_manifest)
                (directory / 'audio/000.abc').write_text(SCORE)
                if failure == 'missing':
                    del manifest['samples'][0]['score'];del manifest['samples'][0]['score_sha256']
                elif failure == 'hash': manifest['samples'][0]['score_sha256'] = '0' * 64
                elif failure == 'all-rest':
                    (directory / 'audio/000.abc').write_text('X:1\nM:4/4\nL:1/8\nK:C\nz8|\n')
                    manifest['samples'][0]['score_sha256'] = hashlib.sha256((directory / 'audio/000.abc').read_bytes()).hexdigest()
                else: manifest['samples'][0]['score'] = '../000.abc'
                (directory / 'dataset.json').write_text(json.dumps(manifest))
                receipt = json.loads((directory / 'completion.json').read_bytes())
                receipt['dataset_sha256'] = hashlib.sha256((directory / 'dataset.json').read_bytes()).hexdigest()
                (directory / 'completion.json').write_text(json.dumps(receipt))
                with patch.object(music_training, 'register_file', side_effect=AssertionError('unverified score registered')), self.assertRaises(ValueError):
                    music_training._publish(child)

    async def test_cancelled_ar_job_keeps_the_saved_nar_and_can_retry(self) -> None:
        marker = self.root / 'ar-started'
        def command(request: MusicLoraTrainingRequest, directory: Path, sources: list[Path], **kwargs: object) -> tuple[list[str], Path, dict[str, str]]:
            script = self.root / 'waiting-ar-worker.py'
            script.write_text(f'import time\nfrom pathlib import Path\nPath({str(marker)!r}).write_text("ready")\ntime.sleep(60)\n')
            return [sys.executable, str(script)], directory, dict(os.environ)
        with patch.object(music_training, '_run', side_effect=self.run), patch.object(music_training, 'prepare_worker', side_effect=command):
            child = await music_training.submit_planner(self.parent.id, YueLoraPairSettings())
            async with asyncio.timeout(10):
                while not marker.exists():
                    await asyncio.sleep(.01)
            result = await music_training.cancel(child.id)
        self.assertEqual(result.status, 'cancelled')
        self.assertTrue(music_training.get(self.parent.id).can_train_missing_ar)
        self.assertEqual(music_lora.list_adapters().adapters, [self.nar])

    async def test_existing_ar_registered_to_the_parent_also_prevents_another_run(self) -> None:
        source = self.directory / 'existing-ar.safetensors';source.write_bytes(paired_checkpoint('ar'))
        music_lora.register_file(source, name=self.parent.request.name, engine='yue2', target='ar', source_job_id=self.parent.id)
        self.assertFalse(music_training.get(self.parent.id).can_train_missing_ar)
        with self.assertRaisesRegex(MusicLoraError, 'music_training_planner_exists'):
            await music_training.submit_planner(self.parent.id, YueLoraPairSettings())

    async def test_planner_route_uses_saved_source_and_existing_origin_validation(self) -> None:
        import httpx
        from fastapi import FastAPI
        from app.api.routes_music_training import router
        app = FastAPI();app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:9000') as client:
            route = f'/api/music-lora/training/jobs/{self.parent.id}/planner'
            response = await client.post(route, json={}, headers={'Origin': 'https://outside.example'})
            self.assertEqual(response.status_code, 403)
            response = await client.post(route, json={'planner_steps': True})
            self.assertEqual(response.status_code, 422)
            with patch.object(music_training, '_run', new=AsyncMock()):
                response = await client.post(route, json={'planner_steps': 5})
                self.assertEqual(response.status_code, 200, response.text)
                await music_training._tasks[response.json()['id']]
            self.assertEqual(response.json()['request']['yue_ar_source_job_id'], self.parent.id)
