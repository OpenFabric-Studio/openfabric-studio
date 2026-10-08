"""Contracts for audiobook jobs (talking speech-clone path)."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .contracts import Contract, JobStatus
from .voice_profile_contracts import CloudSpeechApproval, CloudSpeechProvenance

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
    cloud_approval: CloudSpeechApproval | None = None
    title: str = Field(min_length=1, max_length=200)
    profile_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    chapters: list[AudiobookChapterInput] = Field(min_length=1, max_length=100)
    author: str = Field(default="", max_length=200)
    pronunciations: list[PronunciationEntry] = Field(default_factory=list, max_length=100)
    language: str = Field(default="", max_length=35)
    cast: list[CastMember] = Field(default_factory=list, max_length=16)
    passage_gap_ms: int = Field(default=0, ge=0, le=5000)
    speaker_change_gap_ms: int = Field(default=0, ge=0, le=5000)

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
    revision: int = Field(default=1, ge=1)
    render_language: str = Field(default="", max_length=35)
    duration_ms: int | None = Field(default=None, ge=0)


class AudiobookBook(Contract):
    cloud_models: list[str] = Field(default_factory=list, max_length=17)
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
    passage_gap_ms: int = Field(default=0, ge=0, le=5000)
    speaker_change_gap_ms: int = Field(default=0, ge=0, le=5000)


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


class SubtitleSourceCue(Contract):
    cue_id: str = Field(min_length=1, max_length=200)
    order: int = Field(ge=0)
    speaker: str = Field(default="", max_length=80)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=20_000)

    @model_validator(mode="after")
    def ordered_time(self) -> SubtitleSourceCue:
        if self.end_ms <= self.start_ms:
            raise ValueError("invalid_subtitle_time")
        return self


class EbookChapterDraft(AudiobookChapterInput):
    included: bool = True
    source_cues: list[SubtitleSourceCue] = Field(default_factory=list, max_length=1000)


class EbookImportWarning(Contract):
    code: Literal["chapter_detection", "chapter_split", "non_narrative_content", "nonlinear_content", "unsupported_content", "subtitle_overlap", "cast_review_required", "independent_reimport"]
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
    subtitle_import: bool = False
    cast_review_required: bool = False


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
    cloud_approval: CloudSpeechApproval | None = None
    profile_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    revision: int = Field(ge=1)
    cast: list[CastMember] = Field(default_factory=list, max_length=16)
    cast_reviewed: bool = False
    language: str = Field(default="", max_length=35)

    @model_validator(mode="after")
    def language_code(self) -> CreateAudiobookFromDraftRequest:
        self.language = _language_code(self.language)
        return self


class AudiobookPassage(Contract):
    renderer: Literal["local","openrouter"] = "local"
    cloud_provenance: CloudSpeechProvenance | None = None
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    section_index: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=1200)
    profile_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    speaker: str = Field(min_length=1, max_length=40)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    status: Literal["queued", "running", "done"]
    audio_url: str | None = Field(default=None, max_length=300)
    render_identity: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    language: str = Field(default="", max_length=35)
    # Null means no verified original wording mapping is available (legacy or edited take).
    display_text: str | None = Field(default=None, max_length=20_000)
    gap_after_ms: int | None = Field(default=None, ge=0, le=10000)
    effective_gap_after_ms: int = Field(default=0, ge=0, le=10000)


class AudiobookPassageGap(Contract):
    passage_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    # Null restores the book default. Zero explicitly removes the following pause.
    gap_after_ms: int | None = Field(default=None, ge=0, le=10000)


class AudiobookPacingChapter(Contract):
    chapter_index: int = Field(ge=0, le=99)
    revision: int = Field(ge=1)
    passages: list[AudiobookPassageGap] = Field(default_factory=list, max_length=2000)


class SetAudiobookPacingRequest(Contract):
    passage_gap_ms: int = Field(default=0, ge=0, le=5000)
    speaker_change_gap_ms: int = Field(default=0, ge=0, le=5000)
    chapters: list[AudiobookPacingChapter] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_chapters_and_passages(self) -> SetAudiobookPacingRequest:
        if len({item.chapter_index for item in self.chapters}) != len(self.chapters):
            raise ValueError("duplicate_chapter")
        identifiers = [item.passage_id for chapter in self.chapters for item in chapter.passages]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("duplicate_passage")
        return self


class NarrationDurationPassageSource(Contract):
    book_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    chapter_index: int = Field(ge=0, le=99)
    passage_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    revision: int = Field(ge=1)


class NarrationDurationRequest(Contract):
    profile_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    language: str = Field(default="en", min_length=2, max_length=35)
    text: str = Field(default="", max_length=20_000)
    target_seconds: Literal[15, 30, 60, 90] = 30
    passage_source: NarrationDurationPassageSource | None = None


class NarrationDurationGuidance(Contract):
    state: Literal["approximate", "unavailable"]
    reason: Literal["measured_takes", "model_unverified", "no_matching_takes", "insufficient_speech"]
    target_seconds: Literal[15, 30, 60, 90]
    measurement_count: int = Field(default=0, ge=0, le=100)
    measured_audio_ms: int = Field(default=0, ge=0)
    characters_per_second: float | None = Field(default=None, gt=0, le=100, allow_inf_nan=False)
    suggested_characters: int | None = Field(default=None, ge=0, le=20000)
    estimated_min_ms: int | None = Field(default=None, ge=0)
    estimated_max_ms: int | None = Field(default=None, ge=0)
    render_key: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class AudiobookPassagesResponse(Contract):
    book_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    chapter_index: int = Field(ge=0)
    revision: int = Field(ge=1)
    passages: list[AudiobookPassage]


class AudiobookAuditionOptions(Contract):
    cloud_approval: CloudSpeechApproval | None = None
    chapter_index: int = Field(default=0, ge=0, le=99)
    mode: Literal["cast", "scene"] = "cast"
    max_chars: int = Field(default=600, ge=40, le=1200)


class CreateAudiobookAuditionRequest(CreateAudiobookRequest):
    chapter_index: int = Field(default=0, ge=0, le=99)
    mode: Literal["cast", "scene"] = "cast"
    max_chars: int = Field(default=600, ge=40, le=1200)


class CreateAudiobookCastCheckRequest(CreateAudiobookRequest):
    chapter_index: int = Field(default=0, ge=0, le=99)


class AudiobookCastCheckOptions(Contract):
    chapter_index: int = Field(default=0, ge=0, le=99)
    revision: int = Field(ge=1)


class AudiobookCastCheckTurn(Contract):
    speaker: str = Field(min_length=1, max_length=40)
    profile_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    profile_name: str | None = Field(default=None, max_length=120)
    text: str = Field(min_length=1, max_length=1200)


class AudiobookCastCheckWarning(Contract):
    code: Literal["unmatched_label", "shared_narrator", "unused_cast", "profile_missing", "consent_required"]
    speaker: str = Field(max_length=40)
    profile_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    line_number: int | None = Field(default=None, ge=1, le=20_000)


class AudiobookCastCheck(Contract):
    book_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    chapter_index: int = Field(ge=0, le=99)
    revision: int | None = Field(default=None, ge=1)
    turns: list[AudiobookCastCheckTurn] = Field(max_length=20_000)
    warnings: list[AudiobookCastCheckWarning] = Field(max_length=20_100)


class AudiobookAuditionClip(Contract):
    renderer: Literal["local","openrouter"] = "local"
    cloud_provenance: CloudSpeechProvenance | None = None
    index: int = Field(ge=0)
    speaker: str = Field(min_length=1, max_length=40)
    profile_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    text: str = Field(min_length=1, max_length=1200)
    language: str = Field(min_length=2, max_length=35)
    status: Literal["queued", "running", "done", "failed"] = "queued"
    audio_url: str | None = Field(default=None, max_length=300)
    mock: bool = False


class AudiobookAudition(Contract):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    mode: Literal["cast", "scene"]
    status: Literal["queued", "running", "done", "failed", "cancelled"]
    detail: str = Field(default="", max_length=200)
    clips: list[AudiobookAuditionClip] = Field(default_factory=list, max_length=17)
    scene_audio_url: str | None = Field(default=None, max_length=300)
    created_at: str = Field(min_length=1, max_length=64)
    updated_at: str = Field(min_length=1, max_length=64)
    book_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    chapter_index: int = Field(default=0, ge=0)
    revision: int | None = Field(default=None, ge=1)
    skipped_speakers: list[str] = Field(default_factory=list, max_length=17)


class CreateAudiobookRepairRequest(Contract):
    cloud_approval: CloudSpeechApproval | None = None
    revision: int = Field(ge=1)
    text: str | None = Field(default=None, min_length=1, max_length=1200)


class AcceptAudiobookRepairRequest(Contract):
    revision: int = Field(ge=1)


class AudiobookRepair(Contract):
    renderer: Literal["local","openrouter"] = "local"
    cloud_provenance: CloudSpeechProvenance | None = None
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    book_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    chapter_index: int = Field(ge=0)
    passage_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    revision: int = Field(ge=1)
    status: Literal["queued", "running", "ready", "accepted", "failed", "cancelled"]
    detail: str = Field(default="", max_length=200)
    text: str = Field(min_length=1, max_length=1200)
    audio_url: str | None = Field(default=None, max_length=300)
    mock: bool = False
    created_at: str = Field(min_length=1, max_length=64)
    updated_at: str = Field(min_length=1, max_length=64)


class AudiobookAuditionsResponse(Contract):
    auditions: list[AudiobookAudition]


class AudiobookRepairsResponse(Contract):
    repairs: list[AudiobookRepair]


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
    SubtitleSourceCue,
    AudiobookPassage,
    AudiobookPassageGap,
    AudiobookPacingChapter,
    SetAudiobookPacingRequest,
    NarrationDurationRequest,
    NarrationDurationPassageSource,
    NarrationDurationGuidance,
    AudiobookPassagesResponse,
    AudiobookAuditionOptions,
    CreateAudiobookAuditionRequest,
    AudiobookAuditionClip,
    AudiobookAudition,
    CreateAudiobookRepairRequest,
    AcceptAudiobookRepairRequest,
    AudiobookRepair,
    AudiobookAuditionsResponse,
    AudiobookRepairsResponse,
]


class AudiobookCloudControlRequest(Contract):
    cloud_approval: CloudSpeechApproval | None = None
    action: Literal["resume", "retry", "regenerate"] = "resume"
    chapter_index: int | None = Field(default=None, ge=0, le=99)

AUDIOBOOK_CLIENT_MODELS.append(AudiobookCloudControlRequest)
AUDIOBOOK_CLIENT_MODELS.extend([CreateAudiobookCastCheckRequest, AudiobookCastCheckOptions,
    AudiobookCastCheckTurn, AudiobookCastCheckWarning, AudiobookCastCheck])
