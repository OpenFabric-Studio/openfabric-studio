"""Bounded decoding of the native server's base64 score artifacts."""
from __future__ import annotations

import base64
from pydantic import BaseModel, ConfigDict, Field
from .contracts import JsonObject


class _Artifact(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    id: str = Field(default='', max_length=128)
    payload: str = Field(max_length=150_000)
    meta: dict[str, str] = Field(default_factory=dict)


class _Result(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    artifacts: list[_Artifact] = Field(default_factory=list, max_length=16)
    text: str | None = Field(default=None, max_length=100_000)


def score_text(result: JsonObject) -> str | None:
    decoded = _Result.model_validate(result)
    for artifact in decoded.artifacts:
        if artifact.meta.get('format') == 'abc' or artifact.meta.get('extension') == 'abc':
            text = base64.b64decode(artifact.payload, validate=True).decode('utf-8')
            if len(text) > 100_000 or '\0' in text:
                raise ValueError('Invalid ABC artifact')
            return text or None
    return decoded.text or None
