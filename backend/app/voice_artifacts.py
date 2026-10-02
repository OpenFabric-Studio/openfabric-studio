"""Contained immutable model artifacts and atomic active-model publication."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import Field, TypeAdapter

from .atomic_files import JsonObject, document_lock, read_object, write_object
from .contracts import Contract
from .voice_contracts import VoiceModelChoice


class StoredModel(Contract):
    id: str
    steps: int = Field(ge=0)
    kind: Literal['base', 'trained']
    checkpoint: str | None = None
    config: str | None = None
    reference: str
    preview: str
    resume_path: str | None = None
    preparation_revision: str = ''
    selection_fingerprint: str = ''
    model_signature: str = ''
    base_filename: str = ''
    base_sha256: str = ''


class ArtifactRegistry(Contract):
    active_model_id: str = ''
    models: list[StoredModel] = Field(default_factory=list)


@dataclass(frozen=True)
class ModelArtifact:
    checkpoint: Path | None
    config: Path | None
    steps: int
    kind: Literal['base', 'trained']
    reference: Path
    resume_path: Path | None = None


def contained_file(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or '..' in candidate.parts:
        raise HTTPException(status_code=404, detail='artifact_missing')
    path = root / candidate
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail='artifact_missing') from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail='artifact_missing')
    return path


def load_registry(path: Path) -> ArtifactRegistry:
    source = path / 'artifacts.json'
    if source.is_file():
        try:
            return ArtifactRegistry.model_validate(read_object(source))
        except (ValueError, OSError):
            raise HTTPException(status_code=409, detail='invalid_voice_metadata') from None
    return ArtifactRegistry()


def save_registry(path: Path, registry: ArtifactRegistry) -> None:
    payload = TypeAdapter(JsonObject).validate_python(registry.model_dump(mode='json'))
    write_object(path / 'artifacts.json', payload)


def choices(path: Path) -> tuple[list[VoiceModelChoice], str]:
    registry = load_registry(path)
    return [VoiceModelChoice(id=row.id, steps=row.steps, kind=row.kind, resume_available=bool(row.resume_path and (path / row.resume_path).is_file())) for row in registry.models], registry.active_model_id


def resolve_model_artifact(voice_path: Path, model_id: str) -> ModelArtifact:
    row = next((item for item in load_registry(voice_path).models if item.id == model_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail='invalid_model')
    return ModelArtifact(
        checkpoint=contained_file(voice_path, row.checkpoint) if row.checkpoint else None,
        config=contained_file(voice_path, row.config) if row.config else None,
        steps=row.steps, kind=row.kind, reference=contained_file(voice_path, row.reference),
        resume_path=contained_file(voice_path, row.resume_path) if row.resume_path else None,
    )


def publish(path: Path, models: list[StoredModel], active_model_id: str) -> None:
    # The registry is the single publication point: files are immutable and
    # already complete before an active identity changes.
    with document_lock(path / 'artifacts.json'):
        registry = load_registry(path)
        identifiers = {item.id for item in models}
        merged = [item for item in registry.models if item.id not in identifiers] + models
        if active_model_id not in {item.id for item in merged}:
            raise HTTPException(status_code=409, detail='invalid_model')
        for row in models:
            contained_file(path, row.reference)
            contained_file(path, row.preview)
            if row.kind == 'trained' or row.checkpoint or row.config:
                if not row.checkpoint or not row.config:
                    raise HTTPException(status_code=409, detail='invalid_model')
                contained_file(path, row.checkpoint)
                contained_file(path, row.config)
        save_registry(path, ArtifactRegistry(active_model_id=active_model_id, models=merged))


def activate(path: Path, model_id: str) -> None:
    resolve_model_artifact(path, model_id)
    with document_lock(path / 'artifacts.json'):
        registry = load_registry(path)
        registry.active_model_id = model_id
        save_registry(path, registry)
