"""Only native credential stores; never discover configured third-party backends."""
from __future__ import annotations
import os
import sys
import threading
from typing import Protocol

class CredentialBackend(Protocol):
    @property
    def priority(self) -> float: ...
    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...
    def delete_password(self, service: str, username: str) -> None: ...

_SERVICE = 'OpenFabricStudio.OpenRouter'
_ACCOUNT = 'api-key'
_SESSION: str | None = None
_SESSION_SOURCE = 'session'
_KNOWN_STORED = False
_LOCK = threading.RLock()

def native_backend() -> CredentialBackend | None:
    backend: CredentialBackend
    try:
        if sys.platform == 'darwin':
            from keyring.backends.macOS import Keyring
            backend = Keyring()
        elif sys.platform == 'win32':
            from keyring.backends.Windows import WinVaultKeyring
            backend = WinVaultKeyring()
        elif sys.platform.startswith('linux'):
            from keyring.backends.SecretService import Keyring as SecretServiceKeyring
            backend = SecretServiceKeyring()
        else:
            return None
        return backend if backend.priority > 0 else None
    except Exception:
        # Headless systems, locked stores and missing optional OS services are
        # session-only. No caller sees platform error messages or third-party backends.
        return None

def resolve() -> tuple[str | None, str]:
    global _KNOWN_STORED
    with _LOCK:
        if _SESSION is not None:
            return _SESSION, _SESSION_SOURCE
        value = os.environ.get('OPENROUTER_API_KEY')
        if value:
            return value, 'environment'
        backend = native_backend()
        if backend is not None:
            try:
                value = backend.get_password(_SERVICE, _ACCOUNT)
                if value:
                    _KNOWN_STORED = True
                    return value, 'secure_store'
            except Exception:
                pass
        return None, 'none'

def set_key(value: str, *, persist: bool) -> str:
    global _SESSION, _SESSION_SOURCE, _KNOWN_STORED
    with _LOCK:
        if persist:
            backend = native_backend()
            if backend is not None:
                try:
                    backend.set_password(_SERVICE, _ACCOUNT, value)
                    _KNOWN_STORED = True
                    # Only use the new key after successful secure publication.
                    _SESSION = value
                    _SESSION_SOURCE = 'secure_store'
                    return 'secure_store'
                except Exception:
                    pass
        _SESSION = value
        _SESSION_SOURCE = 'session'
        return 'session'

def clear_session() -> None:
    global _SESSION
    with _LOCK:
        _SESSION = None

def forget() -> None:
    global _KNOWN_STORED
    with _LOCK:
        backend = native_backend()
        if backend is None and _KNOWN_STORED:
            raise ValueError('credential_storage_unavailable')
        if backend is not None:
            try:
                # Do not interpret arbitrary backend errors as a successful removal.
                if backend.get_password(_SERVICE, _ACCOUNT) is not None:
                    backend.delete_password(_SERVICE, _ACCOUNT)
            except Exception as error:
                raise ValueError('credential_storage_unavailable') from error
            _KNOWN_STORED = False
        clear_session()
