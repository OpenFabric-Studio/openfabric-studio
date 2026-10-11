"""Atomic, nonsecret opt-in preference for the clip-only local HTTPS relay."""
from __future__ import annotations

from pathlib import Path
from pydantic import TypeAdapter
from .atomic_files import document_lock, write_object
from .config import DATA_DIR
from .contracts import JsonObject
from .clip_host_contracts import ClipHostSettingsRequest, ClipHostStatus

_json: TypeAdapter[JsonObject]=TypeAdapter(JsonObject)


def _path() -> Path:
    from .clip_hosting import ClipHostError
    path=DATA_DIR/'providers'/'clip-host-settings.json'
    if any(item.is_symlink() for item in (DATA_DIR,path.parent,path)):
        raise ClipHostError('clip_host_storage_unavailable')
    return path


def load() -> ClipHostSettingsRequest:
    from .clip_hosting import ClipHostError
    path=_path()
    with document_lock(path):
        try:
            if not path.exists(): return ClipHostSettingsRequest()
            if path.stat().st_size>4096: raise ValueError('settings_limit')
            return ClipHostSettingsRequest.model_validate_json(path.read_bytes())
        except (OSError,ValueError) as error:
            raise ClipHostError('clip_host_storage_unavailable') from error


def save(body: ClipHostSettingsRequest) -> ClipHostStatus:
    from .clip_hosting import ClipHostError, status
    path=_path()
    with document_lock(path):
        load()
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            write_object(path,_json.validate_json(body.model_dump_json()))
        except (OSError,ValueError) as error:
            raise ClipHostError('clip_host_storage_unavailable') from error
    # Disabling stops new sharing; it does not revoke a remotely accepted input.
    return status()
