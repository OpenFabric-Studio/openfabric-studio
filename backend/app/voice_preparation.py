"""Persisted, reviewable voice samples with original source/time provenance."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TYPE_CHECKING

from fastapi import HTTPException
from pydantic import Field, TypeAdapter

from .atomic_files import JsonObject, document_lock, read_object, write_object
from .contracts import Contract
from .job_lifecycle import await_cleanup, cancel_and_wait, request_cancel
from .voice_artifacts import contained_file
from .voice_contracts import (AnalyzeVoiceCoverageRequest, PrepareVoiceRequest, SelectVoiceSamplesRequest, VoicePreparationResponse,
    VoiceReferenceCandidate, VoiceSegment, VoiceSourceReport, VoiceSourceSelection)
from .voice_contracts import VoiceProgressPhase as ProgressPhase, VoiceProgressUnit as ProgressUnit
from .voice_progress import VoiceProgressTracker, finish_progress

logger = logging.getLogger(__name__)
if TYPE_CHECKING:
    from .voice_build import VoiceProcessSlot
MAX_SELECTED_SEGMENTS = 1000


class SampleFiles(Contract):
    original: str
    cleaned: str | None = None


class SourceStamp(Contract):
    filename: str
    size: int
    modified_ns: int


class PreparationDocument(Contract):
    response: VoicePreparationResponse
    generation: str = ''
    fingerprints: list[SourceStamp] = Field(default_factory=list)
    samples: dict[str, SampleFiles] = Field(default_factory=dict)
    references: dict[str, SampleFiles] = Field(default_factory=dict)


@dataclass
class PreparationJob:
    task: asyncio.Task[None] | None = None
    cancelled: bool = False
    proc: asyncio.subprocess.Process | None = None
    timing: VoiceProgressTracker | None = None
    last_progress_save: float = 0.0

    def track(self, proc: asyncio.subprocess.Process | None) -> None:
        self.proc = proc


_jobs: dict[Path, PreparationJob] = {}


def save(path: Path, document: PreparationDocument) -> None:
    if document.response.status == 'done':
        finish_progress(document.response.progress, 'done')
    elif document.response.status == 'failed':
        finish_progress(document.response.progress, 'failed')
    elif document.response.status == 'cancelled':
        finish_progress(document.response.progress, 'cancelled')
    write_object(path / 'preparation.json', TypeAdapter(JsonObject).validate_python(document.model_dump(mode='json')))


def _phase(path: Path, document: PreparationDocument, job: PreparationJob, phase: ProgressPhase,
    total: int, unit: ProgressUnit, *, current_file: str = '') -> None:
    if job.timing is not None:
        job.timing.phase(phase, total=total, unit=unit, current_file=current_file)
    save(path, document)


def _advance(path: Path, document: PreparationDocument, job: PreparationJob, completed: int) -> None:
    if job.timing is None:
        return
    job.timing.advance(completed)
    stamp = time.monotonic()
    # Persist observable progress at most once a second while slicing many files.
    if completed == job.timing.progress.phase_total or stamp - job.last_progress_save >= 1:
        save(path, document)
        job.last_progress_save = stamp


def load(path: Path) -> PreparationDocument:
    source = path / 'preparation.json'
    try:
        return PreparationDocument.model_validate(read_object(source)) if source.is_file() else PreparationDocument(response=VoicePreparationResponse())
    except (ValueError, OSError):
        raise HTTPException(status_code=409, detail='invalid_voice_metadata') from None


def get(path: Path) -> VoicePreparationResponse:
    with document_lock(path / 'preparation.json'):
        document = load(path)
        if document.response.status in {'queued', 'running'} and path.resolve() not in _jobs:
            if document.response.operation == 'coverage':
                _coverage_advisory(document.response, 'coverage_interrupted')
            else:
                document.response.status, document.response.error_code = 'failed', 'interrupted'
                progress = document.response.progress
                finish_progress(progress, 'failed', now=progress.observed_at if progress else None)
            save(path, document)
        if document.response.status == 'done':
            try:
                changed = fingerprints(path) != document.fingerprints
            except (HTTPException, OSError, ValueError):
                changed = True
            if changed:
                document.response.status, document.response.error_code = 'failed', 'source_changed'
                save(path, document)
        return document.response


def _recording_names(path: Path) -> list[str]:
    raw = read_object(path / 'voice.json').get('recordings', [])
    if not isinstance(raw, list):
        raise HTTPException(status_code=409, detail='invalid_voice_metadata')
    names: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            raise HTTPException(status_code=409, detail='invalid_voice_metadata')
        name = item.get('filename')
        if not isinstance(name, str):
            raise HTTPException(status_code=409, detail='invalid_voice_metadata')
        if Path(name).name != name or '\\' in name:
            raise HTTPException(status_code=409, detail='invalid_source')
        names.append(name)
    return names


def fingerprints(path: Path) -> list[SourceStamp]:
    result: list[SourceStamp] = []
    for name in _recording_names(path):
        source = contained_file(path, f'recordings/{name}')
        stat = source.stat()
        result.append(SourceStamp(filename=name, size=stat.st_size, modified_ns=stat.st_mtime_ns))
    return result


def start(path: Path, options: PrepareVoiceRequest) -> VoicePreparationResponse:
    from .resource_admission import require_setup_idle
    require_setup_idle()
    if path.resolve() in _jobs:
        raise HTTPException(status_code=409, detail='preparation_active')
    stamps = fingerprints(path)
    names = {item.filename for item in stamps}
    sources = options.sources or [VoiceSourceSelection(filename=item.filename) for item in stamps]
    if len({item.filename for item in sources}) != len(sources) or any(item.filename not in names for item in sources):
        raise HTTPException(status_code=400, detail='invalid_source')
    if not any(item.enabled for item in sources):
        raise HTTPException(status_code=400, detail='insufficient_usable_audio')
    options = options.model_copy(update={'sources': sources})
    revision = uuid.uuid4().hex
    timing = VoiceProgressTracker.create('preparation', revision, files_total=sum(item.enabled for item in sources))
    document = PreparationDocument(response=VoicePreparationResponse(revision=revision, status='queued', options=options, progress=timing.progress,
        warnings=['identity_unverified', 'periodicity_is_not_voice_detection']), generation=revision, fingerprints=stamps)
    job = PreparationJob(timing=timing)
    _jobs[path.resolve()] = job
    try:
        save(path, document)
        job.task = asyncio.create_task(_run(path, document, job), name=f'voice-preparation:{path.name}')
    except BaseException:
        _jobs.pop(path.resolve(), None)
        raise
    return document.response


def start_coverage(path: Path, request: AnalyzeVoiceCoverageRequest) -> VoicePreparationResponse:
    """Analyze an existing saved selection without repeating separation."""
    from .resource_admission import require_setup_idle
    require_setup_idle()
    with document_lock(path / 'preparation.json'):
        if path.resolve() in _jobs:
            raise HTTPException(status_code=409, detail='preparation_active')
        response = get(path)
        if response.revision != request.revision:
            raise HTTPException(status_code=409, detail='stale_preparation')
        if response.error_code == 'source_changed':
            raise HTTPException(status_code=409, detail='source_changed')
        if not response.selected_segment_ids or response.status not in {'done', 'cancelled', 'failed'}:
            raise HTTPException(status_code=409, detail='insufficient_usable_audio')
        document = load(path)
        if fingerprints(path) != document.fingerprints:
            raise HTTPException(status_code=409, detail='source_changed')
        document.response.status, document.response.error_code, document.response.operation = 'queued', '', 'coverage'
        timing = VoiceProgressTracker.create('coverage', document.response.revision)
        document.response.progress = timing.progress
        document.response.warnings = [warning for warning in document.response.warnings if warning not in {'coverage_cancelled', 'coverage_interrupted', 'coverage_failed'}]
        job = PreparationJob(timing=timing)
        _jobs[path.resolve()] = job
        try:
            save(path, document)
            job.task = asyncio.create_task(_run_coverage(path, document, job), name=f'voice-coverage:{path.name}')
        except BaseException:
            _jobs.pop(path.resolve(), None)
            raise
        return document.response


async def cancel(path: Path) -> VoicePreparationResponse:
    key = path.resolve()
    job = _jobs.get(key)
    if job:
        job.cancelled = True
        try:
            await cancel_and_wait(job.task)
        finally:
            from .voice_build import kill_proc
            try:
                await await_cleanup(kill_proc(job.proc))
            finally:
                if _jobs.get(key) is job:
                    _jobs.pop(key, None)
                with document_lock(path / 'preparation.json'):
                    document = load(path)
                    progress = document.response.progress
                    same_job = job.timing is None or (progress is not None and progress.job_id == job.timing.progress.job_id)
                    # Workers persist their terminal result before draining owned
                    # subprocesses. Cancellation during that drain must preserve it.
                    if same_job and document.response.status in {'queued', 'running'}:
                        if document.response.operation == 'coverage':
                            _coverage_advisory(document.response, 'coverage_cancelled')
                        else:
                            document.response.status, document.response.error_code = 'cancelled', 'cancelled'
                        save(path, document)
    return get(path)


def request_cancellation(path: Path | None = None) -> None:
    """Prevent queued preparation from starting before the caller drains it."""
    if path is None:
        jobs = list(_jobs.values())
    else:
        job = _jobs.get(path.resolve())
        jobs = [job] if job is not None else []
    for job in jobs:
        job.cancelled = True
        request_cancel(job.task)


async def shutdown() -> None:
    paths = list(_jobs)
    request_cancellation()
    outcomes = await await_cleanup(asyncio.gather(*(cancel(path) for path in paths), return_exceptions=True))
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            logger.error('Voice preparation cleanup failed', exc_info=(type(outcome), outcome, outcome.__traceback__))


def busy() -> bool:
    return bool(_jobs)


def select(path: Path, request: SelectVoiceSamplesRequest) -> VoicePreparationResponse:
    with document_lock(path / 'preparation.json'):
        document = load(path)
        response = document.response
        if response.revision != request.revision:
            raise HTTPException(status_code=409, detail='stale_preparation')
        if response.status in {'queued', 'running'}:
            raise HTTPException(status_code=409, detail='preparation_active')
        accepted = {item.id: item for item in response.segments if item.accepted and 1 <= item.duration_sec <= 30}
        identifiers = set(request.segment_ids)
        cleaned = set(request.cleaned_segment_ids)
        if len(identifiers) != len(request.segment_ids) or not identifiers.issubset(accepted) or not cleaned.issubset(identifiers):
            raise HTTPException(status_code=400, detail='invalid_selection')
        if any(not accepted[item].has_cleaned for item in cleaned):
            raise HTTPException(status_code=400, detail='clean_unavailable')
        seconds = math.fsum(accepted[item].duration_sec for item in identifiers)
        budget = request.max_selected_seconds if request.max_selected_seconds is not None else response.options.max_selected_seconds
        if seconds > budget + 1e-6:
            raise HTTPException(status_code=400, detail='too_much_audio')
        reference = next((item for item in response.references if item.id == request.reference_id), None)
        if reference is None or reference.segment_id not in identifiers:
            raise HTTPException(status_code=400, detail='invalid_reference')
        response.selected_segment_ids = request.segment_ids
        response.cleaned_segment_ids = request.cleaned_segment_ids
        response.reference_id = request.reference_id
        response.accepted_seconds = seconds
        response.options.max_selected_seconds = budget
        _refresh_coverage(response)
        response.revision = uuid.uuid4().hex
        response.status, response.error_code = 'done', ''
        save(path, document)
        return response


def validated(path: Path, revision: str | None) -> PreparationDocument:
    with document_lock(path / 'preparation.json'):
        get(path)
        document = load(path)
        response = document.response
        if not revision or not response.revision:
            raise HTTPException(status_code=409, detail='preparation_required')
        if revision != response.revision:
            raise HTTPException(status_code=409, detail='stale_preparation')
        if response.error_code == 'source_changed':
            raise HTTPException(status_code=409, detail='source_changed')
        if response.status != 'done' or not response.selected_segment_ids or not response.reference_id:
            raise HTTPException(status_code=409, detail='insufficient_usable_audio')
        if not response.options.singer_confirmed:
            raise HTTPException(status_code=409, detail='singer_unconfirmed')
        if fingerprints(path) != document.fingerprints:
            raise HTTPException(status_code=409, detail='source_changed')
        return document


def sample_path(path: Path, segment_id: str, variant: Literal['original', 'cleaned']) -> Path:
    files = load(path).samples.get(segment_id)
    raw = files.original if files and variant == 'original' else files.cleaned if files else None
    if not raw:
        raise HTTPException(status_code=404, detail='sample_missing')
    return contained_file(path, raw)


def reference_path(path: Path, reference_id: str) -> Path:
    document = load(path)
    candidate = next((item for item in document.response.references if item.id == reference_id), None)
    files = document.references.get(reference_id)
    if files is None or candidate is None:
        raise HTTPException(status_code=404, detail='invalid_reference')
    cleaned = candidate.segment_id in document.response.cleaned_segment_ids
    return contained_file(path, files.cleaned if cleaned and files.cleaned else files.original)


def selection_fingerprint(document: PreparationDocument) -> str:
    response = document.response
    payload = {'generation': document.generation, 'segments': response.selected_segment_ids,
        'cleaned': response.cleaned_segment_ids, 'reference': response.reference_id, 'options': response.options.model_dump(mode='json')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _refresh_coverage(response: VoicePreparationResponse) -> None:
    from .voice_coverage import summarize_coverage
    selected = set(response.selected_segment_ids)
    cleaned = set(response.cleaned_segment_ids)
    segments = [segment for segment in response.segments if segment.id in selected]
    measurements = [segment.cleaned_coverage if segment.id in cleaned else segment.original_coverage for segment in segments]
    available = [measurement for measurement in measurements if measurement is not None]
    response.coverage = summarize_coverage(available, unavailable_segments=len(selected) - len(available)) if selected else None


def _coverage_advisory(response: VoicePreparationResponse, warning: str) -> None:
    """Optional CPU analysis never withdraws an otherwise completed review."""
    response.status, response.error_code = 'done', ''
    progress = response.progress
    finish_progress(progress, 'cancelled' if warning == 'coverage_cancelled' else 'failed',
        now=progress.observed_at if progress and warning == 'coverage_interrupted' else None)
    if warning not in response.warnings:
        response.warnings.append(warning)
    _refresh_coverage(response)


async def _measure_selected(path: Path, document: PreparationDocument, job: PreparationJob) -> None:
    from .voice_build import _engine_python, _spawn
    from .voice_coverage import CoverageBatchInput, CoverageBatchReport, CoverageInput
    from .orchestrator.process import tail_log
    response = document.response
    selected, cleaned = set(response.selected_segment_ids), set(response.cleaned_segment_ids)
    inputs: list[CoverageInput] = []
    targets: dict[str, tuple[VoiceSegment, Literal['original', 'cleaned']]] = {}
    for segment in response.segments:
        if segment.id not in selected:
            continue
        variant: Literal['original', 'cleaned'] = 'cleaned' if segment.id in cleaned else 'original'
        measurement = segment.cleaned_coverage if variant == 'cleaned' else segment.original_coverage
        if measurement is not None:
            continue
        key = f'{segment.id}_{variant}'
        try:
            source = sample_path(path, segment.id, variant)
        except HTTPException:
            continue
        inputs.append(CoverageInput(id=key, path=str(source)))
        targets[key] = segment, variant
    _phase(path, document, job, 'coverage', len(inputs), 'samples')
    try:
        if job.cancelled:
            raise asyncio.CancelledError()
        if inputs:
            # One interpreter and one pYIN warm-up for all independent passages.
            with tempfile.TemporaryDirectory(prefix='openfabric-coverage-') as temporary:
                manifest = Path(temporary) / 'inputs.json'
                manifest.write_text(CoverageBatchInput(inputs=inputs).model_dump_json(), encoding='utf-8')
                log_name = f'voice_coverage_{path.name}_{uuid.uuid4().hex}'
                code = await _spawn([str(_engine_python()), '-m', 'app.voice_coverage', '--batch', str(manifest)],
                    cwd=Path(__file__).resolve().parent.parent, log_name=log_name, slot=job)
                report = next((CoverageBatchReport.model_validate_json(line) for line in reversed(tail_log(log_name, lines=10).splitlines())
                    if line.strip().startswith('{')), None) if code == 0 else None
                if report is not None:
                    for key, measurement in report.measurements.items():
                        target = targets.get(key)
                        if target is None:
                            raise ValueError('invalid_coverage_output')
                        segment, variant = target
                        if measurement.duration_sec > segment.duration_sec + .1:
                            raise ValueError('invalid_coverage_output')
                        if variant == 'cleaned':
                            segment.cleaned_coverage = measurement
                        else:
                            segment.original_coverage = measurement
                    _advance(path, document, job, len(report.measurements))
    except Exception:
        logger.warning('Voice coverage measurement unavailable for %s', path.name, exc_info=True)
    _refresh_coverage(response)
    response.warnings = [warning for warning in response.warnings if warning != 'coverage_unavailable']
    if response.coverage and response.coverage.unavailable_segments:
        response.warnings.append('coverage_unavailable')


async def _run_coverage(path: Path, document: PreparationDocument, job: PreparationJob) -> None:
    response = document.response
    try:
        if job.cancelled:
            raise asyncio.CancelledError()
        response.status = 'running'
        if job.timing is not None:
            job.timing.start()
        save(path, document)
        await _measure_selected(path, document, job)
        if fingerprints(path) != document.fingerprints:
            response.status, response.error_code = 'failed', 'source_changed'
        else:
            response.status, response.error_code = 'done', ''
        save(path, document)
    except asyncio.CancelledError:
        _coverage_advisory(response, 'coverage_cancelled')
        save(path, document)
        raise
    except Exception:
        logger.exception('Voice coverage failed for %s', path.name)
        _coverage_advisory(response, 'coverage_failed')
        save(path, document)
    finally:
        from .voice_build import kill_proc
        try:
            await await_cleanup(kill_proc(job.proc))
        finally:
            job.proc = None
            _jobs.pop(path.resolve(), None)


async def assemble(path: Path, document: PreparationDocument, destination: Path, slot: VoiceProcessSlot, cancelled: Callable[[], bool],
    on_sample: Callable[[int], None] | None = None, on_phase: Callable[[Literal['references', 'merging']], None] | None = None) -> tuple[Path, Path]:
    from .voice_build import merge_vocals, phase_safe_mono
    response = document.response
    dataset = destination / 'dataset'
    dataset.mkdir(parents=True)
    paths: list[Path] = []
    for index, identifier in enumerate(response.selected_segment_ids):
        files = document.samples[identifier]
        source = contained_file(path, files.cleaned if identifier in response.cleaned_segment_ids and files.cleaned else files.original)
        target = dataset / f'clip_{index:04d}.wav'
        if cancelled():
            raise asyncio.CancelledError()
        await phase_safe_mono(source, target, slot)
        paths.append(target)
        if on_sample is not None:
            on_sample(index + 1)
    reference = destination / 'reference.wav'
    if on_phase is not None:
        on_phase('references')
    if not response.reference_id:
        raise HTTPException(status_code=409, detail='invalid_reference')
    candidate = next(item for item in response.references if item.id == response.reference_id)
    files = document.references[response.reference_id]
    source_reference = files.cleaned if candidate.segment_id in response.cleaned_segment_ids and files.cleaned else files.original
    await phase_safe_mono(contained_file(path, source_reference), reference, slot)
    preview = destination / 'voice.wav'
    if on_phase is not None:
        on_phase('merging')
    await merge_vocals(paths, preview, slot, cancelled)
    return reference, preview


async def _run(path: Path, document: PreparationDocument, job: PreparationJob) -> None:
    from .voice_build import _engine_python, _ffmpeg, _spawn, _probe_duration, VoiceBuildError
    from .voice_separation import separate_vocal
    from .orchestrator.process import tail_log
    directory = path / 'preparations' / document.generation
    response = document.response
    try:
        directory.mkdir(parents=True)
        response.status = 'running'
        if job.timing is not None:
            job.timing.start()
        save(path, document)
        python = _engine_python()
        if not python.is_file():
            raise VoiceBuildError('engine_missing')
        for index, source in enumerate(item for item in response.options.sources if item.enabled):
            if job.cancelled:
                raise asyncio.CancelledError()
            previous = len(response.segments)
            duration = 0.0
            try:
                _phase(path, document, job, 'inspecting', 1, 'tasks', current_file=source.filename)
                raw = contained_file(path, f'recordings/{source.filename}')
                duration = await _probe_duration(raw)
                if not math.isfinite(duration) or duration <= 0:
                    raise VoiceBuildError('invalid_audio')
                if duration > 3600 or raw.stat().st_size > 512 * 1024 * 1024:
                    raise VoiceBuildError('source_too_long' if duration > 3600 else 'file_too_large')
                vocal = raw
                if source.kind == 'song':
                    _phase(path, document, job, 'separating', 1, 'tasks', current_file=source.filename)
                    vocal = await separate_vocal(raw, directory / f'separation_{index}', quality=response.options.separation_quality,
                        log_name=f'voice_prepare_sep_{path.name}_{index}', on_proc=job.track)
                _phase(path, document, job, 'normalizing', 1, 'tasks', current_file=source.filename)
                original = directory / f'original_{index}.wav'
                await _ffmpeg(['-y', '-i', str(vocal), '-ar', '44100', '-ac', '2', '-c:a', 'pcm_f32le', str(original)], job, lambda: job.cancelled)
                log_name = f'voice_analyze_{path.name}_{document.generation}_{index}'
                script = Path(__file__).with_name('prep_vocal.py')
                _phase(path, document, job, 'screening', 1, 'tasks', current_file=source.filename)
                code = await _spawn([str(python), str(script), '--analyze', str(original)], cwd=script.parent, log_name=log_name, slot=job)
                log = tail_log(log_name, lines=10)
                raw_report: JsonObject | None = None
                for line in log.splitlines():
                    if line.strip().startswith('{'):
                        raw_report = TypeAdapter(JsonObject).validate_json(line)
                if code != 0 or raw_report is None or not isinstance(raw_report.get('segments'), list):
                    raise VoiceBuildError('preparation_failed')
                input_seconds = TypeAdapter(float).validate_python(raw_report.get('input_seconds'))
                if input_seconds > 3600:
                    raise VoiceBuildError('source_too_long')
                cleaned: Path | None = None
                if response.options.clean:
                    _phase(path, document, job, 'cleaning', 1, 'tasks', current_file=source.filename)
                    clean_script = script.with_name('clean_vocal.py')
                    cleaned = directory / f'cleaned_{index}.wav'
                    clean_code = await _spawn([str(python), str(clean_script), str(original), str(cleaned)], cwd=script.parent,
                        log_name=f'voice_prepare_clean_{path.name}_{index}', slot=job)
                    if clean_code != 0 or not cleaned.is_file():
                        cleaned = None
                        response.warnings.append('clean_failed')
                items = raw_report['segments']
                if not isinstance(items, list):
                    raise VoiceBuildError('preparation_failed')
                accepted_sec = 0.0
                _phase(path, document, job, 'slicing', len(items), 'samples', current_file=source.filename)
                for segment_index, item in enumerate(items):
                    if not isinstance(item, dict):
                        raise VoiceBuildError('preparation_failed')
                    identifier = f'{document.generation}_{index}_{segment_index}'
                    start = TypeAdapter(float).validate_python(item.get('start_sec'))
                    end = TypeAdapter(float).validate_python(item.get('end_sec'))
                    segment = VoiceSegment.model_validate({**item, 'id': identifier, 'source_filename': source.filename,
                        'duration_sec': end - start, 'has_cleaned': cleaned is not None})
                    if end > input_seconds + .01 or end <= start:
                        raise VoiceBuildError('preparation_failed')
                    response.segments.append(segment)
                    accepted_sec += segment.duration_sec if segment.accepted else 0
                    files = SampleFiles(original=f'preparations/{document.generation}/{identifier}.wav')
                    await _ffmpeg(['-y', '-ss', str(start), '-i', str(original), '-t', str(end-start), '-c:a', 'pcm_f32le', str(path / files.original)], job, lambda: job.cancelled)
                    if cleaned:
                        files.cleaned = f'preparations/{document.generation}/{identifier}.cleaned.wav'
                        await _ffmpeg(['-y', '-ss', str(start), '-i', str(cleaned), '-t', str(end-start), '-c:a', 'pcm_f32le', str(path / files.cleaned)], job, lambda: job.cancelled)
                    document.samples[identifier] = files
                    _advance(path, document, job, segment_index + 1)
                response.sources.append(VoiceSourceReport(filename=source.filename, duration_sec=input_seconds,
                    accepted_sec=accepted_sec, rejected_sec=max(0, input_seconds-accepted_sec)))
                if job.timing is not None:
                    job.timing.progress.files_completed = index + 1
                save(path, document)
            except Exception as exc:
                from .voice_separation import VoiceSeparationError
                logger.warning('Voice source preparation failed for %s', source.filename, exc_info=True)
                error_code = exc.code if isinstance(exc, (VoiceBuildError, VoiceSeparationError)) and exc.code else 'invalid_audio'
                removed = {item.id for item in response.segments[previous:]}
                del response.segments[previous:]
                document.samples = {key: value for key, value in document.samples.items() if key not in removed}
                finite_duration = duration if math.isfinite(duration) and duration > 0 else 0.0
                response.sources.append(VoiceSourceReport(filename=source.filename, duration_sec=finite_duration,
                    accepted_sec=0, rejected_sec=finite_duration, error_code=error_code))
                if 'source_skipped' not in response.warnings:
                    response.warnings.append('source_skipped')
                if job.timing is not None:
                    job.timing.progress.files_completed = index + 1
                save(path, document)
        ranked = sorted((item for item in response.segments if item.accepted and item.duration_sec >= 1), key=lambda item: (item.duration_sec < 4, -item.score))
        total = 0.0
        for segment in ranked:
            if total + segment.duration_sec <= response.options.max_selected_seconds and len(response.selected_segment_ids) < MAX_SELECTED_SEGMENTS:
                response.selected_segment_ids.append(segment.id)
                total += segment.duration_sec
        selected = set(response.selected_segment_ids)
        response.selected_segment_ids = [item.id for item in response.segments if item.id in selected]
        if sum(item.duration_sec for item in ranked) > total:
            response.warnings.append('duration_limited')
        _phase(path, document, job, 'references', min(3, len(ranked)), 'samples')
        for reference_index, segment in enumerate(ranked[:3]):
            identifier = uuid.uuid4().hex
            duration = min(10.0, segment.duration_sec)
            response.references.append(VoiceReferenceCandidate(id=identifier, segment_id=segment.id, source_filename=segment.source_filename,
                start_sec=segment.start_sec, end_sec=segment.start_sec+duration, duration_sec=duration, score=segment.score))
            sample = document.samples[segment.id]
            files = SampleFiles(original=f'preparations/{document.generation}/reference_{identifier}.wav')
            await _ffmpeg(['-y', '-i', str(path / sample.original), '-t', str(duration), '-c:a', 'pcm_f32le', str(path / files.original)], job, lambda: job.cancelled)
            if sample.cleaned:
                files.cleaned = f'preparations/{document.generation}/reference_{identifier}.cleaned.wav'
                await _ffmpeg(['-y', '-i', str(path / sample.cleaned), '-t', str(duration), '-c:a', 'pcm_f32le', str(path / files.cleaned)], job, lambda: job.cancelled)
            document.references[identifier] = files
            _advance(path, document, job, reference_index + 1)
        response.accepted_seconds = total
        response.reference_id = response.references[0].id if response.references else None
        if response.reference_id and total >= 1:
            response.operation = 'coverage'
            save(path, document)
            await _measure_selected(path, document, job)
        response.operation = 'preparation'
        response.status = 'done' if response.reference_id and total >= 1 else 'failed'
        response.error_code = '' if response.status == 'done' else 'insufficient_usable_audio'
        save(path, document)
    except asyncio.CancelledError:
        if response.operation == 'coverage':
            _coverage_advisory(response, 'coverage_cancelled')
        else:
            response.status, response.error_code = 'cancelled', 'cancelled'
        save(path, document)
        raise
    except Exception as exc:
        logger.exception('Voice preparation failed for %s', path.name)
        response.status, response.error_code = 'failed', exc.code if isinstance(exc, VoiceBuildError) else 'preparation_failed'
        save(path, document)
    finally:
        from .voice_build import kill_proc
        try:
            await await_cleanup(kill_proc(job.proc))
        finally:
            job.proc = None
            _jobs.pop(path.resolve(), None)
