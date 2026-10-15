"""Offline native pre-encoding and base-model training, without GPU imports."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .module_catalog import ModuleEnvironment, configured_environment, engine_python, supported
from .module_evidence import environment_verified
from .music_lora_contracts import MusicLoraError
from .stable_lora_contracts import StableLoraTrainingRequest, StableLoraTrainingStatus
from .video_media import tool
from .video_projects import atomic_text


def fingerprint(request: StableLoraTrainingRequest) -> str:
    return hashlib.sha256(request.model_dump_json().encode('utf-8')).hexdigest()


def training_weights(environment: ModuleEnvironment, variant: str) -> tuple[Path, ...]:
    if variant not in ('small-music', 'medium'):
        raise MusicLoraError('stable_lora_variant_invalid', 422)
    root = environment.paths['stable_audio3'] / 'optimized/mlx/models/mlx'
    return (root / 't5gemma_f16.npz', root / ('dit_medium-base_f16.npz' if variant == 'medium' else 'dit_sm-music-base_f16.npz'),
        root / ('same_l_encoder_f32.npz' if variant == 'medium' else 'same_s_encoder_f32.npz'))


def _present(paths: tuple[Path, ...]) -> bool:
    try:
        return all(path.is_file() and path.stat().st_size > 0 for path in paths)
    except OSError:
        return False


def status(environment: ModuleEnvironment | None = None) -> StableLoraTrainingStatus:
    env = environment or configured_environment()
    source = env.paths['stable_audio3'] / 'optimized/mlx/scripts'
    support = supported('stable_audio3', env)
    installed = support and environment_verified(env, 'stable_audio3') and (source / 'lora_train_mlx.py').is_file() and (source / 'pre_encode_mlx.py').is_file()
    return StableLoraTrainingStatus(supported=support, installed=installed,
        small_weights_ready=_present(training_weights(env, 'small-music')), medium_weights_ready=_present(training_weights(env, 'medium')))


def prepare_worker(request: StableLoraTrainingRequest, run_dir: Path, sources: list[Path]) -> tuple[list[str], Path, dict[str, str]]:
    env = configured_environment()
    if not supported('stable_audio3', env):
        raise MusicLoraError('stable_lora_runtime_unsupported')
    if not environment_verified(env, 'stable_audio3'):
        raise MusicLoraError('stable_lora_engine_not_installed')
    if not _present(training_weights(env, request.variant)):
        raise MusicLoraError('stable_lora_training_weights_missing')
    if len(sources) != len(request.samples):
        raise MusicLoraError('stable_lora_dataset_invalid', 422)
    root = env.paths['stable_audio3'].resolve()
    native = root / 'optimized/mlx'
    python = engine_python(root, env.platform)
    preencode = [str(python), str(native / 'scripts/pre_encode_mlx.py'), '--audio-dir', str(run_dir / 'audio'),
        '--output-dir', str(run_dir / 'latents'), '--codec', 'same-l' if request.variant == 'medium' else 'same-s', '--max-duration', '600']
    train = [str(python), str(native / 'scripts/lora_train_mlx.py'), '--dit', 'medium' if request.variant == 'medium' else 'sm-music',
        '--dit-weights', str(training_weights(env, request.variant)[1]), '--latents-dir', str(run_dir / 'latents'),
        '--lr', str(request.learning_rate), '--name', 'training', '--save-dir', str(run_dir / 'checkpoints'), '--batch-size', '1',
        '--seed', str(request.seed), '--max-steps', str(request.max_steps), '--checkpoint-every', str(min(1000, request.max_steps)),
        '--adapter-type', 'lora', '--rank', str(request.rank), '--grad-checkpoint', '--no-conditioner-lora']
    payload = {'request': request.model_dump(mode='json'), 'request_sha256': fingerprint(request), 'sources': [str(path) for path in sources],
        'preencode_argv': preencode, 'train_argv': train, 'ffmpeg': tool('ffmpeg')}
    atomic_text(run_dir / 'request.json', json.dumps(payload, ensure_ascii=False))
    child_env = os.environ.copy()
    child_env.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1', 'DO_NOT_TRACK': '1', 'PYTHONUTF8': '1'})
    worker = Path(__file__).resolve().parents[1] / 'scripts/stable_lora_worker.py'
    return [str(python), str(worker), '--request', str(run_dir / 'request.json')], run_dir, child_env
