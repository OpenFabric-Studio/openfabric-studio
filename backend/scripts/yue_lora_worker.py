"""Supervised YuE2 training with private excerpts and strict native publication."""
from __future__ import annotations

import argparse
from collections.abc import Callable
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
from typing import Literal, NoReturn
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.music_training_contracts import MusicLoraTrainingRequest
from app.yue_lora_convert import BASE_SHA256, COMFY_REVISION, TRAINER_REVISION, convert_ar, convert_nar, validate_training_checkpoint
from app.yue_lora_runtime import PreparedAudio, YueLoraEnvironment, fingerprint, runtime_ready, score_ready, score_argv, training_argv, validate_prepared_audio, validate_score
from scripts.stable_lora_worker import atomic_json, digest, object_value, text

BASE_BYTES = 7799983228
_STEP = re.compile(r'YuE2 (acoustic|planner) step ([0-9]+)/([0-9]+)\s+loss\s+(\S+)')


@dataclass(frozen=True)
class PreparedScore:
    path: Path
    sha256: str


@dataclass(frozen=True)
class TrainingPlan:
    directory: Path
    sources: list[Path]
    request: MusicLoraTrainingRequest
    environment: YueLoraEnvironment
    fingerprint: str
    prepared_scores: tuple[PreparedScore, ...] | None = None
    prepared_audio: tuple[PreparedAudio, ...] | None = None


def read_plan(path: Path) -> TrainingPlan:
    if not path.is_absolute() or path.is_symlink() or path.stat().st_size > 16 * 1024**2:
        raise ValueError('yue_lora_request_invalid')
    directory = path.parent
    if directory.is_symlink() or directory.resolve() != directory or re.fullmatch('[0-9a-f]{32}', directory.name) is None:
        raise ValueError('yue_lora_directory_invalid')
    payload = object_value(json.loads(path.read_bytes()))
    if set(payload) not in ({'request', 'request_sha256', 'sources', 'runtime'}, {'request', 'request_sha256', 'sources', 'runtime', 'prepared_scores'}, {'request', 'request_sha256', 'sources', 'runtime', 'prepared_audio'}):
        raise ValueError('yue_lora_request_invalid')
    request = MusicLoraTrainingRequest.model_validate(payload['request'])
    expected = fingerprint(request)
    if request.engine != 'yue2' or payload['request_sha256'] != expected:
        raise ValueError('yue_lora_request_changed')
    prepared_scores: tuple[PreparedScore, ...] | None = None
    if 'prepared_scores' in payload:
        supplied = payload['prepared_scores']
        if request.yue_pair is None or not isinstance(supplied, list) or len(supplied) != len(request.samples):
            raise ValueError('yue_lora_scores_invalid')
        checked: list[PreparedScore] = []
        for value in supplied:
            entry = object_value(value)
            if set(entry) != {'path', 'sha256'}:
                raise ValueError('yue_lora_scores_invalid')
            source = Path(text(entry['path'], 10000))
            sha256 = text(entry['sha256'], 64)
            if not source.is_absolute() or source.resolve() != source or re.fullmatch('[0-9a-f]{64}', sha256) is None:
                raise ValueError('yue_lora_scores_invalid')
            validate_score(source)
            if digest(source, maximum=256 * 1024) != sha256:
                raise ValueError('yue_lora_score_changed')
            checked.append(PreparedScore(source, sha256))
        prepared_scores = tuple(checked)
    sources = payload['sources']
    if not isinstance(sources, list) or len(sources) != len(request.samples):
        raise ValueError('yue_lora_dataset_invalid')
    paths = [Path(text(source, 10000)) for source in sources]
    prepared_audio: tuple[PreparedAudio, ...] | None = None
    if request.yue_ar_source_job_id is not None:
        supplied_audio = payload.get('prepared_audio')
        if not isinstance(supplied_audio, list) or len(supplied_audio) != len(paths):
            raise ValueError('yue_lora_prepared_audio_invalid')
        checked_audio: list[PreparedAudio] = []
        for source, value in zip(paths, supplied_audio, strict=True):
            entry = object_value(value)
            if set(entry) != {'path', 'sha256'} or entry['path'] != str(source):
                raise ValueError('yue_lora_prepared_audio_invalid')
            prepared = PreparedAudio(source, text(entry['sha256'], 64))
            validate_prepared_audio(prepared, segment_seconds=request.segment_seconds)
            checked_audio.append(prepared)
        prepared_audio = tuple(checked_audio)
    elif 'prepared_audio' in payload:
        raise ValueError('yue_lora_prepared_audio_invalid')
    if any(not path.is_absolute() or path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError('yue_lora_dataset_invalid')
    runtime = object_value(payload['runtime'])
    expected_runtime = {'python', 'trainer_root', 'comfy_root', 'base_checkpoint', 'ffmpeg', 'device'}
    if (request.yue_pair is not None or request.yue_ar_source_job_id is not None) and prepared_scores is None:
        expected_runtime.update(('score_cli', 'score_model', 'score_model_spec', 'score_backend'))
    if set(runtime) != expected_runtime or runtime['device'] not in ('mps', 'cuda'):
        raise ValueError('yue_lora_runtime_invalid')
    backend = runtime.get('score_backend', 'metal')
    if backend not in ('metal', 'cuda', 'cpu'):
        raise ValueError('yue_lora_runtime_invalid')
    environment = YueLoraEnvironment(Path(text(runtime['python'], 10000)), Path(text(runtime['trainer_root'], 10000)),
        Path(text(runtime['comfy_root'], 10000)), Path(text(runtime['base_checkpoint'], 10000)),
        Path(text(runtime['ffmpeg'], 10000)), 'mps' if runtime['device'] == 'mps' else 'cuda',
        Path(text(runtime['score_cli'], 10000)) if 'score_cli' in runtime else None,
        Path(text(runtime['score_model'], 10000)) if 'score_model' in runtime else None,
        Path(text(runtime['score_model_spec'], 10000)) if 'score_model_spec' in runtime else None,
        'metal' if backend == 'metal' else 'cuda' if backend == 'cuda' else 'cpu')
    if any(not path.is_absolute() for path in (environment.python, environment.trainer_root, environment.comfy_root, environment.base_checkpoint, environment.ffmpeg)) or not runtime_ready(environment):
        raise ValueError('yue_lora_runtime_unprepared')
    if (request.yue_pair is not None or request.yue_ar_source_job_id is not None) and prepared_scores is None and not score_ready(environment):
        raise ValueError('yue_lora_score_runtime_unprepared')
    return TrainingPlan(directory, paths, request, environment, expected, prepared_scores, prepared_audio)


def progress(plan: TrainingPlan, stage: str, *, target: Literal['ar', 'nar'] | None = None, step: int | None = None, loss: float | None = None, seconds_per_step: float | None = None) -> None:
    atomic_json(plan.directory / 'stage.json', {'stage': stage, 'step': step,
        'target': target, 'total_steps': phase_steps(plan, target), 'loss': loss, 'seconds_per_step': seconds_per_step,
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
    import soundfile as sf
    directory = plan.directory / 'audio'
    directory.mkdir()
    manifest: list[dict[str, object]] = []
    formats = {'.wav': 'wav', '.mp3': 'mp3', '.flac': 'flac'}
    if plan.request.yue_ar_source_job_id is not None and (plan.prepared_audio is None or len(plan.prepared_audio) != len(plan.sources)):
        raise ValueError('yue_lora_prepared_audio_invalid')
    for index, (source, sample) in enumerate(zip(plan.sources, plan.request.samples, strict=True)):
        prepared = plan.prepared_audio[index] if plan.prepared_audio is not None else None
        if prepared is not None:
            if prepared.path != source:
                raise ValueError('yue_lora_prepared_audio_invalid')
            validate_prepared_audio(prepared, segment_seconds=plan.request.segment_seconds)
        if source.suffix.lower() not in formats or source.is_symlink():
            raise ValueError('yue_lora_dataset_invalid')
        original = digest(source)
        info = sf.info(source)
        if info.samplerate <= 0 or not math.isfinite(info.duration) or info.duration < .1:
            raise ValueError('yue_lora_audio_invalid')
        if sample.lyrics.strip() and info.duration > plan.request.segment_seconds + 1 / info.samplerate:
            raise ValueError('yue_lora_lyrics_require_matching_trimmed_clip')
        pending, output = directory / f'{index:03d}.partial.wav', directory / f'{index:03d}.wav'
        try:
            if prepared is not None:
                copied = 0
                with source.open('rb') as original_audio, pending.open('xb') as copy:
                    while chunk := original_audio.read(65536):
                        copied += len(chunk)
                        if copied > 48000 * 4 * (plan.request.segment_seconds + 1) + 65536:
                            raise ValueError('yue_lora_prepared_audio_invalid')
                        copy.write(chunk)
                    copy.flush(); os.fsync(copy.fileno())
                validate_prepared_audio(PreparedAudio(pending, prepared.sha256), segment_seconds=plan.request.segment_seconds)
                validate_prepared_audio(prepared, segment_seconds=plan.request.segment_seconds)
            else:
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
        atomic_json(output.with_suffix('.json'), {'style': sample.caption, 'lyrics': sample.lyrics})
        manifest.append({'sample': output.name, 'track_id': sample.track_id, 'source_sha256': original,
            'audio_sha256': digest(output), 'caption': sample.caption, 'lyrics': sample.lyrics, 'instrumental': sample.instrumental,
            'original_duration_seconds': info.duration, 'offset_seconds': 0, 'segment_seconds': plan.request.segment_seconds})
    atomic_json(plan.directory / 'dataset.json', {'request_sha256': plan.fingerprint, 'samples': manifest})


def phase_steps(plan: TrainingPlan, target: Literal['ar', 'nar'] | None) -> int:
    return plan.request.yue_pair.planner_steps if target == 'ar' and plan.request.yue_pair is not None else plan.request.max_steps


def update_progress(plan: TrainingPlan, line: str, previous_step: int, *, target: Literal['ar', 'nar'] = 'nar', elapsed_seconds: float | None = None) -> tuple[int, float, float | None] | None:
    match = _STEP.search(line)
    if match is None:
        return None
    step, total, loss = int(match[2]), int(match[3]), float(match[4])
    if match[1] != ('planner' if target == 'ar' else 'acoustic') or total != phase_steps(plan, target) or not previous_step < step <= total or not math.isfinite(loss):
        raise ValueError('yue_lora_progress_invalid')
    seconds = elapsed_seconds / (step - previous_step) if elapsed_seconds is not None and previous_step else None
    if seconds is not None and (not math.isfinite(seconds) or not 0 < seconds <= 86400):
        seconds = None
    progress(plan, 'training', target=target if plan.request.yue_pair is not None or plan.request.yue_ar_source_job_id is not None else None, step=step, loss=loss, seconds_per_step=seconds)
    return step, loss, seconds


def run_logged(argv: list[str], log_path: Path, on_line: Callable[[str], None]) -> None:
    # Inherit the outer owned supervisor's process group, including native descendants.
    with log_path.open('x', encoding='utf-8') as log:
        child = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        try:
            if child.stdout is None:
                raise RuntimeError('yue_lora_log_unavailable')
            while line := child.stdout.readline(65537):
                if len(line) > 65536:
                    raise ValueError('yue_lora_log_line_too_large')
                print(line, end='', flush=True)
                log.write(line); log.flush()
                on_line(line)
            if child.wait() != 0:
                raise RuntimeError('yue_lora_child_failed')
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait()
            if child.stdout is not None:
                child.stdout.close()


def extract_scores(plan: TrainingPlan) -> None:
    progress(plan, 'transcribing')
    scores = plan.directory / 'scores'
    scores.mkdir()
    manifest = object_value(json.loads((plan.directory / 'dataset.json').read_bytes()))
    samples = manifest.get('samples')
    if not isinstance(samples, list) or len(samples) != len(plan.request.samples):
        raise ValueError('yue_lora_dataset_invalid')
    for index, sample in enumerate(samples):
        audio = plan.directory / 'audio' / f'{index:03d}.wav'
        if plan.prepared_scores is None:
            output = scores / f'{index:03d}'
            output.mkdir()
            run_logged(score_argv(audio, output, plan.environment), scores / f'{index:03d}.log', lambda line: None)
            score = validate_score(output / 'score.abc')
        else:
            prepared = plan.prepared_scores[index]
            score = validate_score(prepared.path)
            if digest(prepared.path, maximum=256 * 1024) != prepared.sha256:
                raise ValueError('yue_lora_score_changed')
        destination = audio.with_suffix('.abc')
        with destination.open('x', encoding='utf-8') as handle:
            handle.write(score + '\n'); handle.flush(); os.fsync(handle.fileno())
        entry = object_value(sample)
        entry['score_sha256'] = digest(destination)
        entry['score'] = destination.name
        samples[index] = entry
    atomic_json(plan.directory / 'dataset.json', manifest)


def run_training(plan: TrainingPlan, *, target: Literal['ar', 'nar']) -> tuple[int, float | None, float | None]:
    phase = target if plan.request.yue_pair is not None or plan.request.yue_ar_source_job_id is not None else None
    progress(plan, 'loading', target=phase)
    latest = 0
    final_loss: float | None = None
    last_reported_at: float | None = None
    seconds_per_step: float | None = None
    def report(line: str) -> None:
        nonlocal latest, final_loss, last_reported_at, seconds_per_step
        if 'dataset:' in line and 'encoded dataset:' not in line:
            progress(plan, 'encoding', target=phase)
        if 'encoded dataset:' in line:
            progress(plan, 'loading', target=phase)
        reported_at = time.monotonic()
        step = update_progress(plan, line, latest, target=target, elapsed_seconds=None if last_reported_at is None else reported_at - last_reported_at)
        if step is not None:
            latest, final_loss, seconds_per_step = step
            last_reported_at = reported_at
    name = 'trainer' if plan.request.yue_pair is None and plan.request.yue_ar_source_job_id is None else f'trainer-{target}'
    run_logged(training_argv(plan.request, plan.directory, plan.environment, target=target), plan.directory / f'{name}.log', report)
    if latest != phase_steps(plan, target):
        raise ValueError('yue_lora_training_incomplete')
    validate_training_checkpoint(plan.directory / f'{name}.safetensors', max_steps=phase_steps(plan, target), rank=plan.request.rank, target=target)
    return latest, final_loss, seconds_per_step


def run(plan: TrainingPlan) -> None:
    os.environ.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1'})
    progress(plan, 'preparing')
    verify_sources(plan)
    base = plan.environment.base_checkpoint
    if base.is_symlink() or base.stat().st_size != BASE_BYTES or digest(base, maximum=BASE_BYTES) != BASE_SHA256:
        raise ValueError('yue_lora_base_changed')
    prepare_dataset(plan)
    paired = plan.request.yue_pair
    ar_only = plan.request.yue_ar_source_job_id is not None
    if paired is not None or ar_only:
        extract_scores(plan)
    if paired is not None:
        run_training(plan, target='ar')
    latest, final_loss, seconds_per_step = run_training(plan, target='ar' if ar_only else 'nar')
    phase: Literal['ar', 'nar'] | None = 'ar' if ar_only else 'nar' if paired is not None else None
    progress(plan, 'publishing', target=phase, step=latest, loss=final_loss, seconds_per_step=seconds_per_step)
    common = {'job_id': plan.directory.name, 'engine': 'yue2', 'request_sha256': plan.fingerprint,
        'dataset_sha256': digest(plan.directory / 'dataset.json'), 'base_sha256': BASE_SHA256, 'max_steps': plan.request.max_steps}
    if paired is not None:
        ar, nar = plan.directory / 'adapter-ar.safetensors', plan.directory / 'adapter-nar.safetensors'
        convert_ar(plan.directory / 'trainer-ar.safetensors', ar, base_sha256=BASE_SHA256)
        convert_nar(plan.directory / 'trainer-nar.safetensors', nar, base_sha256=BASE_SHA256)
        atomic_json(plan.directory / 'completion.json', {**common, 'planner_steps': paired.planner_steps, 'artifacts': [
            {'target': 'ar', 'checkpoint': ar.name, 'sha256': digest(ar)}, {'target': 'nar', 'checkpoint': nar.name, 'sha256': digest(nar)}]})
    elif ar_only:
        output = plan.directory / 'adapter-ar.safetensors'
        convert_ar(plan.directory / 'trainer-ar.safetensors', output, base_sha256=BASE_SHA256)
        atomic_json(plan.directory / 'completion.json', {**common, 'checkpoint': output.name, 'sha256': digest(output)})
    else:
        output = plan.directory / 'adapter.safetensors'
        convert_nar(plan.directory / 'trainer.safetensors', output, base_sha256=BASE_SHA256)
        atomic_json(plan.directory / 'completion.json', {**common, 'checkpoint': output.name, 'sha256': digest(output)})
    progress(plan, 'done', target=phase, step=latest, loss=final_loss, seconds_per_step=seconds_per_step)


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
