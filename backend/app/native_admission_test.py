"""Native proxy and durable submissions share video resource admission."""
from __future__ import annotations
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException, Request, Response
import httpx
from app import resource_admission
from app.api import routes_ace_jobs, routes_proxy
from app.orchestrator.state import ModelStatus

class NativeRouteAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.lock = patch.object(resource_admission, 'admission_lock', asyncio.Lock())
        self.lock.start()
        self.counter = patch.object(resource_admission, '_native_inflight', 0)
        self.counter.start()
    async def asyncTearDown(self) -> None:
        self.counter.stop(); self.lock.stop()
    def request(self, method: str = 'POST') -> Request:
        return Request({'type': 'http', 'method': method, 'path': '/', 'headers': [], 'query_string': b''})
    async def test_busy_video_rejects_native_generation_without_forwarding(self) -> None:
        with patch.object(routes_proxy.manager.state, 'models', {'yue2': SimpleNamespace(status=ModelStatus.RUNNING)}), patch('app.video_jobs.work_busy', return_value=True, create=True), patch.object(routes_proxy, '_proxy_to', AsyncMock()) as forward:
            response = await routes_proxy._make_proxy_route('yue2')(self.request(), 'v1/tasks/run')
        self.assertEqual(response.status_code, 409)
        forward.assert_not_awaited()

    async def test_optional_and_character_work_reject_native_mutations(self) -> None:
        for provider in ('app.optional_engines.work_busy', 'app.video_character_training.work_busy'):
            with self.subTest(provider=provider), patch(provider, return_value=True), \
                 patch('app.video_jobs.work_busy', return_value=False), \
                 patch.object(routes_proxy.manager.state, 'models', {'yue2': SimpleNamespace(status=ModelStatus.RUNNING)}), \
                 patch.object(routes_proxy, '_proxy_to', new=AsyncMock(return_value=Response('ok'))) as forward:
                response = await routes_proxy._make_proxy_route('yue2')(self.request(), 'v1/tasks/run')
                if response.background:
                    await response.background()
                self.assertEqual(response.status_code, 409)
                forward.assert_not_awaited()
                self.assertFalse(resource_admission.native_work_inflight())

    async def test_optional_and_character_work_reject_durable_music_submission(self) -> None:
        for provider in ('app.optional_engines.work_busy', 'app.video_character_training.work_busy'):
            with self.subTest(provider=provider), patch(provider, return_value=True), \
                 patch('app.video_jobs.work_busy', return_value=False), \
                 patch.object(routes_ace_jobs.ace_jobs, 'submit', new=AsyncMock()) as submit:
                with self.assertRaises(HTTPException) as error:
                    await routes_ace_jobs.submit(params='{}', title='Test', voice_id=None, ctx_audio=None)
                self.assertEqual(error.exception.status_code, 409)
                submit.assert_not_awaited()
    async def test_read_only_native_status_stays_available_while_video_runs(self) -> None:
        with patch.object(routes_proxy.manager.state, 'models', {'yue2': SimpleNamespace(status=ModelStatus.RUNNING)}), patch('app.video_jobs.work_busy', return_value=True, create=True), patch.object(routes_proxy, '_proxy_to', AsyncMock(return_value=Response('ok'))):
            response = await routes_proxy._make_proxy_route('yue2')(self.request('GET'), 'v1/models')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(resource_admission.native_work_inflight())
    async def test_native_stream_reservation_lasts_until_response_cleanup(self) -> None:
        with patch.object(routes_proxy.manager.state, 'models', {'yue2': SimpleNamespace(status=ModelStatus.RUNNING)}), patch('app.video_jobs.work_busy', return_value=False, create=True), patch.object(routes_proxy, '_proxy_to', AsyncMock(return_value=Response('ok'))):
            response = await routes_proxy._make_proxy_route('yue2')(self.request(), 'v1/tasks/run')
        self.assertTrue(resource_admission.native_work_inflight())
        self.assertIsNotNone(response.background)
        if response.background:
            await response.background()
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_request_queued_behind_stop_does_not_forward_stale_running_state(self) -> None:
        state = SimpleNamespace(status=ModelStatus.RUNNING)
        with patch.object(routes_proxy.manager.state, 'models', {'ace_step': state}), \
             patch('app.video_jobs.work_busy', return_value=False), \
             patch.object(routes_proxy, '_proxy_to', new=AsyncMock()) as forward:
            async with resource_admission.admission_lock:
                pending = asyncio.create_task(routes_proxy._make_proxy_route('ace_step')(self.request(), 'release_task'))
                await asyncio.sleep(0)
                state.status = ModelStatus.STOPPED
            response = await pending
        self.assertEqual(response.status_code, 503)
        forward.assert_not_awaited()
        self.assertFalse(resource_admission.native_work_inflight())
    async def test_busy_video_blocks_durable_ace_submission_before_upstream(self) -> None:
        with patch('app.video_jobs.work_busy', return_value=True, create=True), patch.object(routes_ace_jobs.ace_jobs, 'submit', AsyncMock()) as submit:
            with self.assertRaises(HTTPException) as caught:
                await routes_ace_jobs.submit(params='{}', title='Test', voice_id=None, ctx_audio=None)
        self.assertEqual(caught.exception.status_code, 409)
        submit.assert_not_awaited()
    async def test_upstream_failure_releases_reservation_and_hides_raw_error(self) -> None:
        with patch.object(routes_proxy.manager.state, 'models', {'yue2': SimpleNamespace(status=ModelStatus.RUNNING)}), patch('app.video_jobs.work_busy', return_value=False), patch.object(routes_proxy, '_proxy_to', AsyncMock(side_effect=httpx.ConnectError('private engine path'))):
            response = await routes_proxy._make_proxy_route('yue2')(self.request(), 'v1/tasks/run')
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(b'private', response.body)
        self.assertFalse(resource_admission.native_work_inflight())
    async def test_cancelled_forwarding_releases_reservation(self) -> None:
        with patch.object(routes_proxy.manager.state, 'models', {'yue2': SimpleNamespace(status=ModelStatus.RUNNING)}), patch('app.video_jobs.work_busy', return_value=False), patch.object(routes_proxy, '_proxy_to', AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await routes_proxy._make_proxy_route('yue2')(self.request(), 'v1/tasks/run')
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_legacy_http_cancellation_drains_reset_before_release(self) -> None:
        entered, resetting, released = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def send(*args, **kwargs):
            entered.set()
            await asyncio.Future()
        async def reset(model):
            resetting.set()
            await released.wait()
        request = self.request()
        request._receive = AsyncMock(return_value={'type': 'http.request', 'body': b'{}', 'more_body': False})
        with patch.object(routes_proxy.manager.state, 'models', {'yue2': SimpleNamespace(status=ModelStatus.RUNNING)}), patch('app.video_jobs.work_busy', return_value=False), patch.object(routes_proxy._client, 'send', side_effect=send), patch.object(routes_proxy.manager, 'restart_model', side_effect=reset):
            task = asyncio.create_task(routes_proxy._make_proxy_route('yue2')(request, 'v1/tasks/run'))
            await entered.wait()
            task.cancel()
            await resetting.wait()
            self.assertTrue(resource_admission.native_work_inflight())
            released.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(resource_admission.native_work_inflight())

    async def test_failed_legacy_reset_quarantines_lease_until_verified_recovery(self) -> None:
        from app import native_yue
        lease = await resource_admission.reserve_native(lambda: False, model_id='yue2', exclusive=True)
        owner = routes_proxy._ProxyOwnership('yue2', lease)
        owner.requested = True
        with patch.object(routes_proxy.manager, 'restart_model', new=AsyncMock(side_effect=RuntimeError('stop failed'))), self.assertLogs('app.api.routes_proxy', level='ERROR'):
            with self.assertRaises(RuntimeError):
                await owner.close()
        self.assertTrue(resource_admission.native_work_inflight())
        with patch.object(native_yue.manager, 'restart_model', new=AsyncMock()):
            await native_yue.recover_quarantined()
        self.assertFalse(resource_admission.native_work_inflight())
