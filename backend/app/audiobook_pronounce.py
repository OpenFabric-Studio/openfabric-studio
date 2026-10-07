"""Spoken-form substitutions applied before audiobook synthesis.

Whole-phrase matches only: a written form never changes characters inside a
longer word. This is an OpenFabric implementation, not copied from Pandrator.
"""
from __future__ import annotations

import re
from collections.abc import Sequence

from .audiobook_contracts import PronunciationEntry

MAX_SPOKEN_CHARS = 40_000


class PronunciationError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def ensure_unique(entries: Sequence[PronunciationEntry]) -> None:
    seen: set[str] = set()
    for entry in entries:
        key = entry.written.casefold()
        if key in seen:
            raise PronunciationError("duplicate_pronunciation")
        seen.add(key)


def _replacement_pattern(entries: Sequence[PronunciationEntry]) -> tuple[re.Pattern[str] | None, dict[str, str]]:
    ordered = sorted(entries, key=lambda entry: len(entry.written), reverse=True)
    lookup: dict[str, str] = {}
    parts: list[str] = []
    for entry in ordered:
        key = entry.written.casefold()
        if key in lookup:
            continue
        lookup[key] = entry.spoken
        parts.append(re.escape(entry.written))
    pattern = re.compile(r"(?<!\w)(" + "|".join(parts) + r")(?!\w)", re.IGNORECASE) if parts else None
    return pattern, lookup


def _spoken_form(match: re.Match[str], lookup: dict[str, str]) -> str:
    # Python IGNORECASE also matches İ/ı against ASCII I. Those matches have
    # different casefold keys, so retain the original existing fallback.
    return lookup.get(match.group(0).casefold(), match.group(0))


def apply_pronunciations(text: str, entries: Sequence[PronunciationEntry]) -> str:
    """Replace written forms with spoken forms. Longer phrases win."""
    if not entries:
        return text
    pattern, lookup = _replacement_pattern(entries)
    if pattern is None:
        return text

    def replace(match: re.Match[str]) -> str:
        return _spoken_form(match, lookup)

    spoken_text = pattern.sub(replace, text)
    if len(spoken_text) > MAX_SPOKEN_CHARS:
        raise PronunciationError("pronunciation_too_long")
    return spoken_text


def apply_pronunciations_with_mapping(text: str, entries: Sequence[PronunciationEntry]) -> tuple[str, list[int | None]]:
    """Map output edges back to original offsets; a replacement's interior is unknown."""
    pattern, lookup = _replacement_pattern(entries)
    boundaries: list[int | None] = [0]
    pieces: list[str] = []
    cursor = 0
    for match in pattern.finditer(text) if pattern is not None else []:
        pieces.append(text[cursor:match.start()])
        boundaries.extend(range(cursor + 1, match.start() + 1))
        replacement = _spoken_form(match, lookup)
        pieces.append(replacement)
        if replacement == match.group(0):
            boundaries.extend(range(match.start() + 1, match.end() + 1))
        else:
            boundaries.extend([None] * (len(replacement) - 1))
            boundaries.append(match.end())
        cursor = match.end()
    pieces.append(text[cursor:])
    boundaries.extend(range(cursor + 1, len(text) + 1))
    spoken = "".join(pieces)
    if entries and len(spoken) > MAX_SPOKEN_CHARS:
        raise PronunciationError("pronunciation_too_long")
    return spoken, boundaries
