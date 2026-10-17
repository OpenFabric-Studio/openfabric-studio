from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI, Request, Response
import httpx

from app import music_queue_plan, music_queue_store, music_terms
from app.api import (
    routes_ace_jobs, routes_local_music, routes_midi, routes_modules, routes_music_queue,
    routes_music_training, routes_proxy, routes_references, routes_stable_lora,
    routes_yue_jobs,
)
from app.local_music_contracts import LocalMusicJob, LocalMusicRequest
from app.client_contracts import MidiStatusResponse
from app.music_queue_contracts import (
    MusicQueueBatch, MusicQueueCatalog, MusicQueueEnqueueRequest, MusicQueueItem,
    MusicQueuePreviewRequest, MusicQueueReadiness, MusicQueueSnapshot,
)
from app.orchestrator.state import ModelRuntimeState, ModelStatus
from app.reference_contracts import ReferenceImport, ReferenceImportRequest, ReferenceStage
from app.resource_admission import NativeLease


class MusicTermsAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        application = FastAPI()
        for router in (routes_ace_jobs.router, routes_local_music.router, routes_midi.router, routes_modules.router,
                       routes_music_queue.router, routes_music_training.router, routes_proxy.router,
                       routes_references.router, routes_stable_lora.router, routes_yue_jobs.router):
            application.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
            base_url='http://127.0.0.1:9000')
        self.addAsyncCleanup(self.client.aclose)
        self.denied = self.enterContext(patch.object(music_terms, 'require',
            side_effect=music_terms.MusicTermsError('music_terms_required')))

    async def test_new_generation_training_and_melody_requests_stop_before_workers(self) -> None:
        sample = {'track_id': 1, 'caption': 'Percussion'}
        training = {'engine': 'yue2', 'name': 'Test', 'samples': [sample]}
        cases: list[tuple[str, dict[str, object], str, str, str]] = [
            ('/api/yue-jobs', {'lyrics': 'Verse', 'seed': 1, 'options': {'style': 'Jazz'}}, 'app.yue_jobs.submit', 'yue2', 'generation'),
            ('/api/local-music/jobs', {'engine': 'stable_audio3', 'prompt': 'Jazz', 'duration_seconds': 10}, 'app.local_music.submit', 'stable_audio3', 'generation'),
            ('/api/music-lora/stable-training/jobs', {'name': 'Test', 'samples': [sample]}, 'app.stable_lora.submit', 'stable_audio3', 'training'),
            ('/api/music-lora/training/jobs', training, 'app.music_training.submit', 'yue2', 'training'),
            ('/api/music-lora/training/packages', training, 'app.music_training.submit', 'yue2', 'training'),
            ('/api/music-lora/training/jobs/' + 'a' * 32 + '/planner', {}, 'app.music_training.submit_planner', 'yue2', 'training'),
            ('/api/references/imports', {'url': 'https://youtube.com/watch?v=abcdefghijk', 'melody': True}, 'app.reference_imports.create', 'yue2', 'transcription'),
            ('/api/references/prepare', {'track_id': 1, 'melody': True}, 'app.reference_imports.prepare', 'yue2', 'transcription'),
        ]
        for path, body, worker, engine, operation in cases:
            self.denied.reset_mock()
            with self.subTest(path=path), patch(worker, side_effect=AssertionError('Worker started before terms admission')) as submit:
                response = await self.client.post(path, json=body)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json(), {'detail': 'music_terms_required'})
                self.denied.assert_called_once_with(engine, operation)
                submit.assert_not_called()

    async def test_primary_ace_submission_uses_the_same_generation_gate_as_proxy_and_queue(self) -> None:
        with patch.object(routes_ace_jobs.ace_jobs, 'submit', new=AsyncMock()) as submit:
            response = await self.client.post('/api/ace-jobs', data={'params': '{}', 'title': 'Test'})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json(), {'detail': 'music_terms_required'})
        self.denied.assert_called_once_with('ace_step', 'generation')
        submit.assert_not_awaited()

    async def test_module_code_setup_is_available_but_selected_weights_require_terms(self) -> None:
        for downloads in (False, True):
            with self.subTest(downloads=downloads), patch.object(routes_modules, 'service') as service:
                service.return_value.create = AsyncMock()
                response = await self.client.post('/api/modules/jobs', json={
                    'features': ['yue2'], 'download_models': downloads, 'plan_token': 'a' * 64})
                if downloads:
                    self.assertEqual(response.status_code, 409, response.text)
                    service.return_value.create.assert_not_called()
                else:
                    service.return_value.create.assert_awaited_once()
        self.denied.assert_called_once_with('yue2', 'setup')

    async def test_raw_native_tasks_use_normalized_endpoint_and_actual_model_family(self) -> None:
        state = ModelRuntimeState('yue2')
        state.status = ModelStatus.RUNNING
        with patch.object(routes_proxy.manager.state, 'models', {'yue2': state}), \
                patch.object(routes_proxy, '_proxy_to', new=AsyncMock(return_value=Response('ok'))) as forward:
            for endpoint in ('v1/tasks/run', 'v1//tasks/./run', 'v1%2ftasks%2frun'):
                for model, operation in (('yue2', 'generation'), ('sheetsage2', 'transcription'), ('muscriptor', 'transcription')):
                    with self.subTest(endpoint=endpoint, model=model):
                        self.denied.reset_mock()
                        response = await self.client.post('/api/yue2/' + endpoint, json={'model': model, 'request': {}})
                        self.assertEqual(response.status_code, 409, response.text)
                        self.denied.assert_called_once_with('yue2', operation)
            forward.assert_not_awaited()

    async def test_saved_jobs_are_readable_and_cancellable_after_terms_are_disabled(self) -> None:
        request = LocalMusicRequest(engine='stable_audio3', prompt='Jazz', duration_seconds=10)
        job = LocalMusicJob(id='a' * 32, status='done', created_at='2026-10-17', request=request)
        with patch('app.local_music.get', return_value=job), \
                patch('app.local_music.cancel', new=AsyncMock(return_value=job)):
            self.assertEqual((await self.client.get('/api/local-music/jobs/' + job.id)).status_code, 200)
            self.assertEqual((await self.client.post('/api/local-music/jobs/' + job.id + '/cancel')).status_code, 200)
        self.denied.assert_not_called()

    async def test_midi_transcription_is_admitted_before_a_worker_starts(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        source = root / 'source.wav'
        source.write_bytes(b'fixture')
        state = ModelRuntimeState('yue2')
        state.status = ModelStatus.RUNNING
        with patch.object(routes_midi.db, 'get_track', return_value={'id': 1}), \
                patch.object(routes_midi.manager.state, 'models', {'yue2': state}), \
                patch.object(routes_midi.midi, 'source_audio_path', return_value=source), \
                patch.object(routes_midi.midi, '_jobs', {}), \
                patch.object(routes_midi.midi, '_run', new=AsyncMock()) as worker:
            response = await self.client.post('/api/tracks/1/midi/full')
        self.assertEqual(response.status_code, 409, response.text)
        self.denied.assert_called_once_with('yue2', 'transcription')
        worker.assert_not_called()

    async def test_midi_post_reconnects_to_existing_work_without_terms_reacceptance(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        source = root / 'source.wav'
        source.write_bytes(b'fixture')
        engine = ModelRuntimeState('yue2')
        engine.status = ModelStatus.RUNNING
        payload = MidiStatusResponse.model_validate({'sources': {name: {'status': 'done', 'error': None}
            for name in routes_midi.midi.SOURCES}, 'urls': {}, 'available': ['full']})
        with patch.object(routes_midi.db, 'get_track', return_value={'id': 1}), \
                patch.object(routes_midi.manager.state, 'models', {'yue2': engine}), \
                patch.object(routes_midi.midi, 'source_audio_path', return_value=source), \
                patch.object(routes_midi, '_payload', return_value=payload), \
                patch.object(routes_midi.midi, '_run', new=AsyncMock()) as worker:
            for status in ('queued', 'running', 'done'):
                prior = routes_midi.midi.MidiJob(status=status)
                with self.subTest(status=status), patch.object(routes_midi.midi, '_jobs', {(1, 'full'): prior}):
                    response = await self.client.post('/api/tracks/1/midi/full')
                    self.assertEqual(response.status_code, 200, response.text)
            self.denied.assert_not_called()
            worker.assert_not_called()
            prior = routes_midi.midi.MidiJob(status='done')
            with patch.object(routes_midi.midi, '_jobs', {(1, 'full'): prior}):
                response = await self.client.post('/api/tracks/1/midi/full?force=true')
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIs(routes_midi.midi._jobs[(1, 'full')], prior)
                worker.assert_not_called()

    async def test_queue_enqueue_and_inference_retry_require_the_selected_engine_terms(self) -> None:
        body = MusicQueuePreviewRequest.model_validate({'brief': {'description': 'Jazz', 'lyrics': 'Verse', 'duration_seconds': 30},
            'selections': [{'engine': 'yue2'}]})
        ready = MusicQueueReadiness(engine='yue2', supported=True, installed=True, weights_ready=True,
            managed_status='stopped', ready=True)
        with patch.object(music_queue_plan, '_weights_ready', return_value=True), \
                patch.object(music_queue_plan, 'catalog', new=AsyncMock(return_value=MusicQueueCatalog(engines=[ready]))):
            preview = await music_queue_plan.preview(body)
        item = MusicQueueItem(**preview.items[0].model_dump(), id='b' * 32, batch_id='a' * 32,
            resolved_seed=1, status='failed')
        state = MusicQueueSnapshot(revision=7, status='paused', batches=[MusicQueueBatch(id=item.batch_id,
            created_at='2026-10-17', brief=body.brief, selections=body.selections, models=preview.models,
            preview_fingerprint=preview.fingerprint, items=[item])])
        for path, payload in (('/retry', {'revision': 7, 'item_id': item.id, 'mode': 'inference'}),):
            self.denied.reset_mock()
            with self.subTest(path=path), patch.object(routes_music_queue.music_queue, 'snapshot', return_value=state), \
                    patch.object(routes_music_queue.music_queue, 'enqueue', new=AsyncMock()) as enqueue, \
                    patch.object(routes_music_queue.music_queue, 'retry', new=AsyncMock()) as retry:
                response = await self.client.post('/api/music-queue' + path, json=payload)
                self.assertEqual(response.status_code, 409, response.text)
                self.denied.assert_called_once_with('yue2', 'generation')
                enqueue.assert_not_awaited()
                retry.assert_not_awaited()
        self.denied.reset_mock()
        with patch.object(routes_music_queue.music_queue, 'retry', new=AsyncMock(return_value=state)) as retry:
            response = await self.client.post('/api/music-queue/retry', json={
                'revision': 7, 'item_id': item.id, 'mode': 'save'})
            self.assertEqual(response.status_code, 200, response.text)
            retry.assert_awaited_once()
        self.denied.assert_not_called()

    async def test_queue_network_retry_reattaches_accepted_work_but_fresh_submission_is_denied(self) -> None:
        body = MusicQueueEnqueueRequest.model_validate({'brief': {'description': 'Jazz', 'lyrics': 'Verse', 'duration_seconds': 30},
            'selections': [{'engine': 'yue2'}], 'revision': 7, 'submission_key': 'already-accepted',
            'preview_fingerprint': 'a' * 64})
        state = MusicQueueSnapshot(revision=7, status='paused')
        self.denied.side_effect = music_terms.MusicTermsError('music_noncommercial_disabled')
        with patch.object(music_queue_store, 'lookup_submission', return_value=state), \
                patch.object(music_queue_store, 'enqueue') as mutate, \
                patch.object(routes_music_queue.music_queue, 'preview', new=AsyncMock()) as preview:
            response = await self.client.post('/api/music-queue/batches', json=body.model_dump(mode='json'))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), state.model_dump(mode='json'))
            self.denied.assert_not_called()
            preview.assert_not_awaited()
            mutate.assert_not_called()
        with patch.object(music_queue_store, 'lookup_submission', return_value=None), \
                patch.object(music_queue_store, 'enqueue') as mutate, \
                patch.object(routes_music_queue.music_queue, 'preview', new=AsyncMock()) as preview:
            response = await self.client.post('/api/music-queue/batches', json=body.model_dump(mode='json'))
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json(), {'detail': 'music_noncommercial_disabled'})
            preview.assert_not_awaited()
            mutate.assert_not_called()

    async def test_queue_retry_keeps_missing_item_and_revision_errors(self) -> None:
        state = MusicQueueSnapshot(revision=7, status='paused')
        for revision, status, code in ((6, 409, 'music_queue_revision_conflict'), (7, 404, 'music_queue_item_missing')):
            with self.subTest(revision=revision), patch.object(routes_music_queue.music_queue, 'snapshot', return_value=state), \
                    patch.object(routes_music_queue.music_queue, 'retry', new=AsyncMock()) as retry:
                response = await self.client.post('/api/music-queue/retry', json={
                    'revision': revision, 'item_id': 'a' * 32, 'mode': 'inference'})
                self.assertEqual(response.status_code, status, response.text)
                self.assertEqual(response.json(), {'detail': code})
                retry.assert_not_awaited()
        self.denied.assert_not_called()

    async def test_lyrics_only_reference_work_and_retrying_completed_melody_do_not_require_model_terms(self) -> None:
        request = ReferenceImportRequest(url='https://youtube.com/watch?v=abcdefghijk', lyrics_source='whisper')
        record = ReferenceImport(id='a' * 32, status='partial', request=request, created_at='2026-10-17',
            updated_at='2026-10-17', stages=[ReferenceStage(name=name, status='done')
                for name in ('download', 'lyrics', 'separation', 'melody')])
        with patch.object(routes_references.service, 'create', new=AsyncMock(return_value=record)):
            response = await self.client.post('/api/references/imports', json=request.model_dump(mode='json'))
            self.assertEqual(response.status_code, 200, response.text)
        record.request.melody = True
        with patch.object(routes_references.service, 'get', return_value=record), \
                patch.object(routes_references.service, 'retry', new=AsyncMock(return_value=record)) as retry:
            response = await self.client.post('/api/references/imports/' + record.id + '/retry')
            self.assertEqual(response.status_code, 200, response.text)
            retry.assert_awaited_once()
        self.denied.assert_not_called()
        record.stages[-1].status = 'failed'
        with patch.object(routes_references.service, 'get', return_value=record), \
                patch.object(routes_references.service, 'retry', new=AsyncMock()) as retry:
            response = await self.client.post('/api/references/imports/' + record.id + '/retry')
            self.assertEqual(response.status_code, 409, response.text)
            retry.assert_not_awaited()
        self.denied.assert_called_once_with('yue2', 'transcription')

    async def test_native_task_admission_is_bounded_and_preserves_accepted_payload_bytes(self) -> None:
        state = ModelRuntimeState('yue2')
        state.status = ModelStatus.RUNNING
        raw = b'{ "model": "yue2", "request": {"lyrics": "Verse", "seed": 7} }'
        lease = Mock(spec=NativeLease)
        lease.release = AsyncMock()

        async def forward(base_url: str, request: Request, path: str, owner: routes_proxy._ProxyOwnership | None = None) -> Response:
            self.assertEqual(await request.body(), raw)
            return Response('accepted')

        with patch.object(routes_proxy.manager.state, 'models', {'yue2': state}), \
                patch.object(routes_proxy, 'reserve_native', new=AsyncMock(return_value=lease)), \
                patch.object(routes_proxy.native_yue, 'recover_quarantined', new=AsyncMock()), \
                patch('app.music_queue_ownership.require_access'), \
                patch.object(routes_proxy, '_proxy_to', new=AsyncMock(side_effect=forward)) as send:
            for payload, status in ((b' ' * (1024 * 1024 + 1), 413), (b'{invalid', 422),
                                    (b'{"model":"unreviewed","request":{}}', 503)):
                response = await self.client.post('/api/yue2/v1/tasks/run', content=payload)
                self.assertEqual(response.status_code, status, response.text)
            send.assert_not_awaited()
            self.denied.assert_not_called()
            self.denied.side_effect = None
            response = await self.client.post('/api/yue2/v1/tasks/run', content=raw)
            self.assertEqual(response.status_code, 200, response.text)
            self.denied.assert_called_once_with('yue2', 'generation')
            lease.release.assert_awaited_once()

    async def test_native_loader_rejects_model_aliases_and_checkpoint_overrides_before_forwarding(self) -> None:
        state = ModelRuntimeState('yue2')
        state.status = ModelStatus.RUNNING
        spec = routes_proxy.yue2_specs()['sheetsage2']
        lease = Mock(spec=NativeLease)
        lease.release = AsyncMock()
        variants = ({'id': 'alias'}, {'family': 'yue2'}, {'task': 'gen'}, {'mode': 'streaming'},
            {'path': routes_proxy.yue2_specs()['yue2']['path']}, {'path': spec['path'] + '/..'},
            {'model_spec_override': '/different/model.json'}, {'config': 'unreviewed'},
            {'load_options': {'weights': '/different/model'}},
            {'session_options': {'yue2.model_gguf': 'different.gguf'}})
        with patch.object(routes_proxy.manager.state, 'models', {'yue2': state}), \
                patch.object(routes_proxy, 'reserve_native', new=AsyncMock(return_value=lease)), \
                patch.object(routes_proxy.native_yue, 'recover_quarantined', new=AsyncMock()) as recover, \
                patch('app.music_queue_ownership.require_access'), \
                patch.object(routes_proxy, '_proxy_to', new=AsyncMock(return_value=Response('unreviewed'))) as forward:
            for variant in variants:
                with self.subTest(variant=variant):
                    body = {**spec, 'load_options': {}, 'session_options': {}, **variant}
                    response = await self.client.post('/api/yue2/v1/models/load', json=body)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(response.json(), {'detail': 'music_terms_request_invalid'})
            forward.assert_not_awaited()
            recover.assert_not_awaited()
            self.denied.assert_not_called()

    async def test_canonical_native_loads_remain_available_without_noncommercial_consent(self) -> None:
        state = ModelRuntimeState('yue2')
        state.status = ModelStatus.RUNNING
        lease = Mock(spec=NativeLease)
        lease.release = AsyncMock()
        with patch.object(routes_proxy.manager.state, 'models', {'yue2': state}), \
                patch.object(routes_proxy, 'reserve_native', new=AsyncMock(return_value=lease)), \
                patch.object(routes_proxy.native_yue, 'recover_quarantined', new=AsyncMock()), \
                patch('app.music_queue_ownership.require_access'), \
                patch.object(routes_proxy, '_proxy_to', new=AsyncMock(return_value=Response('loaded'))) as forward:
            for name, options in (('yue2', {}), ('yue2', {'yue2.model_gguf': 'yue2-3b-q4_0.gguf'}),
                                  ('yue2', {'yue2.model_gguf': 'yue2-3b-q8_0.gguf'}), ('sheetsage2', {}), ('muscriptor', {})):
                with self.subTest(name=name, options=options):
                    body = {**routes_proxy.yue2_specs()[name], 'load_options': {}, 'session_options': options}
                    response = await self.client.post('/api/yue2/v1/models/load', json=body)
                    self.assertEqual(response.status_code, 200, response.text)
                    forwarded_request = forward.await_args.args[1]
                    self.assertEqual(await forwarded_request.json(), body)
            self.denied.assert_not_called()

    async def test_alternative_native_inference_and_download_routes_cannot_bypass_admission(self) -> None:
        state = ModelRuntimeState('yue2')
        state.status = ModelStatus.RUNNING
        lease = Mock(spec=NativeLease)
        lease.release = AsyncMock()
        with patch.object(routes_proxy.manager.state, 'models', {'yue2': state}), \
                patch.object(routes_proxy, 'reserve_native_control', new=AsyncMock(return_value=lease)), \
                patch('app.music_queue_ownership.require_access'), \
                patch.object(routes_proxy, '_proxy_to', new=AsyncMock(return_value=Response('bypass'))) as forward:
            for endpoint in ('v1/audio/speech', 'v1/audio/speech/live', 'v1/audio/transcriptions',
                             'v1/audio/transcriptions/details', 'v1/audio/transcriptions/live',
                             'v1/ui/models/install', 'v1%2faudio%2fspeech%3fignored'):
                with self.subTest(endpoint=endpoint):
                    response = await self.client.post('/api/yue2/' + endpoint, json={'model': 'yue2', 'input': 'Verse'})
                    self.assertEqual(response.status_code, 405, response.text)
                    self.assertEqual(response.json(), {'detail': 'native_operation_unsupported'})
            forward.assert_not_awaited()
            self.denied.assert_not_called()


if __name__ == '__main__':
    unittest.main()
