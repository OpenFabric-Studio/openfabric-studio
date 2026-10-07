"""Dataset provenance and honest, read-only trainer dependency checks."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Literal

from .video_contracts import CharacterDatasetArtifact, CharacterDatasetReview, CharacterTrainingProvenance, CharacterTrainingRecipe
from .video_engine import ENGINE_COMMIT, model_packs

COMPARISON_PROMPTS = [
    'A close portrait of the character looking at the camera in daylight',
    'The character turns their head left and speaks naturally, indoors',
    'The character walks slowly across a quiet room, full body view',
]


def build_provenance(root: Path, paths: list[Path], kinds: list[Literal['photo', 'clip']], review: CharacterDatasetReview, *, builtin: bool = False) -> CharacterTrainingProvenance:
    if len(paths) != len(kinds) or {item.upload_index for item in review.items} != set(range(len(paths))):
        raise ValueError('dataset_item_mismatch')
    items = {item.upload_index: item for item in review.items}
    artifacts: list[CharacterDatasetArtifact] = []
    for index, path in enumerate(paths):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('invalid_reference')
        item = items[index]
        artifacts.append(CharacterDatasetArtifact(path=str(path.relative_to(root)),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), caption=item.caption, role=item.role, kind=kinds[index]))
    training = [item for item in artifacts if item.role == 'training']
    held_out = [item for item in artifacts if item.role == 'held_out']
    if sum(item.kind == 'photo' for item in training) < 3 or not any(item.kind == 'photo' for item in held_out):
        raise ValueError('held_out_required')
    if {item.sha256 for item in training} & {item.sha256 for item in held_out}:
        raise ValueError('held_out_overlap')
    dataset = json.dumps([item.model_dump() for item in artifacts], sort_keys=True, separators=(',', ':'))
    recipe = CharacterTrainingRecipe() if builtin else None
    settings = json.dumps({'settings': review.settings.model_dump(), 'recipe': recipe.model_dump() if recipe else None}, sort_keys=True, separators=(',', ':'))
    return CharacterTrainingProvenance(engine_commit=ENGINE_COMMIT, base_revision=model_packs()['ltx23'].revision,
        dataset_sha256=hashlib.sha256(dataset.encode()).hexdigest(), settings_sha256=hashlib.sha256(settings.encode()).hexdigest(),
        settings=review.settings, recipe=recipe, artifacts=artifacts, comparison_prompts=list(COMPARISON_PROMPTS))


def dependency_status(command: Path) -> tuple[bool, str]:
    if command.name.lower() not in {'python', 'python3', 'python.exe', 'python3.exe'}:
        return False, 'custom_trainer_unverified'
    # find_spec does not import model runtimes, allocate Metal memory or fetch weights.
    source = "import importlib.util,json;print(json.dumps({name:importlib.util.find_spec(name) is not None for name in ['mlx','ltx_trainer_mlx','ltx_pipelines_mlx']}))"
    env = {**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    try:
        result = subprocess.run([str(command), '-I', '-B', '-c', source], capture_output=True, text=True, timeout=5, env=env)
        value: object = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return False, 'trainer_dependencies_missing'
    if not isinstance(value, dict) or set(value) != {'mlx', 'ltx_trainer_mlx', 'ltx_pipelines_mlx'} or any(item is not True for item in value.values()):
        return False, 'trainer_dependencies_missing'
    return True, ''
