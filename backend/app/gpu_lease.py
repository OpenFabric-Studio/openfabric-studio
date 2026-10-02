"""Label actual holders of the existing shared GPU lock without changing admission."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from .voice_contracts import VoiceQueueReason


@dataclass(frozen=True)
class GpuOwner:
    reason: VoiceQueueReason
    label: str = ''


_owners: dict[asyncio.Lock, GpuOwner] = {}


def gpu_owner(lock: asyncio.Lock) -> GpuOwner:
    """Bare legacy holders are busy, never inferred from a queued registry."""
    return _owners.get(lock, GpuOwner('gpu_busy'))


@asynccontextmanager
async def gpu_lease(lock: asyncio.Lock, reason: VoiceQueueReason, label: str = '') -> AsyncIterator[None]:
    async with lock:
        owner = GpuOwner(reason, label.strip()[:200])
        _owners[lock] = owner
        try:
            yield
        finally:
            if _owners.get(lock) is owner:
                _owners.pop(lock)
