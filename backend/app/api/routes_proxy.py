"""Generic reverse proxy: /api/{ace,yue2}/* -> the active model's own REST API.

Mirrors the proxy pattern YuE2's own web-ui/server.py already uses for its
/v1/* passthrough (strip hop-by-hop headers, forward method/body/query as-is)
so both frontends can be written against a single same-origin API surface.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from ..config import MODELS
from ..orchestrator.manager import manager
from ..orchestrator.state import ModelStatus
from .. import video_jobs, native_yue
from ..job_lifecycle import await_cleanup
from ..resource_admission import NativeLease, ResourceBusyError, reserve_native

router = APIRouter(prefix="/api")

_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    "content-encoding", "content-length", "host",
}

_client = httpx.AsyncClient(timeout=None)
logger = logging.getLogger(__name__)


class _ProxyOwnership:
    def __init__(self, model_id: str, lease: NativeLease) -> None:
        self.model_id, self.lease = model_id, lease
        self.requested = False
        self.completed = False
        self.finished = False

    async def close(self) -> None:
        if self.finished:
            return
        # All cleanup calls are drained by their caller; there is one stream
        # owner and its background fallback, never two concurrent resets.
        self.finished = True
        if self.model_id == 'yue2' and self.requested and not self.completed:
            try:
                await manager.restart_model('yue2')
            except BaseException:
                native_yue.quarantine(self.lease)
                logger.exception('Native proxy stop failed; reservation retained')
                raise
        await self.lease.release()


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
            'v1/dataset/preprocess_async', 'v1/lora/load', 'v1/lora/toggle', 'v1/lora/scale'}
    return model_id == 'yue2' and endpoint in {'v1/tasks/run', 'v1/models/load', 'v1/models/unload'}


def _make_proxy_route(model_id: str) -> Callable[..., Awaitable[Response]]:
    async def _route(request: Request, path: str = "") -> Response:
        rs = manager.state.models[model_id]
        if rs.status != ModelStatus.RUNNING:
            return JSONResponse({"error": f"model '{model_id}' is not active"}, status_code=503)
        owner: _ProxyOwnership | None = None
        try:
            if _accelerator_mutation(model_id, path, request.method):
                if model_id == 'yue2':
                    await native_yue.recover_quarantined()
                lease = await reserve_native(video_jobs.work_busy, model_id=model_id, exclusive=model_id == 'yue2')
                owner = _ProxyOwnership(model_id, lease)
            response = await _proxy_to(MODELS[model_id].proxy_target, request, path, owner)
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
        except ResourceBusyError as exc:
            return JSONResponse({'error': str(exc)}, status_code=409)
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
