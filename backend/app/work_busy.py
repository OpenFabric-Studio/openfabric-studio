"""Whether a song, voice, stem, or training job is still running.

The video page asks before it enables Create video. A video job is not
counted here; the page already tracks those itself.
"""
from __future__ import annotations

import asyncio

import httpx
from pydantic import TypeAdapter

from . import audio_exports, audiobook_review, audiobook_workflows, midi, optional_engines, reference_imports, stems, video_character_training, video_character_comparison, voice_comparisons, yue_jobs, yue_upload
from .config import MODELS
from .contracts import JsonObject, JsonValue
from .support_contracts import IdleState
from .orchestrator.manager import manager
from .orchestrator.state import ModelStatus
from .voice_build import work_busy as voice_work_busy

_json: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


def _count(value: object) -> int:
    if not isinstance(value, (int, float, str)) or isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, number)


def unwrap_data(payload: object) -> JsonObject:
    try:
        value: JsonValue = _json.validate_python(payload)
    except ValueError:
        return {}
    if not isinstance(value, dict):
        return {}
    if 'data' in value and 'code' in value:
        data = value.get('data')
        return data if type(value.get('code')) is int and value.get('code') == 200 and isinstance(data, dict) else {}
    return value


def model_work_busy(stats: JsonObject, training: JsonObject) -> bool:
    jobs = stats.get('jobs')
    if isinstance(jobs, dict) and (_count(jobs.get('queued')) or _count(jobs.get('running'))):
        return True
    return bool(_count(stats.get('queue_size')) or training.get('is_training'))


def model_work_state(stats: JsonObject, training: JsonObject) -> IdleState:
    """Only verified queue counts and training state can authorize idle work."""
    jobs, is_training = stats.get('jobs'), training.get('is_training')
    if not isinstance(jobs, dict) or type(is_training) is not bool:
        return 'unknown'
    values: list[JsonValue] = [jobs.get('queued'), jobs.get('running')]
    if 'queue_size' in stats:
        values.append(stats['queue_size'])
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
        return 'unknown'
    return 'busy' if model_work_busy(stats, training) else 'idle'


def local_work_busy() -> bool:
    from .music_queue_ownership import blocked
    if blocked():
        return True
    from .character_swaps import work_busy as character_swap_busy
    from .video_scenes import work_busy as video_scene_busy
    from .reading_media import work_busy as reading_busy
    from .module_jobs import work_busy as setup_busy
    from .audiobook_narration import work_busy as audiobook_busy
    from .cloud_music import work_busy as cloud_music_busy
    from .local_music import work_busy as local_music_busy
    from .stable_lora import work_busy as stable_lora_busy
    from .music_training import work_busy as music_training_busy
    from .training_captions import work_busy as captions_busy
    from .music_analysis import work_busy as music_analysis_busy
    from .music_artifacts import work_busy as music_artifacts_busy
    from .beat_jobs import work_busy as beats_busy
    from .replacement_jobs import work_busy as replacements_busy
    from .mashups import work_busy as mashups_busy
    from .beat_song_mix import work_busy as beat_song_mix_busy
    from .song_sources import work_busy as song_sources_busy
    from .regional_datasets import work_busy as regional_datasets_busy
    from .queued_music_sources import work_busy as queue_inputs_busy
    from .openrouter_client import work_busy as cloud_provider_busy
    return song_sources_busy() or queue_inputs_busy() or beat_song_mix_busy() or regional_datasets_busy() or mashups_busy() or replacements_busy() or beats_busy() or music_artifacts_busy() or music_analysis_busy() or captions_busy() or local_music_busy() or stable_lora_busy() or music_training_busy() or video_scene_busy() or character_swap_busy() or reading_busy() or cloud_music_busy() or cloud_provider_busy() or audiobook_busy() or audiobook_workflows.work_busy() or audiobook_review.work_busy() or setup_busy() or yue_upload.work_busy() or optional_engines.work_busy() or video_character_training.work_busy() or video_character_comparison.work_busy() or reference_imports.work_busy() or yue_jobs.work_busy() or audio_exports.work_busy() or voice_work_busy() or voice_comparisons.work_busy() or stems.work_busy() or midi.work_busy()


async def _get_json(client: httpx.AsyncClient, url: str) -> JsonObject:
    try:
        response = await client.get(url)
        if response.status_code != 200:
            return {}
        payload: object = response.json()
        return unwrap_data(payload)
    except (httpx.HTTPError, ValueError):
        return {}


async def ace_work_state(client: httpx.AsyncClient, base: str) -> IdleState:
    """Async dataset workers outlive the HTTP request that started them."""
    stats, training, labeling, preprocessing = await asyncio.gather(*(
        _get_json(client, f'{base}/{endpoint}') for endpoint in (
            'v1/stats', 'v1/training/status', 'v1/dataset/auto_label_status', 'v1/dataset/preprocess_status',
        )
    ))
    accounting = model_work_state(stats, training)
    if accounting != 'idle':
        return accounting
    statuses = [labeling.get('status'), preprocessing.get('status')]
    if any(status not in ('idle', 'running', 'completed', 'failed') for status in statuses):
        return 'unknown'
    return 'busy' if 'running' in statuses else 'idle'


async def ace_work_busy() -> bool:
    state = manager.state.models.get('ace_step')
    if state is None or state.status != ModelStatus.RUNNING:
        return False
    base = MODELS['ace_step'].proxy_target.rstrip('/')
    async with httpx.AsyncClient(timeout=1.5, trust_env=False) as client:
        return await ace_work_state(client, base) != 'idle'


async def other_work_busy() -> bool:
    if local_work_busy():
        return True
    return await ace_work_busy()
