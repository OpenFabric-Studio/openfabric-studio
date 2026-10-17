"""Proxy native models with reviewed YuE tasks and loader identities.

The separate upload route owns input conversion. Other YuE mutations cannot
bypass terms admission through this proxy's generic forwarding path.
"""
from __future__ import annotations

import logging
import posixpath
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Literal
from urllib.parse import unquote
import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from starlette.types import Message

from pydantic import TypeAdapter, ValidationError
from ..config import MODELS, ACE_STEP_DIR, yue2_specs
from ..contracts import JsonObject, JsonValue
from ..orchestrator.manager import manager
from ..orchestrator.state import ModelStatus
from .. import ace_jobs, ace_lora, video_jobs, native_yue
from ..ace_lora_contracts import AceLoraLoadRequest, AceLoraScaleRequest, AceLoraToggleRequest
from .. import lora_datasets
from ..job_lifecycle import await_cleanup
from ..resource_admission import NativeLease, ResourceBusyError, reserve_native, reserve_native_control
from .routes_music_terms import require_usage

router = APIRouter(prefix="/api")

_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    "content-encoding", "content-length", "host",
}

_client = httpx.AsyncClient(timeout=None)
logger = logging.getLogger(__name__)


class _ProxyOwnership:
    def __init__(self, model_id: str, lease: NativeLease, *, reset_on_disconnect: bool = True) -> None:
        self.model_id, self.lease = model_id, lease
        self.reset_on_disconnect = reset_on_disconnect
        self.requested = False
        self.completed = False
        self.finished = False
        self.lora_control = False

    async def close(self) -> None:
        if self.finished:
            return
        # All cleanup calls are drained by their caller; there is one stream
        # owner and its background fallback, never two concurrent resets.
        self.finished = True
        try:
            if self.model_id == 'yue2' and self.reset_on_disconnect and self.requested and not self.completed:
                try:
                    await manager.restart_model('yue2')
                except BaseException:
                    native_yue.quarantine(self.lease)
                    logger.exception('Native proxy stop failed; reservation retained')
                    raise
            await self.lease.release()
        finally:
            if self.lora_control:
                self.lora_control = False
                ace_lora.control_lock.release()


async def _proxy_to(base_url: str, request: Request, path: str, owner: _ProxyOwnership | None = None) -> Response:
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    body = await request.body()
    url = httpx.URL(f"{base_url}/{path}", params=list(request.query_params.multi_items()))

    req = _client.build_request(
        request.method,
        url,
        headers=headers,
        content=body,
    )
    if owner:
        owner.requested = True
    upstream = await _client.send(req, stream=True)
    response_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}

    async def body_stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
            if owner:
                owner.completed = True
        finally:
            try:
                await upstream.aclose()
            finally:
                if owner:
                    await await_cleanup(owner.close())

    return StreamingResponse(
        body_stream(),
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )


def _accelerator_mutation(model_id: str, path: str, method: str) -> bool:
    if method.upper() not in {'POST', 'PUT', 'PATCH'}:
        return False
    endpoint = path.strip('/')
    if model_id == 'ace_step':
        return endpoint in {'release_task', 'v1/init', 'v1/training/start', 'v1/dataset/auto_label_async',
            'v1/dataset/preprocess_async', 'v1/lora/load', 'v1/lora/unload', 'v1/lora/toggle', 'v1/lora/scale'}
    return model_id == 'yue2' and endpoint in {'v1/tasks/run', 'v1/models/load', 'v1/models/unload'}


async def _training_proxy(base_url: str, request: Request, path: str, owner: _ProxyOwnership | None) -> Response:
    """Serialize builder mutations with the catalog's snapshot/start transactions."""
    async with lora_datasets.native_lock:
        await lora_datasets.require_native_idle(require_running=True)
        if path.startswith('v1/dataset/') and lora_datasets.get_workspace().selected_dataset_id:
            raise lora_datasets.LoraDatasetError('dataset_library_required')
        if path == 'v1/training/start':
            raw = await request.body()
            if len(raw) > 65536:
                raise lora_datasets.LoraDatasetError('dataset_request_too_large', 413)
            try:
                body: JsonValue = TypeAdapter(JsonValue).validate_json(raw)
            except ValueError as exc:
                raise lora_datasets.LoraDatasetError('dataset_request_invalid', 422) from exc
            folder = body.get('lora_output_dir', './lora_output') if isinstance(body, dict) else None
            if not isinstance(folder, str) or not folder or len(folder) > 4096 or '\\' in folder or '\x00' in folder:
                raise lora_datasets.LoraDatasetError('dataset_path_unsafe', 422)
            relative = Path(folder)
            root = ACE_STEP_DIR.resolve()
            output = (root / relative).resolve()
            if relative.is_absolute() or '..' in relative.parts or not output.is_relative_to(root):
                raise lora_datasets.LoraDatasetError('dataset_path_unsafe', 422)
            if output.exists() and (not output.is_dir() or any(output.iterdir())):
                raise lora_datasets.LoraDatasetError('training_output_exists')
        elif path in {'v1/dataset/preprocess', 'v1/dataset/preprocess_async'}:
            # Unattested filename-based tensor reuse can silently mix datasets.
            raise lora_datasets.LoraDatasetError('dataset_library_required')
        return await _proxy_to(base_url, request, path, owner)


_ACE_LORA_OPERATIONS: dict[str, Literal['load', 'unload', 'toggle', 'scale']] = {
    'v1/lora/load': 'load', 'v1/lora/unload': 'unload',
    'v1/lora/toggle': 'toggle', 'v1/lora/scale': 'scale'}
_ACE_LORA_ENDPOINTS = {'v1/lora/status', *_ACE_LORA_OPERATIONS}


async def _bounded_native_request(request: Request) -> tuple[Request, JsonObject]:
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 1024 * 1024:
            raise HTTPException(413, 'music_terms_request_too_large')
        raw.extend(chunk)
    try:
        body = TypeAdapter(JsonObject).validate_json(raw)
    except ValueError as error:
        raise HTTPException(422, 'music_terms_request_invalid') from error

    async def receive() -> Message:
        return {'type': 'http.request', 'body': bytes(raw), 'more_body': False}
    return Request(request.scope, receive), body


async def _native_music_request(request: Request) -> tuple[Request, Literal['generation', 'transcription']]:
    replay, body = await _bounded_native_request(request)
    model = body.get('model')
    specs = yue2_specs()
    operation: Literal['generation', 'transcription']
    if model == specs['yue2']['id']:
        operation = 'generation'
    elif model in (specs['sheetsage2']['id'], specs['muscriptor']['id']):
        operation = 'transcription'
    else:
        raise HTTPException(503, 'music_terms_unavailable')
    if not isinstance(body.get('request'), dict):
        raise HTTPException(422, 'music_terms_request_invalid')

    return replay, operation


async def _native_model_load(request: Request) -> Request:
    replay, body = await _bounded_native_request(request)
    specs = yue2_specs()
    spec = next((spec for spec in specs.values() if spec['id'] == body.get('id')), None)
    if (spec is None or body.keys() - {*spec, 'load_options', 'session_options'}
            or any(body.get(key) != value for key, value in spec.items())
            or body.get('load_options', {}) != {}):
        raise HTTPException(422, 'music_terms_request_invalid')
    options = body.get('session_options', {})
    if not isinstance(options, dict) or options and (
            spec['id'] != specs['yue2']['id'] or options.keys() != {'yue2.model_gguf'}
            or options.get('yue2.model_gguf') not in ('yue2-3b-q8_0.gguf', 'yue2-3b-q4_0.gguf')):
        raise HTTPException(422, 'music_terms_request_invalid')
    return replay


async def _ace_lora_proxy(request: Request, endpoint: str) -> Response:
    if endpoint == 'v1/lora/status':
        if request.method != 'GET':
            raise ace_lora.AceLoraError('ace_lora_operation_failed', 405)
        status = await ace_lora.fetch_status(_client)
        return JSONResponse({'data': status.model_dump(mode='json'), 'code': 200})
    if request.method != 'POST':
        raise ace_lora.AceLoraError('ace_lora_operation_failed', 405)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 65536:
            raise ace_lora.AceLoraError('ace_lora_invalid_adapter', 413)
        raw.extend(chunk)
    try:
        content = bytes(raw) or b'{}'
        if endpoint == 'v1/lora/load':
            command = AceLoraLoadRequest.model_validate_json(content)
            body: JsonObject = {'lora_path': command.lora_path, 'adapter_name': command.adapter_name}
        elif endpoint == 'v1/lora/scale':
            scale = AceLoraScaleRequest.model_validate_json(content)
            body = {'scale': scale.scale, 'adapter_name': scale.adapter_name}
        elif endpoint == 'v1/lora/toggle':
            toggle = AceLoraToggleRequest.model_validate_json(content)
            body = {'use_lora': toggle.use_lora}
        else:
            empty: JsonValue = TypeAdapter(JsonValue).validate_json(content)
            if empty != {}:
                raise ValueError('Unexpected unload body')
            body = {}
    except (ValidationError, ValueError) as exc:
        raise ace_lora.AceLoraError('ace_lora_invalid_adapter', 422) from exc
    async with ace_lora.control_lock:
        if manager.state.models['ace_step'].status != ModelStatus.RUNNING:
            raise ace_lora.AceLoraError('ace_lora_model_inactive', 503)
        if ace_jobs.work_busy():
            raise ace_lora.AceLoraError('ace_lora_busy')
        operation = _ACE_LORA_OPERATIONS[endpoint]
        try:
            result = await ace_lora.mutate(_client, operation, body)
        except ace_lora.AceLoraError as exc:
            if operation != 'load' or exc.code != 'ace_lora_model_inactive':
                raise
            async with lora_datasets.native_lock:
                await lora_datasets.require_native_idle(require_running=True)
                if ace_jobs.work_busy():
                    raise ace_lora.AceLoraError('ace_lora_busy')
                await await_cleanup(ace_lora.initialize_primary(_client))
                result = await ace_lora.mutate(_client, operation, body)
        return JSONResponse(result)


def _make_proxy_route(model_id: str) -> Callable[..., Awaitable[Response]]:
    async def _route(request: Request, path: str = "") -> Response:
        endpoint = posixpath.normpath('/' + unquote(path).replace('\\', '/').split('?', 1)[0].split('#', 1)[0]).strip('/')
        if model_id == 'yue2' and request.method.upper() not in {'GET', 'HEAD', 'OPTIONS'} and (
                request.method.upper() != 'POST' or endpoint not in {'v1/tasks/run', 'v1/models/load', 'v1/models/unload'}):
            raise HTTPException(405, 'native_operation_unsupported')
        if model_id == 'ace_step' and endpoint == 'v1/training/export':
            return JSONResponse({'error': 'lora_export_safe_endpoint_required'}, status_code=410)
        is_ace_lora = model_id == 'ace_step' and endpoint in _ACE_LORA_ENDPOINTS
        is_ace_init = model_id == 'ace_step' and endpoint == 'v1/init'
        rs = manager.state.models[model_id]
        if rs.status != ModelStatus.RUNNING and is_ace_lora:
            return JSONResponse({'error': 'ace_lora_model_inactive'}, status_code=503)
        if rs.status != ModelStatus.RUNNING:
            return JSONResponse({"error": f"model '{model_id}' is not active"}, status_code=503)
        if request.method.upper() in {'POST', 'PUT', 'PATCH'}:
            if model_id == 'yue2' and endpoint == 'v1/tasks/run':
                request, operation = await _native_music_request(request)
                require_usage('yue2', operation)
            elif model_id == 'ace_step' and endpoint in {'release_task', 'v1/training/start'}:
                require_usage('ace_step', 'generation' if endpoint == 'release_task' else 'training')
        owner: _ProxyOwnership | None = None
        try:
            accelerator = _accelerator_mutation(model_id, endpoint if is_ace_lora or is_ace_init or model_id == 'yue2' else path, request.method)
            mutation = request.method.upper() not in {'GET', 'HEAD', 'OPTIONS'} and not (
                model_id == 'ace_step' and request.method.upper() == 'POST' and path.strip('/') == 'query_result')
            if mutation:
                from ..music_queue_ownership import require_access
                require_access()
                if model_id == 'yue2' and endpoint == 'v1/models/load':
                    request = await _native_model_load(request)
                if model_id == 'yue2' and accelerator:
                    await native_yue.recover_quarantined()
                lease = await reserve_native(video_jobs.work_busy, model_id=model_id,
                    exclusive=model_id == 'yue2') if accelerator else await reserve_native_control()
                owner = _ProxyOwnership(model_id, lease, reset_on_disconnect=accelerator)
                # Stop may have drained the engine while this request waited
                # for admission. Never forward its earlier RUNNING snapshot.
                if rs.status != ModelStatus.RUNNING:
                    await await_cleanup(owner.close())
                    return JSONResponse({'error': 'ace_lora_model_inactive' if is_ace_lora else 'model_inactive'}, status_code=503)
            if owner is not None and is_ace_init:
                await ace_lora.control_lock.acquire()
                owner.lora_control = True
                if ace_jobs.work_busy():
                    raise ace_lora.AceLoraError('ace_lora_busy')
                if rs.status != ModelStatus.RUNNING:
                    raise ace_lora.AceLoraError('ace_lora_model_inactive', 503)
            if not is_ace_lora and not is_ace_init:
                endpoint = path.strip('/')
            guarded = mutation and model_id == 'ace_step' and (endpoint.startswith('v1/dataset/') or endpoint in {'v1/training/start', 'v1/init'})
            if is_ace_lora:
                response = await _ace_lora_proxy(request, endpoint)
            else:
                response = await _training_proxy(MODELS[model_id].proxy_target, request, endpoint, owner) if guarded else await _proxy_to(MODELS[model_id].proxy_target, request, path, owner)
            if owner:
                previous = response.background
                async def cleanup() -> None:
                    try:
                        if previous:
                            await previous()
                    finally:
                        if owner:
                            await await_cleanup(owner.close())
                response.background = BackgroundTask(cleanup)
            return response
        except ace_lora.AceLoraError as exc:
            if owner:
                await await_cleanup(owner.close())
            return JSONResponse({'error': exc.code}, status_code=exc.status)
        except ResourceBusyError as exc:
            return JSONResponse({'error': str(exc)}, status_code=409)
        except lora_datasets.LoraDatasetError as exc:
            if owner:
                await await_cleanup(owner.close())
            return JSONResponse({'error': exc.code}, status_code=exc.status)
        except (OSError, ValueError):
            logger.exception('Native dataset control failed')
            if owner:
                await await_cleanup(owner.close())
            return JSONResponse({'error': 'dataset_storage_unavailable'}, status_code=503)
        except httpx.HTTPError:
            logger.exception('Native model proxy failed')
            if owner:
                await await_cleanup(owner.close())
            return JSONResponse({'error': 'upstream_request_failed'}, status_code=502)
        except BaseException:
            if owner:
                await await_cleanup(owner.close())
            raise

    return _route


for _model_id, _definition in MODELS.items():
    router.add_api_route(
        f"/{_definition.proxy_prefix}/{{path:path}}",
        _make_proxy_route(_model_id),
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    )
