"""Durable two-source mashups using existing owned processes, GPU lease and DSP.

The browser observes persisted work. Raw inputs survive cancellation; completed
source receipts are reused only after hash verification. Missing model/native
readiness is an explicit setup action, never an implicit download.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
import errno
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from . import audio_versions, beat_projects, db, mashup_plan, mashup_render, mashup_store, music_analysis, music_dsp_runtime
from .audio_quality import AudioQualityError
from .beat_contracts import BeatError, canonical_json
from .beat_project_contracts import MusicProjectProvenance, MusicProjectPublication, MusicProjectSource, RenderedProjectLane
from .contracts import JsonObject, JsonValue
from .export_provenance import track_component
from .export_provenance_contracts import ProvenanceComponent
from .job_lifecycle import await_cleanup, cancel_and_wait, kill_process_tree, run_owned_thread
from .mashup_contracts import (MashupCreateRequest, MashupEditPlanRequest, MashupError, MashupFailure,
    MashupGeneratePlanRequest, MashupJob, MashupJobRequest, MashupProgress, MashupRenderArtifact,
    MashupRenderManifest, MashupSourceInput, MashupSourcePreview, MashupSourcePreviewRequest,
    MashupSourceSnapshot, MashupState, MashupStemSnapshot, MashupStoragePreview, MashupTimingUpdate, SourceSide)
from .mashup_plan import RATE
from .music_analysis_contracts import KeyEstimate, StemMusicSource, VersionMusicSource
from .music_dsp import MusicDspError
from .music_evidence import merge_music_evidence, serialize_music_evidence, shareable_music_evidence, track_music_evidence
from .stems import separate_file
from .video_io import copy_verified, hash_file
from .video_process import WorkerIdentity, read_owned_output, run_owned_command, spawn_owned, terminate_verified
from .video_projects import VideoProjectError
from .voice_separation import OfflineDemucs, VoiceSeparationError, offline_demucs

logger = logging.getLogger(__name__)
_lock = asyncio.Lock()
_semaphore = asyncio.Semaphore(1)
_tasks: dict[str, asyncio.Task[None]] = {}
_processes: dict[str, asyncio.subprocess.Process] = {}
_unverified: dict[str, WorkerIdentity] = {}
_closing = False
_retaining = 0
MAX_JOBS = 1000
MAX_QUEUED = 8
SEPARATION_TIMEOUT = 3600.0


def _checksum(value: BaseModel | JsonObject) -> str:
    content = canonical_json(value) if isinstance(value, BaseModel) else json.dumps(value,
        sort_keys=True, separators=(',', ':'), allow_nan=False, ensure_ascii=True).encode('ascii')
    return hashlib.sha256(content).hexdigest()


def _resolve(source: VersionMusicSource | StemMusicSource) -> Path:
    try:
        if source.kind == 'version':
            path = audio_versions.resolve_source(source.track_id, source.version_id)
        else:
            from .audio_exports import stem_source
            path = stem_source(source.track_id, source.stem)
        if (db.FILES_DIR.is_symlink() or path.is_symlink() or path.resolve() != path
                or not path.is_relative_to(db.FILES_DIR.resolve()) or not path.is_file()
                or path.suffix.lower() not in {'.wav', '.mp3', '.flac', '.ogg', '.m4a'}):
            raise MashupError('mashup_source_missing')
        return path
    except (OSError, HTTPException) as error:
        raise MashupError('mashup_source_missing') from error


async def _short_command(argv: list[str], timeout: float) -> bytes:
    outcome = await run_owned_command(argv, timeout=timeout, max_bytes=2 * 1024**2,
        stderr=asyncio.subprocess.STDOUT)
    if outcome.returncode != 0:
        raise MashupError('mashup_source_missing')
    return outcome.stdout


async def source_preview(request: MashupSourcePreviewRequest) -> MashupSourcePreview:
    path = _resolve(request.source)
    checksum = await hash_file(path, max_bytes=mashup_render.MAX_BYTES)
    facts = await mashup_render.probe(path, _short_command)
    if request.excerpt_end_sec > facts.duration_sec + .001:
        raise MashupError('mashup_invalid')
    if await hash_file(path, max_bytes=mashup_render.MAX_BYTES) != checksum:
        raise MashupError('mashup_source_changed')
    try:
        ready = await run_owned_thread(offline_demucs, 'fast')
    except VoiceSeparationError:
        ready = None
    retained, temporary, required = mashup_render.preparation_budget(facts.byte_count,
        request.excerpt_end_sec - request.excerpt_start_sec, model_bytes=0 if ready is None else ready.model_bytes)
    available = await run_owned_thread(mashup_render.free_bytes, mashup_render.root())
    return MashupSourcePreview(sha256=checksum, sample_rate_hz=facts.sample_rate_hz, channels=facts.channels,
        duration_sec=facts.duration_sec, source_bytes=facts.byte_count, retained_bytes=retained,
        temporary_bytes=temporary, required_free_bytes=required, free_bytes=available,
        can_start=available is not None and available >= required, separator_ready=ready is not None)


def _admit() -> None:
    if _closing or _unverified:
        raise MashupError('mashup_worker_unverified')
    if len(_tasks) >= MAX_QUEUED + 1 or db.get_db().execute('SELECT count(*) FROM mashup_jobs').fetchone()[0] >= MAX_JOBS:
        raise MashupError('mashup_busy')


def _start_task(job_id: str) -> None:
    task = asyncio.create_task(_run(job_id))
    _tasks[job_id] = task
    task.add_done_callback(lambda finished: _tasks.pop(job_id, None))


def _fsync(path: Path) -> None:
    with path.open('rb') as handle:
        os.fsync(handle.fileno())


def _analysis_key(source: MashupSourceInput, duration: float) -> KeyEstimate:
    reference = source.analysis
    if reference is None:
        return KeyEstimate()
    try:
        job = music_analysis.get(reference.job_id)
    except music_analysis.MusicAnalysisError as error:
        raise MashupError('mashup_analysis_changed') from error
    result = job.result
    if (job.status != 'done' or result is None or result.id != reference.result_id
            or result.source != source.source or result.audio_sha256 != source.sha256
            or result.configuration_sha256 != reference.configuration_sha256
            or result.override_revision != reference.override_revision):
        raise MashupError('mashup_analysis_changed')
    # A whole-song estimate is not a measured key for an arbitrary excerpt.
    if source.excerpt_start_sec != 0 or abs(source.excerpt_end_sec - duration) > 1 / result.sample_rate_hz:
        return KeyEstimate()
    return result.key


async def _capture(mashup_id: str, side: SourceSide, source: MashupSourceInput,
    quality: str) -> MashupSourceSnapshot:
    path = _resolve(source.source)
    component = track_component(source.source.track_id, source.source.version_id, source.sha256)
    evidence = shareable_music_evidence(track_music_evidence(source.source.track_id))
    folder = mashup_render.source_dir(mashup_id, side, create=True)
    raw = folder / ('raw' + path.suffix.lower())
    await copy_verified(path, raw, source.sha256, max_bytes=mashup_render.MAX_BYTES)
    await run_owned_thread(_fsync, raw)
    facts = await mashup_render.probe(raw, _short_command)
    if source.excerpt_end_sec > facts.duration_sec + .001:
        raise MashupError('mashup_invalid')
    key = _analysis_key(source, facts.duration_sec)
    for operator in source.operator_stems:
        stem_path = _resolve(operator)
        target = folder / ('operator-' + operator.stem + stem_path.suffix.lower())
        await copy_verified(stem_path, target, operator.sha256, max_bytes=mashup_render.MAX_BYTES)
        await run_owned_thread(_fsync, target)
    return MashupSourceSnapshot.model_validate({'side': side, 'input': source.model_dump(),
        'raw_filename': raw.name, 'source_bytes': facts.byte_count, 'duration_sec': facts.duration_sec,
        'association': 'operator_declared_unverified' if source.operator_stems else 'fresh_separation',
        'content_origin': component.content_origin, 'music_evidence': evidence.model_dump(), 'analysis_key': key.model_dump(),
        'separation_quality': quality,
        'preparation_recipe': 'ffmpeg-excerpt-operator-stems-v1' if source.operator_stems else 'ffmpeg-excerpt-demucs-local-v1'})


async def create(request: MashupCreateRequest) -> MashupState:
    global _retaining
    _retaining += 1
    try:
        return await _create_locked(request)
    except VideoProjectError as error:
        raise MashupError('mashup_source_changed') from error
    except FileNotFoundError as error:
        raise MashupError('mashup_source_missing') from error
    finally:
        _retaining -= 1


async def _create_locked(request: MashupCreateRequest) -> MashupState:
    checksum = _checksum(request)
    async with _lock:
        previous = mashup_store.submission(request.submission_key, checksum)
        if previous is not None:
            return mashup_store.get(previous.job.mashup_id)
        _admit()
        ready: OfflineDemucs | None = None
        if not request.source_a.operator_stems or not request.source_b.operator_stems:
            try:
                ready = await run_owned_thread(offline_demucs, request.separation_quality)
            except VoiceSeparationError as error:
                raise MashupError('mashup_separation_needed') from error
        required = 0
        for source in (request.source_a, request.source_b):
            path = _resolve(source.source)
            size = path.stat().st_size + sum(_resolve(stem).stat().st_size for stem in source.operator_stems)
            _, _, amount = mashup_render.preparation_budget(size, source.excerpt_end_sec - source.excerpt_start_sec,
                model_bytes=0 if ready is None or source.operator_stems else ready.model_bytes)
            required += amount
        available = await run_owned_thread(mashup_render.free_bytes, mashup_render.root())
        if available is None or available < required:
            raise MashupError('insufficient_disk')
        mashup_id, job_id, timestamp = uuid.uuid4().hex, uuid.uuid4().hex, mashup_store.now()
        accepted = False
        try:
            a = await _capture(mashup_id, 'a', request.source_a, request.separation_quality)
            b = await _capture(mashup_id, 'b', request.source_b, request.separation_quality)
            state = MashupState(id=mashup_id, revision=0, title=request.title, status='preparing',
                source_a=a, source_b=b, created_at=timestamp, updated_at=timestamp)
            state = state.model_copy(update={'candidates': mashup_plan.candidates(state)})
            job = MashupJob(id=job_id, mashup_id=mashup_id, mashup_revision=0, kind='preparation',
                status='queued', progress=MashupProgress(phase='queued'), created_at=timestamp)
            state = mashup_store.create(state, job, request.submission_key, checksum)
            accepted = True
            _start_task(job_id)
            return state
        finally:
            if not accepted:
                folder = mashup_render.root() / 'documents' / mashup_id
                if folder.exists() and folder.resolve() == folder:
                    await run_owned_thread(shutil.rmtree, folder)


def get_job(job_id: str) -> MashupJob:
    return mashup_store.get_job(job_id).job


def _change(job_id: str, **changes: object) -> MashupJob:
    current = get_job(job_id)
    return mashup_store.save_job(MashupJob.model_validate({**current.model_dump(), **changes}))


async def _drain(job_id: str, process: asyncio.subprocess.Process) -> None:
    try:
        await await_cleanup(kill_process_tree(process))
        if process.returncode is None:
            raise OSError('worker_exit_unverified')
    except BaseException:
        identity = mashup_store.get_job(job_id).identity
        if identity is not None:
            _unverified[job_id] = identity
        raise
    else:
        if process.stdin is not None:
            process.stdin.close()
        _processes.pop(job_id, None)
        _unverified.pop(job_id, None)
        mashup_store.identity(job_id, None)


def _environment() -> dict[str, str]:
    return {**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
        'HF_HUB_DISABLE_TELEMETRY': '1', 'DO_NOT_TRACK': '1'}


async def _spawn(job_id: str, argv: list[str], *, cwd: Path | None = None,
    stdout: int = asyncio.subprocess.PIPE) -> asyncio.subprocess.Process:
    directory = mashup_render.job_dir(job_id, create=True) / 'commands' / uuid.uuid4().hex
    directory.mkdir(parents=True)
    process = await spawn_owned(argv, receipt_path=directory / 'worker.json', cwd=cwd, env=_environment(),
        stdout=stdout, stderr=asyncio.subprocess.STDOUT,
        on_identity=lambda identity: mashup_store.identity(job_id, identity))
    _processes[job_id] = process
    return process


async def _command(job_id: str, argv: list[str], timeout: float) -> bytes:
    process = await _spawn(job_id, argv)
    try:
        output = await read_owned_output(process, max_bytes=2 * 1024**2, timeout=timeout)
        if process.returncode != 0:
            raise MashupError('mashup_render_failed')
        return output
    finally:
        await _drain(job_id, process)


async def _separate(job_id: str, audio: Path, output: Path, ready: OfflineDemucs) -> dict[str, Path]:
    process: asyncio.subprocess.Process | None = None
    runtime = output.parent / (output.name + '-runtime')

    async def spawn(ignored_argv: list[str], ignored_cwd: Path, ignored_environment: Mapping[str, str], stdout: int) -> asyncio.subprocess.Process:
        nonlocal process
        argv = await run_owned_thread(ready.command, audio, runtime)
        # The verified helper owns the private model repo; separate_file owns
        # the unique output directory it scans. Never invoke its legacy uv cmd.
        argv[argv.index('-o') + 1] = str(output)
        process = await _spawn(job_id, argv, cwd=ready.engine, stdout=stdout)
        return process

    try:
        async with asyncio.timeout(SEPARATION_TIMEOUT):
            result = await separate_file(audio, output, quality='high' if ready.model == 'htdemucs_ft' else 'fast', log_name='mashup-' + job_id,
                spawn=spawn, gpu_label='Mashup source preparation')
        if set(result) != {'vocals', 'drums', 'bass', 'other'}:
            raise MashupError('mashup_separation_failed')
        for stem, path in result.items():
            if (path.resolve() != path or not path.is_relative_to(output) or path.is_symlink()
                    or len([candidate for candidate in output.rglob('*.wav') if candidate.stem == stem]) != 1):
                raise MashupError('mashup_separation_failed')
        return result
    finally:
        if process is not None:
            await _drain(job_id, process)


async def _verified_source(mashup_id: str, snapshot: MashupSourceSnapshot) -> MashupSourceSnapshot | None:
    folder = mashup_render.source_dir(mashup_id, snapshot.side)
    receipt = folder / 'source.json'
    if not receipt.exists():
        return None
    if receipt.is_symlink() or receipt.stat().st_size > 8 * 1024**2:
        raise MashupError('mashup_source_changed')
    value = MashupSourceSnapshot.model_validate_json(await run_owned_thread(receipt.read_bytes))
    if (value.model_copy(update={'prepared': None, 'stems': []}) != snapshot.model_copy(update={'prepared': None, 'stems': []})
            or value.prepared is None or {stem.stem for stem in value.stems} != {'vocals', 'drums', 'bass', 'other'}):
        raise MashupError('mashup_source_changed')
    raw = mashup_render.contained(folder, value.raw_filename)
    if await hash_file(raw, max_bytes=mashup_render.MAX_BYTES) != value.input.sha256:
        raise MashupError('mashup_source_changed')
    for artifact in [value.prepared, *(stem.artifact for stem in value.stems)]:
        path = mashup_render.contained(folder, artifact.filename)
        if await hash_file(path, max_bytes=mashup_render.MAX_BYTES) != artifact.sha256:
            raise MashupError('mashup_source_changed')
        frames, size = await run_owned_thread(mashup_render._pcm, path)
        if frames != artifact.frames or size != artifact.byte_count:
            raise MashupError('mashup_source_changed')
    return value


async def _prepare_source(job_id: str, state: MashupState, source: MashupSourceSnapshot) -> MashupSourceSnapshot:
    completed = await _verified_source(state.id, source)
    if completed is not None:
        return completed
    folder = mashup_render.source_dir(state.id, source.side)
    raw = mashup_render.contained(folder, source.raw_filename)
    if await hash_file(raw, max_bytes=mashup_render.MAX_BYTES) != source.input.sha256:
        raise MashupError('mashup_source_changed')
    ready: OfflineDemucs | None = None
    if source.association == 'fresh_separation':
        try:
            ready = await run_owned_thread(offline_demucs, source.separation_quality)
        except VoiceSeparationError as error:
            raise MashupError('mashup_separation_needed') from error
    _, _, required = mashup_render.preparation_budget(source.source_bytes,
        source.input.excerpt_end_sec - source.input.excerpt_start_sec, model_bytes=0 if ready is None else ready.model_bytes)
    available = await run_owned_thread(mashup_render.free_bytes, mashup_render.root())
    if available is None or available < required:
        raise MashupError('insufficient_disk')
    work = mashup_render.job_dir(job_id, create=True) / ('prepare-' + source.side + '-' + uuid.uuid4().hex)
    work.mkdir()
    async def run(argv: list[str], timeout: float) -> bytes:
        return await _command(job_id, argv, timeout)
    _change(job_id, progress=MashupProgress(phase='decoding').model_dump())
    prepared = await mashup_render.decode_excerpt(raw, work / (uuid.uuid4().hex + '-prepared.wav'),
        source.input.excerpt_start_sec, source.input.excerpt_end_sec, run)
    stems: list[MashupStemSnapshot] = []
    if ready is not None:
        _change(job_id, progress=MashupProgress(phase='separating').model_dump())
        try:
            separated = await _separate(job_id, work / prepared.filename, work / 'separated', ready)
        except (VoiceSeparationError, RuntimeError, TimeoutError) as error:
            raise MashupError('mashup_separation_failed') from error
    else:
        separated = {}
    for stem in ('vocals', 'drums', 'bass', 'other'):
        target = work / (uuid.uuid4().hex + '-' + stem + '.wav')
        if source.association == 'fresh_separation':
            stem_path = separated.get(stem)
            if stem_path is None:
                raise MashupError('mashup_separation_failed')
            start, end = 0.0, prepared.frames / RATE
        else:
            operator = next((item for item in source.input.operator_stems if item.stem == stem), None)
            if operator is None:
                raise MashupError('mashup_invalid')
            candidates = list(folder.glob('operator-' + stem + '.*'))
            if len(candidates) != 1:
                raise MashupError('mashup_source_missing')
            stem_path = mashup_render.contained(folder, candidates[0].name)
            if await hash_file(stem_path, max_bytes=mashup_render.MAX_BYTES) != operator.sha256:
                raise MashupError('mashup_source_changed')
            start, end = source.input.excerpt_start_sec, source.input.excerpt_end_sec
        artifact = await mashup_render.decode_excerpt(stem_path, target, start, end, run)
        if artifact.frames != prepared.frames:
            raise MashupError('mashup_source_changed')
        stems.append(MashupStemSnapshot.model_validate({'stem': stem, 'artifact': artifact.model_dump()}))
    value = MashupSourceSnapshot.model_validate({**source.model_dump(), 'prepared': prepared.model_dump(),
        'stems': [stem.model_dump() for stem in stems]})
    for artifact in [prepared, *(stem.artifact for stem in stems)]:
        target = folder / artifact.filename
        if target.exists() or target.is_symlink():
            raise MashupError('mashup_source_changed')
        await run_owned_thread(_fsync, work / artifact.filename)
        await run_owned_thread((work / artifact.filename).replace, target)
    await run_owned_thread(mashup_render.write_model, folder / 'source.json', value)
    # Every subprocess has drained before temporary models/decoded copies go.
    await run_owned_thread(shutil.rmtree, work)
    return value


async def _prepare(job_id: str) -> None:
    record = mashup_store.get_job(job_id)
    a = await _prepare_source(job_id, record.snapshot, record.snapshot.source_a)
    b = await _prepare_source(job_id, record.snapshot, record.snapshot.source_b)
    current = mashup_store.get(record.snapshot.id)
    if current.revision != record.snapshot.revision:
        raise MashupError('mashup_revision_conflict')
    updated = current.model_copy(update={'status': 'ready', 'source_a': a, 'source_b': b, 'error': None})
    updated = updated.model_copy(update={'candidates': mashup_plan.candidates(updated)})
    mashup_store.update(current.id, current.revision, updated)
    _change(job_id, status='done', finished_at=mashup_store.now())


def update_timing(mashup_id: str, request: MashupTimingUpdate) -> MashupState:
    state = mashup_store.get(mashup_id)
    if state.revision != request.expected_revision:
        raise MashupError('mashup_revision_conflict')
    if state.status != 'ready':
        raise MashupError('mashup_analysis_incomplete')
    source = state.source_a if request.source == 'a' else state.source_b
    if source.prepared is None or request.timing.downbeat_frames[-1] != source.prepared.frames:
        raise MashupError('mashup_alignment_unsafe')
    if request.timing.supersedes_analysis is not None and request.timing.supersedes_analysis != source.input.analysis:
        raise MashupError('mashup_analysis_changed')
    source = source.model_copy(update={'timing': request.timing})
    state = state.model_copy(update={'source_a' if request.source == 'a' else 'source_b': source, 'plan': None})
    state = state.model_copy(update={'candidates': mashup_plan.candidates(state)})
    return mashup_store.update(mashup_id, request.expected_revision, state)


def generate_plan(mashup_id: str, request: MashupGeneratePlanRequest) -> MashupState:
    state = mashup_store.get(mashup_id)
    plan = mashup_plan.generate(state, request)
    return mashup_store.update(mashup_id, request.expected_revision, state.model_copy(update={'plan': plan}))


def edit_plan(mashup_id: str, request: MashupEditPlanRequest) -> MashupState:
    state = mashup_store.get(mashup_id)
    if state.revision != request.expected_revision:
        raise MashupError('mashup_revision_conflict')
    if state.plan is None:
        raise MashupError('mashup_analysis_incomplete')
    plan = state.plan.model_copy(update={'placements': request.placements,
        'revision': request.expected_revision + 1, 'duration_ticks': max(item.target_end_tick for item in request.placements)})
    mashup_plan.validate_plan(state, plan)
    return mashup_store.update(mashup_id, request.expected_revision, state.model_copy(update={'plan': plan}))


async def storage_preview(mashup_id: str, request: MashupJobRequest) -> MashupStoragePreview:
    state = mashup_store.get(mashup_id)
    if state.revision != request.expected_revision:
        raise MashupError('mashup_revision_conflict')
    native = await run_owned_thread(music_dsp_runtime.native)
    return await run_owned_thread(mashup_render.forecast, state, request, native_ready=native is not None)


async def start_render(mashup_id: str, request: MashupJobRequest) -> MashupJob:
    checksum = _checksum({'mashup_id': mashup_id, 'request': request.model_dump()})
    async with _lock:
        previous = mashup_store.submission(request.submission_key, checksum)
        if previous is not None:
            return previous.job
        _admit()
        state = mashup_store.get(mashup_id)
        if state.status != 'ready' or state.plan is None:
            raise MashupError('mashup_analysis_incomplete')
        forecast = await storage_preview(mashup_id, request)
        if not forecast.can_start:
            raise MashupError('insufficient_disk')
        if not forecast.stretch_ready:
            raise MashupError('mashup_stretch_needed')
        job = MashupJob(id=uuid.uuid4().hex, mashup_id=mashup_id, mashup_revision=state.revision,
            kind=request.mode, status='queued', request=request, progress=MashupProgress(phase='queued'), created_at=mashup_store.now())
        job = mashup_store.insert_job(state, job, request.submission_key, checksum)
        _start_task(job.id)
        return job


async def _execute_render(job_id: str) -> MashupRenderManifest:
    record = mashup_store.get_job(job_id)
    native = await run_owned_thread(music_dsp_runtime.native)
    if record.job.request is None:
        raise MashupError('mashup_invalid')
    forecast = await run_owned_thread(mashup_render.forecast, record.snapshot, record.job.request, native_ready=native is not None)
    if not forecast.can_start:
        raise MashupError('insufficient_disk')
    if not forecast.stretch_ready:
        raise MashupError('mashup_stretch_needed')
    async def run(argv: list[str], timeout: float) -> bytes:
        return await _command(job_id, argv, timeout)
    def progress(completed: int, total: int) -> None:
        _change(job_id, progress=MashupProgress(phase='transforming', completed=completed, total=total).model_dump())
    return await mashup_render.render(record.snapshot, record.job,
        mashup_render.job_dir(job_id, create=True) / 'output', run, native=native, progress=progress)


def _matches(record: mashup_store.StoredJob, manifest: MashupRenderManifest) -> None:
    state, request = record.snapshot, record.job.request
    if state.plan is None or request is None:
        raise MashupError('mashup_source_changed')
    start, frames, selected_items = mashup_render.selected(state, request)
    if (manifest.job_id != record.job.id or manifest.mashup_id != state.id or manifest.mashup_revision != state.revision
            or manifest.mode != request.mode or manifest.plan_sha256 != _checksum(state.plan)
            or manifest.source_a_sha256 != state.source_a.input.sha256 or manifest.source_b_sha256 != state.source_b.input.sha256
            or manifest.frames != frames or manifest.start_tick != start):
        raise MashupError('mashup_source_changed')
    expected_evidence = shareable_music_evidence(merge_music_evidence([state.source_a.music_evidence, state.source_b.music_evidence, None]))
    if manifest.music_evidence != expected_evidence:
        raise MashupError('mashup_source_changed')
    mixes = [item for item in manifest.artifacts if item.kind == 'mix']
    if (len(mixes) != 1 or mixes[0].frames != frames or manifest.metrics.source_sha256 != mixes[0].sha256
            or abs(manifest.metrics.duration_sec - frames / RATE) > 1 / RATE):
        raise MashupError('mashup_source_changed')
    expected = {item.placement.id: item for item in selected_items}
    if (len(manifest.placements) != len(expected)
            or {item.placement_id for item in manifest.placements} != set(expected)):
        raise MashupError('mashup_source_changed')
    by_id = {item.id: item for item in manifest.artifacts}
    for mapping in manifest.placements:
        selected_item = expected[mapping.placement_id]
        placement = selected_item.placement
        artifact = by_id.get(mapping.artifact_id)
        span = placement.source_end_frame - placement.source_start_frame
        if (artifact is None or artifact.kind != 'placement' or artifact.placement_id != placement.id
                or artifact.source != placement.source or artifact.stem != placement.stem or artifact.frames != selected_item.full_frames
                or mapping.artifact_start_frame != selected_item.trim_start
                or mapping.target_start_frame != selected_item.start_frame
                or mapping.target_end_frame != selected_item.start_frame + selected_item.trim_end - selected_item.trim_start
                or mapping.source_start_frame != placement.source_start_frame + round(selected_item.trim_start * span / selected_item.full_frames)
                or mapping.source_end_frame != placement.source_start_frame + round(selected_item.trim_end * span / selected_item.full_frames)):
            raise MashupError('mashup_source_changed')
    stems = [item for item in manifest.artifacts if item.kind == 'stem']
    expected_lanes = {(item.placement.source, item.placement.stem) for item in selected_items}
    if (len(stems) != len(expected_lanes) or {(item.source, item.stem) for item in stems} != expected_lanes
            or any(item.frames != frames for item in stems)):
        raise MashupError('mashup_source_changed')


async def _publish(job_id: str) -> MashupJob:
    record = mashup_store.get_job(job_id)
    if record.identity is not None or job_id in _unverified or job_id in _processes:
        raise MashupError('mashup_worker_unverified')
    manifest = await run_owned_thread(mashup_render.validate_manifest, job_id)
    _matches(record, manifest)
    if record.job.kind == 'full':
        state, plan = record.snapshot, record.snapshot.plan
        if plan is None:
            raise MashupError('mashup_invalid')
        mix = next((item for item in manifest.artifacts if item.kind == 'mix'), None)
        if mix is None:
            raise MashupError('mashup_save_failed')
        path = mashup_render.contained(mashup_render.job_dir(job_id) / 'output', mix.filename)
        track_id = record.track_id
        if track_id is None:
            # Recorded evidence is the authority even if a source library row
            # was deleted after admission. Missing separator identity remains
            # an incomplete lineage link, not a new rights determination.
            evidence = merge_music_evidence([state.source_a.music_evidence, state.source_b.music_evidence, None])
            parents: list[JsonValue] = [source.input.source.track_id for source in (state.source_a, state.source_b)]
            params: JsonObject = {'source': 'mashup', 'mashup_id': state.id, 'mashup_revision': state.revision,
                'mashup_job_id': job_id, 'mashup_plan_sha256': manifest.plan_sha256,
                'source_track_ids': parents, 'source_ancestry_complete': False,
                '_music_evidence': serialize_music_evidence(evidence)}
            track_id = db.insert_track(model='mashup', title=state.title, lyrics='', seed=None,
                duration_ms=manifest.frames * 1000 / RATE, wall_ms=None, params=params,
                audio_path=path, abc_path=None, mashup_job_id=job_id)
        audio_versions.retain_original(track_id, path)
        # Exclude publication IDs from the pinned source-manifest identity;
        # they are transaction receipts, not a change in rendered content.
        base_manifest = manifest.model_copy(update={'track_id': None, 'project_id': None})
        provenance = MusicProjectProvenance(workflow='mashup', workflow_id=state.id, workflow_revision=state.revision,
            source_manifest_sha256=_checksum(base_manifest), sources=[MusicProjectSource(
                track_id=source.input.source.track_id, version_id=source.input.source.version_id,
                sha256=source.input.sha256) for source in (state.source_a, state.source_b)])
        by_id = {item.id: item for item in manifest.artifacts}
        placements = {item.id: item for item in plan.placements}
        lanes: list[RenderedProjectLane] = []
        for item in manifest.placements:
            artifact, placement = by_id.get(item.artifact_id), placements.get(item.placement_id)
            if artifact is None or placement is None or artifact.kind != 'placement':
                raise MashupError('mashup_save_failed')
            lanes.append(RenderedProjectLane(lane_id=f'{placement.source}-{placement.stem}',
                label=f'{placement.source.upper()} {placement.stem}', artifact_id=artifact.id, sha256=artifact.sha256,
                source_url=f'/api/mashups/jobs/{job_id}/artifacts/{artifact.id}',
                start_sec=item.target_start_frame / RATE,
                trim_start_sec=item.artifact_start_frame / RATE,
                trim_end_sec=(item.artifact_start_frame + item.target_end_frame - item.target_start_frame) / RATE))
        built = beat_projects.build_audio_project(state.title, plan.settings.target_bpm, lanes, provenance)
        project_id = beat_projects.publish_audio_project(built, 'mashup-render:' + job_id)
        mashup_store.attach_project(job_id, project_id)
        manifest = manifest.model_copy(update={'track_id': track_id, 'project_id': project_id})
        await run_owned_thread(mashup_render.write_model, mashup_render.job_dir(job_id) / 'output' / 'manifest.json', manifest)
    return _change(job_id, status='done', manifest=manifest.model_dump(), error=None, finished_at=mashup_store.now(),
        progress=MashupProgress(phase='publishing', completed=1, total=1).model_dump())


def _failure(error: Exception) -> MashupFailure:
    if isinstance(error, MashupError):
        return MashupFailure(code=error.code, conflicting_ids=error.conflicting_ids)
    if isinstance(error, OSError) and error.errno == errno.ENOSPC:
        return MashupFailure(code='insufficient_disk')
    if isinstance(error, (VideoProjectError, FileNotFoundError)):
        return MashupFailure(code='mashup_source_changed')
    if isinstance(error, MusicDspError):
        code = error.code
        return MashupFailure(code='insufficient_disk' if code == 'dsp_insufficient_disk' else
            'mashup_stretch_needed' if code in ('dsp_stretch_unavailable', 'dsp_runtime_unverified') else
            'mashup_source_changed' if code in ('dsp_source_changed', 'dsp_invalid_source') else 'mashup_render_failed')
    if isinstance(error, VoiceSeparationError):
        return MashupFailure(code='mashup_separation_failed')
    return MashupFailure(code='mashup_render_failed')


async def _run(job_id: str) -> None:
    try:
        async with _semaphore:
            _change(job_id, status='running', started_at=mashup_store.now())
            record = mashup_store.get_job(job_id)
            if record.job.kind == 'preparation':
                await _prepare(job_id)
            else:
                manifest = await _execute_render(job_id)
                _change(job_id, status='saving', manifest=manifest.model_dump(),
                    progress=MashupProgress(phase='publishing').model_dump())
                await _publish(job_id)
    except asyncio.CancelledError:
        failure = MashupFailure(code='mashup_worker_unverified') if job_id in _unverified else None
        job = get_job(job_id)
        if failure is None and job.kind != 'preparation':
            try:
                manifest = await run_owned_thread(mashup_render.validate_manifest, job_id)
                _matches(mashup_store.get_job(job_id), manifest)
            except (OSError, MashupError, ValidationError):
                pass
            else:
                # A complete manifest crossed the cancellation boundary.
                # Preserve it for explicit publication rather than delete a
                # finished render or publish against the user's cancellation.
                _change(job_id, status='saving', manifest=manifest.model_dump(), error=None)
                return
        _change(job_id, status='blocked' if failure is not None else 'cancelled', error=None if failure is None else failure.model_dump(),
            finished_at=mashup_store.now())
        _preparation_terminal(job_id, 'cancelled', failure)
        await _cleanup_partial(job_id)
    except Exception as error:
        logger.exception('Mashup operation failed')
        job = get_job(job_id)
        failure = MashupFailure(code='mashup_worker_unverified') if job_id in _unverified else _failure(error)
        status = 'blocked' if job_id in _unverified else 'saving' if job.status == 'saving' else 'failed'
        _change(job_id, status=status, error=failure.model_dump(), finished_at=mashup_store.now())
        _preparation_terminal(job_id, 'failed', failure)
        await _cleanup_partial(job_id)


async def _cleanup_partial(job_id: str) -> None:
    record = mashup_store.get_job(job_id)
    if record.identity is not None or job_id in _processes or job_id in _unverified:
        return
    folder = mashup_render.job_dir(job_id)
    if not folder.is_dir():
        return
    for candidate in folder.iterdir():
        scratch = candidate.name.startswith(('prepare-', 'dsp-'))
        incomplete = candidate.name == 'output' and not (candidate / 'manifest.json').is_file()
        if scratch or incomplete:
            if candidate.is_symlink():
                candidate.unlink()
            elif candidate.is_dir():
                await run_owned_thread(shutil.rmtree, candidate)


def _preparation_terminal(job_id: str, status: str, failure: MashupFailure | None) -> None:
    record = mashup_store.get_job(job_id)
    if record.job.kind != 'preparation':
        return
    current = mashup_store.get(record.snapshot.id)
    if current.revision == record.snapshot.revision:
        updated = MashupState.model_validate({**current.model_dump(), 'status': status,
            'error': None if failure is None else failure.model_dump()})
        mashup_store.update(current.id, current.revision, updated)


async def retry_preparation(mashup_id: str) -> MashupState:
    async with _lock:
        _admit()
        state = mashup_store.get(mashup_id)
        if state.status == 'ready' or state.active_job_id is not None:
            return state
        job = MashupJob(id=uuid.uuid4().hex, mashup_id=state.id, mashup_revision=state.revision, kind='preparation',
            status='queued', progress=MashupProgress(phase='queued'), created_at=mashup_store.now())
        key = f'retry-preparation:{state.id}:{state.revision}'
        inserted = mashup_store.insert_job(state, job, key, _checksum({'id': state.id, 'revision': state.revision}))
        if inserted.id not in _tasks and inserted.status == 'queued':
            _start_task(inserted.id)
        return mashup_store.get(mashup_id)


async def cancel(job_id: str) -> MashupJob:
    job = get_job(job_id)
    if job.status in ('done', 'cancelled', 'failed', 'interrupted'):
        return job
    # Publication drains to a durable receipt; interrupting its transaction
    # cannot undo a track attachment and would create duplicate retry hazards.
    if job.status == 'saving':
        task = _tasks.get(job_id)
        if task is not None:
            await await_cleanup(asyncio.shield(task))
        return get_job(job_id)
    _change(job_id, status='cancelling')
    await cancel_and_wait(_tasks.get(job_id))
    current = get_job(job_id)
    if current.status == 'cancelling':
        record = mashup_store.get_job(job_id)
        if record.identity is not None:
            if not await terminate_verified(record.identity):
                _unverified[job_id] = record.identity
                return _change(job_id, status='blocked', error=MashupFailure(code='mashup_worker_unverified').model_dump())
            mashup_store.identity(job_id, None)
            _unverified.pop(job_id, None)
        _change(job_id, status='cancelled', finished_at=mashup_store.now())
        _preparation_terminal(job_id, 'cancelled', None)
    return get_job(job_id)


async def cancel_document(mashup_id: str) -> MashupState:
    state = mashup_store.get(mashup_id)
    if state.active_job_id is not None:
        await cancel(state.active_job_id)
    return mashup_store.get(mashup_id)


async def retry_save(job_id: str) -> MashupJob:
    async with _lock:
        job = get_job(job_id)
        if job.status == 'done':
            return job
        if job.status != 'saving' or job_id in _tasks:
            raise MashupError('mashup_busy')
        return await _publish(job_id)


def artifact_metadata(job_id: str, artifact_id: str) -> MashupRenderArtifact:
    job = get_job(job_id)
    if job.status not in ('done', 'saving') or job.manifest is None:
        raise MashupError('mashup_source_missing')
    chosen = next((item for item in job.manifest.artifacts if item.id == mashup_store.identifier(artifact_id)), None)
    if chosen is None:
        raise MashupError('mashup_source_missing')
    return chosen


def artifact(job_id: str, artifact_id: str) -> tuple[Path, MashupRenderArtifact]:
    chosen = artifact_metadata(job_id, artifact_id)
    return mashup_render.resolve_artifact(job_id, chosen), chosen


def project(job_id: str) -> MusicProjectPublication:
    record = mashup_store.get_job(job_id)
    if record.job.status != 'done' or record.job.kind != 'full' or record.project_id is None:
        raise MashupError('mashup_save_failed')
    row = db.get_db().execute('SELECT provenance_json FROM music_project_publications WHERE project_id=?', (record.project_id,)).fetchone()
    if row is None:
        raise MashupError('mashup_save_failed')
    return MusicProjectPublication(project_id=record.project_id,
        provenance=MusicProjectProvenance.model_validate_json(row['provenance_json']))


def publication_component(track_id: int, source_sha256: str, params: JsonObject) -> ProvenanceComponent | None:
    """Project retained owner facts after source deletion; params are selectors."""
    identifier = params.get('mashup_job_id')
    if not isinstance(identifier, str):
        return None
    try:
        record = mashup_store.get_job(identifier)
        manifest = record.job.manifest
        if (record.job.status != 'done' or record.job.kind != 'full' or record.job.request is None
                or record.job.request.mode != 'full' or record.track_id != track_id or manifest is None
                or manifest.track_id != track_id or params.get('mashup_id') != record.snapshot.id
                or params.get('mashup_revision') != record.snapshot.revision):
            return None
        _matches(record, manifest)
        mix = next(item for item in manifest.artifacts if item.kind == 'mix')
        if mix.sha256 != source_sha256:
            return None
        origins = {record.snapshot.source_a.content_origin, record.snapshot.source_b.content_origin}
        origin = 'unknown' if 'unknown' in origins else 'mixed' if len(origins) > 1 else record.snapshot.source_a.content_origin
        return ProvenanceComponent(role='audio', content_origin=origin, source_id='mashup:' + record.job.id,
            source_sha256=source_sha256, source_records_sha256=manifest.plan_sha256, source_record_count=2,
            music_evidence=manifest.music_evidence, engine='openfabric-cpu-dsp',
            classification_basis='unverified' if origin == 'unknown' else 'app_workflow')
    except (OSError, sqlite3.Error, ValueError, MashupError, StopIteration):
        return None


def work_busy() -> bool:
    return bool(_retaining or _tasks or _processes or _unverified)


async def recover() -> None:
    global _closing
    _closing = False
    for job in mashup_store.list_jobs(active_only=True):
        if job.id in _tasks:
            continue
        record = mashup_store.get_job(job.id)
        if record.identity is not None:
            if not await terminate_verified(record.identity):
                _unverified[job.id] = record.identity
                _change(job.id, status='blocked', error=MashupFailure(code='mashup_worker_unverified').model_dump())
                continue
            mashup_store.identity(job.id, None)
            _unverified.pop(job.id, None)
        try:
            if job.kind != 'preparation':
                manifest = await run_owned_thread(mashup_render.validate_manifest, job.id)
                _matches(record, manifest)
                _change(job.id, status='saving', manifest=manifest.model_dump())
                try:
                    await _publish(job.id)
                except Exception as error:
                    logger.exception('Mashup publication remains available for retry')
                    _change(job.id, status='saving', error=_failure(error).model_dump())
                continue
            a = await _verified_source(record.snapshot.id, record.snapshot.source_a)
            b = await _verified_source(record.snapshot.id, record.snapshot.source_b)
            if a is not None and b is not None:
                current = mashup_store.get(record.snapshot.id)
                if current.revision == record.snapshot.revision:
                    updated = current.model_copy(update={'source_a': a, 'source_b': b, 'status': 'ready', 'error': None})
                    updated = updated.model_copy(update={'candidates': mashup_plan.candidates(updated)})
                    mashup_store.update(current.id, current.revision, updated)
                    _change(job.id, status='done', finished_at=mashup_store.now())
                    continue
                if (current.revision == record.snapshot.revision + 1 and current.status == 'ready'
                        and current.source_a == a and current.source_b == b):
                    _change(job.id, status='done', finished_at=mashup_store.now())
                    continue
        except Exception:
            logger.exception('Mashup recovery could not adopt completed output')
        _change(job.id, status='interrupted', finished_at=mashup_store.now())
        _preparation_terminal(job.id, 'interrupted', None)


async def shutdown() -> None:
    global _closing
    # Admission holds the same lock while retaining files. Drain it before a
    # library move or shutdown can consider this owner idle.
    async with _lock:
        _closing = True
    for job_id, task in list(_tasks.items()):
        if get_job(job_id).status == 'saving':
            await await_cleanup(asyncio.shield(task))
        else:
            await cancel_and_wait(task)
    for job_id, identity in list(_unverified.items()):
        if await terminate_verified(identity):
            _unverified.pop(job_id, None)
            _processes.pop(job_id, None)
            mashup_store.identity(job_id, None)
