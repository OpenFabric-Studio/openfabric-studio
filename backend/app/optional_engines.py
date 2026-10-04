"""Optional local engines. Setup clones them. Generation uses the installed checkout.

The API process does not import Kokoro, Chatterbox, MLX, or RVC, and it never
downloads weights. A missing checkout names the setup script. A missing weight
file names what Seth still has to place by hand. Turbo, 14B, S2V, and Animate
are refused before any process starts.

GPT-SoVITS, Seed-VC, and LTX stay in place. Song videos are not retargeted.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from .module_contracts import ModuleId

OPTIONAL_ENGINE_IDS: tuple[ModuleId, ...] = ('kokoro', 'chatterbox', 'wan22', 'rvc')

SETUP_SCRIPTS: dict[ModuleId, str] = {
    'kokoro': 'setup_kokoro.sh',
    'chatterbox': 'setup_chatterbox.sh',
    'wan22': 'setup_wan22.sh',
    'rvc': 'setup_rvc.sh',
}

PACKAGES: dict[ModuleId, tuple[str, ...]] = {
    'kokoro': ('kokoro', 'soundfile'),
    'chatterbox': ('chatterbox-tts',),
    'wan22': ('mlx-video',),
    'rvc': ('torch', 'soundfile', 'librosa', 'faiss-cpu'),
}

_RVC_PACKAGES = (
    'torch', 'torchaudio', 'soundfile', 'librosa', 'numpy', 'faiss-cpu',
    'pyyaml', 'scipy', 'av', 'einops', 'tqdm',
)

KOKORO_VOICES: dict[str, tuple[str, ...]] = {
    'a': ('af_heart', 'af_bella', 'af_sarah', 'am_adam', 'am_michael'),
    'b': ('bf_emma', 'bf_isabella', 'bm_george', 'bm_fable'),
}
CHATTERBOX_LANGUAGES = (
    'ar', 'da', 'de', 'el', 'en', 'es', 'fi', 'fr', 'he', 'hi', 'it', 'ja', 'ko',
    'ms', 'nl', 'no', 'pl', 'pt', 'ru', 'sv', 'sw', 'tr', 'zh',
)
KOKORO_RUNTIME = (
    'PyTorch Kokoro-82M, not the community MLX port. '
    'The worker sets PYTORCH_ENABLE_MPS_FALLBACK=1 and uses MPS on a Mac when it is available, otherwise CPU.'
)
WAN_RUNTIME = 'mlx-video Wan 2.2 TI2V-5B. LTX remains the song and default video engine.'
RVC_RUNTIME = 'RVC CLI. On a Mac the upstream config uses CPU when CUDA is absent. Seed-VC is unchanged.'


class OptionalEngineError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class LocalRun:
    status: str
    detail: str
    output_path: Path | None = None
    runtime: str = ''


def engine_dir(identifier: ModuleId) -> Path:
    from . import config
    paths = {
        'kokoro': config.KOKORO_DIR,
        'chatterbox': config.CHATTERBOX_DIR,
        'wan22': config.WAN22_DIR,
        'rvc': config.RVC_DIR,
    }
    try:
        return paths[identifier]
    except KeyError as exc:
        raise OptionalEngineError('unknown_engine', 'That optional engine is not registered.') from exc


def install_command(uv: str, identifier: ModuleId, python: Path, target: Path) -> list[str]:
    """Package install only. No model repo, no from_pretrained, no Turbo, no 14B."""
    if identifier == 'rvc':
        return [uv, 'pip', 'install', '--python', str(python), *_RVC_PACKAGES]
    if identifier in ('kokoro', 'chatterbox', 'wan22'):
        command = [uv, 'pip', 'install', '--python', str(python), '-e', str(target)]
        if identifier == 'kokoro':
            command.append('soundfile')
        return command
    raise OptionalEngineError('unknown_engine', 'That optional engine is not registered.')


def setup_hint(identifier: ModuleId) -> str:
    script = SETUP_SCRIPTS.get(identifier)
    if script is None:
        raise OptionalEngineError('unknown_engine', 'That optional engine is not registered.')
    return f'Run ./{script} on the Mac. OpenFabric does not download weights for this engine.'


def installed(identifier: ModuleId) -> bool:
    from .module_catalog import DEFINITIONS, engine_python
    if identifier not in OPTIONAL_ENGINE_IDS:
        return False
    root = engine_dir(identifier)
    definition = DEFINITIONS[identifier]
    python = engine_python(root, sys.platform)
    return all((root / marker).exists() for marker in definition.source_markers) and python.is_file()


def require_installed(identifier: ModuleId) -> Path:
    if identifier not in OPTIONAL_ENGINE_IDS:
        raise OptionalEngineError('unknown_engine', 'That optional engine is not registered.')
    if not installed(identifier):
        raise OptionalEngineError('engine_not_installed', setup_hint(identifier))
    return engine_dir(identifier)


def video_engine_preference() -> str:
    """Song videos stay on LTX. This preference does not retarget them."""
    value = os.environ.get('OPENFABRIC_VIDEO_ENGINE', 'ltx').strip().lower()
    if value in ('', 'ltx', 'ltx-2', 'ltx2'):
        return 'ltx'
    if value in ('wan22', 'wan', 'wan2.2'):
        return 'wan22'
    raise OptionalEngineError(
        'video_engine_refused',
        'OPENFABRIC_VIDEO_ENGINE must be ltx (the default) or wan22. Song videos stay on LTX either way.',
    )


def _weights_root(env_name: str, folder: str) -> Path:
    from .config import DATA_DIR
    value = os.environ.get(env_name, '').strip()
    return Path(value) if value else DATA_DIR / 'models' / folder


def _missing(identifier: ModuleId, names: list[str]) -> None:
    if names:
        raise OptionalEngineError(
            'weights_missing',
            'Place these files yourself. OpenFabric does not download them for ' + identifier + ': ' + ', '.join(names) + '.',
        )


def _output(identifier: str, suffix: str) -> Path:
    from .config import DATA_DIR
    path = DATA_DIR / 'outputs' / 'local-engines' / identifier / f'{uuid.uuid4().hex}{suffix}'
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _engine_python(identifier: ModuleId) -> Path:
    from .module_catalog import engine_python
    return engine_python(require_installed(identifier), sys.platform)


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    env['HF_HUB_OFFLINE'] = '1'
    env['TRANSFORMERS_OFFLINE'] = '1'
    env['HF_HUB_DISABLE_TELEMETRY'] = '1'
    env['PYTORCH_ENABLE_MPS_FALLBACK'] = '1'
    env['PYTHONUTF8'] = '1'
    return env


def launch(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float) -> subprocess.CompletedProcess[str]:
    """One engine process. Tests replace this. It does not download weights."""
    return subprocess.run(
        argv, cwd=str(cwd), env=env, timeout=timeout, check=False,
        capture_output=True, text=True,
    )


def _run(identifier: ModuleId, argv: list[str], *, cwd: Path, output: Path, timeout: float, runtime: str) -> LocalRun:
    try:
        completed = launch(argv, cwd=cwd, env=_child_env(), timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise OptionalEngineError('engine_failed', f'{identifier} timed out before writing output.') from exc
    except OSError as exc:
        raise OptionalEngineError('engine_failed', f'Could not start {identifier}: {exc.strerror or exc}') from exc
    if completed.returncode != 0 or not output.is_file() or output.stat().st_size < 16:
        tail = (completed.stderr or completed.stdout or '').strip().splitlines()
        short = tail[-1][:240] if tail else 'no output'
        raise OptionalEngineError('engine_failed', f'{identifier} failed: {short}')
    return LocalRun('completed', f'{identifier} wrote {output.name}.', output, runtime)


def _worker() -> Path:
    return Path(__file__).resolve().parents[1] / 'scripts' / 'optional_engine_worker.py'


def narrate_kokoro(text: str, *, voice: str = 'af_heart', lang: str = 'a') -> LocalRun:
    """Preset Kokoro voice. A reference clip is not accepted."""
    cleaned = text.strip()
    if not cleaned:
        raise OptionalEngineError('text_required', 'Kokoro needs text.')
    if lang not in KOKORO_VOICES or voice not in KOKORO_VOICES[lang]:
        raise OptionalEngineError('kokoro_voice_refused', 'Kokoro only speaks its preset voices. It does not clone a person.')
    require_installed('kokoro')
    root = _weights_root('OPENFABRIC_KOKORO_WEIGHTS', 'kokoro')
    needed = [name for name, path in (
        ('config.json', root / 'config.json'),
        ('kokoro-v1_0.pth', root / 'kokoro-v1_0.pth'),
        (f'voices/{voice}.pt', root / 'voices' / f'{voice}.pt'),
    ) if not path.is_file()]
    _missing('kokoro', needed)
    output = _output('kokoro', '.wav')
    request = output.with_suffix('.json')
    request.write_text(json.dumps({
        'task': 'kokoro', 'text': cleaned, 'lang': lang,
        'config_path': str(root / 'config.json'),
        'model_path': str(root / 'kokoro-v1_0.pth'),
        'voice_path': str(root / 'voices' / f'{voice}.pt'),
        'output_path': str(output),
    }), encoding='utf-8')
    python = _engine_python('kokoro')
    return _run('kokoro', [str(python), str(_worker()), str(request)], cwd=require_installed('kokoro'),
                output=output, timeout=180, runtime=KOKORO_RUNTIME)


def speak_chatterbox(
    text: str, *, model: str = 'original', audio_prompt_path: str | None = None, language_id: str = 'en',
) -> LocalRun:
    if model == 'turbo':
        raise OptionalEngineError('chatterbox_turbo_refused', 'Chatterbox Turbo is not wired and is not claimed to work on a Mac.')
    if model not in ('original', 'multilingual'):
        raise OptionalEngineError('chatterbox_model_refused', 'Only the original and multilingual Chatterbox classes are registered.')
    if model == 'multilingual' and language_id not in CHATTERBOX_LANGUAGES:
        raise OptionalEngineError('chatterbox_language_refused', 'That language is not in the Chatterbox multilingual list.')
    require_installed('chatterbox')
    cleaned = text.strip()
    if not cleaned:
        raise OptionalEngineError('text_required', 'Chatterbox needs text.')
    prompt: Path | None = None
    if audio_prompt_path:
        prompt = Path(audio_prompt_path)
        if not prompt.is_file() or prompt.suffix.lower() not in ('.wav', '.flac', '.mp3'):
            raise OptionalEngineError('audio_missing', 'The Chatterbox reference clip must be an existing wav, flac, or mp3 file.')
    root = _weights_root('OPENFABRIC_CHATTERBOX_WEIGHTS', 'chatterbox') / model
    if model == 'original':
        names = ('ve.safetensors', 't3_cfg.safetensors', 's3gen.safetensors', 'tokenizer.json')
        t3_name = ''
    else:
        names = ('ve.pt', 's3gen.pt', 'grapheme_mtl_merged_expanded_v1.json')
        t3_name = 't3_mtl23ls_v3.safetensors' if (root / 't3_mtl23ls_v3.safetensors').is_file() else 't3_mtl23ls_v2.safetensors'
    needed = [name for name in names if not (root / name).is_file()]
    if model == 'multilingual' and not (root / t3_name).is_file():
        needed.append('t3_mtl23ls_v2.safetensors or t3_mtl23ls_v3.safetensors')
    if prompt is None and not (root / 'conds.pt').is_file():
        needed.append('conds.pt (or pass a reference clip)')
    _missing('chatterbox', needed)
    output = _output('chatterbox', '.wav')
    request = output.with_suffix('.json')
    request.write_text(json.dumps({
        'task': 'chatterbox', 'model': model, 'text': cleaned, 'weights': str(root),
        't3_model': t3_name, 'language_id': language_id,
        'audio_prompt_path': str(prompt) if prompt is not None else '',
        'output_path': str(output),
    }), encoding='utf-8')
    return _run('chatterbox', [str(_engine_python('chatterbox')), str(_worker()), str(request)],
                cwd=require_installed('chatterbox'), output=output, timeout=300,
                runtime='PyTorch Chatterbox ' + model + ' via from_local. Device is cuda, mps, or cpu. Turbo is not called. This does not replace GPT-SoVITS.')


def render_wan(
    prompt: str, *, variant: str = 'ti2v-5b', image_path: str | None = None,
    width: int = 832, height: int = 480, num_frames: int = 17,
) -> LocalRun:
    if variant != 'ti2v-5b':
        raise OptionalEngineError('wan_variant_refused', 'Only Wan 2.2 TI2V-5B is registered. 14B, S2V, and Animate are not wired.')
    cleaned = prompt.strip()
    if not cleaned:
        raise OptionalEngineError('text_required', 'Wan needs a prompt.')
    if width % 32 or height % 32 or not (256 <= width <= 1280) or not (256 <= height <= 1280):
        raise OptionalEngineError('wan_size_refused', 'Wan 2.2 TI2V sizes must be multiples of 32, from 256 to 1280.')
    if num_frames % 4 != 1 or not (5 <= num_frames <= 81):
        raise OptionalEngineError('wan_frames_refused', 'Wan frame count must be 4n+1, from 5 to 81.')
    require_installed('wan22')
    image: Path | None = None
    if image_path:
        image = Path(image_path)
        if not image.is_file() or image.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
            raise OptionalEngineError('image_missing', 'The Wan image must be an existing png, jpg, or webp file.')
    root = _weights_root('OPENFABRIC_WAN22_MODEL_DIR', 'wan22-ti2v-5b')
    forbidden = [name for name in ('low_noise_model.safetensors', 'high_noise_model.safetensors') if (root / name).is_file()]
    if forbidden or any(token in root.name.lower() for token in ('14b', 's2v', 'animate')):
        raise OptionalEngineError('wan_variant_refused', 'That folder looks like Wan 14B, S2V, or Animate. Only a converted TI2V-5B folder is accepted.')
    needed = [name for name in ('config.json', 'model.safetensors', 't5_encoder.safetensors', 'vae.safetensors') if not (root / name).is_file()]
    _missing('wan22', needed)
    output = _output('wan22', '.mp4')
    argv = [
        str(_engine_python('wan22')), '-m', 'mlx_video.models.wan_2.generate',
        '--model-dir', str(root), '--prompt', cleaned,
        '--width', str(width), '--height', str(height), '--num-frames', str(num_frames),
        '--output-path', str(output),
    ]
    if image is not None:
        argv.extend(['--image', str(image)])
    return _run('wan22', argv, cwd=require_installed('wan22'), output=output, timeout=3600, runtime=WAN_RUNTIME)


def convert_rvc(model_path: str, input_path: str) -> LocalRun:
    require_installed('rvc')
    voice = Path(model_path)
    source = Path(input_path)
    if voice.suffix.lower() != '.pth' or not voice.is_file():
        raise OptionalEngineError('weights_missing', 'RVC needs a trained .pth voice you already have. OpenFabric does not train or download one. ' + setup_hint('rvc'))
    if not source.is_file() or source.suffix.lower() not in ('.wav', '.flac', '.mp3'):
        raise OptionalEngineError('audio_missing', 'RVC needs an existing wav, flac, or mp3 input.')
    root = require_installed('rvc')
    needed = []
    if not (root / 'assets' / 'hubert_base' / 'config.json').is_file():
        needed.append('assets/hubert_base/config.json (local HuBERT folder)')
    if not (root / 'assets' / 'rmvpe' / 'rmvpe.pt').is_file():
        needed.append('assets/rmvpe/rmvpe.pt')
    _missing('rvc', needed)
    output = _output('rvc', '.wav')
    argv = [
        str(_engine_python('rvc')), str(root / 'infer' / 'cli.py'),
        '--model', str(voice), '--input', str(source), '--output', str(output),
        '--f0-method', 'rmvpe', '--overwrite', '--format', 'wav',
    ]
    return _run('rvc', argv, cwd=root, output=output, timeout=600, runtime=RVC_RUNTIME)


def prepare_chatterbox(model: str) -> None:
    """Compatibility check used by tests. Forbidden classes fail before install."""
    if model == 'turbo':
        raise OptionalEngineError('chatterbox_turbo_refused', 'Chatterbox Turbo is not wired and is not claimed to work on a Mac.')
    if model not in ('original', 'multilingual'):
        raise OptionalEngineError('chatterbox_model_refused', 'Only the original and multilingual Chatterbox classes are registered.')
    require_installed('chatterbox')


def prepare_wan(variant: str) -> None:
    if variant != 'ti2v-5b':
        raise OptionalEngineError('wan_variant_refused', 'Only Wan 2.2 TI2V-5B is registered. 14B, S2V, and Animate are not wired.')
    require_installed('wan22')


def prepare_kokoro() -> None:
    require_installed('kokoro')


def prepare_rvc() -> None:
    require_installed('rvc')
