from __future__ import annotations

import asyncio
from dataclasses import replace
import logging
import uuid

from ..config import ALLOW_CONCURRENT_MODELS, MODELS, ProcessSpec
from ..contracts import JsonObject
from ..job_lifecycle import await_cleanup
from .process import ManagedProcess, StartCancelled
from .state import ModelRuntimeState, ModelStatus, OrchestratorState

logger = logging.getLogger("orchestrator")

# How often the watchdog checks that a RUNNING model's processes are alive.
WATCH_INTERVAL = 2.0


class OrchestratorManager:
    """Owns the on/off state of both models and enforces mutual exclusion.

    A single asyncio.Lock serializes switch_to()/stop_active() calls so that
    rapid clicks in the UI can't start two model process trees concurrently
    or interleave a stop with a start. Stop and shutdown do not wait behind a
    start that is still loading: they cancel it first, and a switch that was
    queued before the stop is dropped.

    A watchdog task marks a RUNNING model as ERROR as soon as one of its
    processes exits (e.g. CUDA out of memory), so the proxy and the job
    routes see it as inactive instead of forwarding to a dead port.
    """

    def __init__(self) -> None:
        self.state = OrchestratorState(models={mid: ModelRuntimeState(mid) for mid in MODELS})
        self._processes: dict[str, list[ManagedProcess]] = {}
        self._instances: dict[str, str] = {}
        self._launch_specs: dict[str, tuple[ProcessSpec, ...]] = {}
        self._lock = asyncio.Lock()
        self._cancel_start: dict[str, asyncio.Event] = {}
        self._stop_requests = 0
        self._watchdog: asyncio.Task[None] | None = None

    def start_watchdog(self) -> None:
        if self._watchdog is None:
            self._watchdog = asyncio.create_task(self._watch())

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(WATCH_INTERVAL)
            for model_id in list(MODELS):
                try:
                    if self._mark_if_exited(model_id):
                        await self._stop_processes(model_id)
                except Exception:  # noqa: BLE001 - the watchdog must keep running
                    logger.exception("watchdog check failed for %s", model_id)

    def _mark_if_exited(self, model_id: str) -> bool:
        rs = self.state.models[model_id]
        if rs.status != ModelStatus.RUNNING:
            return False
        dead = next((p for p in self._processes.get(model_id, []) if not p.is_running), None)
        if dead is None:
            return False
        summary = dead.exit_summary()
        logger.error("model %s: %s", model_id, summary)
        rs.status = ModelStatus.ERROR
        rs.error_message = summary
        if self.state.active_model == model_id:
            self.state.active_model = None
        return True

    def _cancel_pending_starts(self) -> None:
        self._stop_requests += 1
        for event in self._cancel_start.values():
            event.set()

    def status_snapshot(self) -> JsonObject:
        return {
            "active_model": self.state.active_model,
            "models": {
                mid: {
                    "id": mid,
                    "label": MODELS[mid].label,
                    "status": rs.status.value,
                    "error": rs.error_message,
                }
                for mid, rs in self.state.models.items()
            },
        }

    async def switch_to(self, model_id: str, *, process_specs: tuple[ProcessSpec, ...] | None = None) -> None:
        if model_id not in MODELS:
            raise ValueError(f"unknown model '{model_id}'")
        specs = self._copy_specs(tuple(MODELS[model_id].processes) if process_specs is None else process_specs)
        if not specs:
            raise ValueError('Engine launch requires process specs')
        stop_requests = self._stop_requests
        async with self._lock:
            if self._stop_requests != stop_requests:
                raise StartCancelled(f"start of '{model_id}' was cancelled")
            if self._mark_if_exited(model_id):
                await self._stop_processes(model_id)
            if self.state.models[model_id].status == ModelStatus.RUNNING and self._launch_specs.get(model_id) == specs:
                self.state.active_model = model_id
                return
            # A failed drain retains the old tree. Never replace its registry
            # with a new run, including a switch back to the same model.
            if model_id in self._processes:
                await self._stop_model(model_id)
            if not ALLOW_CONCURRENT_MODELS:
                for current in tuple(self._processes):
                    if current != model_id:
                        await self._stop_model(current)
            if process_specs is None:
                await self._start_model(model_id)
            else:
                await self._start_model(model_id, process_specs=specs)

    async def stop_active(self) -> None:
        self._cancel_pending_starts()
        async with self._lock:
            if self.state.active_model is not None:
                await self._stop_model(self.state.active_model)

    def owned_instance(self, model_id: str) -> str | None:
        """Return an opaque identity only for a verified, running owned tree."""
        if model_id not in MODELS or self.state.models[model_id].status != ModelStatus.RUNNING:
            return None
        instance = self._instances.get(model_id)
        processes = self._processes.get(model_id, [])
        specs = self._launch_specs.get(model_id, tuple(MODELS[model_id].processes))
        if instance is None or len(processes) != len(specs):
            return None
        try:
            for process, spec in zip(processes, specs):
                if process.spec != spec:
                    return None
                process.require_owned()
        except RuntimeError:
            return None
        return instance

    async def stop_owned(self, model_id: str, instance_id: str) -> None:
        """Stop one exact resident run; caller holds idle resource admission."""
        async with self._lock:
            if self._instances.get(model_id) != instance_id:
                raise ValueError('engine_changed')
            if self.owned_instance(model_id) != instance_id:
                raise ValueError('engine_not_owned')
            self._cancel_pending_starts()
            await self._stop_model(model_id)

    async def restart_model(self, model_id: str) -> None:
        """Reset an owned model without resurrecting a user's Stop request.

        Callers must hold the model's exclusive admission lease. Inference
        cancellation uses this method only after establishing run ownership.
        """
        if model_id not in MODELS:
            raise ValueError('Unknown model')
        stop_requests = self._stop_requests
        async with self._lock:
            if self._stop_requests != stop_requests:
                await self._stop_model(model_id)
                return
            status = self.state.models[model_id].status
            if status == ModelStatus.STOPPED and not self._processes.get(model_id):
                return
            if status not in (ModelStatus.RUNNING, ModelStatus.ERROR, ModelStatus.STOPPING):
                raise StartCancelled('Engine restart interrupted')
            specs = self._launch_specs.get(model_id, tuple(MODELS[model_id].processes))
            await self._stop_model(model_id)
            if self._stop_requests == stop_requests:
                if specs == tuple(MODELS[model_id].processes):
                    await self._start_model(model_id)
                else:
                    await self._start_model(model_id, process_specs=specs)

    async def stop_all(self) -> None:
        self._cancel_pending_starts()
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None
        async with self._lock:
            # Everything still holding processes: RUNNING models, and any the
            # watchdog marked ERROR but had not finished cleaning up.
            for model_id in list(self._processes):
                await self._stop_model(model_id)

    @staticmethod
    def _copy_specs(specs: tuple[ProcessSpec, ...]) -> tuple[ProcessSpec, ...]:
        return tuple(replace(spec, cmd=list(spec.cmd), env=dict(spec.env), extra_path_dirs=list(spec.extra_path_dirs))
                     for spec in specs)

    async def _start_model(self, model_id: str, *, process_specs: tuple[ProcessSpec, ...] | None = None) -> None:
        if model_id in self._processes:
            raise RuntimeError('Engine shutdown is incomplete')
        specs = self._copy_specs(tuple(MODELS[model_id].processes) if process_specs is None else process_specs)
        self._launch_specs[model_id] = self._copy_specs(specs)
        rs = self.state.models[model_id]
        rs.status = ModelStatus.STARTING
        rs.error_message = None
        started: list[ManagedProcess] = []
        # Ownership begins at spawn, including partial/failed startup trees.
        self._processes[model_id] = started
        cancel = self._cancel_start[model_id] = asyncio.Event()
        try:
            for spec in specs:
                if cancel.is_set():
                    raise StartCancelled(f"start of '{model_id}' was cancelled")
                proc = ManagedProcess(spec)
                proc.start()
                started.append(proc)
                await proc.wait_healthy(cancel)
            self._instances[model_id] = uuid.uuid4().hex
            rs.status = ModelStatus.RUNNING
            self.state.active_model = model_id
        except BaseException as exc:
            cancelled = isinstance(exc, (StartCancelled, asyncio.CancelledError))
            if cancelled:
                logger.info("start of model %s cancelled", model_id)
            else:
                logger.exception("failed to start model %s", model_id)
                rs.error_message = str(exc)
            async def drain_start() -> None:
                rs.status = ModelStatus.STOPPING
                await self._stop_processes(model_id)
                rs.status = ModelStatus.STOPPED if cancelled else ModelStatus.ERROR
            # A second cancellation must not discard an unverified live tree.
            await await_cleanup(drain_start())
            raise
        finally:
            self._cancel_start.pop(model_id, None)

    async def _stop_processes(self, model_id: str) -> None:
        # Reverse start order: the dependent process (e.g. YuE2's web UI)
        # stops before the process it depends on (the inference server).
        # A process leaves the list only once it has stopped, so if this is
        # interrupted, stop_all() still finds the ones that are left.
        procs = self._processes.get(model_id, [])
        while procs:
            proc = procs[-1]
            await proc.stop()
            if procs and procs[-1] is proc:
                procs.pop()
        if self._processes.get(model_id) is procs:
            del self._processes[model_id]
            self._instances.pop(model_id, None)
            self._launch_specs.pop(model_id, None)

    async def _stop_model(self, model_id: str) -> None:
        rs = self.state.models[model_id]
        rs.status = ModelStatus.STOPPING
        await self._stop_processes(model_id)
        rs.status = ModelStatus.STOPPED
        rs.error_message = None
        if self.state.active_model == model_id:
            self.state.active_model = None


manager = OrchestratorManager()
