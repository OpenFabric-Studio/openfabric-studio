"""Narrow queue adapter over existing durable music jobs and exact owned runs."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from typing import Literal

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, TypeAdapter

from . import ace_jobs, config, local_music, music_queue_ownership as ownership, native_yue, resource_admission, video_jobs, yue_jobs
from .contracts import JsonObject, SavedTrack
from .generation_contracts import AceGenerationSettings
from .local_music_contracts import LocalMusicError
from .music_lora_contracts import MusicLoraError
from .music_queue_contracts import MusicQueueError, MusicQueueItem, MusicQueuePayload
from .orchestrator.manager import manager
from .orchestrator.state import ModelStatus

logger = logging.getLogger(__name__)
_object = TypeAdapter(JsonObject)
_POLL_SECONDS = 0.25
_CANCEL_SECONDS = 120
_RECOVERY_SECONDS = 600
_NATIVE = ('ace_step', 'yue2')
ResultStatus = Literal['done', 'failed', 'cancelled', 'interrupted', 'blocked']


@dataclass
class EngineResult:
    status: ResultStatus
    tracks: list[SavedTrack] = field(default_factory=list)
    error_code: str = ''


class _AceHealth(BaseModel):
    model_config = ConfigDict(strict=True)
    models_initialized: bool
    loaded_model: str | None = None


class _AceMessage(BaseModel):
    model_config = ConfigDict(strict=True)
    message: str


async def _ace_request(client: httpx.AsyncClient, endpoint: str, payload: JsonObject | None = None) -> JsonObject:
    url = config.MODELS['ace_step'].proxy_target + endpoint
    response = await client.get(url) if payload is None else await client.post(url, json=payload)
    response.raise_for_status()
    result = _object.validate_json(response.content)
    if 'code' in result and 'data' in result:
        if result['code'] not in (0, 200):
            raise ValueError('ACE request rejected')
        return _object.validate_python(result['data'])
    return result


async def _prepare_ace() -> None:
    # Queue requests use explicit captions, so they do not require the LM.
    async with httpx.AsyncClient(timeout=600, follow_redirects=False) as client:
        health = _AceHealth.model_validate(await _ace_request(client, '/health'))
        if not health.models_initialized:
            _AceMessage.model_validate(await _ace_request(client, '/v1/init', {'init_llm': False}))
            health = _AceHealth.model_validate(await _ace_request(client, '/health'))
            if not health.models_initialized:
                raise ValueError('ACE model initialization incomplete')
        # This is session-global native state; browser adapter choices are irrelevant.
        _AceMessage.model_validate(await _ace_request(client, '/v1/lora/unload', {}))


def _owner_access() -> None:
    ownership.require_access()
    if not ownership.busy() or ownership.blocked():
        raise MusicQueueError('music_queue_cleanup_failed', 503)


def _task(item: MusicQueueItem) -> asyncio.Task[None] | None:
    identifier = item.child_job_id
    if identifier is None:
        return None
    if item.engine == 'ace_step':
        return ace_jobs._tasks.get(identifier)
    if item.engine == 'yue2':
        return yue_jobs._tasks.get(identifier)
    return local_music._tasks.get(identifier)


def _tracks(item: MusicQueueItem, tracks: list[SavedTrack]) -> list[SavedTrack]:
    result = {track.id: track for track in item.tracks}
    result.update((track.id, track) for track in tracks)
    return list(result.values())


def _terminal(item: MusicQueueItem, status: str, tracks: list[SavedTrack], code: str) -> EngineResult | None:
    saved = _tracks(item, tracks)
    if status == 'done':
        return EngineResult('done', saved, code)
    if status == 'cancelled':
        return EngineResult('cancelled', saved, code)
    if status == 'interrupted' or status == 'failed' and code in ('interrupted', 'local_music_interrupted'):
        return EngineResult('interrupted', saved, code)
    if status == 'failed':
        return EngineResult('failed', saved, code or 'generation_failed')
    return None


def inspect_child(item: MusicQueueItem) -> EngineResult | None:
    """Read recovery evidence without submitting, loading, or republishing audio."""
    identifier = item.child_job_id
    if identifier is None:
        return EngineResult('interrupted', item.tracks, 'music_queue_submission_interrupted')
    task = _task(item)
    if task is not None and not task.done():
        return None
    try:
        if item.engine == 'ace_step':
            ace = ace_jobs.get_job(identifier)
            return _terminal(item, ace.status, ace.tracks, ace.error_code)
        if item.engine == 'yue2':
            yue = yue_jobs.get_job(identifier)
            tracks = [yue.track] if yue.track is not None else []
            if identifier in yue_jobs._leases or native_yue._quarantined or yue.status == 'stopping':
                return EngineResult('blocked', _tracks(item, tracks), 'music_queue_cleanup_failed')
            return _terminal(item, yue.status, tracks, yue.error_code)
        local = local_music.get(identifier)
        tracks = [local.track] if local.track is not None else []
        if identifier in local_music._unverified:
            return EngineResult('blocked', _tracks(item, tracks), 'music_queue_cleanup_failed')
        return _terminal(item, local.status, tracks, local.error_code or '')
    except (HTTPException, LocalMusicError) as error:
        status = error.status_code if isinstance(error, HTTPException) else error.status
        if status == 404:
            return EngineResult('interrupted', item.tracks, 'music_queue_child_missing')
        logger.exception('Queue child metadata unavailable')
        return EngineResult('blocked', item.tracks, 'music_queue_child_unavailable')
    except Exception:
        logger.exception('Queue child metadata invalid')
        return EngineResult('blocked', item.tracks, 'music_queue_child_unavailable')


async def recover_child(item: MusicQueueItem) -> EngineResult | None:
    """Let an existing recovery publisher finish; never launch another child."""
    task = _task(item)
    if task is not None and not task.done():
        try:
            async with asyncio.timeout(_RECOVERY_SECONDS):
                await asyncio.shield(asyncio.gather(task, return_exceptions=True))
        except TimeoutError:
            return EngineResult('blocked', item.tracks, 'music_queue_cleanup_failed')
    return inspect_child(item)


def _ace_params(settings: AceGenerationSettings, seed: int) -> JsonObject:
    if (settings.mode != 'custom' or settings.useRefAudio or settings.taskType != 'text2music' or
            settings.voiceId is not None or settings.loraName is not None or settings.styleReferenceRequiresReupload or
            settings.loraRequiresReselection or settings.batchSize != 1 or settings.durationAuto or
            settings.randomSeed or settings.seed != seed or settings.audioFormat != 'wav' or settings.useCotCaption):
        raise MusicQueueError('music_queue_plan_invalid', 422)
    params: JsonObject = {'prompt': settings.customPrompt.strip(),
        'lyrics': '' if settings.instrumental else settings.customLyrics,
        'audio_duration': settings.duration, 'seed': seed, 'use_random_seed': False,
        'batch_size': 1, 'audio_format': 'wav', 'use_cot_caption': False,
        'model': settings.selectedModel or config.ACE_STEP_DIT_MODEL,
        '_generation_settings': _object.validate_json(settings.model_dump_json())}
    for key, value in (('bpm', settings.bpm), ('key_scale', settings.keyScale),
                       ('time_signature', settings.timeSignature), ('vocal_language', settings.vocalLanguage),
                       ('inference_steps', settings.inferenceSteps), ('guidance_scale', settings.guidanceScale)):
        if value is not None and value != '':
            params[key] = value
    return params


class EngineSession:
    """One queue reservation owns a sequence of child jobs and native identities."""
    def __init__(self) -> None:
        self._owned: dict[str, str] = {}
        self._unverified: set[str] = set()
        self._children: dict[str, MusicQueueItem] = {}
        self._current: str | None = None

    async def _stop(self, engine: str, instance: str) -> None:
        if (engine not in manager._processes and manager.owned_instance(engine) is None and
                manager.state.models[engine].status in (ModelStatus.STOPPED, ModelStatus.ERROR)):
            self._owned.pop(engine, None)
            return
        if manager.owned_instance(engine) != instance:
            raise MusicQueueError('music_queue_cleanup_failed', 503)
        try:
            await manager.stop_owned(engine, instance)
        except Exception as error:
            raise MusicQueueError('music_queue_cleanup_failed', 503) from error
        if engine in manager._processes or manager.owned_instance(engine) is not None:
            raise MusicQueueError('music_queue_cleanup_failed', 503)
        self._owned.pop(engine, None)

    async def ensure(self, payload: MusicQueuePayload) -> None:
        _owner_access()
        if self._unverified:
            raise MusicQueueError('music_queue_cleanup_failed', 503)
        if self._current == payload.engine:
            if payload.engine not in _NATIVE:
                return
            if self._owned.get(payload.engine) == manager.owned_instance(payload.engine):
                return
            raise MusicQueueError('music_queue_cleanup_failed', 503)
        if self._current is not None:
            await self.close()
        async with resource_admission.admission_lock:
            if resource_admission.native_work_inflight():
                raise MusicQueueError('music_queue_cleanup_failed', 503)
            for engine, state in manager.state.models.items():
                instance = manager.owned_instance(engine)
                if instance is None:
                    if engine in manager._processes or state.status not in (ModelStatus.STOPPED, ModelStatus.ERROR):
                        self._unverified.add(engine)
                        raise MusicQueueError('music_queue_cleanup_failed', 503)
                    continue
                previous = self._owned.get(engine)
                if previous is not None and previous != instance:
                    raise MusicQueueError('music_queue_cleanup_failed', 503)
                self._owned[engine] = instance
                if engine != payload.engine:
                    await self._stop(engine, instance)
            if payload.engine in _NATIVE:
                try:
                    await manager.switch_to(payload.engine)
                    instance = manager.owned_instance(payload.engine)
                    if instance is None:
                        raise MusicQueueError('music_queue_cleanup_failed', 503)
                    self._owned[payload.engine] = instance
                    if payload.engine == 'ace_step':
                        await _prepare_ace()
                except BaseException as error:
                    if payload.engine in manager._processes and payload.engine not in self._owned:
                        self._unverified.add(payload.engine)
                    if isinstance(error, (asyncio.CancelledError, MusicQueueError)):
                        raise
                    logger.exception('Queue native engine startup failed')
                    raise MusicQueueError('music_queue_engine_start_failed', 503) from error
        self._current = payload.engine

    async def submit(self, item: MusicQueueItem) -> str:
        _owner_access()
        payload = item.payload
        if self._current != payload.engine:
            raise MusicQueueError('music_queue_engine_not_ready', 409)
        if payload.engine in _NATIVE and self._owned.get(payload.engine) != manager.owned_instance(payload.engine):
            raise MusicQueueError('music_queue_cleanup_failed', 503)
        if payload.engine == 'ace_step':
            params = _ace_params(payload.request, item.resolved_seed)
            try:
                async with resource_admission.native_admission(video_jobs.work_busy):
                    released = await ace_jobs.submit(params, payload.request.customPrompt.strip()[:60], None, None)
                    identifier = released.task_id
                    # Lease release awaits: retain the accepted child before that boundary.
                    self._children[item.id] = item.model_copy(update={'child_job_id': identifier})
            except resource_admission.ResourceBusyError as error:
                raise MusicQueueError(error.code) from error
            except Exception as error:
                logger.exception('Queue ACE submission could not be proven')
                raise MusicQueueError('music_queue_submission_interrupted', 503) from error
        elif payload.engine == 'yue2':
            if payload.request.voice_id is not None or payload.request.seed != item.resolved_seed:
                raise MusicQueueError('music_queue_plan_invalid', 422)
            try:
                identifier = yue_jobs.submit(payload.request).id
            except (HTTPException, MusicLoraError) as error:
                code = error.code if isinstance(error, MusicLoraError) else 'music_queue_submission_failed'
                status = error.status if isinstance(error, MusicLoraError) else error.status_code
                raise MusicQueueError(code, status) from error
            except Exception as error:
                logger.exception('Queue YuE submission could not be proven')
                raise MusicQueueError('music_queue_submission_interrupted', 503) from error
        else:
            if payload.request.seed != item.resolved_seed:
                raise MusicQueueError('music_queue_plan_invalid', 422)
            try:
                identifier = (await local_music.submit(payload.request)).id
            except (LocalMusicError, MusicLoraError) as error:
                raise MusicQueueError(error.code, error.status) from error
            except HTTPException as error:
                raise MusicQueueError('music_queue_submission_failed', error.status_code) from error
        self._children[item.id] = item.model_copy(update={'child_job_id': identifier})
        return identifier

    def _before_yue(self, item: MusicQueueItem) -> str | None:
        if item.engine != 'yue2' or 'yue2' not in self._owned:
            return None
        previous = self._owned['yue2']
        if manager.owned_instance('yue2') != previous:
            self._unverified.add('yue2')
            raise MusicQueueError('music_queue_cleanup_failed', 503)
        return previous

    def _after_yue(self, previous: str | None) -> None:
        if previous is None:
            return
        _owner_access()
        # The owned child can restart YuE while stopping inference. All external
        # native mutation is excluded by the inherited queue reservation.
        instance = manager.owned_instance('yue2')
        if instance is not None:
            self._owned['yue2'] = instance
        elif 'yue2' not in manager._processes:
            self._owned.pop('yue2', None)
        else:
            self._unverified.add('yue2')
            raise MusicQueueError('music_queue_cleanup_failed', 503)

    async def _wait(self, item: MusicQueueItem, seconds: float) -> EngineResult:
        async with asyncio.timeout(seconds):
            while True:
                result = inspect_child(item)
                if result is not None:
                    return result
                await asyncio.sleep(_POLL_SECONDS)

    async def wait(self, item: MusicQueueItem) -> EngineResult:
        previous = self._before_yue(item)
        try:
            result = await self._wait(item, 7200 if item.engine not in _NATIVE else 10800)
        except TimeoutError:
            await self.cancel(item)
            timed_out = inspect_child(item)
            if timed_out is None or timed_out.status == 'blocked':
                return EngineResult('blocked', item.tracks, 'music_queue_cleanup_failed')
            result = timed_out
            if result.status != 'done':
                # Native cancellation alone does not prove accelerator work stopped.
                result = EngineResult('interrupted' if item.engine in _NATIVE else 'failed',
                    result.tracks, 'music_queue_generation_timeout')
        self._after_yue(previous)
        if result.status != 'blocked':
            self._children.pop(item.id, None)
        return result

    async def cancel(self, item: MusicQueueItem) -> None:
        _owner_access()
        identifier = item.child_job_id
        if identifier is None:
            return
        previous = self._before_yue(item)
        try:
            if item.engine == 'ace_step':
                await ace_jobs.cancel(identifier)
            elif item.engine == 'yue2':
                await yue_jobs.cancel(identifier)
            else:
                await local_music.cancel(identifier)
            result = await self._wait(item, _CANCEL_SECONDS)
            if result.status == 'blocked':
                raise MusicQueueError('music_queue_cleanup_failed', 503)
            self._after_yue(previous)
            self._children.pop(item.id, None)
        except Exception as error:
            raise MusicQueueError('music_queue_cleanup_failed', 503) from error

    async def retry_save(self, item: MusicQueueItem) -> EngineResult:
        _owner_access()
        identifier = item.child_job_id
        if identifier is None:
            raise MusicQueueError('music_queue_child_missing', 409)
        result = inspect_child(item)
        if result is None or result.status == 'blocked':
            raise MusicQueueError('music_queue_cleanup_failed', 503)
        if result.status == 'done':
            return result
        self._children[item.id] = item
        try:
            if item.engine == 'ace_step':
                await ace_jobs.retry_save(identifier)
            elif item.engine == 'yue2':
                yue_jobs.recover()
            else:
                await local_music.recover()
        except (HTTPException, LocalMusicError) as error:
            missing = (isinstance(error, HTTPException) and
                (error.status_code == 409 and error.detail == 'no_results_to_retry' or
                 error.status_code == 404 and error.detail == 'job_not_found') or
                isinstance(error, LocalMusicError) and error.status == 404 and error.code == 'local_music_job_not_found')
            if not missing:
                raise
            self._children.pop(item.id, None)
            raise MusicQueueError('music_queue_save_retry_unavailable') from error
        return await self.wait(item)

    async def close(self) -> None:
        _owner_access()
        for item in tuple(self._children.values()):
            await self.cancel(item)
        if self._unverified:
            raise MusicQueueError('music_queue_cleanup_failed', 503)
        async with resource_admission.admission_lock:
            if resource_admission.native_work_inflight():
                raise MusicQueueError('music_queue_cleanup_failed', 503)
            for engine, instance in tuple(self._owned.items()):
                await self._stop(engine, instance)
        self._current = None
