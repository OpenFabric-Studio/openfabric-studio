"""Bounded incremental parsing of inspected Seed-VC phase/chunk events."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .voice_progress import VoiceProgressTracker

PREFIX = 'REMIQORA_PROGRESS '
_MAX_LINE = 4096
_READ_BYTES = 32768


class ApplyProgressEvent(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    phase: Literal['loading', 'analyzing', 'converting']
    current: int = Field(ge=0, le=100000)
    total: int = Field(ge=0, le=100000)
    at: float = Field(ge=0, le=100000000000, allow_inf_nan=False)

    @model_validator(mode='after')
    def valid_counts(self) -> ApplyProgressEvent:
        if self.current > self.total or (self.phase != 'converting' and self.total != 0):
            raise ValueError('invalid_progress_counts')
        if self.phase == 'converting' and self.total == 0:
            raise ValueError('invalid_progress_counts')
        return self


def parse_event(line: str) -> ApplyProgressEvent | None:
    if len(line) > _MAX_LINE or not line.startswith(PREFIX):
        return None
    try:
        return ApplyProgressEvent.model_validate_json(line[len(PREFIX):])
    except ValidationError:
        return None


def apply_event(tracker: VoiceProgressTracker, event: ApplyProgressEvent, *, now: float | None = None) -> bool:
    """Reject regressing stages and inconsistent totals within a conversion."""
    progress = tracker.progress
    stamp = event.at if now is None else now
    if progress.status not in {'queued', 'running'} or progress.finished_at is not None \
            or stamp < max(progress.queued_at, progress.observed_at) or stamp > time.time() + 5:
        return False
    phases = ['loading', 'analyzing', 'converting']
    if progress.phase in phases:
        if phases.index(event.phase) < phases.index(progress.phase):
            return False
        if event.phase == progress.phase:
            if event.total != progress.phase_total or event.current <= progress.phase_current:
                return False
            tracker.advance(event.current, now=stamp)
            return True
    tracker.phase(event.phase, total=event.total, unit='chunks' if event.phase == 'converting' else 'tasks', now=stamp)
    if event.current:
        tracker.advance(event.current, now=stamp)
    return True


@dataclass
class ApplyLogReader:
    path: Path
    _offset: int = 0
    _pending: bytes = b''
    _discarding: bool = False
    _identity: tuple[int, int] | None = None

    def at_end(self) -> bool:
        try:
            return self._offset >= self.path.stat().st_size
        except OSError:
            return True

    def read(self) -> list[ApplyProgressEvent]:
        try:
            with self.path.open('rb') as handle:
                info = os.fstat(handle.fileno())
                identity = info.st_dev, info.st_ino
                if (self._identity is not None and identity != self._identity) or info.st_size < self._offset:
                    self._offset, self._pending, self._discarding = 0, b'', False
                self._identity = identity
                handle.seek(self._offset)
                block = handle.read(_READ_BYTES)
                self._offset += len(block)
        except OSError:
            return []
        events: list[ApplyProgressEvent] = []
        for part in block.splitlines(keepends=True):
            complete = part.endswith((b'\n', b'\r'))
            if not self._discarding:
                self._pending += part
                if len(self._pending) > _MAX_LINE:
                    self._discarding = True
                    self._pending = b''
            if complete:
                if not self._discarding:
                    event = parse_event(self._pending.decode('utf-8', errors='replace').strip())
                    if event is not None:
                        events.append(event)
                self._pending, self._discarding = b'', False
        return events
