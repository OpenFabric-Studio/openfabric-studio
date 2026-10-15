"""Prepare bounded HeartMuLa worker plans without importing accelerator libraries."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from .contracts import JsonObject
from .local_music_contracts import LocalMusicRequest
from .module_catalog import ModuleEnvironment, configured_environment
from .music_lora_contracts import MusicLoraError
from .music_training_contracts import MusicLoraTrainingRequest, MusicLoraTrainingStatus
from .video_projects import atomic_text


def fingerprint(request: MusicLoraTrainingRequest) -> str:
    return hashlib.sha256(request.model_dump_json(exclude_none=True).encode()).hexdigest()


def status(environment: ModuleEnvironment | None = None) -> MusicLoraTrainingStatus:
    from .music_training_setup import environment_ready, weights_ready
    env = environment or configured_environment()
    supported = env.platform == 'darwin' and env.architecture.lower() in ('arm64', 'aarch64') or env.platform in ('linux', 'win32')
    installed = supported and environment_ready(env, 'heartmula')
    target: Literal['mps', 'cuda'] = 'mps' if env.platform == 'darwin' else 'cuda'
    return MusicLoraTrainingStatus(engine='heartmula', supported=supported, installed=installed,
        weights_ready=weights_ready(env, 'heartmula'), device=target if supported else None,
        validation='experimental', error_code=None if installed else 'music_training_environment_missing')


def runtime_paths(environment: ModuleEnvironment) -> tuple[Path, JsonObject]:
    from .music_training_setup import heart_model_paths, training_root
    root = training_root(environment, 'heartmula').resolve()
    base, decoder, tokenizer = heart_model_paths(environment)
    folder = 'Scripts/python.exe' if environment.platform == 'win32' else 'bin/python'
    runtime: JsonObject = {'simpletuner_root': str(root / 'simpletuner'), 'heartlib_root': str(root / 'heartlib'),
        'codec_python': str(root / 'codec-venv' / folder), 'base_path': str(base.resolve()),
        'decoder_path': str(decoder.resolve()), 'encoder_path': str(root / 'encoder'),
        'tokenizer_path': str(tokenizer.resolve()), 'device': 'mps' if environment.platform == 'darwin' else 'cuda'}
    return root / 'trainer-venv' / folder, runtime


def _ready(environment: ModuleEnvironment) -> None:
    current = status(environment)
    if not current.supported:
        raise MusicLoraError('music_training_runtime_unsupported')
    if not current.installed:
        raise MusicLoraError('music_training_environment_missing')
    if not current.weights_ready:
        raise MusicLoraError('music_training_weights_missing')


def prepare_worker(request: MusicLoraTrainingRequest, run_dir: Path, sources: list[Path]) -> tuple[list[str], Path, dict[str, str]]:
    env = configured_environment()
    _ready(env)
    if request.engine != 'heartmula' or len(sources) != len(request.samples):
        raise MusicLoraError('music_training_dataset_invalid', 422)
    interpreter, runtime = runtime_paths(env)
    request_path = run_dir / 'request.json'
    if request_path.exists():
        raise MusicLoraError('music_training_existing_run', 409)
    payload = {'request': request.model_dump(mode='json', exclude_none=True), 'sources': [str(source) for source in sources],
        'request_sha256': fingerprint(request), 'runtime': runtime}
    atomic_text(request_path, json.dumps(payload, ensure_ascii=False, allow_nan=False))
    child_env = os.environ.copy()
    child_env.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
        'DO_NOT_TRACK': '1', 'PYTHONUTF8': '1', 'PYTORCH_ENABLE_MPS_FALLBACK': '1'})
    worker = Path(__file__).resolve().parents[1] / 'scripts/heart_lora_worker.py'
    return [str(interpreter), str(worker), '--request', str(request_path)], run_dir, child_env


def generation_command(request: LocalMusicRequest, adapter: Path, raw_output: Path) -> tuple[list[str], Path]:
    env = configured_environment()
    _ready(env)
    if request.engine != 'heartmula':
        raise MusicLoraError('music_lora_target_mismatch', 422)
    interpreter, runtime = runtime_paths(env)
    request_path = raw_output.parent / 'inference.json'
    if request_path.exists() or raw_output.exists():
        raise MusicLoraError('music_training_existing_run', 409)
    payload = {'request': request.model_dump(mode='json', exclude_none=True), 'sources': [], 'runtime': runtime,
        'adapter_path': str(adapter), 'strength': request.adapter.strength if request.adapter is not None else 1.0,
        'output_path': str(raw_output)}
    atomic_text(request_path, json.dumps(payload, ensure_ascii=False, allow_nan=False))
    worker = Path(__file__).resolve().parents[1] / 'scripts/heart_lora_worker.py'
    return [str(worker), '--mode', 'generate', '--request', str(request_path)], interpreter
