"""Deterministic brief adaptation; readiness checks never start a model."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import config, local_music_runtime, music_lora
from .generation_contracts import AceGenerationSettings, YueGenerationSettings
from .local_music_contracts import LocalMusicRequest
from .module_catalog import DEFINITIONS, ModuleEnvironment, configured_environment, engine_python, supported
from .module_evidence import environment_fingerprint, environment_verified
from .module_runtime import RuntimeEvidence, RuntimeTarget, runtime_status
from .music_lora_contracts import MusicAdapter, MusicLoraError
from .music_queue_contracts import (
    MusicQueueAceRequest, MusicQueueAdjustment, MusicQueueBrief, MusicQueueCatalog,
    MusicQueueEngine, MusicQueueEngineSelection, MusicQueueHeartRequest, MusicQueueModelPlan,
    MusicQueueNormalizedItem, MusicQueuePayload, MusicQueuePreview, MusicQueuePreviewRequest,
    MusicQueueReadiness, MusicQueueStableRequest, MusicQueueYueRequest,
)
from .orchestrator.manager import manager
from .orchestrator.state import ModelStatus
from .yue_contracts import YueOptions, YueSubmitRequest

ENGINES: tuple[MusicQueueEngine, ...] = ('ace_step', 'yue2', 'heartmula', 'stable_audio3')
_WEIGHT_NAMES = ('model.safetensors', 'pytorch_model.bin', 'diffusion_pytorch_model.safetensors',
                 'diffusion_pytorch_model.bin')


class _WeightIndex(BaseModel):
    model_config = ConfigDict(strict=True)
    weight_map: dict[str, str] = Field(min_length=1, max_length=100000)


def _present(paths: tuple[Path, ...]) -> bool:
    try:
        return all(path.is_file() and path.stat().st_size > 0 for path in paths)
    except OSError:
        return False


def _ace_component(directory: Path) -> bool:
    for name in _WEIGHT_NAMES:
        if _present((directory / name,)):
            return True
        index = directory / (name + '.index.json')
        try:
            if not index.is_file() or index.stat().st_size > 8 * 1024**2:
                continue
            weights = _WeightIndex.model_validate_json(index.read_bytes())
            files = tuple(directory / value for value in set(weights.weight_map.values()))
            if all(path.resolve().is_relative_to(directory.resolve()) for path in files) and _present(files):
                return True
        except (OSError, ValueError, ValidationError):
            continue
    return False


def _yue_root(environment: ModuleEnvironment) -> Path:
    if environment.paths['yue2'].resolve() == config.YUE2_DIR.resolve():
        return config.YUE2_MODEL_PATH
    return environment.paths['yue2'] / 'models/Yue2-3B-GGUF'


def _weights_ready(environment: ModuleEnvironment, selection: MusicQueueEngineSelection) -> bool:
    if selection.engine == 'ace_step':
        root = Path(os.environ.get('ACESTEP_CHECKPOINTS_DIR') or environment.paths['ace_step'] / 'checkpoints').expanduser()
        # Match the reviewed downloader's components, including real shards behind indexes.
        return all(_ace_component(root / component) for component in
            (config.ACE_STEP_DIT_MODEL, 'vae', 'Qwen3-Embedding-0.6B', config.ACE_STEP_LM_MODEL))
    if selection.engine == 'yue2':
        root = _yue_root(environment)
        return _present(tuple(root / name for name in (
            f'yue2-3b-{selection.precision}.gguf', 'yue2-vae-f16.gguf',
            'sidecars/yue2-model-config.json', 'sidecars/yue2-generation-config.json',
            'sidecars/yue2-qwen.tiktoken', 'sidecars/yue2-vae-config.json')))
    return _present(local_music_runtime.weight_paths(environment, selection.engine, selection.variant))


def _native_installed(environment: ModuleEnvironment, engine: Literal['ace_step', 'yue2']) -> bool:
    root = environment.paths[engine]
    if not all((root / marker).is_file() for marker in DEFINITIONS[engine].source_markers):
        return False
    if engine == 'ace_step':
        if environment_verified(environment, engine):
            return True
        # External installs lack managed receipts; require their real launcher and environment footprint.
        launcher = root / ('.venv/Scripts/acestep-api.exe' if environment.platform == 'win32' else '.venv/bin/acestep-api')
        return _present((engine_python(root, environment.platform), launcher)) and environment_fingerprint(environment, engine) is not None
    definition = config.MODELS['yue2']
    if definition.processes and root.resolve() == definition.processes[0].cwd.resolve():
        return _present((Path(definition.processes[0].cmd[0]),))
    preset = {'darwin': 'macos-metal-release', 'win32': 'windows-cuda-release', 'linux': 'linux-cuda-release'}.get(environment.platform)
    return preset is not None and _present((root / 'build' / preset / 'bin' /
        ('audiocpp_server.exe' if environment.platform == 'win32' else 'audiocpp_server'),))


async def _native_readiness(environment: ModuleEnvironment, engine: Literal['ace_step', 'yue2']) -> MusicQueueReadiness:
    support = supported(engine, environment)
    installed = support and _native_installed(environment, engine)
    selection = MusicQueueEngineSelection(engine=engine)
    weights = _weights_ready(environment, selection)
    if engine == 'yue2' and not weights:
        weights = _weights_ready(environment, MusicQueueEngineSelection(engine=engine, precision='q4_0'))
    state = manager.state.models[engine]
    definition = config.MODELS[engine]
    configured = bool(definition.processes and environment.paths[engine].resolve() == definition.processes[0].cwd.resolve())
    status = state.status if configured else ModelStatus.STOPPED
    instance = manager.owned_instance(engine) if configured else None
    runtime = RuntimeEvidence()
    if status == ModelStatus.RUNNING and instance is not None:
        runtime = await runtime_status(environment, RuntimeTarget(engine, definition.proxy_target))
        installed = installed or support and runtime.installed
    error = ('music_queue_runtime_unsupported' if not support else
             'music_queue_engine_not_installed' if not installed else
             'music_queue_weights_missing' if not weights else
             'music_queue_engine_transition' if status in (ModelStatus.STARTING, ModelStatus.STOPPING) else
             'music_queue_engine_cleanup_required' if status == ModelStatus.ERROR else
             'music_queue_engine_not_owned' if status == ModelStatus.RUNNING and instance is None else
             'music_queue_runtime_unverified' if status == ModelStatus.RUNNING and not runtime.installed else '')
    return MusicQueueReadiness(engine=engine, supported=support, installed=installed, weights_ready=weights,
        managed_status=status.value, owned_instance=instance, ready=not error, error_code=error)


async def catalog(environment: ModuleEnvironment | None = None) -> MusicQueueCatalog:
    """Inspect source receipts, local files and exact owned native services only."""
    env = environment or configured_environment()
    ace, yue = await asyncio.gather(_native_readiness(env, 'ace_step'), _native_readiness(env, 'yue2'))
    engines = [ace, yue]
    for status in local_music_runtime.engine_status(env).engines:
        error = ('music_queue_runtime_unsupported' if not status.supported else
                 'music_queue_engine_not_installed' if not status.installed else
                 'music_queue_weights_missing' if not status.weights_ready else '')
        engines.append(MusicQueueReadiness(engine=status.id, supported=status.supported, installed=status.installed,
            weights_ready=status.weights_ready, managed_status='one_shot', ready=not error, error_code=error))
    return MusicQueueCatalog(engines=engines, adapters=music_lora.list_adapters().adapters)


def _directions(brief: MusicQueueBrief) -> str:
    parts = [brief.description]
    if brief.bpm is not None:
        parts.append(f'Tempo: {brief.bpm:g} BPM')
    if brief.key.strip():
        parts.append('Key: ' + brief.key)
    if brief.language.strip():
        parts.append('Language: ' + brief.language)
    return '\n'.join(parts)


def _adapters(selection: MusicQueueEngineSelection) -> list[MusicAdapter]:
    selected = ((selection.ar_adapter, 'ar'), (selection.nar_adapter, 'nar')) if selection.engine == 'yue2' else ((selection.adapter, selection.variant),)
    if selection.engine == 'yue2' and any(adapter is not None for adapter, _ in selected) and not config.YUE2_LORA_AVAILABLE:
        raise MusicLoraError('music_lora_runtime_unsupported')
    identities: list[MusicAdapter] = []
    for adapter, target in selected:
        if adapter is not None:
            identity = music_lora.get(adapter.id)
            if (identity.engine, identity.target) != (selection.engine, target):
                raise MusicLoraError('music_lora_target_mismatch')
            music_lora.resolve(adapter, engine=selection.engine, target=target)
            if music_lora.get(adapter.id) != identity:
                raise MusicLoraError('music_lora_adapter_changed')
            identities.append(identity)
    return identities


def _payload(brief: MusicQueueBrief, selection: MusicQueueEngineSelection, duration: int | None) -> MusicQueuePayload:
    if selection.engine == 'ace_step':
        return MusicQueueAceRequest(engine='ace_step', request=AceGenerationSettings(engine='ace_step',
            mode='custom', customPrompt=brief.description, customLyrics=brief.lyrics, instrumental=brief.instrumental,
            duration=float(duration if duration is not None else brief.duration_seconds), durationAuto=False,
            bpm=brief.bpm, keyScale=brief.key, vocalLanguage=brief.language, selectedModel=config.ACE_STEP_DIT_MODEL,
            seed=0, randomSeed=False, batchSize=1, useCotCaption=False, audioFormat='wav'))
    prompt = _directions(brief)
    if selection.engine == 'yue2':
        return MusicQueueYueRequest(engine='yue2', request=YueSubmitRequest(title=brief.title, lyrics=brief.lyrics,
            seed=0, options=YueOptions(style=prompt), precision=selection.precision,
            ar_adapter=selection.ar_adapter, nar_adapter=selection.nar_adapter,
            settings=YueGenerationSettings(engine='yue2', title=brief.title, lyrics=brief.lyrics, style=prompt,
                precision=selection.precision, seed=0, randomSeed=False, batchSize=1,
                arAdapter=selection.ar_adapter, narAdapter=selection.nar_adapter)))
    if len(prompt) > 4000:
        raise ValueError('music_queue_prompt_too_long')
    request = LocalMusicRequest(engine=selection.engine, title=brief.title, prompt=prompt, lyrics=brief.lyrics,
        duration_seconds=duration if duration is not None else brief.duration_seconds,
        seed=0, variant=selection.variant, adapter=selection.adapter)
    if selection.engine == 'heartmula':
        return MusicQueueHeartRequest(engine='heartmula', request=request)
    return MusicQueueStableRequest(engine='stable_audio3', request=request)


async def preview(request: MusicQueuePreviewRequest, environment: ModuleEnvironment | None = None) -> MusicQueuePreview:
    """Keep incompatible/missing engines visible without changing the original brief."""
    env = environment or configured_environment()
    available = await catalog(env)
    by_engine = {engine.engine: engine for engine in available.engines}
    models: list[MusicQueueModelPlan] = []
    items: list[MusicQueueNormalizedItem] = []
    brief = request.brief
    for selection in request.selections:
        readiness = by_engine[selection.engine].model_copy(deep=True)
        if readiness.ready and not _weights_ready(env, selection):
            readiness.weights_ready = False
            readiness.ready = False
            readiness.error_code = 'music_queue_weights_missing'
        mode: Literal['target', 'maximum', 'model_led'] = ('model_led' if selection.engine == 'yue2' else
            'maximum' if selection.engine == 'heartmula' else 'target')
        cap = 300 if selection.engine == 'ace_step' else 240 if selection.engine == 'heartmula' else 120 if selection.variant == 'small-music' else 380
        duration = None if selection.engine == 'yue2' else min(brief.duration_seconds, cap)
        error = ('music_queue_yue_vocals_required' if selection.engine == 'yue2' and brief.instrumental else
                 'music_queue_yue_lyrics_required' if selection.engine == 'yue2' and not brief.lyrics.strip() else
                 'stable_audio3_instrumental_only' if selection.engine == 'stable_audio3' and not brief.instrumental else '')
        model = MusicQueueModelPlan(selection=selection, readiness=readiness, compatible=not error, error_code=error,
            effective_duration_seconds=duration, duration_mode=mode)
        if duration is not None and duration != brief.duration_seconds:
            model.adjustments.append(MusicQueueAdjustment(field='duration_seconds', code='music_queue_duration_clamped',
                requested=str(brief.duration_seconds), effective=str(duration)))
        if selection.engine in ('heartmula', 'stable_audio3'):
            model.warnings.append('music_queue_one_shot_reload')
        if selection.engine == 'heartmula':
            model.warnings.append('music_queue_heart_duration_maximum')
        if selection.engine == 'yue2':
            model.warnings.append('music_queue_yue_model_led_duration')
        if not error:
            try:
                model.adapters = _adapters(selection)
                payload = _payload(brief, selection, duration)
                items.extend(MusicQueueNormalizedItem(engine=selection.engine, variation_index=index,
                    payload=payload.model_copy(deep=True), adapters=model.adapters)
                    for index in range(1, selection.variations + 1))
            except MusicLoraError as exc:
                model.compatible = False
                model.error_code = exc.code
            except (OSError, ValidationError, ValueError) as exc:
                model.compatible = False
                model.error_code = 'music_queue_prompt_too_long' if str(exc) == 'music_queue_prompt_too_long' else 'music_queue_plan_invalid'
        models.append(model)
    canonical = {'brief': brief.model_dump(mode='json'),
        'selections': [selection.model_dump(mode='json') for selection in request.selections],
        'items': [item.model_dump(mode='json') for item in items]}
    fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    return MusicQueuePreview(brief=brief, selections=request.selections, models=models, items=items,
        total_items=sum(selection.variations for selection in request.selections),
        can_enqueue=all(model.compatible and model.readiness.ready for model in models), fingerprint=fingerprint)
