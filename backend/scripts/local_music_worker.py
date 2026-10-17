"""Run a pinned model CLI in its own environment, then produce a PCM WAV.

The backend supervisor owns this process and every codec/model descendant.
This stdlib-only wrapper keeps GPU imports out of the API environment.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
from typing import Literal
import wave


@dataclass(frozen=True)
class WorkerConditioning:
    mode: Literal['audio_to_audio', 'inpaint']
    source_wav: Path
    source_sha256: str
    source_frames: int
    script_sha256: str
    noise_level: float | None
    start_sec: float | None
    end_sec: float | None

@dataclass(frozen=True)
class WorkerCommand:
    argv: list[str]
    cwd: Path
    raw_output: Path
    output: Path
    ffmpeg: str
    conditioning: WorkerConditioning | None = None


def _digest(path: Path, maximum: int) -> str:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= maximum:
        raise ValueError('invalid_conditioning_file')
    digest, count = hashlib.sha256(), 0
    with path.open('rb') as handle:
        while block := handle.read(65536):
            count += len(block)
            if count > maximum:
                raise ValueError('invalid_conditioning_file')
            digest.update(block)
    return digest.hexdigest()


def _hex(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise ValueError('invalid_conditioning_file')
    return value


def _number(value: object) -> float:
    if not isinstance(value, (float, int)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError('invalid_conditioning_number')
    return float(value)


def _option(argv: list[str], option: str) -> str:
    if argv.count(option) != 1 or any(item.startswith(option + '=') for item in argv):
        raise ValueError('invalid_conditioning_arguments')
    index = argv.index(option)
    if index + 1 >= len(argv):
        raise ValueError('invalid_conditioning_arguments')
    return argv[index + 1]


def _validate_wav(path: Path, frames: int) -> None:
    with wave.open(str(path), 'rb') as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth(), audio.getnframes()) != (44100, 2, 2, frames):
            raise ValueError('invalid_conditioning_audio')
        remaining = frames
        while remaining:
            count = min(remaining, 65536)
            if len(audio.readframes(count)) != count * 4:
                raise ValueError('invalid_conditioning_audio')
            remaining -= count


def _conditioning(value: object, argv: list[str], directory: Path) -> WorkerConditioning | None:
    if value is None:
        if any(item.startswith(('--init-audio', '--inpaint-range')) for item in argv):
            raise ValueError('invalid_conditioning_arguments')
        return None
    if not isinstance(value, dict) or value.get('mode') not in ('audio_to_audio', 'inpaint'):
        raise ValueError('invalid_conditioning_command')
    source = Path(_text(value.get('source_wav')))
    frames = value.get('source_frames')
    if (source.name != 'conditioning.wav' or source.parent != directory or not source.is_absolute()
            or source.is_symlink() or source.resolve().parent != directory.resolve()
            or type(frames) is not int or not 1 <= frames <= 44100 * 380):
        raise ValueError('invalid_conditioning_command')
    mode: Literal['audio_to_audio', 'inpaint'] = 'audio_to_audio' if value['mode'] == 'audio_to_audio' else 'inpaint'
    noise, start, end = None, None, None
    if mode == 'audio_to_audio':
        noise = _number(value.get('noise_level'))
        if (not .01 <= noise <= .99 or float(_option(argv, '--init-noise-level')) != noise
                or any(item.startswith('--inpaint-range') for item in argv)):
            raise ValueError('conditioning_native_source_unused')
    else:
        start, end = _number(value.get('start_sec')), _number(value.get('end_sec'))
        if (value.get('noise_level') is not None or not 0 <= start < end <= frames / 44100
                or round(start * 44100 / 4096) >= round(end * 44100 / 4096)
                or _option(argv, '--inpaint-range') != f'{start:.17g},{end:.17g}'
                or any(item.startswith('--init-noise-level') for item in argv)):
            raise ValueError('invalid_conditioning_arguments')
    if (_option(argv, '--init-audio') != str(source) or float(_option(argv, '--seconds')) != frames / 44100):
        raise ValueError('invalid_conditioning_arguments')
    result = WorkerConditioning(mode, source, _hex(value.get('source_sha256')), frames,
        _hex(value.get('script_sha256')), noise, start, end)
    _validate_conditioning(result, argv)
    return result


def _validate_conditioning(value: WorkerConditioning, argv: list[str]) -> None:
    if (_digest(value.source_wav, 512 * 1024**2) != value.source_sha256
            or _digest(Path(argv[0]), 1024**2) != value.script_sha256):
        raise ValueError('conditioning_source_changed')
    _validate_wav(value.source_wav, value.source_frames)


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
        Path(_text(payload.get('raw_output'))), Path(_text(payload.get('output'))), _text(payload.get('ffmpeg')),
        _conditioning(payload.get('conditioning'), [_text(item) for item in raw_argv], path.parent))
    if not command.raw_output.is_absolute() or not command.output.is_absolute() or command.raw_output.parent != command.output.parent or command.output.parent != path.parent:
        raise ValueError('invalid_worker_output')
    if command.raw_output == command.output or command.raw_output.is_symlink() or command.output.is_symlink():
        raise ValueError('invalid_worker_output')
    return command


def run(command: WorkerCommand) -> None:
    pending = command.output.with_name('output.partial.wav')
    if any(path.exists() or path.is_symlink() for path in (command.raw_output, command.output, pending)):
        raise ValueError('invalid_worker_output')
    os.environ.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1'})
    os.chdir(command.cwd)
    sys.path.insert(0, str(command.cwd))
    sys.path.insert(0, str(Path(command.argv[0]).parent))
    sys.argv = command.argv
    if command.conditioning is not None:
        _validate_conditioning(command.conditioning, command.argv)
    try:
        runpy.run_path(command.argv[0], run_name='__main__')
    except SystemExit as exc:
        if exc.code not in (None, 0):
            raise
    if not command.raw_output.is_file() or command.raw_output.is_symlink():
        raise ValueError('model_audio_missing')
    if command.conditioning is not None:
        _validate_conditioning(command.conditioning, command.argv)
        _validate_wav(command.raw_output, command.conditioning.source_frames)
    if pending.is_symlink() or pending.exists() or command.output.exists():
        raise ValueError('invalid_worker_output')
    subprocess.run([command.ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i',
        str(command.raw_output), '-map', '0:a:0', '-c:a', 'pcm_s16le', '-f', 'wav', str(pending)], check=True)
    if command.conditioning is not None:
        _validate_wav(pending, command.conditioning.source_frames)
    os.link(pending, command.output, follow_symlinks=False)
    try:
        pending.unlink()
    except OSError:
        logging.getLogger(__name__).warning('Completed audio retained after partial cleanup failed', exc_info=True)
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
