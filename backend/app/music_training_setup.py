"""Pinned isolated training setup; generation sources and weights are read only."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .atomic_files import write_object
from .job_lifecycle import await_cleanup
from .module_catalog import ModuleEnvironment, available_bytes, platform_key
from .module_contracts import LicenseScope, ModuleLicenseDeclaration, ModulePlan, ModulePlanRequest, ModulePlanStep
from .module_install import SourcePin, _uv_env, DownloadArtifact
from .module_jobs import InstallContext, InstallOutcome, ModuleSetupError
from .module_music_models import ModelArtifact, _path, _preflight, _publish, _valid_file
from .music_training_contracts import MusicTrainingEngine

_ASSETS = Path(__file__).parent / 'assets'
# This session-owned receipt predates source-directory import checks.
_LEGACY_RECIPES: dict[MusicTrainingEngine, str] = {
    'yue2': '203b4f3261a6104e2d0207fc0e8a153b1c8b14f0fae983854d1eeb2d3fee7897',
}


@dataclass(frozen=True)
class TrainingSource:
    folder: str
    pin: SourcePin
    marker: str


@dataclass(frozen=True)
class TrainingVenv:
    folder: str
    requirements: str
    pythonpath: tuple[str, ...]
    check: str


@dataclass(frozen=True)
class TrainingRecipe:
    sources: tuple[TrainingSource, ...]
    environments: tuple[TrainingVenv, ...]


@dataclass(frozen=True)
class TrainingAsset:
    artifact: ModelArtifact
    target: Path


class TrainingOwnership(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1] = 1
    root: str
    recipe: str = Field(pattern=r'^[0-9a-f]{64}$')
    fingerprint: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')


def recipes() -> dict[MusicTrainingEngine, TrainingRecipe]:
    return {
        'yue2': TrainingRecipe((
            TrainingSource('trainer', SourcePin('https://github.com/speedyrulz/ComfyUI-YuE2-Trainer.git', '6a9355efff60589d93b41b569676f09d5db85d09', '3.12.13'), 'train_cli.py'),
            TrainingSource('comfy', SourcePin('https://github.com/Comfy-Org/ComfyUI.git', 'b058ec652802e1de24f1d429b3c7c1fb868f791c', '3.12.13'), 'comfy/ldm/yue2/model.py')),
            (TrainingVenv('venv', 'music-training-yue2-requirements.txt', ('comfy', 'trainer'), 'import torch, comfy.sd; from comfy.ldm.yue2.model import model_config; from yue2_trainer.lora import create_lora; from yue2_trainer.parallel import resolve_devices; assert resolve_devices("mps")[0].type == "mps"; assert model_config().hidden_size == 2048'),)),
    }


def training_root(environment: ModuleEnvironment, engine: MusicTrainingEngine) -> Path:
    return _path(environment.root, 'training/' + engine, writing=True)


def _python(root: Path, folder: str, platform: str) -> Path:
    return root / folder / ('Scripts/python.exe' if platform == 'win32' else 'bin/python')


def _published_artifacts(engine: MusicTrainingEngine) -> list[ModelArtifact]:
    revision = '2f76ca75e6ee094169de899cc7fc99d6887e2196'
    artifact = ModelArtifact(path='training-assets/yue2/' + revision + '/yue2_3b_bf16.safetensors',
        url='https://huggingface.co/Comfy-Org/YuE2/resolve/' + revision + '/checkpoints/yue2_3b_bf16.safetensors',
        sha256='33765adbf9813c9a50318218760b2fd819a319862460a04884607581961c6fee', bytes=7799983228)
    return [artifact]


def training_assets(environment: ModuleEnvironment, engine: MusicTrainingEngine) -> list[TrainingAsset]:
    return [TrainingAsset(item, _path(environment.root, item.path, writing=True)) for item in _published_artifacts(engine)]


def recipe_hash(engine: MusicTrainingEngine) -> str:
    recipe = recipes()[engine]
    digest = hashlib.sha256(b'openfabric-training-recipe-2')
    digest.update(repr(recipe).encode())
    for venv in recipe.environments:
        digest.update((_ASSETS / venv.requirements).read_bytes())
    artifacts = _published_artifacts(engine)
    for artifact in artifacts:
        digest.update(artifact.model_dump_json().encode())
    return digest.hexdigest()


def _receipt_path(environment: ModuleEnvironment, engine: MusicTrainingEngine) -> Path:
    return _path(environment.root, '.setup/owned-training/' + engine + '.json', writing=True)


def _owned(environment: ModuleEnvironment, engine: MusicTrainingEngine) -> TrainingOwnership | None:
    path = _receipt_path(environment, engine)
    try:
        if path.stat().st_size > 16384:
            return None
        value = TrainingOwnership.model_validate_json(path.read_bytes())
        return value if value.root == str(training_root(environment, engine)) and value.recipe in (recipe_hash(engine), _LEGACY_RECIPES[engine]) else None
    except (OSError, ValueError, ValidationError):
        return None


def _fingerprint(environment: ModuleEnvironment, engine: MusicTrainingEngine) -> str | None:
    root = training_root(environment, engine)
    digest = hashlib.sha256()
    try:
        for source in recipes()[engine].sources:
            checkout = _path(root, source.folder)
            head = _path(checkout, '.git/HEAD')
            if head.stat().st_size > 100 or head.read_text().strip() != source.pin.commit:
                return None
            marker = _path(checkout, source.marker)
            if not marker.is_file():
                return None
            for index, path in enumerate(sorted(checkout.rglob('*.py'))):
                if index >= 5000:
                    return None
                confined = _path(checkout, str(path.relative_to(checkout)))
                stat = confined.stat()
                digest.update(f'{path.relative_to(checkout)}:{stat.st_size}:{stat.st_mtime_ns}'.encode())
        for venv in recipes()[engine].environments:
            python = _path(environment.root, str(_python(root, venv.folder, environment.platform).relative_to(environment.root)))
            stamp = python.stat()
            digest.update(f'{python.resolve()}:{stamp.st_size}:{stamp.st_mtime_ns}'.encode())
            sites = sorted((root / venv.folder / 'lib').glob('python*/site-packages'))
            if not sites or len(sites) > 4:
                return None
            for site in sites:
                for index, child in enumerate(sorted(site.iterdir())):
                    if index >= 5000:
                        return None
                    if child.name == '__pycache__' or child.suffix == '.pyc':
                        continue
                    if child.is_dir():
                        digest.update(f'{child.name}:directory'.encode())
                    else:
                        stamp = child.stat()
                        digest.update(f'{child.name}:{stamp.st_size}:{stamp.st_mtime_ns}'.encode())
                    if child.name.endswith('.dist-info'):
                        for name in ('METADATA', 'RECORD', 'direct_url.json'):
                            metadata = _path(root, str((child / name).relative_to(root)))
                            if metadata.is_file():
                                stamp = metadata.stat()
                                digest.update(f'{metadata.relative_to(root)}:{stamp.st_size}:{stamp.st_mtime_ns}'.encode())
        return digest.hexdigest()
    except (OSError, ModuleSetupError):
        return None


def environment_ready(environment: ModuleEnvironment, engine: MusicTrainingEngine) -> bool:
    try:
        owned = _owned(environment, engine)
        return owned is not None and owned.recipe == recipe_hash(engine) and owned.fingerprint is not None and owned.fingerprint == _fingerprint(environment, engine)
    except (OSError, ModuleSetupError):
        return False


def weights_ready(environment: ModuleEnvironment, engine: MusicTrainingEngine) -> bool:
    try:
        assets = training_assets(environment, engine)
        return bool(assets) and all(_valid_file(item.target) for item in assets)
    except (OSError, ModuleSetupError):
        return False


def prepared(environment: ModuleEnvironment, engine: MusicTrainingEngine) -> bool:
    return environment_ready(environment, engine) and weights_ready(environment, engine)


def _licenses(engine: MusicTrainingEngine) -> list[ModuleLicenseDeclaration]:
    rows: list[tuple[str, str, LicenseScope, str, str]] = [
        ('yue-trainer', 'Headless YuE2 trainer', 'code', 'Apache-2.0', 'https://github.com/speedyrulz/ComfyUI-YuE2-Trainer/blob/6a9355efff60589d93b41b569676f09d5db85d09/LICENSE'),
        ('comfy-training', 'ComfyUI YuE2 model runtime', 'code', 'GPL-3.0', 'https://github.com/Comfy-Org/ComfyUI/blob/b058ec652802e1de24f1d429b3c7c1fb868f791c/LICENSE'),
        ('yue-training-base', 'YuE2 BF16 training checkpoint', 'model', 'CC-BY-NC-4.0', 'https://github.com/multimodal-art-projection/YuE/blob/1647252d68b70cbead046ffd3f7caf036dde47ee/MODEL_LICENSE')]
    return [ModuleLicenseDeclaration(component_id=identifier, name=name, scope=scope, status='declared', declared_license=license_name,
        source_url=url, notes='Code and model terms are separate. YuE2 permits some creator output monetization; this does not establish commercial adapter redistribution rights.', reviewed_at='2026-10-15')
        for identifier, name, scope, license_name, url in rows]


def plan(request: ModulePlanRequest, environment: ModuleEnvironment) -> ModulePlan:
    from .module_catalog import approval_scope
    from .module_install import native_tools_need_repair
    engine = request.music_training_engine
    if engine is None:
        raise ModuleSetupError('plan_changed')
    root = training_root(environment, engine)
    assets = training_assets(environment, engine)
    missing = [item for item in assets if not _valid_file(item.target)]
    for item in missing:
        _preflight(environment.root, [str(item.target.relative_to(environment.root))])
    size = sum(item.artifact.bytes for item in missing)
    installed = environment_ready(environment, engine)
    owned = _owned(environment, engine)
    conflict = root.exists() and (not root.is_dir() or any(root.iterdir())) and owned is None
    support = platform_key(environment) == 'darwin-arm64'
    prerequisites = environment.uv is not None and shutil.which('git') is not None
    repair = native_tools_need_repair(environment)
    operation: Literal['unsupported', 'manual', 'install', 'verify', 'reuse'] = 'unsupported' if not support else 'manual' if conflict or not installed and not prerequisites else 'install' if not installed else 'verify' if missing or repair else 'reuse'
    required = 2 * size + (0 if installed else 8 * 1024**3)
    spaces = [available_bytes(path) for path in {environment.root, root, *(item.target.parent for item in missing)}]
    free = min(space for space in spaces if space is not None) if all(space is not None for space in spaces) else None
    enough = free is not None and free >= required
    detail = ('Existing unowned training files were preserved; use a new managed location or review those files manually.' if conflict else
        'Prepare isolated pinned training dependencies and required models. Existing generation source, environments, models and user data are preserved.')
    step = ModulePlanStep(module_id=engine, name='YuE2 training preparation',
        operation=operation, estimated_download_bytes=size, detail=detail, actions=[], licenses=_licenses(engine))
    warnings = ['Training environments and checkpoint files are separate from generation.',
        'Training setup is experimental on Apple Silicon. Installation does not establish musical quality or adapter compatibility.']
    if not installed:
        warnings.append('Pinned source and Python dependency download sizes are unknown. An additional 8 GiB free-space allowance is reserved; actual requirements may be larger.')
    if not installed and not prerequisites:
        warnings.append('Managed training setup requires uv and Git.')
    if not enough:
        warnings.append('Insufficient or unverified free space in a training, model or cache location.')
    canonical = {'scope': approval_scope(request, environment), 'steps': [step.model_dump(mode='json')], 'copies': 0}
    token = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return ModulePlan(features=request.features, download_models=True, music_training_engine=engine, plan_token=token,
        steps=[step], estimated_download_bytes=size, download_size_unknown=not installed, required_free_bytes=required,
        free_bytes=free, can_install=enough and operation in ('install', 'verify'), warnings=warnings)


async def prepare(context: InstallContext, engine: MusicTrainingEngine) -> InstallOutcome:
    from .module_install import _clone, download_artifact, repair_native_tools
    environment = context.environment
    if platform_key(environment) != 'darwin-arm64':
        raise ModuleSetupError('unsupported_platform')
    repair_native_tools(context)
    root = training_root(environment, engine)
    owned = _owned(environment, engine)
    if root.exists() and (not root.is_dir() or any(root.iterdir())) and owned is None:
        raise ModuleSetupError('installation_conflict')
    assets = training_assets(environment, engine)
    for item in assets:
        if not _valid_file(item.target):
            _preflight(environment.root, [str(item.target.relative_to(environment.root))])
    if not environment_ready(environment, engine):
        legacy = owned is not None and owned.recipe == _LEGACY_RECIPES[engine]
        legacy_ready = legacy and owned is not None and owned.fingerprint is not None and owned.fingerprint == _fingerprint(environment, engine)
        uv = environment.uv
        if uv is None or shutil.which('git') is None:
            raise ModuleSetupError('environment_unverified')
        marker = _receipt_path(environment, engine)
        marker.parent.mkdir(parents=True, exist_ok=True)
        if not legacy:
            write_object(marker, {'schema_version': 1, 'root': str(root), 'recipe': recipe_hash(engine), 'fingerprint': None})
        root.mkdir(parents=True, exist_ok=True)
        for source in recipes()[engine].sources:
            target = _path(root, source.folder, writing=True)
            context.update_detail('Checking pinned training source: ' + source.folder + '.')
            if not target.exists():
                await _clone(context, engine, source.pin, target)
            head = _path(target, '.git/HEAD')
            if not head.is_file() or head.stat().st_size > 100 or head.read_text().strip() != source.pin.commit:
                raise ModuleSetupError('installation_conflict')
            await context.run(['git', '-C', str(target), 'diff-index', '--quiet', 'HEAD', '--'], timeout=30)
        for venv in recipes()[engine].environments:
            target = _path(root, venv.folder, writing=True)
            python = _python(root, venv.folder, environment.platform)
            child_env = {**_uv_env(context), 'PYTHONPATH': os.pathsep.join(str(_path(root, path)) for path in venv.pythonpath),
                'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}
            context.update_detail(('Checking existing pinned training packages: ' if legacy_ready else 'Installing pinned isolated training packages: ') + venv.folder + '.')
            if not python.is_file():
                await context.run([uv, 'venv', '--python', recipes()[engine].sources[0].pin.python, str(target)], env=_uv_env(context))
            _path(environment.root, str(python.relative_to(environment.root)))
            if not legacy_ready:
                await context.run([uv, 'pip', 'install', '--python', str(python), '-r', str(_ASSETS / venv.requirements)], env=_uv_env(context))
            await context.run([uv, 'pip', 'check', '--python', str(python)], env=_uv_env(context), timeout=60)
            await context.run([str(python), '-c', venv.check], cwd=_path(root, venv.pythonpath[0]), env=child_env, timeout=120)
        fingerprint = _fingerprint(environment, engine)
        if fingerprint is None:
            raise ModuleSetupError('environment_unverified')
        write_object(marker, {'schema_version': 1, 'root': str(root), 'recipe': recipe_hash(engine), 'fingerprint': fingerprint})
    for item in assets:
        if _valid_file(item.target):
            continue
        context.update_detail('Preparing training model: ' + item.target.name + '.')
        model_source = context.confined(environment.root / 'cache/music-models' / item.artifact.sha256 / item.target.name)
        await download_artifact(DownloadArtifact(url=item.artifact.url, sha256=item.artifact.sha256, bytes=item.artifact.bytes), model_source, context, on_progress=context.update_download)
        if not _valid_file(model_source):
            raise ModuleSetupError('artifact_integrity')
        relative = str(item.target.relative_to(environment.root))
        await await_cleanup(asyncio.to_thread(_publish, model_source, environment.root, relative))
    if not prepared(environment, engine):
        raise ModuleSetupError('environment_unverified')
    return InstallOutcome('verified', 'Pinned isolated training sources, dependencies and model files are prepared. Generation files were preserved. Training and generated quality remain separate checks.')
