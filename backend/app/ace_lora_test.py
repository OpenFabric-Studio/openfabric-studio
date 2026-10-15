"""Bounded, schema-owned adapter controls never expose vendor error output."""
from __future__ import annotations

import asyncio
import json
import unittest
from collections.abc import AsyncIterator

import httpx

from app import ace_lora
from app.ace_lora_contracts import AceLoraStatus


class AceLoraBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_normalizes_only_known_fields(self) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
            'code': 200, 'data': {'lora_loaded': True, 'use_lora': True, 'lora_scale': 0.75,
            'active_adapter': 'Inspiration-1', 'adapters': ['Inspiration-1'], 'private': '/secret/path'}}))) as client:
            status = await ace_lora.fetch_status(client)
        self.assertEqual(status, AceLoraStatus(lora_loaded=True, use_lora=True, lora_scale=0.75,
            active_adapter='Inspiration-1', adapters=['Inspiration-1']))
        self.assertNotIn('private', status.model_dump())

    async def test_status_rejects_malformed_or_unbounded_native_state(self) -> None:
        valid = {'lora_loaded': False, 'use_lora': False, 'lora_scale': 1,
                 'active_adapter': None, 'adapters': []}
        for key, value in [('lora_loaded', 'false'), ('use_lora', 0), ('lora_scale', float('nan')),
                           ('lora_scale', 1.1), ('active_adapter', 'x' * 501),
                           ('adapters', ['x'] * 65), ('adapters', [1])]:
            with self.subTest(key=key, value=value):
                body = dict(valid)
                body[key] = value
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,
                    content=json.dumps({'code': 200, 'data': body})))) as client:
                    with self.assertRaises(ace_lora.AceLoraError) as raised:
                        await ace_lora.fetch_status(client)
                self.assertEqual(raised.exception.code, 'ace_lora_status_unavailable')

    async def test_known_http_and_envelope_errors_are_safe_and_specific(self) -> None:
        cases = [
            ('❌ LoRA path not found: /private/token=secret', 'ace_lora_path_missing'),
            ('❌ Invalid adapter: expected PEFT LoRA directory containing adapter_config.json', 'ace_lora_invalid_adapter'),
            ('❌ Could not read adapter_config.json: secret', 'ace_lora_invalid_adapter'),
            ('❌ Adapter name already in use: name', 'ace_lora_already_loaded'),
            ('❌ LoRA loading is not supported on quantized models. secret', 'ace_lora_quantized_unsupported'),
            ('❌ No LoRA adapter loaded. Please load a LoRA first.', 'ace_lora_not_loaded'),
            ('Model not initialized', 'ace_lora_model_inactive'),
            ('unknown secret /private/key', 'ace_lora_operation_failed'),
        ]
        for message, expected in cases:
            for status, payload in [(400, {'detail': message}), (200, {'code': 400, 'error': message, 'data': None})]:
                with self.subTest(message=message, status=status):
                    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status, json=payload))) as client:
                        with self.assertRaises(ace_lora.AceLoraError) as raised:
                            await ace_lora.mutate(client, 'load', {'lora_path': 'adapter'})
                    self.assertEqual(raised.exception.code, expected)
                    self.assertNotIn('secret', str(raised.exception))

    async def test_upstream_size_and_invalid_json_are_bounded(self) -> None:
        for payload in (b'x' * 65537, b'not-json', b'{"code":true,"data":{}}'):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=payload))) as client:
                with self.assertRaises(ace_lora.AceLoraError) as raised:
                    await ace_lora.mutate(client, 'unload', {})
            self.assertEqual(raised.exception.code, 'ace_lora_operation_failed')

    async def test_ack_discards_native_path_and_message(self) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,
            json={'code': 200, 'data': {'message': '✅ /private/adapter', 'lora_path': '/private/adapter'}}))) as client:
            result = await ace_lora.mutate(client, 'load', {'lora_path': 'adapter'})
        self.assertEqual(result, {'data': {'message': 'Applied'}, 'code': 200})

    async def test_malformed_success_acknowledgements_are_rejected(self) -> None:
        for data in (None, {}, {'message': None}, {'message': 1}, {'message': ''}, {'message': 'x' * 8193}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,
                json={'code': 200, 'data': data}))) as client:
                with self.assertRaises(ace_lora.AceLoraError) as raised:
                    await ace_lora.mutate(client, 'unload', {})
            self.assertEqual(raised.exception.code, 'ace_lora_operation_failed')

    async def test_enabled_adapter_state_requires_a_known_named_adapter(self) -> None:
        for active, adapters in ((None, []), ('unknown', ['known']), (' ', [' '])):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,
                json={'code': 200, 'data': {'lora_loaded': True, 'use_lora': True, 'lora_scale': 0.75,
                'active_adapter': active, 'adapters': adapters}}))) as client:
                with self.assertRaises(ace_lora.AceLoraError) as raised:
                    await ace_lora.fetch_status(client)
            self.assertEqual(raised.exception.code, 'ace_lora_status_unavailable')

    async def test_scale_warnings_fail_even_when_native_status_echoes_requested_scale(self) -> None:
        warnings = ['⚠️ LoRA scale unchanged: 0.75 (skipped 42 targets)',
                    '⚠️ Scale set to 0.75 (no modules found)', '⚠️ Scale set to 0.75 (partial)',
                    '⚠️ LoKr scale set to 0.75 (no LyCORIS net found)', 'unrecognized success format']
        for warning in warnings:
            def respond(request: httpx.Request) -> httpx.Response:
                if request.method == 'GET':
                    return httpx.Response(200, json={'code': 200, 'data': {'lora_loaded': True,
                        'use_lora': True, 'lora_scale': 0.75, 'active_adapter': 'name', 'adapters': ['name']}})
                return httpx.Response(200, json={'code': 200, 'data': {'message': warning}})
            with self.subTest(warning=warning):
                async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                    with self.assertRaises(ace_lora.AceLoraError) as raised:
                        await ace_lora.mutate(client, 'scale', {'scale': 0.75})
                    self.assertEqual((await ace_lora.fetch_status(client)).lora_scale, 0.75)
                self.assertEqual(raised.exception.code, 'ace_lora_operation_failed')

    async def test_scale_success_and_idempotent_unload_are_accepted(self) -> None:
        for operation, message in [('scale', '✅ LoRA scale (name): 0.75'),
                                   ('scale', '✅ LoKr scale (name): 0.75'),
                                   ('unload', '⚠️ No LoRA adapter loaded.')]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,
                json={'code': 200, 'data': {'message': message}}))) as client:
                self.assertEqual(await ace_lora.mutate(client, operation, {}), {'data': {'message': 'Applied'}, 'code': 200})

    async def test_primary_model_uses_current_inventory_default(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, '/v1/model_inventory')
            return httpx.Response(200, json={'code': 200, 'data': {'default_model': 'current-primary',
                'models': [{'name': 'other-secondary'}]}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            self.assertEqual(await ace_lora.fetch_primary_model(client), 'current-primary')

    async def test_unknown_or_invalid_primary_model_fails_safely(self) -> None:
        for data in (None, {}, {'default_model': None}, {'default_model': True},
                     {'default_model': ''}, {'default_model': '   '}, {'default_model': 'x' * 501}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,
                json={'code': 200, 'data': data}))) as client:
                with self.assertRaises(ace_lora.AceLoraError) as raised:
                    await ace_lora.fetch_primary_model(client)
            self.assertEqual(raised.exception.code, 'ace_lora_status_unavailable')

    async def test_lazy_uninitialized_primary_has_no_loaded_adapter(self) -> None:
        for configured_name in (None, 'acestep-v15-xl-sft'):
            calls: list[str] = []
            def respond(request: httpx.Request) -> httpx.Response:
                calls.append(request.url.path)
                if request.url.path == '/v1/lora/status':
                    return httpx.Response(500, json={'detail': 'Model not initialized'})
                self.assertEqual(request.url.path, '/health')
                return httpx.Response(200, json={'code': 200, 'data': {'status': 'ok', 'service': 'ACE-Step API',
                    'models_initialized': False, 'loaded_model': configured_name}})
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                status = await ace_lora.fetch_status(client)
            self.assertEqual(status, AceLoraStatus(lora_loaded=False, use_lora=False, lora_scale=1,
                active_adapter=None, adapters=[]))
            self.assertEqual(calls, ['/v1/lora/status', '/health'])

    async def test_failed_status_cannot_infer_no_adapter_from_uncertain_health(self) -> None:
        valid = {'status': 'ok', 'service': 'ACE-Step API', 'models_initialized': False, 'loaded_model': 'configured'}
        uncertain = [{**valid, 'models_initialized': value} for value in (True, 0, 'false', None)]
        uncertain.extend([{key: value for key, value in valid.items() if key != 'models_initialized'},
            {**valid, 'status': 'starting'}, {**valid, 'service': 'unknown'},
            {**valid, 'loaded_model': True}, {**valid, 'loaded_model': 'x' * 501},
            {key: value for key, value in valid.items() if key != 'loaded_model'}])
        for health in uncertain:
            def respond(request: httpx.Request) -> httpx.Response:
                if request.url.path == '/v1/lora/status':
                    return httpx.Response(500, json={'detail': 'private /secret/native-error'})
                return httpx.Response(200, json={'code': 200, 'data': health})
            with self.subTest(health=health):
                async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                    with self.assertRaises(ace_lora.AceLoraError) as raised:
                        await ace_lora.fetch_status(client)
                self.assertEqual(raised.exception.code, 'ace_lora_status_unavailable')
                self.assertNotIn('secret', str(raised.exception))

    async def test_explicit_initialization_uses_configured_primary_without_llm(self) -> None:
        calls: list[str] = []
        def respond(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == '/health':
                return httpx.Response(200, json={'code': 200, 'data': {'status': 'ok', 'service': 'ACE-Step API',
                    'models_initialized': False, 'loaded_model': 'configured-primary'}})
            self.assertEqual(request.url.path, '/v1/init')
            self.assertEqual(json.loads(request.content), {'init_llm': False})
            return httpx.Response(200, json={'code': 200, 'data': {'message': 'Model initialization completed',
                'slot': 1, 'loaded_model': 'configured-primary'}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await ace_lora.initialize_primary(client)
        self.assertEqual(calls, ['/health', '/v1/init'])

    async def test_initialization_requires_proven_uninitialized_primary(self) -> None:
        calls: list[str] = []
        def respond(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return httpx.Response(200, json={'code': 200, 'data': {'status': 'ok', 'service': 'ACE-Step API',
                'models_initialized': True, 'loaded_model': 'configured-primary'}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with self.assertRaises(ace_lora.AceLoraError):
                await ace_lora.initialize_primary(client)
        self.assertEqual(calls, ['/health'])

    async def test_initialization_rejects_malformed_or_failed_ack(self) -> None:
        valid = {'message': 'Model initialization completed', 'slot': 1, 'loaded_model': 'configured-primary'}
        for data in (None, {}, {**valid, 'slot': True}, {**valid, 'slot': 2},
                     {**valid, 'loaded_model': None}, {**valid, 'loaded_model': ' '},
                     {**valid, 'message': 'unknown'}):
            def respond(request: httpx.Request) -> httpx.Response:
                if request.url.path == '/health':
                    return httpx.Response(200, json={'code': 200, 'data': {'status': 'ok', 'service': 'ACE-Step API',
                        'models_initialized': False, 'loaded_model': 'configured-primary'}})
                return httpx.Response(200, json={'code': 200, 'data': data})
            with self.subTest(data=data):
                async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                    with self.assertRaises(ace_lora.AceLoraError) as raised:
                        await ace_lora.initialize_primary(client)
                self.assertEqual(raised.exception.code, 'ace_lora_operation_failed')

    async def test_unload_is_idempotent_for_verified_lazy_start(self) -> None:
        calls: list[str] = []
        def respond(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == '/v1/lora/unload':
                return httpx.Response(500, json={'detail': 'Model not initialized'})
            self.assertEqual(request.url.path, '/health')
            return httpx.Response(200, json={'code': 200, 'data': {'status': 'ok', 'service': 'ACE-Step API',
                'models_initialized': False, 'loaded_model': 'configured-primary'}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            self.assertEqual(await ace_lora.mutate(client, 'unload', {}), {'data': {'message': 'Applied'}, 'code': 200})
        self.assertEqual(calls, ['/v1/lora/unload', '/health'])

    async def test_transport_failure_and_timeout_are_safe(self) -> None:
        def fail(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout('/secret/token', request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            with self.assertRaises(ace_lora.AceLoraError) as raised:
                await ace_lora.fetch_status(client)
        self.assertEqual(raised.exception.code, 'ace_lora_status_unavailable')

    async def test_cancellation_closes_response_and_propagates(self) -> None:
        entered = asyncio.Event()
        closed = asyncio.Event()
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                entered.set()
                await asyncio.Event().wait()
                yield b''
            async def aclose(self) -> None:
                closed.set()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Stream()))) as client:
            task = asyncio.create_task(ace_lora.fetch_status(client))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(closed.is_set())
