"""Pinned music assets, without changing existing source, environments or weights."""
from __future__ import annotations

import asyncio
import ast
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
from typing import Annotated, IO, Literal, TYPE_CHECKING
import uuid
import zipfile
import zlib

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from .contracts import JsonObject
from .job_lifecycle import await_cleanup
from .module_contracts import ModuleId
from .stable_lora_contracts import StableLoraVariant

if TYPE_CHECKING:
    from .module_catalog import ModuleEnvironment
    from .module_jobs import InstallContext, InstallOutcome


class ModelArtifact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(pattern=r'^[A-Za-z0-9._/-]+$', max_length=300)
    url: str = Field(pattern=r'^https://huggingface\.co/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/resolve/[0-9a-f]{40}/', max_length=1000)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    bytes: int = Field(strict=True, ge=1, le=100_000_000_000)

    @field_validator('path')
    @classmethod
    def relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or not path.parts or any(part in ('.', '..') or ':' in part or '\\' in part for part in path.parts):
            raise ValueError('Unsafe model path')
        return value


BundleId = Literal['ace-main', 'ace-xl', 'ace-lm4b', 'heartmula', 'heartcodec', 'heart-tokenizer', 'stable-small', 'stable-medium', 'stable-small-training', 'stable-medium-training']


class ModelManifest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1]
    bundles: dict[BundleId, list[ModelArtifact]]


class _Tensor(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dtype: Literal['F16', 'BF16', 'F32', 'F64', 'I8', 'U8', 'BOOL', 'I16', 'U16', 'I32', 'U32', 'I64', 'U64']
    shape: list[Annotated[int, Field(strict=True, ge=0, le=1_000_000_000)]] = Field(max_length=8)
    data_offsets: tuple[Annotated[int, Field(strict=True, ge=0, le=100_000_000_000)], Annotated[int, Field(strict=True, ge=0, le=100_000_000_000)]]


class _NpyHeader(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    descr: str = Field(min_length=2, max_length=64)
    fortran_order: bool
    shape: tuple[Annotated[int, Field(strict=True, ge=0, le=1_000_000_000)], ...] = Field(max_length=8)


_json = TypeAdapter(JsonObject)
_header = TypeAdapter(dict[str, _Tensor | dict[str, str] | None])
_DTYPE_BYTES = {'F16': 2, 'BF16': 2, 'F32': 4, 'F64': 8, 'I8': 1, 'U8': 1, 'BOOL': 1,
                'I16': 2, 'U16': 2, 'I32': 4, 'U32': 4, 'I64': 8, 'U64': 8}
_HEART_OUTPUTS = ('ckpt-mlx/heartmula/config.json', 'ckpt-mlx/heartmula/model.safetensors',
                  'ckpt-mlx/heartcodec/config.json', 'ckpt-mlx/heartcodec/model.safetensors', 'ckpt/tokenizer.json')


def manifest_path() -> Path:
    return Path(__file__).parent / 'assets/music-models.json'


def _manifest() -> ModelManifest:
    from .module_jobs import ModuleSetupError
    try:
        path = manifest_path()
        if path.stat().st_size > 300_000:
            raise ValueError('Oversized manifest')
        return ModelManifest.model_validate_json(path.read_bytes())
    except (OSError, ValueError, ValidationError) as error:
        raise ModuleSetupError('artifact_integrity') from error


def _path(root: Path, relative: str, *, writing: bool = False) -> Path:
    from .module_jobs import ModuleSetupError
    trusted = root.expanduser().resolve()
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or any(part in ('.', '..') or ':' in part or '\\' in part for part in parts):
        raise ModuleSetupError('unsafe_install_path')
    candidate = trusted
    for part in parts:
        candidate /= part
        if not candidate.resolve().is_relative_to(trusted) or writing and candidate.is_symlink():
            raise ModuleSetupError('unsafe_install_path')
    return candidate


def _valid_npy_member(source: IO[bytes], size: int) -> bool:
    prefix = source.read(8)
    if len(prefix) != 8 or prefix[:6] != b'\x93NUMPY' or prefix[6:] not in (b'\x01\x00', b'\x02\x00', b'\x03\x00'):
        return False
    length_size = 2 if prefix[6] == 1 else 4
    encoded_length = source.read(length_size)
    if len(encoded_length) != length_size:
        return False
    length = int.from_bytes(encoded_length, 'little')
    if not 1 <= length <= 65536 or 8 + length_size + length > size:
        return False
    encoded = source.read(length)
    if len(encoded) != length or not encoded.endswith(b'\n'):
        return False
    decoded: object = ast.literal_eval(encoded.decode('utf-8' if prefix[6] == 3 else 'latin1'))
    header = _NpyHeader.model_validate(decoded)
    descriptor = re.fullmatch(r'[<>=|]([biufcSUV])([0-9]{1,10})', header.descr)
    if descriptor is None:
        return False
    kind, itemsize = descriptor.group(1), int(descriptor.group(2))
    numeric_sizes = {'b': (1,), 'i': (1, 2, 4, 8), 'u': (1, 2, 4, 8), 'f': (2, 4, 8, 16), 'c': (8, 16, 32)}
    if kind in numeric_sizes and itemsize not in numeric_sizes[kind]:
        return False
    if kind == 'U':
        itemsize *= 4
    return 8 + length_size + length + math.prod(header.shape) * itemsize == size


def _valid_file(path: Path) -> bool:
    """Bounded format checks only; existing models are never silently replaced."""
    try:
        size = path.stat().st_size
        if not path.is_file() or size <= 0:
            return False
        if path.suffix == '.safetensors':
            with path.open('rb') as source:
                encoded_length = source.read(8)
                if len(encoded_length) != 8:
                    return False
                length = int.from_bytes(encoded_length, 'little')
                if not 2 <= length <= 16 * 1024**2 or length + 8 > size:
                    return False
                header = _header.validate_json(source.read(length))
            tensors: list[_Tensor] = []
            for name, value in header.items():
                if name == '__metadata__' and (isinstance(value, dict) or value is None):
                    continue
                if not isinstance(value, _Tensor):
                    return False
                tensors.append(value)
            if not tensors or len(tensors) > 50_000:
                return False
            offset = 0
            for tensor in sorted(tensors, key=lambda item: item.data_offsets[0]):
                start, end = tensor.data_offsets
                if start != offset or end - start != math.prod(tensor.shape) * _DTYPE_BYTES[tensor.dtype]:
                    return False
                offset = end
            return offset == size - 8 - length
        if path.suffix == '.npz':
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if not 1 <= len(members) <= 50_000:
                    return False
                for member in members:
                    if not member.filename.endswith('.npy') or member.file_size < 6:
                        return False
                    with archive.open(member) as source:
                        if not _valid_npy_member(source, member.file_size):
                            return False
            return True
        if path.suffix == '.json':
            return size <= 32 * 1024**2 and bool(_json.validate_json(path.read_bytes()))
        if path.suffix == '.py':
            if size > 2 * 1024**2:
                return False
            ast.parse(path.read_bytes())
        return True
    except (OSError, ValueError, ValidationError, struct.error, OverflowError, RecursionError, SyntaxError, RuntimeError, zipfile.BadZipFile, zlib.error):
        return False


def _all_valid(root: Path, paths: list[str] | tuple[str, ...]) -> bool:
    return bool(paths) and all(_valid_file(_path(root, relative)) for relative in paths)


def stable_training_artifacts(variant: StableLoraVariant) -> list[ModelArtifact]:
    from .module_jobs import ModuleSetupError
    bundle: BundleId = 'stable-medium-training' if variant == 'medium' else 'stable-small-training'
    artifacts = _manifest().bundles.get(bundle, [])
    if len(artifacts) != 3:
        raise ModuleSetupError('artifact_integrity')
    return artifacts


async def setup_stable_training_models(context: InstallContext, variant: StableLoraVariant) -> InstallOutcome:
    from .module_jobs import InstallOutcome, ModuleSetupError
    artifacts = stable_training_artifacts(variant)
    root = context.environment.paths['stable_audio3']
    _preflight(root, [item.path for item in artifacts])
    await _install_artifacts(context, 'stable_audio3', artifacts)
    if not _all_valid(root, [item.path for item in artifacts]):
        raise ModuleSetupError('artifact_integrity')
    return InstallOutcome('verified', 'Training base, matching audio encoder and text encoder passed structural checks. Training and generated audio quality remain unverified.')


def _required_artifacts(environment: ModuleEnvironment, identifier: ModuleId) -> list[ModelArtifact]:
    from .module_jobs import ModuleSetupError
    manifest = _manifest()
    if identifier == 'heartmula':
        bundles: tuple[BundleId, ...] = ('heartmula', 'heartcodec', 'heart-tokenizer')
        root = environment.paths[identifier]
        return [item for bundle in bundles
                if bundle == 'heart-tokenizer' or not _all_valid(root, (f'ckpt-mlx/{bundle}/config.json', f'ckpt-mlx/{bundle}/model.safetensors'))
                for item in manifest.bundles.get(bundle, [])]
    if identifier == 'stable_audio3':
        medium = manifest.bundles.get('stable-medium', [])
        bundle: BundleId = 'stable-medium' if _all_valid(environment.paths[identifier], [item.path for item in medium]) else 'stable-small'
        return manifest.bundles.get(bundle, [])
    if identifier == 'ace_step':
        from .config import ACE_STEP_DIT_MODEL, ACE_STEP_LM_MODEL
        if ACE_STEP_DIT_MODEL not in ('acestep-v15-turbo', 'acestep-v15-xl-sft') or ACE_STEP_LM_MODEL not in ('acestep-5Hz-lm-1.7B', 'acestep-5Hz-lm-4B'):
            raise ModuleSetupError('environment_unverified')
        components = {ACE_STEP_DIT_MODEL, ACE_STEP_LM_MODEL, 'vae', 'Qwen3-Embedding-0.6B'}
        artifacts = [item for item in manifest.bundles.get('ace-main', [])
                     if PurePosixPath(item.path).parts[1] in components]
        if ACE_STEP_DIT_MODEL == 'acestep-v15-xl-sft':
            artifacts.extend(manifest.bundles.get('ace-xl', []))
        if ACE_STEP_LM_MODEL == 'acestep-5Hz-lm-4B':
            artifacts.extend(manifest.bundles.get('ace-lm4b', []))
        return artifacts
    raise ModuleSetupError('unsupported_platform')


def music_models_present(environment: ModuleEnvironment, identifier: ModuleId) -> bool:
    from .module_jobs import ModuleSetupError
    try:
        paths = list(_HEART_OUTPUTS) if identifier == 'heartmula' else [item.path for item in _required_artifacts(environment, identifier)]
        return _all_valid(environment.paths[identifier], paths)
    except (OSError, ModuleSetupError):
        return False


def model_download_bytes(environment: ModuleEnvironment, identifier: ModuleId) -> int | None:
    from .module_jobs import ModuleSetupError
    try:
        if music_models_present(environment, identifier):
            return 0
        return sum(item.bytes for item in _required_artifacts(environment, identifier)
                   if not _valid_file(_path(environment.paths[identifier], item.path))
                   and _installed_model_code(environment, identifier, item) is None)
    except (OSError, ModuleSetupError):
        return None


def conversion_required_bytes(environment: ModuleEnvironment, identifier: ModuleId) -> int:
    """Reserve conversion staging and publication even when originals already exist."""
    if identifier != 'heartmula' or music_models_present(environment, identifier):
        return 0
    return 2 * sum(item.bytes for item in _required_artifacts(environment, identifier) if item.path.endswith('.safetensors'))


def model_directories(environment: ModuleEnvironment, identifier: ModuleId) -> tuple[Path, ...]:
    """Include nested model destinations so disk admission sees mounted directories."""
    from .module_jobs import ModuleSetupError
    root = environment.paths[identifier].expanduser().resolve()
    try:
        paths = [item.path for item in _required_artifacts(environment, identifier)]
        if identifier == 'heartmula':
            paths.extend(_HEART_OUTPUTS)
        return tuple(dict.fromkeys([root, *(_path(root, relative).parent for relative in paths)]))
    except (OSError, ModuleSetupError):
        return (root,)


def _installed_model_code(environment: ModuleEnvironment, identifier: ModuleId, artifact: ModelArtifact) -> Path | None:
    if identifier != 'ace_step' or not artifact.path.endswith('.py'):
        return None
    parts = PurePosixPath(artifact.path).parts
    variants = {'acestep-v15-turbo': 'turbo', 'acestep-v15-xl-sft': 'xl_sft'}
    variant = variants.get(parts[1]) if len(parts) == 3 else None
    if variant is None:
        return None
    source = _path(environment.paths[identifier], 'acestep/models/' + variant + '/' + parts[-1])
    return source if _valid_file(source) else None


def _preflight(root: Path, paths: list[str] | tuple[str, ...]) -> None:
    from .module_jobs import ModuleSetupError
    for relative in paths:
        path = _path(root, relative)
        if path.exists() or path.is_symlink():
            if not _valid_file(path):
                raise ModuleSetupError('installation_conflict')
        else:
            _path(root, relative, writing=True)


def _publish(source: Path, root: Path, relative: str) -> None:
    from .module_jobs import ModuleSetupError
    target = _path(root, relative, writing=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = _path(root, str(PurePosixPath(relative).with_name('.' + target.name + '.' + uuid.uuid4().hex + '.part')), writing=True)
    try:
        with source.open('rb') as reader, temporary.open('xb') as writer:
            shutil.copyfileobj(reader, writer, 1024**2)
            writer.flush()
            os.fsync(writer.fileno())
        _path(root, relative, writing=True)
        try:
            os.link(temporary, target)
        except FileExistsError as error:
            raise ModuleSetupError('installation_conflict') from error
    finally:
        temporary.unlink(missing_ok=True)


async def _install_artifacts(context: InstallContext, identifier: ModuleId, artifacts: list[ModelArtifact]) -> None:
    from .module_install import DownloadArtifact, download_artifact
    from .module_jobs import ModuleSetupError
    root = context.environment.paths[identifier]
    for item in artifacts:
        if _valid_file(_path(root, item.path)):
            continue
        source = _installed_model_code(context.environment, identifier, item)
        if source is not None:
            # Upstream syncs checkpoint Python code from the installed engine version.
            await await_cleanup(asyncio.to_thread(_publish, source, root, item.path))
            continue
        context.update_detail('Downloading and verifying ' + Path(item.path).name + '.')
        cached = context.confined(context.environment.root / 'cache/music-models' / item.sha256 / Path(item.path).name)
        artifact = DownloadArtifact(url=item.url, sha256=item.sha256, bytes=item.bytes)
        await download_artifact(artifact, cached, context, on_progress=context.update_download)
        if not _valid_file(cached):
            raise ModuleSetupError('artifact_integrity')
        await await_cleanup(asyncio.to_thread(_publish, cached, root, item.path))


async def _convert_heart(context: InstallContext) -> None:
    from .module_catalog import engine_python
    from .module_jobs import ModuleSetupError
    root = context.environment.paths['heartmula']
    staging = context.confined(context.workspace / ('heart-convert-' + uuid.uuid4().hex))
    try:
        for kind, original in (('heartmula', 'HeartMuLa-oss-3B'), ('heartcodec', 'HeartCodec-oss-20260123')):
            output = ('ckpt-mlx/' + kind + '/config.json', 'ckpt-mlx/' + kind + '/model.safetensors')
            if _all_valid(root, output):
                continue
            context.update_detail('Converting ' + kind + ' weights to MLX bfloat16.')
            destination = context.confined(staging / kind)
            child_env = {**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1', 'DO_NOT_TRACK': '1'}
            await context.run([str(engine_python(root, context.environment.platform)), '-m', 'heartlib_mlx.utils.convert',
                '--src', str(_path(root, 'ckpt/' + original)), '--dst', str(destination),
                '--model-type', kind, '--dtype', 'bfloat16'], cwd=root, env=child_env)
            context.update_detail('Verifying converted ' + kind + ' configuration and tensor data.')
            pending: list[tuple[Path, str]] = []
            for relative in output:
                converted = context.confined(destination / Path(relative).name)
                if not _valid_file(converted):
                    raise ModuleSetupError('artifact_integrity')
                pending.append((converted, relative))
            # Preserve completed components if the next conversion fails.
            for source, relative in pending:
                if not _valid_file(_path(root, relative)):
                    await await_cleanup(asyncio.to_thread(_publish, source, root, relative))
    finally:
        # A failed descendant drain retains the worker and its scratch ownership.
        if staging.exists() and context.identifier not in context.service.processes:
            await await_cleanup(asyncio.to_thread(shutil.rmtree, staging))


async def setup_music_models(context: InstallContext, identifier: ModuleId, download_models: bool) -> InstallOutcome:
    from .module_jobs import InstallOutcome, ModuleSetupError
    if music_models_present(context.environment, identifier):
        return InstallOutcome('verified', 'Existing required model files passed structural checks. Audio generation and GPU compatibility remain unverified.')
    if not download_models:
        return InstallOutcome('manual', 'Model downloads were not selected. Select downloads to finish the required local music models.')
    try:
        artifacts = _required_artifacts(context.environment, identifier)
    except ModuleSetupError as error:
        if identifier == 'ace_step' and error.code == 'environment_unverified':
            return InstallOutcome('manual', 'The configured ACE-Step variant has no reviewed download manifest. Its setting and existing files were preserved.')
        raise
    if not artifacts:
        raise ModuleSetupError('artifact_integrity')
    root = context.environment.paths[identifier]
    _preflight(root, [item.path for item in artifacts])
    if identifier == 'heartmula':
        _preflight(root, _HEART_OUTPUTS)
    await _install_artifacts(context, identifier, artifacts)
    if identifier == 'heartmula':
        await _convert_heart(context)
    if not music_models_present(context.environment, identifier):
        raise ModuleSetupError('artifact_integrity')
    return InstallOutcome('verified', 'Required local models are installed and structurally checked. Audio generation and GPU compatibility remain unverified.')
