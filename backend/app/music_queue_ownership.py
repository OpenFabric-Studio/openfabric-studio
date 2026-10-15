"""Private capability for one music coordinator and its inherited child tasks."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from . import resource_admission

_owner: MusicQueueOwner | None = None
_context: ContextVar[MusicQueueOwner | None] = ContextVar('music_queue_owner', default=None)
_stop_handler: Callable[[], Awaitable[None]] | None = None


def busy() -> bool:
    return _owner is not None


def blocked() -> bool:
    return _owner is not None and _context.get() is not _owner


def require_access() -> None:
    if blocked():
        raise resource_admission.ResourceBusyError('music_queue_busy')


async def _resources_idle() -> bool:
    from .module_jobs import speech_work_busy
    from .orchestrator.manager import manager
    from .orchestrator.state import ModelStatus
    from .stems import gpu_lock
    from .support_controls import _idle_state
    if speech_work_busy() or gpu_lock.locked() or resource_admission.native_work_inflight():
        return False
    if any(state.status in (ModelStatus.STARTING, ModelStatus.STOPPING) or
           state.status == ModelStatus.ERROR and model_id in manager._processes
           for model_id, state in manager.state.models.items()):
        return False
    return await _idle_state() == 'idle'


class MusicQueueOwner:
    @contextmanager
    def context(self) -> Iterator[None]:
        if _owner is not self:
            raise resource_admission.ResourceBusyError('music_queue_busy')
        token = _context.set(self)
        try:
            yield
        finally:
            _context.reset(token)

    async def release(self) -> bool:
        global _owner
        async with resource_admission.admission_lock:
            if _owner is not self:
                return False
            with self.context():
                if not await _resources_idle():
                    return False
            _owner = None
            return True


async def reserve() -> MusicQueueOwner:
    global _owner
    async with resource_admission.admission_lock:
        if _owner is not None:
            raise resource_admission.ResourceBusyError('music_queue_busy')
        owner = MusicQueueOwner()
        # Synchronous submitters must also stay out while upstream accounting awaits.
        _owner = owner
        try:
            with owner.context():
                if not await _resources_idle():
                    raise resource_admission.ResourceBusyError('native_model_busy')
        except BaseException:
            _owner = None
            raise
        return owner


def register_stop_handler(handler: Callable[[], Awaitable[None]] | None) -> None:
    global _stop_handler
    _stop_handler = handler


async def stop_for_manual_control() -> bool:
    """Drain queue advancement before a user stops a native engine."""
    if not blocked():
        return False
    handler = _stop_handler
    if handler is None:
        raise resource_admission.ResourceBusyError('music_queue_busy')
    await handler()
    require_access()
    return True
