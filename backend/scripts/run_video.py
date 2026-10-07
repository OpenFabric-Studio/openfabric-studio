#!/usr/bin/env python3
"""Offline MLX child entry point; install tracked patches before vendor import."""
from __future__ import annotations

import argparse
import importlib
import json
import os
import runpy
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.video_engine import VideoEngineError, install_compatibility


def write_telemetry(path: Path, peak: int | None) -> None:
    """Optional measurement cannot follow a destination link or leak staging."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False, encoding='utf-8') as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps({'peak_mlx_memory_bytes': peak}) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine-dir', type=Path, required=True)
    parser.add_argument('--candidate', action='store_true')
    parser.add_argument('--telemetry', type=Path)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    parsed = parser.parse_args()
    root: object = parsed.engine_dir
    arguments: object = parsed.arguments
    if not isinstance(root, Path) or not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
        return 2
    child_args = [item for item in arguments if isinstance(item, str)]
    if child_args and child_args[0] == '--':
        child_args = child_args[1:]
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN'] = '1'
    try:
        if parsed.candidate:
            from app.video_candidate import install_candidate_compatibility
            install_candidate_compatibility(root)
        else:
            install_compatibility(root)
        for package in ('ltx-core-mlx', 'ltx-pipelines-mlx'):
            sys.path.insert(0, str(root.resolve() / 'packages' / package / 'src'))
        sys.argv = ['ltx-2-mlx', *child_args]
        runpy.run_module('ltx_pipelines_mlx', run_name='__main__')
    except VideoEngineError as exc:
        print(json.dumps({'error_code': exc.code}), file=sys.stderr)
        return 2
    finally:
        telemetry: object = parsed.telemetry
        if isinstance(telemetry, Path):
            # Only emitted after the worker has loaded MLX. This value is MLX
            # allocator accounting, distinct from process RSS. Never invent zero.
            peak: int | None = None
            if 'mlx.core' in sys.modules:
                mlx = importlib.import_module('mlx.core')
                measure = getattr(mlx, 'get_peak_memory', None)
                if callable(measure):
                    value: object = measure()
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        peak = value
            # Atomic replacement does not follow a dangling destination symlink.
            try:
                write_telemetry(telemetry, peak)
            except OSError:
                print(json.dumps({'telemetry_error': 'unavailable'}), file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
