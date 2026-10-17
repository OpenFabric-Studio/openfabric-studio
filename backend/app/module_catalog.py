"""Offline module inventory and reviewed, server-owned dependency plans.

No model imports, downloads, cache placement or engine start happens during a
status request. Engine readiness requires more evidence than an existing folder.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform as host_platform
import re
import shutil
import sys
from collections.abc import Mapping, Sequence
from typing import Literal

from .video_process import WorkerOutputError, run_owned_command
from .module_contracts import (
    ModuleAction, ModuleEvidence, ModuleId, ModuleInfo, ModuleInventory,
    ModulePlan, ModulePlanRequest, ModulePlanStep, ModuleState,
)
from .module_evidence import environment_verified
from .module_licenses import declarations
from .module_runtime import configured_targets, RuntimeEvidence, runtime_status, speaker_status
from .job_lifecycle import run_owned_thread

CATALOG_VERSION = 'openfabric-modules-3'
MODULE_IDS: tuple[ModuleId, ...] = ('ace_step', 'yue2', 'stable_audio3', 'speech', 'singing', 'separation', 'video', 'media', 'transcription', 'source_import', 'ebooks', 'kokoro', 'chatterbox', 'wan22', 'rvc', 'speaker_review', 'music_dsp', 'music_beats')
DEPENDENCY_ONLY_MODULES = frozenset({'speaker_review'})
AUTOMATIC_MUSIC_MODELS = frozenset({'ace_step', 'stable_audio3'})
REINSTALLABLE_MODULES: frozenset[ModuleId] = frozenset({'ace_step', 'separation', 'speech', 'singing', 'stable_audio3', 'kokoro', 'chatterbox', 'wan22', 'rvc'})
ENGINE_FOLDERS: Mapping[ModuleId, str] = {
    'ace_step': 'ACE-Step-1.5', 'yue2': 'YuE2', 'speech': 'gpt-sovits', 'singing': 'seed-vc',
    'separation': 'Demucs', 'video': 'ltx-2-mlx', 'media': 'ffmpeg', 'transcription': 'whisper.cpp',
    'source_import': 'reference-tools', 'ebooks': 'calibre',
    'kokoro': 'kokoro', 'chatterbox': 'chatterbox', 'wan22': 'mlx-video', 'rvc': 'rvc',
    'speaker_review': 'SpeakerReview',
    'stable_audio3': 'stable-audio-3',
    'music_dsp': 'music-dsp',
    'music_beats': 'MusicBeats',
}


@dataclass(frozen=True)
class ModuleDefinition:
    id: ModuleId
    name: str
    description: str
    dependencies: tuple[ModuleId, ...]
    source_markers: tuple[str, ...]
    packages: tuple[str, ...]
    download_bytes: int | None
    guidance: str
    url: str


DEFINITIONS: Mapping[ModuleId, ModuleDefinition] = {
    'music_beats': ModuleDefinition('music_beats', 'Beat and downbeat analysis', 'Optional local CPU beat timing estimates; manual corrections remain available.', ('media',), ('.openfabric-beats-runtime.json', '.venv/pyvenv.cfg'), ('beat-this', 'torch', 'torchaudio', 'numpy', 'einops', 'rotary-embedding-torch', 'soxr', 'pydantic'), None, 'Install the isolated pinned CPU runtime and, when selected, the small beat timing checkpoint. Analysis never downloads a model. Timing estimates require review and do not establish musical quality.', 'https://github.com/CPJKU/beat_this/tree/ad7974846029835307ba19a3d5cefbf40b243041'),
    'music_dsp': ModuleDefinition('music_dsp', 'Music processing', 'Optional native time-stretch and pitch processing; no music model.', ('media',), ('.openfabric-rubberband.json', 'bin/rubberband', 'COPYING', 'README.md', 'CHANGELOG', 'rubberband-4.0.0-source.tar.bz2'), (), 1736719, 'Install the pinned Rubber Band 4.0.0 executable with its license and corresponding source archive into this app’s managed tools. Initial runtime support is Apple Silicon macOS; quality requires listening review.', 'https://breakfastquay.com/rubberband/'),
    'speaker_review': ModuleDefinition('speaker_review', 'Speaker review', 'Optional local CPU speaker similarity screening with human-approved references.', (), ('.openfabric-speaker-runtime.json', '.venv/pyvenv.cfg'), ('speechbrain', 'torch', 'torchaudio'), None, 'Set up the separate CPU dependency environment. No weights are downloaded, even with model downloads selected. Obtain and verify the reviewed ECAPA checkpoint explicitly, then configure OPENFABRIC_SPEAKER_REVIEW_PYTHON and OPENFABRIC_SPEAKER_REVIEW_WEIGHTS. Similarity is a screening hint, not an identity verdict.', 'https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb'),
    'ace_step': ModuleDefinition('ace_step', 'ACE-Step', 'Music generation, covers and section edits.', ('media',), ('acestep/api_server.py', 'pyproject.toml'), ('torch', 'fastapi'), None, 'Automatically check or install the isolated environment, reuse existing selected models and download missing pinned model files when included. Existing source and model files are preserved.', 'https://github.com/ace-step/ACE-Step-1.5'),
    'yue2': ModuleDefinition('yue2', 'YuE', 'Native music generation, melody planning and MIDI tools.', ('media',), ('tools/model_manager_v2.py',), (), None, 'Install the native engine and selected weights. Windows needs NVIDIA compute capability 7.5+ and driver 580+. Linux requires the documented CUDA source build.', 'https://github.com/0xShug0/audio.cpp'),
    'stable_audio3': ModuleDefinition('stable_audio3', 'Stable Audio 3', 'Instrumental generation with the official Small Music and Medium MLX runtimes on Apple Silicon.', ('media',), ('optimized/mlx/requirements.txt', 'optimized/mlx/scripts/sa3_mlx.py', 'optimized/mlx/scripts/weights.py', 'optimized/mlx/models/defs/sa3_pipeline.py'), ('mlx', 'numpy', 'sentencepiece', 'huggingface-hub', 'soundfile'), None, 'Automatically install pinned source and Python 3.11 MLX dependencies, then download the Small Music, SAME-S and T5Gemma bundles when included. Reuse an existing complete Small Music or Medium bundle. Stability Community and Gemma terms apply. Apple Silicon only.', 'https://github.com/Stability-AI/stable-audio-3/tree/main/optimized/mlx'),
    'speech': ModuleDefinition('speech', 'Speech', 'GPT-SoVITS speech and audiobook narration.', ('media',), ('api.py', 'GPT_SoVITS'), ('torch', 'librosa', 'soundfile'), None, 'Install the reviewed GPT-SoVITS checkout and Python 3.11 environment. Complete upstream pretrained-weight setup and start api.py on loopback port 9880. Review a short speech preview before narration.', 'https://github.com/RVC-Boss/GPT-SoVITS'),
    'singing': ModuleDefinition('singing', 'Singing', 'Seed-VC singing conversion and voice training.', ('media', 'separation'), ('train.py', 'inference.py'), ('torch', 'transformers', 'soundfile'), None, 'Install the reviewed Seed-VC checkout and compatibility patch. Singing weights and auxiliary models must be reviewed before use. CPU training is unsupported; use compatible NVIDIA or Apple Silicon hardware.', 'https://github.com/Plachtaa/seed-vc'),
    'separation': ModuleDefinition('separation', 'Separation', 'Demucs stems; optional RoFormer uses separate reviewed setup.', ('media',), ('pyproject.toml',), ('demucs', 'torch', 'numpy'), None, 'Install Demucs and explicitly select the htdemucs model download. High-quality htdemucs_ft and RoFormer remain separate model choices requiring their documented setup.', 'https://github.com/facebookresearch/demucs'),
    'video': ModuleDefinition('video', 'Generated video', 'Pinned LTX/MLX generated scenes on Apple Silicon.', ('media',), ('pyproject.toml', 'uv.lock'), ('mlx',), None, 'Install the pinned Apple MLX environment. Select LTX 2.3 weights explicitly; LTX 2.5 stays an opt-in comparison. CPU cover/visualizer modes require only Media tools.', 'https://github.com/dgrauet/ltx-2-mlx'),
    'media': ModuleDefinition('media', 'Media tools', 'FFmpeg and FFprobe for audio/video import and export.', (), (), (), None, 'Install both FFmpeg and FFprobe. On Linux use your distribution package manager; system installation may require administrator access.', 'https://ffmpeg.org/download.html'),
    'transcription': ModuleDefinition('transcription', 'Transcription', 'Optional local whisper.cpp transcript preparation.', ('media',), (), (), None, 'Install whisper.cpp whisper-cli and a compatible local model; set REFERENCE_WHISPER_BIN and REFERENCE_WHISPER_MODEL server-side. No transcription weights are silently downloaded.', 'https://github.com/ggml-org/whisper.cpp'),
    'source_import': ModuleDefinition('source_import', 'Source imports', 'Pinned YouTube audio and subtitle import tools.', ('media',), (), (), None, 'Install backend/requirements-reference.txt into the backend environment and Deno 2.6.6 or newer. Provider availability remains separate from tool readiness.', 'https://docs.deno.com/runtime/getting_started/installation/'),
    'ebooks': ModuleDefinition('ebooks', 'Ebooks', 'Calibre normalization of unencrypted MOBI books.', (), (), (), None, 'Install Calibre separately from its official installer. Confirm ebook-convert is on PATH or set OPENFABRIC_EBOOK_CONVERT to its executable. Do not bypass document encryption.', 'https://calibre-ebook.com/download'),
    'kokoro': ModuleDefinition('kokoro', 'Kokoro', 'Preset narration from Kokoro-82M (Apache-2.0, hexgrad). It does not clone a person. On a Mac, set PYTORCH_ENABLE_MPS_FALLBACK=1.', ('media',), ('pyproject.toml', 'kokoro/pipeline.py'), ('kokoro', 'soundfile'), None, 'Clone the pinned Kokoro checkout with ./setup_kokoro.sh or Settings. No voice weights are downloaded. Install espeak-ng yourself. This does not replace GPT-SoVITS. The community MLX port is not the path this setup installs.', 'https://github.com/hexgrad/kokoro'),
    'chatterbox': ModuleDefinition('chatterbox', 'Chatterbox', 'Zero-shot voice clone from Chatterbox (MIT, Resemble AI). Original and multilingual can use cuda, cpu, or mps. Turbo is not a Mac path.', ('media',), ('pyproject.toml', 'src/chatterbox/tts.py'), ('chatterbox-tts',), None, 'Clone the pinned Chatterbox checkout with ./setup_chatterbox.sh or Settings. Weights are not downloaded. Turbo is not called and is not claimed to work on a Mac. This does not replace GPT-SoVITS.', 'https://github.com/resemble-ai/chatterbox'),
    'wan22': ModuleDefinition('wan22', 'Wan 2.2 5B', 'Text and image to video with Wan 2.2 TI2V-5B through mlx-video on Apple Silicon. 14B, S2V, and Animate are not wired.', ('media',), ('pyproject.toml', 'mlx_video/models/wan_2/generate.py'), ('mlx-video',), None, 'Clone the pinned mlx-video checkout with ./setup_wan22.sh or Settings. Apple Silicon only. OpenFabric does not download Wan 2.2 TI2V-5B weights and does not set up 14B, S2V, or Animate.', 'https://github.com/Blaizzy/mlx-video'),
    'rvc': ModuleDefinition('rvc', 'RVC', 'Timbre conversion with RVC (MIT). On a Mac this is CPU, not GPU. Seed-VC singing stays separate.', ('media',), ('webui.py', 'infer'), ('torch', 'soundfile', 'librosa', 'faiss-cpu', 'transformers'), None, 'Clone the pinned RVC checkout with ./setup_rvc.sh or Settings and install CPU packages from PyPI. HuBERT, RMVPE, and trained voices are not downloaded. On a Mac this does not use the GPU. Seed-VC is unchanged.', 'https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI'),
}


@dataclass(frozen=True)
class ModuleEnvironment:
    root: Path
    data_dir: Path
    platform: str
    architecture: str
    paths: Mapping[ModuleId, Path]
    python: Path
    uv: str | None
    ffmpeg_bin: Path
    calibre: str | None

    @classmethod
    def for_root(cls, root: Path, *, platform: str | None = None, architecture: str | None = None,
                 paths: Mapping[ModuleId, Path] | None = None, data_dir: Path | None = None) -> ModuleEnvironment:
        resolved = root.expanduser().resolve()
        mapped = {identifier: resolved / ('tools' if identifier in ('media', 'transcription', 'source_import', 'ebooks', 'music_dsp') else 'engines') / ENGINE_FOLDERS[identifier] for identifier in MODULE_IDS}
        if paths is not None:
            mapped.update(paths)
        return cls(resolved, data_dir or resolved / 'data', platform or sys.platform,
                   architecture or host_platform.machine().lower(), mapped, Path(sys.executable),
                   shutil.which('uv'), mapped['media'] / 'bin', shutil.which('ebook-convert'))


def configured_environment() -> ModuleEnvironment:
    from . import config
    root = Path(os.environ.get('OPENFABRIC_MODULE_ROOT') or str(Path.home() / '.openfabric-studio' / 'runtime'))
    paths: dict[ModuleId, Path] = {
        'ace_step': config.ACE_STEP_DIR, 'yue2': config.YUE2_DIR, 'speech': config.GPT_SOVITS_DIR,
        'singing': config.SEED_VC_DIR, 'separation': config.DEMUCS_DIR, 'video': config.LTX_DIR,
        'kokoro': config.KOKORO_DIR, 'chatterbox': config.CHATTERBOX_DIR, 'wan22': config.WAN22_DIR,
        'rvc': config.RVC_DIR,
        'stable_audio3': config.STABLE_AUDIO3_DIR,
    }
    if speaker_python := os.environ.get('OPENFABRIC_SPEAKER_REVIEW_PYTHON'):
        interpreter = Path(speaker_python).expanduser()
        if interpreter.parent.parent.name == '.venv':
            # Preserve a deliberately configured external review environment.
            paths['speaker_review'] = interpreter.parent.parent.parent
    base = ModuleEnvironment.for_root(root, paths=paths, data_dir=config.DATA_DIR)
    return ModuleEnvironment(base.root, base.data_dir, base.platform, base.architecture, base.paths,
        base.python, os.environ.get('UV_BIN') or base.uv, config.FFMPEG_BIN_DIR,
        os.environ.get('OPENFABRIC_EBOOK_CONVERT') or base.calibre)


def engine_python(engine: Path, platform: str) -> Path:
    return engine / '.venv' / ('Scripts/python.exe' if platform == 'win32' else 'bin/python')


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    output: str


async def probe(argv: list[str], *, timeout: float = 2, max_bytes: int = 8192) -> ProbeResult:
    """Version-only subprocess with bounded output, time and descendant ownership."""
    try:
        result = await run_owned_command(argv, timeout=timeout, max_bytes=max_bytes, stderr=asyncio.subprocess.STDOUT)
        return ProbeResult(result.returncode == 0, result.stdout.decode('utf-8', errors='replace').strip())
    except (OSError, TimeoutError, WorkerOutputError):
        return ProbeResult(False, '')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _architecture(environment: ModuleEnvironment) -> str:
    return {'x86_64': 'x64', 'amd64': 'x64', 'aarch64': 'arm64'}.get(environment.architecture.lower(), environment.architecture.lower())


def platform_key(environment: ModuleEnvironment) -> str:
    return f'{environment.platform}-{_architecture(environment)}'


def supported(identifier: ModuleId, environment: ModuleEnvironment) -> bool:
    if identifier in ('video', 'wan22', 'stable_audio3', 'music_dsp'):
        return platform_key(environment) == 'darwin-arm64'
    if identifier == 'singing':
        return platform_key(environment) in ('darwin-arm64', 'linux-x64', 'win32-x64')
    if identifier in ('ace_step', 'yue2', 'separation', 'speech', 'kokoro', 'chatterbox', 'rvc', 'music_beats'):
        return platform_key(environment) in ('darwin-arm64', 'linux-x64', 'win32-x64')
    return environment.platform in ('win32', 'darwin', 'linux')


def is_managed(identifier: ModuleId, environment: ModuleEnvironment) -> bool:
    expected = environment.root / ('tools' if identifier in ('media', 'transcription', 'source_import', 'ebooks', 'music_dsp') else 'engines') / ENGINE_FOLDERS[identifier]
    if expected.is_symlink() or not expected.resolve().is_relative_to(environment.root.resolve()):
        return False
    if identifier == 'media':
        return environment.ffmpeg_bin.resolve() == (expected / 'bin').resolve()
    candidate = environment.paths[identifier]
    # A symlink is an external installation even if it has the managed name.
    return not candidate.is_symlink() and candidate.resolve() == expected.resolve()


def _evidence(code: str, detail: str, verified: bool = False) -> ModuleEvidence:
    return ModuleEvidence(code=code, detail=detail, verified=verified)


def _manual(definition: ModuleDefinition) -> ModuleAction:
    return ModuleAction(kind='manual', label='Review setup instructions', detail=definition.guidance, url=definition.url)


def _environment_receipt(identifier: ModuleId, environment: ModuleEnvironment) -> bool:
    """Bounded receipt identity checks; receipts never imply model inference."""
    return environment_verified(environment, identifier)


def _tool(name: str, environment: ModuleEnvironment) -> str:
    candidate = environment.ffmpeg_bin / (name + '.exe' if environment.platform == 'win32' else name)
    return str(candidate) if candidate.is_file() else shutil.which(name) or str(candidate)


def available_bytes(path: Path) -> int | None:
    """Check the filesystem that will hold a destination, even before it exists."""
    try:
        while not path.exists() and path != path.parent:
            path = path.parent
        return shutil.disk_usage(path).free
    except OSError:
        return None


async def _runtime_inventory(environment: ModuleEnvironment) -> dict[ModuleId, RuntimeEvidence]:
    targets = configured_targets(environment)
    results = await asyncio.gather(*(runtime_status(environment, target) for target in targets))
    return {target.module_id: result for target, result in zip(targets, results)}


def recommend_features(modules: Sequence[ModuleInfo]) -> list[ModuleId]:
    """Choose one starter from existing evidence, without certifying hardware suitability."""
    supported_modules = {module.id: module for module in modules if module.supported}
    music: tuple[ModuleId, ...] = ('ace_step', 'yue2', 'stable_audio3')
    for state in ('ready', 'installed', None):
        for identifier in music:
            module = supported_modules.get(identifier)
            if module is not None and (state is None or module.state == state):
                return [identifier]
    return ['media'] if 'media' in supported_modules else []


async def inventory(environment: ModuleEnvironment | None = None) -> ModuleInventory:
    from .module_install import reinstall_available
    from .module_music_models import music_models_present
    from .module_native_setup import native_environment_present, native_setup_present
    env = environment or configured_environment()
    ffmpeg, ffprobe, calibre, runtimes = await asyncio.gather(
        probe([_tool('ffmpeg', env), '-version']), probe([_tool('ffprobe', env), '-version']),
        probe([env.calibre, '--version']) if env.calibre else asyncio.sleep(0, result=ProbeResult(False, '')),
        _runtime_inventory(env),
    )
    media_ready = ffmpeg.ok and ffmpeg.output.startswith('ffmpeg version ') and ffprobe.ok and ffprobe.output.startswith('ffprobe version ')
    calibre_ready = calibre.ok and re.search(r'ebook-convert\s+\(calibre\s+\d', calibre.output, re.IGNORECASE) is not None
    native_installed = native_environment_present(env)
    modules: list[ModuleInfo] = []
    for identifier in MODULE_IDS:
        definition = DEFINITIONS[identifier]
        support = supported(identifier, env)
        managed = is_managed(identifier, env)
        evidence: list[ModuleEvidence] = []
        state: ModuleState = 'missing'
        capabilities: list[str] = []
        if not support:
            state = 'unsupported'
            evidence.append(_evidence('unsupported_platform', 'This engine has no reviewed integration for this operating system and architecture.'))
        elif identifier == 'media':
            state = 'ready' if media_ready else 'partial' if ffmpeg.ok or ffprobe.ok else 'missing'
            evidence = [_evidence('ffmpeg_version', ffmpeg.output.splitlines()[0][:300] if ffmpeg.ok and ffmpeg.output else 'FFmpeg version check failed.', ffmpeg.ok and ffmpeg.output.startswith('ffmpeg version ')),
                        _evidence('ffprobe_version', ffprobe.output.splitlines()[0][:300] if ffprobe.ok and ffprobe.output else 'FFprobe version check failed.', ffprobe.ok and ffprobe.output.startswith('ffprobe version '))]
            capabilities = ['media_processing'] if media_ready else []
        elif identifier == 'ebooks':
            state = 'ready' if calibre_ready else 'partial' if env.calibre else 'missing'
            evidence.append(_evidence('calibre_version', calibre.output.splitlines()[0][:300] if calibre_ready else 'Calibre ebook-convert has not passed a bounded version check.', calibre_ready))
            capabilities = ['mobi_normalization'] if calibre_ready else []
        elif identifier == 'transcription':
            binary = shutil.which(os.environ.get('REFERENCE_WHISPER_BIN', 'whisper-cli'))
            model = os.environ.get('REFERENCE_WHISPER_MODEL', '')
            present = bool(model and Path(model).is_file())
            state = 'installed' if binary and present and media_ready else 'partial' if binary or present else 'missing'
            evidence.extend((_evidence('whisper_binary', 'Configured whisper.cpp CLI found.' if binary else 'whisper.cpp CLI missing.', binary is not None), _evidence('whisper_model', 'Configured local model exists; content and inference remain unverified.' if present else 'Local model must be selected server-side.', False)))
        elif identifier == 'source_import':
            try:
                pinned = importlib.metadata.version('yt-dlp') == '2026.8.19' and importlib.metadata.version('yt-dlp-ejs') == '0.8.0'
            except importlib.metadata.PackageNotFoundError:
                pinned = False
            deno = shutil.which('deno')
            deno_result = await probe([deno, '--version']) if deno else ProbeResult(False, '')
            match = re.match(r'deno (\d+)\.(\d+)\.(\d+)(?:\s|$)', deno_result.output)
            deno_ok = deno_result.ok and match is not None and tuple(int(part) for part in match.groups()) >= (2, 6, 6)
            state = 'ready' if pinned and deno_ok and media_ready else 'partial' if pinned or deno else 'missing'
            evidence.extend((_evidence('pinned_packages', 'Exact yt-dlp and solver versions are installed.' if pinned else 'Pinned import packages are missing or incompatible.', pinned), _evidence('deno_version', 'Deno meets the required minimum version.' if deno_ok else 'Deno 2.6.6 or newer is required.', deno_ok), _evidence('provider_unverified', 'Tool readiness does not establish current YouTube availability.')))
            capabilities = ['source_import_tools'] if state == 'ready' else []
        elif identifier == 'speaker_review':
            speaker_runtime = speaker_status(env)
            state = 'ready' if speaker_runtime.ready else 'partial' if speaker_runtime.installed or env.paths[identifier].exists() else 'missing'
            evidence.extend(speaker_runtime.evidence)
            capabilities.extend(speaker_runtime.capabilities)
        elif identifier == 'music_beats':
            from .music_beats_runtime import status as beats_status
            timing = await run_owned_thread(beats_status, env)
            state = 'ready' if timing.ready and media_ready else 'partial' if timing.installed or env.paths[identifier].is_dir() else 'missing'
            evidence.extend(timing.evidence)
            capabilities.extend(timing.capabilities if media_ready else ())
        elif identifier == 'music_dsp':
            from .music_dsp_runtime import status as dsp_status
            processing = await run_owned_thread(dsp_status, env)
            state = 'ready' if processing.ready and media_ready else 'installed' if processing.ready else 'partial' if processing.installed else 'missing'
            evidence.extend(processing.evidence)
            capabilities.extend(processing.capabilities if media_ready else ())
        else:
            engine = env.paths[identifier]
            source = all((engine / marker).exists() for marker in definition.source_markers)
            python = engine_python(engine, env.platform)
            environment_ok = _environment_receipt(identifier, env)
            present = source or python.is_file() or engine.is_dir()
            state = 'installed' if source and environment_ok else 'partial' if present else 'missing'
            evidence.extend((_evidence('source_markers', 'Required source entrypoints exist.' if source else 'Required source entrypoints are missing.', source),
                _evidence('environment_verified', 'A setup verification receipt matches this environment.' if environment_ok else 'Environment packages have not been verified for this checkout.', environment_ok),
                _evidence('inference_unverified', 'Runtime checks do not measure generated quality; review a representative preview before relying on output.')))
            if source and environment_ok:
                capabilities.append('verified_environment')
            if identifier == 'yue2' and native_installed:
                state = 'installed'
                native_models = native_setup_present(env)
                evidence.append(_evidence('native_model_files', 'Required native model files passed bounded format checks.' if native_models else 'Required native models are missing. Automatic setup can install them when model downloads are included.', native_models))
            runtime = runtimes.get(identifier)
            if runtime is not None:
                evidence.extend(runtime.evidence)
                capabilities.extend(runtime.capabilities)
                if runtime.ready:
                    state = 'ready'
                elif runtime.installed:
                    state = 'installed'
            if identifier in AUTOMATIC_MUSIC_MODELS:
                files = music_models_present(env, identifier)
                evidence.append(_evidence('local_model_files', 'Required local model files passed bounded structural checks; generation remains unverified.' if files else 'Required local model files are missing or incomplete. Setup can finish the reviewed model files.', files))
                if state == 'installed' and not files:
                    state = 'partial'
            if identifier == 'yue2' and native_installed and state == 'installed' and not native_setup_present(env):
                state = 'partial'
        prerequisites = env.uv is not None and (identifier == 'separation' or shutil.which('git') is not None)
        existing_music = identifier in AUTOMATIC_MUSIC_MODELS and engine_python(env.paths[identifier], env.platform).is_file() and all((env.paths[identifier] / marker).exists() for marker in definition.source_markers)
        automatic = support and (existing_music or identifier == 'yue2' and native_installed or managed and (identifier in ('speaker_review', 'music_dsp', 'music_beats') or (identifier in ('media', 'yue2') and platform_key(env) in ('win32-x64', 'darwin-arm64')) or identifier in ('ace_step', 'separation', 'video', 'speech', 'singing', 'kokoro', 'chatterbox', 'wan22', 'rvc', 'stable_audio3') and prerequisites))
        automation: Literal['unsupported', 'automatic', 'manual'] = 'unsupported' if not support else 'automatic' if automatic else 'manual'
        actions = [] if state == 'ready' else [_manual(definition)]
        if automatic and managed and state != 'ready':
            actions.insert(0, ModuleAction(kind='install', label='Install managed components', detail='Review the fixed dependency plan before starting downloads. Existing external checkouts are preserved.'))
        if support and managed and identifier in ('ace_step', 'separation', 'video', 'speech', 'singing', 'kokoro', 'chatterbox', 'wan22', 'rvc', 'stable_audio3') and not prerequisites:
            missing = 'uv' if env.uv is None else 'Git'
            evidence.append(_evidence('installer_prerequisite', f'{missing} is required before automatic setup.'))
            actions.insert(0, ModuleAction(kind='manual', label=f'Install {missing}', detail=f'Install {missing} and restart the app so its executable is available. Review the updated plan before source or model downloads.', url='https://docs.astral.sh/uv/getting-started/installation/' if missing == 'uv' else 'https://git-scm.com/downloads'))
        if identifier not in ('media', 'ebooks', 'source_import', 'speaker_review', 'music_dsp', 'music_beats') and state in ('installed', 'partial'):
            actions.append(ModuleAction(kind='verify', label='Verify a short preview', detail='Review the selected model and a short output in its workspace; setup does not claim GPU quality or performance.'))
        if not managed and identifier not in ('ebooks', 'source_import', 'transcription'):
            evidence.append(_evidence('external_installation', 'Configured external installation is preserved; automatic replacement is disabled.', True))
        modules.append(ModuleInfo(id=identifier, name=definition.name, description=definition.description,
            state=state, supported=support, managed=managed, automation=automation,
            dependencies=list(definition.dependencies), capabilities=capabilities, evidence=evidence,
            actions=actions, estimated_download_bytes=definition.download_bytes, licenses=declarations(identifier),
            can_reinstall=support and managed and automatic and identifier in REINSTALLABLE_MODULES and reinstall_available(env, identifier)))
    free = available_bytes(env.root)
    acceleration: Literal['apple_silicon', 'nvidia_unverified', 'cpu', 'unknown'] = 'apple_silicon' if platform_key(env) == 'darwin-arm64' else 'nvidia_unverified' if shutil.which('nvidia-smi') else 'cpu'
    return ModuleInventory(platform=env.platform, architecture=env.architecture, acceleration=acceleration,
        managed_root=str(env.root), free_bytes=free, checked_at=now(), modules=modules,
        recommended_features=recommend_features(modules))


def expanded_features(features: list[ModuleId]) -> list[ModuleId]:
    result: list[ModuleId] = []
    def include(identifier: ModuleId) -> None:
        for dependency in DEFINITIONS[identifier].dependencies:
            include(dependency)
        if identifier not in result:
            result.append(identifier)
    for feature in features:
        include(feature)
    return result


def approval_scope(request: ModulePlanRequest, environment: ModuleEnvironment) -> str:
    """Persist the immutable catalog/platform/destination scope separately from status."""
    from .module_install import manifest_path, SOURCE_PINS
    from .module_native_setup import native_model_manifest_hash
    manifest = manifest_path()
    artifact_digest = hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.is_file() and manifest.stat().st_size <= 200000 else 'unavailable'
    music_manifest = Path(__file__).parent / 'assets/music-models.json'
    music_digest = hashlib.sha256(music_manifest.read_bytes()).hexdigest() if music_manifest.is_file() and music_manifest.stat().st_size <= 200000 else 'unavailable'
    canonical = {'catalog': CATALOG_VERSION, 'platform': platform_key(environment), 'root': str(environment.root),
        'features': request.features, 'download_models': request.download_models, 'manifest': artifact_digest,
        'music_manifest': music_digest,
        'native_model_adapter': native_model_manifest_hash(),
        'ace_models': {name: os.environ.get(name, default).strip() or default for name, default in (('ACE_STEP_DIT_MODEL', 'acestep-v15-turbo'), ('ACE_STEP_LM_MODEL', 'acestep-5Hz-lm-1.7B'))} if 'ace_step' in request.features else {},
        'source_pins': {identifier: pin.commit for identifier, pin in SOURCE_PINS.items()},
        'licenses': {identifier: [declaration.model_dump(mode='json') for declaration in declarations(identifier)]
            for identifier in expanded_features(request.features)},
        'paths': {identifier: str(environment.paths[identifier].resolve()) for identifier in expanded_features(request.features)}}
    if request.reinstall:
        canonical['reinstall'] = request.reinstall
    if request.stable_training_variant is not None:
        canonical['stable_training_variant'] = request.stable_training_variant
    if request.stable_generation_variant is not None:
        canonical['stable_generation_variant'] = request.stable_generation_variant
    if request.stable_conditioning_variant is not None:
        canonical['stable_conditioning_variant'] = request.stable_conditioning_variant
    if request.ace_generation_model is not None:
        canonical['ace_generation_model'] = request.ace_generation_model
    if request.music_training_engine is not None:
        from .music_training_setup import recipe_hash, training_license_declarations
        canonical['music_training_engine'] = request.music_training_engine
        canonical['music_training_recipe'] = recipe_hash(request.music_training_engine)
        canonical['music_training_licenses'] = [declaration.model_dump(mode='json') for declaration in training_license_declarations(request.music_training_engine)]
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _download_bytes(identifier: ModuleId, environment: ModuleEnvironment, download_models: bool) -> int | None:
    """Only immutable manifest artifacts have verified byte counts."""
    from .module_install import load_manifest
    assets = load_manifest()
    key = platform_key(environment)
    if identifier == 'music_dsp':
        from .music_dsp_runtime import BINARY_ARCHIVE, SOURCE_ARCHIVE
        return BINARY_ARCHIVE.bytes + SOURCE_ARCHIVE.bytes
    if identifier == 'music_beats':
        from .music_beats_runtime import BeatRuntimeError, WEIGHTS_BYTES, identity
        try:
            identity(environment.paths[identifier], require_weights=False)
        except BeatRuntimeError:
            return None
        if not download_models:
            return 0
        try:
            identity(environment.paths[identifier])
        except BeatRuntimeError:
            return WEIGHTS_BYTES
        return 0
    if identifier in AUTOMATIC_MUSIC_MODELS:
        from .module_music_models import model_download_bytes
        return model_download_bytes(environment, identifier) if download_models else 0
    if identifier == 'media':
        media = assets.ffmpeg.assets.get(key)
        return media.bytes + (media.ffprobe.bytes if media.ffprobe is not None else 0) if media is not None else None
    if identifier == 'yue2' and not download_models:
        native = assets.engine.assets.get(key)
        return sum(file.bytes for file in native.files) if native is not None else None
    if identifier == 'yue2':
        from .module_install import missing_native_score_files
        from .module_native_setup import native_setup_present
        if not native_setup_present(environment):
            return None
        artifact = assets.engine.assets.get(key)
        return sum(file.bytes for file in artifact.files) if artifact is not None and missing_native_score_files(environment) else 0
    return None


async def plan(request: ModulePlanRequest, environment: ModuleEnvironment | None = None) -> ModulePlan:
    from .module_jobs import ModuleSetupError
    from .module_install import native_tools_need_repair
    from .module_native_setup import native_environment_present, native_setup_present
    env = environment or configured_environment()
    if request.ace_generation_model is not None:
        from .module_music_models import plan_ace_conditioning
        return plan_ace_conditioning(request, env)
    if request.music_training_engine is not None:
        from .music_training_setup import plan as music_training_plan
        return music_training_plan(request, env)
    if request.stable_training_variant is not None or request.stable_generation_variant is not None or request.stable_conditioning_variant is not None:
        from .stable_lora_setup import plan as training_plan
        return training_plan(request, env)
    status = await inventory(env)
    by_id = {module.id: module for module in status.modules}
    if any(not by_id[identifier].can_reinstall for identifier in request.reinstall):
        raise ModuleSetupError('reinstall_unavailable')
    steps: list[ModulePlanStep] = []
    warnings = ['Installation verifies components, not generated quality or platform GPU performance.']
    for identifier in expanded_features(request.features):
        module = by_id[identifier]
        definition = DEFINITIONS[identifier]
        existing_verifiable = (
            identifier != 'speaker_review' and bool(definition.packages)
            and (not module.managed or 'verified_environment' in module.capabilities)
            and engine_python(env.paths[identifier], env.platform).is_file()
            and all((env.paths[identifier] / marker).exists() for marker in definition.source_markers)
        ) or identifier == 'yue2' and native_environment_present(env)
        operation: Literal['unsupported', 'verify', 'reuse', 'install', 'manual']
        if not module.supported:
            operation = 'unsupported'
        elif identifier in request.reinstall:
            operation = 'install'
        elif identifier == 'yue2' and (native_tools_need_repair(env) or request.download_models
                and (existing_verifiable or module.state in ('installed', 'ready')) and not native_setup_present(env)):
            operation = 'verify'
        elif identifier == 'music_beats' and module.state == 'partial':
            operation = 'verify'
        elif module.state in ('installed', 'ready'):
            operation = 'reuse'
        elif existing_verifiable:
            operation = 'verify'
        else:
            operation = 'install' if module.automation == 'automatic' else 'manual'
        size = _download_bytes(identifier, env, request.download_models) if operation == 'install' or operation == 'verify' and (identifier in AUTOMATIC_MUSIC_MODELS or identifier == 'music_beats' or identifier == 'yue2' and request.download_models) else 0
        detail = 'Already installed. Reuse existing components without running their installer.' if operation == 'reuse' else 'Reinstall isolated Python dependencies. Existing source, model files and user data are preserved.' if identifier in request.reinstall else DEFINITIONS[identifier].guidance
        steps.append(ModulePlanStep(module_id=identifier, name=module.name, operation=operation,
            estimated_download_bytes=size, detail=detail, actions=[] if operation == 'reuse' else module.actions, licenses=module.licenses))
        if operation == 'manual':
            warnings.append(f'{module.name} requires a visible manual step; setup cannot complete it automatically.')
    if not request.download_models:
        warnings.append('Model downloads are not selected. Engines can remain partial until their required weights are installed and verified.')
    known = sum(step.estimated_download_bytes or 0 for step in steps)
    unknown = any(step.estimated_download_bytes is None for step in steps if step.operation in ('install', 'verify')) or any(step.operation == 'install' and step.module_id in AUTOMATIC_MUSIC_MODELS for step in steps)
    required = known + known // 2
    required += sum(128 * 1024**2 for step in steps if step.operation == 'install' and step.module_id == 'music_dsp')
    required += sum((step.estimated_download_bytes or 0) // 2 for step in steps if step.module_id in AUTOMATIC_MUSIC_MODELS)
    required += sum(8 * 1024**3 for step in steps if step.operation == 'install' and step.module_id in AUTOMATIC_MUSIC_MODELS and step.estimated_download_bytes is not None)
    if unknown:
        # Admission floor, not an invented download estimate or sufficiency guarantee.
        required += sum((24 if request.download_models and step.module_id not in DEPENDENCY_ONLY_MODULES and step.module_id != 'music_beats' else 8) * 1024**3 for step in steps if step.operation in ('install', 'verify') and step.estimated_download_bytes is None)
        warnings.append('Some dependency download sizes are unknown. The free-space check includes an 8 GiB environment allowance, or a 24 GiB minimum for unmeasured model installs. Known music downloads include cache and installation space; actual requirements may be larger.')
    destinations = {env.root}
    for step in steps:
        if step.operation == 'install':
            destinations.add(env.paths[step.module_id])
        if request.download_models and step.operation in ('install', 'verify'):
            if step.module_id in AUTOMATIC_MUSIC_MODELS:
                from .module_music_models import model_directories
                destinations.update(model_directories(env, step.module_id))
            elif step.module_id == 'yue2':
                from .module_native_setup import PACKAGES
                destinations.update(env.paths['yue2'] / 'models' / package.directory for package in PACKAGES)
    spaces = [available_bytes(path) for path in destinations]
    free = min(space for space in spaces if space is not None) if all(space is not None for space in spaces) else None
    enough = free is not None and free >= required
    if not enough:
        warnings.append('The selected plan has insufficient or unverified free disk space in a cache or installation location.')
    if any(not path.resolve().is_relative_to(env.root.resolve()) for path in destinations):
        warnings.append('The free-space check uses the lowest available space across cache and model installation locations, including external drives.')
    canonical = {'scope': approval_scope(request, env), 'catalog': CATALOG_VERSION, 'platform': platform_key(env), 'root': str(env.root),
        'features': request.features, 'download_models': request.download_models,
        'steps': [step.model_dump(mode='json') for step in steps],
        'paths': {identifier: str(env.paths[identifier].resolve()) for identifier in expanded_features(request.features)}}
    token = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    actionable = any(step.operation not in ('unsupported', 'reuse') for step in steps)
    if not actionable and any(step.operation == 'reuse' for step in steps):
        warnings.append('Selected components are already installed. No installation is needed; dependency reinstallation requires an explicit selection.')
    if request.reinstall:
        warnings.append('Dependency reinstallation replaces packages in the selected isolated environments and may download packages again. Existing source, models, datasets, checkpoints and configuration are preserved.')
    return ModulePlan(features=request.features, download_models=request.download_models, reinstall=request.reinstall, plan_token=token,
        steps=steps, estimated_download_bytes=known, download_size_unknown=unknown, required_free_bytes=required,
        free_bytes=free, can_install=enough and actionable, warnings=warnings)
