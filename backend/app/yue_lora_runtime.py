"""Reviewed headless YuE2 commands; runtime readiness never imports GPU packages."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING, Literal

from .music_training_contracts import MusicLoraTrainingRequest, MusicLoraTrainingStatus

if TYPE_CHECKING:
    from .module_catalog import ModuleEnvironment


@dataclass(frozen=True)
class YueLoraEnvironment:
    python: Path
    trainer_root: Path
    comfy_root: Path
    base_checkpoint: Path
    ffmpeg: Path
    device: Literal['mps', 'cuda']


def default_environment(environment: ModuleEnvironment | None = None) -> YueLoraEnvironment:
    from .module_catalog import configured_environment
    from .video_media import tool
    from .yue_lora_convert import BASE_REVISION
    env = environment or configured_environment()
    root = env.root / 'training/yue2'
    base = env.root / 'training-assets/yue2' / BASE_REVISION / 'yue2_3b_bf16.safetensors'
    return YueLoraEnvironment(root / ('venv/Scripts/python.exe' if env.platform == 'win32' else 'venv/bin/python'),
        root / 'trainer', root / 'comfy', base, Path(tool('ffmpeg')), 'mps' if env.platform == 'darwin' else 'cuda')


def status(environment: ModuleEnvironment | None = None) -> MusicLoraTrainingStatus:
    from .module_catalog import configured_environment
    from .music_training_setup import environment_ready, weights_ready
    env = environment or configured_environment()
    supported = env.platform == 'darwin' and env.architecture in ('arm64', 'aarch64')
    installed = supported and environment_ready(env, 'yue2')
    ready = supported and weights_ready(env, 'yue2')
    return MusicLoraTrainingStatus(engine='yue2', supported=supported, installed=installed, weights_ready=ready,
        device='mps' if supported else None, validation='experimental',
        error_code=None if installed and ready else 'yue_lora_runtime_unprepared' if supported else 'yue_lora_platform_unsupported')


def runtime_ready(environment: YueLoraEnvironment) -> bool:
    return all(path.is_file() for path in (environment.python, environment.trainer_root / 'train_cli.py',
        environment.comfy_root / 'comfy/ldm/yue2/model.py', environment.base_checkpoint, environment.ffmpeg))


def fingerprint(request: MusicLoraTrainingRequest) -> str:
    return hashlib.sha256(request.model_dump_json(exclude_none=True).encode()).hexdigest()


def training_argv(request: MusicLoraTrainingRequest, run_dir: Path, environment: YueLoraEnvironment) -> list[str]:
    if request.engine != 'yue2':
        raise ValueError('yue_lora_engine_invalid')
    return [str(environment.python), '-u', str(environment.trainer_root / 'train_cli.py'), 'acoustic',
        '--comfy-root', str(environment.comfy_root), '--checkpoint', str(environment.base_checkpoint),
        '--data', str(run_dir / 'audio'), '--cache-dir', str(run_dir / 'cache'), '--out', str(run_dir / 'trainer.safetensors'),
        '--devices', environment.device, '--conditioning', 'compact', '--prefix-mode', 'off',
        '--steps', str(request.max_steps), '--rank', str(request.rank), '--alpha', str(request.rank),
        '--lr', str(request.learning_rate), '--seed', str(request.seed), '--segment-seconds', str(request.segment_seconds),
        '--max-seconds', str(request.segment_seconds), '--batch-size', '1', '--grad-accumulation', '1',
        '--warmup', str(min(10, max(0, request.max_steps - 1))), '--lora-dtype', 'fp32', '--targets', 'attention+mlp',
        '--vae-precision', 'fp32', '--eval-every', '0', '--eval-holdout', '0', '--sample-every', '0',
        '--save-every', str(min(25, request.max_steps)), '--log-every', '1', '--no-recursive']


def prepare_worker(request: MusicLoraTrainingRequest, run_dir: Path, sources: list[Path],
                   environment: YueLoraEnvironment | None = None) -> tuple[list[str], Path, dict[str, str]]:
    env = environment or default_environment()
    if environment is None:
        readiness = status()
        if not readiness.installed or not readiness.weights_ready:
            raise ValueError('yue_lora_runtime_unprepared')
    if request.engine != 'yue2' or not runtime_ready(env):
        raise ValueError('yue_lora_runtime_unprepared')
    if (not run_dir.is_absolute() or run_dir.is_symlink() or run_dir.resolve() != run_dir
            or re.fullmatch('[0-9a-f]{32}', run_dir.name) is None):
        raise ValueError('yue_lora_run_directory_invalid')
    if len(sources) != len(request.samples) or any(not source.is_absolute() or source.is_symlink() or not source.is_file() for source in sources):
        raise ValueError('yue_lora_dataset_invalid')
    plan = {'request': request.model_dump(mode='json'), 'request_sha256': fingerprint(request),
        'sources': [str(source) for source in sources], 'runtime': {'python': str(env.python),
            'trainer_root': str(env.trainer_root), 'comfy_root': str(env.comfy_root), 'base_checkpoint': str(env.base_checkpoint),
            'ffmpeg': str(env.ffmpeg), 'device': env.device}}
    with (run_dir / 'request.json').open('x', encoding='utf-8') as handle:
        json.dump(plan, handle, ensure_ascii=False, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    child_env = os.environ.copy()
    child_env.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
        'DO_NOT_TRACK': '1', 'PYTHONUTF8': '1', 'PYTHONUNBUFFERED': '1'})
    worker = Path(__file__).resolve().parents[1] / 'scripts/yue_lora_worker.py'
    return [str(env.python), str(worker), '--request', str(run_dir / 'request.json')], run_dir, child_env
