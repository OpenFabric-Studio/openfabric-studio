"""Serialize local admission while native requests reserve accelerator resources."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import HTTPException

from .job_lifecycle import await_cleanup

admission_lock = asyncio.Lock()
_native_inflight = 0
_native_models: dict[str, int] = {}


class ResourceBusyError(Exception):
    """An incompatible local worker already owns resources."""

    def __init__(self, code: Literal['video_work_busy', 'native_model_busy', 'module_setup_busy']) -> None:
        self.code = code
        super().__init__(code)


def require_setup_idle() -> None:
    """Call on the event loop immediately before registering local work.

    Setup admission uses the same event loop; there must be no await between
    this check and publication to a job registry. Speech worker threads use
    module_jobs.speech_admission instead.
    """
    from .module_jobs import work_busy
    if work_busy():
        raise HTTPException(status_code=409, detail='module_setup_busy')


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
        from .module_jobs import work_busy
        if work_busy():
            raise ResourceBusyError('module_setup_busy')
        from .optional_engines import work_busy as optional_busy
        from .video_character_training import work_busy as character_training_busy
        from .audiobook_review import work_busy as asr_busy
        from .speaker_review import work_busy as speaker_busy
        from .audiobook_workflows import work_busy as audiobook_workflows_busy
        from .video_character_comparison import work_busy as character_comparison_busy
        if video_busy() or optional_busy() or character_training_busy() or character_comparison_busy() or asr_busy() or speaker_busy() or audiobook_workflows_busy():
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
