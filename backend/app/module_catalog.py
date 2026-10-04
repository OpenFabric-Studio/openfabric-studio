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
from collections.abc import Mapping
from typing import Literal

from .job_lifecycle import kill_process_tree, spawn_process
from .module_contracts import (
    ModuleAction, ModuleEvidence, ModuleId, ModuleInfo, ModuleInventory,
    ModulePlan, ModulePlanRequest, ModulePlanStep, ModuleState,
)
from .module_evidence import environment_verified
from .module_runtime import configured_targets, RuntimeEvidence, runtime_status

CATALOG_VERSION = 'openfabric-modules-1'
MODULE_IDS: tuple[ModuleId, ...] = ('ace_step', 'yue2', 'speech', 'singing', 'separation', 'video', 'media', 'transcription', 'source_import', 'ebooks', 'kokoro', 'chatterbox', 'wan22', 'rvc')
ENGINE_FOLDERS: Mapping[ModuleId, str] = {
    'ace_step': 'ACE-Step-1.5', 'yue2': 'YuE2', 'speech': 'gpt-sovits', 'singing': 'seed-vc',
    'separation': 'Demucs', 'video': 'ltx-2-mlx', 'media': 'ffmpeg', 'transcription': 'whisper.cpp',
    'source_import': 'reference-tools', 'ebooks': 'calibre',
    'kokoro': 'kokoro', 'chatterbox': 'chatterbox', 'wan22': 'mlx-video', 'rvc': 'rvc',
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
    'ace_step': ModuleDefinition('ace_step', 'ACE-Step', 'Music generation, covers and section edits.', ('media',), ('acestep/api_server.py', 'pyproject.toml'), ('torch', 'fastapi'), None, 'Install the pinned ACE-Step source/environment. Select model downloads explicitly, then verify a short generation with your hardware.', 'https://github.com/ace-step/ACE-Step-1.5'),
    'yue2': ModuleDefinition('yue2', 'YuE', 'Native music generation, melody planning and MIDI tools.', ('media',), ('tools/model_manager_v2.py',), (), None, 'Install the native engine and selected weights. Windows needs NVIDIA compute capability 7.5+ and driver 580+. Linux requires the documented CUDA source build.', 'https://github.com/0xShug0/audio.cpp'),
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
    'rvc': ModuleDefinition('rvc', 'RVC', 'Timbre conversion with RVC (MIT). On a Mac this is CPU, not GPU. Seed-VC singing stays separate.', ('media',), ('webui.py', 'infer'), ('torch', 'soundfile', 'librosa', 'faiss-cpu'), None, 'Clone the pinned RVC checkout with ./setup_rvc.sh or Settings and install CPU packages from PyPI. HuBERT, RMVPE, and trained voices are not downloaded. On a Mac this does not use the GPU. Seed-VC is unchanged.', 'https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI'),
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
        mapped = {identifier: resolved / ('tools' if identifier in ('media', 'transcription', 'source_import', 'ebooks') else 'engines') / ENGINE_FOLDERS[identifier] for identifier in MODULE_IDS}
        if paths is not None:
            mapped.update(paths)
        return cls(resolved, data_dir or resolved / 'data', platform or sys.platform,
                   architecture or host_platform.machine().lower(), mapped, Path(sys.executable),
                   shutil.which('uv'), mapped['media'] / 'bin', shutil.which('ebook-convert'))


def configured_environment() -> ModuleEnvironment:
    from . import config
    root = Path(os.environ.get('OPENFABRIC_MODULE_ROOT') or str(Path.home() / '.openfabric-studio' / 'runtime'))
    paths: Mapping[ModuleId, Path] = {
        'ace_step': config.ACE_STEP_DIR, 'yue2': config.YUE2_DIR, 'speech': config.GPT_SOVITS_DIR,
        'singing': config.SEED_VC_DIR, 'separation': config.DEMUCS_DIR, 'video': config.LTX_DIR,
        'kokoro': config.KOKORO_DIR, 'chatterbox': config.CHATTERBOX_DIR, 'wan22': config.WAN22_DIR,
        'rvc': config.RVC_DIR,
    }
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
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await spawn_process(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        if proc.stdout is None:
            return ProbeResult(False, '')
        output = bytearray()
        async with asyncio.timeout(timeout):
            while chunk := await proc.stdout.read(min(2048, max_bytes - len(output) + 1)):
                output.extend(chunk)
                if len(output) > max_bytes:
                    return ProbeResult(False, '')
            code = await proc.wait()
        return ProbeResult(code == 0, output.decode('utf-8', errors='replace').strip())
    except (OSError, TimeoutError):
        return ProbeResult(False, '')
    finally:
        if proc is not None:
            await kill_process_tree(proc)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _architecture(environment: ModuleEnvironment) -> str:
    return {'x86_64': 'x64', 'amd64': 'x64', 'aarch64': 'arm64'}.get(environment.architecture.lower(), environment.architecture.lower())


def platform_key(environment: ModuleEnvironment) -> str:
    return f'{environment.platform}-{_architecture(environment)}'


def supported(identifier: ModuleId, environment: ModuleEnvironment) -> bool:
    if identifier in ('video', 'wan22'):
        return platform_key(environment) == 'darwin-arm64'
    if identifier == 'singing':
        return platform_key(environment) in ('darwin-arm64', 'linux-x64', 'win32-x64')
    if identifier in ('ace_step', 'yue2', 'separation', 'speech', 'kokoro', 'chatterbox', 'rvc'):
        return platform_key(environment) in ('darwin-arm64', 'linux-x64', 'win32-x64')
    return environment.platform in ('win32', 'darwin', 'linux')


def is_managed(identifier: ModuleId, environment: ModuleEnvironment) -> bool:
    expected = environment.root / ('tools' if identifier in ('media', 'transcription', 'source_import', 'ebooks') else 'engines') / ENGINE_FOLDERS[identifier]
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


async def _runtime_inventory(environment: ModuleEnvironment) -> dict[ModuleId, RuntimeEvidence]:
    targets = configured_targets(environment)
    results = await asyncio.gather(*(runtime_status(environment, target) for target in targets))
    return {target.module_id: result for target, result in zip(targets, results)}


async def inventory(environment: ModuleEnvironment | None = None) -> ModuleInventory:
    env = environment or configured_environment()
    ffmpeg, ffprobe, calibre, runtimes = await asyncio.gather(
        probe([_tool('ffmpeg', env), '-version']), probe([_tool('ffprobe', env), '-version']),
        probe([env.calibre, '--version']) if env.calibre else asyncio.sleep(0, result=ProbeResult(False, '')),
        _runtime_inventory(env),
    )
    media_ready = ffmpeg.ok and ffmpeg.output.startswith('ffmpeg version ') and ffprobe.ok and ffprobe.output.startswith('ffprobe version ')
    calibre_ready = calibre.ok and re.search(r'ebook-convert\s+\(calibre\s+\d', calibre.output, re.IGNORECASE) is not None
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
            runtime = runtimes.get(identifier)
            if runtime is not None:
                evidence.extend(runtime.evidence)
                capabilities.extend(runtime.capabilities)
                if runtime.ready:
                    state = 'ready'
                elif runtime.installed:
                    state = 'installed'
        prerequisites = env.uv is not None and (identifier == 'separation' or shutil.which('git') is not None)
        automatic = support and managed and ((identifier in ('media', 'yue2') and platform_key(env) in ('win32-x64', 'darwin-arm64')) or identifier in ('ace_step', 'separation', 'video', 'speech', 'singing', 'kokoro', 'chatterbox', 'wan22', 'rvc') and prerequisites)
        automation: Literal['unsupported', 'automatic', 'manual'] = 'unsupported' if not support else 'automatic' if automatic else 'manual'
        actions = [] if state == 'ready' else [_manual(definition)]
        if automatic and state != 'ready':
            actions.insert(0, ModuleAction(kind='install', label='Install managed components', detail='Review the fixed dependency plan before starting downloads. Existing external checkouts are preserved.'))
        if support and managed and identifier in ('ace_step', 'separation', 'video', 'speech', 'singing', 'kokoro', 'chatterbox', 'wan22', 'rvc') and not prerequisites:
            missing = 'uv' if env.uv is None else 'Git'
            evidence.append(_evidence('installer_prerequisite', f'{missing} is required before automatic setup.'))
            actions.insert(0, ModuleAction(kind='manual', label=f'Install {missing}', detail=f'Install {missing} and restart the app so its executable is available. Review the updated plan before source or model downloads.', url='https://docs.astral.sh/uv/getting-started/installation/' if missing == 'uv' else 'https://git-scm.com/downloads'))
        if identifier not in ('media', 'ebooks', 'source_import') and state in ('installed', 'partial'):
            actions.append(ModuleAction(kind='verify', label='Verify a short preview', detail='Review the selected model and a short output in its workspace; setup does not claim GPU quality or performance.'))
        if not managed and identifier not in ('ebooks', 'source_import', 'transcription'):
            evidence.append(_evidence('external_installation', 'Configured external installation is preserved; automatic replacement is disabled.', True))
        modules.append(ModuleInfo(id=identifier, name=definition.name, description=definition.description,
            state=state, supported=support, managed=managed, automation=automation,
            dependencies=list(definition.dependencies), capabilities=capabilities, evidence=evidence,
            actions=actions, estimated_download_bytes=definition.download_bytes))
    ancestor = env.root
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    try:
        free: int | None = shutil.disk_usage(ancestor).free
    except OSError:
        free = None
    acceleration: Literal['apple_silicon', 'nvidia_unverified', 'cpu', 'unknown'] = 'apple_silicon' if platform_key(env) == 'darwin-arm64' else 'nvidia_unverified' if shutil.which('nvidia-smi') else 'cpu'
    return ModuleInventory(platform=env.platform, architecture=env.architecture, acceleration=acceleration,
        managed_root=str(env.root), free_bytes=free, checked_at=now(), modules=modules)


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
    manifest = manifest_path()
    artifact_digest = hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.is_file() and manifest.stat().st_size <= 200000 else 'unavailable'
    canonical = {'catalog': CATALOG_VERSION, 'platform': platform_key(environment), 'root': str(environment.root),
        'features': request.features, 'download_models': request.download_models, 'manifest': artifact_digest,
        'source_pins': {identifier: pin.commit for identifier, pin in SOURCE_PINS.items()},
        'paths': {identifier: str(environment.paths[identifier].resolve()) for identifier in expanded_features(request.features)}}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _download_bytes(identifier: ModuleId, environment: ModuleEnvironment, download_models: bool) -> int | None:
    """Only immutable manifest artifacts have verified byte counts."""
    from .module_install import load_manifest
    assets = load_manifest()
    key = platform_key(environment)
    if identifier == 'media':
        media = assets.ffmpeg.assets.get(key)
        return media.bytes + (media.ffprobe.bytes if media.ffprobe is not None else 0) if media is not None else None
    if identifier == 'yue2' and not download_models:
        native = assets.engine.assets.get(key)
        return sum(file.bytes for file in native.files) if native is not None else None
    return None


async def plan(request: ModulePlanRequest, environment: ModuleEnvironment | None = None) -> ModulePlan:
    env = environment or configured_environment()
    status = await inventory(env)
    by_id = {module.id: module for module in status.modules}
    steps: list[ModulePlanStep] = []
    warnings = ['Installation verifies components, not generated quality or platform GPU performance.']
    for identifier in expanded_features(request.features):
        module = by_id[identifier]
        definition = DEFINITIONS[identifier]
        external_verifiable = bool(definition.packages) and not module.managed and engine_python(env.paths[identifier], env.platform).is_file() and all((env.paths[identifier] / marker).exists() for marker in definition.source_markers)
        operation: Literal['unsupported', 'verify', 'install', 'manual'] = 'unsupported' if not module.supported else 'verify' if module.state == 'ready' or external_verifiable else 'install' if module.automation == 'automatic' else 'manual'
        size = _download_bytes(identifier, env, request.download_models) if operation == 'install' else 0
        steps.append(ModulePlanStep(module_id=identifier, name=module.name, operation=operation,
            estimated_download_bytes=size, detail=DEFINITIONS[identifier].guidance, actions=module.actions))
        if operation == 'manual':
            warnings.append(f'{module.name} requires a visible manual step; setup cannot complete it automatically.')
    if not request.download_models:
        warnings.append('Model downloads are not selected. Engines can remain partial until their required weights are installed and verified.')
    known = sum(step.estimated_download_bytes or 0 for step in steps)
    unknown = any(step.estimated_download_bytes is None for step in steps if step.operation == 'install')
    required = known + known // 2
    if unknown:
        # Admission floor, not an invented download estimate or sufficiency guarantee.
        required += sum((24 if request.download_models else 8) * 1024**3 for step in steps if step.operation == 'install' and step.estimated_download_bytes is None)
        warnings.append('Dependency/model download sizes are unknown. The disk check applies an 8 GiB minimum per unmeasured engine, or 24 GiB with models selected; actual requirements may be larger.')
    enough = status.free_bytes is not None and status.free_bytes >= required
    if not enough:
        warnings.append('The selected plan has insufficient or unverified free disk space.')
    canonical = {'scope': approval_scope(request, env), 'catalog': CATALOG_VERSION, 'platform': platform_key(env), 'root': str(env.root),
        'features': request.features, 'download_models': request.download_models,
        'steps': [step.model_dump(mode='json') for step in steps],
        'paths': {identifier: str(env.paths[identifier].resolve()) for identifier in expanded_features(request.features)}}
    token = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return ModulePlan(features=request.features, download_models=request.download_models, plan_token=token,
        steps=steps, estimated_download_bytes=known, download_size_unknown=unknown, required_free_bytes=required,
        free_bytes=status.free_bytes, can_install=enough and any(step.operation != 'unsupported' for step in steps), warnings=warnings)
