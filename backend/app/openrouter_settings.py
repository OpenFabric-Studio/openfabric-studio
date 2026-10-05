"""Only nonsecret preferences are persisted in the local library."""
from __future__ import annotations
from pathlib import Path
from typing import Literal
from pydantic import ValidationError, TypeAdapter
from . import openrouter_credentials as credentials
from .atomic_files import document_lock, write_object
from .config import DATA_DIR
from .contracts import JsonObject
from .openrouter_contracts import OpenRouterSettingsRequest, OpenRouterKeyRequest, OpenRouterStatus

SETTINGS_PATH = DATA_DIR/'providers'/'openrouter-settings.json'
_json: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)

def load() -> OpenRouterSettingsRequest:
    with document_lock(SETTINGS_PATH):
        if SETTINGS_PATH.is_symlink() or SETTINGS_PATH.parent.is_symlink():
            raise ValueError('provider_storage_unavailable')
        if not SETTINGS_PATH.exists():
            return OpenRouterSettingsRequest()
        if SETTINGS_PATH.stat().st_size > 4096:
            raise ValueError('provider_storage_unavailable')
        try:
            return OpenRouterSettingsRequest.model_validate_json(SETTINGS_PATH.read_bytes())
        except (OSError, ValidationError) as error:
            raise ValueError('provider_storage_unavailable') from error

def status() -> OpenRouterStatus:
    settings = load()
    key, source = credentials.resolve()
    # The credential module returns only these closed names.
    narrowed: Literal['none','session','environment','secure_store']
    if source == 'session': narrowed = 'session'
    elif source == 'environment': narrowed = 'environment'
    elif source == 'secure_store': narrowed = 'secure_store'
    else: narrowed = 'none'
    return OpenRouterStatus(enabled=settings.enabled,estimate_limit_usd=settings.estimate_limit_usd,
        credential_configured=key is not None,credential_source=narrowed,secure_storage_available=credentials.native_backend() is not None)

def save(body: OpenRouterSettingsRequest) -> OpenRouterStatus:
    with document_lock(SETTINGS_PATH):
        load()
        SETTINGS_PATH.parent.mkdir(parents=True,exist_ok=True)
        write_object(SETTINGS_PATH,_json.validate_json(body.model_dump_json()))
    return status()

def set_credential(body: OpenRouterKeyRequest) -> OpenRouterStatus:
    credentials.set_key(body.api_key,persist=body.persist)
    return status()

def delete_credential() -> OpenRouterStatus:
    credentials.forget()
    return status()
