"""Optional pinned macOS tool, installed through the existing setup owner."""
from __future__ import annotations

from pathlib import Path
import hashlib
import shutil
from typing import TYPE_CHECKING, Annotated, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .atomic_files import write_object
from .job_lifecycle import run_owned_thread
from .module_contracts import ModuleEvidence
from .module_install import DownloadArtifact
from .module_jobs import InstallContext, InstallOutcome, ModuleSetupError
from .module_runtime import RuntimeEvidence
from .music_stretch import REVISION, RubberBandStretch

if TYPE_CHECKING:
    from .module_catalog import ModuleEnvironment

BINARY_ARCHIVE = DownloadArtifact(url='https://breakfastquay.com/files/releases/rubberband-4.0.0-gpl-executable-macos.tar.bz2',
    bytes=1500014, sha256='0dc91509a31a94c1144436cc20e6f1d165bdab39fe125f9be7d46139921b733f')
SOURCE_ARCHIVE = DownloadArtifact(url='https://breakfastquay.com/files/releases/rubberband-4.0.0.tar.bz2',
    bytes=236705, sha256='af050313ee63bc18b35b2e064e5dce05b276aaf6d1aa2b8a82ced1fe2f8028e9')
BINARY_SHA256 = 'bffe8a37f204958352e768fc4a751d3dbce18e3b42c2a3427a80498e7d8cee79'
SOURCE_NAME = 'rubberband-4.0.0-source.tar.bz2'
RECEIPT_NAME = '.openfabric-rubberband.json'
FILES = ('bin/rubberband', 'COPYING', 'README.md', 'CHANGELOG', SOURCE_NAME)
Checksum = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$', min_length=64, max_length=64)]


class RuntimeReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    schema_version: Literal[1] = 1
    revision: str = Field(min_length=1, max_length=120)
    binary_archive_sha256: Checksum
    source_archive_sha256: Checksum
    files: dict[str, Checksum] = Field(min_length=5, max_length=5)


def _checksum(path: Path, root: Path) -> str:
    if path.is_symlink() or not path.is_file() or path.resolve() != path or not path.is_relative_to(root) or path.stat().st_size > 32 * 1024**2:
        raise ModuleSetupError('artifact_integrity')
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        while block := stream.read(65536):
            size += len(block)
            if size > 32 * 1024**2:
                raise ModuleSetupError('artifact_integrity')
            digest.update(block)
    return digest.hexdigest()


def _receipt(root: Path) -> RuntimeReceipt:
    path = root / RECEIPT_NAME
    if root.is_symlink() or root.resolve() != root or path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
        raise ModuleSetupError('environment_unverified')
    with path.open('rb') as stream:
        body = stream.read(16385)
    if len(body) > 16384:
        raise ModuleSetupError('environment_unverified')
    value = RuntimeReceipt.model_validate_json(body)
    if (value.revision != REVISION or set(value.files) != set(FILES)
            or value.binary_archive_sha256 != BINARY_ARCHIVE.sha256 or value.source_archive_sha256 != SOURCE_ARCHIVE.sha256
            or value.files['bin/rubberband'] != BINARY_SHA256 or value.files[SOURCE_NAME] != SOURCE_ARCHIVE.sha256
            or any(_checksum(root / name, root) != value.files[name] for name in FILES)):
        raise ModuleSetupError('artifact_integrity')
    return value


def status(environment: ModuleEnvironment) -> RuntimeEvidence:
    root = environment.paths['music_dsp']
    available = False
    if environment.platform == 'darwin' and environment.architecture in {'arm64', 'aarch64'}:
        try:
            _receipt(root)
            available = True
        except (OSError, ValueError, RuntimeError, ValidationError, ModuleSetupError):
            pass
    return RuntimeEvidence(ready=available, installed=root.exists(),
        capabilities=('verified_native_stretch', 'uniform_stretch_600s', 'mapped_stretch_30s') if available else (),
        evidence=(ModuleEvidence(code='rubberband_identity',
            detail='Pinned Rubber Band executable, notices and corresponding source archive match the installed receipt.' if available
                else 'The reviewed managed Rubber Band runtime is missing, changed or unsupported on this platform.', verified=available),))


def native(environment: ModuleEnvironment | None = None) -> RubberBandStretch | None:
    if environment is None:
        from .module_catalog import configured_environment
        environment = configured_environment()
    if not status(environment).ready:
        return None
    return RubberBandStretch(environment.paths['music_dsp'] / 'bin/rubberband', BINARY_SHA256)


async def setup(context: InstallContext, target: Path) -> InstallOutcome:
    from .module_install import download_artifact, extract_archive
    env = context.environment
    if env.platform != 'darwin' or env.architecture not in {'arm64', 'aarch64'}:
        raise ModuleSetupError('unsupported_platform')
    context.confined(target)
    if target.exists() or target.is_symlink():
        raise ModuleSetupError('installation_conflict')
    if shutil.disk_usage(env.root).free < 128 * 1024**2:
        raise ModuleSetupError('insufficient_disk')
    context.update_detail('Downloading the pinned Rubber Band tool and corresponding source; no model weights are needed.')
    archive = context.confined(context.workspace / 'rubberband-4.0.0.tar.bz2')
    source = context.confined(context.workspace / SOURCE_NAME)
    await download_artifact(BINARY_ARCHIVE, archive, context)
    await download_artifact(SOURCE_ARCHIVE, source, context)
    if (await run_owned_thread(_checksum, archive, env.root) != BINARY_ARCHIVE.sha256
            or archive.stat().st_size != BINARY_ARCHIVE.bytes
            or await run_owned_thread(_checksum, source, env.root) != SOURCE_ARCHIVE.sha256
            or source.stat().st_size != SOURCE_ARCHIVE.bytes):
        raise ModuleSetupError('artifact_integrity')
    extracted = context.confined(context.workspace / ('rubberband-extract-' + uuid.uuid4().hex))
    await run_owned_thread(extract_archive, archive, extracted)
    package = extracted / 'rubberband-4.0.0-gpl-executable-macos'
    if await run_owned_thread(_checksum, package / 'rubberband', env.root) != BINARY_SHA256:
        raise ModuleSetupError('artifact_integrity')
    stage = context.confined(context.workspace / ('rubberband-stage-' + uuid.uuid4().hex))
    (stage / 'bin').mkdir(parents=True)
    for name in ('rubberband', 'COPYING', 'README.md', 'CHANGELOG'):
        candidate = package / name
        await run_owned_thread(_checksum, candidate, env.root)
        await run_owned_thread(shutil.copyfile, candidate, stage / ('bin/rubberband' if name == 'rubberband' else name))
    await run_owned_thread(shutil.copyfile, source, stage / SOURCE_NAME)
    (stage / 'bin/rubberband').chmod(0o755)
    context.update_detail('Verifying the pinned native executable and retained license/source files.')
    await context.run([str(stage / 'bin/rubberband'), '--version'], timeout=15)
    files = {name: await run_owned_thread(_checksum, stage / name, env.root) for name in FILES}
    receipt = RuntimeReceipt(revision=REVISION, binary_archive_sha256=BINARY_ARCHIVE.sha256,
        source_archive_sha256=SOURCE_ARCHIVE.sha256, files=files)
    write_object(stage / RECEIPT_NAME, receipt.model_dump(mode='json'))
    await run_owned_thread(_receipt, stage)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise ModuleSetupError('installation_conflict')
    await run_owned_thread(stage.rename, target)
    return InstallOutcome('verified', 'Pinned native stretch tool, notices and corresponding source are installed. Musical quality still requires listening review.')
