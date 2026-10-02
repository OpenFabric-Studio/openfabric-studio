"""Observed passage coverage, not singer identification or a quality guarantee.

pYIN uses 50–2000 Hz, filtered 16 kHz analysis, 2048-sample windows and
320-sample hops. Reliable frames require estimated voicing probability >=0.8
and RMS >=-60 dBFS. That probability describes voicing, not pitch correctness.
Only disjoint 20 ms frame spans count toward durations; window overlap and
unanalyzed edges never inflate the amount of observed audio. Passages are
analyzed independently so edits cannot invent pitch continuity between files.
CLI input is limited to 30 seconds per passage and 1000 passages per batch.

Spectral proxies use the original sample rate: 95% Welch power rolloff and
power at/above 4 kHz. They also vary with vowels, timbre, noise and separation;
they cannot prove recording fidelity or recover missing information.

Backend imports and summarize_coverage need no DSP packages. The CLI and
analyze_coverage import installed CPU dependencies only when requested.
"""
from __future__ import annotations

import importlib
import json
import math
import sys
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import Field, model_validator

from .contracts import Contract
from .voice_contracts import VoiceCoverageMeasurement, VoiceCoverageSummary, VoicePitchBin

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray
    from .prep_vocal import AudioArray

ANALYSIS_SAMPLE_RATE = 16000
FRAME_LENGTH = 2048
HOP_LENGTH = 320
FMIN = 50.0
FMAX = 2000.0
MIN_VOICING_PROBABILITY = .8
RMS_FLOOR = .001
MAX_PASSAGE_SECONDS = 30
_LIMITATIONS = ('observed_pitch_is_not_full_vocal_range', 'spectral_measurement_is_not_recording_quality', 'identity_unverified')


class CoverageError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CoverageInput(Contract):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    path: str = Field(min_length=1, max_length=8192)


class CoverageBatchInput(Contract):
    inputs: list[CoverageInput] = Field(min_length=1, max_length=1000)

    @model_validator(mode='after')
    def unique_ids(self) -> CoverageBatchInput:
        if len({item.id for item in self.inputs}) != len(self.inputs):
            raise ValueError('duplicate_coverage_id')
        return self


class CoverageBatchReport(Contract):
    measurements: dict[str, VoiceCoverageMeasurement] = Field(default_factory=dict)
    failed_ids: list[str] = Field(default_factory=list)


@runtime_checkable
class PitchEstimator(Protocol):
    """Inspected librosa 0.10.2/0.11.0 pyin call; validate returned arrays below."""
    def __call__(self, y: AudioArray, *, sr: int, fmin: float, fmax: float,
                 frame_length: int, hop_length: int, center: bool,
                 resolution: float, fill_na: float) -> object: ...


def _pitch_estimator() -> PitchEstimator:
    try:
        module = importlib.import_module('librosa')
        function: object = getattr(module, 'pyin', None)
    except ImportError as exc:
        raise CoverageError('engine_incompatible') from exc
    if not isinstance(function, PitchEstimator):
        raise CoverageError('engine_incompatible')
    return function


def _pitch_arrays(raw: object, expected: int) -> tuple[NDArray[np.float64], NDArray[np.bool_], NDArray[np.float64]]:
    import numpy as np
    if not isinstance(raw, tuple) or len(raw) != 3:
        raise CoverageError('engine_incompatible')
    frequency, voiced, probability = raw
    # Explicit narrowing keeps this untrusted third-party result out of callers.
    if not isinstance(frequency, np.ndarray) or not isinstance(voiced, np.ndarray) or not isinstance(probability, np.ndarray):
        raise CoverageError('engine_incompatible')
    if any(value.shape != (expected,) for value in (frequency, voiced, probability)):
        raise CoverageError('engine_incompatible')
    if frequency.dtype.kind != 'f' or voiced.dtype.kind != 'b' or probability.dtype.kind != 'f':
        raise CoverageError('engine_incompatible')
    frequencies = np.asarray(frequency, dtype=np.float64)
    probabilities = np.asarray(probability, dtype=np.float64)
    if not np.isfinite(probabilities).all() or np.any((probabilities < 0) | (probabilities > 1)):
        raise CoverageError('engine_incompatible')
    return frequencies, np.asarray(voiced, dtype=np.bool_), probabilities


def _spectral(mono: NDArray[np.float64], sample_rate: int) -> tuple[float | None, float | None]:
    import numpy as np
    from scipy import signal
    if mono.size < 2:
        return None, None
    frequencies, power = signal.welch(mono, fs=sample_rate, nperseg=min(2048, mono.size), detrend='constant')
    total = float(power.sum())
    if not math.isfinite(total) or total <= 1e-20:
        return None, None
    cumulative = np.cumsum(power)
    index = min(int(np.searchsorted(cumulative, total * .95)), frequencies.size - 1)
    rolloff = float(frequencies[index])
    high = float(power[frequencies >= 4000].sum() / total) if sample_rate > 8000 else None
    return rolloff, min(1.0, max(0.0, high)) if high is not None else None


def analyze_coverage(audio: AudioArray, sample_rate: int) -> VoiceCoverageMeasurement:
    """Measure one unmodified passage; raise stable errors for invalid input."""
    import numpy as np
    from scipy import signal
    from .prep_vocal import safe_mono
    if isinstance(sample_rate, bool) or not 1 <= sample_rate <= 384000:
        raise CoverageError('invalid_audio')
    try:
        mono = safe_mono(audio).astype(np.float64)
    except ValueError as exc:
        raise CoverageError('invalid_audio') from exc
    if not np.isfinite(mono).all():
        raise CoverageError('invalid_audio')
    duration = mono.size / sample_rate
    if duration > MAX_PASSAGE_SECONDS:
        raise CoverageError('invalid_audio')
    rolloff, high = _spectral(mono, sample_rate)
    warnings = list(_LIMITATIONS)
    if sample_rate <= 8000:
        warnings.append('limited_analysis_bandwidth')
    report = VoiceCoverageMeasurement(duration_sec=duration, analyzed_sec=0, voiced_sec=0, reliable_voiced_sec=0,
        source_sample_rate=sample_rate, spectral_rolloff95_hz=rolloff, high_band_energy_fraction=high, warnings=warnings)
    if sample_rate != ANALYSIS_SAMPLE_RATE:
        divisor = math.gcd(sample_rate, ANALYSIS_SAMPLE_RATE)
        mono = np.asarray(signal.resample_poly(mono, ANALYSIS_SAMPLE_RATE // divisor, sample_rate // divisor), dtype=np.float64)
    if mono.size < FRAME_LENGTH:
        report.warnings.append('no_reliable_pitch')
        return report
    frames = np.lib.stride_tricks.sliding_window_view(mono, FRAME_LENGTH)[::HOP_LENGTH]
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    raw = _pitch_estimator()(mono, sr=ANALYSIS_SAMPLE_RATE, fmin=FMIN, fmax=FMAX, frame_length=FRAME_LENGTH,
        hop_length=HOP_LENGTH, center=False, resolution=.2, fill_na=float('nan'))
    frequency, voiced, probability = _pitch_arrays(raw, frames.shape[0])
    valid = voiced & np.isfinite(frequency) & (frequency >= FMIN) & (frequency <= FMAX) & (rms >= RMS_FLOOR)
    reliable = valid & (probability >= MIN_VOICING_PROBABILITY)
    frame_seconds = HOP_LENGTH / ANALYSIS_SAMPLE_RATE
    report.analyzed_sec = min(duration, frames.shape[0] * frame_seconds)
    report.voiced_sec = min(report.analyzed_sec, int(np.count_nonzero(valid)) * frame_seconds)
    report.reliable_voiced_sec = min(report.voiced_sec, int(np.count_nonzero(reliable)) * frame_seconds)
    if not np.any(reliable):
        report.warnings.append('no_reliable_pitch')
        return report
    pitches = frequency[reliable]
    percentiles = np.percentile(pitches, [5, 50, 95])
    report.pitch_p05_hz, report.pitch_median_hz, report.pitch_p95_hz = (float(value) for value in percentiles)
    report.median_voicing_probability = float(np.median(probability[reliable]))
    bins: dict[int, float] = defaultdict(float)
    for pitch in pitches:
        note = round(69 + 12 * math.log2(float(pitch) / 440))
        bins[max(0, min(127, note))] += frame_seconds
    report.pitch_bins = [VoicePitchBin(midi_note=note, seconds=seconds) for note, seconds in sorted(bins.items())]
    if report.reliable_voiced_sec < .25 * report.analyzed_sec:
        report.warnings.append('sparse_reliable_pitch')
    if np.any(pitches <= FMIN * 2 ** (1 / 12)) or np.any(pitches >= FMAX / 2 ** (1 / 12)):
        report.warnings.append('pitch_near_analysis_boundary')
    return report


def _weighted_pitch(bins: list[VoicePitchBin], percentile: float) -> float | None:
    total = sum(item.seconds for item in bins)
    if total <= 0:
        return None
    accumulated = 0.0
    for item in bins:
        accumulated += item.seconds
        if accumulated >= total * percentile:
            return 440 * 2 ** ((item.midi_note - 69) / 12)
    return None


def _weighted_proxy(measurements: list[VoiceCoverageMeasurement], getter: Callable[[VoiceCoverageMeasurement], float | None]) -> float | None:
    weighted, seconds = 0.0, 0.0
    for measurement in measurements:
        value = getter(measurement)
        if value is not None and measurement.duration_sec > 0:
            weighted += value * measurement.duration_sec
            seconds += measurement.duration_sec
    return weighted / seconds if seconds else None


def summarize_coverage(measurements: list[VoiceCoverageMeasurement], unavailable_segments: int = 0) -> VoiceCoverageSummary:
    """Sum disjoint frame durations; pitch quantiles use MIDI-bin centers.

    Spectral proxies are duration-weighted means of available passage values,
    not a pooled spectrum, recording-quality rating, or inferred singer range.
    """
    if unavailable_segments < 0:
        raise ValueError('invalid_unavailable_segments')
    bins: dict[int, float] = defaultdict(float)
    warnings: set[str] = set()
    for measurement in measurements:
        for item in measurement.pitch_bins:
            bins[item.midi_note] += item.seconds
        warnings.update(measurement.warnings)
    histogram = [VoicePitchBin(midi_note=note, seconds=seconds) for note, seconds in sorted(bins.items())]
    if measurements:
        warnings.update(_LIMITATIONS)
    if unavailable_segments:
        warnings.add('coverage_unavailable')
    if not histogram:
        warnings.add('no_reliable_pitch')
    return VoiceCoverageSummary(duration_sec=sum(item.duration_sec for item in measurements),
        analyzed_sec=sum(item.analyzed_sec for item in measurements), voiced_sec=sum(item.voiced_sec for item in measurements),
        reliable_voiced_sec=sum(item.reliable_voiced_sec for item in measurements), pitch_bins=histogram,
        pitch_p05_hz=_weighted_pitch(histogram, .05), pitch_median_hz=_weighted_pitch(histogram, .5),
        pitch_p95_hz=_weighted_pitch(histogram, .95), measurements=len(measurements), unavailable_segments=unavailable_segments,
        spectral_rolloff95_hz=_weighted_proxy(measurements, lambda item: item.spectral_rolloff95_hz),
        high_band_energy_fraction=_weighted_proxy(measurements, lambda item: item.high_band_energy_fraction), warnings=sorted(warnings))


def analyze_file(path: Path) -> VoiceCoverageMeasurement:
    try:
        import soundfile as sf
        header, sample_rate = sf.read(path, frames=0, always_2d=True)
        if not 1 <= sample_rate <= 384000 or not 1 <= header.shape[1] <= 32:
            raise CoverageError('invalid_audio')
        # Inspect the header without loading an arbitrary recording into memory.
        # One extra frame distinguishes a full 30s passage from a longer file.
        audio, actual_rate = sf.read(path, frames=sample_rate * MAX_PASSAGE_SECONDS + 1, always_2d=True)
        if actual_rate != sample_rate or audio.shape[0] > sample_rate * MAX_PASSAGE_SECONDS:
            raise CoverageError('invalid_audio')
        return analyze_coverage(audio, sample_rate)
    except CoverageError:
        raise
    except ImportError as exc:
        raise CoverageError('engine_incompatible') from exc
    except Exception as exc:
        raise CoverageError('invalid_audio') from exc


def main(argv: list[str]) -> int:
    args = argv[1:]
    if len(args) == 2 and args[0] == '--batch':
        try:
            manifest = CoverageBatchInput.model_validate_json(Path(args[1]).read_text(encoding='utf-8'))
        except (ValueError, OSError):
            print(json.dumps({'error_code': 'invalid_coverage_manifest'}), file=sys.stderr)
            return 2
        report = CoverageBatchReport()
        for item in manifest.inputs:
            try:
                report.measurements[item.id] = analyze_file(Path(item.path))
            except CoverageError:
                report.failed_ids.append(item.id)
        print(report.model_dump_json(), flush=True)
        return 0
    if len(args) != 1 or args[0].startswith('--'):
        print(json.dumps({'error_code': 'invalid_coverage_arguments'}), file=sys.stderr)
        return 2
    try:
        measurement = analyze_file(Path(args[0]))
    except CoverageError as exc:
        print(json.dumps({'error_code': exc.code}), file=sys.stderr)
        return 1
    print(measurement.model_dump_json(), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
