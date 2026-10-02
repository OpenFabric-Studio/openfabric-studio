#!/usr/bin/env python3
"""Offline MLX child entry point; install tracked patches before vendor import."""
from __future__ import annotations

import argparse
import json
import os
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.video_engine import VideoEngineError, install_compatibility


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine-dir', type=Path, required=True)
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
        install_compatibility(root)
        for package in ('ltx-core-mlx', 'ltx-pipelines-mlx'):
            sys.path.insert(0, str(root.resolve() / 'packages' / package / 'src'))
        sys.argv = ['ltx-2-mlx', *child_args]
        runpy.run_module('ltx_pipelines_mlx', run_name='__main__')
    except VideoEngineError as exc:
        print(json.dumps({'error_code': exc.code}), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
