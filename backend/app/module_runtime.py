"""Read only, bounded identity/model probes for existing loopback services."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .module_contracts import ModuleEvidence, ModuleId
from .module_evidence import capability_verified

if TYPE_CHECKING:
    from .module_catalog import ModuleEnvironment


@dataclass(frozen=True)
class RuntimeTarget:
    module_id: ModuleId
    base_url: str


@dataclass(frozen=True)
class RuntimeEvidence:
    ready: bool = False
    installed: bool = False
    capabilities: tuple[str, ...] = ()
    evidence: tuple[ModuleEvidence, ...] = ()


def speaker_status(environment: ModuleEnvironment) -> RuntimeEvidence:
    """Inspect optional local metadata; dependency setup does not activate QA."""
    from .module_catalog import engine_python
    from .speaker_review import capability
    python = engine_python(environment.paths['speaker_review'], environment.platform)
    status = capability(python_path=python)
    configured_python = os.environ.get('OPENFABRIC_SPEAKER_REVIEW_PYTHON', '')
    try:
        activated = bool(configured_python and Path(configured_python).expanduser().resolve() == python.resolve())
    except (OSError, ValueError, RuntimeError):
        # A malformed optional activation path cannot hide unrelated modules.
        activated = False
    ready = status.available and activated
    weights_verified = status.available and status.encoder is not None
    return RuntimeEvidence(ready, status.deps_available, ('speaker_similarity_screening',) if ready else (), (
        ModuleEvidence(code='speaker_dependencies', detail='Separate CPU dependencies are compatible.' if status.deps_available else 'Separate CPU dependencies are missing or incompatible.', verified=status.deps_available),
        ModuleEvidence(code='speaker_weights', detail='Reviewed local checkpoint verified.' if weights_verified else 'Select and verify the reviewed local checkpoint explicitly; setup does not download it.', verified=weights_verified),
        ModuleEvidence(code='speaker_activation', detail='Optional speaker review is configured.' if ready else 'Configure OPENFABRIC_SPEAKER_REVIEW_PYTHON and OPENFABRIC_SPEAKER_REVIEW_WEIGHTS explicitly. Similarity needs human review and a calibrated threshold.', verified=ready),
    ))


class AceHealth(BaseModel):
    model_config = ConfigDict(strict=True)
    status: str = Field(max_length=100)
    service: str = Field(max_length=100)
    models_initialized: bool
    llm_initialized: bool
    loaded_model: str | None = Field(max_length=300)
    loaded_lm_model: str | None = Field(max_length=300)


class NativeHealth(BaseModel):
    # v0.8.1 app/server/runtime.cpp /health: status=ok, backend_name(...).
    model_config = ConfigDict(strict=True)
    status: str = Field(max_length=100)
    backend: str = Field(max_length=100)


class NativeModel(BaseModel):
    model_config = ConfigDict(strict=True)
    id: str = Field(max_length=300)
    loaded: bool


class NativeInventory(BaseModel):
    data: list[NativeModel] = Field(max_length=1000)


class ApiParameter(BaseModel):
    name: str = Field(max_length=100)


class ApiOperation(BaseModel):
    parameters: list[ApiParameter] = Field(default_factory=list, max_length=100)


class ApiPath(BaseModel):
    get: ApiOperation | None = None
    post: ApiOperation | None = None


class ApiSchema(BaseModel):
    paths: dict[str, ApiPath]


def _loopback(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return parts.scheme == 'http' and parts.hostname in ('127.0.0.1', 'localhost', '::1') and parts.username is None and parts.password is None and not parts.query and not parts.fragment and (parts.port is None or 1 <= parts.port <= 65535)
    except ValueError:
        return False


async def _json(client: httpx.AsyncClient, url: str) -> object:
    async with asyncio.timeout(1.5):
        async with client.stream('GET', url) as response:
            if response.status_code != 200:
                return None
            content = bytearray()
            async for chunk in response.aiter_bytes(4096):
                content.extend(chunk)
                if len(content) > 128 * 1024:
                    return None
            value: object = json.loads(content)
            return value


def _unwrapped(value: object) -> object:
    if isinstance(value, dict) and isinstance(value.get('code'), int):
        result: object = value.get('data')
        return result
    return value


async def runtime_status(environment: ModuleEnvironment, target: RuntimeTarget,
                         *, client: httpx.AsyncClient | None = None) -> RuntimeEvidence:
    if not _loopback(target.base_url):
        return RuntimeEvidence()
    if client is None:
        async with httpx.AsyncClient(timeout=1.5, follow_redirects=False, trust_env=False) as owned:
            return await runtime_status(environment, target, client=owned)
    try:
        base = target.base_url.rstrip('/')
        if target.module_id == 'ace_step':
            health = AceHealth.model_validate(_unwrapped(await _json(client, base + '/health')))
            identity = health.status == 'ok' and health.service == 'ACE-Step API'
            ready = identity and health.models_initialized and health.llm_initialized and bool(health.loaded_model) and bool(health.loaded_lm_model)
            return RuntimeEvidence(ready, identity, ('music_generation_models',) if ready else (),
                (ModuleEvidence(code='runtime_identity', detail='ACE-Step API identity verified.' if identity else 'The running service did not match ACE-Step.', verified=identity),
                 ModuleEvidence(code='runtime_models', detail='Generation and language models report initialized.' if ready else 'Generation and language models are not both initialized.', verified=ready)))
        if target.module_id == 'yue2':
            health_data, inventory_data = await asyncio.gather(_json(client, base + '/health'), _json(client, base + '/v1/models'))
            native = NativeHealth.model_validate(health_data)
            models = NativeInventory.model_validate(inventory_data)
            identity = native.status in ('ok', 'healthy') and native.backend in ('metal', 'cuda', 'cpu', 'vulkan')
            ready = identity and any(model.id == 'yue2' and model.loaded for model in models.data)
            return RuntimeEvidence(ready, identity, ('music_generation_models',) if ready else (),
                (ModuleEvidence(code='runtime_identity', detail='Native engine health and typed model inventory verified.' if identity else 'The native engine identity is unverified.', verified=identity),
                 ModuleEvidence(code='runtime_models', detail='YuE generation model reports loaded.' if ready else 'A loaded YuE generation model is not reported.', verified=ready)))
        if target.module_id == 'speech':
            schema = ApiSchema.model_validate(await _json(client, base + '/openapi.json'))
            root = schema.paths.get('/')
            required = {'refer_wav_path', 'prompt_text', 'prompt_language', 'text', 'text_language'}
            identity = root is not None and root.get is not None and root.post is not None and required.issubset({parameter.name for parameter in root.get.parameters}) and '/set_model' in schema.paths and '/change_refer' in schema.paths
            ready = identity and capability_verified(environment, 'speech', base)
            return RuntimeEvidence(ready, identity, ('speech_synthesis', 'audiobook_narration') if ready else (),
                (ModuleEvidence(code='runtime_identity', detail='Expected GPT-SoVITS API methods verified.' if identity else 'The service API does not match the supported GPT-SoVITS interface.', verified=identity),
                 ModuleEvidence(code='successful_speech', detail='A validated real speech result matches this checkout and environment.' if ready else 'A real short speech preview must complete for this checkout and environment.', verified=ready)))
    except (httpx.HTTPError, TimeoutError, ValueError, ValidationError):
        return RuntimeEvidence(evidence=(ModuleEvidence(code='runtime_unverified', detail='The existing loopback service could not be verified with bounded read-only checks.', verified=False),))
    return RuntimeEvidence()


def configured_targets(environment: ModuleEnvironment) -> list[RuntimeTarget]:
    from . import config
    from .orchestrator.manager import manager
    from .orchestrator.state import ModelStatus
    result: list[RuntimeTarget] = []
    for identifier in ('ace_step', 'yue2'):
        state = manager.state.models.get(identifier)
        definition = config.MODELS.get(identifier)
        module_id: ModuleId = 'ace_step' if identifier == 'ace_step' else 'yue2'
        if state is not None and state.status == ModelStatus.RUNNING and definition is not None and definition.processes and environment.paths[module_id].resolve() == definition.processes[0].cwd.resolve():
            result.append(RuntimeTarget(module_id, definition.proxy_target))
    if environment.paths['speech'].resolve() == config.GPT_SOVITS_DIR.resolve() and (environment.paths['speech'] / 'api.py').is_file():
        from .speech_clone import api_base_url, mock_enabled
        if not mock_enabled():
            result.append(RuntimeTarget('speech', api_base_url()))
    return result
