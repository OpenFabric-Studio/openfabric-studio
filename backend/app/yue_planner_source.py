"""Verify the saved NAR run before reusing its exact prepared audio for AR."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, StrictBool
from scripts.stable_lora_worker import digest

from . import music_lora
from .music_lora_contracts import MusicAdapter, MusicAdapterSelection
from .music_training_contracts import MusicLoraTrainingCompletion, MusicLoraTrainingJob, MusicLoraTrainingRequest
from .music_training_runtime import artifact_base, fingerprint
from .yue_lora_convert import BASE_SHA256
from .yue_lora_runtime import PreparedAudio, validate_prepared_audio, validate_score


class _SavedRequest(BaseModel):
    request: MusicLoraTrainingRequest
    request_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class _SavedSample(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    sample: str = Field(min_length=1, max_length=100)
    track_id: int | None = Field(default=None, strict=True, ge=1)
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    audio_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    caption: str = Field(max_length=4000)
    lyrics: str = Field(max_length=20000)
    instrumental: StrictBool
    original_duration_seconds: float | None = Field(default=None, strict=True, ge=.1)
    offset_seconds: int | None = Field(default=None, strict=True, ge=0, le=0)
    segment_seconds: int | None = Field(default=None, strict=True, ge=6, le=30)
    score: str | None = None
    score_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')


class _SavedDataset(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    samples: list[_SavedSample] = Field(min_length=1, max_length=100)


@dataclass(frozen=True)
class PlannerSource:
    adapter: MusicAdapter
    audio: tuple[PreparedAudio, ...]


def saved_file(directory: Path, name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or '\\' in name or any(part in ('.', '..') for part in relative.parts):
        raise ValueError('music_training_planner_source_unavailable')
    path = directory / relative
    if not path.is_file() or path.resolve() != path or not path.is_relative_to(directory):
        raise ValueError('music_training_planner_source_unavailable')
    return path


def _small_file(directory: Path, name: str) -> bytes:
    path = saved_file(directory, name)
    if path.stat().st_size > 2 * 1024**2:
        raise ValueError('music_training_planner_source_unavailable')
    return path.read_bytes()


def _dataset(job: MusicLoraTrainingJob, directory: Path) -> _SavedDataset:
    manifest = _SavedDataset.model_validate_json(_small_file(directory, 'dataset.json'))
    if manifest.request_sha256 != fingerprint(job.request) or len(manifest.samples) != len(job.request.samples):
        raise ValueError('music_training_planner_source_unavailable')
    for index, (entry, sample) in enumerate(zip(manifest.samples, job.request.samples, strict=True)):
        if (entry.sample != f'{index:03d}.wav' or entry.track_id != sample.track_id
                or entry.caption != sample.caption or entry.lyrics != sample.lyrics or entry.instrumental != sample.instrumental
                or entry.segment_seconds not in (None, job.request.segment_seconds)):
            raise ValueError('music_training_planner_source_unavailable')
    return manifest


def dataset_audio(job: MusicLoraTrainingJob, directory: Path, *, expected: tuple[PreparedAudio, ...] | None = None) -> tuple[PreparedAudio, ...]:
    manifest = _dataset(job, directory)
    output: list[PreparedAudio] = []
    for index, entry in enumerate(manifest.samples):
        if (expected is not None and (entry.segment_seconds is None or entry.offset_seconds is None or entry.original_duration_seconds is None)
                or expected is not None and (entry.audio_sha256 != expected[index].sha256 or entry.source_sha256 != expected[index].sha256)):
            raise ValueError('music_training_planner_source_unavailable')
        audio = PreparedAudio(saved_file(directory, 'audio/' + entry.sample), entry.audio_sha256)
        validate_prepared_audio(audio, segment_seconds=job.request.segment_seconds)
        if expected is not None:
            if entry.score != f'{index:03d}.abc' or entry.score_sha256 is None:
                raise ValueError('music_training_planner_source_unavailable')
            score = saved_file(directory, 'audio/' + entry.score)
            validate_score(score)
            if digest(score, 256 * 1024) != entry.score_sha256:
                raise ValueError('music_training_planner_source_unavailable')
        output.append(audio)
    return tuple(output)


def _parent_completion(job: MusicLoraTrainingJob, directory: Path) -> MusicLoraTrainingCompletion:
    if (job.status != 'done' or job.kind != 'training' or job.request.engine != 'yue2'
            or job.request.yue_pair is not None or job.request.yue_ar_source_job_id is not None
            or job.adapter is None or job.adapter.engine != 'yue2' or job.adapter.target != 'nar'):
        raise ValueError('music_training_planner_source_unavailable')
    snapshot = _SavedRequest.model_validate_json(_small_file(directory, 'request.json'))
    expected = fingerprint(job.request)
    completion = MusicLoraTrainingCompletion.model_validate_json(_small_file(directory, 'completion.json'))
    if (snapshot.request != job.request or snapshot.request_sha256 != expected or completion.job_id != job.id
            or completion.engine != 'yue2' or completion.request_sha256 != expected
            or completion.max_steps != job.request.max_steps or completion.base_sha256 != BASE_SHA256):
        raise ValueError('music_training_planner_source_unavailable')
    return completion


def _parent_adapter(job: MusicLoraTrainingJob, completion: MusicLoraTrainingCompletion) -> MusicAdapter:
    registered = music_lora.find_by_job(job.id, target='nar')
    if registered is None or registered != job.adapter or registered.sha256 != completion.sha256:
        raise ValueError('music_training_planner_source_unavailable')
    return registered


def available(job: MusicLoraTrainingJob, directory: Path) -> bool:
    """Polling only checks metadata and presence; Start verifies all file contents."""
    completion = _parent_completion(job, directory)
    manifest = _dataset(job, directory)
    if saved_file(directory, completion.checkpoint).suffix != '.safetensors':
        return False
    for entry in manifest.samples:
        saved_file(directory, 'audio/' + entry.sample)
    registered = _parent_adapter(job, completion)
    saved_file(music_lora._directory(registered.id), 'adapter.safetensors')
    return True


def verify(job: MusicLoraTrainingJob, directory: Path) -> PlannerSource:
    completion = _parent_completion(job, directory)
    if completion.dataset_sha256 != digest(saved_file(directory, 'dataset.json'), 2 * 1024**2):
        raise ValueError('music_training_planner_source_unavailable')
    checkpoint = saved_file(directory, completion.checkpoint)
    if (checkpoint.suffix != '.safetensors' or digest(checkpoint) != completion.sha256
            or artifact_base(checkpoint, job.request) != BASE_SHA256):
        raise ValueError('music_training_planner_source_unavailable')
    music_lora.validate_file(checkpoint, engine='yue2', target='nar')
    registered = _parent_adapter(job, completion)
    music_lora.resolve(MusicAdapterSelection(id=registered.id), engine='yue2', target='nar')
    return PlannerSource(registered, dataset_audio(job, directory))
