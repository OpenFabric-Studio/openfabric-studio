"""Contracts for audiobook jobs (talking speech-clone path)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .contracts import Contract, JobStatus

AudiobookBookStatus = Literal["draft", "queued", "running", "done", "failed"]


class AudiobookChapterInput(Contract):
    title: str = Field(default="", max_length=200)
    text: str = Field(min_length=1, max_length=20_000)


class CreateAudiobookRequest(Contract):
    title: str = Field(min_length=1, max_length=200)
    profile_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    chapters: list[AudiobookChapterInput] = Field(min_length=1, max_length=100)


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


class AudiobookBook(Contract):
    id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    title: str = Field(min_length=1, max_length=200)
    profile_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    chapter_count: int = Field(ge=1)
    status: AudiobookBookStatus
    export_path: str | None = None
    created_at: str = Field(min_length=1, max_length=64)
    updated_at: str = Field(min_length=1, max_length=64)


class AudiobookBooksResponse(Contract):
    books: list[AudiobookBook]


class AudiobookJobsResponse(Contract):
    jobs: list[AudiobookJob]


class AudiobookCreateResponse(Contract):
    book: AudiobookBook
    jobs: list[AudiobookJob]


AUDIOBOOK_CLIENT_MODELS: list[type[BaseModel]] = [
    AudiobookChapterInput,
    CreateAudiobookRequest,
    AudiobookJob,
    AudiobookBook,
    AudiobookBooksResponse,
    AudiobookJobsResponse,
    AudiobookCreateResponse,
]
