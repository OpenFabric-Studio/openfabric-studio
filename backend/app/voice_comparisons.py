"""Persisted, cancellable listening trials on audio excluded from training."""
from __future__ import annotations

import asyncio
import itertools
import logging
import math
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from fastapi import HTTPException, UploadFile
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from .atomic_files import JsonObject, document_lock, read_object, write_object
from .contracts import Contract
from .config import SEED_VC_DIR
from .orchestrator.process import tail_log
from .job_lifecycle import await_cleanup, cancel_and_wait, request_cancel
from .stems import gpu_lock
from .gpu_lease import gpu_lease
from .voice_contracts import (
    VoiceComparisonRequest, VoiceComparisonResponse, VoiceComparisonsResponse,
    VoiceComparisonTrial, VoiceTrialMetrics, VoiceTrialRating, VoiceTrialSource,
    VoiceTrialSourcesResponse,
)
from .voice_separation import separate_vocal
from .voice_artifacts import ModelArtifact

logger = logging.getLogger(__name__)
_ID = re.compile(r"^[0-9a-f]{32}$")
_MAX_UPLOAD = 256 * 1024 * 1024
_ACTIVE = {"queued", "running"}
_Model = TypeVar("_Model", bound=BaseModel)


class StoredSource(Contract):
    source: VoiceTrialSource
    extension: str = Field(pattern=r"^(wav|mp3|flac|ogg|opus|m4a)$")


class SourceRegistry(Contract):
    sources: list[StoredSource] = Field(default_factory=list)


@dataclass
class ComparisonJob:
    response: VoiceComparisonResponse
    path: Path
    task: asyncio.Task[None] | None = None
    models: dict[str, ModelArtifact] = field(default_factory=dict)
    references: dict[str, Path] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelSnapshot:
    checkpoint: Path | None
    config: Path | None


_jobs: dict[tuple[str, str], ComparisonJob] = {}


def _voice_path(voice_id: str) -> Path:
    from .voice_build import voice_dir
    return voice_dir(voice_id)


def _require_id(value: str) -> None:
    if not _ID.fullmatch(value):
        raise HTTPException(status_code=404, detail="comparison_not_found")


def _directory(root: Path, comparison_id: str) -> Path:
    _require_id(comparison_id)
    result = root / "trials" / "comparisons" / comparison_id
    if not result.resolve().is_relative_to(root.resolve()):
        raise HTTPException(status_code=404, detail="comparison_not_found")
    return result


def _read(path: Path, model: type[_Model]) -> _Model:
    try:
        return model.model_validate(read_object(path))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="comparison_not_found") from None
    except (OSError, ValueError, ValidationError):
        raise HTTPException(status_code=409, detail="invalid_trial_metadata") from None


def _write(path: Path, model: BaseModel) -> None:
    write_object(path, TypeAdapter(JsonObject).validate_python(model.model_dump(mode="json")))


def persist_response(root: Path, response: VoiceComparisonResponse) -> None:
    directory = _directory(root, response.id)
    directory.mkdir(parents=True, exist_ok=True)
    _write(directory / "result.json", response)


def get_comparison(voice_id: str, comparison_id: str) -> VoiceComparisonResponse:
    _require_id(comparison_id)
    root = _voice_path(voice_id)
    key = (voice_id, comparison_id)
    if key in _jobs:
        return _jobs[key].response
    response = _read(_directory(root, comparison_id) / "result.json", VoiceComparisonResponse)
    if response.voice_id != voice_id or response.id != comparison_id:
        raise HTTPException(status_code=409, detail="invalid_trial_metadata")
    if response.status in _ACTIVE:
        response.status = "failed"
        response.error_code = "interrupted"
        for trial in response.trials:
            if trial.status in _ACTIVE:
                trial.status = "failed"
                trial.error_code = "interrupted"
        persist_response(root, response)
    return response


def list_comparisons(voice_id: str) -> VoiceComparisonsResponse:
    root = _voice_path(voice_id)
    parent = root / "trials" / "comparisons"
    ids = sorted((path.name for path in parent.iterdir() if path.is_dir() and _ID.fullmatch(path.name)), reverse=True) if parent.is_dir() else []
    return VoiceComparisonsResponse(comparisons=[get_comparison(voice_id, value) for value in ids])


def _source_registry(root: Path) -> SourceRegistry:
    path = root / "trials" / "sources.json"
    if not path.resolve().is_relative_to(root.resolve()):
        raise HTTPException(status_code=404, detail="trial_source_not_found")
    return _read(path, SourceRegistry) if path.is_file() else SourceRegistry()


def list_sources(voice_id: str) -> VoiceTrialSourcesResponse:
    root = _voice_path(voice_id)
    return VoiceTrialSourcesResponse(sources=[item.source for item in _source_registry(root).sources])


def source_path(root: Path, source_id: str) -> Path:
    _require_id(source_id)
    item = next((item for item in _source_registry(root).sources if item.source.id == source_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="trial_source_not_found")
    path = root / "trials" / "sources" / f"{source_id}.{item.extension}"
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise HTTPException(status_code=404, detail="trial_source_not_found")
    return path


async def add_source(voice_id: str, upload: UploadFile) -> VoiceTrialSource:
    from .voice_build import _probe_duration, ALLOWED_AUDIO_EXT
    root = _voice_path(voice_id)
    filename = Path((upload.filename or "").replace("\\", "/")).name
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in ALLOWED_AUDIO_EXT or len(filename) > 255:
        raise HTTPException(status_code=422, detail="unsupported_audio")
    source_id = uuid.uuid4().hex
    folder = root / "trials" / "sources"
    if not folder.resolve().is_relative_to(root.resolve()):
        raise HTTPException(status_code=404, detail="trial_source_not_found")
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{source_id}.{extension}"
    partial = dest.with_suffix(f".{extension}.partial")
    try:
        total = 0
        with partial.open("xb") as handle:
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > _MAX_UPLOAD:
                    raise HTTPException(status_code=413, detail="audio_too_large")
                handle.write(chunk)
        duration = await _probe_duration(partial)
        if not math.isfinite(duration) or duration <= 0 or duration > 7200:
            raise HTTPException(status_code=422, detail="invalid_audio")
        partial.replace(dest)
        source = VoiceTrialSource(id=source_id, filename=filename, duration_sec=duration)
        with document_lock(root / "trials" / "sources.json"):
            registry = _source_registry(root)
            registry.sources.append(StoredSource(source=source, extension=extension))
            _write(root / "trials" / "sources.json", registry)
        return source
    except BaseException:
        partial.unlink(missing_ok=True)
        dest.unlink(missing_ok=True)
        raise


def start_comparison(voice_id: str, request: VoiceComparisonRequest) -> VoiceComparisonResponse:
    from .resource_admission import require_setup_idle
    require_setup_idle()
    from .voice_build import resolve_model_artifact, resolve_reference_artifact
    root = _voice_path(voice_id)
    if any(key[0] == voice_id for key in _jobs):
        raise HTTPException(status_code=409, detail="comparison_busy")
    source_path(root, request.source_id)
    source = next(item.source for item in _source_registry(root).sources if item.source.id == request.source_id)
    if request.start_sec + request.duration_sec > source.duration_sec + 0.05:
        raise HTTPException(status_code=422, detail="trial_out_of_range")
    references = request.reference_ids or ["published"]
    models = {model_id: resolve_model_artifact(root, model_id) for model_id in request.model_ids}
    reference_paths = {reference_id: resolve_reference_artifact(root, reference_id) for reference_id in references}
    combinations = itertools.product(request.model_ids, references, request.diffusion_steps)
    trials = [VoiceComparisonTrial(id=str(index), model_id=model_id, reference_id=reference_id, diffusion_steps=steps) for index, (model_id, reference_id, steps) in enumerate(combinations)]
    response = VoiceComparisonResponse(id=uuid.uuid4().hex, voice_id=voice_id, status="queued", source_filename=source.filename, request=request, trials=trials)
    job = ComparisonJob(response=response, path=_directory(root, response.id), models=models, references=reference_paths)
    persist_response(root, response)
    _jobs[(voice_id, response.id)] = job
    job.task = asyncio.create_task(_run(job))
    return response


def _snapshot(job: ComparisonJob) -> tuple[dict[str, ModelSnapshot], dict[str, Path]]:
    folder = job.path / "work"
    folder.mkdir(parents=True, exist_ok=True)
    models: dict[str, ModelSnapshot] = {}
    references: dict[str, Path] = {}
    # Resolve identities at enqueue time; immutable files survive later reviews
    # and active-model changes while the task is waiting to start.
    for index, (model_id, artifact) in enumerate(job.models.items()):
        checkpoint = folder / f"model_{index}.pth" if artifact.checkpoint is not None else None
        config = folder / f"config_{index}.yml" if artifact.config is not None else None
        if checkpoint is not None and artifact.checkpoint is not None:
            shutil.copyfile(artifact.checkpoint, checkpoint)
        if config is not None and artifact.config is not None:
            shutil.copyfile(artifact.config, config)
        models[model_id] = ModelSnapshot(checkpoint=checkpoint, config=config)
    for index, (reference_id, reference) in enumerate(job.references.items()):
        target = folder / f"reference_{index}.wav"
        shutil.copyfile(reference, target)
        references[reference_id] = target
    return models, references


async def _run_trial(job: ComparisonJob, trial: VoiceComparisonTrial, source: Path, reference: Path, model: ModelSnapshot) -> None:
    from . import voice_build as voice
    slot = voice.ProcSlot()
    output = job.path / "work" / f"trial_{trial.id}"
    output.mkdir()
    command = [str(voice._engine_python()), "inference.py", "--source", str(source), "--target", str(reference), "--output", str(output), "--diffusion-steps", str(trial.diffusion_steps), "--inference-cfg-rate", "0.7", "--f0-condition", "True", "--auto-f0-adjust", "False", "--fp16", "False", "--seed", str(job.response.request.seed)]
    if model.checkpoint is not None:
        command.extend(["--checkpoint", str(model.checkpoint)])
    if model.config is not None:
        command.extend(["--config", str(model.config)])
    async with gpu_lease(gpu_lock, 'voice_conversion', voice._voice_name(job.response.voice_id)):
        code = await voice._spawn(command, cwd=SEED_VC_DIR, log_name=f"trial_{job.response.id}_{trial.id}", slot=slot)
    wavs = list(output.glob("vc_*.wav"))
    if code != 0 or len(wavs) != 1:
        raise RuntimeError("trial_failed")
    audio = job.path / "audio" / f"{trial.id}.wav"
    audio.parent.mkdir(exist_ok=True)
    shutil.copyfile(wavs[0], audio)
    measure = Path(__file__).with_name("voice_measure.py")
    log_name = f"trial_measure_{job.response.id}_{trial.id}"
    expected = await voice._probe_duration(source)
    code = await voice._spawn([str(voice._engine_python()), str(measure), str(audio), str(expected)], cwd=measure.parent, log_name=log_name, slot=slot)
    if code != 0:
        raise RuntimeError("trial_measure_failed")
    lines = tail_log(log_name, lines=20).splitlines()
    if not lines:
        raise RuntimeError("trial_measure_failed")
    trial.metrics = VoiceTrialMetrics.model_validate_json(lines[-1])
    trial.audio_url = f"/api/voices/{job.response.voice_id}/comparisons/{job.response.id}/trials/{trial.id}/audio"
    trial.status = "done"


async def _run(job: ComparisonJob) -> None:
    from . import voice_build as voice
    from .seed_vc_compat import EngineCompatibilityError, ensure_compatibility
    root = job.path.parents[2]
    slot = voice.ProcSlot()
    try:
        ensure_compatibility(SEED_VC_DIR)
        voice.ensure_whisper_float32_off_cuda()
        models, references = _snapshot(job)
        job.response.status = "running"
        persist_response(root, job.response)
        request = job.response.request
        cropped = job.path / "work" / "cropped.wav"
        await voice._ffmpeg(["-y", "-ss", str(request.start_sec), "-i", str(source_path(root, request.source_id)), "-t", str(request.duration_sec), "-ar", "44100", "-ac", "2", "-c:a", "pcm_f32le", str(cropped)], slot, lambda: False)
        source = await separate_vocal(cropped, job.path / "work" / "stems", quality=request.separation_quality, log_name=f"trial_sep_{job.response.id}", on_proc=slot.track) if request.input_kind == "song" else cropped
        mono_source = job.path / "work" / "source_mono.wav"
        await voice.phase_safe_mono(source, mono_source, slot)
        for reference_id, reference in references.items():
            mono_reference = reference.with_stem(f"{reference.stem}_mono")
            await voice.phase_safe_mono(reference, mono_reference, slot)
            references[reference_id] = mono_reference
        for trial in job.response.trials:
            trial.status = "running"
            persist_response(root, job.response)
            try:
                await _run_trial(job, trial, mono_source, references[trial.reference_id], models[trial.model_id])
            except Exception:
                logger.exception("Voice listening trial failed")
                trial.status = "failed"
                trial.error_code = "trial_failed"
            persist_response(root, job.response)
        job.response.status = "done" if all(trial.status == "done" for trial in job.response.trials) else "failed"
    except asyncio.CancelledError:
        job.response.status = "cancelled"
        for trial in job.response.trials:
            if trial.status in _ACTIVE:
                trial.status = "cancelled"
        raise
    except EngineCompatibilityError:
        logger.exception("Voice engine is incompatible with listening trials")
        job.response.status = "failed"
        job.response.error_code = "engine_incompatible"
    except Exception:
        logger.exception("Voice comparison failed")
        job.response.status = "failed"
        job.response.error_code = "comparison_failed"
        for trial in job.response.trials:
            if trial.status in _ACTIVE:
                trial.status = "failed"
                trial.error_code = job.response.error_code
    finally:
        if job.response.status == "failed":
            for trial in job.response.trials:
                if trial.status in _ACTIVE:
                    trial.status = "failed"
                    trial.error_code = job.response.error_code or "trial_failed"
        _finish(job)


def _finish(job: ComparisonJob) -> None:
    """Release owned scratch data and registry even when final persistence fails."""
    try:
        persist_response(job.path.parents[2], job.response)
    except Exception:
        logger.exception("Could not persist final voice comparison status")
    finally:
        shutil.rmtree(job.path / "work", ignore_errors=True)
        key = (job.response.voice_id, job.response.id)
        if _jobs.get(key) is job:
            _jobs.pop(key)


async def cancel_comparison(voice_id: str, comparison_id: str) -> VoiceComparisonResponse:
    _require_id(comparison_id)
    job = _jobs.get((voice_id, comparison_id))
    if job is not None:
        await _cancel(job)
    return get_comparison(voice_id, comparison_id)


def rate_trial(voice_id: str, comparison_id: str, trial_id: str, rating: VoiceTrialRating) -> VoiceComparisonResponse:
    root = _voice_path(voice_id)
    with document_lock(_directory(root, comparison_id) / "result.json"):
        response = get_comparison(voice_id, comparison_id)
        trial = next((trial for trial in response.trials if trial.id == trial_id), None)
        if trial is None:
            raise HTTPException(status_code=404, detail="trial_not_found")
        if trial.status != "done":
            raise HTTPException(status_code=409, detail="trial_not_complete")
        trial.rating = rating
        persist_response(root, response)
        return response


def audio_path(voice_id: str, comparison_id: str, trial_id: str) -> Path:
    response = get_comparison(voice_id, comparison_id)
    if not any(trial.id == trial_id and trial.status == "done" for trial in response.trials) or not re.fullmatch(r"\d{1,2}", trial_id):
        raise HTTPException(status_code=404, detail="trial_not_found")
    root = _voice_path(voice_id)
    path = _directory(root, comparison_id) / "audio" / f"{trial_id}.wav"
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise HTTPException(status_code=404, detail="trial_not_found")
    return path


def work_busy() -> bool:
    return bool(_jobs)


async def _cancel(job: ComparisonJob) -> None:
    try:
        await cancel_and_wait(job.task)
    finally:
        # A cancelled task may never have entered its coroutine/finally block.
        if job.response.status in _ACTIVE:
            job.response.status = "cancelled"
            for trial in job.response.trials:
                if trial.status in _ACTIVE:
                    trial.status = "cancelled"
        _finish(job)


async def release_voice(voice_id: str) -> None:
    jobs = [job for key, job in list(_jobs.items()) if key[0] == voice_id]
    for job in jobs:
        request_cancel(job.task)
    await await_cleanup(asyncio.gather(*(_cancel(job) for job in jobs)))


async def shutdown() -> None:
    jobs = list(_jobs.values())
    for job in jobs:
        request_cancel(job.task)
    await await_cleanup(asyncio.gather(*(_cancel(job) for job in jobs)))
