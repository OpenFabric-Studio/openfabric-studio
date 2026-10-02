"""Check, copy, patch and optionally build a pinned native engine in a new workspace.

No models are downloaded and no running installation or configuration is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

SOURCE_COMMIT = '39f9013463053e206aa160f8453d734f78999b9d'
RELEASE_COMMIT = 'f2b4937306daa25f5c78520f3c626ed31495a37a'
SUPPORTED_COMMITS = (SOURCE_COMMIT, RELEASE_COMMIT)
UPSTREAM = 'https://github.com/0xShug0/audio.cpp.git'
PATCH_DIR = Path(__file__).resolve().parents[2] / 'external/patches'
Backend = Literal['cpu', 'metal', 'cuda']
Tls = Literal['bundled', 'system']


@dataclass(frozen=True)
class PatchIdentity:
    name: str
    sha256: str


PATCH_IDENTITIES = (
    PatchIdentity('yue-workspace-release.patch', '188d512e892ab02fbc20487d1ba58112b2b53b8860858434119c455e12876cda'),
    PatchIdentity('yue-progress.patch', '43f6d52b6e3ceb898e72d3cd3a7fc6f8a1278a566f7da363c89036db31df99b5'),
)
COMMON_SOURCE_HASHES = {
    'src/models/yue2/ar_runtime.cpp': '6f37b9f597e8728b8fdf3573a6ed4ce3d52678556347b76d9a675872c58ff92f',
    'src/models/yue2/nar_runtime.cpp': '053d77291c8a009976f66b5400fee9637554fbcd14a20c4564d51b9de1e63add',
    'src/models/yue2/pipeline.cpp': 'abf733f4951c9a1ed670c1bf92540feee7ef93ef4d6cd5a33b1b274bfec0a0d6',
    'include/engine/models/yue2/openfabric_progress.h': '19c6f403b200a824fc1f2b7e21bffeebc1d5ec980ca8461848733c44c47ac0d6',
}
SESSION_SOURCE_HASHES = {
    SOURCE_COMMIT: '5cbf220bc28a2dce3ed210fdd9d0f44925952e43613a0f462c109aec257161e5',
    RELEASE_COMMIT: '7b62686fb074d9e4c02fc5d1bf5e52ee6b1409130d01ca10fc1dee77d2d1667a',
}


@dataclass(frozen=True)
class NativeBuildManifest:
    schema_version: int
    source_commit: str
    backend: Backend
    binary_sha256: str
    patches: tuple[PatchIdentity, ...]
    progress_schema: int
    workspace_release: bool
    run_option: str
    known_phase_totals: tuple[str, ...]


def sha256_file(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def git_output(source: Path, arguments: list[str]) -> str:
    return subprocess.check_output(['git', '-C', str(source), *arguments], text=True,
                                   env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}).strip()


def validate_source(source: Path) -> str:
    commit = git_output(source, ['rev-parse', 'HEAD'])
    if commit not in SUPPORTED_COMMITS:
        raise ValueError('unsupported_native_source')
    if git_output(source, ['status', '--porcelain', '--untracked-files=all']):
        raise ValueError('native_source_not_clean')
    return commit


def verify_patch_identity() -> tuple[Path, ...]:
    result: list[Path] = []
    for identity in PATCH_IDENTITIES:
        path = PATCH_DIR / identity.name
        if sha256_file(path) != identity.sha256:
            raise ValueError('native_patch_identity_mismatch')
        result.append(path)
    return tuple(result)


def check_patches(source: Path) -> str:
    commit = validate_source(source)
    patches = verify_patch_identity()
    subprocess.run(['git', '-C', str(source), 'apply', '--check', *(str(path) for path in patches)], check=True)
    return commit


def verify_patched_source(source: Path, commit: str) -> None:
    if commit not in SUPPORTED_COMMITS or git_output(source, ['rev-parse', 'HEAD']) != commit:
        raise ValueError('unsupported_native_source')
    expected = {**COMMON_SOURCE_HASHES, 'src/models/yue2/session.cpp': SESSION_SOURCE_HASHES[commit]}
    changed = set(git_output(source, ['diff', '--name-only', 'HEAD']).splitlines())
    untracked = set(git_output(source, ['ls-files', '--others', '--exclude-standard']).splitlines())
    if changed | untracked != set(expected):
        raise ValueError('native_patched_source_not_exact')
    for name, digest in expected.items():
        if sha256_file(source / name) != digest:
            raise ValueError('native_patched_source_not_exact')
    verify_patch_identity()


def atomic_json(destination: Path, value: object) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f'.{destination.name}.', suffix='.partial', dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_source(workspace: Path, commit: str, source: Path | None = None) -> Path:
    if commit not in SUPPORTED_COMMITS:
        raise ValueError('unsupported_native_source')
    patches = verify_patch_identity()
    workspace = workspace.resolve()
    if source is not None and workspace.is_relative_to(source.resolve()):
        raise ValueError('native_workspace_inside_source')
    destination = workspace / 'source'
    marker = workspace / '.openfabric-native-source.json'
    if workspace.exists() and any(workspace.iterdir()):
        if not marker.is_file() or marker.stat().st_size > 65536:
            raise ValueError('native_workspace_not_owned')
        raw: object = json.loads(marker.read_text())
        if not isinstance(raw, dict) or raw != {'source_commit': commit, 'patches': [asdict(item) for item in PATCH_IDENTITIES]}:
            raise ValueError('native_workspace_identity_mismatch')
        verify_patched_source(destination, commit)
        return destination
    if source is not None and check_patches(source) != commit:
        raise ValueError('native_source_commit_mismatch')
    workspace.mkdir(parents=True, exist_ok=True)
    upstream = str(source.resolve()) if source is not None else UPSTREAM
    subprocess.run(['git', 'clone', '--no-hardlinks', '--no-checkout', upstream, str(destination)], check=True)
    if source is None:
        subprocess.run(['git', '-C', str(destination), 'fetch', '--depth=1', 'origin', commit], check=True)
    subprocess.run(['git', '-C', str(destination), 'checkout', '--detach', commit], check=True)
    check_patches(destination)
    subprocess.run(['git', '-C', str(destination), 'apply', *(str(path) for path in patches)], check=True)
    verify_patched_source(destination, commit)
    atomic_json(marker, {'source_commit': commit, 'patches': [asdict(item) for item in PATCH_IDENTITIES]})
    return destination


def create_manifest(binary: Path, source_commit: str, backend: Backend) -> NativeBuildManifest:
    if source_commit not in SUPPORTED_COMMITS:
        raise ValueError('unsupported_native_source')
    verify_patch_identity()
    return NativeBuildManifest(1, source_commit, backend, sha256_file(binary), PATCH_IDENTITIES,
                               1, True, 'openfabric_run_id', ('acoustic', 'decode'))


def manifest_path(binary: Path) -> Path:
    return binary.with_name(binary.name + '.openfabric.json')


def write_manifest(binary: Path, manifest: NativeBuildManifest) -> None:
    atomic_json(manifest_path(binary), asdict(manifest))


def read_build_manifest(binary: Path) -> NativeBuildManifest:
    """Validate optional progress capability before advertising it to the app."""
    path = manifest_path(binary)
    if path.stat().st_size > 65536:
        raise ValueError('invalid_native_build_manifest')
    raw: object = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError('invalid_native_build_manifest')
    if type(raw.get('schema_version')) is not int or type(raw.get('progress_schema')) is not int or type(raw.get('workspace_release')) is not bool:
        raise ValueError('invalid_native_build_manifest')
    commit = raw.get('source_commit')
    backend = raw.get('backend')
    digest = raw.get('binary_sha256')
    if not isinstance(commit, str) or commit not in SUPPORTED_COMMITS or backend not in ('cpu', 'metal', 'cuda') or not isinstance(digest, str):
        raise ValueError('invalid_native_build_manifest')
    typed_backend: Backend = 'cpu' if backend == 'cpu' else 'metal' if backend == 'metal' else 'cuda'
    expected = NativeBuildManifest(1, commit, typed_backend, digest, PATCH_IDENTITIES,
                                   1, True, 'openfabric_run_id', ('acoustic', 'decode'))
    # JSON arrays and dataclass tuples use the same wire representation.
    if raw != json.loads(json.dumps(asdict(expected))):
        raise ValueError('invalid_native_build_manifest')
    if sha256_file(binary) != digest:
        raise ValueError('native_binary_identity_mismatch')
    verify_patch_identity()
    return expected


def cmake_command(source: Path, build: Path, backend: Backend, commit: str,
                  tls: Tls = 'bundled', boringssl_archive: Path | None = None) -> list[str]:
    version = '0.8.1' if commit == RELEASE_COMMIT else 'dev'
    command = ['cmake', '-S', str(source), '-B', str(build), '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release',
            f'-DAUDIOCPP_VERSION={version}+openfabric.native1', '-DAUDIOCPP_MODEL_SET=custom',
            '-DAUDIOCPP_MODELS=yue2,sheetsage2,muscriptor', '-DAUDIOCPP_BUILD_SERVER_FRONTENDS=OFF',
            '-DAUDIOCPP_BUILD_NATIVE_MODEL_MANAGER=ON', '-DENGINE_ENABLE_OPENMP=OFF', '-DGGML_OPENMP=OFF',
            '-DENGINE_ENABLE_NATIVE_CPU=OFF', '-DENGINE_ENABLE_HIP=OFF', '-DENGINE_ENABLE_VULKAN=OFF',
            f'-DENGINE_ENABLE_CUDA={"ON" if backend == "cuda" else "OFF"}',
            f'-DENGINE_ENABLE_METAL={"ON" if backend == "metal" else "OFF"}', '-DGGML_METAL_EMBED_LIBRARY=ON',
            f'-DAUDIOCPP_USE_SYSTEM_OPENSSL={"ON" if tls == "system" else "OFF"}']
    if boringssl_archive is not None:
        if tls != 'bundled' or not boringssl_archive.is_file():
            raise ValueError('invalid_boringssl_archive')
        command.append(f'-DAUDIOCPP_BORINGSSL_ARCHIVE={boringssl_archive.resolve()}')
    return command


def build_native(workspace: Path, source: Path, commit: str, backend: Backend, jobs: int,
                 tls: Tls = 'bundled', boringssl_archive: Path | None = None) -> Path:
    if not 1 <= jobs <= 64:
        raise ValueError('invalid_native_build_jobs')
    if backend == 'metal' and sys.platform != 'darwin':
        raise ValueError('metal_requires_macos')
    if any(shutil.which(tool) is None for tool in ('cmake', 'ninja')):
        raise ValueError('native_build_tools_unavailable')
    verify_patched_source(source, commit)
    build = workspace.resolve() / 'build'
    binary = build / 'bin' / ('audiocpp_server.exe' if sys.platform == 'win32' else 'audiocpp_server')
    # A failed rebuild must never leave an old capability manifest looking current.
    manifest_path(binary).unlink(missing_ok=True)
    subprocess.run(cmake_command(source, build, backend, commit, tls, boringssl_archive), check=True)
    subprocess.run(['cmake', '--build', str(build), '--config', 'Release', '--target', 'audiocpp_server', '--parallel', str(jobs)], check=True)
    verify_patched_source(source, commit)
    write_manifest(binary, create_manifest(binary, commit, backend))
    return binary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, help='Optional clean, read-only checkout to copy; otherwise clone official upstream')
    parser.add_argument('--workspace', type=Path, help='New isolated source/build directory outside the active installation')
    parser.add_argument('--commit', choices=SUPPORTED_COMMITS, default=SOURCE_COMMIT)
    parser.add_argument('--backend', choices=('cpu', 'metal', 'cuda'), default='cpu')
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--tls', choices=('bundled', 'system'), default='bundled',
                        help='Use upstream-pinned BoringSSL sources, or installed OpenSSL development libraries')
    parser.add_argument('--boringssl-archive', type=Path, help='Optional offline archive checked by upstream SHA256')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='Check source and patch applicability without changing source')
    mode.add_argument('--apply', action='store_true', help='Make and verify a patched clean copy')
    mode.add_argument('--build', action='store_true', help='Prepare, compile and write a verified capability manifest')
    arguments = parser.parse_args()
    source: Path | None = arguments.source
    workspace: Path | None = arguments.workspace
    backend: Backend = 'cpu' if arguments.backend == 'cpu' else 'metal' if arguments.backend == 'metal' else 'cuda'
    if arguments.check:
        if source is None:
            parser.error('--check requires --source')
        if check_patches(source) != arguments.commit:
            raise ValueError('native_source_commit_mismatch')
        print(f'Native source and complete patches verified: {arguments.commit}')
        return
    if workspace is None:
        parser.error('--apply/--build require --workspace')
    prepared = prepare_source(workspace, arguments.commit, source)
    if arguments.build:
        tls: Tls = 'system' if arguments.tls == 'system' else 'bundled'
        binary = build_native(workspace, prepared, arguments.commit, backend, arguments.jobs, tls, arguments.boringssl_archive)
        print(f'YUE2_SERVER_BIN={binary}')
        print(f'Capability manifest: {manifest_path(binary)}')
    else:
        print(f'Patched native source verified: {prepared}')


if __name__ == '__main__':
    main()
