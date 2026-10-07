"""Local review-only diagnostics and identity-bound idle engine controls."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import logging

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from ..module_catalog import configured_environment, inventory
from ..module_security import require_local_origin
from ..support_contracts import EngineRuntimeResponse, StopEngineRequest, SupportReport
from ..support_controls import EngineControlError, engine_status, stop_engine
from ..support_diagnostics import build_report

logger = logging.getLogger(__name__)


class SupportRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        original = super().get_route_handler()
        async def handle(request: Request) -> Response:
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                require_local_origin(request)
            try:
                return await original(request)
            except RequestValidationError as exc:
                raise HTTPException(422, detail='invalid_support_request') from exc
        return handle


router = APIRouter(prefix='/api/support', tags=['local support'], route_class=SupportRoute)


@router.get('/engines', response_model=EngineRuntimeResponse)
async def get_engines() -> EngineRuntimeResponse:
    try:
        return await engine_status()
    except Exception as exc:
        logger.exception('Engine status unavailable')
        raise HTTPException(503, detail='support_unavailable') from exc


@router.post('/engines/stop', response_model=EngineRuntimeResponse)
async def stop_owned_engine(body: StopEngineRequest) -> EngineRuntimeResponse:
    try:
        await stop_engine(body.engine_id, body.instance_id)
        return await engine_status()
    except EngineControlError as exc:
        raise HTTPException(503 if exc.code == 'engine_stop_failed' else 409, detail=exc.code) from exc
    except Exception as exc:
        logger.exception('Engine control unavailable')
        raise HTTPException(503, detail='support_unavailable') from exc


@router.get('/report', response_model=SupportReport)
async def get_report() -> SupportReport:
    try:
        modules, engines = await asyncio.gather(inventory(configured_environment()), engine_status())
        return build_report(modules, engines.engines, [])
    except Exception as exc:
        logger.exception('Support report unavailable')
        raise HTTPException(503, detail='support_unavailable') from exc
