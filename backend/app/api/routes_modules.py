"""Typed local module inventory and catalog-controlled installation jobs."""
from __future__ import annotations

from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from ..module_catalog import configured_environment, inventory, plan
from ..module_contracts import ModuleInventory, ModuleInstallJob, ModuleInstallRequest, ModuleJobsResponse, ModulePlan, ModulePlanRequest
from ..module_jobs import ModuleSetupError, service
from ..module_security import require_local_origin

logger = logging.getLogger(__name__)


class SetupRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        original = super().get_route_handler()
        async def handle(request: Request) -> Response:
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                require_local_origin(request)
            try:
                return await original(request)
            except RequestValidationError as exc:
                raise HTTPException(422, detail='invalid_setup_request') from exc
        return handle


router = APIRouter(prefix='/api/modules', tags=['setup modules'], route_class=SetupRoute)
JobId = Annotated[str, Path(pattern=r'^[0-9a-f]{32}$')]


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except ModuleSetupError as exc:
        status = 404 if exc.code == 'job_missing' else 409 if exc.code in ('setup_busy', 'plan_changed', 'setup_not_resumable', 'worker_unverified') else 422
        raise HTTPException(status, detail=exc.code) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception('Module setup operation failed')
        raise HTTPException(503, detail='setup_unavailable') from exc


@router.get('', response_model=ModuleInventory)
async def get_inventory() -> ModuleInventory:
    with _errors():
        return await inventory(configured_environment())


@router.post('/plan', response_model=ModulePlan)
async def review_plan(body: ModulePlanRequest) -> ModulePlan:
    with _errors():
        return await plan(body, configured_environment())


@router.get('/jobs', response_model=ModuleJobsResponse)
async def list_jobs() -> ModuleJobsResponse:
    with _errors():
        return service().list()


@router.post('/jobs', response_model=ModuleInstallJob)
async def create_job(body: ModuleInstallRequest) -> ModuleInstallJob:
    with _errors():
        return await service().create(body)


@router.get('/jobs/{identifier}', response_model=ModuleInstallJob)
async def get_job(identifier: JobId) -> ModuleInstallJob:
    with _errors():
        return service().get(identifier)


@router.post('/jobs/{identifier}/cancel', response_model=ModuleInstallJob)
async def cancel_job(identifier: JobId) -> ModuleInstallJob:
    with _errors():
        return await service().cancel(identifier)


@router.post('/jobs/{identifier}/resume', response_model=ModuleInstallJob)
async def resume_job(identifier: JobId) -> ModuleInstallJob:
    with _errors():
        return await service().resume(identifier)
