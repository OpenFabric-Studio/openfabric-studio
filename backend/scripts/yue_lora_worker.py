"""Supervised compact NAR training, private audio and strict native publication."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from types import FrameType
from typing import NoReturn
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.music_training_contracts import MusicLoraTrainingRequest
from app.yue_lora_convert import BASE_SHA256, COMFY_REVISION, TRAINER_REVISION, convert_nar, validate_training_checkpoint
from app.yue_lora_runtime import YueLoraEnvironment, fingerprint, runtime_ready, training_argv
from scripts.stable_lora_worker import atomic_json, digest, object_value, text

BASE_BYTES = 7799983228
_STEP = re.compile(r'YuE2 acoustic step ([0-9]+)/([0-9]+)\s+loss\s+(\S+)')


@dataclass(frozen=True)
class TrainingPlan:
    directory: Path
    sources: list[Path]
    request: MusicLoraTrainingRequest
    environment: YueLoraEnvironment
    fingerprint: str


def read_plan(path: Path) -> TrainingPlan:
    if not path.is_absolute() or path.is_symlink() or path.stat().st_size > 16 * 1024**2:
        raise ValueError('yue_lora_request_invalid')
    directory = path.parent
    if directory.is_symlink() or directory.resolve() != directory or re.fullmatch('[0-9a-f]{32}', directory.name) is None:
        raise ValueError('yue_lora_directory_invalid')
    payload = object_value(json.loads(path.read_bytes()))
    if set(payload) != {'request', 'request_sha256', 'sources', 'runtime'}:
        raise ValueError('yue_lora_request_invalid')
    request = MusicLoraTrainingRequest.model_validate(payload['request'])
    expected = fingerprint(request)
    if request.engine != 'yue2' or payload['request_sha256'] != expected:
        raise ValueError('yue_lora_request_changed')
    sources = payload['sources']
    if not isinstance(sources, list) or len(sources) != len(request.samples):
        raise ValueError('yue_lora_dataset_invalid')
    paths = [Path(text(source, 10000)) for source in sources]
    if any(not path.is_absolute() or path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError('yue_lora_dataset_invalid')
    runtime = object_value(payload['runtime'])
    if set(runtime) != {'python', 'trainer_root', 'comfy_root', 'base_checkpoint', 'ffmpeg', 'device'} or runtime['device'] not in ('mps', 'cuda'):
        raise ValueError('yue_lora_runtime_invalid')
    environment = YueLoraEnvironment(Path(text(runtime['python'], 10000)), Path(text(runtime['trainer_root'], 10000)),
        Path(text(runtime['comfy_root'], 10000)), Path(text(runtime['base_checkpoint'], 10000)),
        Path(text(runtime['ffmpeg'], 10000)), 'mps' if runtime['device'] == 'mps' else 'cuda')
    if any(not path.is_absolute() for path in (environment.python, environment.trainer_root, environment.comfy_root, environment.base_checkpoint, environment.ffmpeg)) or not runtime_ready(environment):
        raise ValueError('yue_lora_runtime_unprepared')
    return TrainingPlan(directory, paths, request, environment, expected)


def progress(plan: TrainingPlan, stage: str, *, step: int | None = None, loss: float | None = None, seconds_per_step: float | None = None) -> None:
    atomic_json(plan.directory / 'stage.json', {'stage': stage, 'step': step,
        'total_steps': plan.request.max_steps, 'loss': loss, 'seconds_per_step': seconds_per_step,
        'updated_at': datetime.now(timezone.utc).isoformat()})


def verify_sources(plan: TrainingPlan) -> None:
    for root, revision in ((plan.environment.trainer_root, TRAINER_REVISION), (plan.environment.comfy_root, COMFY_REVISION)):
        if root.is_symlink() or root.resolve() != root:
            raise ValueError('yue_lora_source_unsafe')
        actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True, timeout=20).strip()
        if actual != revision:
            raise ValueError('yue_lora_source_revision_changed')
        subprocess.run(['git', '-C', str(root), 'diff-index', '--quiet', 'HEAD', '--'], check=True, timeout=20)
        untracked = subprocess.check_output(['git', '-C', str(root), 'ls-files', '--others', '--exclude-standard'], text=True, timeout=20)
        if any(path.endswith('.py') for path in untracked.splitlines()):
            raise ValueError('yue_lora_source_unreviewed')


def prepare_dataset(plan: TrainingPlan) -> None:
    directory = plan.directory / 'audio'
    directory.mkdir()
    manifest: list[dict[str, object]] = []
    formats = {'.wav': 'wav', '.mp3': 'mp3', '.flac': 'flac'}
    for index, (source, sample) in enumerate(zip(plan.sources, plan.request.samples, strict=True)):
        if source.suffix.lower() not in formats or source.is_symlink():
            raise ValueError('yue_lora_dataset_invalid')
        original = digest(source)
        pending, output = directory / f'{index:03d}.partial.wav', directory / f'{index:03d}.wav'
        try:
            subprocess.run([str(plan.environment.ffmpeg), '-nostdin', '-v', 'error', '-xerror', '-protocol_whitelist', 'file,pipe',
                '-f', formats[source.suffix.lower()], '-i', str(source), '-t', str(plan.request.segment_seconds),
                '-map', '0:a:0', '-ac', '2', '-ar', '48000', '-c:a', 'pcm_s16le', '-f', 'wav', str(pending)], check=True, timeout=120)
            with wave.open(str(pending)) as audio:
                if audio.getnchannels() != 2 or audio.getsampwidth() != 2 or audio.getframerate() != 48000 or not .1 <= audio.getnframes() / 48000 <= plan.request.segment_seconds + .01:
                    raise ValueError('yue_lora_audio_invalid')
            if digest(source) != original:
                raise ValueError('yue_lora_source_changed')
            pending.replace(output)
        finally:
            pending.unlink(missing_ok=True)
        atomic_json(output.with_suffix('.json'), {'style': sample.caption, 'lyrics': ''})
        manifest.append({'sample': output.name, 'track_id': sample.track_id, 'source_sha256': original,
            'audio_sha256': digest(output), 'caption': sample.caption, 'lyrics': '', 'instrumental': sample.instrumental})
    atomic_json(plan.directory / 'dataset.json', {'request_sha256': plan.fingerprint, 'samples': manifest})


def update_progress(plan: TrainingPlan, line: str, previous_step: int, *, elapsed_seconds: float | None = None) -> tuple[int, float, float | None] | None:
    match = _STEP.search(line)
    if match is None:
        return None
    step, total, loss = int(match[1]), int(match[2]), float(match[3])
    if total != plan.request.max_steps or not previous_step < step <= total or not math.isfinite(loss):
        raise ValueError('yue_lora_progress_invalid')
    seconds = elapsed_seconds / (step - previous_step) if elapsed_seconds is not None and previous_step else None
    if seconds is not None and (not math.isfinite(seconds) or not 0 < seconds <= 86400):
        seconds = None
    progress(plan, 'training', step=step, loss=loss, seconds_per_step=seconds)
    return step, loss, seconds


def run(plan: TrainingPlan) -> None:
    os.environ.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1'})
    progress(plan, 'preparing')
    verify_sources(plan)
    base = plan.environment.base_checkpoint
    if base.is_symlink() or base.stat().st_size != BASE_BYTES or digest(base, maximum=BASE_BYTES) != BASE_SHA256:
        raise ValueError('yue_lora_base_changed')
    prepare_dataset(plan)
    progress(plan, 'loading')
    latest = 0
    final_loss: float | None = None
    last_reported_at: float | None = None
    seconds_per_step: float | None = None
    with (plan.directory / 'trainer.log').open('x', encoding='utf-8') as log:
        child = subprocess.Popen(training_argv(plan.request, plan.directory, plan.environment), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1)
        try:
            if child.stdout is None:
                raise RuntimeError('yue_lora_log_unavailable')
            for line in child.stdout:
                if len(line) > 65536:
                    raise ValueError('yue_lora_log_line_too_large')
                print(line, end='', flush=True)
                log.write(line); log.flush()
                if 'dataset:' in line and 'encoded dataset:' not in line:
                    progress(plan, 'encoding')
                if 'encoded dataset:' in line:
                    progress(plan, 'loading')
                reported_at = time.monotonic()
                step = update_progress(plan, line, latest, elapsed_seconds=None if last_reported_at is None else reported_at - last_reported_at)
                if step is not None:
                    latest, final_loss, seconds_per_step = step
                    last_reported_at = reported_at
            if child.wait() != 0:
                raise RuntimeError('yue_lora_trainer_failed')
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait()
            if child.stdout is not None:
                child.stdout.close()
    if latest != plan.request.max_steps:
        raise ValueError('yue_lora_training_incomplete')
    checkpoint = plan.directory / 'trainer.safetensors'
    validate_training_checkpoint(checkpoint, max_steps=plan.request.max_steps, rank=plan.request.rank)
    progress(plan, 'publishing', step=latest, loss=final_loss, seconds_per_step=seconds_per_step)
    output = plan.directory / 'adapter.safetensors'
    convert_nar(checkpoint, output, base_sha256=BASE_SHA256)
    atomic_json(plan.directory / 'completion.json', {'job_id': plan.directory.name, 'engine': 'yue2',
        'request_sha256': plan.fingerprint, 'checkpoint': output.name, 'sha256': digest(output),
        'dataset_sha256': digest(plan.directory / 'dataset.json'), 'base_sha256': BASE_SHA256, 'max_steps': plan.request.max_steps})
    progress(plan, 'done', step=latest, loss=final_loss, seconds_per_step=seconds_per_step)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True, type=Path)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, cancel)
    run(read_plan(args.request))


def cancel(signum: int, frame: FrameType | None) -> NoReturn:
    raise SystemExit(128 + signum)


if __name__ == '__main__':
    main()
