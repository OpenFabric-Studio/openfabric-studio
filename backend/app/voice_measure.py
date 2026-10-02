"""Measure trial audio in the engine environment; no inferred identity scores."""
from __future__ import annotations

import json
import math
import sys

import numpy as np
import soundfile as sf


def measure_audio(filename: str, expected_seconds: float) -> dict[str, float]:
    audio, sample_rate = sf.read(filename, dtype="float64", always_2d=True)
    samples = np.asarray(audio, dtype=np.float64)
    if samples.size == 0 or sample_rate <= 0 or not np.isfinite(samples).all():
        raise ValueError("invalid_audio")
    duration = samples.shape[0] / int(sample_rate)
    rms = float(np.sqrt(np.mean(samples ** 2)))
    return {
        "duration_sec": duration,
        "duration_delta_sec": duration - expected_seconds,
        "level_db": 20 * math.log10(max(rms, 1e-6)),
        "peak": float(np.max(np.abs(samples))),
        "clipped_fraction": float(np.mean(np.abs(samples) >= 0.999)),
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: voice_measure.py INPUT EXPECTED_SECONDS")
    print(json.dumps(measure_audio(sys.argv[1], float(sys.argv[2])), allow_nan=False))
