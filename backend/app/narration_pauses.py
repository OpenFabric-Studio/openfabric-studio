"""Split narration audio on local energy pauses.

Parakeet and other VAD weights are not downloaded. Their weight licenses were
not treated as cleared for this application, so a chapter is never cut through
a loud region. Silence is plain PCM energy.
"""
from __future__ import annotations

import math
import wave
from pathlib import Path


def chunk_pcm(samples: bytes, sample_rate: int, sampwidth: int, channels: int, *,
              min_silence_ms: int = 280) -> list[tuple[int, int]]:
    """Return frame spans. Without a long enough pause, the buffer stays whole."""
    if sampwidth != 2 or channels < 1 or not 8000 <= sample_rate <= 192000:
        raise ValueError("unsupported_pcm")
    frame = sampwidth * channels
    count = len(samples) // frame
    if count == 0:
        return []
    mono: list[int] = []
    for index in range(count):
        base = index * frame
        total = 0
        for channel in range(channels):
            offset = base + channel * 2
            total += int.from_bytes(samples[offset:offset + 2], "little", signed=True)
        mono.append(total // channels)
    window = max(1, sample_rate * 20 // 1000)
    energies: list[float] = []
    for start in range(0, count, window):
        chunk = mono[start:start + window]
        energies.append(math.sqrt(sum(sample * sample for sample in chunk) / len(chunk)))
    peak = max(energies) if energies else 0.0
    if peak <= 1:
        return [(0, count)]
    floor = max(peak * 0.12, 1.0)
    min_windows = max(1, (min_silence_ms * sample_rate // 1000 + window - 1) // window)
    silent = [energy <= floor for energy in energies]
    splits: list[int] = []
    index = 0
    while index < len(silent):
        if not silent[index]:
            index += 1
            continue
        end = index
        while end < len(silent) and silent[end]:
            end += 1
        if end - index >= min_windows:
            midpoint = ((index + end) // 2) * window
            if window <= midpoint < count - window:
                splits.append(midpoint)
        index = end
    min_gap = int(0.35 * sample_rate)
    kept: list[int] = []
    previous = 0
    for point in splits:
        if point - previous >= min_gap and count - point >= min_gap:
            kept.append(point)
            previous = point
    spans: list[tuple[int, int]] = []
    cursor = 0
    for point in kept:
        spans.append((cursor, point))
        cursor = point
    spans.append((cursor, count))
    return spans


def chunk_wav(path: Path) -> list[dict[str, int]]:
    with wave.open(str(path), "rb") as handle:
        sample_rate = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        frames = handle.readframes(handle.getnframes())
    spans = chunk_pcm(frames, sample_rate, width, channels)
    return [{"start_ms": start * 1000 // sample_rate, "end_ms": end * 1000 // sample_rate} for start, end in spans]
