from __future__ import annotations

import tempfile
import asyncio
from collections.abc import Callable
from io import BytesIO
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException, UploadFile

from app.voice_contracts import VoiceComparisonRequest, VoiceComparisonResponse, VoiceComparisonTrial, VoiceTrialRating
from app.voice_comparisons import ComparisonJob
from app.voice_build import VoiceProcessSlot


def response() -> VoiceComparisonResponse:
    return VoiceComparisonResponse(id='b'*32, voice_id='a'*32, status='done', source_filename='held-out.wav', request=VoiceComparisonRequest(source_id='c'*32, model_ids=['base']), trials=[VoiceComparisonTrial(id='0', model_id='base', reference_id='published', diffusion_steps=30, status='done')])


class ComparisonPersistenceTests(unittest.TestCase):
    def test_saved_trial_is_recovered_without_browser_state(self) -> None:
        from app import voice_comparisons as trials
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(trials, '_voice_path', return_value=root):
                trials.persist_response(root, response())
                self.assertEqual(trials.list_comparisons('a'*32).comparisons[0].id, 'b'*32)

    def test_interrupted_job_keeps_completed_outputs(self) -> None:
        from app import voice_comparisons as trials
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            saved = response()
            saved.status = 'running'
            with patch.object(trials, '_voice_path', return_value=root):
                trials.persist_response(root, saved)
                recovered = trials.get_comparison('a'*32, saved.id)
                self.assertEqual(recovered.status, 'failed')
                self.assertEqual(recovered.error_code, 'interrupted')
                self.assertEqual(recovered.trials[0].status, 'done')

    def test_rating_is_persisted_for_one_existing_completed_trial(self) -> None:
        from app import voice_comparisons as trials
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(trials, '_voice_path', return_value=root):
                trials.persist_response(root, response())
                rating = VoiceTrialRating(identity=4, pitch=3, intelligibility=5, artifacts=2)
                trials.rate_trial('a'*32, 'b'*32, '0', rating)
                self.assertEqual(trials.get_comparison('a'*32, 'b'*32).trials[0].rating, rating)

    def test_invalid_job_id_is_rejected_before_filesystem_access(self) -> None:
        from app import voice_comparisons as trials
        with self.assertRaises(HTTPException) as failure:
            trials.get_comparison('a'*32, '../../outside')
        self.assertEqual(getattr(failure.exception, 'status_code', None), 404)


class ComparisonCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_probe_duration_is_rejected_and_partial_is_removed(self) -> None:
        from app import voice_comparisons as trials
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(trials, '_voice_path', return_value=root), patch('app.voice_build._probe_duration', new=AsyncMock(return_value=float('nan'))):
                with self.assertRaises(HTTPException) as failure:
                    await trials.add_source('a'*32, UploadFile(file=BytesIO(b'audio'), filename='test.wav'))
                self.assertEqual(failure.exception.status_code, 422)
                self.assertFalse(list((root/'trials'/'sources').iterdir()))

    async def test_trial_upload_is_stored_outside_training_recordings(self) -> None:
        from app import voice_comparisons as trials
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(trials, '_voice_path', return_value=root), patch('app.voice_build._probe_duration', new=AsyncMock(return_value=3.0)):
                source = await trials.add_source('a'*32, UploadFile(file=BytesIO(b'audio'), filename='../song.wav'))
                self.assertEqual(source.filename, 'song.wav')
                self.assertEqual(trials.source_path(root, source.id).read_bytes(), b'audio')
                self.assertFalse((root/'recordings').exists())

    async def test_trial_upload_rejects_symlink_destination_escape(self) -> None:
        from app import voice_comparisons as trials
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'voice'
            root.mkdir()
            outside = Path(temporary)/'outside'
            outside.mkdir()
            (root/'trials').symlink_to(outside, target_is_directory=True)
            with patch.object(trials, '_voice_path', return_value=root), patch('app.voice_build._probe_duration', new=AsyncMock(return_value=3.0)):
                with self.assertRaises(HTTPException):
                    await trials.add_source('a'*32, UploadFile(file=BytesIO(b'audio'), filename='song.wav'))
                self.assertFalse(list(outside.iterdir()))

    async def test_cancel_before_job_coroutine_starts_releases_registry(self) -> None:
        from app import voice_comparisons as trials
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            saved = response()
            saved.status = 'queued'
            saved.trials[0].status = 'queued'
            job = trials.ComparisonJob(response=saved, path=root/'trials'/'comparisons'/saved.id)
            job.task = asyncio.create_task(asyncio.Event().wait())
            with patch.object(trials, '_voice_path', return_value=root), patch.dict(trials._jobs, {(saved.voice_id, saved.id): job}, clear=True):
                trials.persist_response(root, saved)
                cancelled = await trials.cancel_comparison(saved.voice_id, saved.id)
                self.assertEqual(cancelled.status, 'cancelled')
                self.assertEqual(cancelled.trials[0].status, 'cancelled')
                self.assertFalse(trials.work_busy())


class ComparisonLifecycleRegressions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app import voice_build as voice
        from app.orchestrator import process
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / ('a' * 32)
        self.path.mkdir()
        from app.atomic_files import write_object
        write_object(self.path / 'voice.json', {'id': 'a' * 32, 'name': 'Test', 'created_at': 'now', 'status': 'idle', 'recordings': []})
        self.logs = self.root / 'logs'
        self.logs.mkdir()
        self.patches = [patch.object(voice, 'VOICES_DIR', self.root), patch.object(process, 'LOG_DIR', self.logs),
            patch('app.seed_vc_compat.ensure_compatibility'), patch.object(voice, 'ensure_whisper_float32_off_cuda')]
        for context in self.patches:
            context.start()

    async def asyncTearDown(self) -> None:
        from app import voice_comparisons as trials
        tasks = [job.task for job in trials._jobs.values() if job.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        trials._jobs.clear()
        for context in reversed(self.patches):
            context.stop()
        self.temp.cleanup()

    def job(self, identifier: str = 'b' * 32) -> ComparisonJob:
        from app import voice_comparisons as trials
        saved = response()
        saved.id, saved.status, saved.trials[0].status = identifier, 'queued', 'queued'
        job = trials.ComparisonJob(saved, self.path / 'trials/comparisons' / identifier)
        trials._jobs[(saved.voice_id, identifier)] = job
        return job

    async def test_persistence_failure_always_removes_work_and_registry(self) -> None:
        from app import voice_comparisons as trials
        job = self.job()
        job.response.request = self.publish_inputs()
        (job.path / 'work').mkdir(parents=True)
        with patch.object(trials, 'persist_response', side_effect=OSError('disk unavailable')):
            with self.assertLogs(trials.logger, level='ERROR'):
                await trials._run(job)
        self.assertFalse(trials._jobs)
        self.assertFalse((job.path / 'work').exists())

    async def test_cancelled_cancel_caller_finalizes_prestart_job(self) -> None:
        from app import voice_comparisons as trials
        job = self.job()
        job.task = asyncio.create_task(asyncio.Event().wait())
        trials.persist_response(self.path, job.response)
        controller = asyncio.create_task(trials._cancel(job))
        await asyncio.sleep(0)
        controller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await controller
        self.assertFalse(trials._jobs)
        self.assertEqual(job.response.status, 'cancelled')

    async def test_shutdown_cancels_queued_tasks_before_any_can_start(self) -> None:
        from app import voice_comparisons as trials
        starts: list[str] = []
        async def queued(identifier: str) -> None:
            starts.append(identifier)
            await asyncio.Event().wait()
        for identifier in ['b' * 32, 'd' * 32]:
            job = self.job(identifier)
            job.task = asyncio.create_task(queued(identifier))
        await trials.shutdown()
        self.assertEqual(starts, [])
        self.assertFalse(trials._jobs)

    async def test_cancelled_release_waits_for_every_active_cleanup(self) -> None:
        from app import voice_comparisons as trials
        cleanup_started = [asyncio.Event(), asyncio.Event()]
        allow_cleanup = asyncio.Event()
        async def active(index: int) -> None:
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started[index].set()
                await allow_cleanup.wait()
        jobs = []
        for index, identifier in enumerate(['b' * 32, 'd' * 32]):
            job = self.job(identifier)
            job.response.status = 'running'
            job.task = asyncio.create_task(active(index))
            jobs.append(job)
        await asyncio.sleep(0)
        controller = asyncio.create_task(trials.release_voice('a' * 32))
        await asyncio.gather(*(event.wait() for event in cleanup_started))
        controller.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        try:
            self.assertFalse(controller.done(), 'release returned before owned cleanup finished')
        finally:
            allow_cleanup.set()
            with self.assertRaises(asyncio.CancelledError):
                await controller
            await asyncio.gather(*(job.task for job in jobs), return_exceptions=True)
        self.assertFalse(trials._jobs)

    def publish_inputs(self) -> VoiceComparisonRequest:
        from app import voice_comparisons as trials
        from app.voice_artifacts import StoredModel, publish
        from app.voice_contracts import VoicePreparationResponse, VoiceReferenceCandidate, VoiceTrialSource
        from app.voice_preparation import PreparationDocument, SampleFiles, save
        models = []
        for index in range(2):
            for suffix in ['pth', 'yml', 'wav']:
                (self.path / f'model_{index}.{suffix}').write_bytes(f'original {index} {suffix}'.encode())
            models.append(StoredModel(id=f'model_{index}', steps=200 + index * 300, kind='trained',
                checkpoint=f'model_{index}.pth', config=f'model_{index}.yml', reference=f'model_{index}.wav', preview=f'model_{index}.wav'))
        publish(self.path, models, 'model_0')
        refs = [VoiceReferenceCandidate(id=f'ref_{index}', segment_id=f'segment_{index}', source_filename='original.wav',
            start_sec=0, end_sec=10, duration_sec=10, score=.7) for index in range(2)]
        save(self.path, PreparationDocument(response=VoicePreparationResponse(revision='revision', status='done', references=refs),
            references={f'ref_{index}': SampleFiles(original=f'model_{index}.wav') for index in range(2)}))
        folder = self.path / 'trials/sources'
        folder.mkdir(parents=True)
        source_id = 'c' * 32
        (folder / f'{source_id}.wav').write_bytes(b'held-out original')
        trials._write(self.path / 'trials/sources.json', trials.SourceRegistry(sources=[trials.StoredSource(
            source=VoiceTrialSource(id=source_id, filename='held-out.wav', duration_sec=20), extension='wav')]))
        return VoiceComparisonRequest(source_id=source_id, input_kind='vocal', model_ids=['model_0', 'model_1'],
            reference_ids=['ref_0', 'ref_1'], diffusion_steps=[30, 50], duration_sec=10, seed=314)

    async def orchestration(self, cancel: bool) -> None:
        import json
        from app import voice_build as voice
        from app import voice_comparisons as trials
        request = self.publish_inputs()
        originals = {file: file.read_bytes() for file in self.path.glob('model_*')}
        calls: list[list[str]] = []
        blocked = asyncio.Event()
        started = asyncio.Event()
        inference_count = 0
        async def ffmpeg(args: list[str], slot: VoiceProcessSlot, cancelled: Callable[[], bool], timeout: float = 1800) -> str:
            self.assertIn('-c:a', args)
            self.assertEqual(args[args.index('-c:a') + 1], 'pcm_f32le')
            Path(args[-1]).write_bytes(Path(args[args.index('-i') + 1]).read_bytes())
            return ''
        async def spawn(command: list[str], *, cwd: Path, log_name: str, slot: VoiceProcessSlot) -> int:
            nonlocal inference_count
            calls.append(command.copy())
            if '--mono' in command:
                Path(command[-1]).write_bytes(Path(command[-2]).read_bytes())
            elif command[1] == 'inference.py':
                inference_count += 1
                if cancel and inference_count == 2:
                    started.set()
                    await blocked.wait()
                # The running job must use copied model/reference inputs.
                for flag in ['--source', '--target', '--checkpoint', '--config']:
                    value = Path(command[command.index(flag) + 1])
                    self.assertTrue(value.is_relative_to(self.path / 'trials/comparisons'))
                output = Path(command[command.index('--output') + 1])
                (output / 'vc_result.wav').write_bytes(b'converted vocal')
            else:
                payload = {'duration_sec': 10, 'duration_delta_sec': 0, 'level_db': -16, 'peak': .5, 'clipped_fraction': 0}
                (self.logs / f'{log_name}.log').write_text(json.dumps(payload))
            return 0
        with patch.object(voice, '_spawn', side_effect=spawn), patch.object(voice, '_ffmpeg', side_effect=ffmpeg), patch.object(voice, '_probe_duration', new=AsyncMock(return_value=10)):
            result = trials.start_comparison('a' * 32, request)
            task = trials._jobs[('a' * 32, result.id)].task
            if cancel:
                await started.wait()
                restored = await trials.cancel_comparison('a' * 32, result.id)
                self.assertEqual(restored.status, 'cancelled')
                self.assertEqual(restored.trials[0].status, 'done')
                self.assertIsNotNone(restored.trials[0].metrics)
                self.assertEqual(trials.audio_path('a' * 32, result.id, '0').read_bytes(), b'converted vocal')
                self.assertTrue(all(trial.status == 'cancelled' for trial in restored.trials[1:]))
            else:
                await task
                restored = trials.get_comparison('a' * 32, result.id)
                inference = [call for call in calls if call[1] == 'inference.py']
                self.assertEqual(len(inference), 8)
                self.assertEqual(len([call for call in calls if '--mono' in call]), 3)
                self.assertEqual(len({call[call.index('--source') + 1] for call in inference}), 1)
                self.assertEqual(len({call[call.index('--target') + 1] for call in inference}), 2)
                self.assertTrue(all(call[call.index('--seed') + 1] == '314' for call in inference))
                self.assertEqual({(trial.model_id, trial.reference_id, trial.diffusion_steps) for trial in restored.trials},
                    {(model, ref, steps) for model in ['model_0', 'model_1'] for ref in ['ref_0', 'ref_1'] for steps in [30, 50]})
                self.assertTrue(all(trial.status == 'done' and trial.metrics is not None for trial in restored.trials))
        for file, content in originals.items():
            self.assertEqual(file.read_bytes(), content)
        self.assertFalse((self.path / 'trials/comparisons' / result.id / 'work').exists())
        self.assertFalse(trials.work_busy())

    async def test_all_combinations_share_phase_safe_immutable_inputs(self) -> None:
        await self.orchestration(cancel=False)

    async def test_queued_trial_keeps_reference_paths_when_preparation_is_replaced(self) -> None:
        from app import voice_comparisons as trials
        from app.voice_contracts import VoicePreparationResponse
        from app.voice_preparation import PreparationDocument, save
        request = self.publish_inputs()
        result = trials.start_comparison('a' * 32, request)
        job = trials._jobs[('a' * 32, result.id)]
        save(self.path, PreparationDocument(response=VoicePreparationResponse()))
        try:
            _, references = trials._snapshot(job)
            self.assertEqual(set(references), {'ref_0', 'ref_1'})
            self.assertEqual(references['ref_0'].read_bytes(), b'original 0 wav')
        finally:
            await trials._cancel(job)

    async def test_queued_trial_keeps_published_reference_when_active_model_changes(self) -> None:
        from app import voice_comparisons as trials
        from app.voice_artifacts import activate
        request = self.publish_inputs().model_copy(update={'reference_ids': []})
        result = trials.start_comparison('a' * 32, request)
        job = trials._jobs[('a' * 32, result.id)]
        activate(self.path, 'model_1')
        try:
            _, references = trials._snapshot(job)
            self.assertEqual(references['published'].read_bytes(), b'original 0 wav')
        finally:
            await trials._cancel(job)

    async def test_cancel_retains_completed_trial_audio_and_metrics(self) -> None:
        await self.orchestration(cancel=True)
