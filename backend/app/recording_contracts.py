"""Original recording facts and declared labels; amplitude screening is not listening QA."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, field_validator

from .contracts import Contract

RecordingPurpose = Literal['training', 'held_out']
RecordingId = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$', strict=True)]
RecordingWarning = Literal['noise_estimate_is_amplitude_proxy', 'pitch_listening_required',
    'full_scale_samples', 'mostly_near_silence', 'capture_differs_from_guidance']


class RecordingContract(Contract):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)


class RecordingLabels(RecordingContract):
    model_config = ConfigDict(serialize_by_alias=True)
    language: str = Field(default='', max_length=120, strict=True, pattern=r'^[^\x00-\x1f\x7f]*$')
    vocal_register: str = Field(default='', alias='register', max_length=120, strict=True, pattern=r'^[^\x00-\x1f\x7f]*$')
    articulation: str = Field(default='', max_length=120, strict=True, pattern=r'^[^\x00-\x1f\x7f]*$')
    expression: str = Field(default='', max_length=120, strict=True, pattern=r'^[^\x00-\x1f\x7f]*$')


class RecordingQC(RecordingContract):
    peak: float = Field(ge=0, le=8)
    clipped_fraction: float = Field(ge=0, le=1)
    rms_dbfs: float = Field(ge=-120, le=19)
    near_silence_fraction: float = Field(ge=0, le=1)
    noise_floor_proxy_dbfs: float = Field(ge=-120, le=19)
    samples_analyzed: int = Field(ge=1, le=3600 * 192000 * 8)
    measurement_method: Literal['streamed-amplitude-v1'] = 'streamed-amplitude-v1'
    pitch_listening_required: Literal[True] = True
    warnings: list[RecordingWarning] = Field(default_factory=list, max_length=5)


class RecordingAudioInfo(RecordingContract):
    format: str = Field(min_length=1, max_length=40, strict=True)
    sample_rate: int = Field(ge=1, le=192000)
    channels: int = Field(ge=1, le=8)
    subtype: str = Field(min_length=1, max_length=40, strict=True)
    duration_sec: float = Field(gt=0, le=3600)
    qc: RecordingQC


class RecordingSession(RecordingAudioInfo):
    id: RecordingId
    voice_id: RecordingId
    purpose: RecordingPurpose
    labels: RecordingLabels
    original_filename: str = Field(min_length=1, max_length=255, strict=True)
    relative_path: str = Field(min_length=1, max_length=512, strict=True)
    original_sha256: str = Field(pattern=r'^[0-9a-f]{64}$', strict=True)
    file_bytes: int = Field(ge=1, le=512 * 1024 * 1024)
    created_at: str = Field(min_length=1, max_length=50, strict=True)

    @field_validator('original_filename')
    @classmethod
    def plain_filename(cls, value: str) -> str:
        if Path(value).name != value or '\\' in value or value in {'.', '..'} or not value.isprintable():
            raise ValueError('recording_invalid_filename')
        return value

    @field_validator('relative_path')
    @classmethod
    def contained_relative_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or '..' in path.parts or '\\' in value or not value.isprintable():
            raise ValueError('recording_invalid_path')
        return value


class RecordingSessionsResponse(RecordingContract):
    voice_id: RecordingId
    sessions: list[RecordingSession] = Field(default_factory=list)


class InspectRecordingRequest(RecordingContract):
    purpose: RecordingPurpose
    filename: str = Field(min_length=1, max_length=255, strict=True)
    labels: RecordingLabels = Field(default_factory=RecordingLabels)

    @field_validator('filename')
    @classmethod
    def plain_filename(cls, value: str) -> str:
        return RecordingSession.plain_filename(value)


class RecordingScriptCategory(RecordingContract):
    id: str = Field(min_length=1, max_length=60)
    label: str = Field(min_length=1, max_length=120)
    minutes: int = Field(ge=1, le=60)
    purpose: RecordingPurpose = 'training'


def _script_categories() -> list[RecordingScriptCategory]:
    categories: tuple[tuple[str, str, int, RecordingPurpose], ...] = (
        ('comfortable_phrases', 'Comfortable melodic phrases', 10, 'training'),
        ('pitch_vowels', 'Pitch and sustained vowels', 10, 'training'),
        ('dynamics_articulation', 'Dynamics and articulation', 8, 'training'),
        ('spanish_lyrics', 'Spanish lyrics', 7, 'training'),
        ('english_lyrics', 'English lyrics', 7, 'training'),
        ('expressiveness', 'Expressiveness', 3, 'training'))
    return [RecordingScriptCategory(id=identifier, label=label, minutes=minutes, purpose=purpose)
        for identifier, label, minutes, purpose in categories]


class RecordingScript(RecordingContract):
    heuristic_only: Literal[True] = True
    target_minutes: Literal[45] = 45
    initial_min_minutes: Literal[15] = 15
    initial_max_minutes: Literal[30] = 30
    selection_max_minutes: Literal[60] = 60
    capture_guidance: str = 'Dry mono 48 kHz, 24-bit WAV; preserve the original. Lower-rate preprocessing cannot restore missing detail.'
    held_out_guidance: str = 'Record dedicated held-out listening passages separately; they do not count toward the training target and must never enter training.'
    categories: list[RecordingScriptCategory] = Field(default_factory=_script_categories, max_length=6)
