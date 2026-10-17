"""Private bounded beat-worker protocol and conservative grid summaries.

No model import, download, process registry or public path lives in this module.
"""
from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Literal, Self

import numpy as np
from pydantic import Field, model_validator

from .music_analysis_contracts import (MeterEstimate, MusicAnalysisContract, MusicAnalysisSha256,
    MusicAnalysisTimes, MusicAnalysisWarning, TempoEstimate)

SOURCE_COMMIT: Literal['ad7974846029835307ba19a3d5cefbf40b243041'] = 'ad7974846029835307ba19a3d5cefbf40b243041'
BEATS_REVISION: Literal['beat-this-small0-cpu-v1'] = 'beat-this-small0-cpu-v1'
MAX_WINDOW_FRAMES = 2646000
MAX_WINDOW_SECONDS = 120.0
LICENSE_EVIDENCE_IDS = ('beat-this-code-mit', 'beat-this-small0-weights-mit')


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class BeatRuntimeIdentity(MusicAnalysisContract):
    revision: Literal['beat-this-small0-cpu-v1'] = BEATS_REVISION
    source_commit: Literal['ad7974846029835307ba19a3d5cefbf40b243041'] = SOURCE_COMMIT
    source_sha256: MusicAnalysisSha256
    weights_sha256: MusicAnalysisSha256
    python_sha256: MusicAnalysisSha256
    python_version: str = Field(min_length=5, max_length=40)
    packages: dict[str, str] = Field(min_length=8, max_length=8)

    @property
    def configuration_sha256(self) -> str:
        return digest({'identity': self.model_dump(), 'cpu_threads': 1, 'float16': False, 'dbn': False,
            'rate': 22050, 'window_sec': 120, 'overlap_sec': 10, 'channel': 'maximum-energy-channel-v1',
            'stitch': 'central-events-20ms-v1', 'tempo': 'median-stable-interval-v1'})


class BeatWindowResult(MusicAnalysisContract):
    duration_sec: float = Field(gt=0, le=120)
    beats_sec: MusicAnalysisTimes = Field(default_factory=list, max_length=6001)
    downbeats_sec: MusicAnalysisTimes = Field(default_factory=list, max_length=6001)

    @model_validator(mode='after')
    def timeline(self) -> Self:
        if any(value >= self.duration_sec for value in self.beats_sec + self.downbeats_sec):
            raise ValueError('beat_time_outside_window')
        for value in self.downbeats_sec:
            index = bisect_left(self.beats_sec, value)
            if not any(abs(candidate - value) <= .020000001 for candidate in self.beats_sec[max(0, index - 1):index + 1]):
                raise ValueError('downbeat_detached_from_beat')
        return self


class BeatWorkerRequest(MusicAnalysisContract):
    schema_version: Literal[1] = 1
    runtime_root: str = Field(min_length=1, max_length=4096)
    checkpoint_name: Literal['beat-checkpoint.ckpt'] = 'beat-checkpoint.ckpt'
    input_name: Literal['beats-input.f32'] = 'beats-input.f32'
    input_sha256: MusicAnalysisSha256
    frames: int = Field(ge=1, le=MAX_WINDOW_FRAMES)
    channels: int = Field(ge=1, le=2)
    sample_rate_hz: Literal[22050] = 22050
    identity: BeatRuntimeIdentity


class BeatAnalysisRecipe(MusicAnalysisContract):
    runtime_root: str = Field(min_length=1, max_length=4096)
    identity: BeatRuntimeIdentity


class BeatWorkerOutcome(MusicAnalysisContract):
    input_sha256: MusicAnalysisSha256
    configuration_sha256: MusicAnalysisSha256
    result: BeatWindowResult


@dataclass(frozen=True)
class TimedBeatWindow:
    start_sec: float
    central_start_sec: float
    central_end_sec: float
    result: BeatWindowResult


@dataclass(frozen=True)
class BeatGrid:
    beats_sec: list[float]
    downbeats_sec: list[float]
    tempo: TempoEstimate
    meter: MeterEstimate
    warnings: list[MusicAnalysisWarning]


def stitch_windows(windows: Sequence[TimedBeatWindow], duration: float) -> BeatGrid:
    if not math.isfinite(duration) or not 0 < duration <= 7200 or not 1 <= len(windows) <= 66:
        raise ValueError('beat_window_budget')
    beats: list[float] = []
    downbeats: list[float] = []
    previous_end = 0.0
    for window in windows:
        values = (window.start_sec, window.central_start_sec, window.central_end_sec)
        if (not all(math.isfinite(value) for value in values)
                or not 0 <= window.start_sec <= window.central_start_sec < window.central_end_sec <= duration
                or window.central_end_sec > window.start_sec + window.result.duration_sec + .002000001
                or abs(window.central_start_sec - previous_end) > 1e-7):
            raise ValueError('beat_windows_not_contiguous')
        for relative in window.result.beats_sec:
            value = relative + window.start_sec
            if window.central_start_sec <= value < window.central_end_sec:
                if not beats or value - beats[-1] > .020000001:
                    beats.append(value)
        for relative in window.result.downbeats_sec:
            value = relative + window.start_sec
            if window.central_start_sec <= value < window.central_end_sec and beats:
                index = bisect_left(beats, value)
                candidates = beats[max(0, index - 1):index + 1]
                nearest = min(candidates, key=lambda candidate: abs(candidate - value))
                if abs(nearest - value) <= .020000001 and (not downbeats or nearest > downbeats[-1]):
                    downbeats.append(nearest)
        previous_end = window.central_end_sec
    if abs(previous_end - duration) > 1e-7 or len(beats) > 50000:
        raise ValueError('beat_window_budget')
    tempo = TempoEstimate(half_double_ambiguous=bool(beats))
    if len(beats) >= 8:
        intervals = np.diff(np.asarray(beats, dtype=np.float64))
        median = float(np.median(intervals))
        bpm = 60 / median
        # Descriptive timing regularity, never a calibrated model confidence.
        if 20 <= bpm <= 400 and float(np.quantile(intervals, .9)) <= float(np.quantile(intervals, .1)) * 1.2:
            tempo = TempoEstimate(status='known', bpm=bpm, confidence=None,
                confidence_method='median-stable-beat-interval-v1', half_double_ambiguous=True)
    warnings: list[MusicAnalysisWarning] = ['beat_grid_uncertain', 'meter_unknown']
    if beats:
        warnings.append('tempo_half_double')
    if len(windows) > 1:
        warnings.extend(('window_boundary_uncertain', 'analysis_partial'))
    return BeatGrid(beats, downbeats, tempo, MeterEstimate(), warnings)
