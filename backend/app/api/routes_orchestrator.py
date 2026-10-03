from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..config import yue2_specs
from ..orchestrator.manager import manager
from ..orchestrator.process import StartCancelled
from .. import video_jobs
from ..resource_admission import ResourceBusyError, native_admission

from ..client_contracts import OrchestratorConfigResponse, OrchestratorStatusResponse

from ..client_contracts import SwitchRequest

router = APIRouter(prefix="/api/orchestrator", tags=["orchestrator"])


@router.get("/config", response_model=OrchestratorConfigResponse)
async def get_config():
    return {
        "yue2_specs": yue2_specs(),
    }


@router.get("/status", response_model=OrchestratorStatusResponse)
async def get_status():
    return manager.status_snapshot()


@router.post("/switch", response_model=OrchestratorStatusResponse)
async def switch(req: SwitchRequest):
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
async def stop():
    await manager.stop_active()
    return manager.status_snapshot()
