"""Shared musical choices supported by the bounded ABC editor."""
from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field

MusicTempo: TypeAlias = Annotated[int, Field(strict=True, ge=20, le=300)]
MusicKey: TypeAlias = Literal[
    'C', 'G', 'D', 'A', 'E', 'B', 'F#', 'C#', 'F', 'Bb', 'Eb', 'Ab', 'Db', 'Gb', 'Cb',
    'Am', 'Em', 'Bm', 'F#m', 'C#m', 'G#m', 'D#m', 'A#m', 'Dm', 'Gm', 'Cm', 'Fm', 'Bbm', 'Ebm', 'Abm',
]
MusicMeter: TypeAlias = Literal['2/4', '3/4', '4/4', '6/8', '9/8', '12/8']
