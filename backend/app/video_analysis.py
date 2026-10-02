"""Bounded CPU song measurements; beat/section estimates remain manually editable.

8 kHz analysis, 100 ms windows and 50 ms hops are overview measurements, not
sample-accurate beat annotations or aligned lyrics. Channel power/magnitudes
are combined after analysis, preserving antiphase stereo energy.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import selectors
import subprocess
import sys
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .video_contracts import VideoContract, VideoEnergyPoint, VideoMarker, VideoSongAnalysis

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

SAMPLE_RATE = 8000
WINDOW = 800
HOP = 400
STEP_SEC = HOP / SAMPLE_RATE
MAX_SECONDS = 21600
MAX_POINTS = 4000
DECODE_TIMEOUT = 180.0
logger = logging.getLogger(__name__)


class AnalysisFailure(VideoContract):
    """Allowlisted worker errors; internal exception text never crosses the API."""
    error_code: Literal['analysis_unavailable', 'analysis_timeout', 'invalid_audio',
                        'audio_too_long', 'source_missing', 'analysis_failed']


def analysis_available() -> bool:
    try:
        import numpy
    except ImportError:
        return False
    return True


def planner_energy(analysis: VideoSongAnalysis) -> list[float]:
    """Average measured intervals into the legacy planner's one-second bins."""
    totals = [0.0] * math.ceil(analysis.duration_sec)
    weights = [0.0] * len(totals)
    points = sorted(analysis.energy, key=lambda point: point.time_sec)
    for index, point in enumerate(points):
        cursor = point.time_sec
        end = min(analysis.duration_sec, points[index + 1].time_sec
                  if index + 1 < len(points) else analysis.duration_sec)
        while cursor < end:
            second = int(cursor)
            stop = min(end, second + 1)
            weight = stop - cursor
            totals[second] += point.value * weight
            weights[second] += weight
            cursor = stop
    return [total / weight if weight else 0.0 for total, weight in zip(totals, weights, strict=True)]


class AnalysisError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _marker(kind: Literal['beat', 'onset', 'section'], seconds: float, confidence: float, label: str = '') -> VideoMarker:
    return VideoMarker(id=uuid.uuid5(uuid.NAMESPACE_URL, f'remiqora:video:{kind}:{seconds:.6f}').hex,
        time_sec=seconds, kind=kind, confidence=min(.95, max(0, confidence)), label=label)


def _report(rms: list[float], peaks: list[float], flux: list[float], duration: float) -> VideoSongAnalysis:
    import numpy as np
    if not rms or not math.isfinite(duration) or not 0 < duration <= MAX_SECONDS:
        raise AnalysisError('invalid_audio')
    warnings = ['beat_timing_is_estimated', 'sections_are_energy_heuristics',
                'lyrics_are_not_time_aligned', 'waveform_is_downsampled', 'energy_is_relative']
    stride = max(1, math.ceil(len(rms) / MAX_POINTS))
    peak_scale = max(max(peaks), 1e-12)
    rms_scale = max(max(rms), 1e-12)
    waveform = [min(1, max(peaks[index:index + stride]) / peak_scale) for index in range(0, len(peaks), stride)]
    energy = [VideoEnergyPoint(time_sec=min(duration, index * STEP_SEC),
        value=min(1, sum(rms[index:index + stride]) / len(rms[index:index + stride]) / rms_scale))
        for index in range(0, len(rms), stride)]
    novelty = np.asarray(flux, dtype=np.float64)
    maximum = float(np.max(novelty))
    normalized = novelty / max(maximum, 1e-12)
    threshold = max(.1, float(np.median(normalized)) + 2 * float(np.median(np.abs(normalized - np.median(normalized)))))
    onsets = [index for index in range(1, len(flux) - 1)
              if normalized[index] >= threshold and normalized[index] > normalized[index - 1]
              and normalized[index] >= normalized[index + 1]]
    markers: list[VideoMarker] = []
    onset_stride = max(1, math.ceil(len(onsets) / 1800))
    for index in onsets[::onset_stride]:
        markers.append(_marker('onset', min(duration, index * STEP_SEC), float(normalized[index]), 'Estimated onset'))
    tempo: float | None = None
    if len(onsets) >= 4 and maximum > 1e-8:
        # Direct autocorrelation over the small supported tempo-lag interval.
        # This is an onset-periodicity estimate; half/double tempo ambiguity remains.
        scores: list[tuple[float, int]] = []
        for lag in range(4, min(41, len(flux) // 2)):
            left, right = normalized[:-lag], normalized[lag:]
            denominator = math.sqrt(float(np.sum(left * left)) * float(np.sum(right * right)))
            correlation = float(np.sum(left * right)) / max(denominator, 1e-12)
            scores.append((correlation, lag))
        if scores:
            best = max(score for score, _lag in scores)
            candidates = [(score, lag) for score, lag in scores if score >= best - .02]
            confidence, lag = min(candidates, key=lambda item: item[1])
            if confidence >= .25:
                tempo = 60 / (lag * STEP_SEC)
                phase = max(range(lag), key=lambda offset: float(np.sum(normalized[offset::lag])))
                beat_frames = list(range(phase, len(flux), lag))
                beat_stride = max(1, math.ceil(len(beat_frames) / 1800))
                for index in beat_frames[::beat_stride]:
                    markers.append(_marker('beat', min(duration, index * STEP_SEC), confidence, 'Estimated beat'))
                warnings.append('tempo_may_be_half_or_double')
                if beat_stride > 1:
                    warnings.append('markers_decimated')
    if tempo is None:
        warnings.append('beat_estimate_unavailable')
    section_window = round(8 / STEP_SEC)
    previous_mean = 0.0
    sections = 0
    for index in range(0, len(rms), section_window):
        current = sum(rms[index:index + section_window]) / len(rms[index:index + section_window]) / rms_scale
        change = abs(current - previous_mean)
        if index > 0 and change >= .22 and sections < 40:
            markers.append(_marker('section', min(duration, index * STEP_SEC), min(.75, change), 'Energy change'))
            sections += 1
        previous_mean = current
    if onset_stride > 1 and 'markers_decimated' not in warnings:
        warnings.append('markers_decimated')
    markers.sort(key=lambda item: (item.time_sec, item.kind))
    return VideoSongAnalysis(duration_sec=duration, sample_rate=SAMPLE_RATE, waveform_peaks=waveform,
        waveform_step_sec=STEP_SEC * stride, energy=energy, markers=markers[:MAX_POINTS],
        tempo_bpm=tempo, warnings=warnings)


def _analyze_chunks(chunks: Iterable[NDArray[np.float64]]) -> VideoSongAnalysis:
    import numpy as np
    pending = np.empty((0, 2), dtype=np.float64)
    previous_spectrum = np.zeros(WINDOW // 2 + 1, dtype=np.float64)
    window = np.hanning(WINDOW)[:, None]
    rms: list[float] = []
    peaks: list[float] = []
    flux: list[float] = []
    total = 0
    for chunk in chunks:
        if chunk.ndim != 2 or chunk.shape[1] != 2 or not np.all(np.isfinite(chunk)):
            raise AnalysisError('invalid_audio')
        total += chunk.shape[0]
        if total > SAMPLE_RATE * MAX_SECONDS:
            raise AnalysisError('audio_too_long')
        pending = np.concatenate((pending, chunk), axis=0)
        start = 0
        while pending.shape[0] - start >= WINDOW:
            frame = pending[start:start + WINDOW]
            spectrum = np.mean(np.abs(np.fft.rfft(frame * window, axis=0)), axis=1)
            rms.append(float(np.sqrt(np.mean(frame * frame))))
            peaks.append(float(np.max(np.abs(frame))))
            flux.append(float(np.sum(np.maximum(spectrum - previous_spectrum, 0))))
            previous_spectrum = spectrum
            start += HOP
        pending = pending[start:]
    if pending.size:
        rms.append(float(np.sqrt(np.mean(pending * pending))))
        peaks.append(float(np.max(np.abs(pending))))
        flux.append(0.0)
    return _report(rms, peaks, flux, total / SAMPLE_RATE)


def analyze_samples(samples: object, sample_rate: int) -> VideoSongAnalysis:
    """Small CPU fixtures/already decoded audio; production files use streaming CLI."""
    import numpy as np
    if sample_rate != SAMPLE_RATE or isinstance(sample_rate, bool) or not isinstance(samples, np.ndarray):
        raise AnalysisError('invalid_audio')
    audio: NDArray[np.float64] = np.asarray(samples, dtype=np.float64)
    if audio.ndim == 1:
        audio = np.repeat(audio[:, None], 2, axis=1)
    if audio.ndim != 2 or audio.shape[1] != 2 or not 0 < audio.shape[0] <= SAMPLE_RATE * 120:
        raise AnalysisError('invalid_audio')
    return _analyze_chunks((audio,))


def analyze_song(path: Path, ffmpeg: str = 'ffmpeg') -> VideoSongAnalysis:
    """Stream at most six hours/180 CPU seconds; no weights, source writes or downloads."""
    try:
        import numpy as np
    except ImportError as exc:
        raise AnalysisError('analysis_unavailable') from exc
    if not path.is_file():
        raise AnalysisError('source_missing')
    command = [ffmpeg, '-v', 'error', '-nostdin', '-i', str(path), '-vn', '-ac', '2',
               '-ar', str(SAMPLE_RATE), '-f', 'f32le', '-']
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise AnalysisError('analysis_unavailable') from exc
    deadline = time.monotonic() + DECODE_TIMEOUT
    selector = selectors.DefaultSelector()
    if process.stdout is None:
        process.kill()
        process.wait()
        raise AnalysisError('analysis_failed')
    stream = process.stdout
    selector.register(stream, selectors.EVENT_READ)

    def chunks() -> Iterable[NDArray[np.float64]]:
        remainder = b''
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise AnalysisError('analysis_timeout')
            data = os.read(stream.fileno(), 65536)
            if not data:
                break
            data = remainder + data
            boundary = len(data) // 8 * 8
            audio = np.frombuffer(data[:boundary], dtype='<f4').astype(np.float64).reshape(-1, 2)
            remainder = data[boundary:]
            yield audio
        if remainder:
            raise AnalysisError('invalid_audio')

    try:
        report = _analyze_chunks(chunks())
        process.wait(timeout=max(.1, deadline - time.monotonic()))
        if process.returncode != 0:
            raise AnalysisError('analysis_failed')
        return report
    finally:
        selector.close()
        stream.close()
        if process.poll() is None:
            process.kill()
        process.wait()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    path: object = args.input
    binary: object = args.ffmpeg
    if not isinstance(path, Path) or not isinstance(binary, str):
        return 2
    try:
        print(analyze_song(path, binary).model_dump_json())
    except AnalysisError as exc:
        print(json.dumps({'error_code': exc.code}))
        return 2
    except Exception:
        logger.exception('Video CPU analysis failed')
        print(json.dumps({'error_code': 'analysis_failed'}))
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
