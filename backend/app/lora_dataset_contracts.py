"""Bounded app-owned dataset catalog and recoverable LoRA workspace contracts."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .contracts import Contract


class DatasetContract(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class LoraDatasetRevision(DatasetContract):
    revision: int = Field(ge=0, le=9007199254740991)


class CreateLoraDatasetRequest(DatasetContract):
    name: str = Field(min_length=1, max_length=80)

    @field_validator('name')
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError('dataset_name_invalid')
        return value.strip()


class RenameLoraDatasetRequest(CreateLoraDatasetRequest, LoraDatasetRevision):
    pass


class LoraDatasetEntry(DatasetContract):
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    name: str = Field(min_length=1, max_length=80)
    dataset_path: str = Field(min_length=1, max_length=4096)
    audio_dir: str = Field(min_length=1, max_length=4096)
    sample_count: int = Field(ge=0, le=100000)
    labeled_count: int = Field(ge=0, le=100000)
    revision: int = Field(ge=0, le=9007199254740991)
    trashed: bool = False
    available: bool = True
    error_code: str = Field(default='', max_length=120)


class LoraDatasetIssue(DatasetContract):
    path: str = Field(max_length=4096)
    code: Literal['dataset_invalid', 'dataset_path_unsafe', 'dataset_unavailable']


class LoraDatasetsResponse(DatasetContract):
    datasets: list[LoraDatasetEntry] = Field(max_length=10000)
    issues: list[LoraDatasetIssue] = Field(default_factory=list, max_length=10000)


class LoraWorkspaceForm(DatasetContract):
    audio_dir: str = Field(default='datasets/my-voice', max_length=4096)
    custom_tag: str = Field(default='', max_length=200)
    tag_position: Literal['prepend', 'append', 'replace'] = 'replace'
    all_instrumental: bool = True
    skip_metas: bool = False
    format_lyrics: bool = False
    transcribe_lyrics: bool = False
    only_unlabeled: bool = True
    genre_ratio: int = Field(default=0, ge=0, le=100)
    output_dir: str = Field(default='', max_length=4096)
    skip_existing: bool = False
    tensor_dir: str = Field(default='', max_length=4096)
    lora_output_dir: str = Field(default='./lora_output', max_length=4096)
    lora_rank: int = Field(default=64, ge=1, le=256)
    lora_alpha: int = Field(default=128, ge=1, le=512)
    lora_dropout: float = Field(default=0.1, ge=0, le=1)
    learning_rate: float = Field(default=0.0001, gt=0, le=1)
    train_epochs: int = Field(default=10, ge=1, le=100000)
    train_batch_size: int = Field(default=1, ge=1, le=1024)
    gradient_accumulation: int = Field(default=4, ge=1, le=10000)
    save_every_n_epochs: int = Field(default=5, ge=1, le=100000)
    training_seed: int = Field(default=42, ge=0, le=4294967295)
    use_fp8: bool = False
    gradient_checkpointing: bool = False
    export_path: str = Field(default='', max_length=4096)
    registry_name: str = Field(default='', max_length=120)


class LoraObservedTask(DatasetContract):
    task_id: str = Field(default='', max_length=200)
    dataset_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    dataset_path: str = Field(min_length=1, max_length=4096)
    output_dir: str = Field(default='', max_length=4096)


class LoraPreprocessReceipt(DatasetContract):
    dataset_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    dataset_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    model_identity: str = Field(min_length=1, max_length=4096)
    output_dir: str = Field(min_length=1, max_length=4096)
    task_id: str = Field(default='', max_length=200)


class LoraWorkspace(LoraDatasetRevision):
    revision: int = Field(default=0, ge=0, le=9007199254740991)
    selected_dataset_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    training_tensor_dir: str = Field(default='', max_length=4096)
    form: LoraWorkspaceForm = Field(default_factory=LoraWorkspaceForm)
    auto_label_task: LoraObservedTask | None = None
    preprocess_task: LoraObservedTask | None = None
    preprocess_receipt: LoraPreprocessReceipt | None = None


class ScanLoraDatasetRequest(LoraDatasetRevision):
    audio_dir: str | None = Field(default=None, max_length=4096)
    custom_tag: str = Field(default='', max_length=200)
    tag_position: Literal['prepend', 'append', 'replace'] = 'replace'
    all_instrumental: bool = True


class SaveLoraDatasetRequest(LoraDatasetRevision):
    custom_tag: str = Field(default='', max_length=200)
    tag_position: Literal['prepend', 'append', 'replace'] = 'replace'
    all_instrumental: bool = True
    genre_ratio: int = Field(default=0, ge=0, le=100)


class LoraDatasetSample(Contract):
    index: int = Field(default=0, ge=0, le=100000)
    id: str = Field(min_length=1, max_length=200, pattern=r'^[A-Za-z0-9_-]+$')
    filename: str = Field(default='', max_length=4096)
    audio_path: str = Field(default='', max_length=4096)
    duration: float = Field(default=0, ge=0, le=86400)
    caption: str = Field(default='', max_length=100000)
    genre: str = Field(default='', max_length=10000)
    prompt_override: Literal['caption', 'genre'] | None = None
    lyrics: str = Field(default='[Instrumental]', max_length=200000)
    raw_lyrics: str = Field(default='', max_length=200000)
    formatted_lyrics: str = Field(default='', max_length=200000)
    bpm: int | None = Field(default=None, ge=0, le=1000)
    keyscale: str = Field(default='', max_length=200)
    timesignature: str = Field(default='', max_length=200)
    language: str = Field(default='unknown', max_length=100)
    is_instrumental: bool = True
    custom_tag: str = Field(default='', max_length=200)
    labeled: bool = False

    @field_validator('audio_path')
    @classmethod
    def valid_audio_path(cls, value: str) -> str:
        if '\x00' in value:
            raise ValueError('dataset_audio_path_invalid')
        return value


class LoraDatasetMetadata(Contract):
    custom_tag: str = Field(default='', max_length=200)
    tag_position: Literal['prepend', 'append', 'replace'] = 'replace'
    all_instrumental: bool = True
    genre_ratio: int = Field(default=0, ge=0, le=100)


class LoraDatasetOperationResponse(DatasetContract):
    metadata: LoraDatasetMetadata = Field(default_factory=LoraDatasetMetadata)
    dataset: LoraDatasetEntry
    samples: list[LoraDatasetSample] = Field(max_length=100000)
    message: str = Field(default='', max_length=2000)


class AutoLabelLoraDatasetRequest(LoraDatasetRevision):
    skip_metas: bool = False
    format_lyrics: bool = False
    transcribe_lyrics: bool = False
    only_unlabeled: bool = True


class UpdateLoraDatasetSampleRequest(LoraDatasetRevision):
    caption: str = Field(default='', max_length=100000)
    genre: str = Field(default='', max_length=10000)
    prompt_override: Literal['caption', 'genre'] | None = None
    lyrics: str = Field(default='[Instrumental]', max_length=200000)
    bpm: int | None = Field(default=None, ge=0, le=1000)
    keyscale: str = Field(default='', max_length=200)
    timesignature: str = Field(default='', max_length=200)
    language: str = Field(default='unknown', max_length=100)
    is_instrumental: bool = True


class PreprocessLoraDatasetRequest(LoraDatasetRevision):
    output_dir: str = Field(min_length=1, max_length=4096)
    skip_existing: bool = False


class LoraDatasetTaskStarted(DatasetContract):
    task_id: str = Field(max_length=200)
    message: str = Field(default='', max_length=2000)
    total: int = Field(ge=0, le=100000)


LORA_DATASET_CLIENT_MODELS: list[type[BaseModel]] = [
    LoraDatasetRevision, CreateLoraDatasetRequest, RenameLoraDatasetRequest,
    LoraDatasetEntry, LoraDatasetIssue, LoraDatasetsResponse, LoraWorkspaceForm,
    LoraObservedTask, LoraPreprocessReceipt, LoraWorkspace, ScanLoraDatasetRequest,
    SaveLoraDatasetRequest, LoraDatasetSample, LoraDatasetMetadata, LoraDatasetOperationResponse,
    PreprocessLoraDatasetRequest, LoraDatasetTaskStarted, AutoLabelLoraDatasetRequest, UpdateLoraDatasetSampleRequest,
]
