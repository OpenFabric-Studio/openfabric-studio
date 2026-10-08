"""Line-by-line cast for one audiobook narrator plus named speakers.

Inspired by Pandrator multi-voice and YoVoice character performances.
This is an OpenFabric reimplementation. Those projects are not vendored.
"""
from __future__ import annotations

import re
from contextlib import closing

from . import voice_profiles
from .audiobook_contracts import (CastMember, PronunciationEntry, CreateAudiobookCastCheckRequest,
    AudiobookCastCheckOptions, AudiobookCastCheck, AudiobookCastCheckTurn, AudiobookCastCheckWarning)
from .audiobooks import AudiobookError

_LABEL = re.compile(r"^([^:\n]{1,40}):[ \t]*(.*)$")
NARRATOR = "Narrator"


def line_label(raw: str) -> tuple[str, str] | None:
    match = _LABEL.match(raw.lstrip(" \t"))
    return (match.group(1).strip(), match.group(2).strip()) if match is not None else None


def require_cast(members: list[CastMember]) -> None:
    """Saved voices only, with consent, and names that can label a line."""
    if len(members) > 16:
        raise AudiobookError("cast_too_large")
    seen: set[str] = set()
    for member in members:
        name = member.name.strip()
        if not name or ":" in name or any(ord(char) < 32 for char in name) or len(name) > 40:
            raise AudiobookError("invalid_cast_name")
        key = name.casefold()
        if key in seen:
            raise AudiobookError("duplicate_cast_name")
        seen.add(key)
        profile = voice_profiles.get_profile(member.profile_id)
        if not profile.consent_confirmed:
            raise AudiobookError("consent_required", 403)


def split_turns(text: str, narrator_id: str, cast: list[CastMember]) -> list[tuple[str, str, str]]:
    """Return profile id, speaker label, and spoken text.

    A line that starts with a cast name and a colon is spoken by that voice.
    The label is not spoken. Any other line uses the book narrator. Back-to-back
    lines for the same speaker stay in one turn.
    """
    voices = {member.name.casefold(): (member.profile_id, member.name) for member in cast}
    turns: list[tuple[str, str, str]] = []

    def append(profile_id: str, speaker: str, spoken: str) -> None:
        if turns and turns[-1][0] == profile_id and turns[-1][1] == speaker:
            previous = turns[-1][2]
            turns[-1] = (profile_id, speaker, f"{previous}\n{spoken}")
            return
        turns.append((profile_id, speaker, spoken))

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        label = line_label(raw)
        if label is not None:
            found = voices.get(label[0].casefold())
            if found is not None:
                spoken = label[1]
                if spoken:
                    append(found[0], found[1], spoken)
                continue
        append(narrator_id, NARRATOR, stripped)
    return turns


def _check(text: str, narrator_id: str, cast: list[CastMember], pronunciations: list[PronunciationEntry],
           chapter_index: int, book_id: str | None = None, revision: int | None = None) -> AudiobookCastCheck:
    from .audiobook_narration import plan_text
    from .audiobook_pronounce import ensure_unique, PronunciationError
    names = {member.name.casefold() for member in cast}
    if len(names) != len(cast):
        raise AudiobookError("duplicate_cast_name")
    try:
        ensure_unique(pronunciations)
        planned = plan_text(text, narrator_id, cast, pronunciations)
    except PronunciationError as error:
        raise AudiobookError(error.code) from error
    used = {(profile, speaker) for _, profile, speaker in planned}
    warnings: list[AudiobookCastCheckWarning] = []
    for number, raw in enumerate(text.splitlines(), 1):
        label = line_label(raw)
        if label is not None and label[0].casefold() not in names:
            warnings.append(AudiobookCastCheckWarning(code="unmatched_label", speaker=label[0],
                profile_id=narrator_id, line_number=number))
    profiles: dict[str, str | None] = {}
    for speaker, identifier in [(NARRATOR, narrator_id), *((member.name, member.profile_id) for member in cast)]:
        try:
            profile = voice_profiles.get_profile(identifier)
            profiles[identifier] = profile.name
            if not profile.consent_confirmed:
                warnings.append(AudiobookCastCheckWarning(code="consent_required", speaker=speaker, profile_id=identifier))
        except voice_profiles.VoiceProfileError as error:
            if error.code != "profile_not_found":
                raise
            profiles[identifier] = None
            warnings.append(AudiobookCastCheckWarning(code="profile_missing", speaker=speaker, profile_id=identifier))
    for member in cast:
        if member.profile_id == narrator_id:
            warnings.append(AudiobookCastCheckWarning(code="shared_narrator", speaker=member.name, profile_id=member.profile_id))
        if (member.profile_id, member.name) not in used:
            warnings.append(AudiobookCastCheckWarning(code="unused_cast", speaker=member.name, profile_id=member.profile_id))
    return AudiobookCastCheck(book_id=book_id, chapter_index=chapter_index, revision=revision,
        turns=[AudiobookCastCheckTurn(speaker=speaker, profile_id=profile, profile_name=profiles[profile], text=spoken)
               for spoken, profile, speaker in planned], warnings=warnings)


def check_draft(body: CreateAudiobookCastCheckRequest) -> AudiobookCastCheck:
    if body.chapter_index >= len(body.chapters):
        raise AudiobookError("chapter_not_found", 404)
    return _check(body.chapters[body.chapter_index].text, body.profile_id, body.cast,
        body.pronunciations, body.chapter_index)


def check_book(book_id: str, body: AudiobookCastCheckOptions) -> AudiobookCastCheck:
    from . import audiobooks
    if re.fullmatch(r"[0-9a-f]{32}", book_id) is None:
        raise AudiobookError("invalid_book_id", 404)
    # One SQLite read transaction keeps cast, text and chapter revision consistent across workers.
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        audiobooks._ensure_schema(connection)
        connection.execute("BEGIN")
        book_row = connection.execute("SELECT * FROM audiobook_books WHERE id = ?", (book_id,)).fetchone()
        if book_row is None:
            raise AudiobookError("book_not_found", 404)
        book = audiobooks._row_to_book(book_row)
        chapter_row = connection.execute("SELECT * FROM audiobook_jobs WHERE book_id = ? AND chapter_index = ?",
            (book_id, body.chapter_index)).fetchone()
        if chapter_row is None:
            raise AudiobookError("chapter_not_found", 404)
        chapter = audiobooks._row_to_job(chapter_row)
    if chapter.revision != body.revision:
        raise AudiobookError("chapter_changed", 409)
    return _check(chapter.chapter_text, book.profile_id, book.cast, book.pronunciations,
        body.chapter_index, book_id, chapter.revision)
