"""Optional local engines. Setup clones them. Generation uses the installed checkout.

The API process does not import Kokoro, Chatterbox, MLX, or RVC, and it never
downloads weights. A missing checkout names the setup script. A missing weight
file names what Seth still has to place by hand. Turbo, 14B, S2V, and Animate
are refused before any process starts.

GPT-SoVITS, Seed-VC, and LTX stay in place. Song videos are not retargeted.
"""
from __future__ import annotations

import asyncio
import logging
import wave
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .module_contracts import ModuleId
from .contracts import Contract
from .job_lifecycle import await_cleanup, cancel_and_wait, kill_process_tree
from .video_process import WorkerIdentity, spawn_owned, terminate_verified
from .resource_admission import admission_lock, native_work_inflight, require_setup_idle
from .gpu_lease import gpu_lease
from .stems import gpu_lock
from .video_projects import atomic_text
from .video_media import VideoMediaError, probe_media, tool
from typing import Literal
from pydantic import Field

_LOG = logging.getLogger(__name__)
_TASKS: dict[str, asyncio.Task[LocalRun]] = {}
_UNVERIFIED: set[str] = set()
_STOPPING = False


class StoredRun(Contract):
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    engine: Literal['kokoro', 'chatterbox', 'wan22', 'rvc']
    status: Literal['queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted']
    output_path: str
    worker: WorkerIdentity | None = None


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


def local_root(identifier: str) -> Path:
    from .config import DATA_DIR
    if identifier not in {'kokoro', 'chatterbox', 'wan22', 'rvc', 'inputs', '_runs'}:
        raise OptionalEngineError('unknown_engine', 'That optional engine is not registered.')
    data = DATA_DIR.resolve()
    path = DATA_DIR
    for component in ('outputs', 'local-engines', identifier):
        path = path / component
        if path.is_symlink() or not path.resolve().is_relative_to(data):
            raise OptionalEngineError('storage_unavailable', 'Local engine storage is unavailable.')
    return path.resolve()


def contained_output(path: Path) -> Path:
    root = local_root(path.parent.name)
    resolved = path.resolve()
    if path.is_symlink() or resolved.parent != root:
        raise OptionalEngineError('storage_unavailable', 'Local engine storage is unavailable.')
    return resolved


def _output(identifier: str, suffix: str) -> Path:
    root = local_root(identifier)
    root.mkdir(parents=True, exist_ok=True)
    name = f'{uuid.uuid4().hex}{suffix}' if identifier == 'inputs' else f'{uuid.uuid4().hex}.partial{suffix}'
    return contained_output(root / name)


def stage_input_path(suffix: str) -> Path:
    """A browser-picked clip, still, or trained voice. Not a weight download."""
    allowed = {'.wav', '.flac', '.mp3', '.png', '.jpg', '.jpeg', '.webp', '.pth'}
    if suffix.lower() not in allowed:
        raise OptionalEngineError('input_refused', 'Choose a wav, flac, mp3, png, jpg, webp, or pth file.')
    return _output('inputs', suffix.lower())


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


async def launch(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float,
                 receipt_path: Path | None = None, on_identity: Callable[[WorkerIdentity], None] | None = None) -> subprocess.CompletedProcess[str]:
    """Supervise one worker, retaining backend diagnostics and draining its tree."""
    root = local_root('_runs')
    root.mkdir(parents=True, exist_ok=True)
    receipt = contained_output(receipt_path or root / f'{uuid.uuid4().hex}.worker.json')
    log_path = contained_output(receipt.with_suffix('.log'))
    proc: asyncio.subprocess.Process | None = None
    try:
        with log_path.open('wb') as log:
            proc = await spawn_owned(argv, receipt_path=receipt, cwd=cwd, env=env,
                                     stdout=log.fileno(), on_identity=on_identity)
            async with asyncio.timeout(timeout):
                code = await proc.wait()
        with log_path.open('rb') as reader:
            reader.seek(0, 2)
            reader.seek(max(0, reader.tell() - 8000))
            tail = reader.read(8000).decode('utf-8', errors='replace')
        return subprocess.CompletedProcess(argv, code, '', tail)
    finally:
        if proc is not None:
            await await_cleanup(kill_process_tree(proc))
            if proc.stdin is not None:
                proc.stdin.close()


def _record_path(identifier: str) -> Path:
    if len(identifier) != 32 or any(char not in '0123456789abcdef' for char in identifier):
        raise OptionalEngineError('storage_unavailable', 'Local engine storage is unavailable.')
    return contained_output(local_root('_runs') / f'{identifier}.json')


def _save_run(record: StoredRun) -> None:
    try:
        atomic_text(_record_path(record.id), record.model_dump_json())
    except OSError as exc:
        raise OptionalEngineError('storage_unavailable', 'Local engine storage is unavailable.') from exc


async def _validate_output(path: Path) -> None:
    if path.suffix == '.wav':
        with wave.open(str(path), 'rb') as source:
            if source.getnframes() <= 0 or source.getnchannels() < 1 or source.getframerate() < 1:
                raise OptionalEngineError('engine_failed', 'The engine did not write valid audio.')
            expected = source.getnframes() * source.getnchannels() * source.getsampwidth()
            actual = 0
            while chunk := source.readframes(65536):
                actual += len(chunk)
            if actual != expected:
                raise OptionalEngineError('engine_failed', 'The engine did not write valid audio.')
    else:
        info = await probe_media(path)
        if info.width < 1 or info.height < 1 or info.video_duration <= 0:
            raise OptionalEngineError('engine_failed', 'The engine did not write valid video.')
        from .job_lifecycle import spawn_process, communicate_process
        proc = await spawn_process(tool('ffmpeg'), '-v', 'error', '-xerror', '-i', str(path), '-map', '0:v:0', '-f', 'null', '-',
                                   stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        await communicate_process(proc, 120)
        if proc.returncode != 0:
            raise OptionalEngineError('engine_failed', 'The engine did not write valid video.')


async def _execute(record: StoredRun, argv: list[str], cwd: Path, output: Path, timeout: float, runtime: str) -> LocalRun:
    request = output.with_suffix('.json')
    completed_worker = False
    try:
        record.status = 'running'
        _save_run(record)
        def own(identity: WorkerIdentity) -> None:
            record.worker = identity
            _save_run(record)
        async with gpu_lease(gpu_lock, 'video_generation' if record.engine == 'wan22' else 'voice_conversion', record.engine):
            completed = await launch(argv, cwd=cwd, env=_child_env(), timeout=timeout,
                                     receipt_path=local_root('_runs') / f'{record.id}.worker.json', on_identity=own)
        completed_worker = True
        record.worker = None
        if completed.returncode != 0:
            _LOG.warning('Optional engine %s failed: %s', record.engine, completed.stderr or completed.stdout)
            raise OptionalEngineError('engine_failed', 'The local engine failed. See the backend log for diagnostics.')
        await _validate_output(contained_output(output))
        final = contained_output(Path(record.output_path))
        output.replace(final)
        record.status = 'completed'
        _save_run(record)
        return LocalRun('completed', f'{record.engine} wrote {final.name}.', final, runtime)
    except asyncio.CancelledError:
        record.status = 'cancelled'
        raise
    except (TimeoutError, subprocess.TimeoutExpired, OSError, EOFError, wave.Error, VideoMediaError) as exc:
        _LOG.exception('Optional engine %s failed', record.engine)
        record.status = 'failed'
        raise OptionalEngineError('engine_failed', 'The local engine failed. See the backend log for diagnostics.') from exc
    except OptionalEngineError:
        record.status = 'failed'
        raise
    except Exception as exc:
        _LOG.exception('Optional engine %s failed unexpectedly', record.engine)
        record.status = 'failed'
        raise OptionalEngineError('engine_failed', 'The local engine failed. See the backend log for diagnostics.') from exc
    finally:
        await await_cleanup(_cleanup_run(record, output, request, completed_worker))


async def _cleanup_run(record: StoredRun, output: Path, request: Path, completed_worker: bool) -> None:
    # A failed drain retains the identity and gates admission until startup
    # can prove that exact supervised worker no longer exists.
    if record.worker is not None and not completed_worker:
        try:
            if await terminate_verified(record.worker):
                record.worker = None
            else:
                _UNVERIFIED.add(record.id)
        except Exception:
            _UNVERIFIED.add(record.id)
            _LOG.exception('Optional worker ownership could not be reconciled')
    try:
        if record.status != 'completed' and record.worker is None:
            output.unlink(missing_ok=True)
        if record.worker is None:
            request.unlink(missing_ok=True)
        _save_run(record)
    finally:
        _TASKS.pop(record.id, None)

async def _run(identifier: ModuleId, argv: list[str], *, cwd: Path, output: Path, timeout: float, runtime: str) -> LocalRun:
    if identifier not in ('kokoro', 'chatterbox', 'wan22', 'rvc'):
        raise OptionalEngineError('unknown_engine', 'That optional engine is not registered.')
    output = contained_output(output)
    final = output.with_name(output.name.replace('.partial', ''))
    record = StoredRun(id=output.name[:32], engine=identifier, status='queued', output_path=str(final))
    try:
        async with admission_lock:
            require_setup_idle()
            from .video_jobs import work_busy as video_busy
            from .work_busy import other_work_busy
            if _STOPPING or work_busy() or native_work_inflight() or video_busy() or await other_work_busy():
                raise OptionalEngineError('engine_busy', 'Another local job is using the engine resources.')
            _save_run(record)
            task = asyncio.create_task(_execute(record, argv, cwd, output, timeout, runtime))
            _TASKS[record.id] = task
    except BaseException:
        output.with_suffix('.json').unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        raise
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
        await await_cleanup(asyncio.gather(task, return_exceptions=True))
        if record.status == 'queued':
            record.status = 'cancelled'
            _save_run(record)
            output.with_suffix('.json').unlink(missing_ok=True)
        raise
    finally:
        if task.done():
            _TASKS.pop(record.id, None)


def work_busy() -> bool:
    return bool(_UNVERIFIED) or any(not task.done() for task in _TASKS.values())


async def recover() -> None:
    global _STOPPING
    _STOPPING = True
    root = local_root('_runs')
    if root.is_dir():
        for path in root.glob('*.json'):
            if len(path.stem) != 32:
                continue
            record = StoredRun.model_validate_json(contained_output(path).read_bytes())
            if record.id != path.stem or record.id in _TASKS:
                continue
            if record.worker is not None:
                expected = root / f'{record.id}.worker.json'
                if expected.is_symlink() or Path(record.worker.receipt) != expected or not await terminate_verified(record.worker):
                    _UNVERIFIED.add(record.id)
                    continue
                record.worker = None
                _UNVERIFIED.discard(record.id)
            if record.status in {'queued', 'running'}:
                record.status = 'interrupted'
                final = contained_output(Path(record.output_path))
                if final.is_file():
                    try:
                        await _validate_output(final)
                        record.status = 'completed'
                    except (OSError, EOFError, wave.Error, VideoMediaError, OptionalEngineError):
                        _LOG.warning('Interrupted optional output is invalid', exc_info=True)
                candidate = contained_output(Path(record.output_path).with_name(Path(record.output_path).stem + '.partial' + Path(record.output_path).suffix))
                candidate.unlink(missing_ok=True)
                candidate.with_suffix('.json').unlink(missing_ok=True)
            _save_run(record)
    _STOPPING = False


async def shutdown() -> None:
    global _STOPPING
    _STOPPING = True
    tasks = tuple(_TASKS.values())
    for task in tasks:
        if not task.done():
            task.cancel()
    await await_cleanup(asyncio.gather(*tasks, return_exceptions=True))
    for identifier, task in tuple(_TASKS.items()):
        if task.done():
            record = StoredRun.model_validate_json(_record_path(identifier).read_bytes())
            if record.status == 'queued':
                record.status = 'cancelled'
                _save_run(record)
            _TASKS.pop(identifier, None)


def _worker() -> Path:
    return Path(__file__).resolve().parents[1] / 'scripts' / 'optional_engine_worker.py'


async def narrate_kokoro(text: str, *, voice: str = 'af_heart', lang: str = 'a') -> LocalRun:
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
    return await _run('kokoro', [str(python), str(_worker()), str(request)], cwd=require_installed('kokoro'),
                output=output, timeout=180, runtime=KOKORO_RUNTIME)


async def speak_chatterbox(
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
    names: tuple[str, ...]
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
    return await _run('chatterbox', [str(_engine_python('chatterbox')), str(_worker()), str(request)],
                cwd=require_installed('chatterbox'), output=output, timeout=300,
                runtime='PyTorch Chatterbox ' + model + ' via from_local. Device is cuda, mps, or cpu. Turbo is not called. This does not replace GPT-SoVITS.')


async def render_wan(
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
    return await _run('wan22', argv, cwd=require_installed('wan22'), output=output, timeout=3600, runtime=WAN_RUNTIME)


async def convert_rvc(model_path: str, input_path: str) -> LocalRun:
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
    return await _run('rvc', argv, cwd=root, output=output, timeout=600, runtime=RVC_RUNTIME)


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
