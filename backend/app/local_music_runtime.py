"""Offline adapters for the reviewed HeartMuLa and Stable Audio 3 CLIs."""
from __future__ import annotations

import os
from pathlib import Path

from . import music_lora
from .local_music_contracts import LocalMusicEngineId, LocalMusicEngines, LocalMusicEngineStatus, LocalMusicError, LocalMusicRequest
from .module_catalog import ModuleEnvironment, configured_environment, engine_python, supported
from .module_evidence import environment_verified
from .video_media import tool
from .video_projects import atomic_text
import json


ENGINES: tuple[LocalMusicEngineId, ...] = ('heartmula', 'stable_audio3')


def weight_paths(environment: ModuleEnvironment, engine: LocalMusicEngineId, variant: str = 'small-music') -> tuple[Path, ...]:
    root = environment.paths[engine]
    if engine == 'heartmula':
        return tuple(root / name for name in (
            'ckpt-mlx/heartmula/config.json', 'ckpt-mlx/heartmula/model.safetensors',
            'ckpt-mlx/heartcodec/config.json', 'ckpt-mlx/heartcodec/model.safetensors', 'ckpt/tokenizer.json'))
    names = ('t5gemma_f16.npz', 'dit_medium_f16.npz', 'same_l_decoder_f32.npz') if variant == 'medium' else (
        't5gemma_f16.npz', 'dit_sm-music_f16.npz', 'same_s_decoder_f32.npz')
    return tuple(root / 'optimized/mlx/models/mlx' / name for name in names)


def _present(paths: tuple[Path, ...]) -> bool:
    try:
        return all(path.is_file() and path.stat().st_size > 0 for path in paths)
    except OSError:
        return False


def engine_status(environment: ModuleEnvironment | None = None) -> LocalMusicEngines:
    env = environment or configured_environment()
    engines: list[LocalMusicEngineStatus] = []
    for engine in ENGINES:
        support = supported(engine, env)
        installed = support and environment_verified(env, engine)
        weights_ready = _present(weight_paths(env, engine)) or engine == 'stable_audio3' and _present(weight_paths(env, engine, 'medium'))
        detail = ('This integration requires an Apple Silicon Mac.' if not support else
                  'Install and verify the isolated engine environment in Settings.' if not installed else
                  'Place the reviewed local weights described in setup instructions.' if not weights_ready else
                  'Local files are present. Audio quality and speed require an actual generation.')
        from .heart_lora_runtime import status as heart_training_status
        adapter_status = heart_training_status(env) if engine == 'heartmula' else None
        adapter_ready = adapter_status is not None and adapter_status.supported and adapter_status.installed and adapter_status.weights_ready
        engines.append(LocalMusicEngineStatus(id=engine, installed=installed, weights_ready=weights_ready,
            supported=support, adapter_runtime_ready=adapter_ready, runtime='Community HeartMuLa MLX port' if engine == 'heartmula' else 'Official Stable Audio 3 MLX', detail=detail))
    return LocalMusicEngines(engines=engines)


def prepare_worker(request: LocalMusicRequest, output: Path) -> tuple[list[str], Path, dict[str, str]]:
    env = configured_environment()
    heart_adapter = request.engine == 'heartmula' and request.adapter is not None
    if not heart_adapter and not supported(request.engine, env):
        raise LocalMusicError('local_music_runtime_unsupported')
    if not heart_adapter and not environment_verified(env, request.engine):
        raise LocalMusicError('local_music_engine_not_installed')
    if not heart_adapter and not _present(weight_paths(env, request.engine, request.variant)):
        raise LocalMusicError('local_music_weights_missing')
    root = env.paths[request.engine].resolve()
    raw = output.parent / 'raw.wav'
    interpreter = engine_python(root, env.platform)
    if heart_adapter and request.adapter is not None:
        from .heart_lora_runtime import generation_command
        selected = music_lora.resolve(request.adapter, engine='heartmula', target='heartmula-3b')
        command, interpreter = generation_command(request, selected, raw)
        cwd = output.parent
    elif request.engine == 'heartmula':
        cwd = root
        command = [str(root / 'generate.py'), '--checkpoint', str(root / 'ckpt-mlx'),
            '--tags=' + request.prompt, '--lyrics=' + request.lyrics,
            '--duration', str(request.duration_seconds), '--output', str(raw), '--dtype', 'bfloat16']
    else:
        cwd = root / 'optimized/mlx'
        command = [str(cwd / 'scripts/sa3_mlx.py'), '--prompt=' + request.prompt,
            '--dit', 'medium' if request.variant == 'medium' else 'sm-music',
            '--decoder', 'same-l' if request.variant == 'medium' else 'same-s',
            '--seconds', str(request.duration_seconds), '--seed', str(request.seed), '--out', str(raw)]
    if request.adapter is not None and request.engine == 'stable_audio3':
        adapter = music_lora.resolve(request.adapter, engine=request.engine, target=request.variant)
        command.extend(['--lora', str(adapter), 'strength=' + str(request.adapter.strength)])
    request_path = output.parent / 'request.json'
    payload = {'request': request.model_dump(mode='json'), 'argv': command, 'cwd': str(cwd),
               'raw_output': str(raw), 'output': str(output), 'ffmpeg': tool('ffmpeg'),
               'seed': request.seed if request.engine == 'heartmula' and not heart_adapter else None}
    atomic_text(request_path, json.dumps(payload, ensure_ascii=False))
    child_env = os.environ.copy()
    child_env.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
                      'DO_NOT_TRACK': '1', 'PYTHONUTF8': '1'})
    worker = Path(__file__).resolve().parents[1] / 'scripts/local_music_worker.py'
    return [str(interpreter), str(worker), '--request', str(request_path)], cwd, child_env
