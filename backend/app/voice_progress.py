"""Small shared tracker for persisted voice jobs and measured per-phase rates."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from .voice_contracts import VoiceJobProgress, VoiceProgressPhase as ProgressPhase, VoiceProgressUnit as ProgressUnit

ProgressKind = Literal['preparation', 'coverage', 'build', 'apply']
TerminalStatus = Literal['done', 'failed', 'cancelled']


def read_progress(value: object) -> VoiceJobProgress | None:
    """Legacy metadata has no timing. Damaged optional timing cannot invent it."""
    if value is None:
        return None
    try:
        return VoiceJobProgress.model_validate(value)
    except ValidationError:
        return None


def finish_progress(progress: VoiceJobProgress | None, status: TerminalStatus, *, now: float | None = None) -> None:
    if progress is None:
        return
    stamp = time.time() if now is None else now
    progress.status = status
    if progress.finished_at is None:
        progress.finished_at = max(stamp, progress.started_at or progress.queued_at)
    progress.estimated_phase_remaining_sec = None
    progress.queue_reason, progress.queue_label = '', ''


@dataclass
class VoiceProgressTracker:
    progress: VoiceJobProgress
    _anchor_at: float | None = None
    _anchor_current: int = 0
    _deltas: int = 0

    @classmethod
    def create(cls, kind: ProgressKind, revision: str, *, files_total: int = 0, now: float | None = None) -> VoiceProgressTracker:
        stamp = time.time() if now is None else now
        return cls(VoiceJobProgress(job_id=uuid.uuid4().hex, kind=kind, preparation_revision=revision,
            queued_at=stamp, observed_at=stamp, files_total=files_total))

    def start(self, *, now: float | None = None) -> None:
        stamp = time.time() if now is None else now
        self.progress.status = 'running'
        if self.progress.started_at is None:
            self.progress.started_at = max(stamp, self.progress.queued_at)
        self.progress.observed_at = max(stamp, self.progress.started_at)

    def phase(self, phase: ProgressPhase, *, total: int, unit: ProgressUnit, current: int = 0,
        current_file: str = '', now: float | None = None) -> None:
        stamp = time.time() if now is None else now
        self.progress.phase, self.progress.phase_unit = phase, unit
        self.progress.phase_current, self.progress.phase_total = current, total
        self.progress.current_file = current_file
        self.progress.phase_started_at, self.progress.observed_at = stamp, stamp
        self.progress.estimated_phase_remaining_sec = None
        self.progress.queue_reason, self.progress.queue_label = '', ''
        self._anchor_at, self._anchor_current, self._deltas = None, 0, 0

    def advance(self, completed: int, *, now: float | None = None) -> None:
        stamp = time.time() if now is None else now
        current = min(max(0, completed), self.progress.phase_total)
        if current <= self.progress.phase_current:
            return
        self.progress.phase_current, self.progress.observed_at = current, stamp
        if self._anchor_at is None:
            # Exclude startup/model warm-up before the first measured completion.
            self._anchor_at, self._anchor_current = stamp, current
            return
        self._deltas += 1
        elapsed, advanced = stamp - self._anchor_at, current - self._anchor_current
        if self._deltas >= 2 and elapsed > 0 and advanced > 0:
            self.progress.estimated_phase_remaining_sec = elapsed / advanced * (self.progress.phase_total - current)

    def finish(self, status: TerminalStatus, *, now: float | None = None) -> None:
        finish_progress(self.progress, status, now=now)
