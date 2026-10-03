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


def apply_pronunciations(text: str, entries: Sequence[PronunciationEntry]) -> str:
    """Replace written forms with spoken forms. Longer phrases win."""
    if not entries:
        return text
    ordered = sorted(entries, key=lambda entry: len(entry.written), reverse=True)
    lookup: dict[str, str] = {}
    parts: list[str] = []
    for entry in ordered:
        key = entry.written.casefold()
        if key in lookup:
            continue
        lookup[key] = entry.spoken
        parts.append(re.escape(entry.written))
    pattern = re.compile(r"(?<!\w)(" + "|".join(parts) + r")(?!\w)", re.IGNORECASE)

    def replace(match: re.Match[str]) -> str:
        spoken = lookup.get(match.group(0).casefold())
        return spoken if spoken is not None else match.group(0)

    spoken_text = pattern.sub(replace, text)
    if len(spoken_text) > MAX_SPOKEN_CHARS:
        raise PronunciationError("pronunciation_too_long")
    return spoken_text
