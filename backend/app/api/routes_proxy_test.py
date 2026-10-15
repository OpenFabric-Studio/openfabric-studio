"""All native side effects respect the queue reservation; status stays readable."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import Request, Response

from app import music_queue_ownership as ownership, resource_admission
from app.api import routes_proxy
from app.orchestrator.state import ModelRuntimeState, ModelStatus


class QueueProxyAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.enterContext(patch.object(ownership, '_owner', None))
        self.enterContext(patch.object(resource_admission, 'admission_lock', asyncio.Lock()))
        self.enterContext(patch.object(resource_admission, '_native_inflight', 0))
        self.enterContext(patch('app.support_controls._idle_state', new=AsyncMock(return_value='idle')))
        state = ModelRuntimeState('ace_step')
        state.status = ModelStatus.RUNNING
        self.enterContext(patch.object(routes_proxy.manager.state, 'models', {'ace_step': state}))
        self.forward = self.enterContext(patch.object(routes_proxy, '_proxy_to', new=AsyncMock(return_value=Response('ok'))))

    async def request(self, method: str, path: str) -> Response:
        request = Request({'type': 'http', 'method': method, 'path': '/', 'headers': [], 'query_string': b''})
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
