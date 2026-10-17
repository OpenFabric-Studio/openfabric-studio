"""One optional CPU pitch diagnostic under the replacement attempt supervisor."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sys

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.replacement_pitch import (MAX_MANIFEST_BYTES, MAX_OUTCOME_BYTES, PitchDiagnosticError,
    analyze_pitch)
from app.replacement_pitch_contracts import PitchWorkerInput, ReplacementPitchWorkerOutcome


def _offline(event: str, args: tuple[object, ...]) -> None:
    if event in {'socket.connect', 'socket.getaddrinfo', 'urllib.Request', 'http.client.connect'}:
        raise RuntimeError('pitch_network_forbidden')


def main(request_path: Path, result_path: Path) -> int:
    root = request_path.parent
    try:
        if (root.is_symlink() or root.resolve() != root or not root.is_dir()
                or request_path.is_symlink() or request_path.resolve() != request_path
                or result_path.parent != root or result_path.is_symlink() or result_path.exists()
                or not 0 < request_path.stat().st_size <= MAX_MANIFEST_BYTES):
            return 2
        with request_path.open('rb') as stream:
            body = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(body) > MAX_MANIFEST_BYTES:
            return 2
        request = PitchWorkerInput.model_validate_json(body)
    except (OSError, ValueError):
        return 2
    checksum = hashlib.sha256(body).hexdigest()
    try:
        outcome = ReplacementPitchWorkerOutcome(input_sha256=checksum, result=analyze_pitch(request))
    except PitchDiagnosticError as error:
        outcome = ReplacementPitchWorkerOutcome(input_sha256=checksum, error_code=error.code)
    except ImportError:
        outcome = ReplacementPitchWorkerOutcome(input_sha256=checksum, error_code='pitch_runtime_unavailable')
    except (OSError, RuntimeError, ValueError):
        outcome = ReplacementPitchWorkerOutcome(input_sha256=checksum, error_code='pitch_worker_failed')
    encoded = outcome.model_dump_json().encode()
    if len(encoded) > MAX_OUTCOME_BYTES:
        return 2
    partial = root / 'output.partial'
    try:
        with partial.open('xb') as stream:
            stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
        if result_path.exists() or result_path.is_symlink():
            return 2
        partial.replace(result_path)
    except OSError:
        return 2
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--result', type=Path, required=True)
    options = parser.parse_args()
    os.environ.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
        NUMBA_NUM_THREADS='1', NUMBA_CACHE_DIR=str(options.request.parent / 'numba-cache'))
    sys.addaudithook(_offline)
    raise SystemExit(main(options.request, options.result))
