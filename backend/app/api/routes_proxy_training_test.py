"""Native mutations cannot replace a live dataset or overwrite a training run."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import Request, Response
from app import lora_datasets, resource_admission
from app.api import routes_proxy
from app.lora_dataset_contracts import LoraWorkspace
from app.orchestrator.state import ModelRuntimeState, ModelStatus


class TrainingProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(routes_proxy, 'ACE_STEP_DIR', self.root, create=True))
        self.enterContext(patch.object(resource_admission, 'admission_lock', asyncio.Lock()))
        self.enterContext(patch.object(resource_admission, '_native_inflight', 0))
        self.enterContext(patch('app.music_queue_ownership._owner', None))
        self.enterContext(patch.object(lora_datasets, 'native_lock', asyncio.Lock()))
        state = ModelRuntimeState('ace_step'); state.status = ModelStatus.RUNNING
        self.enterContext(patch.object(routes_proxy.manager.state, 'models', {'ace_step': state}))
        self.idle = self.enterContext(patch.object(lora_datasets, 'require_native_idle', new=AsyncMock()))
        self.enterContext(patch.object(lora_datasets, 'get_workspace', return_value=LoraWorkspace()))
        self.forward = self.enterContext(patch.object(routes_proxy, '_proxy_to', new=AsyncMock(return_value=Response('ok'))))

    async def request(self, path: str, payload: dict[str, object] | None = None) -> Response:
        async def receive() -> dict[str, object]:
            return {'type': 'http.request', 'body': json.dumps(payload or {}).encode(), 'more_body': False}
        request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': [], 'query_string': b''}, receive)
        return await routes_proxy._make_proxy_route('ace_step')(request, path)

    async def test_busy_label_blocks_competing_dataset_reset_and_releases_admission(self) -> None:
        self.idle.side_effect = lora_datasets.LoraDatasetError('dataset_busy')
        response = await self.request('v1/dataset/scan')
        self.assertEqual(response.status_code, 409)
        self.forward.assert_not_awaited()
        self.assertFalse(resource_admission.native_work_inflight())
        self.assertFalse(lora_datasets.native_lock.locked())

    async def test_managed_dataset_requires_atomic_app_wrappers(self) -> None:
        with patch.object(lora_datasets, 'get_workspace', return_value=LoraWorkspace(selected_dataset_id='a' * 32)):
            for path in ('v1/dataset/auto_label_async', 'v1/dataset/load', 'v1/dataset/sample/0', 'v1/dataset/save'):
                with self.subTest(path=path):
                    response = await self.request(path)
                    self.assertEqual(response.status_code, 409)
                    self.assertIn(b'dataset_library_required', response.body)
        self.forward.assert_not_awaited()

    async def test_existing_training_output_is_preserved(self) -> None:
        folder = self.root / 'output'; folder.mkdir(); checkpoint = folder / 'checkpoint.safetensors'; checkpoint.write_bytes(b'old')
        response = await self.request('v1/training/start', {'lora_output_dir': 'output'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(checkpoint.read_bytes(), b'old')
        self.forward.assert_not_awaited()

    async def test_training_output_cannot_escape_engine_root(self) -> None:
        for path in ('../outside', '/tmp/outside'):
            response = await self.request('v1/training/start', {'lora_output_dir': path})
            self.assertEqual(response.status_code, 422)
        self.forward.assert_not_awaited()

    async def test_fresh_training_output_is_forwarded_and_releases_lease(self) -> None:
        response = await self.request('v1/training/start', {'lora_output_dir': 'output/new-run'})
        self.assertEqual(response.status_code, 200)
        self.idle.assert_awaited_once()
        if response.background:
            await response.background()
        self.assertFalse(resource_admission.native_work_inflight())
