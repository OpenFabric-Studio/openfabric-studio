"""Sanitize native LoRA status and failures before they cross the app boundary."""
from __future__ import annotations

import asyncio
from typing import Literal

import httpx
from pydantic import TypeAdapter, ValidationError

from .ace_lora_contracts import AceLoraErrorCode, AceLoraStatus
from .config import MODELS
from .contracts import JsonObject, JsonValue

control_lock = asyncio.Lock()
_json: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_LIMIT = 65536


class AceLoraError(Exception):
    def __init__(self, code: AceLoraErrorCode, status: int = 409) -> None:
        self.code, self.status = code, status
        super().__init__(code)


def _classify(message: JsonValue) -> AceLoraErrorCode:
    if not isinstance(message, str):
        return 'ace_lora_operation_failed'
    if message.startswith(('❌ LoRA path not found:', '❌ Please provide a LoRA path.')):
        return 'ace_lora_path_missing'
    if message.startswith(('❌ Invalid adapter:', '❌ Could not read adapter_config.json:', '❌ adapter_config.json ')):
        return 'ace_lora_invalid_adapter'
    if message.startswith('❌ Adapter name already in use:'):
        return 'ace_lora_already_loaded'
    if message.startswith('❌ LoRA loading is not supported on quantized models.'):
        return 'ace_lora_quantized_unsupported'
    if message.startswith(('❌ No LoRA adapter loaded.', '⚠️ No LoRA adapter loaded.')):
        return 'ace_lora_not_loaded'
    if message.startswith(('Model not initialized', '❌ Model not initialized.')):
        return 'ace_lora_model_inactive'
    if message.startswith('❌ Unknown adapter:'):
        return 'ace_lora_selection_mismatch'
    return 'ace_lora_operation_failed'


async def _request(client: httpx.AsyncClient, method: Literal['GET', 'POST'],
                   endpoint: Literal['status', 'load', 'unload', 'toggle', 'scale', 'model_inventory', 'health', 'init'],
                   body: JsonObject | None = None) -> JsonValue:
    unavailable: AceLoraErrorCode = 'ace_lora_status_unavailable' if method == 'GET' else 'ace_lora_operation_failed'
    seconds = 10.0 if method == 'GET' else 300.0
    path = {'health': '/health', 'model_inventory': '/v1/model_inventory', 'init': '/v1/init'}.get(endpoint, f'/v1/lora/{endpoint}')
    try:
        async with asyncio.timeout(seconds):
            async with client.stream(method, f'{MODELS["ace_step"].proxy_target}{path}',
                                     json=body, timeout=httpx.Timeout(seconds, connect=5.0, pool=5.0)) as response:
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > _LIMIT:
                        raise AceLoraError(unavailable, 502)
                    content.extend(chunk)
                value = _json.validate_json(bytes(content))
                if not isinstance(value, dict):
                    raise AceLoraError(unavailable, 502)
                code = value.get('code')
                if not response.is_success or type(code) is not int or code != 200:
                    if method == 'GET':
                        raise AceLoraError(unavailable, 502)
                    error = _classify(value.get('detail', value.get('error')))
                    raise AceLoraError(error, 409 if error in {'ace_lora_already_loaded', 'ace_lora_not_loaded',
                        'ace_lora_selection_mismatch'} else 400 if error != 'ace_lora_operation_failed' else 502)
                return value.get('data')
    except (httpx.HTTPError, TimeoutError, ValidationError, ValueError) as exc:
        raise AceLoraError(unavailable, 502) from exc


def _uninitialized_primary(health: JsonValue) -> bool:
    if not isinstance(health, dict):
        return False
    loaded = health.get('loaded_model')
    return (health.get('status') == 'ok' and health.get('service') == 'ACE-Step API'
            and health.get('models_initialized') is False and 'loaded_model' in health
            and (loaded is None or isinstance(loaded, str) and bool(loaded.strip()) and len(loaded) <= 500))


async def fetch_status(client: httpx.AsyncClient) -> AceLoraStatus:
    try:
        value = await _request(client, 'GET', 'status')
        return AceLoraStatus.model_validate(value)
    except (AceLoraError, ValidationError) as exc:
        # Lazy startup has a configured model name before a handler is loaded.
        if _uninitialized_primary(await _request(client, 'GET', 'health')):
            return AceLoraStatus(lora_loaded=False, use_lora=False, lora_scale=1, active_adapter=None, adapters=[])
        raise AceLoraError('ace_lora_status_unavailable', 502) from exc


async def initialize_primary(client: httpx.AsyncClient) -> None:
    if not _uninitialized_primary(await _request(client, 'GET', 'health')):
        raise AceLoraError('ace_lora_model_inactive', 503)
    value = await _request(client, 'POST', 'init', {'init_llm': False})
    loaded = value.get('loaded_model') if isinstance(value, dict) else None
    slot = value.get('slot') if isinstance(value, dict) else None
    if (not isinstance(value, dict) or value.get('message') != 'Model initialization completed'
            or type(slot) is not int or slot != 1 or not isinstance(loaded, str) or not loaded.strip() or len(loaded) > 500):
        raise AceLoraError('ace_lora_operation_failed', 502)


async def fetch_primary_model(client: httpx.AsyncClient) -> str:
    value = await _request(client, 'GET', 'model_inventory')
    name = value.get('default_model') if isinstance(value, dict) else None
    if not isinstance(name, str) or not name.strip() or len(name) > 500:
        raise AceLoraError('ace_lora_status_unavailable', 502)
    return name


async def mutate(client: httpx.AsyncClient, operation: Literal['load', 'unload', 'toggle', 'scale'],
                 body: JsonObject) -> JsonObject:
    try:
        value = await _request(client, 'POST', operation, body)
    except AceLoraError as exc:
        if operation != 'unload' or exc.code != 'ace_lora_model_inactive':
            raise
        if not _uninitialized_primary(await _request(client, 'GET', 'health')):
            raise
        return {'data': {'message': 'Applied'}, 'code': 200}
    message = value.get('message') if isinstance(value, dict) else None
    if not isinstance(message, str) or not message.strip() or len(message) > 8192:
        raise AceLoraError('ace_lora_operation_failed', 502)
    # Native scale warnings update reported state even when no weights changed.
    if operation == 'scale' and not message.startswith(('✅ LoRA scale (', '✅ LoKr scale (')):
        raise AceLoraError('ace_lora_operation_failed', 502)
    return {'data': {'message': 'Applied'}, 'code': 200}
