"""Serialize local admission while native requests reserve accelerator resources."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from .job_lifecycle import await_cleanup

admission_lock = asyncio.Lock()
_native_inflight = 0
_native_models: dict[str, int] = {}


class ResourceBusyError(Exception):
    """An incompatible video worker already owns local resources."""


def native_work_inflight() -> bool:
    return _native_inflight > 0


class NativeLease:
    def __init__(self, model_id: str | None = None) -> None:
        self._released = False
        self._model_id = model_id

    async def _release(self) -> None:
        global _native_inflight
        async with admission_lock:
            if not self._released:
                self._released = True
                _native_inflight -= 1
                if self._model_id is not None:
                    count = _native_models.get(self._model_id, 0) - 1
                    if count > 0:
                        _native_models[self._model_id] = count
                    else:
                        _native_models.pop(self._model_id, None)

    async def release(self) -> None:
        await await_cleanup(self._release())


async def reserve_native(video_busy: Callable[[], bool], *, model_id: str | None = None,
                         exclusive: bool = False) -> NativeLease:
    global _native_inflight
    async with admission_lock:
        if video_busy():
            raise ResourceBusyError("video_work_busy")
        if exclusive and model_id is not None and _native_models.get(model_id, 0):
            raise ResourceBusyError('native_model_busy')
        _native_inflight += 1
        if model_id is not None:
            _native_models[model_id] = _native_models.get(model_id, 0) + 1
        return NativeLease(model_id)


@asynccontextmanager
async def native_admission(video_busy: Callable[[], bool]) -> AsyncIterator[None]:
    lease = await reserve_native(video_busy)
    try:
        yield
    finally:
        await lease.release()
