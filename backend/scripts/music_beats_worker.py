"""One bounded CPU window under the existing analysis supervisor, offline only."""
from __future__ import annotations

import argparse
from collections.abc import Callable
import hashlib
import io
import os
from pathlib import Path
import sys

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from numpy.typing import NDArray
from pydantic import TypeAdapter

from app.music_analysis_contracts import MusicAnalysisTimes
from app.music_beats import BeatWindowResult, BeatWorkerOutcome, BeatWorkerRequest
from app.music_beats_runtime import WEIGHTS_BYTES, identity

BeatTracker = Callable[[NDArray[np.float32], int], tuple[NDArray[np.float64], NDArray[np.float64]]]
_EVENT_TIMES = TypeAdapter(MusicAnalysisTimes)


def _window_events(values: NDArray[np.float64], duration: float) -> list[float]:
    # Validate the complete native sequence before removing its padded tail.
    times = _EVENT_TIMES.validate_python(values.tolist(), strict=True)
    # The pinned postprocessor uses 50 Hz frames. A centered final frame may
    # represent the clip endpoint; retain only events in [0, duration), without
    # moving them. Anything beyond one native frame is malformed output.
    if any(value > duration + .020000001 for value in times):
        raise ValueError('beat_time_outside_window')
    return [value for value in times if value < duration]


def _read(path: Path, maximum: int) -> bytes:
    if path.is_symlink() or path.resolve() != path or path.stat().st_size > maximum:
        raise ValueError('analysis_runtime_unverified')
    with path.open('rb') as stream:
        result = stream.read(maximum + 1)
    if len(result) > maximum:
        raise ValueError('analysis_budget_exceeded')
    return result


def analyze_window(request: BeatWorkerRequest, root: Path, tracker: BeatTracker) -> BeatWindowResult:
    expected_bytes = request.frames * request.channels * 4
    data = _read(root / request.input_name, expected_bytes)
    if len(data) != expected_bytes or hashlib.sha256(data).hexdigest() != request.input_sha256:
        raise ValueError('source_changed')
    samples = np.frombuffer(data, dtype='<f4').reshape((request.frames, request.channels))
    if not np.all(np.isfinite(samples)) or float(np.max(np.abs(samples))) > 16:
        raise ValueError('invalid_audio')
    # Upstream averages stereo. Select one actual channel to avoid erasing
    # antiphase material; this explicit recipe participates in the identity.
    powers = np.sum(np.square(samples, dtype=np.float64), axis=0)
    mono = np.ascontiguousarray(samples[:, int(np.argmax(powers))], dtype=np.float32)
    beats, downbeats = tracker(mono, request.sample_rate_hz)
    if beats.ndim != 1 or downbeats.ndim != 1 or len(beats) > 6001 or len(downbeats) > 6001:
        raise ValueError('analysis_budget_exceeded')
    duration = request.frames / request.sample_rate_hz
    return BeatWindowResult(duration_sec=duration, beats_sec=_window_events(beats, duration),
        downbeats_sec=_window_events(downbeats, duration))


def _offline(event: str, args: tuple[object, ...]) -> None:
    if event in {'socket.connect', 'socket.getaddrinfo', 'urllib.Request', 'http.client.connect'}:
        raise ValueError('analysis_network_forbidden')


def native_tracker(request: BeatWorkerRequest, root: Path) -> BeatTracker:
    checkpoint = root / request.checkpoint_name
    content = _read(checkpoint, WEIGHTS_BYTES)
    if len(content) != WEIGHTS_BYTES or hashlib.sha256(content).hexdigest() != request.identity.weights_sha256:
        raise ValueError('analysis_runtime_unverified')
    os.environ.update(CUDA_VISIBLE_DEVICES='', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        TORCH_FORCE_WEIGHTS_ONLY_LOAD='1', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
    import torch
    from beat_this import inference
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)

    def local_checkpoint(path: str, device: object = 'cpu') -> dict[str, object]:
        if path != str(checkpoint) or str(device) != 'cpu':
            raise ValueError('analysis_runtime_unverified')
        # Deserialization consumes these already verified, retained bytes.
        loaded = torch.load(io.BytesIO(content), map_location='cpu', weights_only=True)
        return TypeAdapter(dict[str, object]).validate_python(loaded, strict=True)

    inference.load_checkpoint = local_checkpoint
    return inference.Audio2Beats(checkpoint_path=str(checkpoint), device='cpu', float16=False, dbn=False)


def main(request_path: Path, result_path: Path) -> int:
    root = request_path.parent
    if root.is_symlink() or root.resolve() != root or result_path.parent != root or result_path.exists() or result_path.is_symlink():
        raise ValueError('analysis_runtime_unverified')
    request = BeatWorkerRequest.model_validate_json(_read(request_path, 16384))
    runtime_root = Path(request.runtime_root)
    if identity(runtime_root, require_weights=False) != request.identity:
        raise ValueError('analysis_runtime_unverified')
    def predict(samples: NDArray[np.float32], rate: int) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        return native_tracker(request, root)(samples, rate)

    result = analyze_window(request, root, predict)
    if identity(runtime_root, require_weights=False) != request.identity:
        raise ValueError('analysis_runtime_unverified')
    outcome = BeatWorkerOutcome(input_sha256=request.input_sha256,
        configuration_sha256=request.identity.configuration_sha256, result=result)
    with result_path.open('xb') as output:
        output.write(outcome.model_dump_json().encode()); output.flush(); os.fsync(output.fileno())
    return 0


if __name__ == '__main__':
    # Irreversible policy belongs only to this dedicated child, never API/tests.
    sys.addaudithook(_offline)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--result', type=Path, required=True)
    options = parser.parse_args()
    try:
        raise SystemExit(main(options.request, options.result))
    except (OSError, ValueError, RuntimeError):
        print('analysis_runtime_unverified', file=sys.stderr)
        raise SystemExit(1)
