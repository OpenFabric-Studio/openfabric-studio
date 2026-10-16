"""Reviewed headless YuE2 commands; runtime readiness never imports GPU packages."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING, Literal
import wave

from pydantic import TypeAdapter

from .contracts import JsonObject
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
    score_cli: Path | None = None
    score_model: Path | None = None
    score_model_spec: Path | None = None
    score_backend: Literal['metal', 'cuda', 'cpu'] = 'metal'


@dataclass(frozen=True)
class PreparedAudio:
    path: Path
    sha256: str


def validate_prepared_audio(audio: PreparedAudio, *, segment_seconds: int) -> None:
    path = audio.path
    try:
        if (not path.is_absolute() or path.is_symlink() or path.resolve() != path or not path.is_file()
                or path.suffix != '.wav' or not 44 <= path.stat().st_size <= 48000 * 4 * (segment_seconds + 1) + 65536
                or re.fullmatch('[0-9a-f]{64}', audio.sha256) is None):
            raise ValueError('yue_lora_prepared_audio_invalid')
        with wave.open(str(path)) as handle:
            if (handle.getnchannels() != 2 or handle.getsampwidth() != 2 or handle.getframerate() != 48000
                    or not .1 <= handle.getnframes() / 48000 <= segment_seconds + .01):
                raise ValueError('yue_lora_prepared_audio_invalid')
        with path.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != audio.sha256:
                raise ValueError('yue_lora_prepared_audio_changed')
    except (OSError, EOFError, wave.Error) as error:
        raise ValueError('yue_lora_prepared_audio_invalid') from error


def default_environment(environment: ModuleEnvironment | None = None) -> YueLoraEnvironment:
    from .config import MODELS, SHEETSAGE_MODEL_PATH
    from .module_catalog import configured_environment
    from .video_media import tool
    from .yue_lora_convert import BASE_REVISION
    env = environment or configured_environment()
    root = env.root / 'training/yue2'
    base = env.root / 'training-assets/yue2' / BASE_REVISION / 'yue2_3b_bf16.safetensors'
    engine = env.paths['yue2']
    configured = MODELS['yue2'].processes[0]
    suffix = '.exe' if env.platform == 'win32' else ''
    preset = 'macos-metal-release' if env.platform == 'darwin' else 'windows-cuda-release' if env.platform == 'win32' else 'linux-cuda-release'
    cli = Path(configured.cmd[0]).with_name('audiocpp_cli' + suffix) if engine == configured.cwd else engine / f'build/{preset}/bin/audiocpp_cli{suffix}'
    score_model = SHEETSAGE_MODEL_PATH if engine == configured.cwd else engine / 'models/SheetSage2-GGUF/sheetsage2-orig.gguf'
    return YueLoraEnvironment(root / ('venv/Scripts/python.exe' if env.platform == 'win32' else 'venv/bin/python'),
        root / 'trainer', root / 'comfy', base, Path(tool('ffmpeg')), 'mps' if env.platform == 'darwin' else 'cuda',
        cli, score_model, engine / 'model_specs/sheetsage2.json', 'metal' if env.platform == 'darwin' else 'cuda')


def status(environment: ModuleEnvironment | None = None) -> MusicLoraTrainingStatus:
    from .module_catalog import configured_environment
    from .music_training_setup import environment_ready, weights_ready
    from .video_media import VideoMediaError
    env = environment or configured_environment()
    supported = env.platform == 'darwin' and env.architecture in ('arm64', 'aarch64')
    installed = supported and environment_ready(env, 'yue2')
    ready = supported and weights_ready(env, 'yue2')
    paired = False
    if installed and ready:
        try:
            paired = score_ready(default_environment(env))
        except (OSError, ValueError, VideoMediaError):
            pass
    return MusicLoraTrainingStatus(engine='yue2', supported=supported, installed=installed, weights_ready=ready,
        paired_ready=paired,
        device='mps' if supported else None, validation='experimental',
        error_code=(None if paired else 'yue_lora_score_runtime_unprepared') if installed and ready
        else 'yue_lora_runtime_unprepared' if supported else 'yue_lora_platform_unsupported')


def runtime_ready(environment: YueLoraEnvironment) -> bool:
    return all(path.is_file() for path in (environment.python, environment.trainer_root / 'train_cli.py',
        environment.comfy_root / 'comfy/ldm/yue2/model.py', environment.base_checkpoint, environment.ffmpeg))


def score_ready(environment: YueLoraEnvironment) -> bool:
    cli, model, spec = environment.score_cli, environment.score_model, environment.score_model_spec
    if cli is None or model is None or spec is None:
        return False
    try:
        if any(not path.is_absolute() or path.is_symlink() or path.resolve() != path or not path.is_file() for path in (cli, model, spec)):
            return False
        if cli.stat().st_size <= 0 or os.name != 'nt' and not os.access(cli, os.X_OK) or model.stat().st_size <= 24 or not 1 <= spec.stat().st_size <= 2 * 1024**2:
            return False
        with model.open('rb') as handle:
            if handle.read(4) != b'GGUF':
                return False
        return TypeAdapter(JsonObject).validate_json(spec.read_bytes()).get('family') == 'sheetsage2'
    except (OSError, ValueError):
        return False


def score_argv(audio: Path, output_dir: Path, environment: YueLoraEnvironment) -> list[str]:
    if not score_ready(environment):
        raise ValueError('yue_lora_score_runtime_unprepared')
    return [str(environment.score_cli), '--task', 'midi', '--family', 'sheetsage2', '--model', str(environment.score_model),
        '--backend', environment.score_backend, '--mode', 'offline', '--model-spec-override', str(environment.score_model_spec),
        '--audio', str(audio), '--max-tokens', '5120', '--out-dir', str(output_dir)]


def validate_score(path: Path) -> str:
    """Require bounded ABC with real notes; native all-rest fallback is not training data."""
    if path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= 256 * 1024:
        raise ValueError('yue_lora_score_invalid')
    try:
        with path.open('rb') as handle:
            encoded = handle.read(256 * 1024 + 1)
        if len(encoded) > 256 * 1024:
            raise ValueError('yue_lora_score_invalid')
        score = encoded.decode('utf-8').strip()
    except UnicodeDecodeError as error:
        raise ValueError('yue_lora_score_invalid') from error
    if any(ord(character) < 32 and character not in '\n\r\t' for character in score):
        raise ValueError('yue_lora_score_invalid')
    if any(re.search(rf'(?m)^{field}:\s*\S+', score) is None for field in ('X', 'M', 'L', 'K')):
        raise ValueError('yue_lora_score_invalid')
    body = '\n'.join(line.split('%', 1)[0] for line in score.splitlines() if re.match(r'^\s*[A-Za-z]:', line) is None)
    body = re.sub(r'"[^"\n]*"|![^!\n]*!|\+[^+\n]*\+|\[[A-Za-z]:[^\]\n]*\]', '', body)
    if re.search(r'[A-Za-z]', re.sub(r'[A-Ga-gzZxXHLMNOPSTuv]', '', body)) is not None or re.search(r'[A-Ga-g]', body) is None:
        raise ValueError('yue_lora_score_invalid')
    return score


def fingerprint(request: MusicLoraTrainingRequest) -> str:
    return hashlib.sha256(request.model_dump_json(exclude_none=True).encode()).hexdigest()


def training_argv(request: MusicLoraTrainingRequest, run_dir: Path, environment: YueLoraEnvironment, *, target: Literal['ar', 'nar'] = 'nar') -> list[str]:
    if request.engine != 'yue2':
        raise ValueError('yue_lora_engine_invalid')
    if request.yue_ar_source_job_id is not None and target != 'ar':
        raise ValueError('yue_lora_ar_only_required')
    if target == 'ar' and request.yue_pair is None and request.yue_ar_source_job_id is None:
        raise ValueError('yue_lora_pair_required')
    steps = request.yue_pair.planner_steps if target == 'ar' and request.yue_pair is not None else request.max_steps
    rate = request.yue_pair.planner_learning_rate if target == 'ar' and request.yue_pair is not None else request.learning_rate
    checkpoint = 'trainer.safetensors' if request.yue_pair is None and request.yue_ar_source_job_id is None else f'trainer-{target}.safetensors'
    return [str(environment.python), '-u', str(environment.trainer_root / 'train_cli.py'), 'planner' if target == 'ar' else 'acoustic',
        '--comfy-root', str(environment.comfy_root), '--checkpoint', str(environment.base_checkpoint),
        '--data', str(run_dir / 'audio'), '--cache-dir', str(run_dir / 'cache'), '--out', str(run_dir / checkpoint),
        '--devices', environment.device,
        *(['--abc-mode', 'full', '--max-tokens', '5120', '--probe-every', '0'] if target == 'ar' else ['--conditioning', 'compact', '--prefix-mode', 'off', '--segment-seconds', str(request.segment_seconds)]),
        '--steps', str(steps), '--rank', str(request.rank), '--alpha', str(request.rank),
        '--lr', str(rate), '--seed', str(request.seed),
        '--max-seconds', str(request.segment_seconds), '--batch-size', '1', '--grad-accumulation', '1',
        '--warmup', str(min(10, max(0, steps - 1))), '--lora-dtype', 'fp32', '--targets', 'attention+mlp',
        '--vae-precision', 'fp32', '--eval-every', '0', '--eval-holdout', '0', '--sample-every', '0',
        '--save-every', str(min(25, steps)), '--log-every', '1', '--no-recursive']


def prepare_worker(request: MusicLoraTrainingRequest, run_dir: Path, sources: list[Path],
                   environment: YueLoraEnvironment | None = None, *, prepared_audio: tuple[PreparedAudio, ...] | None = None) -> tuple[list[str], Path, dict[str, str]]:
    env = environment or default_environment()
    if environment is None:
        readiness = status()
        if not readiness.installed or not readiness.weights_ready:
            raise ValueError('yue_lora_runtime_unprepared')
    if request.engine != 'yue2' or not runtime_ready(env):
        raise ValueError('yue_lora_runtime_unprepared')
    if request.yue_ar_source_job_id is not None:
        if prepared_audio is None or len(prepared_audio) != len(sources) or any(audio.path != source for audio, source in zip(prepared_audio, sources, strict=True)):
            raise ValueError('yue_lora_prepared_audio_invalid')
        for audio in prepared_audio:
            validate_prepared_audio(audio, segment_seconds=request.segment_seconds)
    elif prepared_audio is not None:
        raise ValueError('yue_lora_prepared_audio_invalid')
    if (request.yue_pair is not None or request.yue_ar_source_job_id is not None) and not score_ready(env):
        raise ValueError('yue_lora_score_runtime_unprepared')
    if (not run_dir.is_absolute() or run_dir.is_symlink() or run_dir.resolve() != run_dir
            or re.fullmatch('[0-9a-f]{32}', run_dir.name) is None):
        raise ValueError('yue_lora_run_directory_invalid')
    if len(sources) != len(request.samples) or any(not source.is_absolute() or source.is_symlink() or not source.is_file() for source in sources):
        raise ValueError('yue_lora_dataset_invalid')
    runtime: JsonObject = {'python': str(env.python),
            'trainer_root': str(env.trainer_root), 'comfy_root': str(env.comfy_root), 'base_checkpoint': str(env.base_checkpoint),
            'ffmpeg': str(env.ffmpeg), 'device': env.device}
    if request.yue_pair is not None or request.yue_ar_source_job_id is not None:
        runtime.update({'score_cli': str(env.score_cli), 'score_model': str(env.score_model),
            'score_model_spec': str(env.score_model_spec), 'score_backend': env.score_backend})
    plan: JsonObject = {'request': request.model_dump(mode='json'), 'request_sha256': fingerprint(request),
        'sources': [str(source) for source in sources], 'runtime': runtime}
    if prepared_audio is not None:
        plan['prepared_audio'] = [{'path': str(audio.path), 'sha256': audio.sha256} for audio in prepared_audio]
    with (run_dir / 'request.json').open('x', encoding='utf-8') as handle:
        json.dump(plan, handle, ensure_ascii=False, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    child_env = os.environ.copy()
    child_env.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
        'DO_NOT_TRACK': '1', 'PYTHONUTF8': '1', 'PYTHONUNBUFFERED': '1'})
    worker = Path(__file__).resolve().parents[1] / 'scripts/yue_lora_worker.py'
    return [str(env.python), str(worker), '--request', str(run_dir / 'request.json')], run_dir, child_env
