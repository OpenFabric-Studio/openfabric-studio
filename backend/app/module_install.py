"""Fixed installer adapters. Targets, source revisions and commands are server-owned.

Only owned managed installations can be resumed. Unowned/external source trees
are never reset, removed, patched or resynchronised. Model inference is separate
from installing and checking an environment.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
from typing import Literal
import zipfile

import httpx
from pydantic import BaseModel, Field, ValidationError

from .atomic_files import write_object
from .contracts import JsonValue
from .module_catalog import engine_python, is_managed, MODULE_IDS, platform_key, supported
from .module_contracts import ModuleId, ModuleDownloadProgress
from .module_evidence import environment_fingerprint
from .module_jobs import InstallContext, InstallOutcome, ModuleSetupError
from .job_lifecycle import await_cleanup


class DownloadArtifact(BaseModel):
    url: str = Field(pattern=r'^https://', max_length=1000)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    bytes: int = Field(ge=1, le=100_000_000_000)


class FFmpegArtifact(DownloadArtifact):
    kind: Literal['binary', 'archive'] = 'archive'
    binDir: str | None = None
    ffprobe: DownloadArtifact | None = None


class FFmpegManifest(BaseModel):
    assets: dict[str, FFmpegArtifact]


class EngineArtifact(BaseModel):
    preset: str = Field(pattern=r'^[a-z0-9-]+$')
    files: list[DownloadArtifact] = Field(min_length=1, max_length=5)


class EngineManifest(BaseModel):
    tag: str
    assets: dict[str, EngineArtifact]


class AceManifest(BaseModel):
    commit: str = Field(pattern=r'^[0-9a-f]{40}$')


class WeightManifest(BaseModel):
    packages: list[str] = Field(max_length=20)


class AssetManifest(BaseModel):
    ffmpeg: FFmpegManifest
    engine: EngineManifest
    aceStep: AceManifest
    weights: WeightManifest


def manifest_path() -> Path:
    packaged = Path(__file__).parent / 'module_assets.json'
    return packaged if packaged.is_file() else Path(__file__).resolve().parents[2] / 'desktop/manifest.json'


def load_manifest() -> AssetManifest:
    path = manifest_path()
    if path.stat().st_size > 200_000:
        raise ModuleSetupError('artifact_integrity')
    return AssetManifest.model_validate_json(path.read_bytes())


@dataclass(frozen=True)
class SourcePin:
    repository: str
    commit: str
    python: str


SOURCE_PINS: dict[ModuleId, SourcePin] = {
    'speech': SourcePin('https://github.com/RVC-Boss/GPT-SoVITS.git', '48b1a0169a28582a8984402f82cf438d3bfa6aca', '3.11'),
    'singing': SourcePin('https://github.com/Plachtaa/seed-vc.git', '51383efd921027683c89e5348211d93ff12ac2a8', '3.12'),
    'video': SourcePin('https://github.com/dgrauet/ltx-2-mlx.git', '1724ca673d59f023a8a95efee06e5d36d61c2765', '3.12'),
    # Pins reviewed 4 Oct 2026. Setup clones these commits and does not fetch weights.
    'kokoro': SourcePin('https://github.com/hexgrad/kokoro.git', 'dfb907a02bba8152ca444717ca5d78747ccb4bec', '3.11'),
    'chatterbox': SourcePin('https://github.com/resemble-ai/chatterbox.git', '5de7a54aa4e5e2baadb0182dde554908b48b85c2', '3.11'),
    'wan22': SourcePin('https://github.com/Blaizzy/mlx-video.git', '87db56a51758fefb748a359b90a5283bb8ba4837', '3.12'),
    'rvc': SourcePin('https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI.git', '81eed5e8f68b6bed1789f682fe78cdd324495afc', '3.12'),
}


def venv_python(root: Path, platform: str) -> Path:
    return engine_python(root, platform)


def _sha256(path: Path) -> str:
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_STALL_SECONDS = 45.0
_TRANSIENT_HTTP = frozenset({408, 429, 500, 502, 503, 504})


class _IncompleteDownload(Exception):
    pass


def _retry_delay(attempt: int, response: httpx.Response | None) -> float:
    value = response.headers.get('retry-after', '') if response is not None else ''
    return min(30.0, float(value)) if value.isascii() and value.isdigit() and len(value) < 10 else float(2 ** (attempt - 1))


def _download_progress(artifact: DownloadArtifact, received: int, attempt: int,
                       phase: Literal['downloading', 'retrying', 'verifying', 'complete'],
                       callback: Callable[[ModuleDownloadProgress], None] | None, delay: float | None = None) -> None:
    if callback is not None:
        callback(ModuleDownloadProgress(artifact_name=Path(artifact.url).name[-120:] or 'Pinned asset',
            bytes_received=min(received, artifact.bytes), total_bytes=artifact.bytes, attempt=attempt,
            max_attempts=DOWNLOAD_ATTEMPTS, phase=phase, retry_after_sec=delay))


async def _download_once(artifact: DownloadArtifact, partial: Path, client: httpx.AsyncClient, attempt: int,
                         callback: Callable[[ModuleDownloadProgress], None] | None) -> None:
    offset = partial.stat().st_size if partial.is_file() else 0
    if offset >= artifact.bytes:
        if offset == artifact.bytes and await await_cleanup(asyncio.to_thread(_sha256, partial)) == artifact.sha256:
            return
        partial.unlink()
        offset = 0
    _download_progress(artifact, offset, attempt, 'downloading', callback)
    headers = {'Range': f'bytes={offset}-', 'Accept-Encoding': 'identity'} if offset else {'Accept-Encoding': 'identity'}
    async with client.stream('GET', artifact.url, headers=headers) as response:
        response.raise_for_status()
        append = response.status_code == 206
        length = response.headers.get('content-length')
        if response.headers.get('content-encoding', 'identity').lower() != 'identity':
            raise ModuleSetupError('artifact_integrity')
        expected = artifact.bytes
        if append:
            match = re.fullmatch(r'bytes ([0-9]+)-([0-9]+)/([0-9]+)', response.headers.get('content-range', ''))
            if match is None:
                raise ModuleSetupError('artifact_integrity')
            start, end, total = (int(value) for value in match.groups())
            if start != offset or total != artifact.bytes or end != artifact.bytes - 1:
                raise ModuleSetupError('artifact_integrity')
            expected -= offset
        elif response.status_code != 200:
            raise ModuleSetupError('artifact_integrity')
        if length is not None and (not length.isascii() or not length.isdigit() or int(length) > expected or append and int(length) != expected):
            raise ModuleSetupError('artifact_integrity')
        written = offset if append else 0
        with partial.open('ab' if append else 'wb') as output:
            chunks = response.aiter_bytes(65536).__aiter__()
            while True:
                try:
                    async with asyncio.timeout(DOWNLOAD_STALL_SECONDS):
                        chunk = await anext(chunks)
                except StopAsyncIteration:
                    break
                written += len(chunk)
                if written > artifact.bytes:
                    raise ModuleSetupError('artifact_integrity')
                output.write(chunk)
                _download_progress(artifact, written, attempt, 'downloading', callback)
            output.flush()
            os.fsync(output.fileno())
        if written != artifact.bytes:
            raise _IncompleteDownload()


async def download_artifact(artifact: DownloadArtifact, destination: Path, context: InstallContext,
                            *, client: httpx.AsyncClient | None = None,
                            on_progress: Callable[[ModuleDownloadProgress], None] | None = None) -> Path:
    context.confined(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size == artifact.bytes and await await_cleanup(asyncio.to_thread(_sha256, destination)) == artifact.sha256:
        return destination
    partial = context.confined(destination.with_name(destination.name + '.part'))
    if client is None:
        async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(45, connect=15)) as owned:
            return await download_artifact(artifact, destination, context, client=owned, on_progress=on_progress)
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            await _download_once(artifact, partial, client, attempt, on_progress)
            break
        except (httpx.TransportError, httpx.HTTPStatusError, TimeoutError, _IncompleteDownload) as error:
            if isinstance(error, httpx.HTTPStatusError) and error.response.status_code not in _TRANSIENT_HTTP:
                raise ModuleSetupError('download_failed') from error
            if attempt == DOWNLOAD_ATTEMPTS:
                raise ModuleSetupError('download_stalled' if isinstance(error, (TimeoutError, httpx.TimeoutException)) else 'download_failed') from error
            delay = _retry_delay(attempt, error.response if isinstance(error, httpx.HTTPStatusError) else None)
            _download_progress(artifact, partial.stat().st_size if partial.is_file() else 0, attempt, 'retrying', on_progress, delay)
            await asyncio.sleep(delay)
    _download_progress(artifact, partial.stat().st_size, attempt, 'verifying', on_progress)
    if partial.stat().st_size != artifact.bytes or await await_cleanup(asyncio.to_thread(_sha256, partial)) != artifact.sha256:
        partial.unlink(missing_ok=True)
        raise ModuleSetupError('artifact_integrity')
    partial.replace(destination)
    _download_progress(artifact, artifact.bytes, attempt, 'complete', on_progress)
    return destination


def _archive_path(name: str, root: Path) -> Path:
    relative = PurePosixPath(name.replace('\\', '/'))
    if relative.is_absolute() or '..' in relative.parts or not relative.parts or ':' in relative.parts[0]:
        raise ModuleSetupError('archive_unsafe')
    candidate = root.joinpath(*relative.parts)
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ModuleSetupError('archive_unsafe')
    return candidate


def extract_archive(archive: Path, destination: Path) -> None:
    """Validate every member before writing; reject links, devices and bombs."""
    if destination.exists() and any(destination.iterdir()):
        raise ModuleSetupError('installation_conflict')
    if destination.is_symlink():
        raise ModuleSetupError('archive_unsafe')
    destination.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            members = source.infolist()
            if len(members) > 25000 or sum(member.file_size for member in members) > 8 * 1024**3:
                raise ModuleSetupError('archive_unsafe')
            for member in members:
                _archive_path(member.filename, destination)
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                    raise ModuleSetupError('archive_unsafe')
            for member in members:
                target = _archive_path(member.filename, destination)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(member) as reader, target.open('xb') as writer:
                        shutil.copyfileobj(reader, writer)
    else:
        with tarfile.open(archive, 'r:*') as source:
            tar_members = source.getmembers()
            if len(tar_members) > 25000 or sum(tar_member.size for tar_member in tar_members) > 8 * 1024**3:
                raise ModuleSetupError('archive_unsafe')
            for tar_member in tar_members:
                _archive_path(tar_member.name, destination)
                if not (tar_member.isfile() or tar_member.isdir()):
                    raise ModuleSetupError('archive_unsafe')
            for tar_member in tar_members:
                target = _archive_path(tar_member.name, destination)
                if tar_member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    tar_reader = source.extractfile(tar_member)
                    if tar_reader is None:
                        raise ModuleSetupError('archive_unsafe')
                    with tar_reader, target.open('xb') as writer:
                        shutil.copyfileobj(tar_reader, writer)
                    target.chmod(tar_member.mode & 0o777)


class OwnedInstallation(BaseModel):
    module_id: ModuleId
    root: str
    version: str
    files: dict[str, str]


def _owned_path(context: InstallContext, identifier: ModuleId) -> Path:
    return context.confined(context.environment.root / '.setup' / 'owned' / f'{identifier}.json')


def _owned(context: InstallContext, identifier: ModuleId, target: Path, version: str) -> bool:
    marker = _owned_path(context, identifier)
    try:
        if marker.stat().st_size > 16384:
            return False
        owned = OwnedInstallation.model_validate_json(marker.read_bytes())
        if owned.module_id != identifier or owned.root != str(target.resolve()) or owned.version != version:
            return False
        for relative, digest in owned.files.items():
            path = _archive_path(relative, target)
            if not path.is_file() or _sha256(path) != digest:
                return False
        return bool(owned.files)
    except (OSError, ValueError, ValidationError, ModuleSetupError):
        return False


def _record_owned(context: InstallContext, identifier: ModuleId, target: Path, version: str) -> None:
    from .module_catalog import DEFINITIONS
    markers = DEFINITIONS[identifier].source_markers
    if identifier == 'media':
        markers = ('bin/ffmpeg.exe', 'bin/ffprobe.exe') if context.environment.platform == 'win32' else ('bin/ffmpeg', 'bin/ffprobe')
    elif identifier == 'yue2':
        markers = ('tools/model_manager_v2.py',)
    files: dict[str, JsonValue] = {relative: _sha256(target / relative) for relative in markers if (target / relative).is_file()}
    marker = _owned_path(context, identifier)
    marker.parent.mkdir(parents=True, exist_ok=True)
    write_object(marker, {'module_id': identifier, 'root': str(target.resolve()), 'version': version, 'files': files})


def _uv_env(context: InstallContext) -> dict[str, str]:
    root = context.environment.root
    return {**os.environ, 'UV_CACHE_DIR': str(root / 'cache/uv'), 'UV_PYTHON_INSTALL_DIR': str(root / 'tools/python'),
        'UV_PYTHON_PREFERENCE': 'only-managed', 'HF_HOME': str(root / 'cache/huggingface'),
        'TORCH_HOME': str(root / 'cache/torch'), 'UV_NO_PROGRESS': '1', 'PYTHONUTF8': '1',
        'HF_HUB_DISABLE_TELEMETRY': '1', 'NO_COLOR': '1'}


async def _clone(context: InstallContext, identifier: ModuleId, pin: SourcePin, target: Path) -> None:
    staged = context.confined(context.workspace / f'{identifier}-source')
    if staged.exists():
        # Only this job's unpublished scratch source is reset, never a target.
        await await_cleanup(asyncio.to_thread(shutil.rmtree, staged))
    staged.mkdir(parents=True)
    await context.run(['git', '-C', str(staged), 'init'])
    await context.run(['git', '-C', str(staged), 'remote', 'add', 'origin', pin.repository])
    await context.run(['git', '-C', str(staged), 'fetch', '--depth', '1', 'origin', pin.commit])
    await context.run(['git', '-C', str(staged), 'checkout', '--detach', 'FETCH_HEAD'])
    if identifier == 'ace_step':
        patch = Path(__file__).resolve().parents[2] / 'external/patches/ace-step.patch'
        if not patch.is_file():
            # Packaged resources keep the baseline patch in the sibling directory.
            patch = Path(__file__).resolve().parents[2] / 'patches/ace-step.patch'
        await context.run(['git', 'apply', '--whitespace=nowarn', str(patch)], cwd=staged)
    elif identifier == 'singing':
        await context.run([str(context.environment.python), str(Path(__file__).parent / 'seed_vc_compat.py'), str(staged)])
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ModuleSetupError('installation_conflict')
    staged.rename(target)


async def _check_environment(context: InstallContext, identifier: ModuleId, target: Path) -> None:
    from .module_catalog import DEFINITIONS
    python = venv_python(target, context.environment.platform)
    code = 'import importlib.metadata as m;packages=' + repr(list(DEFINITIONS[identifier].packages)) + ';[m.version(name) for name in packages]'
    await context.run([str(python), '-I', '-c', code], timeout=30)
    # pip check validates declared dependency compatibility without GPU imports.
    uv = context.environment.uv
    command = [uv, 'pip', 'check', '--python', str(python)] if uv else [str(python), '-I', '-m', 'pip', 'check']
    await context.run(command, env=_uv_env(context), timeout=30)
    fingerprint = environment_fingerprint(context.environment, identifier)
    if fingerprint is None:
        raise ModuleSetupError('environment_unverified')
    marker = context.confined(context.environment.root / '.setup/verification' / f'{identifier}.json')
    marker.parent.mkdir(parents=True, exist_ok=True)
    write_object(marker, {'schema_version': 2, 'root': str(target.resolve()), 'fingerprint': fingerprint,
        'environment_verified': True})


async def verify(context: InstallContext, identifier: ModuleId) -> InstallOutcome:
    """Explicitly check existing environments without changing their contents."""
    from .module_catalog import DEFINITIONS, inventory
    status = await inventory(context.environment)
    module = next(item for item in status.modules if item.id == identifier)
    if module.state == 'ready':
        return InstallOutcome('verified', 'Current bounded tool or runtime/model checks passed. No engine files were changed.')
    if identifier == 'speaker_review':
        return _speaker_outcome(context)
    definition = DEFINITIONS[identifier]
    target = context.environment.paths[identifier]
    if definition.packages and all((target / marker).exists() for marker in definition.source_markers) and engine_python(target, context.environment.platform).is_file():
        await _check_environment(context, identifier, target)
        return InstallOutcome('manual', 'Existing environment packages and declared compatibility checked without changing the checkout. Model setup and a short output still require review.')
    raise ModuleSetupError('environment_unverified')


def _speaker_outcome(context: InstallContext) -> InstallOutcome:
    from .module_runtime import speaker_status
    status = speaker_status(context.environment)
    if not status.installed:
        raise ModuleSetupError('environment_unverified')
    if status.ready:
        return InstallOutcome('verified', 'Separate CPU dependencies and the reviewed explicitly configured local checkpoint are verified. Similarity still requires human review.')
    return InstallOutcome('manual', 'Separate CPU dependencies verified. No weights were downloaded. Select the reviewed local ECAPA checkpoint explicitly and configure OPENFABRIC_SPEAKER_REVIEW_PYTHON and OPENFABRIC_SPEAKER_REVIEW_WEIGHTS before speaker review.')


async def _media(context: InstallContext, target: Path, assets: AssetManifest) -> InstallOutcome:
    artifact = assets.ffmpeg.assets.get(platform_key(context.environment))
    if artifact is None:
        return InstallOutcome('manual', 'Install both ffmpeg and ffprobe with your operating-system package manager.')
    cached = context.environment.root / 'cache/downloads' / Path(artifact.url).name
    await download_artifact(artifact, cached, context, on_progress=context.update_download)
    staged = context.confined(context.workspace / 'media')
    if staged.exists():
        await await_cleanup(asyncio.to_thread(shutil.rmtree, staged))
    (staged / 'bin').mkdir(parents=True)
    suffix = '.exe' if context.environment.platform == 'win32' else ''
    if artifact.kind == 'binary':
        if artifact.ffprobe is None:
            return InstallOutcome('manual', 'This platform manifest has no pinned FFprobe; install a paired toolchain manually.')
        probe_cached = context.environment.root / 'cache/downloads' / Path(artifact.ffprobe.url).name
        await download_artifact(artifact.ffprobe, probe_cached, context, on_progress=context.update_download)
        shutil.copyfile(cached, staged / 'bin/ffmpeg')
        shutil.copyfile(probe_cached, staged / 'bin/ffprobe')
    else:
        extracted = context.confined(context.workspace / 'media-archive')
        if extracted.exists():
            await await_cleanup(asyncio.to_thread(shutil.rmtree, extracted))
        # Run archive work in an owned process; cancellation drains it before reuse.
        await context.run([str(context.environment.python), '-m', 'app.module_install', '--extract', str(cached), str(extracted)])
        for name in ('ffmpeg', 'ffprobe'):
            matches = list(extracted.rglob(name + suffix))
            if len(matches) != 1:
                raise ModuleSetupError('artifact_integrity')
            shutil.copyfile(matches[0], staged / 'bin' / (name + suffix))
    if context.environment.platform != 'win32':
        for file in (staged / 'bin').iterdir():
            file.chmod(0o755)
    from .module_catalog import probe
    for name in ('ffmpeg', 'ffprobe'):
        result = await probe([str(staged / 'bin' / (name + suffix)), '-version'])
        if not result.ok or not result.output.startswith(name + ' version '):
            raise ModuleSetupError('environment_unverified')
    target.parent.mkdir(parents=True, exist_ok=True)
    staged.rename(target)
    return InstallOutcome('verified', 'FFmpeg and FFprobe both passed bounded version checks.')


async def _native(context: InstallContext, target: Path, assets: AssetManifest, download: bool) -> InstallOutcome:
    artifact = assets.engine.assets.get(platform_key(context.environment))
    if artifact is None:
        return InstallOutcome('manual', 'Use the documented Linux source build and CUDA toolkit prerequisites.')
    staged = context.confined(context.workspace / 'native')
    if staged.exists():
        await await_cleanup(asyncio.to_thread(shutil.rmtree, staged))
    binary_dir = staged / 'build' / artifact.preset / 'bin'
    binary_dir.mkdir(parents=True)
    for index, file in enumerate(artifact.files):
        cached = context.environment.root / 'cache/downloads' / Path(file.url).name
        await download_artifact(file, cached, context, on_progress=context.update_download)
        extracted = context.confined(context.workspace / f'native-archive-{index}')
        if extracted.exists():
            await await_cleanup(asyncio.to_thread(shutil.rmtree, extracted))
        await context.run([str(context.environment.python), '-m', 'app.module_install', '--extract', str(cached), str(extracted)])
        for source in extracted.iterdir():
            destination = staged / source.name if source.name in ('tools', 'model_specs') else binary_dir / source.name
            if source.is_dir():
                shutil.copytree(source, destination, dirs_exist_ok=True)
            else:
                shutil.copyfile(source, destination)
    binary = binary_dir / ('audiocpp_server.exe' if context.environment.platform == 'win32' else 'audiocpp_server')
    if not binary.is_file() or not (staged / 'tools/model_manager_v2.py').is_file():
        raise ModuleSetupError('artifact_integrity')
    if context.environment.platform != 'win32':
        binary.chmod(0o755)
    target.parent.mkdir(parents=True, exist_ok=True)
    staged.rename(target)
    _record_owned(context, 'yue2', target, assets.engine.tag)
    return await _native_weights(context, target, assets, download)


async def _native_weights(context: InstallContext, target: Path, assets: AssetManifest, download: bool) -> InstallOutcome:
    if not download:
        return InstallOutcome('manual', 'Native engine installed. Review the plan with model downloads selected to install required weights.')
    if shutil.which('git') is None:
        return InstallOutcome('manual', 'Git is required to apply the verified resumable model downloader before installing weights.')
    patch_dir = Path(__file__).resolve().parents[2] / 'external/patches'
    if not (patch_dir / 'yue-model-resume.patch').is_file():
        patch_dir = Path(__file__).resolve().parents[2] / 'patches'
    reliability = patch_dir / 'yue-model-download-reliability.patch'
    try:
        # Check the final layer first: it changes old-patch context, so checking
        # the old layer alone is no longer a reliable installed-state probe.
        await context.run(['git', 'apply', '--reverse', '--check', str(reliability)], cwd=target)
    except ModuleSetupError as exc:
        if exc.code != 'installer_failed':
            raise
        patch = patch_dir / 'yue-model-resume.patch'
        try:
            await context.run(['git', 'apply', '--reverse', '--check', str(patch)], cwd=target)
        except ModuleSetupError as previous:
            if previous.code != 'installer_failed':
                raise
            await context.run(['git', 'apply', '--check', str(patch)], cwd=target)
            await context.run(['git', 'apply', '--whitespace=nowarn', str(patch)], cwd=target)
        await context.run(['git', 'apply', '--check', str(reliability)], cwd=target)
        await context.run(['git', 'apply', '--whitespace=nowarn', str(reliability)], cwd=target)
    # Refresh ownership before any awaited model download can be interrupted.
    _record_owned(context, 'yue2', target, assets.engine.tag)
    for package in assets.weights.packages:
        if package not in ('yue2_main_q8_0', 'yue2_main_q4_0', 'yue2_vae_f16', 'sheetsage2_orig', 'muscriptor_small_f32'):
            raise ModuleSetupError('artifact_integrity')
        await context.run([str(context.environment.python), 'tools/model_manager_v2.py', 'install', package, '--progress'], cwd=target, env=_uv_env(context), download_progress=True)
    return InstallOutcome('manual', 'Native packages downloaded by the verified model manager. Verify GPU compatibility and a short generation in Music before claiming readiness.')


async def install(context: InstallContext, identifier: ModuleId, download_models: bool) -> InstallOutcome:
    env = context.environment
    if identifier not in MODULE_IDS or not supported(identifier, env):
        raise ModuleSetupError('unsupported_platform')
    if not is_managed(identifier, env):
        return InstallOutcome('manual', 'The configured external installation is preserved. Review its setup outside the managed installer.')
    target = context.confined(env.paths[identifier])
    assets = load_manifest()
    version = 'speechbrain-1.0.3-torch-2.6.0-cpu-v1' if identifier == 'speaker_review' else assets.aceStep.commit if identifier == 'ace_step' else assets.engine.tag if identifier == 'yue2' else SOURCE_PINS[identifier].commit if identifier in SOURCE_PINS else hashlib.sha256(manifest_path().read_bytes()).hexdigest()
    owned = _owned(context, identifier, target, version)
    if target.exists() and not owned:
        return InstallOutcome('manual', 'An existing or modified installation was preserved. Use a separate managed root or review the existing setup manually.')
    if identifier == 'speaker_review':
        target.mkdir(parents=True, exist_ok=True)
        marker = context.confined(target / '.openfabric-speaker-runtime.json')
        write_object(marker, {'schema_version': 1, 'dependency_setup': version})
        # Claim only this app-created runtime before the awaited setup, so
        # cancelled dependency installation can be resumed without overwriting
        # an unrelated existing environment.
        _record_owned(context, identifier, target, version)
        await context.run([str(env.python), str(Path(__file__).resolve().parents[1] / 'scripts/setup_speaker_review.py'), '--runtime-root', str(target), '--python', str(env.python)], env=_uv_env(context))
        result = _speaker_outcome(context)
        _record_owned(context, identifier, target, version)
        return result
    if identifier == 'media':
        if owned:
            from .module_catalog import inventory
            ready = await inventory(env)
            media = next(module for module in ready.modules if module.id == 'media')
            return InstallOutcome('verified' if media.state == 'ready' else 'manual', 'Existing media tools were checked; repair requires reviewing the preserved installation.')
        result = await _media(context, target, assets)
        if target.is_dir():
            _record_owned(context, identifier, target, version)
        return result
    if identifier == 'yue2':
        result = await _native_weights(context, target, assets, download_models) if owned else await _native(context, target, assets, download_models)
        if target.is_dir():
            _record_owned(context, identifier, target, version)
        return result
    if identifier not in ('ace_step', 'singing', 'speech', 'separation', 'video', 'kokoro', 'chatterbox', 'wan22', 'rvc'):
        return InstallOutcome('manual', 'Complete this tool installation using the reviewed instructions.')
    if env.uv is None or identifier != 'separation' and shutil.which('git') is None:
        return InstallOutcome('manual', 'Managed setup requires uv and Git. Install these tools before resuming; source and weights have not been changed.')
    uv = env.uv
    if uv is None:
        raise ModuleSetupError('environment_unverified')
    if not owned:
        if identifier == 'separation':
            target.mkdir(parents=True)
            project = '[project]\nname="openfabric-demucs"\nversion="0.1.0"\nrequires-python=">=3.11,<3.13"\ndependencies=["demucs>=4.0.1","numpy>=1.26.4","torch>=2.11.0"]\n[tool.uv]\npackage=false\n'
            if env.platform != 'darwin' and shutil.which('nvidia-smi'):
                project += '[[tool.uv.index]]\nname="pytorch-cu128"\nurl="https://download.pytorch.org/whl/cu128"\nexplicit=true\n[tool.uv.sources]\ntorch={index="pytorch-cu128"}\n'
            (target / 'pyproject.toml').write_text(project, encoding='utf-8')
        else:
            pin = SourcePin('https://github.com/ace-step/ACE-Step-1.5.git', assets.aceStep.commit, '3.12') if identifier == 'ace_step' else SOURCE_PINS[identifier]
            await _clone(context, identifier, pin, target)
        _record_owned(context, identifier, target, version)
    child_env = _uv_env(context)
    if identifier in ('ace_step', 'separation'):
        command = [uv, 'sync', '--frozen'] if identifier == 'ace_step' or (target / 'uv.lock').exists() else [uv, 'sync']
        await context.run(command, cwd=target, env=child_env)
        if download_models:
            command = [uv, 'run', 'acestep-download'] if identifier == 'ace_step' else [uv, 'run', 'python', '-c', "from demucs.pretrained import get_model;get_model('htdemucs')"]
            await context.run(command, cwd=target, env=child_env)
    elif identifier == 'video':
        command = [str(env.python), str(Path(__file__).resolve().parents[1] / 'scripts/setup_video.py'), '--engine-dir', str(target), '--cache-dir', str(env.data_dir / 'models/ltx')]
        if download_models:
            command.append('--download-models')
        await context.run(command, env=child_env)
    elif identifier in ('kokoro', 'chatterbox', 'wan22', 'rvc'):
        from .optional_engines import install_command
        python = venv_python(target, env.platform)
        if not python.is_file():
            await context.run([uv, 'venv', '--python', SOURCE_PINS[identifier].python, str(target / '.venv')], env=child_env)
        # Editable or CPU package install only. Never pass a weight repo or from_pretrained.
        await context.run(install_command(uv, identifier, python, target), env=child_env)
    else:
        python = venv_python(target, env.platform)
        if not python.is_file():
            await context.run([uv, 'venv', '--python', SOURCE_PINS[identifier].python, str(target / '.venv')], env=child_env)
        if identifier == 'speech':
            await context.run([uv, 'pip', 'install', '--python', str(python), '-r', str(target / 'requirements.txt')], env=child_env)
        else:
            await context.run([uv, 'pip', 'install', '--python', str(python), 'torch', 'torchaudio', 'transformers==4.46.3', 'numpy<2', 'matplotlib>=3.8,<3.10', 'descript-audio-codec', 'scipy', 'librosa', 'pyyaml', 'munch', 'einops', 'huggingface_hub', 'soundfile', 'tqdm', 'pydantic>=2.13.5,<3'], env=child_env)
    await _check_environment(context, identifier, target)
    detail = 'Pinned source and environment checked. Model weights and a short inference preview remain to be reviewed.'
    if identifier == 'speech':
        detail += ' Complete upstream pretrained weights and start the loopback speech API manually.'
    elif identifier == 'singing':
        detail += ' CPU training is unsupported; weights remain a separate reviewed step.'
    elif identifier == 'kokoro':
        detail = 'Pinned Kokoro checkout checked. No voices were downloaded. Install espeak-ng yourself. Narration is POST /api/local-engines/kokoro after config.json, kokoro-v1_0.pth, and a preset voice file are in place. Kokoro does not clone a person and does not replace GPT-SoVITS. The runner uses PyTorch with PYTORCH_ENABLE_MPS_FALLBACK=1.'
    elif identifier == 'chatterbox':
        detail = 'Pinned Chatterbox checkout checked. Weights were not downloaded. Call POST /api/local-engines/chatterbox after the original or multilingual checkpoint files are in place. Turbo is refused. GPT-SoVITS is unchanged.'
    elif identifier == 'wan22':
        detail = 'Pinned mlx-video checkout checked. Wan 2.2 TI2V-5B weights were not downloaded or converted. Call POST /api/local-engines/wan with engine wan22 after the converted TI2V-5B folder is in place. 14B, S2V, and Animate are refused. Song videos stay on LTX.'
    elif identifier == 'rvc':
        detail = 'Pinned RVC checkout checked with CPU packages from PyPI. Call POST /api/local-engines/rvc after assets/hubert_base, assets/rmvpe/rmvpe.pt, and a trained .pth voice are in place. On a Mac this is CPU. Seed-VC is unchanged.'
    if identifier in ('kokoro', 'chatterbox', 'wan22', 'rvc') and download_models:
        detail += ' The download flag does not fetch these weights.'
    return InstallOutcome('manual', detail)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Owned internal archive extraction')
    parser.add_argument('--extract', nargs=2, type=Path, required=True)
    options = parser.parse_args()
    values: object = options.extract
    if not isinstance(values, list) or len(values) != 2 or not all(isinstance(value, Path) for value in values):
        raise SystemExit(2)
    source: object = values[0]
    destination: object = values[1]
    if isinstance(source, Path) and isinstance(destination, Path):
        extract_archive(source, destination)
