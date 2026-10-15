"""Same-origin bounded export API, independent of the destructive vendor endpoint."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import sys

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.responses import Response
from starlette.types import Message

from .. import lora_datasets, lora_export
from ..job_lifecycle import await_cleanup, run_owned_thread
from ..lora_export_contracts import LoraExportOptionsResponse, LoraExportPickResponse, LoraExportRequest, LoraExportResponse, LoraTrainedFilesRequest, LoraTrainedFilesResponse
from ..module_security import require_local_origin
from ..resource_admission import ResourceBusyError, reserve_native_control


class LoraExportRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()
        async def bounded(request: Request) -> Response:
            if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                require_local_origin(request)
            count = 0
            async def receive() -> Message:
                nonlocal count
                message = await request.receive()
                body: object = message.get('body', b'')
                if isinstance(body, bytes):
                    count += len(body)
                if count > 65536:
                    raise HTTPException(413, 'lora_export_request_too_large')
                return message
            try:
                return await handler(Request(request.scope, receive))
            except (lora_export.LoraExportError, lora_datasets.LoraDatasetError) as exc:
                raise HTTPException(exc.status, exc.code) from exc
            except ResourceBusyError as exc:
                raise HTTPException(409, exc.code) from exc
            except (RequestValidationError, ValidationError) as exc:
                raise HTTPException(422, 'lora_export_request_invalid') from exc
        return bounded


router = APIRouter(prefix='/api/lora-export', tags=['lora-export'], route_class=LoraExportRoute)
_pickers: set[asyncio.Task[str | None]] = set()


async def _run(body: LoraExportRequest, *, copy: bool) -> LoraExportResponse:
    async with lora_datasets.native_lock:
        lease = await reserve_native_control()
        try:
            await lora_datasets.require_native_idle()
            return await run_owned_thread(lora_export.export_lora if copy else lora_export.preview_export, body)
        finally:
            await lease.release()


@router.post('', response_model=LoraExportResponse)
async def export(body: LoraExportRequest) -> LoraExportResponse:
    return await _run(body, copy=True)


@router.post('/preview', response_model=LoraExportResponse)
async def preview(body: LoraExportRequest) -> LoraExportResponse:
    return await _run(body, copy=False)


@router.post('/check', response_model=LoraTrainedFilesResponse)
async def check(body: LoraTrainedFilesRequest) -> LoraTrainedFilesResponse:
    async with lora_datasets.native_lock:
        return await run_owned_thread(lora_export.check_trained_files, body)


@router.get('/options', response_model=LoraExportOptionsResponse)
async def options() -> LoraExportOptionsResponse:
    return LoraExportOptionsResponse(can_pick=sys.platform == 'darwin')


@router.post('/pick', response_model=LoraExportPickResponse)
async def pick(request: Request) -> LoraExportPickResponse:
    picker = asyncio.create_task(lora_export.pick_parent())
    _pickers.add(picker)
    stop = asyncio.Event()
    disconnected = asyncio.create_task(_disconnected(request, stop))
    try:
        finished, _ = await asyncio.wait({picker, disconnected}, return_when=asyncio.FIRST_COMPLETED)
        return LoraExportPickResponse(path=None if disconnected in finished else picker.result())
    finally:
        # is_disconnected's AnyIO cancel scope can absorb cancellation; the signal still ends the watcher.
        stop.set()
        disconnected.cancel()
        if not picker.done():
            picker.cancel()
        try:
            await await_cleanup(asyncio.gather(picker, disconnected, return_exceptions=True))
        finally:
            _pickers.discard(picker)


async def _disconnected(request: Request, stop: asyncio.Event) -> None:
    while not stop.is_set():
        if await request.is_disconnected() or stop.is_set():
            return
        await asyncio.sleep(0.1)


async def _drain_exports() -> None:
    try:
        pickers = tuple(_pickers)
        for picker in pickers:
            picker.cancel()
        await asyncio.gather(*pickers, return_exceptions=True)
        await lora_export.shutdown_pickers()
    finally:
        async with lora_datasets.native_lock:
            pass


async def shutdown() -> None:
    # The request retains this lock until its owned filesystem thread has drained.
    await await_cleanup(_drain_exports())
