"""Idle admission for stopping exact persistent engines owned by this backend."""
from __future__ import annotations

import asyncio
import logging

import httpx

from .config import MODELS
from .job_lifecycle import await_cleanup
from .orchestrator.manager import manager
from .orchestrator.state import ModelStatus
from .support_contracts import EngineRuntime, EngineRuntimeResponse, IdleState, OwnedEngineId, SupportErrorCode
from . import resource_admission

logger = logging.getLogger(__name__)
ENGINE_IDS: tuple[OwnedEngineId, ...] = ('ace_step', 'yue2')


class EngineControlError(Exception):
    def __init__(self, code: SupportErrorCode) -> None:
        self.code = code
        super().__init__(code)


async def _idle_state() -> IdleState:
    """Fail closed on unavailable/malformed upstream job accounting.

    Called while admission is held for a mutation, so newly submitted native,
    video, optional-engine and setup work cannot enter the stop window.
    """
    from . import ace_jobs, video_jobs
    from .speaker_review import work_busy as speaker_busy
    from .work_busy import local_work_busy
    try:
        if resource_admission.native_work_inflight() or video_jobs.work_busy() or local_work_busy() or ace_jobs.work_busy() or speaker_busy():
            return 'busy'
        state = manager.state.models.get('ace_step')
        if state is None or state.status != ModelStatus.RUNNING:
            return 'idle'
        base = MODELS['ace_step'].proxy_target.rstrip('/')
        async with httpx.AsyncClient(timeout=1.5, trust_env=False) as client:
            stats, training = await asyncio.gather(client.get(f'{base}/v1/stats'), client.get(f'{base}/v1/training/status'))
        if stats.status_code != 200 or training.status_code != 200:
            return 'unknown'
        stats_value: object = stats.json()
        training_value: object = training.json()
        return _accounting_state(stats_value, training_value)
    except Exception:  # noqa: BLE001 - no unavailable accounting may authorize a stop
        logger.exception('Could not verify idle engine accounting')
        return 'unknown'


def _unwrap(value: object) -> object:
    if isinstance(value, dict) and 'data' in value and 'code' in value:
        return value.get('data') if type(value.get('code')) is int and value.get('code') == 200 else None
    return value


def _accounting_state(stats_value: object, training_value: object) -> IdleState:
    stats, training = _unwrap(stats_value), _unwrap(training_value)
    if not isinstance(stats, dict) or not isinstance(training, dict):
        return 'unknown'
    jobs = stats.get('jobs')
    is_training = training.get('is_training')
    if not isinstance(jobs, dict) or type(is_training) is not bool:
        return 'unknown'
    values: list[object] = [jobs.get('queued'), jobs.get('running')]
    if 'queue_size' in stats:
        values.append(stats['queue_size'])
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
        return 'unknown'
    return 'busy' if is_training or any(isinstance(value, int) and value > 0 for value in values) else 'idle'


async def engine_status() -> EngineRuntimeResponse:
    idle_state = await _idle_state()
    engines = []
    for model_id in ENGINE_IDS:
        state = manager.state.models[model_id].status
        instance = manager.owned_instance(model_id)
        engines.append(EngineRuntime(id=model_id, state=state.value, owned=instance is not None,
            instance_id=instance, idle_state=idle_state, can_stop=instance is not None and idle_state == 'idle'))
    return EngineRuntimeResponse(engines=engines)


async def _stop_engine(model_id: OwnedEngineId, instance_id: str) -> None:
    async with resource_admission.admission_lock:
        if resource_admission.native_work_inflight():
            raise EngineControlError('engine_busy')
        idle = await _idle_state()
        if idle != 'idle':
            raise EngineControlError('engine_busy' if idle == 'busy' else 'engine_state_unverified')
        if manager._instances.get(model_id) != instance_id:
            raise EngineControlError('engine_changed')
        if manager.owned_instance(model_id) != instance_id:
            raise EngineControlError('engine_not_owned')
        try:
            await manager.stop_owned(model_id, instance_id)
        except ValueError as exc:
            raise EngineControlError('engine_changed' if str(exc) == 'engine_changed' else 'engine_not_owned') from exc
        except RuntimeError as exc:
            logger.exception('Owned engine stop did not drain')
            raise EngineControlError('engine_stop_failed') from exc


async def stop_engine(model_id: OwnedEngineId, instance_id: str) -> None:
    await await_cleanup(_stop_engine(model_id, instance_id))
