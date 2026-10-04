"""Bounded, app-owned snapshots used by generation history and saved presets."""
from __future__ import annotations

from typing import Annotated, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GenerationEngine = Literal['ace_step', 'yue2']
GenerationId = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]
GenerationRevision = Annotated[int, Field(ge=1, le=9_007_199_254_740_991)]
GenerationSeed = Annotated[int, Field(ge=-9_007_199_254_740_991, le=9_007_199_254_740_991)]
GenerationText = Annotated[str, Field(max_length=100_000)]
GenerationLabel = Annotated[str, Field(max_length=500)]


class GenerationContract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False,
                              json_schema_serialization_defaults_required=True)


class GenerationSamplingSettings(GenerationContract):
    temperature: float | None = Field(default=None, ge=0, le=5)
    top_p: float | None = Field(default=None, gt=0, le=1)
    top_k: int | None = Field(default=None, ge=1, le=100_000)
    repetition_penalty: float | None = Field(default=None, gt=0, le=100)
    penalty_window: int | None = Field(default=None, ge=1, le=9000)
    min_tokens: int | None = Field(default=None, ge=0, le=9000)
    max_tokens: int | None = Field(default=None, ge=1, le=9000)

    @model_validator(mode='after')
    def validate_token_range(self) -> Self:
        if self.min_tokens is not None and self.max_tokens is not None and self.min_tokens > self.max_tokens:
            raise ValueError('generation_invalid_token_range')
        return self


class GenerationAbcSamplingSettings(GenerationSamplingSettings):
    penalty_window: int | None = Field(default=None, ge=1, le=4096)
    min_tokens: int | None = Field(default=None, ge=0, le=4096)
    max_tokens: int | None = Field(default=None, ge=1, le=4096)


class AceGenerationSettings(GenerationContract):
    referenceImportId: GenerationId | None = None
    engine: Literal['ace_step']
    mode: Literal['simple', 'custom'] = 'simple'
    simpleQuery: GenerationText = ''
    customPrompt: GenerationText = ''
    instrumental: bool = False
    customLyrics: GenerationText = ''
    duration: float = Field(default=120, ge=10, le=300)
    durationAuto: bool = True
    audioFormat: Literal['mp3', 'wav', 'flac'] = 'mp3'
    bpm: float | None = Field(default=None, ge=0, le=1000)
    keyScale: GenerationLabel = ''
    timeSignature: GenerationLabel = ''
    vocalLanguage: GenerationLabel = ''
    inferenceSteps: int | None = Field(default=None, ge=1, le=10_000)
    guidanceScale: float | None = Field(default=None, ge=0, le=1000)
    selectedModel: GenerationLabel = ''
    seed: GenerationSeed | None = None
    randomSeed: bool = True
    batchSize: int = Field(default=1, ge=1, le=8)
    useRefAudio: bool = False
    taskType: Literal['text2music', 'cover', 'repaint', 'extract', 'lego', 'complete'] = 'text2music'
    repaintStart: float | None = Field(default=None, ge=0, le=86400)
    repaintEnd: float | None = Field(default=None, ge=0, le=86400)
    trackName: GenerationLabel = 'vocals'
    trackClasses: list[GenerationLabel] = Field(default_factory=list, max_length=100)
    coverStrength: float = Field(default=1, ge=0, le=1)
    voiceId: GenerationId | None = None
    useCotCaption: bool = True
    styleReferenceRequiresReupload: bool = False
    sourceReferenceName: GenerationLabel | None = None
    styleReferenceName: GenerationLabel | None = None
    loraRequiresReselection: bool = False
    loraName: GenerationLabel | None = None
    loraScale: float = Field(default=1, ge=0, le=2)


class YueGenerationSettings(GenerationContract):
    referenceImportId: GenerationId | None = None
    engine: Literal['yue2']
    title: GenerationLabel = ''
    lyrics: GenerationText = ''
    style: GenerationText = ''
    cot: Literal['off', 'melody', 'full'] = 'off'
    precision: Literal['q8_0', 'q4_0'] = 'q8_0'
    abc: GenerationText = ''
    cfgScale: float | None = Field(default=None, ge=0, le=20)
    numInferenceSteps: int | None = Field(default=None, ge=1, le=256)
    semantic: GenerationSamplingSettings = Field(default_factory=GenerationSamplingSettings)
    abcSampling: GenerationAbcSamplingSettings = Field(default_factory=GenerationAbcSamplingSettings)
    seed: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991)
    randomSeed: bool = False
    batchSize: int = Field(default=1, ge=1, le=4)
    voiceId: GenerationId | None = None
    referenceRequiresReupload: bool = False


GenerationSettings: TypeAlias = AceGenerationSettings | YueGenerationSettings


class GenerationHistoryEntry(GenerationContract):
    id: GenerationId
    engine: GenerationEngine
    created_at: str = Field(min_length=1, max_length=64)
    title: GenerationLabel
    lyrics: GenerationText
    seed: GenerationSeed | None
    track_id: int | None = Field(ge=1, le=9_007_199_254_740_991)
    settings: GenerationSettings
    reference_requires_reupload: bool


class GenerationHistoryResponse(GenerationContract):
    data: list[GenerationHistoryEntry]
    total: int = Field(ge=0, le=10_000)
    retention_limit: int = Field(ge=1, le=10_000)


class GenerationLibrarySettings(GenerationContract):
    history_limit: int = Field(default=100, ge=1, le=10_000)
    revision: GenerationRevision = 1


class UpdateGenerationLibrarySettingsRequest(GenerationContract):
    history_limit: int = Field(ge=1, le=10_000)
    revision: GenerationRevision


class GenerationPreset(GenerationContract):
    id: GenerationId
    name: str = Field(min_length=1, max_length=200)
    engine: GenerationEngine
    settings: GenerationSettings
    revision: GenerationRevision
    created_at: str = Field(min_length=1, max_length=64)
    updated_at: str = Field(min_length=1, max_length=64)
    source_history_id: GenerationId | None


class GenerationPresetsResponse(GenerationContract):
    data: list[GenerationPreset]
    total: int = Field(default=0, ge=0)


class CreateGenerationPresetRequest(GenerationContract):
    name: str = Field(min_length=1, max_length=200)
    settings: GenerationSettings
    source_history_id: GenerationId | None = None

    @field_validator('name')
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('generation_preset_name_required')
        return value


class UpdateGenerationPresetRequest(CreateGenerationPresetRequest):
    revision: GenerationRevision


class DuplicateGenerationPresetRequest(GenerationContract):
    name: str = Field(min_length=1, max_length=200)
    revision: GenerationRevision

    @field_validator('name')
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return CreateGenerationPresetRequest.normalize_name(value)


class ImportGenerationPreset(CreateGenerationPresetRequest):
    legacy_key: str = Field(min_length=1, max_length=500)


class ImportGenerationPresetsRequest(GenerationContract):
    presets: list[ImportGenerationPreset] = Field(max_length=1000)


class GenerationPresetImportResponse(GenerationContract):
    data: list[GenerationPreset]
    imported: int = Field(ge=0, le=1000)


class GenerationDeleteResponse(GenerationContract):
    deleted: Literal[True] = True


GENERATION_CLIENT_MODELS: list[type[BaseModel]] = [
    GenerationSamplingSettings, AceGenerationSettings, YueGenerationSettings,
    GenerationHistoryEntry, GenerationHistoryResponse, GenerationLibrarySettings,
    UpdateGenerationLibrarySettingsRequest, GenerationPreset, GenerationPresetsResponse,
    CreateGenerationPresetRequest, UpdateGenerationPresetRequest, DuplicateGenerationPresetRequest,
    ImportGenerationPreset, ImportGenerationPresetsRequest, GenerationPresetImportResponse,
    GenerationDeleteResponse,
]
