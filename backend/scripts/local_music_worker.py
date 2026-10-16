"""Run a pinned model CLI in its own environment, then produce a PCM WAV.

The backend supervisor owns this process and every codec/model descendant.
This stdlib-only wrapper keeps GPU imports out of the API environment.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import runpy
import subprocess
import sys

@dataclass(frozen=True)
class WorkerCommand:
    argv: list[str]
    cwd: Path
    raw_output: Path
    output: Path
    ffmpeg: str


def _text(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 25000 or '\0' in value:
        raise ValueError('invalid_worker_command')
    return value


def read_command(path: Path) -> WorkerCommand:
    if path.is_symlink() or path.stat().st_size > 256 * 1024:
        raise ValueError('invalid_worker_command')
    payload: object = json.loads(path.read_bytes())
    if not isinstance(payload, dict):
        raise ValueError('invalid_worker_command')
    raw_argv: object = payload.get('argv')
    if not isinstance(raw_argv, list) or not 1 <= len(raw_argv) <= 32:
        raise ValueError('invalid_worker_command')
    command = WorkerCommand([_text(item) for item in raw_argv], Path(_text(payload.get('cwd'))),
        Path(_text(payload.get('raw_output'))), Path(_text(payload.get('output'))), _text(payload.get('ffmpeg')))
    if not command.raw_output.is_absolute() or not command.output.is_absolute() or command.raw_output.parent != command.output.parent or command.output.parent != path.parent:
        raise ValueError('invalid_worker_output')
    if command.raw_output == command.output or command.raw_output.is_symlink() or command.output.is_symlink():
        raise ValueError('invalid_worker_output')
    return command


def run(command: WorkerCommand) -> None:
    os.environ.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1'})
    os.chdir(command.cwd)
    sys.path.insert(0, str(command.cwd))
    sys.path.insert(0, str(Path(command.argv[0]).parent))
    sys.argv = command.argv
    try:
        runpy.run_path(command.argv[0], run_name='__main__')
    except SystemExit as exc:
        if exc.code not in (None, 0):
            raise
    if not command.raw_output.is_file() or command.raw_output.is_symlink():
        raise ValueError('model_audio_missing')
    pending = command.output.with_name('output.partial.wav')
    if pending.is_symlink():
        raise ValueError('invalid_worker_output')
    subprocess.run([command.ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-y', '-i',
        str(command.raw_output), '-map', '0:a:0', '-c:a', 'pcm_s16le', '-f', 'wav', str(pending)], check=True)
    pending.replace(command.output)
    try:
        command.raw_output.unlink()
    except OSError:
        logging.getLogger(__name__).warning('Completed audio retained after raw cleanup failed', exc_info=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', type=Path, required=True)
    args = parser.parse_args()
    run(read_command(args.request))


if __name__ == '__main__':
    main()
