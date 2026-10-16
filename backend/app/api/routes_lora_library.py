"""Same-origin, revision-checked saved ACE datasets and workspace."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
import logging
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.responses import Response
from starlette.types import Message

from .. import lora_datasets as library, video_jobs
from ..lora_dataset_contracts import (
    CreateLoraDatasetRequest, RenameLoraDatasetRequest, LoraDatasetRevision,
    LoraDatasetEntry, LoraDatasetsResponse, LoraWorkspace,
    LoraDatasetOperationResponse, ScanLoraDatasetRequest, SaveLoraDatasetRequest,
    PreprocessLoraDatasetRequest, LoraDatasetTaskStarted, AutoLabelLoraDatasetRequest, UpdateLoraDatasetSampleRequest,
    RemoveLoraDatasetRecordsRequest, RemoveLoraDatasetRecordsResponse,
)
from ..module_security import require_local_origin
from ..resource_admission import ResourceBusyError, reserve_native, reserve_native_control

logger = logging.getLogger(__name__)


class DatasetRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()
        async def bounded(request: Request) -> Response:
            if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                require_local_origin(request)
            count = 0
            limit = 1024 * 1024 if request.url.path == '/api/lora-datasets/remove-records' else 64 * 1024
            async def receive() -> Message:
                nonlocal count
                message = await request.receive()
                body: object = message.get('body', b'')
                if isinstance(body, bytes):
                    count += len(body)
                if count > limit:
                    raise HTTPException(413, 'dataset_request_too_large')
                return message
            try:
                return await handler(Request(request.scope, receive))
            except library.LoraDatasetError as exc:
                raise HTTPException(exc.status, exc.code) from exc
            except ResourceBusyError as exc:
                raise HTTPException(409, exc.code) from exc
            except (RequestValidationError, ValidationError) as exc:
                raise HTTPException(422, 'dataset_request_invalid') from exc
            except (OSError, ValueError) as exc:
                logger.exception('Dataset storage unavailable')
                raise HTTPException(503, 'dataset_storage_unavailable') from exc
        return bounded


router = APIRouter(prefix='/api/lora-datasets', tags=['lora-datasets'], route_class=DatasetRoute)


@router.get('', response_model=LoraDatasetsResponse)
async def list_datasets() -> LoraDatasetsResponse:
    return library.list_datasets()


@router.post('', response_model=LoraDatasetEntry)
async def create(body: CreateLoraDatasetRequest) -> LoraDatasetEntry:
    async with library.native_lock:
        return library.create_dataset(body)


@router.get('/workspace', response_model=LoraWorkspace)
async def workspace() -> LoraWorkspace:
    return library.get_workspace()


@router.post('/remove-records', response_model=RemoveLoraDatasetRecordsResponse)
async def remove_records(body: RemoveLoraDatasetRecordsRequest) -> RemoveLoraDatasetRecordsResponse:
    async with library.native_lock:
        await library.require_native_idle()
        return library.remove_dataset_records(body)


@router.put('/workspace', response_model=LoraWorkspace)
async def save_workspace(body: LoraWorkspace) -> LoraWorkspace:
    async with library.native_lock:
        previous = library.get_workspace()
        if previous.selected_dataset_id != body.selected_dataset_id:
            await library.require_native_idle()
        return library.save_workspace(body)


@router.get('/{identifier}/saved', response_model=LoraDatasetOperationResponse)
async def saved_dataset(identifier: str, revision: int = Query(ge=0, le=9007199254740991)) -> LoraDatasetOperationResponse:
    return library.read_saved_dataset(identifier, revision)


@router.post('/{identifier}/rename', response_model=LoraDatasetEntry)
async def rename(identifier: str, body: RenameLoraDatasetRequest) -> LoraDatasetEntry:
    async with library.native_lock:
        await library.require_native_idle()
        return library.rename_dataset(identifier, body)


@router.post('/{identifier}/trash', response_model=LoraDatasetEntry)
async def trash(identifier: str, body: LoraDatasetRevision) -> LoraDatasetEntry:
    async with library.native_lock:
        await library.require_native_idle()
        return library.trash_dataset(identifier, body)


@router.post('/{identifier}/restore', response_model=LoraDatasetEntry)
async def restore(identifier: str, body: LoraDatasetRevision) -> LoraDatasetEntry:
    async with library.native_lock:
        return library.restore_dataset(identifier, body)


@router.post('/{identifier}/load', response_model=LoraDatasetOperationResponse)
async def load(identifier: str, body: LoraDatasetRevision) -> LoraDatasetOperationResponse:
    async with library.native_lock:
        lease = await reserve_native_control()
        try:
            return await library.load_dataset(identifier, body)
        finally:
            await lease.release()


@router.post('/{identifier}/scan', response_model=LoraDatasetOperationResponse)
async def scan(identifier: str, body: ScanLoraDatasetRequest) -> LoraDatasetOperationResponse:
    async with library.native_lock:
        lease = await reserve_native_control()
        try:
            return await library.scan_dataset(identifier, body)
        finally:
            await lease.release()


@router.post('/{identifier}/save', response_model=LoraDatasetOperationResponse)
async def save(identifier: str, body: SaveLoraDatasetRequest) -> LoraDatasetOperationResponse:
    async with library.native_lock:
        lease = await reserve_native_control()
        try:
            return await library.save_dataset(identifier, body)
        finally:
            await lease.release()


@router.post('/{identifier}/preprocess', response_model=LoraDatasetTaskStarted)
async def preprocess(identifier: str, body: PreprocessLoraDatasetRequest) -> LoraDatasetTaskStarted:
    async with library.native_lock:
        lease = await reserve_native(video_jobs.work_busy, model_id='ace_step')
        try:
            return await library.preprocess_dataset(identifier, body)
        finally:
            await lease.release()


@router.post('/{identifier}/auto-label', response_model=LoraDatasetTaskStarted)
async def auto_label(identifier: str, body: AutoLabelLoraDatasetRequest) -> LoraDatasetTaskStarted:
    async with library.native_lock:
        lease = await reserve_native(video_jobs.work_busy, model_id='ace_step')
        try:
            return await library.auto_label_dataset(identifier, body)
        finally:
            await lease.release()


@router.post('/{identifier}/samples/{sample_index}', response_model=LoraDatasetOperationResponse)
async def update_sample(identifier: str, sample_index: int, body: UpdateLoraDatasetSampleRequest) -> LoraDatasetOperationResponse:
    async with library.native_lock:
        lease = await reserve_native_control()
        try:
            return await library.update_dataset_sample(identifier, sample_index, body)
        finally:
            await lease.release()
