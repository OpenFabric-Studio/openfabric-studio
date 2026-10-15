"""Queue delegation owns exact native runs and waits for child cleanup."""
from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import HTTPException

from app import music_queue_engines as engines
from app.contracts import AceJobReleaseResponse, AceJobResponse, SavedTrack
from app.generation_contracts import AceGenerationSettings
from app.local_music_contracts import LocalMusicJob, LocalMusicRequest
from app.local_music_contracts import LocalMusicError
from app.music_queue_contracts import (
    MusicQueueAceRequest, MusicQueueError, MusicQueueHeartRequest, MusicQueueItem,
    MusicQueueYueRequest,
)
from app.orchestrator.state import ModelStatus
from app.yue_contracts import YueJobResponse, YueOptions, YueSubmitRequest


class MusicQueueEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_timeout_remains_interrupted_until_session_is_released(self) -> None:
        item = self.item()
        with patch.object(self.session, '_wait', new=AsyncMock(side_effect=TimeoutError)), \
             patch.object(self.session, 'cancel', new=AsyncMock()) as cancel, \
             patch.object(engines, 'inspect_child', return_value=engines.EngineResult('cancelled')):
            result = await self.session.wait(item)
        cancel.assert_awaited_once_with(item)
        self.assertEqual(result.status, 'interrupted')
        self.assertEqual(result.error_code, 'music_queue_generation_timeout')

    async def asyncSetUp(self) -> None:
        self.temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temporary)
        self.events: list[str] = []
        self.instances: dict[str, str] = {}
        self.states = {engine: SimpleNamespace(status=ModelStatus.STOPPED) for engine in ('ace_step', 'yue2')}
        self.processes: dict[str, list[object]] = {}
        self.manager = Mock()
        self.manager.state.models = self.states
        self.manager._processes = self.processes
        self.manager.owned_instance.side_effect = lambda engine: self.instances.get(engine)

        async def start(engine: str) -> None:
            self.events.append('load:' + engine)
            self.instances[engine] = engine + '-instance'
            self.states[engine].status = ModelStatus.RUNNING
            self.processes[engine] = [object()]

        async def stop(engine: str, instance: str) -> None:
            self.assertEqual(instance, self.instances[engine])
            self.events.append('unload:' + engine)
            del self.instances[engine]
            del self.processes[engine]
            self.states[engine].status = ModelStatus.STOPPED

        self.manager.switch_to = AsyncMock(side_effect=start)
        self.manager.stop_owned = AsyncMock(side_effect=stop)
        self.enterContext(patch.object(engines, 'manager', self.manager))
        self.enterContext(patch.object(engines.ownership, 'busy', return_value=True))
        self.enterContext(patch.object(engines.ownership, 'blocked', return_value=False))
        self.enterContext(patch.object(engines.ownership, 'require_access'))
        self.enterContext(patch.object(engines.resource_admission, 'native_work_inflight', return_value=False))
        self.enterContext(patch.object(engines.ace_jobs, '_tasks', {}))
        self.enterContext(patch.object(engines.yue_jobs, '_tasks', {}))
        self.enterContext(patch.object(engines.yue_jobs, '_leases', {}))
        self.enterContext(patch.object(engines.native_yue, '_quarantined', []))
        self.enterContext(patch.object(engines.local_music, '_tasks', {}))
        self.enterContext(patch.object(engines.local_music, '_unverified', set()))
        self.enterContext(patch.object(engines, '_POLL_SECONDS', 0.001))
        self.real_prepare = engines._prepare_ace
        self.prepare = self.enterContext(patch.object(engines, '_prepare_ace', new=AsyncMock()))
        self.session = engines.EngineSession()

    def item(self, engine: str = 'ace_step', *, child: str | None = 'a' * 32) -> MusicQueueItem:
        if engine == 'ace_step':
            payload = MusicQueueAceRequest(engine='ace_step', request=AceGenerationSettings(engine='ace_step',
                mode='custom', customPrompt='Warm jazz', customLyrics='A lyric', duration=60,
                durationAuto=False, audioFormat='wav', seed=71, randomSeed=False, batchSize=1,
                useCotCaption=False, selectedModel='fixture-model', bpm=100, keyScale='C major', vocalLanguage='en'))
        elif engine == 'yue2':
            payload = MusicQueueYueRequest(engine='yue2', request=YueSubmitRequest(lyrics='A lyric', seed=71,
                options=YueOptions(style='Warm jazz'), precision='q4_0'))
        else:
            payload = MusicQueueHeartRequest(engine='heartmula', request=LocalMusicRequest(engine='heartmula',
                title='Fixture', prompt='Warm jazz', duration_seconds=60, seed=71))
        return MusicQueueItem(id='b' * 32, batch_id='c' * 32, engine=payload.engine,
            payload=payload, variation_index=1, resolved_seed=71, child_job_id=child)

    def ace_job(self, status: str = 'done', *, code: str = '') -> AceJobResponse:
        return AceJobResponse(task_id='a' * 32, status=status, created_at='today', title='Fixture', lyrics='',
            audio_format='wav', batch_size=1, params={}, progress=1, stage=status, error='', error_code=code,
            tracks=[], voice_id=None)

    async def test_native_group_is_loaded_once_saved_before_unload_and_switch(self) -> None:
        item = self.item()
        self.enterContext(patch.object(engines.ace_jobs, 'get_job', return_value=self.ace_job()))
        submitted = self.enterContext(patch.object(engines.ace_jobs, 'submit', new=AsyncMock(
            return_value=AceJobReleaseResponse(task_id='a' * 32, status='queued', queue_position=0))))
        self.enterContext(patch.object(engines.resource_admission, 'native_admission', return_value=_Admission()))
        for _ in range(2):
            await self.session.ensure(item.payload)
            await self.session.submit(item)
            self.assertEqual((await self.session.wait(item)).status, 'done')
            self.events.append('save:ace_step')
        await self.session.ensure(self.item('yue2').payload)
        await self.session.close()
        self.assertEqual(self.events, ['load:ace_step', 'save:ace_step', 'save:ace_step',
            'unload:ace_step', 'load:yue2', 'unload:yue2'])
        self.prepare.assert_awaited_once()
        params = submitted.call_args.args[0]
        self.assertEqual({key: params[key] for key in ('prompt', 'lyrics', 'audio_duration', 'seed',
            'use_random_seed', 'batch_size', 'audio_format', 'use_cot_caption', 'bpm', 'key_scale', 'vocal_language', 'model')},
            {'prompt': 'Warm jazz', 'lyrics': 'A lyric', 'audio_duration': 60, 'seed': 71,
             'use_random_seed': False, 'batch_size': 1, 'audio_format': 'wav', 'use_cot_caption': False,
             'bpm': 100, 'key_scale': 'C major', 'vocal_language': 'en', 'model': 'fixture-model'})
        self.assertEqual(params['_generation_settings'], item.payload.request.model_dump(mode='json'))
        self.assertIsNone(submitted.call_args.args[2])

    async def test_first_local_item_stops_existing_exact_native_instances(self) -> None:
        await self.manager.switch_to('ace_step')
        await self.manager.switch_to('yue2')
        await self.session.ensure(self.item('heartmula').payload)
        self.assertEqual(self.events[-2:], ['unload:ace_step', 'unload:yue2'])
        self.assertEqual(self.instances, {})

    async def test_terminal_metadata_waits_for_registered_worker_finally(self) -> None:
        item = self.item()
        gate = asyncio.Event()
        worker = asyncio.create_task(gate.wait())
        engines.ace_jobs._tasks[item.child_job_id] = worker
        self.enterContext(patch.object(engines.ace_jobs, 'get_job', return_value=self.ace_job()))
        waiting = asyncio.create_task(self.session.wait(item))
        await asyncio.sleep(0.01)
        self.assertFalse(waiting.done())
        self.assertIsNone(engines.inspect_child(item))
        gate.set()
        await worker
        self.assertEqual((await waiting).status, 'done')

    async def test_replacement_is_never_stopped_and_cleanup_remains_blocked(self) -> None:
        await self.session.ensure(self.item().payload)
        self.instances['ace_step'] = 'replacement'
        for _ in range(2):
            with self.assertRaises(MusicQueueError) as failed:
                await self.session.close()
            self.assertEqual(failed.exception.code, 'music_queue_cleanup_failed')
        self.manager.stop_owned.assert_not_awaited()

    async def test_failed_start_with_retained_partial_tree_blocks_cleanup(self) -> None:
        async def fail(engine: str) -> None:
            self.processes[engine] = [object()]
            self.states[engine].status = ModelStatus.ERROR
            raise RuntimeError('private startup failure')
        self.manager.switch_to.side_effect = fail
        with self.assertRaises(MusicQueueError):
            await self.session.ensure(self.item().payload)
        with self.assertRaises(MusicQueueError) as failed:
            await self.session.close()
        self.assertEqual(failed.exception.code, 'music_queue_cleanup_failed')
        self.manager.stop_owned.assert_not_awaited()

    async def test_local_quarantine_blocks_even_with_failed_published_status(self) -> None:
        item = self.item('heartmula')
        job = LocalMusicJob(id='a' * 32, status='failed', created_at='today', request=item.payload.request,
            error_code='local_music_worker_unverified')
        self.enterContext(patch.object(engines.local_music, 'get', return_value=job))
        engines.local_music._unverified.add('a' * 32)
        self.assertEqual((await self.session.wait(item)).status, 'blocked')

    async def test_yue_cancel_adopts_only_trusted_owned_restart_before_close(self) -> None:
        item = self.item('yue2')
        await self.session.ensure(item.payload)
        job = YueJobResponse(id='a' * 32, status='cancelled', stage='cancelled', created_at='today',
            title='', lyrics='A lyric', seed=71, precision='q4_0', options=YueOptions(style='Warm jazz'), voice_id=None)
        self.enterContext(patch.object(engines.yue_jobs, 'get_job', return_value=job))

        async def cancel(identifier: str) -> YueJobResponse:
            self.instances['yue2'] = 'trusted-restart'
            return job
        self.enterContext(patch.object(engines.yue_jobs, 'cancel', new=AsyncMock(side_effect=cancel)))
        await self.session.cancel(item)
        await self.session.close()
        self.manager.stop_owned.assert_awaited_once_with('yue2', 'trusted-restart')

    async def test_missing_child_is_interrupted_and_retains_known_tracks(self) -> None:
        item = self.item()
        item.tracks = [SavedTrack(id=3, short_id=None, model='ace_step', created_at='today', title='Saved',
            lyrics='', seed=71, duration_ms=1000, wall_ms=None, params={}, filename='saved.wav',
            audio_url='/files/saved.wav', abc_url=None, stems=None, midi=None)]
        self.enterContext(patch.object(engines.ace_jobs, 'get_job', side_effect=HTTPException(404, 'missing')))
        result = engines.inspect_child(item)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, 'interrupted')
        self.assertEqual(result.error_code, 'music_queue_child_missing')
        self.assertEqual(result.tracks, item.tracks)

    async def test_invalid_native_health_becomes_stable_startup_error(self) -> None:
        self.prepare.side_effect = TypeError('private error')
        with self.assertRaises(MusicQueueError) as failed:
            await self.session.ensure(self.item().payload)
        self.assertEqual(failed.exception.code, 'music_queue_engine_start_failed')
        await self.session.close()

    async def test_ace_initializes_unloaded_weights_then_clears_native_lora(self) -> None:
        requests: list[httpx.Request] = []

        def response(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == '/health':
                initialized = len(requests) > 1
                return httpx.Response(200, json={'code': 200, 'data': {'models_initialized': initialized}})
            return httpx.Response(200, json={'code': 200, 'data': {'message': 'ok'}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(response))
        with patch.object(engines.httpx, 'AsyncClient', return_value=client):
            await self.real_prepare()
        self.assertEqual([request.url.path for request in requests],
            ['/health', '/v1/init', '/health', '/v1/lora/unload'])
        self.assertEqual(requests[1].content, b'{"init_llm":false}')
        self.assertEqual(requests[3].content, b'{}')

    async def test_ace_rejects_malformed_health_without_mutating_engine(self) -> None:
        requests: list[httpx.Request] = []

        def response(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={'models_initialized': 'true'})

        client = httpx.AsyncClient(transport=httpx.MockTransport(response))
        with patch.object(engines.httpx, 'AsyncClient', return_value=client):
            self.prepare.side_effect = self.real_prepare
            with self.assertRaises(MusicQueueError) as failed:
                await self.session.ensure(self.item().payload)
        self.assertEqual(failed.exception.code, 'music_queue_engine_start_failed')
        self.assertEqual([request.url.path for request in requests], ['/health'])
        await self.session.close()

    async def test_local_request_uses_existing_async_worker_service(self) -> None:
        item = self.item('heartmula')
        await self.session.ensure(item.payload)
        job = LocalMusicJob(id='a' * 32, status='done', created_at='today', request=item.payload.request)
        submitted = self.enterContext(patch.object(engines.local_music, 'submit', new=AsyncMock(return_value=job)))
        self.enterContext(patch.object(engines.local_music, 'get', return_value=job))
        self.assertEqual(await self.session.submit(item), job.id)
        self.assertEqual((await self.session.wait(item)).status, 'done')
        await self.session.close()
        submitted.assert_awaited_once_with(item.payload.request)
        self.manager.switch_to.assert_not_awaited()

    async def test_failed_child_drain_prevents_native_stop_and_next_engine(self) -> None:
        item = self.item()
        await self.session.ensure(item.payload)
        self.enterContext(patch.object(engines.ace_jobs, 'submit', new=AsyncMock(
            return_value=AceJobReleaseResponse(task_id='a' * 32, status='queued', queue_position=0))))
        self.enterContext(patch.object(engines.resource_admission, 'native_admission', return_value=_Admission()))
        await self.session.submit(item)
        self.enterContext(patch.object(engines.ace_jobs, 'cancel', new=AsyncMock(side_effect=TimeoutError)))
        with self.assertRaises(MusicQueueError) as failed:
            await self.session.ensure(self.item('yue2').payload)
        self.assertEqual(failed.exception.code, 'music_queue_cleanup_failed')
        self.manager.stop_owned.assert_not_awaited()
        self.assertNotIn('load:yue2', self.events)

    async def test_save_retry_uses_retained_child_and_never_submits_inference(self) -> None:
        item = self.item()
        job = self.ace_job('failed', code='save_failed')
        self.enterContext(patch.object(engines.ace_jobs, 'get_job', side_effect=lambda identifier: job))

        async def save(identifier: str) -> AceJobResponse:
            job.status = 'done'
            job.error_code = ''
            return job

        retry = self.enterContext(patch.object(engines.ace_jobs, 'retry_save', new=AsyncMock(side_effect=save)))
        submit = self.enterContext(patch.object(engines.ace_jobs, 'submit', new=AsyncMock()))
        self.assertEqual((await self.session.retry_save(item)).status, 'done')
        retry.assert_awaited_once_with(item.child_job_id)
        submit.assert_not_awaited()
        self.manager.switch_to.assert_not_awaited()

    async def test_queue_rejects_advanced_ace_voice_side_effect(self) -> None:
        item = self.item()
        item.payload.request.voiceId = 'd' * 32
        await self.session.ensure(item.payload)
        submit = self.enterContext(patch.object(engines.ace_jobs, 'submit', new=AsyncMock()))
        with self.assertRaises(MusicQueueError) as failed:
            await self.session.submit(item)
        self.assertEqual(failed.exception.code, 'music_queue_plan_invalid')
        submit.assert_not_awaited()
        await self.session.close()

    async def test_cancelled_save_retry_is_still_owned_until_close_drains_it(self) -> None:
        item = self.item()
        job = self.ace_job('failed', code='save_failed')
        entered = asyncio.Event()
        worker_gate = asyncio.Event()
        worker: asyncio.Task[None] | None = None
        self.enterContext(patch.object(engines.ace_jobs, 'get_job', side_effect=lambda identifier: job))

        async def retry(identifier: str) -> AceJobResponse:
            nonlocal worker
            worker = asyncio.create_task(worker_gate.wait())
            engines.ace_jobs._tasks[identifier] = worker
            job.status = 'running'
            entered.set()
            return job

        async def cancel(identifier: str) -> AceJobResponse:
            worker_gate.set()
            if worker is not None:
                await worker
            job.status = 'cancelled'
            return job

        self.enterContext(patch.object(engines.ace_jobs, 'retry_save', new=AsyncMock(side_effect=retry)))
        cancelled = self.enterContext(patch.object(engines.ace_jobs, 'cancel', new=AsyncMock(side_effect=cancel)))
        saving = asyncio.create_task(self.session.retry_save(item))
        await entered.wait()
        saving.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await saving
        try:
            await self.session.close()
            cancelled.assert_awaited_once_with(item.child_job_id)
        finally:
            worker_gate.set()
            if worker is not None:
                await worker

    async def test_submit_cancellation_during_admission_exit_keeps_child_owned(self) -> None:
        item = self.item(child=None)
        await self.session.ensure(item.payload)
        entered = asyncio.Event()
        gate = asyncio.Event()

        class SlowExit(_Admission):
            async def __aexit__(self, *arguments: object) -> None:
                entered.set()
                await gate.wait()

        self.enterContext(patch.object(engines.resource_admission, 'native_admission', return_value=SlowExit()))
        self.enterContext(patch.object(engines.ace_jobs, 'submit', new=AsyncMock(
            return_value=AceJobReleaseResponse(task_id='a' * 32, status='queued', queue_position=0))))
        self.enterContext(patch.object(engines.ace_jobs, 'get_job', return_value=self.ace_job('cancelled')))
        cancel = self.enterContext(patch.object(engines.ace_jobs, 'cancel', new=AsyncMock()))
        submitting = asyncio.create_task(self.session.submit(item))
        await entered.wait()
        submitting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await submitting
        await self.session.close()
        cancel.assert_awaited_once_with('a' * 32)
        self.assertEqual(self.events[-1], 'unload:ace_step')

    async def test_safe_local_rejection_is_normalized_without_internal_error(self) -> None:
        item = self.item('heartmula')
        await self.session.ensure(item.payload)
        self.enterContext(patch.object(engines.local_music, 'submit', new=AsyncMock(
            side_effect=LocalMusicError('local_music_weights_missing', 409))))
        with self.assertRaises(MusicQueueError) as rejected:
            await self.session.submit(item)
        self.assertEqual(rejected.exception.code, 'local_music_weights_missing')
        self.assertEqual(rejected.exception.status, 409)

    async def test_native_ambiguous_submission_is_interrupted_and_not_retried(self) -> None:
        item = self.item()
        await self.session.ensure(item.payload)
        self.enterContext(patch.object(engines.resource_admission, 'native_admission', return_value=_Admission()))
        submit = self.enterContext(patch.object(engines.ace_jobs, 'submit', new=AsyncMock(
            side_effect=TypeError('Malformed native success response: inference may exist'))))
        with self.assertRaises(MusicQueueError) as interrupted:
            await self.session.submit(item)
        self.assertEqual(interrupted.exception.code, 'music_queue_submission_interrupted')
        submit.assert_awaited_once()

    async def test_ace_preflight_rejection_preserves_safe_code_without_ambiguous_submission(self) -> None:
        item = self.item()
        await self.session.ensure(item.payload)
        self.enterContext(patch.object(engines.resource_admission, 'native_admission', return_value=_Admission()))
        for status, code, expected in ((409, 'ace_lora_selection_mismatch', 'ace_lora_selection_mismatch'),
                                      (502, 'ace_lora_status_unavailable', 'ace_lora_status_unavailable'),
                                      (503, 'model_inactive', 'ace_lora_model_inactive')):
            with self.subTest(code=code), patch.object(engines.ace_jobs, 'submit', new=AsyncMock(
                    side_effect=HTTPException(status, code))) as submit:
                with self.assertRaises(MusicQueueError) as rejected:
                    await self.session.submit(item)
                self.assertEqual(rejected.exception.code, expected)
                self.assertEqual(rejected.exception.status, status)
                self.assertEqual(self.session._children, {})
                submit.assert_awaited_once()

    async def test_unknown_ace_http_failure_stays_interrupted_without_raw_details(self) -> None:
        item = self.item()
        await self.session.ensure(item.payload)
        self.enterContext(patch.object(engines.resource_admission, 'native_admission', return_value=_Admission()))
        for detail in ('/private/model/path', {'private': '/secret/path'}):
            with self.subTest(detail=detail), patch.object(engines.ace_jobs, 'submit', new=AsyncMock(
                    side_effect=HTTPException(502, detail))) as submit:
                with self.assertRaises(MusicQueueError) as interrupted:
                    await self.session.submit(item)
                self.assertEqual(interrupted.exception.code, 'music_queue_submission_interrupted')
                self.assertNotIn('private', str(interrupted.exception))
                self.assertNotIn('secret', str(interrupted.exception))
                self.assertEqual(self.session._children, {})
                submit.assert_awaited_once()

    async def test_recovery_waits_for_existing_delayed_publication_without_starting_work(self) -> None:
        item = self.item()
        job = self.ace_job('running')
        gate = asyncio.Event()

        async def publication() -> None:
            await gate.wait()
            job.status = 'done'

        worker = asyncio.create_task(publication())
        engines.ace_jobs._tasks['a' * 32] = worker
        self.enterContext(patch.object(engines.ace_jobs, 'get_job', side_effect=lambda identifier: job))
        submit = self.enterContext(patch.object(engines.ace_jobs, 'submit', new=AsyncMock()))
        recovering = asyncio.create_task(engines.recover_child(item))
        try:
            await asyncio.sleep(0.01)
            self.assertFalse(recovering.done())
            gate.set()
            result = await recovering
            self.assertEqual(result.status, 'done')
            submit.assert_not_awaited()
            self.manager.switch_to.assert_not_awaited()
        finally:
            gate.set()
            await worker

    async def test_recovery_timeout_blocks_without_cancelling_owned_publication(self) -> None:
        item = self.item()
        gate = asyncio.Event()
        worker = asyncio.create_task(gate.wait())
        engines.ace_jobs._tasks['a' * 32] = worker
        with patch.object(engines, '_RECOVERY_SECONDS', 0.001):
            try:
                result = await engines.recover_child(item)
                self.assertEqual(result.status, 'blocked')
                self.assertEqual(result.error_code, 'music_queue_cleanup_failed')
                self.assertFalse(worker.done())
            finally:
                gate.set()
                await worker

    async def test_missing_ace_retained_results_is_safe_retry_error_and_releases_cleanly(self) -> None:
        item = self.item()
        self.enterContext(patch.object(engines.ace_jobs, 'get_job', return_value=self.ace_job('failed', code='save_failed')))
        self.enterContext(patch.object(engines.ace_jobs, 'retry_save', new=AsyncMock(
            side_effect=HTTPException(409, 'no_results_to_retry'))))
        cancel = self.enterContext(patch.object(engines.ace_jobs, 'cancel', new=AsyncMock()))
        with self.assertRaises(MusicQueueError) as rejected:
            await self.session.retry_save(item)
        self.assertEqual(rejected.exception.code, 'music_queue_save_retry_unavailable')
        await self.session.close()
        cancel.assert_not_awaited()
        self.manager.stop_owned.assert_not_awaited()

    async def test_missing_local_retained_job_is_safe_retry_error(self) -> None:
        item = self.item('heartmula')
        job = LocalMusicJob(id='a' * 32, status='failed', created_at='today', request=item.payload.request,
            error_code='local_music_save_failed')
        self.enterContext(patch.object(engines.local_music, 'get', return_value=job))
        self.enterContext(patch.object(engines.local_music, 'recover', new=AsyncMock(
            side_effect=LocalMusicError('local_music_job_not_found', 404))))
        cancel = self.enterContext(patch.object(engines.local_music, 'cancel', new=AsyncMock()))
        with self.assertRaises(MusicQueueError) as rejected:
            await self.session.retry_save(item)
        self.assertEqual(rejected.exception.code, 'music_queue_save_retry_unavailable')
        await self.session.close()
        cancel.assert_not_awaited()


class _Admission:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *arguments: object) -> None:
        return None


if __name__ == '__main__':
    unittest.main()
