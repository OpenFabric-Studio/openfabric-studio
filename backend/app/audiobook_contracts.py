"""Contracts for audiobook jobs (talking speech-clone path)."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .contracts import Contract, JobStatus

AudiobookBookStatus = Literal["draft", "queued", "running", "done", "failed", "paused", "cancelled"]


class AudiobookChapterInput(Contract):
    title: str = Field(default="", max_length=200)
    text: str = Field(min_length=1, max_length=20_000)


class CastMember(Contract):
    """A named speaker bound to a saved speech profile."""
    name: str = Field(min_length=1, max_length=40)
    profile_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")

    @model_validator(mode="after")
    def cleaned_name(self) -> CastMember:
        name = self.name.strip()
        if not name or ":" in name or any(ord(char) < 32 for char in name):
            raise ValueError("invalid_cast_name")
        self.name = name
        return self


class PronunciationEntry(Contract):
    """Written text replaced with the spoken form before synthesis."""
    written: str = Field(min_length=1, max_length=80)
    spoken: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def stripped(self) -> PronunciationEntry:
        written = self.written.strip()
        spoken = self.spoken.strip()
        if not written or not spoken:
            raise ValueError("pronunciation_required")
        self.written = written
        self.spoken = spoken
        return self


_LANGUAGE = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}")


def _language_code(value: str) -> str:
    text = value.strip()
    if text and _LANGUAGE.fullmatch(text) is None:
        raise ValueError("invalid_language")
    return text


class CreateAudiobookRequest(Contract):
    title: str = Field(min_length=1, max_length=200)
    profile_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    chapters: list[AudiobookChapterInput] = Field(min_length=1, max_length=100)
    author: str = Field(default="", max_length=200)
    pronunciations: list[PronunciationEntry] = Field(default_factory=list, max_length=100)
    language: str = Field(default="", max_length=35)
    cast: list[CastMember] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def language_code(self) -> CreateAudiobookRequest:
        self.language = _language_code(self.language)
        return self


class AudiobookJob(Contract):
    id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    book_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    chapter_index: int = Field(ge=0)
    chapter_title: str = Field(default="", max_length=200)
    status: JobStatus
    detail: str = Field(default="", max_length=2000)
    output_path: str | None = None
    created_at: str = Field(min_length=1, max_length=64)
    updated_at: str = Field(min_length=1, max_length=64)
    completed_sections: int = Field(default=0, ge=0)
    total_sections: int = Field(default=0, ge=0)
    language: str = Field(default="", max_length=35)
    language_ready: bool = False
    chapter_text: str = Field(default="", max_length=20_000)


class AudiobookBook(Contract):
    id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    title: str = Field(min_length=1, max_length=200)
    profile_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    chapter_count: int = Field(ge=1)
    status: AudiobookBookStatus
    export_path: str | None = None
    created_at: str = Field(min_length=1, max_length=64)
    updated_at: str = Field(min_length=1, max_length=64)
    source_import_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    author: str = Field(default="", max_length=200)
    pronunciations: list[PronunciationEntry] = Field(default_factory=list, max_length=100)
    mp3_ready: bool = False
    m4b_ready: bool = False
    has_cover: bool = False
    export_note: str = Field(default="", max_length=500)
    language: str = Field(default="", max_length=35)
    cast: list[CastMember] = Field(default_factory=list, max_length=16)


class ChapterLanguageUpdate(Contract):
    chapter_index: int = Field(ge=0, le=99)
    language: str = Field(default="", max_length=35)

    @model_validator(mode="after")
    def language_code(self) -> ChapterLanguageUpdate:
        self.language = _language_code(self.language)
        return self


class SetAudiobookLanguagesRequest(Contract):
    language: str = Field(default="", max_length=35)
    chapters: list[ChapterLanguageUpdate] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def language_code(self) -> SetAudiobookLanguagesRequest:
        self.language = _language_code(self.language)
        return self


class AudiobookBooksResponse(Contract):
    books: list[AudiobookBook]


class AudiobookJobsResponse(Contract):
    jobs: list[AudiobookJob]


class AudiobookCreateResponse(Contract):
    book: AudiobookBook
    jobs: list[AudiobookJob]


class EbookChapterDraft(AudiobookChapterInput):
    included: bool = True


class EbookImportWarning(Contract):
    code: Literal["chapter_detection", "chapter_split", "non_narrative_content", "nonlinear_content"]
    message: str = Field(min_length=1, max_length=400)


class PatchEbookDraftRequest(Contract):
    title: str = Field(min_length=1, max_length=200)
    chapters: list[EbookChapterDraft] = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1)
    author: str | None = Field(default=None, max_length=200)
    pronunciations: list[PronunciationEntry] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def valid_text(self) -> PatchEbookDraftRequest:
        if not self.title.strip() or any(not chapter.text.strip() for chapter in self.chapters):
            raise ValueError("draft_text_required")
        return self


class EbookDraft(Contract):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    title: str = Field(min_length=1, max_length=200)
    chapters: list[EbookChapterDraft] = Field(min_length=1, max_length=100)
    source_filename: str = Field(min_length=1, max_length=240)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    warnings: list[EbookImportWarning] = Field(default_factory=list, max_length=100)
    revision: int = Field(ge=1)
    author: str = Field(default="", max_length=200)
    pronunciations: list[PronunciationEntry] = Field(default_factory=list, max_length=100)
    created_at: str = Field(min_length=1, max_length=64)
    updated_at: str = Field(min_length=1, max_length=64)


class ImportPastedTextRequest(Contract):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=2_000_000)
    author: str = Field(default="", max_length=200)


class SetPronunciationsRequest(Contract):
    pronunciations: list[PronunciationEntry] = Field(default_factory=list, max_length=100)


class EbookDraftSummary(Contract):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    title: str = Field(min_length=1, max_length=200)
    source_filename: str = Field(min_length=1, max_length=240)
    chapter_count: int = Field(ge=1, le=100)
    revision: int = Field(ge=1)
    created_at: str = Field(min_length=1, max_length=64)
    updated_at: str = Field(min_length=1, max_length=64)


class EbookDraftsResponse(Contract):
    drafts: list[EbookDraftSummary]


class SetCastRequest(Contract):
    cast: list[CastMember] = Field(default_factory=list, max_length=16)


class SetChapterTextRequest(Contract):
    text: str = Field(min_length=1, max_length=20_000)


class CreateAudiobookFromDraftRequest(Contract):
    profile_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    revision: int = Field(ge=1)
    cast: list[CastMember] = Field(default_factory=list, max_length=16)


AUDIOBOOK_CLIENT_MODELS: list[type[BaseModel]] = [
    CastMember,
    PronunciationEntry,
    AudiobookChapterInput,
    CreateAudiobookRequest,
    AudiobookJob,
    AudiobookBook,
    AudiobookBooksResponse,
    AudiobookJobsResponse,
    AudiobookCreateResponse,
    EbookChapterDraft,
    EbookImportWarning,
    PatchEbookDraftRequest,
    EbookDraft,
    ImportPastedTextRequest,
    SetPronunciationsRequest,
    EbookDraftSummary,
    EbookDraftsResponse,
    CreateAudiobookFromDraftRequest,
    ChapterLanguageUpdate,
    SetAudiobookLanguagesRequest,
    SetCastRequest,
    SetChapterTextRequest,
]
