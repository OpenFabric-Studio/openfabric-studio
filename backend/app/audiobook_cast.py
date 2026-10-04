"""Line-by-line cast for one audiobook narrator plus named speakers.

Inspired by Pandrator multi-voice and YoVoice character performances.
This is an OpenFabric reimplementation. Those projects are not vendored.
"""
from __future__ import annotations

import re

from . import voice_profiles
from .audiobook_contracts import CastMember
from .audiobooks import AudiobookError

_LABEL = re.compile(r"^([^:\n]{1,40}):[ \t]*(.*)$")
NARRATOR = "Narrator"


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
        match = _LABEL.match(raw.lstrip(" \t"))
        if match is not None:
            found = voices.get(match.group(1).strip().casefold())
            if found is not None:
                spoken = match.group(2).strip()
                if spoken:
                    append(found[0], found[1], spoken)
                continue
        append(narrator_id, NARRATOR, stripped)
    if not turns and text.strip():
        return [(narrator_id, NARRATOR, text.strip())]
    return turns
