"""Bounded fresh-run training for the reviewed YuE2 worker."""
from __future__ import annotations

from typing import Literal, Self
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from .contracts import Contract
from .music_lora_contracts import MusicAdapter
from .music_training_source_contracts import MusicLoraSource, source_key

MusicTrainingEngine = Literal['yue2']
MusicTrainingStage = Literal['preparing', 'transcribing', 'encoding', 'loading', 'training', 'publishing', 'done']
MusicTrainingKind = Literal['training', 'nvidia-package']


class MusicLoraTrainingSample(MusicLoraSource):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    caption: str = Field(min_length=1, max_length=4000)
    lyrics: str = Field(default='', max_length=20000)
    instrumental: StrictBool = False

    @model_validator(mode='after')
    def valid_description(self) -> Self:
        if not self.caption.strip() or self.instrumental and self.lyrics.strip():
            raise ValueError('music_training_sample_invalid')
        return self


class YueLoraPairSettings(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    planner_steps: int = Field(default=100, strict=True, ge=1, le=100000)
    planner_learning_rate: float = Field(default=0.00005, strict=True, ge=1e-7, le=0.01)


class MusicLoraTrainingRequest(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    engine: MusicTrainingEngine
    name: str = Field(min_length=1, max_length=120)
    samples: list[MusicLoraTrainingSample] = Field(min_length=1, max_length=100)
    max_steps: int = Field(default=1000, ge=1, le=100000, strict=True)
    rank: Literal[8, 16, 32] = 16
    learning_rate: float = Field(default=0.0001, ge=0.0000001, le=0.01, strict=True)
    seed: int = Field(default=42, ge=0, le=2147483647, strict=True)
    segment_seconds: int = Field(default=12, ge=6, le=30, strict=True)
    yue_pair: YueLoraPairSettings | None = None
    yue_ar_source_job_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')

    @model_validator(mode='after')
    def valid_dataset(self) -> Self:
        if not self.name.strip() or len({source_key(sample) for sample in self.samples}) != len(self.samples):
            raise ValueError('music_training_dataset_invalid')
        if self.yue_ar_source_job_id is not None and self.yue_pair is not None:
            raise ValueError('music_training_planner_request_invalid')
        if self.yue_pair is None and self.yue_ar_source_job_id is None and any(sample.lyrics.strip() for sample in self.samples):
            raise ValueError('yue_acoustic_training_lyrics_unsupported')
        return self


class MusicLoraTrainingProgress(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    stage: MusicTrainingStage = 'preparing'
    target: Literal['ar', 'nar'] | None = None
    step: int | None = Field(default=None, strict=True, ge=0, le=100000)
    total_steps: int | None = Field(default=None, strict=True, ge=1, le=100000)
    loss: float | None = None
    seconds_per_step: float | None = Field(default=None, strict=True, gt=0, le=86400)
    updated_at: str | None = Field(default=None, max_length=64)

    @model_validator(mode='after')
    def bounded_step(self) -> Self:
        if self.step is not None and self.total_steps is not None and self.step > self.total_steps:
            raise ValueError('music_training_progress_invalid')
        return self


class MusicLoraTrainingCompletion(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    job_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    engine: MusicTrainingEngine
    request_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    checkpoint: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    dataset_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    base_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    max_steps: int = Field(strict=True, ge=1, le=100000)


class MusicLoraTrainingPairArtifact(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    target: Literal['ar', 'nar']
    checkpoint: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class MusicLoraTrainingPairCompletion(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    job_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    engine: Literal['yue2'] = 'yue2'
    request_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    dataset_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    base_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    max_steps: int = Field(strict=True, ge=1, le=100000)
    planner_steps: int = Field(strict=True, ge=1, le=100000)
    artifacts: list[MusicLoraTrainingPairArtifact] = Field(min_length=2, max_length=2)

    @model_validator(mode='after')
    def complete_targets(self) -> Self:
        if {artifact.target for artifact in self.artifacts} != {'ar', 'nar'}:
            raise ValueError('music_training_completion_invalid')
        return self


class MusicLoraTrainingStatus(Contract):
    engine: MusicTrainingEngine
    supported: bool
    installed: bool
    weights_ready: bool
    paired_ready: bool | None = None
    device: Literal['mps', 'cuda'] | None = None
    validation: Literal['experimental', 'verified'] = 'experimental'
    error_code: str | None = Field(default=None, max_length=80)


class MusicLoraPackageCompletion(Contract):
    model_config = ConfigDict(extra='forbid')
    job_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    engine: MusicTrainingEngine
    request_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    archive: Literal['package.zip']
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    bytes: int = Field(strict=True, ge=1, le=64 * 1024**3)


class MusicLoraTrainingStatuses(Contract):
    engines: list[MusicLoraTrainingStatus] = Field(max_length=1)


class MusicLoraTrainingJob(Contract):
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    status: Literal['queued', 'running', 'done', 'failed', 'cancelled', 'interrupted']
    created_at: str
    started_at: str | None = Field(default=None, max_length=64)
    finished_at: str | None = Field(default=None, max_length=64)
    request: MusicLoraTrainingRequest
    kind: MusicTrainingKind = 'training'
    progress: MusicLoraTrainingProgress = Field(default_factory=MusicLoraTrainingProgress)
    adapter: MusicAdapter | None = None
    adapters: list[MusicAdapter] = Field(default_factory=list, max_length=2)
    can_train_missing_ar: bool = False  # Availability hint; Start verifies the saved files.
    package_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    error_code: str | None = Field(default=None, max_length=80)


class MusicLoraTrainingJobs(Contract):
    jobs: list[MusicLoraTrainingJob] = Field(max_length=1000)
    history_incomplete: bool = False


MUSIC_TRAINING_CLIENT_MODELS: list[type[BaseModel]] = [MusicLoraTrainingSample, YueLoraPairSettings, MusicLoraTrainingRequest,
    MusicLoraTrainingProgress, MusicLoraTrainingStatus, MusicLoraTrainingStatuses, MusicLoraTrainingJob, MusicLoraTrainingJobs]
