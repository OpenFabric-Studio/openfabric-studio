"""Whether a song, voice, stem, or training job is still running.

The video page asks before it enables Create video. A video job is not
counted here; the page already tracks those itself.
"""
from __future__ import annotations

import asyncio

import httpx

from . import audio_exports, midi, reference_imports, stems, voice_comparisons, yue_jobs
from .config import MODELS
from .orchestrator.manager import manager
from .orchestrator.state import ModelStatus
from .voice_build import work_busy as voice_work_busy


def _count(value: object) -> int:
    if not isinstance(value, (int, float, str)) or isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, number)


def unwrap_data(payload: object) -> dict:
    if isinstance(payload, dict) and "data" in payload and "code" in payload:
        data = payload.get("data")
        return data if isinstance(data, dict) else {}
    return payload if isinstance(payload, dict) else {}


def model_work_busy(stats: dict, training: dict) -> bool:
    jobs = stats.get("jobs") if isinstance(stats.get("jobs"), dict) else {}
    if _count(jobs.get("queued")) or _count(jobs.get("running")) or _count(stats.get("queue_size")):
        return True
    return bool(training.get("is_training"))


def local_work_busy() -> bool:
    from .module_jobs import work_busy as setup_busy
    from .audiobook_narration import work_busy as audiobook_busy
    return audiobook_busy() or setup_busy() or reference_imports.work_busy() or yue_jobs.work_busy() or audio_exports.work_busy() or voice_work_busy() or voice_comparisons.work_busy() or stems.work_busy() or midi.work_busy()


async def _get_json(client: httpx.AsyncClient, url: str) -> dict:
    try:
        response = await client.get(url)
    except httpx.HTTPError:
        return {}
    if response.status_code != 200:
        return {}
    try:
        return unwrap_data(response.json())
    except ValueError:
        return {}


async def ace_work_busy() -> bool:
    state = manager.state.models.get("ace_step")
    if state is None or state.status != ModelStatus.RUNNING:
        return False
    base = MODELS["ace_step"].proxy_target.rstrip("/")
    async with httpx.AsyncClient(timeout=1.5) as client:
        stats, training = await asyncio.gather(
            _get_json(client, f"{base}/v1/stats"),
            _get_json(client, f"{base}/v1/training/status"),
        )
    return model_work_busy(stats, training)


async def other_work_busy() -> bool:
    if local_work_busy():
        return True
    return await ace_work_busy()
