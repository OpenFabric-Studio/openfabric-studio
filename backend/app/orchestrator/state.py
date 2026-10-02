from __future__ import annotations

import enum
from dataclasses import dataclass, field


class ModelStatus(str, enum.Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class ModelRuntimeState:
    model_id: str
    status: ModelStatus = ModelStatus.STOPPED
    error_message: str | None = None


@dataclass
class OrchestratorState:
    active_model: str | None = None
    models: dict[str, ModelRuntimeState] = field(default_factory=dict)
