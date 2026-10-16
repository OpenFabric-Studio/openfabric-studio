"""Queue HTTP controls validate local requests and keep internal errors private."""
from __future__ import annotations

from collections.abc import AsyncIterator
import sqlite3
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
import httpx

from app import music_queue, music_queue_ownership, music_queue_plan, music_queue_store
from app.api import routes_music_queue as routes
from app.contracts import JsonObject
from app.music_lora_contracts import MusicLoraError
from app.music_queue_contracts import (
    MusicQueueCancelRequest, MusicQueueCatalog, MusicQueueControlRequest,
    MusicQueueBrief, MusicQueueEngineSelection, MusicQueueEnqueueRequest,
    MusicQueueError, MusicQueuePreviewRequest, MusicQueueReadiness,
    MusicQueueReorderRequest, MusicQueueRetryRequest, MusicQueueSnapshot,
)
from app.resource_admission import ResourceBusyError


class MusicQueueApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        app = FastAPI()
        app.include_router(routes.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:9000')
        self.addAsyncCleanup(self.client.aclose)
        self.state = MusicQueueSnapshot(revision=7, status='paused')
        self.ready = MusicQueueReadiness(engine='stable_audio3', supported=True, installed=True,
            weights_ready=True, managed_status='one_shot', ready=True)
        self.request = MusicQueuePreviewRequest(brief=MusicQueueBrief(
            description='Warm jazz', duration_seconds=30), selections=[MusicQueueEngineSelection(engine='stable_audio3')])
        with patch.object(music_queue_plan, 'catalog', new=AsyncMock(return_value=MusicQueueCatalog(engines=[self.ready]))), \
                patch.object(music_queue_plan, '_weights_ready', return_value=True):
            self.plan = await music_queue_plan.preview(self.request)
        self.enqueued = MusicQueueEnqueueRequest(**self.request.model_dump(), revision=7,
            submission_key='submission-1', preview_fingerprint=self.plan.fingerprint)
        self.batch_id, self.item_id = 'a' * 32, 'b' * 32

    async def test_read_only_routes_return_typed_snapshot_and_catalog(self) -> None:
        catalog = MusicQueueCatalog(engines=[self.ready])
        with patch.object(music_queue, 'snapshot', return_value=self.state) as snapshot:
            response = await self.client.get('/api/music-queue')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MusicQueueSnapshot.model_validate(response.json()), self.state)
        snapshot.assert_called_once_with()
        with patch.object(music_queue_plan, 'catalog', new=AsyncMock(return_value=catalog)) as operation:
            response = await self.client.get('/api/music-queue/catalog')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MusicQueueCatalog.model_validate(response.json()), catalog)
        operation.assert_awaited_once_with()

    async def test_preview_and_enqueue_pass_validated_requests(self) -> None:
        with patch.object(music_queue_plan, 'preview', new=AsyncMock(return_value=self.plan)) as preview:
            response = await self.client.post('/api/music-queue/preview', json=self.request.model_dump(mode='json'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.plan.model_dump(mode='json'))
        preview.assert_awaited_once_with(self.request)
        with patch.object(music_queue, 'enqueue', new=AsyncMock(return_value=self.state)) as enqueue:
            response = await self.client.post('/api/music-queue/batches', json=self.enqueued.model_dump(mode='json'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.state.model_dump(mode='json'))
        enqueue.assert_awaited_once_with(self.enqueued)

    async def test_every_control_action_and_retry_mode_preserves_typed_fields(self) -> None:
        for action in ('start', 'pause', 'cancel_active'):
            body = MusicQueueControlRequest.model_validate({'revision': 7, 'action': action})
            with self.subTest(action=action), patch.object(music_queue, 'control', new=AsyncMock(return_value=self.state)) as operation:
                response = await self.client.post('/api/music-queue/control', json=body.model_dump(mode='json'))
                self.assertEqual(response.status_code, 200)
                operation.assert_awaited_once_with(body)
        for mode in ('save', 'inference'):
            retry = MusicQueueRetryRequest.model_validate({'revision': 7, 'item_id': self.item_id, 'mode': mode})
            with self.subTest(mode=mode), patch.object(music_queue, 'retry', new=AsyncMock(return_value=self.state)) as operation:
                response = await self.client.post('/api/music-queue/retry', json=retry.model_dump(mode='json'))
                self.assertEqual(response.status_code, 200)
                operation.assert_awaited_once_with(retry)
        reorder = MusicQueueReorderRequest(revision=7, batch_ids=[self.batch_id])
        with patch.object(music_queue, 'reorder', return_value=self.state) as operation:
            response = await self.client.post('/api/music-queue/reorder', json=reorder.model_dump(mode='json'))
        self.assertEqual(response.status_code, 200)
        operation.assert_called_once_with(reorder)
        cancel = MusicQueueCancelRequest(revision=7, batch_id=self.batch_id)
        with patch.object(music_queue, 'cancel_batch', new=AsyncMock(return_value=self.state)) as operation:
            response = await self.client.post(f'/api/music-queue/batches/{self.batch_id}/cancel', json=cancel.model_dump(mode='json'))
        self.assertEqual(response.status_code, 200)
        operation.assert_awaited_once_with(cancel)

    async def test_all_mutations_reject_foreign_origin_before_parsing_or_side_effects(self) -> None:
        paths = ['/preview', '/batches', '/control', '/reorder', '/retry', f'/batches/{self.batch_id}/cancel']
        operations = [self.enterContext(patch.object(music_queue, name, new=AsyncMock()))
            for name in ('enqueue', 'control', 'reorder', 'retry', 'cancel_batch')]
        operations.append(self.enterContext(patch.object(music_queue_plan, 'preview', new=AsyncMock())))
        self.enterContext(patch.dict('os.environ', {'OPENFABRIC_SETUP_ALLOWED_ORIGINS': ''}))
        for path in paths:
            for origin in ('https://evil.example', 'null', 'http://127.0.0.1:4000'):
                with self.subTest(path=path, origin=origin):
                    response = await self.client.post('/api/music-queue' + path, content=b'{invalid',
                        headers={'origin': origin, 'content-type': 'application/json'})
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.json(), {'detail': 'setup_origin_forbidden'})
        for operation in operations:
            operation.assert_not_called()

    async def test_oversized_body_is_bounded_including_stream_without_trusted_length(self) -> None:
        with patch.object(music_queue_plan, 'preview', new=AsyncMock()) as operation:
            response = await self.client.post('/api/music-queue/preview', content=b' ' * 131073,
                headers={'content-type': 'application/json'})
            self.assertEqual(response.status_code, 413)
            self.assertEqual(response.json(), {'detail': 'music_queue_request_too_large'})
            async def chunks() -> AsyncIterator[bytes]:
                for _ in range(3):
                    yield b' ' * 65536
            response = await self.client.post('/api/music-queue/preview', content=chunks(),
                headers={'content-type': 'application/json', 'content-length': '0'})
            self.assertEqual(response.status_code, 413)
            operation.assert_not_awaited()

    async def test_invalid_inputs_return_stable_code_without_echoing_private_prompt(self) -> None:
        sentinel = 'private lyrics and prompt sentinel'
        cases: list[tuple[str, JsonObject]] = [
            ('/preview', {'brief': {'description': sentinel}, 'selections': [{'engine': 'stable_audio3'}]}),
            ('/preview', {'brief': {**self.request.brief.model_dump(), 'vendor_override': sentinel},
                'selections': [{'engine': 'stable_audio3'}]}),
            ('/control', {'revision': True, 'action': 'start'}),
            ('/control', {'revision': 7, 'action': 'restart'}),
            ('/reorder', {'revision': 7, 'batch_ids': [self.batch_id, self.batch_id]}),
            ('/retry', {'revision': 7, 'item_id': self.item_id, 'mode': 'automatic'}),
            (f'/batches/{self.batch_id}/cancel', {'revision': 7, 'batch_id': '../private'}),
            ('/batches', {**self.enqueued.model_dump(mode='json'), 'submission_key': 'unsafe/key'}),
        ]
        for path, body in cases:
            with self.subTest(path=path, body=body):
                response = await self.client.post('/api/music-queue' + path, json=body)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json(), {'detail': 'music_queue_request_invalid'})
                self.assertNotIn(sentinel, response.text)

    async def test_cancel_path_body_mismatch_cannot_cancel_another_batch(self) -> None:
        with patch.object(music_queue, 'cancel_batch', new=AsyncMock()) as operation:
            response = await self.client.post(f'/api/music-queue/batches/{self.batch_id}/cancel',
                json={'revision': 7, 'batch_id': 'c' * 32})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {'detail': 'music_queue_batch_mismatch'})
        operation.assert_not_awaited()

    async def test_stale_revision_is_http_conflict_before_queue_ownership_or_mutation(self) -> None:
        with patch.object(music_queue, 'snapshot', return_value=self.state), \
                patch.object(music_queue_ownership, 'reserve', new=AsyncMock()) as reserve, \
                patch.object(music_queue_store, 'mutate') as mutate:
            response = await self.client.post('/api/music-queue/control', json={'revision': 6, 'action': 'start'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {'detail': 'music_queue_revision_conflict'})
        reserve.assert_not_awaited()
        mutate.assert_not_called()

    async def test_domain_adapter_and_busy_errors_keep_status_and_stable_codes(self) -> None:
        failures: list[MusicQueueError | MusicLoraError | ResourceBusyError] = [MusicQueueError('music_queue_item_missing', 404),
            MusicLoraError('music_lora_adapter_changed', 409), ResourceBusyError('music_queue_busy')]
        for failure in failures:
            with self.subTest(failure=failure), patch.object(music_queue_plan, 'preview', new=AsyncMock(side_effect=failure)):
                response = await self.client.post('/api/music-queue/preview', json=self.request.model_dump(mode='json'))
            self.assertEqual(response.status_code, 404 if isinstance(failure, MusicQueueError) else 409)
            self.assertEqual(response.json(), {'detail': failure.code})

    async def test_storage_errors_are_logged_but_never_expose_internal_paths(self) -> None:
        for failure in (OSError('/private/library/queue.sqlite'), sqlite3.OperationalError('private SQL details'), ValueError('private corrupt JSON')):
            with self.subTest(failure=failure), self.assertLogs(routes.logger, level='ERROR'), \
                    patch.object(music_queue, 'snapshot', side_effect=failure):
                response = await self.client.get('/api/music-queue')
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {'detail': 'music_queue_storage_unavailable'})
            self.assertNotIn(str(failure), response.text)
