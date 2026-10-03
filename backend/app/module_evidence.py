"""Bounded checkout/environment identity and receipts from real capabilities.

Polling reads metadata only. It never imports model packages or changes an
engine. A receipt is invalidated when its interpreter, source entrypoints or
package metadata footprint changes.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .atomic_files import write_object
from .module_contracts import ModuleId

if TYPE_CHECKING:
    from .module_catalog import ModuleEnvironment


class VerificationReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: int = 2
    root: str
    fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    environment_verified: bool


class CapabilityReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: int = 1
    module_id: ModuleId
    root: str
    fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    service_identity: str = Field(max_length=500)
    verified_at: str


def environment_fingerprint(environment: ModuleEnvironment, identifier: ModuleId) -> str | None:
    """Hash at most 5,000 package entries and small source entrypoints."""
    from .module_catalog import DEFINITIONS, engine_python
    engine = environment.paths[identifier]
    python = engine_python(engine, environment.platform)
    try:
        digest = hashlib.sha256()
        stamp = python.stat()
        digest.update(f'{python.resolve()}:{stamp.st_size}:{stamp.st_mtime_ns}'.encode())
        for marker in DEFINITIONS[identifier].source_markers:
            path = engine / marker
            stat = path.stat()
            digest.update(f'{marker}:{stat.st_size}:{stat.st_mtime_ns}'.encode())
            if path.is_file():
                if stat.st_size > 2 * 1024**2:
                    return None
                digest.update(path.read_bytes())
        if environment.platform == 'win32':
            sites = [engine / '.venv/Lib/site-packages']
        else:
            library = engine / '.venv/lib'
            sites = sorted(library.glob('python*/site-packages')) if library.is_dir() else []
        if not sites or len(sites) > 4:
            return None
        count = 0
        for site in sites:
            if not site.is_dir():
                return None
            for child in sorted(site.iterdir()):
                count += 1
                if count > 5000:
                    return None
                stat = child.stat()
                digest.update(f'{child.name}:{stat.st_size}:{stat.st_mtime_ns}'.encode())
                if child.name.endswith('.dist-info') and child.is_dir():
                    for name in ('METADATA', 'RECORD', 'INSTALLER', 'direct_url.json'):
                        metadata = child / name
                        if metadata.is_file():
                            stat = metadata.stat()
                            digest.update(f'{name}:{stat.st_size}:{stat.st_mtime_ns}'.encode())
        return digest.hexdigest()
    except OSError:
        return None


def environment_verified(environment: ModuleEnvironment, identifier: ModuleId) -> bool:
    path = environment.root / '.setup/verification' / f'{identifier}.json'
    try:
        if path.is_symlink() or path.stat().st_size > 16384:
            return False
        receipt = VerificationReceipt.model_validate_json(path.read_bytes())
        return receipt.schema_version == 2 and receipt.environment_verified and receipt.root == str(environment.paths[identifier].resolve()) and receipt.fingerprint == environment_fingerprint(environment, identifier)
    except (OSError, ValueError, ValidationError):
        return False


def _capability_path(environment: ModuleEnvironment, identifier: ModuleId) -> Path:
    return environment.data_dir / 'models/.module-capabilities' / f'{identifier}.json'


def record_capability_success(identifier: ModuleId, *, environment: ModuleEnvironment | None = None,
                              service_identity: str | None = None) -> None:
    """Called only after real validated output, never from a health response."""
    from .module_catalog import configured_environment
    env = environment or configured_environment()
    fingerprint = environment_fingerprint(env, identifier)
    if fingerprint is None:
        return
    if service_identity is None:
        if identifier != 'speech':
            return
        from .speech_clone import api_base_url
        service_identity = api_base_url()
    receipt = CapabilityReceipt(module_id=identifier, root=str(env.paths[identifier].resolve()),
        fingerprint=fingerprint, service_identity=service_identity,
        verified_at=datetime.now(timezone.utc).isoformat())
    path = _capability_path(env, identifier)
    if path.is_symlink() or not path.resolve().is_relative_to(env.data_dir.resolve()):
        raise ValueError('unsafe_capability_receipt')
    path.parent.mkdir(parents=True, exist_ok=True)
    write_object(path, {'schema_version': receipt.schema_version, 'module_id': receipt.module_id,
        'root': receipt.root, 'fingerprint': receipt.fingerprint,
        'service_identity': receipt.service_identity, 'verified_at': receipt.verified_at})


def capability_verified(environment: ModuleEnvironment, identifier: ModuleId, service_identity: str) -> bool:
    path = _capability_path(environment, identifier)
    try:
        if path.is_symlink() or not path.resolve().is_relative_to(environment.data_dir.resolve()) or path.stat().st_size > 16384:
            return False
        receipt = CapabilityReceipt.model_validate_json(path.read_bytes())
        return receipt.schema_version == 1 and receipt.module_id == identifier and receipt.root == str(environment.paths[identifier].resolve()) and receipt.service_identity == service_identity and receipt.fingerprint == environment_fingerprint(environment, identifier)
    except (OSError, ValueError, ValidationError):
        return False
