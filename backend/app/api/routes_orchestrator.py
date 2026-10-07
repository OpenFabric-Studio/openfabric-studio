from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..config import yue2_specs
from ..orchestrator.manager import manager
from ..orchestrator.process import StartCancelled
from .. import video_jobs
from ..resource_admission import ResourceBusyError, native_admission
from ..module_security import require_local_origin
from ..support_controls import EngineControlError, stop_engine

from ..client_contracts import OrchestratorConfigResponse, OrchestratorStatusResponse

from ..client_contracts import SwitchRequest
from ..contracts import JsonObject

router = APIRouter(prefix="/api/orchestrator", tags=["orchestrator"])


@router.get("/config", response_model=OrchestratorConfigResponse)
async def get_config() -> OrchestratorConfigResponse:
    return OrchestratorConfigResponse.model_validate({'yue2_specs': yue2_specs()})


@router.get("/status", response_model=OrchestratorStatusResponse)
async def get_status() -> JsonObject:
    return manager.status_snapshot()


@router.post("/switch", response_model=OrchestratorStatusResponse)
async def switch(req: SwitchRequest) -> JsonObject:
    try:
        async with native_admission(video_jobs.work_busy):
            await manager.switch_to(req.model)
    except ResourceBusyError as exc:
        raise HTTPException(status_code=409, detail=exc.code) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except StartCancelled as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, TimeoutError) as exc:
        # Startup failed; manager.status_snapshot() already reflects the
        # per-model error state/message for the UI to display.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return manager.status_snapshot()


@router.post("/stop", response_model=OrchestratorStatusResponse)
async def stop(request: Request) -> JsonObject:
    require_local_origin(request)
    model_id = manager.state.active_model
    if model_id is None:
        return manager.status_snapshot()
    if model_id not in ('ace_step', 'yue2'):
        raise HTTPException(409, detail='engine_not_owned')
    instance = manager.owned_instance(model_id)
    if instance is None:
        raise HTTPException(409, detail='engine_not_owned')
    try:
        await stop_engine('ace_step' if model_id == 'ace_step' else 'yue2', instance)
    except EngineControlError as exc:
        raise HTTPException(503 if exc.code == 'engine_stop_failed' else 409, detail=exc.code) from exc
    return manager.status_snapshot()
