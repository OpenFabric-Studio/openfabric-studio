"""Resolve recording references on the backend; reject traversal and symlink escapes."""
from __future__ import annotations

from pathlib import Path
from typing import Literal
from . import db
from .music_lora_contracts import MusicLoraError
from .music_training_source_contracts import MusicLoraSource


def source_path(sample: MusicLoraSource, *, prefix: Literal['stable_lora', 'music_training']) -> Path:
    if sample.regional_manifest is not None:
        if prefix != 'stable_lora':
            raise MusicLoraError(prefix + '_dataset_invalid', 422)
        from .regional_datasets import training_excerpt_source
        from .regional_dataset_contracts import RegionalDatasetError
        reference = sample.regional_manifest
        try:
            return training_excerpt_source(reference.manifest_id, reference.manifest_sha256, reference.excerpt_id).path
        except (RegionalDatasetError, OSError, RuntimeError, ValueError) as error:
            raise MusicLoraError(prefix + '_dataset_changed', 409) from error
    if sample.track_id is not None:
        row = db.get_track(sample.track_id)
        value: object = row['audio_path'] if row is not None else None
        if not isinstance(value, str):
            raise MusicLoraError(prefix + '_track_not_found', 404)
        root = db.FILES_DIR
        path = Path(value)
    else:
        from . import lora_datasets
        selection = sample.dataset
        if selection is None:
            raise MusicLoraError(prefix + '_dataset_invalid', 422)
        try:
            snapshot = lora_datasets.read_saved_dataset(selection.dataset_id, selection.revision)
            recording = next((item for item in snapshot.samples if item.id == selection.sample_id), None)
            if recording is None:
                raise MusicLoraError(prefix + '_dataset_invalid', 422)
            root = lora_datasets.datasets_root()
            path = Path(recording.audio_path)
            if not path.is_absolute():
                path = root.parent / path
        except lora_datasets.LoraDatasetError as error:
            raise MusicLoraError(prefix + '_dataset_changed', 409) from error
    if root.is_symlink() or not path.is_absolute() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_size > 512 * 1024**2 or path.suffix.lower() not in ('.wav', '.mp3', '.flac'):
        raise MusicLoraError(prefix + '_dataset_invalid', 422)
    current = path.parent
    while current.resolve() != root.resolve():
        if current.is_symlink() or current.parent == current:
            raise MusicLoraError(prefix + '_dataset_invalid', 422)
        current = current.parent
    return path.resolve()
