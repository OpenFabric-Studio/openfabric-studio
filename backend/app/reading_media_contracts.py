"""Accepted narration reuse; captions have passage timing, never inferred words."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, model_validator
from .contracts import Contract, JobStatus
from .export_provenance_contracts import ProvenanceComponent


class ReadingCue(Contract):
    passage_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    display_text: str = Field(min_length=1, max_length=20000)
    spoken_text: str = Field(min_length=1, max_length=20000)


class ReadAlongRequest(Contract):
    revision: int = Field(ge=1)
    aspect: Literal['portrait', 'landscape'] = 'portrait'
    preview_seconds: int | None = Field(default=None, ge=1, le=25)


class ReadAlongExport(Contract):
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    book_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    chapter_index: int = Field(ge=0, le=99)
    source_revision: int = Field(ge=1)
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    status: JobStatus
    detail: str = Field(default='', max_length=200)
    aspect: Literal['portrait', 'landscape']
    preview_seconds: int | None = Field(default=None, ge=1, le=25)
    duration_ms: int = Field(gt=0)
    timing: Literal['passage'] = 'passage'
    text_basis: Literal['original_mapping', 'spoken_fallback'] = 'original_mapping'
    created_at: str = Field(min_length=1, max_length=64)
    updated_at: str = Field(min_length=1, max_length=64)
    video_url: str | None = None
    srt_url: str | None = None
    vtt_url: str | None = None
    manifest_url: str | None = None


class ReadAlongExportsResponse(Contract):
    exports: list[ReadAlongExport]


class RetainedAudioSource(Contract):
    kind: Literal['speech_trial', 'chapter']
    source_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    chapter_index: int | None = Field(default=None, ge=0, le=99)
    revision: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def chapter_identity(self) -> RetainedAudioSource:
        if self.kind == 'chapter' and (self.chapter_index is None or self.revision is None):
            raise ValueError('chapter_revision_required')
        if self.kind == 'speech_trial' and (self.chapter_index is not None or self.revision is not None):
            raise ValueError('invalid_trial_source')
        return self


class RetainedAudioInfo(Contract):
    source: RetainedAudioSource
    duration_ms: int = Field(gt=0)
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    content_origin: Literal['generated', 'mixed', 'recorded', 'unknown']


class RetainedAudioVideoRequest(Contract):
    source: RetainedAudioSource
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    name: str = Field(default='Narration video', min_length=1, max_length=120)
    clip_start_ms: int = Field(default=0, ge=0)
    clip_end_ms: int | None = Field(default=None, gt=0)


class RetainedAudioIdentity(Contract):
    source: RetainedAudioSource
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    clip_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    clip_start_ms: int = Field(ge=0)
    clip_end_ms: int = Field(gt=0)
    profile_ids: list[str] = Field(default_factory=list, max_length=17)
    components: list[ProvenanceComponent] = Field(default_factory=list, max_length=1000)


READING_MEDIA_CLIENT_MODELS: list[type[BaseModel]] = [ReadingCue, ReadAlongRequest, ReadAlongExport,
    ReadAlongExportsResponse, RetainedAudioSource, RetainedAudioInfo, RetainedAudioVideoRequest]
