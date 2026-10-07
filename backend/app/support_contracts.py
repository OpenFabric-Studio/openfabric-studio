"""Allowlisted engine controls and locally reviewed support reports."""
from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, StrictBool

from .contracts import Contract
from .module_contracts import ModuleId, ModuleState

OwnedEngineId = Literal['ace_step', 'yue2']
EngineState = Literal['stopped', 'starting', 'running', 'stopping', 'error']
IdleState = Literal['idle', 'busy', 'unknown']
SupportErrorCode = Literal['engine_busy', 'engine_changed', 'engine_not_owned', 'engine_inactive',
    'engine_state_unverified', 'engine_stop_failed', 'support_unavailable', 'backend_unreachable']
SupportActionId = Literal['refresh_engines', 'preview_report', 'stop_engine', 'download_report']


class SupportContract(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class EngineRuntime(SupportContract):
    id: OwnedEngineId
    state: EngineState
    owned: StrictBool
    instance_id: str | None = Field(pattern=r'^[0-9a-f]{32}$')
    idle_state: IdleState
    can_stop: StrictBool


class EngineRuntimeResponse(SupportContract):
    engines: list[EngineRuntime] = Field(max_length=2)


class StopEngineRequest(SupportContract):
    engine_id: OwnedEngineId
    instance_id: str = Field(pattern=r'^[0-9a-f]{32}$')


class SupportAction(SupportContract):
    id: SupportActionId
    outcome: Literal['completed', 'failed']
    error_code: SupportErrorCode | None = None


class SupportModuleState(SupportContract):
    id: ModuleId
    state: ModuleState
    supported: StrictBool
    managed: StrictBool
    restart_required: StrictBool


class SupportEngineState(SupportContract):
    id: OwnedEngineId
    state: EngineState
    owned: StrictBool
    idle_state: IdleState


class SupportReport(SupportContract):
    schema_version: Literal[1] = 1
    source: Literal['backend', 'browser_fallback']
    app_version: str | None = Field(default=None, max_length=50, pattern=r'^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$')
    python_version: str | None = Field(default=None, max_length=20, pattern=r'^\d+\.\d+\.\d+$')
    platform: Literal['darwin', 'win32', 'linux', 'unknown']
    architecture: Literal['arm64', 'x64', 'x86', 'unknown']
    acceleration: Literal['apple_silicon', 'nvidia_unverified', 'cpu', 'unknown']
    physical_memory_bytes: int | None = Field(default=None, ge=1, le=17_592_186_044_416)
    backend_connection: Literal['reachable', 'unreachable', 'unverified']
    modules: list[SupportModuleState] = Field(default_factory=list, max_length=16)
    engines: list[SupportEngineState] = Field(default_factory=list, max_length=2)
    recent_actions: list[SupportAction] = Field(default_factory=list, max_length=20)


SUPPORT_CLIENT_MODELS: tuple[type[Contract], ...] = (
    EngineRuntime, EngineRuntimeResponse, StopEngineRequest, SupportAction,
    SupportModuleState, SupportEngineState, SupportReport,
)
