"""Generation must submit the adapter the user confirmed, under one control lock."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError

from app import ace_jobs, db
from app.contracts import JsonObject


class AceLoraGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.enterContext(patch.object(db, '_db', None))
        self.enterContext(patch.object(db, 'DATA_DIR', root))
        self.enterContext(patch.object(db, 'DB_PATH', root / 'catalog.db'))
        self.enterContext(patch.object(db, 'FILES_DIR', root / 'files'))
        self.connection = db.get_db()
        self.requests: list[str] = []
        self.status: JsonObject = {
            'lora_loaded': True, 'use_lora': True, 'lora_scale': 0.75,
            'active_adapter': 'expected', 'adapters': ['expected'],
        }
        self.inventory: JsonObject = {'default_model': 'primary-model'}
        self.uninitialized = False
        self.enterContext(patch.object(ace_jobs, '_engine_running', return_value=True))
        self.enterContext(patch.object(ace_jobs, 'launch'))
        self.lock = asyncio.Lock()
        self.enterContext(patch.object(ace_jobs.ace_lora, 'control_lock', self.lock))

    async def asyncTearDown(self) -> None:
        await ace_jobs.shutdown()
        self.connection.close()
        self.temp.cleanup()

    def params(self, name: str | None = 'expected', scale: float = 0.75) -> JsonObject:
        return {'lyrics': 'fixture', '_generation_settings': {
            'engine': 'ace_step', 'loraName': name, 'loraScale': scale,
            # History uses this flag to ask for reselection when restored later.
            'loraRequiresReselection': name is not None,
        }}

    async def native(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(f'{request.method} {request.url.path}')
        if request.method == 'GET':
            if self.uninitialized and request.url.path == '/v1/lora/status':
                return httpx.Response(500, json={'detail': 'Model not initialized'})
            if self.uninitialized and request.url.path == '/health':
                return httpx.Response(200, json={'code': 200, 'data': {
                    'status': 'ok', 'service': 'ACE-Step API', 'models_initialized': False,
                    'loaded_model': 'acestep-v15-xl-sft',
                }})
            if request.url.path == '/v1/model_inventory':
                return httpx.Response(200, json={'code': 200, 'data': self.inventory})
            return httpx.Response(200, json={'code': 200, 'data': self.status})
        if request.url.path == '/cancel_task':
            return httpx.Response(200, json={'code': 200, 'data': {'cancelled': True}})
        self.assertEqual(request.url.path, '/release_task')
        payload = TypeAdapter(JsonObject).validate_json(await request.aread())
        self.assertNotIn('_generation_settings', payload)
        return httpx.Response(200, json={'code': 200, 'data': {
            'task_id': 'guarded-job', 'status': 'queued', 'queue_position': 0,
        }})

    async def submit(self, params: JsonObject) -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.native))
        with patch.object(ace_jobs.httpx, 'AsyncClient', return_value=client):
            await ace_jobs.submit(params, 'Fixture', None, None)

    async def assert_mismatch(self, params: JsonObject) -> None:
        with self.assertRaises(HTTPException) as caught:
            await self.submit(params)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail, 'ace_lora_selection_mismatch')
        self.assertFalse(any(request.startswith('POST ') for request in self.requests))
        self.assertEqual(ace_jobs.list_jobs(), [])

    async def test_rejects_different_active_adapter_before_native_submission(self) -> None:
        self.status['active_adapter'] = 'previous-adapter'
        self.status['adapters'] = ['expected', 'previous-adapter']
        await self.assert_mismatch(self.params())

    async def test_rejects_different_strength_before_native_submission(self) -> None:
        self.status['lora_scale'] = 0.5
        await self.assert_mismatch(self.params())

    async def test_rejects_unintended_enabled_adapter_for_no_lora_snapshot(self) -> None:
        await self.assert_mismatch(self.params(name=None))

    async def test_rejects_disabled_intended_adapter(self) -> None:
        self.status['use_lora'] = False
        await self.assert_mismatch(self.params())

    async def test_rejects_unknown_status_without_exposing_native_detail(self) -> None:
        self.status = {'private': '/secret/model/path'}
        with self.assertRaises(HTTPException) as caught:
            await self.submit(self.params())
        self.assertNotIn('secret', str(caught.exception.detail))
        self.assertEqual(caught.exception.status_code, 502)
        self.assertEqual(caught.exception.detail, 'ace_lora_status_unavailable')
        self.assertFalse(any(request.startswith('POST ') for request in self.requests))
        self.assertEqual(ace_jobs.list_jobs(), [])

    async def test_matching_adapter_and_strength_are_recorded_durably(self) -> None:
        params = self.params()
        await self.submit(params)
        self.assertEqual(self.requests[-1], 'POST /release_task')
        self.assertTrue(any(request.startswith('GET ') for request in self.requests))
        self.assertNotIn('GET /v1/model_inventory', self.requests)
        self.assertEqual(ace_jobs.get_job('guarded-job').params['_generation_settings'], params['_generation_settings'])

    async def test_loaded_but_disabled_adapter_allows_explicit_no_lora(self) -> None:
        self.status['use_lora'] = False
        await self.submit(self.params(name=None))
        self.assertEqual(self.requests[-1], 'POST /release_task')

    async def test_legacy_submission_without_snapshot_preserves_native_behavior(self) -> None:
        await self.submit({'lyrics': 'legacy fixture', 'model': 'secondary-model'})
        self.assertEqual(self.requests, ['POST /release_task'])

    async def test_lora_rejects_every_secondary_model_selector_alias(self) -> None:
        for alias in ('model', 'model_name', 'modelName', 'dit_model', 'ditModel'):
            with self.subTest(alias=alias):
                params = self.params()
                params[alias] = 'secondary-model'
                await self.assert_mismatch(params)

    async def test_lora_rejects_conflicting_selector_aliases(self) -> None:
        params = self.params()
        params.update({'model': 'primary-model', 'modelName': 'secondary-model'})
        await self.assert_mismatch(params)

    async def test_lora_rejects_nonstring_selector(self) -> None:
        for selector in (False, 0, [], {'private': '/secret/model'}):
            with self.subTest(selector=selector):
                params = self.params()
                params['model_name'] = selector
                await self.assert_mismatch(params)

    async def test_lora_accepts_selectors_matching_current_primary(self) -> None:
        params = self.params()
        params.update({alias: 'primary-model' for alias in ('model', 'model_name', 'modelName', 'dit_model', 'ditModel')})
        await self.submit(params)
        self.assertEqual(self.requests, ['GET /v1/lora/status', 'GET /v1/model_inventory', 'POST /release_task'])

    async def test_lora_cannot_confirm_primary_model_from_invalid_inventory(self) -> None:
        self.inventory = {'default_model': {'private': '/secret/model'}}
        params = self.params()
        params['model'] = 'primary-model'
        with self.assertRaises(HTTPException) as caught:
            await self.submit(params)
        self.assertEqual(caught.exception.status_code, 502)
        self.assertEqual(caught.exception.detail, 'ace_lora_status_unavailable')
        self.assertFalse(any(request.startswith('POST ') for request in self.requests))

    async def test_no_lora_keeps_native_model_selection(self) -> None:
        self.status['use_lora'] = False
        params = self.params(name=None)
        params['model'] = 'secondary-model'
        await self.submit(params)
        self.assertNotIn('GET /v1/model_inventory', self.requests)

    async def test_lazy_startup_allows_no_lora_generation_with_configured_model_name(self) -> None:
        self.uninitialized = True
        await self.submit(self.params(name=None))
        self.assertEqual(self.requests, ['GET /v1/lora/status', 'GET /health', 'POST /release_task'])
        self.assertEqual(ace_jobs.get_job('guarded-job').status, 'queued')

    async def test_lazy_startup_does_not_pretend_requested_adapter_is_loaded(self) -> None:
        self.uninitialized = True
        await self.assert_mismatch(self.params())
        self.assertEqual(self.requests, ['GET /v1/lora/status', 'GET /health'])

    async def test_snapshot_is_validated_with_generation_contract(self) -> None:
        params = self.params()
        params['_generation_settings'] = {'engine': 'ace_step', 'loraScale': 'not a number'}
        with self.assertRaises(ValidationError):
            await self.submit(params)
        self.assertEqual(self.requests, [])

    async def test_waits_for_control_lock_before_checking_or_submitting(self) -> None:
        await self.lock.acquire()
        pending = asyncio.create_task(self.submit(self.params()))
        try:
            await asyncio.sleep(0)
            self.assertEqual(self.requests, [])
        finally:
            self.lock.release()
            await pending
        self.assertEqual(self.requests, ['GET /v1/lora/status', 'POST /release_task'])

    async def test_holds_control_lock_until_job_is_durably_registered(self) -> None:
        record = ace_jobs.record_job

        def held_record(task_id: str, params: JsonObject, title: str, voice_id: str | None) -> None:
            self.assertTrue(self.lock.locked())
            record(task_id, params, title, voice_id)
            self.assertTrue(ace_jobs.work_busy())

        with patch.object(ace_jobs, 'record_job', side_effect=held_record):
            await self.submit(self.params())
        self.assertFalse(self.lock.locked())
        self.assertTrue(ace_jobs.work_busy())

    async def test_rechecks_engine_after_waiting_for_control_lock(self) -> None:
        await self.lock.acquire()
        with patch.object(ace_jobs, '_engine_running', side_effect=[True, False]):
            pending = asyncio.create_task(self.submit(self.params()))
            try:
                await asyncio.sleep(0)
                self.assertEqual(self.requests, [])
            finally:
                self.lock.release()
            with self.assertRaises(HTTPException) as caught:
                await pending
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail, 'model_inactive')
        self.assertEqual(self.requests, [])

    async def test_failed_registration_cancels_native_job_before_unlocking(self) -> None:
        async def checked_native(request: httpx.Request) -> httpx.Response:
            self.assertTrue(self.lock.locked())
            return await self.native(request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(checked_native))
        with patch.object(ace_jobs.httpx, 'AsyncClient', return_value=client), \
             patch.object(ace_jobs, 'record_job', side_effect=OSError('injected write failure')):
            with self.assertRaises(OSError):
                await ace_jobs.submit(self.params(), 'Fixture', None, None)
        self.assertEqual(self.requests, ['GET /v1/lora/status', 'POST /release_task', 'POST /cancel_task'])
        self.assertFalse(self.lock.locked())
        self.assertEqual(ace_jobs.list_jobs(), [])
