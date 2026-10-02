"""Owned native transcription requests; HTTP cancellation also drains inference."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from pydantic import TypeAdapter

from . import video_jobs
from .config import MODELS
from .contracts import JsonObject
from .job_lifecycle import await_cleanup
from .orchestrator.manager import manager
from .orchestrator.state import ModelStatus
from .resource_admission import NativeLease, ResourceBusyError, reserve_native

logger = logging.getLogger(__name__)
_json = TypeAdapter(JsonObject)
_quarantined: list[NativeLease] = []
_recovery_lock = asyncio.Lock()


def engine_running() -> bool:
    return manager.state.models['yue2'].status == ModelStatus.RUNNING


async def send(endpoint: str, payload: JsonObject | None) -> JsonObject:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10800, connect=10, write=120, pool=30), follow_redirects=False) as client:
        async with client.stream('GET' if payload is None else 'POST', MODELS['yue2'].proxy_target + endpoint, json=payload) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > 384 * 1024**2:
                    raise ValueError('Native result too large')
                data.extend(chunk)
            return _json.validate_json(data)


class OwnedSession:
    def __init__(self) -> None:
        self.mutating = False

    async def get(self, endpoint: str) -> JsonObject:
        return await send(endpoint, None)

    async def post(self, endpoint: str, payload: JsonObject) -> JsonObject:
        self.mutating = True
        result = await send(endpoint, payload)
        self.mutating = False
        return result


async def recover_quarantined() -> None:
    async with _recovery_lock:
        if _quarantined:
            await await_cleanup(manager.restart_model('yue2'))
            while _quarantined:
                await _quarantined.pop().release()


def quarantine(lease: NativeLease) -> None:
    """Transfer a failed legacy request's held lease to verified recovery."""
    if lease not in _quarantined:
        _quarantined.append(lease)


@asynccontextmanager
async def owned_session() -> AsyncIterator[OwnedSession]:
    await recover_quarantined()
    started = time.monotonic()
    while True:
        if not engine_running():
            raise RuntimeError('model_inactive')
        try:
            lease = await reserve_native(video_jobs.work_busy, model_id='yue2', exclusive=True)
            break
        except ResourceBusyError:
            if time.monotonic() - started > 600:
                raise TimeoutError('native_queue_timeout')
            await asyncio.sleep(0.25)
    session = OwnedSession()
    try:
        yield session
    finally:
        if session.mutating:
            try:
                await await_cleanup(manager.restart_model('yue2'))
            except BaseException:
                quarantine(lease)
                logger.exception('Native stop failed; resource reservation retained')
                raise
        await lease.release()


async def shutdown() -> None:
    if _quarantined:
        await manager.stop_all()
        while _quarantined:
            await _quarantined.pop().release()
