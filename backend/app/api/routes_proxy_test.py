"""All native side effects respect the queue reservation; status stays readable."""
from __future__ import annotations

import asyncio
import unittest
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import httpx

from fastapi import Request, Response
from starlette.types import Message

from app import ace_lora, music_queue_ownership as ownership, resource_admission
from app.ace_lora_contracts import AceLoraStatus
from app.api import routes_proxy
from app.orchestrator.state import ModelRuntimeState, ModelStatus


_PROXY_TO = routes_proxy._proxy_to


class QueueProxyAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.enterContext(patch.object(ownership, '_owner', None))
        self.enterContext(patch.object(resource_admission, 'admission_lock', asyncio.Lock()))
        self.enterContext(patch.object(resource_admission, '_native_inflight', 0))
        self.enterContext(patch('app.support_controls._idle_state', new=AsyncMock(return_value='idle')))
        state = ModelRuntimeState('ace_step')
        state.status = ModelStatus.RUNNING
        self.enterContext(patch.object(routes_proxy.manager.state, 'models', {'ace_step': state}))
        self.enterContext(patch.object(ace_lora, 'control_lock', asyncio.Lock()))
        self.enterContext(patch('app.ace_jobs.work_busy', return_value=False))
        self.status = self.enterContext(patch.object(ace_lora, 'fetch_status', new=AsyncMock(return_value=AceLoraStatus(
            lora_loaded=True, use_lora=True, lora_scale=0.75, active_adapter='chosen', adapters=['chosen']))))
        self.mutate = self.enterContext(patch.object(ace_lora, 'mutate', new=AsyncMock(return_value={'data': {'message': 'Applied'}, 'code': 200})))
        self.training = self.enterContext(patch.object(routes_proxy, '_training_proxy', new=AsyncMock(return_value=Response('ok'))))
        self.forward = self.enterContext(patch.object(routes_proxy, '_proxy_to', new=AsyncMock(return_value=Response('ok'))))

    async def request(self, method: str, path: str, body: bytes = b'') -> Response:
        async def receive() -> Message:
            return {'type': 'http.request', 'body': body, 'more_body': False}
        request = Request({'type': 'http', 'method': method, 'path': '/', 'headers': [], 'query_string': b''}, receive)
        return await routes_proxy._make_proxy_route('ace_step')(request, path)

    async def test_queue_blocks_native_lora_unload_delete_and_unknown_side_effects(self) -> None:
        owner = await ownership.reserve()
        for method, path in (('POST', 'v1/init'), ('POST', 'v1/lora/unload'), ('DELETE', 'v1/lora/style'), ('PATCH', 'v1/new-mutation')):
            with self.subTest(method=method, path=path):
                response = await self.request(method, path)
                self.assertEqual(response.status_code, 409)
                self.assertIn(b'music_queue_busy', response.body)
        self.forward.assert_not_awaited()
        self.assertTrue(await owner.release())

    async def test_current_owner_mutation_and_external_reads_remain_available(self) -> None:
        owner = await ownership.reserve()
        for method, path in (('GET', 'v1/lora/status'), ('POST', 'query_result')):
            response = await self.request(method, path)
            self.assertEqual(response.status_code, 200)
        with owner.context():
            response = await self.request('POST', 'v1/lora/unload')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(resource_admission.native_work_inflight())
        if response.background:
            await response.background()
        self.assertFalse(resource_admission.native_work_inflight())
        self.assertTrue(await owner.release())

    async def test_unknown_native_mutation_prevents_queue_claim_until_response_drains(self) -> None:
        response = await self.request('POST', 'v1/metadata')
        self.assertTrue(resource_admission.native_work_inflight())
        with self.assertRaises(resource_admission.ResourceBusyError):
            await ownership.reserve()
        if response.background:
            await response.background()
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_legacy_export_is_never_forwarded_even_with_normalized_paths(self) -> None:
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            for path in ('v1/training/export', '/v1/training/export/', 'v1//training/./export', 'v1/training/start/../export', 'v1%2ftraining%2fexport'):
                with self.subTest(method=method, path=path):
                    response = await self.request(method, path)
                    self.assertEqual(response.status_code, 410)
                    self.assertIn(b'lora_export_safe_endpoint_required', response.body)
        self.forward.assert_not_awaited()
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_nonaccelerator_control_preserves_legacy_admission_during_video(self) -> None:
        with patch('app.video_jobs.work_busy', return_value=True):
            response = await self.request('POST', 'v1/metadata')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(resource_admission.native_work_inflight())
        if response.background:
            await response.background()

    async def test_external_yue_request_cannot_reset_quarantine_before_queue_check(self) -> None:
        owner = await ownership.reserve()
        state = ModelRuntimeState('yue2')
        state.status = ModelStatus.RUNNING
        with patch.object(routes_proxy.manager.state, 'models', {'yue2': state}), \
             patch.object(routes_proxy.native_yue, 'recover_quarantined', new=AsyncMock()) as recover:
            request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': [], 'query_string': b''})
            response = await routes_proxy._make_proxy_route('yue2')(request, 'v1/models/load')
        self.assertEqual(response.status_code, 409)
        recover.assert_not_awaited()
        self.assertTrue(await owner.release())

    async def test_lora_controls_reject_busy_generation_and_keep_status_readable(self) -> None:
        with patch('app.ace_jobs.work_busy', return_value=True):
            response = await self.request('POST', 'v1/lora/unload')
            status = await self.request('GET', 'v1/lora/status')
        self.assertEqual(response.status_code, 409)
        self.assertIn(b'ace_lora_busy', response.body)
        self.assertEqual(status.status_code, 200)
        self.mutate.assert_not_awaited()
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_lora_request_validation_blocks_unbounded_or_coerced_values(self) -> None:
        for path, body in [('scale', b'{"scale":2}'), ('toggle', b'{"use_lora":"false"}'),
                           ('load', b'{"lora_path":null}'), ('load', b'x' * 65537),
                           ('unload', b'{"unexpected":"value"}')]:
            with self.subTest(path=path):
                response = await self.request('POST', f'v1/lora/{path}', body)
                self.assertIn(response.status_code, (413, 422))
                self.assertIn(b'ace_lora_invalid_adapter', response.body)
        self.mutate.assert_not_awaited()
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_lora_native_error_is_stable_and_releases_admission(self) -> None:
        self.mutate.side_effect = ace_lora.AceLoraError('ace_lora_already_loaded')
        response = await self.request('POST', 'v1/lora/load', b'{"lora_path":"/adapter"}')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.body, b'{"error":"ace_lora_already_loaded"}')
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_lora_mutation_holds_shared_lock_until_upstream_completes(self) -> None:
        entered = asyncio.Event()
        async def pending(*args: object, **kwargs: object) -> None:
            entered.set()
            await asyncio.Event().wait()
        self.mutate.side_effect = pending
        task = asyncio.create_task(self.request('POST', 'v1/lora/unload'))
        await asyncio.wait_for(entered.wait(), 2)
        self.assertTrue(ace_lora.control_lock.locked())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(ace_lora.control_lock.locked())
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_normalized_lora_paths_cannot_bypass_sanitization(self) -> None:
        for path in ('v1%2flora%2fstatus', 'v1//lora/./status', 'v1/lora/status/'):
            response = await self.request('GET', path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'lora_loaded', response.body)
        self.forward.assert_not_awaited()

    async def test_inactive_lora_status_uses_safe_app_code(self) -> None:
        routes_proxy.manager.state.models['ace_step'].status = ModelStatus.STOPPED
        response = await self.request('GET', 'v1/lora/status')
        self.assertEqual(response.status_code, 503)
        self.assertIn(b'ace_lora_model_inactive', response.body)


    async def test_init_holds_adapter_lock_through_owned_response(self) -> None:
        for path in ('v1/init', 'v1%2finit'):
            response = await self.request('POST', path)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(ace_lora.control_lock.locked())
            self.assertTrue(resource_admission.native_work_inflight())
            if response.background:
                await response.background()
            self.assertFalse(ace_lora.control_lock.locked())
            self.assertFalse(resource_admission.native_work_inflight())

    async def test_init_rejects_owned_generation_and_releases_lock(self) -> None:
        with patch('app.ace_jobs.work_busy', return_value=True):
            response = await self.request('POST', 'v1/init')
        self.assertEqual(response.status_code, 409)
        self.assertIn(b'ace_lora_busy', response.body)
        self.training.assert_not_awaited()
        self.assertFalse(ace_lora.control_lock.locked())
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_cancelled_init_waiter_releases_lease_without_releasing_another_lock(self) -> None:
        async with ace_lora.control_lock:
            task = asyncio.create_task(self.request('POST', 'v1/init'))
            await asyncio.sleep(0)
            self.training.assert_not_awaited()
            self.assertTrue(resource_admission.native_work_inflight())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(ace_lora.control_lock.locked())
            self.assertFalse(resource_admission.native_work_inflight())
        self.assertFalse(ace_lora.control_lock.locked())

    async def test_init_forward_failure_releases_owned_lock(self) -> None:
        self.training.side_effect = RuntimeError('fixture')
        with self.assertRaises(RuntimeError):
            await self.request('POST', 'v1/init')
        self.assertFalse(ace_lora.control_lock.locked())
        self.assertFalse(resource_admission.native_work_inflight())


    async def test_init_stream_completion_releases_adapter_lock(self) -> None:
        self.training.side_effect = _PROXY_TO
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b'initialized'))) as client:
            with patch.object(routes_proxy, '_client', client):
                response = await self.request('POST', 'v1/init')
                self.assertTrue(ace_lora.control_lock.locked())
                chunks = [chunk async for chunk in response.body_iterator]
                self.assertEqual(chunks, [b'initialized'])
                self.assertFalse(ace_lora.control_lock.locked())
                self.assertFalse(resource_admission.native_work_inflight())
                if response.background:
                    await response.background()
                self.assertFalse(ace_lora.control_lock.locked())

    async def test_cancelled_init_stream_drains_response_and_adapter_lock(self) -> None:
        entered = asyncio.Event()
        closed = asyncio.Event()
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                entered.set()
                await asyncio.Event().wait()
                yield b''
            async def aclose(self) -> None:
                closed.set()
        self.training.side_effect = _PROXY_TO
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Stream()))) as client:
            with patch.object(routes_proxy, '_client', client):
                response = await self.request('POST', 'v1/init')
                async def consume() -> None:
                    async for _ in response.body_iterator:
                        pass
                task = asyncio.create_task(consume())
                await asyncio.wait_for(entered.wait(), 2)
                self.assertTrue(ace_lora.control_lock.locked())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(closed.is_set())
                self.assertFalse(ace_lora.control_lock.locked())
                self.assertFalse(resource_admission.native_work_inflight())


    async def test_explicit_load_initializes_once_under_both_control_locks(self) -> None:
        native_lock = asyncio.Lock()
        async def initialize(client: httpx.AsyncClient) -> None:
            self.assertTrue(ace_lora.control_lock.locked())
            self.assertTrue(native_lock.locked())
            idle.assert_awaited_once_with(require_running=True)
        self.mutate.side_effect = [ace_lora.AceLoraError('ace_lora_model_inactive'), {'data': {'message': 'Applied'}, 'code': 200}]
        with patch.object(routes_proxy.lora_datasets, 'native_lock', native_lock), \
             patch.object(routes_proxy.lora_datasets, 'require_native_idle', new=AsyncMock()) as idle, \
             patch.object(ace_lora, 'initialize_primary', new=AsyncMock(side_effect=initialize)) as init:
            response = await self.request('POST', 'v1/lora/load', b'{"lora_path":"adapter"}')
        self.assertEqual(response.status_code, 200)
        init.assert_awaited_once()
        self.assertEqual(self.mutate.await_count, 2)
        self.assertFalse(ace_lora.control_lock.locked())
        self.assertFalse(native_lock.locked())
        if response.background:
            await response.background()
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_adapter_initialization_failure_does_not_retry_load(self) -> None:
        self.mutate.side_effect = ace_lora.AceLoraError('ace_lora_model_inactive')
        with patch.object(routes_proxy.lora_datasets, 'require_native_idle', new=AsyncMock()), \
             patch.object(ace_lora, 'initialize_primary', new=AsyncMock(side_effect=ace_lora.AceLoraError('ace_lora_operation_failed', 502))):
            response = await self.request('POST', 'v1/lora/load', b'{"lora_path":"adapter"}')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.mutate.await_count, 1)
        self.assertFalse(ace_lora.control_lock.locked())
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_other_adapter_failures_never_initialize_models(self) -> None:
        for operation, code, body in [('load', 'ace_lora_invalid_adapter', b'{"lora_path":"adapter"}'),
                                      ('scale', 'ace_lora_model_inactive', b'{"scale":0.75}'),
                                      ('toggle', 'ace_lora_model_inactive', b'{"use_lora":true}')]:
            self.mutate.side_effect = ace_lora.AceLoraError(code)
            with patch.object(ace_lora, 'initialize_primary', new=AsyncMock()) as init:
                await self.request('POST', f'v1/lora/{operation}', body)
            init.assert_not_awaited()
            self.assertFalse(resource_admission.native_work_inflight())

    async def test_cancelled_adapter_initialization_releases_both_locks_and_lease(self) -> None:
        entered = asyncio.Event()
        finish = asyncio.Event()
        native_lock = asyncio.Lock()
        async def pending(client: httpx.AsyncClient) -> None:
            entered.set()
            await finish.wait()
        self.mutate.side_effect = ace_lora.AceLoraError('ace_lora_model_inactive')
        with patch.object(routes_proxy.lora_datasets, 'native_lock', native_lock), \
             patch.object(routes_proxy.lora_datasets, 'require_native_idle', new=AsyncMock()), \
             patch.object(ace_lora, 'initialize_primary', new=AsyncMock(side_effect=pending)):
            task = asyncio.create_task(self.request('POST', 'v1/lora/load', b'{"lora_path":"adapter"}'))
            await asyncio.wait_for(entered.wait(), 2)
            self.assertTrue(ace_lora.control_lock.locked())
            self.assertTrue(native_lock.locked())
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.assertTrue(ace_lora.control_lock.locked())
            self.assertTrue(native_lock.locked())
            self.assertTrue(resource_admission.native_work_inflight())
            finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.mutate.await_count, 1)
        self.assertFalse(ace_lora.control_lock.locked())
        self.assertFalse(native_lock.locked())
        self.assertFalse(resource_admission.native_work_inflight())


    async def test_lazy_initialization_retries_load_only_once(self) -> None:
        self.mutate.side_effect = ace_lora.AceLoraError('ace_lora_model_inactive')
        with patch.object(routes_proxy.lora_datasets, 'require_native_idle', new=AsyncMock()), \
             patch.object(ace_lora, 'initialize_primary', new=AsyncMock()) as init:
            response = await self.request('POST', 'v1/lora/load', b'{"lora_path":"adapter"}')
        self.assertNotEqual(response.status_code, 200)
        init.assert_awaited_once()
        self.assertEqual(self.mutate.await_count, 2)
        self.assertFalse(ace_lora.control_lock.locked())
        self.assertFalse(resource_admission.native_work_inflight())
