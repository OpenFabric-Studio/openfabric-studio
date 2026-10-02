"""Atomic JSON publication and per-document read/modify/write locks."""
from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator

from pydantic import TypeAdapter
from .contracts import JsonValue

JsonObject = dict[str, JsonValue]
_json: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_locks: dict[Path, threading.RLock] = {}
_registry_lock = threading.Lock()


@contextmanager
def document_lock(path: Path) -> Iterator[None]:
    """Hold across a complete metadata transaction, including its read."""
    key = path.resolve()
    with _registry_lock:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        yield


def read_object(path: Path) -> JsonObject:
    value = _json.validate_json(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON document must be an object")
    return value


def write_object(path: Path, payload: JsonObject) -> None:
    """Publish a complete document, leaving the old one intact on failure."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with document_lock(path):
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
