"""Deterministic brief adaptation; readiness checks never start a model."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from . import config, local_music_runtime, music_lora, music_conditioning, song_capabilities, song_from_beat
from .contracts import JsonObject
from .generation_contracts import AceGenerationSettings, YueGenerationSettings
from .local_music_contracts import LocalMusicError, LocalMusicRequest
from .module_catalog import DEFINITIONS, ModuleEnvironment, configured_environment, engine_python, supported
from .module_evidence import environment_fingerprint, environment_verified
from .module_runtime import RuntimeEvidence, RuntimeTarget, runtime_status
from .music_lora_contracts import MusicAdapter, MusicLoraError
from .queued_music_source_contracts import QueuedMusicSource
from .music_queue_contracts import (
    MusicQueueAceRequest, MusicQueueAdjustment, MusicQueueBrief, MusicQueueCatalog,
    MusicQueueEngine, MusicQueueEngineSelection, MusicQueueModelPlan,
    MusicQueueNormalizedItem, MusicQueuePayload, MusicQueuePreview, MusicQueuePreviewRequest,
    MusicQueueReadiness, MusicQueueStableRequest, MusicQueueYueRequest,
    MusicQueueError,
)
from .song_from_beat_contracts import AceCompleteConditioning, AceLegoConditioning
from .orchestrator.manager import manager
from .orchestrator.state import ModelStatus
from .yue_contracts import YueOptions, YueSubmitRequest

ENGINES: tuple[MusicQueueEngine, ...] = ('ace_step', 'yue2', 'stable_audio3')
_WEIGHT_NAMES = ('model.safetensors', 'pytorch_model.bin', 'diffusion_pytorch_model.safetensors',
                 'diffusion_pytorch_model.bin')
_object = TypeAdapter(JsonObject)


def selection_document(selection: MusicQueueEngineSelection) -> JsonObject:
    payload = _object.validate_json(selection.model_dump_json())
    if payload.get('conditioning') is None:
        payload.pop('conditioning', None)
    return payload


def request_hash(request: MusicQueuePreviewRequest) -> str:
    # Keep pre-conditioning submission replays byte-compatible with their old JSON.
    document = {'brief': request.brief.model_dump(mode='json'),
        'selections': [selection_document(selection) for selection in request.selections]}
    return hashlib.sha256(json.dumps(document, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


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
        if isinstance(selection.conditioning, (AceCompleteConditioning, AceLegoConditioning)):
            return song_capabilities.weights_present(environment)
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
        conditioning = selection.conditioning
        if conditioning is not None and not isinstance(conditioning, (AceCompleteConditioning, AceLegoConditioning)):
            raise ValueError('conditioning_task_unsupported')
        return MusicQueueAceRequest(engine='ace_step', request=AceGenerationSettings(engine='ace_step',
            mode='custom', customPrompt=brief.description, customLyrics=brief.lyrics, instrumental=brief.instrumental,
            duration=float(duration if duration is not None else min(brief.duration_seconds, 300)), durationAuto=False,
            bpm=brief.bpm, keyScale=brief.key, vocalLanguage=brief.language,
            selectedModel=song_capabilities.BASE_MODEL if conditioning is not None else config.ACE_STEP_DIT_MODEL,
            taskType='complete' if isinstance(conditioning, AceCompleteConditioning) else 'lego' if conditioning is not None else 'text2music',
            useRefAudio=conditioning is not None,
            trackClasses=list(conditioning.track_classes) if isinstance(conditioning, AceCompleteConditioning) else [],
            trackName=conditioning.track_name if isinstance(conditioning, AceLegoConditioning) else 'vocals',
            seed=0, randomSeed=False, batchSize=1, useCotCaption=False, audioFormat='wav'), conditioning=conditioning)
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
    if isinstance(selection.conditioning, (AceCompleteConditioning, AceLegoConditioning)):
        raise ValueError('conditioning_task_unsupported')
    request = LocalMusicRequest(engine=selection.engine, title=brief.title, prompt=prompt, lyrics=brief.lyrics,
        duration_seconds=duration if duration is not None else brief.duration_seconds,
        seed=0, variant=selection.variant, adapter=selection.adapter, conditioning=selection.conditioning)
    return MusicQueueStableRequest(engine='stable_audio3', request=request)


async def preview(request: MusicQueuePreviewRequest, environment: ModuleEnvironment | None = None, *,
                  retained_inputs: list[QueuedMusicSource] | None = None) -> MusicQueuePreview:
    """Keep incompatible/missing engines visible without changing the original brief."""
    env = environment or configured_environment()
    available = await catalog(env)
    by_engine = {engine.engine: engine for engine in available.engines}
    models: list[MusicQueueModelPlan] = []
    items: list[MusicQueueNormalizedItem] = []
    brief = request.brief
    for selection in request.selections:
        readiness = by_engine[selection.engine].model_copy(deep=True)
        ace = selection.conditioning if isinstance(selection.conditioning, (AceCompleteConditioning, AceLegoConditioning)) else None
        if ace is not None:
            capability = song_capabilities.capabilities(env).modes[0]
            readiness.installed = capability.installed
            readiness.weights_ready = capability.weights_ready
            transition_error = readiness.error_code if readiness.error_code not in (
                'music_queue_weights_missing', 'music_queue_engine_not_installed') else ''
            readiness.error_code = transition_error or (capability.reason or '')
            readiness.ready = not readiness.error_code
        if readiness.ready and not _weights_ready(env, selection):
            readiness.weights_ready = False
            readiness.ready = False
            readiness.error_code = 'music_queue_weights_missing'
        mode: Literal['target', 'model_led'] = 'model_led' if selection.engine == 'yue2' or ace is not None else 'target'
        cap = 300 if selection.engine == 'ace_step' else 120 if selection.variant == 'small-music' else 380
        duration = None if selection.engine == 'yue2' or ace is not None else min(brief.duration_seconds, cap)
        error = ('music_queue_yue_vocals_required' if selection.engine == 'yue2' and brief.instrumental else
                 'music_queue_yue_lyrics_required' if selection.engine == 'yue2' and not brief.lyrics.strip() else
                 'stable_audio3_instrumental_only' if selection.engine == 'stable_audio3' and not brief.instrumental else '')
        roles = ace.track_classes if isinstance(ace, AceCompleteConditioning) else [ace.track_name] if ace is not None else []
        if brief.instrumental and any(role in ('vocals', 'backing_vocals') for role in roles):
            error = 'music_queue_instrumental_vocal_role'
        elif any(role in ('vocals', 'backing_vocals') for role in roles) and brief.lyrics.strip().lower() in ('', '[inst]', '[instrumental]'):
            error = 'music_queue_ace_vocal_lyrics_required'
        model = MusicQueueModelPlan(selection=selection, readiness=readiness, compatible=not error, error_code=error,
            effective_duration_seconds=duration, duration_mode=mode)
        if duration is not None and duration != brief.duration_seconds:
            model.adjustments.append(MusicQueueAdjustment(field='duration_seconds', code='music_queue_duration_clamped',
                requested=str(brief.duration_seconds), effective=str(duration)))
        if selection.engine == 'stable_audio3':
            model.warnings.append('music_queue_one_shot_reload')
        if selection.engine == 'yue2':
            model.warnings.append('music_queue_yue_model_led_duration')
        if not error:
            try:
                model.adapters = _adapters(selection)
                payload = _payload(brief, selection, duration)
                if payload.engine == 'ace_step' and payload.conditioning is not None:
                    retained = next((value for value in retained_inputs or [] if value.snapshot.source == payload.conditioning.source
                        and value.snapshot.sha256 == payload.conditioning.source_sha256), None)
                    if retained_inputs is not None and retained is None:
                        raise MusicQueueError('conditioning_snapshot_invalid')
                    model.conditioning_preview = await song_from_beat.preview(payload.conditioning,
                        retained_input=retained, environment=env)
                    model.warnings.extend(['music_queue_ace_generated_full_mix', 'music_queue_ace_source_duration',
                        'music_queue_ace_runtime_unverified'])
                    if not model.conditioning_preview.can_start:
                        model.compatible = False
                        model.error_code = model.conditioning_preview.error_code or 'conditioning_runtime_unavailable'
                if payload.engine == 'stable_audio3' and payload.request.conditioning is not None:
                    if retained_inputs is None:
                        model.conditioning_preview = await music_conditioning.preview(payload.request)
                        forecast = model.conditioning_preview
                        required = forecast.required_free_bytes + forecast.source_bytes
                        enough = forecast.free_bytes is not None and forecast.free_bytes >= required
                        model.conditioning_preview = forecast.model_copy(update={'required_free_bytes': required,
                            'can_start': forecast.can_start and enough,
                            'error_code': forecast.error_code if forecast.error_code is not None or enough else 'insufficient_disk'})
                    else:
                        retained = next((value for value in retained_inputs if value.snapshot.source == payload.request.conditioning.source
                            and value.snapshot.sha256 == payload.request.conditioning.source_sha256), None)
                        if retained is None:
                            raise LocalMusicError('conditioning_snapshot_invalid')
                        model.conditioning_preview = await music_conditioning.preview(payload.request, retained_input=retained)
                    if not model.conditioning_preview.can_start:
                        model.compatible = False
                        model.error_code = model.conditioning_preview.error_code or 'conditioning_runtime_unavailable'
                if model.compatible:
                    items.extend(MusicQueueNormalizedItem(engine=selection.engine, variation_index=index,
                        payload=payload.model_copy(deep=True), adapters=model.adapters)
                        for index in range(1, selection.variations + 1))
            except LocalMusicError as exc:
                model.compatible = False
                model.error_code = exc.code
            except MusicQueueError as exc:
                model.compatible = False
                model.error_code = exc.code
            except MusicLoraError as exc:
                model.compatible = False
                model.error_code = exc.code
            except (OSError, ValidationError, ValueError) as exc:
                model.compatible = False
                model.error_code = 'music_queue_prompt_too_long' if str(exc) == 'music_queue_prompt_too_long' else 'music_queue_plan_invalid'
        models.append(model)
    if retained_inputs is None:
        # Every selected engine captures its own immutable input before enqueue.
        intake_bytes = sum(model.conditioning_preview.source_bytes for model in models
            if model.conditioning_preview is not None)
        for model in models:
            combined_forecast = model.conditioning_preview
            if combined_forecast is None or intake_bytes == combined_forecast.source_bytes:
                continue
            required = combined_forecast.required_free_bytes + intake_bytes - combined_forecast.source_bytes
            enough = combined_forecast.free_bytes is not None and combined_forecast.free_bytes >= required
            model.conditioning_preview = combined_forecast.model_copy(update={'required_free_bytes': required,
                'can_start': combined_forecast.can_start and enough,
                'error_code': combined_forecast.error_code if combined_forecast.error_code is not None or enough else 'insufficient_disk'})
            if not enough:
                model.compatible = False
                model.error_code = model.error_code or 'insufficient_disk'
        compatible = {model.selection.engine for model in models if model.compatible}
        items = [item for item in items if item.engine in compatible]
    canonical = {'brief': brief.model_dump(mode='json'),
        'selections': [selection_document(selection) for selection in request.selections],
        'items': [item.model_dump(mode='json') for item in items]}
    fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    return MusicQueuePreview(brief=brief, selections=request.selections, models=models, items=items,
        total_items=sum(selection.variations for selection in request.selections),
        can_enqueue=all(model.compatible and model.readiness.ready for model in models), fingerprint=fingerprint)
