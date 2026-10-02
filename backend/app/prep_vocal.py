"""Measure original-time vocal candidates; screening is not singer identification.

The --analyze command never compacts time or changes samples. The legacy
preparation command joins only candidates that pass numerical screening.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TypedDict

import numpy as np
from numpy.typing import NDArray
import soundfile as sf
from scipy import signal

FloatArray = NDArray[np.float32]
AudioArray = NDArray[np.float32] | NDArray[np.float64]
SPLIT_GAP_SECONDS = 0.45
CHUNK_SECONDS = 10.0
MAX_KEEP_SECONDS = 15 * 60
JOIN_SECONDS = 0.25
CROSSFADE_SECONDS = 0.01


@dataclass(frozen=True)
class SegmentAnalysis:
    start_sec: float
    end_sec: float
    score: float
    periodicity: float
    level_db: float
    peak: float
    clipped_fraction: float
    accepted: bool
    reasons: tuple[str, ...]


class PrepReport(TypedDict):
    schema_version: int
    input_seconds: float
    output_seconds: float
    kept: int
    dropped: int
    level_db: float
    peak: float
    periodicity: float
    pitch: float
    max_gap_sec: float
    score: float
    segments: list[SegmentAnalysis]


def _as_channels(audio: AudioArray) -> FloatArray:
    channels = np.asarray(audio, dtype=np.float32)
    if channels.ndim == 1:
        channels = channels[:, None]
    if channels.ndim != 2 or not 1 <= channels.shape[1] <= 32:
        raise ValueError('invalid_audio_shape')
    return np.ascontiguousarray(channels)


def _mono(audio: FloatArray) -> FloatArray:
    """Analysis mix, falling back to a channel if stereo phase cancels energy."""
    if audio.shape[0] == 0:
        return np.zeros(0, dtype=np.float32)
    mixed = audio.mean(axis=1).astype(np.float32)
    energies = np.mean(audio.astype(np.float64) ** 2, axis=0)
    if float(np.mean(mixed.astype(np.float64) ** 2)) < 0.25 * float(np.max(energies)):
        return audio[:, int(np.argmax(energies))].copy()
    return mixed


def safe_mono(audio: AudioArray) -> FloatArray:
    """Downmix a copy, preserving voiced energy when stereo phase cancels it."""
    channels = _as_channels(audio)
    if not np.isfinite(channels).all():
        raise ValueError('nonfinite_audio')
    return _mono(channels)


def _frame_rms(mono: FloatArray, sample_rate: int) -> tuple[NDArray[np.float64], int]:
    frame = max(1, int(0.02 * sample_rate))
    count = mono.shape[0] // frame
    if count == 0:
        return np.array([np.sqrt(np.mean(mono.astype(np.float64) ** 2)) if mono.size else 0.0]), frame
    frames = mono[:count * frame].reshape(count, frame).astype(np.float64)
    return np.sqrt(np.mean(frames ** 2, axis=1)), frame


def _silence_runs(mono: FloatArray, sample_rate: int) -> list[tuple[bool, int, int]]:
    rms, frame = _frame_rms(mono, sample_rate)
    threshold = max(1e-5, float(np.percentile(rms, 98)) * 0.06)
    silent = rms < threshold
    runs: list[tuple[bool, int, int]] = []
    start = 0
    for index in range(1, silent.shape[0]):
        if silent[index] != silent[start]:
            runs.append((bool(silent[start]), start * frame, index * frame))
            start = index
    runs.append((bool(silent[start]), start * frame, mono.shape[0]))
    return [(kind, begin, end) for kind, begin, end in runs if end > begin]


def _pitch_strength(mono: FloatArray, sample_rate: int) -> float:
    """Bounded autocorrelation periodicity, with filtered analysis resampling."""
    if sample_rate > 16000:
        original_energy = float(np.mean(mono.astype(np.float64) ** 2))
        divisor = math.gcd(sample_rate, 16000)
        mono = np.asarray(signal.resample_poly(mono, 16000 // divisor, sample_rate // divisor), dtype=np.float32)
        if float(np.mean(mono.astype(np.float64) ** 2)) < original_energy * 0.0001:
            return 0.0
        sample_rate = 16000
    frame = int(0.032 * sample_rate)
    if mono.shape[0] < frame or frame < 8:
        return 0.0
    hops = max(1, (mono.shape[0] - frame) // 24)
    min_lag = max(1, int(sample_rate / 1100))
    max_lag = min(frame - 1, int(sample_rate / 70))
    if max_lag <= min_lag:
        return 0.0
    window = np.hanning(frame).astype(np.float64)
    strengths: list[float] = []
    for start in range(0, mono.shape[0] - frame + 1, hops):
        chunk = mono[start:start + frame].astype(np.float64)
        chunk -= chunk.mean()
        if float(np.sqrt(np.mean(chunk ** 2))) < 1e-4:
            continue
        chunk *= window
        spectrum = np.fft.rfft(chunk, n=frame * 2)
        corr = np.fft.irfft(spectrum * np.conj(spectrum))[:frame]
        if corr[0] > 1e-8:
            strengths.append(float(np.max(corr[min_lag:max_lag] / corr[0])))
    return float(np.clip(np.median(strengths), 0, 1)) if strengths else 0.0


def _flatness(mono: FloatArray) -> float:
    frame = min(2048, mono.shape[0])
    if frame < 32:
        return 1.0
    values: list[float] = []
    hop = max(frame, mono.shape[0] // 8)
    for start in range(0, max(1, mono.shape[0] - frame + 1), hop):
        chunk = mono[start:start + frame].astype(np.float64)
        if chunk.size < frame:
            break
        magnitude = np.abs(np.fft.rfft((chunk - chunk.mean()) * np.hanning(frame))) + 1e-12
        values.append(float(np.exp(np.mean(np.log(magnitude))) / np.mean(magnitude)))
    return float(np.median(values)) if values else 1.0


def _measure(audio: FloatArray, sample_rate: int, start: int, end: int) -> SegmentAnalysis:
    finite = bool(np.isfinite(audio).all())
    safe = np.where(np.isfinite(audio), audio, 0).astype(np.float32)
    mono = _mono(safe)
    rms = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2))) if mono.size else 0.0
    level = float(20 * np.log10(max(rms, 1e-6)))
    peak = float(np.max(np.abs(safe))) if safe.size else 0.0
    clipped = float(np.mean(np.abs(safe) >= 0.98)) if safe.size else 0.0
    periodicity = _pitch_strength(mono, sample_rate)
    level_score = max(0.0, 1.0 - abs(level + 16.0) / 26.0)
    score = float(np.clip(0.62 * periodicity + 0.18 * (1 - _flatness(mono)) + 0.2 * level_score - 0.8 * clipped, 0, 1))
    rejection: list[str] = []
    if not finite:
        rejection.append('nonfinite')
    if (end - start) / sample_rate < 1:
        rejection.append('too_short')
    if level < -40:
        rejection.append('too_quiet')
    if periodicity < 0.34:
        rejection.append('low_periodicity')
    if clipped > 0.01:
        rejection.append('clipped')
    if score < 0.28:
        rejection.append('low_screening_score')
    accepted = not rejection
    reasons = rejection + (['identity_unverified', 'periodicity_is_not_voice_detection'] if accepted else [])
    return SegmentAnalysis(start / sample_rate, end / sample_rate, score, periodicity, level, peak, clipped, accepted, tuple(reasons))


def analyze_segments(audio: AudioArray, sample_rate: int) -> list[SegmentAnalysis]:
    """Partition original time into <=10s candidates, including rejected gaps."""
    if not 1 <= sample_rate <= 384000:
        raise ValueError('invalid_sample_rate')
    channels = _as_channels(audio)
    if not channels.size:
        return []
    safe = np.where(np.isfinite(channels), channels, 0).astype(np.float32)
    boundaries = {0, channels.shape[0]}
    for silent, begin, end in _silence_runs(_mono(safe), sample_rate):
        if silent and (end - begin) / sample_rate >= SPLIT_GAP_SECONDS:
            boundaries.update((begin, end))
    ordered = sorted(boundaries)
    segments: list[SegmentAnalysis] = []
    chunk = max(1, int(CHUNK_SECONDS * sample_rate))
    for begin, end in zip(ordered, ordered[1:]):
        for start in range(begin, end, chunk):
            stop = min(end, start + chunk)
            segments.append(_measure(channels[start:stop], sample_rate, start, stop))
    return segments


def _crossfade_all(parts: list[FloatArray], fade: int, channels: int) -> FloatArray:
    usable = [part for part in parts if part.size]
    if not usable:
        return np.zeros((0, channels), dtype=np.float32)
    out = np.empty((sum(part.shape[0] for part in usable), channels), dtype=np.float32)
    cursor = usable[0].shape[0]
    out[:cursor] = usable[0]
    for part in usable[1:]:
        fade_n = min(fade, cursor, part.shape[0])
        start = cursor - fade_n
        if fade_n:
            ramp = np.linspace(0, 1, fade_n, dtype=np.float32)[:, None]
            out[start:cursor] = out[start:cursor] * (1 - ramp) + part[:fade_n] * ramp
        rest = part.shape[0] - fade_n
        out[cursor:cursor + rest] = part[fade_n:]
        cursor += rest
    return out[:cursor]


def _max_internal_gap(mono: FloatArray, sample_rate: int) -> float:
    runs = _silence_runs(mono, sample_rate)
    return max(((end - begin) / sample_rate for index, (silent, begin, end) in enumerate(runs)
                if silent and index not in (0, len(runs) - 1)), default=0.0)


def prepare_vocal(audio: AudioArray, sample_rate: int, *, limit_duration: bool = False,
                  max_keep_seconds: float = MAX_KEEP_SECONDS) -> tuple[FloatArray, PrepReport]:
    channels = _as_channels(audio)
    segments = analyze_segments(channels, sample_rate)
    kept = [segment for segment in segments if segment.accepted]
    if limit_duration:
        selected: list[SegmentAnalysis] = []
        total = 0.0
        for segment in sorted(kept, key=lambda item: item.score, reverse=True):
            if total >= max_keep_seconds:
                break
            selected.append(segment)
            total += segment.end_sec - segment.start_sec
        kept = sorted(selected, key=lambda item: item.start_sec)
    parts: list[FloatArray] = []
    previous_end = 0
    for segment in kept:
        begin, end = round(segment.start_sec * sample_rate), round(segment.end_sec * sample_rate)
        if parts and begin > previous_end:
            gap = min(JOIN_SECONDS, (begin - previous_end) / sample_rate)
            parts.append(np.zeros((round(gap * sample_rate), channels.shape[1]), dtype=np.float32))
        parts.append(channels[begin:end])
        previous_end = end
    prepared = _crossfade_all(parts, int(CROSSFADE_SECONDS * sample_rate), channels.shape[1])
    mono = _mono(prepared)
    rms = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2))) if mono.size else 0.0
    periodicity = float(np.median([segment.periodicity for segment in kept])) if kept else 0.0
    report: PrepReport = {
        'schema_version': 2, 'input_seconds': channels.shape[0] / sample_rate,
        'output_seconds': prepared.shape[0] / sample_rate, 'kept': len(kept),
        'dropped': len(segments) - len(kept), 'level_db': float(20 * np.log10(max(rms, 1e-6))),
        'peak': float(np.max(np.abs(prepared))) if prepared.size else 0.0,
        'periodicity': periodicity, 'pitch': periodicity,
        'max_gap_sec': _max_internal_gap(mono, sample_rate) if mono.size else 0.0,
        'score': float(np.median([segment.score for segment in kept])) if kept else 0.0,
        'segments': segments,
    }
    return prepared, report


def main(argv: list[str]) -> int:
    args = argv[1:]
    analyze = bool(args and args[0] == '--analyze')
    limit = bool(args and args[0] == '--limit')
    mono = bool(args and args[0] == '--mono')
    if analyze or limit or mono:
        args = args[1:]
    if len(args) != (1 if analyze else 2):
        print('usage: prep_vocal.py --analyze INPUT | --mono INPUT OUTPUT | [--limit] INPUT OUTPUT', file=sys.stderr)
        return 2
    source = Path(args[0])
    if mono and source.resolve() == Path(args[1]).resolve():
        print('original_overwrite_forbidden', file=sys.stderr)
        return 2
    audio, sample_rate = sf.read(source, always_2d=True)
    if analyze:
        segments = analyze_segments(audio, int(sample_rate))
        print(json.dumps({'schema_version': 2, 'input_seconds': audio.shape[0] / sample_rate,
                          'segments': [asdict(segment) for segment in segments]}, allow_nan=False), flush=True)
        return 0
    if mono:
        prepared = safe_mono(audio)
    else:
        prepared, report = prepare_vocal(audio, int(sample_rate), limit_duration=limit)
        payload = dict(report)
        payload['segments'] = [asdict(segment) for segment in report['segments']]
        print(json.dumps(payload, allow_nan=False), flush=True)
    if not mono and prepared.shape[0] < int(0.5 * sample_rate):
        print('insufficient_usable_audio', file=sys.stderr)
        return 3
    dest = Path(args[1])
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix('.partial.wav')
    try:
        sf.write(partial, prepared, int(sample_rate), subtype='FLOAT')
        partial.replace(dest)
    finally:
        partial.unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main(sys.argv))
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(1) from exc
