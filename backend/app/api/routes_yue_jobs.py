"""Durable generation; browser requests observe owned work instead of owning it."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path

from .. import yue_jobs
from ..yue_contracts import DeleteYueJobResponse, YueJobResponse, YueJobsResponse, YueSubmitRequest

router = APIRouter(prefix='/api/yue-jobs', tags=['yue-jobs'])
JobId = Annotated[str, Path(pattern=r'^[0-9a-f]{32}$')]


@router.post('', response_model=YueJobResponse)
async def submit(body: YueSubmitRequest) -> YueJobResponse:
    return yue_jobs.submit(body)


@router.get('', response_model=YueJobsResponse)
async def jobs() -> YueJobsResponse:
    return YueJobsResponse(jobs=yue_jobs.list_jobs())


@router.get('/{job_id}', response_model=YueJobResponse)
async def job(job_id: JobId) -> YueJobResponse:
    return yue_jobs.get_job(job_id)


@router.post('/{job_id}/cancel', response_model=YueJobResponse)
async def cancel(job_id: JobId) -> YueJobResponse:
    return await yue_jobs.cancel(job_id)


@router.delete('/{job_id}', response_model=DeleteYueJobResponse)
async def delete(job_id: JobId) -> DeleteYueJobResponse:
    yue_jobs.delete_job(job_id)
    return DeleteYueJobResponse(deleted=True)
