"""Check native music files and add missing weights without changing a checkout."""
from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile

from .contracts import JsonValue
from .job_lifecycle import await_cleanup
from .module_catalog import ModuleEnvironment, platform_key
from .module_jobs import InstallContext, InstallOutcome, ModuleSetupError


@dataclass(frozen=True)
class NativePackage:
    identifier: str
    directory: str
    repository: str
    precision: str
    files: tuple[str, ...]
    strip_prefix: str = ''


_SIDECARS = ('sidecars/yue2-model-config.json', 'sidecars/yue2-generation-config.json',
    'sidecars/yue2-qwen.tiktoken', 'sidecars/yue2-vae-config.json')
MODEL_REVISIONS = {
    'audio-cpp/Yue2-3B-GGUF': 'eb14a51700bf8baac825a690b2f3b24a01239f85',
    'audio-cpp/SheetSage2-GGUF': '4e88814450385334524e50df43724af583f74206',
    'audio-cpp/audio.cpp-gguf': 'f861648c0c1f4da9f18e9a0ce5e217b0679bb2f5',
}
# Fixed package declarations from the reviewed audio.cpp v0.8.1 model specs.
PACKAGES = (
    NativePackage('yue2_main_q8_0', 'Yue2-3B-GGUF', 'audio-cpp/Yue2-3B-GGUF', 'q8_0',
        (*_SIDECARS, 'yue2-3b-q8_0.gguf')),
    NativePackage('yue2_main_q4_0', 'Yue2-3B-GGUF', 'audio-cpp/Yue2-3B-GGUF', 'q4_0',
        (*_SIDECARS, 'yue2-3b-q4_0.gguf')),
    NativePackage('yue2_vae_f16', 'Yue2-3B-GGUF', 'audio-cpp/Yue2-3B-GGUF', 'f16',
        (*_SIDECARS, 'yue2-vae-f16.gguf')),
    NativePackage('sheetsage2_orig', 'SheetSage2-GGUF', 'audio-cpp/SheetSage2-GGUF', 'orig',
        ('sheetsage2-orig.gguf',)),
    NativePackage('muscriptor_small_f32', 'MuScriptor-Small-GGUF', 'audio-cpp/audio.cpp-gguf', 'f32',
        ('muscriptor-small-f32.gguf',), 'MuScriptor-Small-GGUF'),
)
REQUIRED_FILES = tuple(dict.fromkeys(f'{package.directory}/{file}' for package in PACKAGES for file in package.files))
_DOWNLOADER_SHA256 = 'da8137c59161a28b2a5f99fd691f01dd1c3b6bb4a96bed9f2f86378d07737d88'
_DOWNLOADER_URL = 'https://raw.githubusercontent.com/0xShug0/audio.cpp/39f9013463053e206aa160f8453d734f78999b9d/tools/model_manager_v2.py'
_DOWNLOADER_BYTES = 38245


def native_model_manifest_hash() -> str:
    payload = [(package.identifier, package.directory, package.repository, MODEL_REVISIONS[package.repository],
        package.precision, package.files, package.strip_prefix) for package in PACKAGES]
    return hashlib.sha256(json.dumps([_DOWNLOADER_URL, _DOWNLOADER_SHA256, _DOWNLOADER_BYTES, payload], separators=(',', ':')).encode()).hexdigest()


def _path(root: Path, relative: str) -> Path:
    candidate = root / relative
    if root.is_symlink() or not candidate.resolve().is_relative_to(root.resolve()):
        raise ModuleSetupError('unsafe_install_path')
    for parent in (candidate, *candidate.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ModuleSetupError('unsafe_install_path')
    return candidate


def _valid_model(path: Path) -> bool:
    """Bounded format screening; this does not measure inference or model quality."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        if path.suffix == '.gguf':
            with path.open('rb') as source:
                header = source.read(24)
            if len(header) != 24:
                return False
            magic, version, tensors, metadata = struct.unpack('<4sIQQ', header)
            return magic == b'GGUF' and version in (2, 3) and 0 < tensors <= 10_000_000 and 0 < metadata <= 1_000_000 and path.stat().st_size > 24
        if path.suffix == '.json':
            if path.stat().st_size > 1024**2:
                return False
            value: object = json.loads(path.read_bytes())
            return isinstance(value, dict) and bool(value)
        with path.open('rb') as source:
            lines = source.read(8192).splitlines()[:16]
        if not lines:
            return False
        for line in lines:
            token, rank = line.split()
            if not base64.b64decode(token, validate=True) or not rank.isdigit() or len(rank) > 10:
                return False
        return True
    except (OSError, ValueError, binascii.Error):
        return False


def native_environment_present(environment: ModuleEnvironment) -> bool:
    """Check the expected native entrypoints without starting the engine."""
    preset = {'darwin-arm64': 'macos-metal-release', 'win32-x64': 'windows-cuda-release',
        'linux-x64': 'linux-cuda-release'}.get(platform_key(environment))
    if preset is None:
        return False
    engine = environment.paths['yue2']
    suffix = '.exe' if environment.platform == 'win32' else ''
    try:
        binary = _path(engine, f'build/{preset}/bin/audiocpp_server{suffix}')
        manager = _path(engine, 'tools/model_manager_v2.py')
        return binary.is_file() and binary.stat().st_size > 0 and (environment.platform == 'win32' or os.access(binary, os.X_OK)) and manager.is_file() and 0 < manager.stat().st_size <= 2 * 1024**2
    except (OSError, ModuleSetupError):
        return False


def native_setup_present(environment: ModuleEnvironment) -> bool:
    """Read at most fixed headers and sidecars, never multi-gigabyte weights."""
    if not native_environment_present(environment):
        return False
    try:
        engine = environment.paths['yue2']
        return all(_valid_model(_path(engine, 'models/' + relative)) for relative in REQUIRED_FILES)
    except (OSError, ModuleSetupError):
        return False


def _trusted_source(path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and path.stat().st_size <= 2 * 1024**2 and hashlib.sha256(path.read_bytes()).hexdigest() == _DOWNLOADER_SHA256


async def _prepare_downloader(context: InstallContext, staging: Path) -> Path:
    from .module_install import DownloadArtifact, download_artifact
    repository = Path(__file__).resolve().parents[2]
    source = repository / 'desktop/test/fixtures/model_manager_v2.py'
    if not _trusted_source(source):
        source = context.environment.paths['yue2'] / 'tools/model_manager_v2.py'
    if not _trusted_source(source):
        artifact = DownloadArtifact(url=_DOWNLOADER_URL, sha256=_DOWNLOADER_SHA256, bytes=_DOWNLOADER_BYTES)
        source = context.confined(context.environment.root / 'cache/downloads' / f'model-manager-{_DOWNLOADER_SHA256}.py')
        await download_artifact(artifact, source, context, on_progress=context.update_download)
    script = context.confined(staging / 'tools/model_manager_v2.py')
    script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, script)
    patches = repository / 'external/patches'
    if not patches.is_dir():
        patches = repository / 'patches'
    for name in ('yue-model-resume.patch', 'yue-model-download-reliability.patch'):
        await context.run(['git', 'apply', '--whitespace=nowarn', str(patches / name)], cwd=staging)
    return script


def publish_models(source: Path, destination: Path) -> None:
    """Publish verified staged files atomically without replacing existing data."""
    pending: list[tuple[Path, Path]] = []
    for relative in REQUIRED_FILES:
        staged = _path(source, relative)
        if not staged.exists():
            continue
        target = _path(destination, relative)
        if not _valid_model(staged):
            raise ModuleSetupError('artifact_integrity')
        if target.exists():
            if target.is_file() and (target.samefile(staged) or target.stat().st_size == staged.stat().st_size and _digest(target) == _digest(staged)):
                continue
            raise ModuleSetupError('installation_conflict')
        pending.append((staged, target))
    for staged, target in pending:
        _path(destination, str(target.relative_to(destination)))
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            try:
                os.link(staged, target)
            except OSError as error:
                if error.errno != errno.EXDEV:
                    raise
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.openfabric-native-', suffix='.partial', delete=False) as output:
                    temporary = Path(output.name)
                    with staged.open('rb') as reader:
                        shutil.copyfileobj(reader, output, 1024**2)
                    output.flush()
                    os.fsync(output.fileno())
                os.link(temporary, target)
        except FileExistsError as error:
            raise ModuleSetupError('installation_conflict') from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _digest(path: Path) -> str:
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def _check_staging(root: Path) -> None:
    """The reviewed downloader trusts its destination tree; reject old symlinks."""
    if root.is_symlink():
        raise ModuleSetupError('unsafe_install_path')
    for count, path in enumerate(root.rglob('*'), 1):
        if count > 1000 or path.is_symlink():
            raise ModuleSetupError('unsafe_install_path')


async def setup_native_models(context: InstallContext, download_models: bool) -> InstallOutcome:
    from .module_install import _uv_env
    engine = context.environment.paths['yue2']
    if not native_environment_present(context.environment):
        raise ModuleSetupError('environment_unverified')
    required = {relative: _path(engine, 'models/' + relative) for relative in REQUIRED_FILES}
    conflicts = [relative for relative, path in required.items() if path.exists() and not _valid_model(path)]
    if conflicts:
        return InstallOutcome('manual', 'Existing malformed native model files were preserved. Review or move the conflicting files before resuming setup: ' + ', '.join(conflicts))
    if all(path.is_file() for path in required.values()):
        return InstallOutcome('verified', 'Native engine entrypoints and all required GGUF models, JSON sidecars and tokenizer passed bounded local checks. Existing files were reused; output quality still requires review.')
    if not download_models:
        return InstallOutcome('manual', 'Native engine found. Select model downloads to install the missing required weights automatically.')
    if shutil.which('git') is None:
        return InstallOutcome('manual', 'Git is required to prepare the reviewed native model downloader. Install Git, then resume this setup.')
    staging = context.confined(context.workspace / 'native-model-setup')
    staging.mkdir(parents=True, exist_ok=True)
    context.update_detail('Preparing the reviewed native model downloader; existing engine files are preserved.')
    script = await _prepare_downloader(context, staging)
    specifications: list[JsonValue] = []
    selected: list[str] = []
    for package in PACKAGES:
        files: list[JsonValue] = [f'{package.strip_prefix}/{file}' if package.strip_prefix else file for file in package.files if not required[f'{package.directory}/{file}'].exists()]
        if files:
            selected.append(package.identifier)
            specifications.append({'family': package.identifier, 'id': package.identifier, 'display_name': package.identifier,
                'target_directory': package.directory, 'format': 'gguf', 'precision': package.precision,
                'files': files, 'strip_prefix': package.strip_prefix,
                'download': {'kind': 'huggingface_snapshot', 'repo': package.repository, 'revision': MODEL_REVISIONS[package.repository], 'gated': False}})
    specs = context.confined(staging / 'model_specs')
    specs.mkdir(exist_ok=True)
    if any(path.name != 'required.json' for path in specs.iterdir()):
        raise ModuleSetupError('artifact_integrity')
    context.confined(specs / 'required.json').write_text(json.dumps({'family': 'openfabric-native', 'packages': specifications}), encoding='utf-8')
    models = context.confined(staging / 'models')
    _check_staging(models)
    child_environment = {**_uv_env(context), 'HF_ENDPOINT': 'https://huggingface.co'}
    for identifier in selected:
        context.update_detail('Downloading and verifying required native model package: ' + identifier)
        await context.run([str(context.environment.python), '-I', str(script), '--specs-dir', str(specs), 'install', identifier,
            '--models-root', str(models), '--progress'], cwd=staging, env=child_environment, download_progress=True)
    context.update_detail('Publishing verified native model files without replacing existing files.')
    # Drain disk publication before cancellation can release setup ownership.
    await await_cleanup(asyncio.to_thread(publish_models, models, engine / 'models'))
    if not native_setup_present(context.environment):
        raise ModuleSetupError('environment_unverified')
    return InstallOutcome('verified', 'Required native model files were downloaded with immutable content verification and passed bounded local GGUF, sidecar and tokenizer checks. Output quality still requires review.')
