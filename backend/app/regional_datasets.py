"""Rights-aware saved-dataset manifests and bounded optional shared-audio review."""
from __future__ import annotations

from collections.abc import Sequence
import asyncio
from dataclasses import dataclass
import hashlib
import errno
import logging
import math
import os
from pathlib import Path
import struct
import json
import sys
import time
from typing import Literal

import numpy as np
import soundfile as sf

from . import db, lora_datasets, regional_dataset_store as store
from .job_lifecycle import await_cleanup, cancel_and_wait, run_owned_thread
from .module_catalog import available_bytes
from .music_lora_contracts import MusicLoraError
from .music_training_source_contracts import MusicLoraSource, MusicLoraRegionalSource
from .music_training_sources import source_path
from .video_io import copy_verified, hash_file
from .video_projects import VideoProjectError
from .regional_dataset_contracts import (RegionalDatasetReviewRequest, RegionalDatasetWarning,
    RegionalRecordingInspection, RegionalRecordingReview, RegionalSplitPlan, RegionalSplitRecording,
    RegionalDatasetError, RegionalDatasetInspectionRequest, RegionalDatasetManifest, RegionalDatasetPreview,
    RegionalDatasetReview, RegionalManifestRecording, RegionalManifestSelection, RegionalPreparationJob,
    RegionalPreparationRequest, RegionalPreparedExcerpt, RegionalPreparedManifest, RegionalPreparationJobs,
    RegionalPreparedSelection, RegionalTrainingSelection, RegionalTrainingSubmission)
from .stable_lora_contracts import StableLoraSample, StableLoraTrainingJob
from .regional_acoustic_contracts import (RegionalAcousticScanRequest, RegionalAcousticJob, RegionalAcousticJobs,
    RegionalAcousticResult, RegionalAcousticReport, RegionalAcousticExcerpt)
from .regional_acoustic_helper import (AcousticWorkerRequest, AcousticWorkerSource, AcousticWorkerOutcome,
    AcousticProcessIdentity, AUDITION_BYTES, RATE, configuration_sha256, validate_report)
from .video_process import WorkerIdentity, WorkerReceipt, spawn_owned, read_owned_output, terminate_verified
from .job_lifecycle import kill_process_tree
from .atomic_files import write_object

logger = logging.getLogger(__name__)
_tasks: dict[str, asyncio.Task[None]] = {}
_closing = False
_lock = asyncio.Lock()
_acoustic_ids: set[str] = set()
_acoustic_unverified: set[str] = set()
_acoustic_processes: dict[str, asyncio.subprocess.Process] = {}
_MAX_TOTAL_BYTES = 8 * 1024**3
_RESERVE = 64 * 1024**2


@dataclass(frozen=True)
class RegionalExcerptSource:
    path: Path
    sha256: str
    caption: str
    manifest_sha256: str


def _workspace(identifier: str, *, create: bool = False) -> Path:
    from pydantic import TypeAdapter
    from .music_analysis_contracts import MusicAnalysisId
    TypeAdapter(MusicAnalysisId).validate_python(identifier)
    root = db.FILES_DIR.resolve()
    folder = root / '_regional_datasets'
    workspace = folder / identifier
    if (db.FILES_DIR.is_symlink() or folder.is_symlink() or workspace.is_symlink()
            or folder.resolve() != folder or workspace.resolve() != workspace):
        raise RegionalDatasetError('regional_dataset_storage_unavailable', 503)
    if create:
        workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _signature(path: Path) -> tuple[int, int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _inspect_pcm(path: Path, identifier: str, checksum: str) -> RegionalRecordingInspection:
    started = time.monotonic()
    before = _signature(path)
    if path.is_symlink() or path.resolve() != path or not 0 < before[2] <= 512 * 1024**2:
        raise RegionalDatasetError('regional_dataset_limit', 422)
    info = sf.info(path)
    if (not 8000 <= info.samplerate <= 192000 or info.channels not in (1, 2)
            or not 0 < info.frames <= info.samplerate * 7200):
        raise RegionalDatasetError('regional_dataset_invalid', 422)
    digest = hashlib.sha256(struct.pack('<IIQ', info.samplerate, info.channels, info.frames))
    count = 0
    with sf.SoundFile(path) as audio:
        while len(block := audio.read(65536, dtype='float32', always_2d=True)):
            count += len(block)
            if (count > info.frames or not np.all(np.isfinite(block)) or np.max(np.abs(block)) > 16
                    or time.monotonic() - started > 120):
                raise RegionalDatasetError('regional_dataset_limit', 422)
            digest.update(np.asarray(block, dtype='<f4').tobytes())
    if count != info.frames or _signature(path) != before:
        raise RegionalDatasetError('regional_dataset_source_changed')
    return RegionalRecordingInspection(recording_id=identifier, source_sha256=checksum,
        decoded_pcm_sha256=digest.hexdigest(), source_bytes=before[2], sample_rate_hz=info.samplerate,
        channels=1 if info.channels == 1 else 2, frames=count)


async def _source_path(request: RegionalDatasetInspectionRequest) -> Path:
    try:
        return await run_owned_thread(source_path, MusicLoraSource(dataset=request.source), prefix='stable_lora')
    except (MusicLoraError, lora_datasets.LoraDatasetError, OSError) as error:
        raise RegionalDatasetError('regional_dataset_source_missing', 404) from error


async def inspect_recording(request: RegionalDatasetInspectionRequest) -> RegionalRecordingInspection:
    try:
        path = await _source_path(request)
        signature = _signature(path)
        checksum = await hash_file(path, max_bytes=512 * 1024**2)
        result = await run_owned_thread(_inspect_pcm, path, request.recording_id, checksum)
        if await _source_path(request) != path or signature != _signature(path):
            raise RegionalDatasetError('regional_dataset_source_changed')
        return result
    except (OSError, RuntimeError, VideoProjectError) as error:
        raise RegionalDatasetError('regional_dataset_source_changed') from error


async def save_review(request: RegionalDatasetReviewRequest) -> RegionalDatasetReview:
    try:
        snapshot = await run_owned_thread(lora_datasets.read_saved_dataset, request.dataset_id, request.dataset_revision)
    except lora_datasets.LoraDatasetError as error:
        raise RegionalDatasetError('regional_dataset_revision_conflict') from error
    if not {item.source.sample_id for item in request.recordings} <= {item.id for item in snapshot.samples}:
        raise RegionalDatasetError('regional_dataset_source_missing', 404)
    _acoustic_evidence(request)
    await _verify_acoustic_distinct(request)
    return store.save_review(db.get_db(), request)


def get_review(dataset_id: str) -> RegionalDatasetReview:
    return store.get_review(db.get_db(), dataset_id)


def get_manifest(identifier: str) -> RegionalPreparedManifest:
    return store.get_manifest(db.get_db(), identifier)


def list_preparations(dataset_id: str | None = None) -> RegionalPreparationJobs:
    return RegionalPreparationJobs(jobs=[get(job.id) for job in store.list_preparations(db.get_db(), dataset_id)])


def _selected_review(selection: RegionalManifestSelection) -> RegionalDatasetReview:
    result = store.get_review(db.get_db(), selection.dataset_id)
    if result.revision != selection.review_revision or result.sha256 != selection.review_sha256:
        raise RegionalDatasetError('regional_dataset_revision_conflict')
    return result


async def _inspections(review: RegionalDatasetReview) -> list[RegionalRecordingInspection]:
    result: list[RegionalRecordingInspection] = []
    size = 0
    for recording in review.request.recordings:
        request = RegionalDatasetInspectionRequest(recording_id=recording.id, source=recording.source)
        path = await _source_path(request)
        size += path.stat().st_size
        if size > _MAX_TOTAL_BYTES:
            raise RegionalDatasetError('regional_dataset_limit', 422)
        result.append(await inspect_recording(request))
    return result


def _forecast(review: RegionalDatasetReview, inspections: Sequence[RegionalRecordingInspection]) -> RegionalDatasetPreview:
    try:
        split = split_recordings(review.request, inspections, _acoustic_evidence(review.request, inspections))
    except ValueError as error:
        if str(error) == 'regional_dataset_source_changed':
            raise RegionalDatasetError('regional_dataset_source_changed') from error
        raise RegionalDatasetError('regional_dataset_invalid', 422) from error
    retained = {item.recording_id for item in split.recordings if item.split != 'excluded'}
    by_id = {item.recording_id: item for item in inspections}
    unique = {item.source_sha256: item.source_bytes for item in inspections if item.recording_id in retained}
    source_bytes = sum(unique.values())
    excerpt_bytes = sum((item.end_frame - item.start_frame) * by_id[item.recording_id].channels * 4 + 4096
        for item in review.request.excerpts if item.recording_id in retained)
    if source_bytes + excerpt_bytes > _MAX_TOTAL_BYTES:
        raise RegionalDatasetError('regional_dataset_limit', 422)
    required = source_bytes + 2 * excerpt_bytes + _RESERVE
    free = available_bytes(db.FILES_DIR)
    enough = free is not None and free >= required
    return RegionalDatasetPreview(review_revision=review.revision, review_sha256=review.sha256, split=split,
        source_bytes=source_bytes, excerpt_bytes=excerpt_bytes, required_free_bytes=required,
        free_bytes=min(free, 9007199254740991) if free is not None else None,
        can_prepare=enough and split.training_recordings > 0,
        error_code='insufficient_disk' if not enough else 'regional_dataset_rights_required' if split.training_recordings == 0 else None)


async def preview(selection: RegionalManifestSelection) -> RegionalDatasetPreview:
    review = _selected_review(selection)
    await _verify_acoustic_distinct(review.request)
    result = _forecast(review, await _inspections(review))
    _selected_review(selection)
    return result


def _crop_excerpt(source: Path, output: Path, excerpt: RegionalPreparedExcerpt, source_signature: tuple[int, int, int, int, int]) -> None:
    """Write one bounded float32 crop; the original snapshot is never modified."""
    started, remaining = time.monotonic(), excerpt.frames
    if source.is_symlink() or _signature(source) != source_signature:
        raise RegionalDatasetError('regional_dataset_source_changed')
    with sf.SoundFile(source) as original, output.open('xb') as handle:
        original.seek(excerpt.source_start_frame)
        with sf.SoundFile(handle.fileno(), mode='w', samplerate=excerpt.sample_rate_hz, channels=excerpt.channels,
                format='WAV', subtype='FLOAT', closefd=False) as copied:
            while remaining:
                count = min(remaining, 65536)
                block = original.read(count, dtype='float32', always_2d=True)
                if len(block) != count or not np.all(np.isfinite(block)) or time.monotonic() - started > 120:
                    raise RegionalDatasetError('regional_dataset_invalid', 422)
                copied.write(block); remaining -= count
        handle.flush(); os.fsync(handle.fileno())
    if source.is_symlink() or _signature(source) != source_signature:
        raise RegionalDatasetError('regional_dataset_source_changed')
    info = sf.info(output)
    if (info.frames, info.samplerate, info.channels, info.subtype) != (excerpt.frames, excerpt.sample_rate_hz, excerpt.channels, 'FLOAT'):
        raise RegionalDatasetError('regional_dataset_invalid', 422)


def _candidate(job: RegionalPreparationJob) -> RegionalDatasetManifest:
    path = _workspace(job.id) / 'manifest.json'
    if (path.is_symlink() or path.resolve() != path or not path.is_file()
            or path.stat().st_size > 16 * 1024**2 or job.prepared_manifest_sha256 is None):
        raise RegionalDatasetError('regional_dataset_source_changed')
    wrapped = RegionalPreparedManifest.model_validate_json(path.read_bytes())
    manifest = wrapped.manifest
    if (wrapped.sha256 != job.prepared_manifest_sha256 or store.canonical_sha256(manifest) != wrapped.sha256
            or manifest.id != job.id or manifest.dataset_id != job.request.dataset_id
            or manifest.review_revision != job.request.review_revision or manifest.review_sha256 != job.request.review_sha256):
        raise RegionalDatasetError('regional_dataset_source_changed')
    return manifest


def _write_candidate(workspace: Path, manifest: RegionalDatasetManifest) -> str:
    wrapped = RegionalPreparedManifest(sha256=store.canonical_sha256(manifest), manifest=manifest)
    partial, target = workspace / 'manifest.partial.json', workspace / 'manifest.json'
    with partial.open('xb') as handle:
        handle.write(wrapped.model_dump_json().encode('utf-8')); handle.flush(); os.fsync(handle.fileno())
    os.link(partial, target, follow_symlinks=False)
    try:
        partial.unlink()
    except OSError:
        logger.warning('Completed regional manifest retained after partial cleanup failed')
    return wrapped.sha256


def get(identifier: str) -> RegionalPreparationJob:
    job = store.get_preparation(db.get_db(), identifier)
    can_retry = False
    if job.status in ('failed', 'cancelled', 'interrupted') and job.prepared_manifest_sha256 is not None:
        try:
            _candidate(job)
            can_retry = True
        except (OSError, ValueError, RegionalDatasetError):
            pass
    return job.model_copy(update={'can_retry_save': can_retry})


def work_busy() -> bool:
    from .regional_evaluation import work_busy as evaluation_busy
    return bool(_tasks) or bool(_acoustic_unverified) or evaluation_busy()


async def _run(identifier: str) -> None:
    job = get(identifier)
    try:
        async with lora_datasets.native_lock:
            workspace = _workspace(identifier, create=True)
            sources, excerpts_dir = workspace / 'sources', workspace / 'excerpts'
            sources.mkdir(exist_ok=False); excerpts_dir.mkdir(exist_ok=False)
            review = _selected_review(job.request)
            inspections = await _inspections(review)
            await _verify_acoustic_distinct(review.request)
            forecast = _forecast(review, inspections)
            if not forecast.can_prepare:
                raise RegionalDatasetError(forecast.error_code or 'regional_dataset_rights_required')
            job = job.model_copy(update={'status': 'running', 'phase': 'snapshot'})
            store.update_preparation(db.get_db(), job)
            by_id = {item.recording_id: item for item in inspections}
            splits = {item.recording_id: item.split for item in forecast.split.recordings}
            copied: dict[str, Path] = {}
            signatures: dict[Path, tuple[int, int, int, int, int]] = {}
            original_paths: dict[str, Path] = {}
            for recording in review.request.recordings:
                if splits[recording.id] == 'excluded':
                    continue
                if recording.source_sha256 not in copied:
                    source = await _source_path(RegionalDatasetInspectionRequest(recording_id=recording.id, source=recording.source))
                    destination = sources / (recording.id + source.suffix.lower())
                    await copy_verified(source, destination, recording.source_sha256, max_bytes=512 * 1024**2)
                    with destination.open('rb') as handle:
                        os.fsync(handle.fileno())
                    copied[recording.source_sha256] = destination
                    signatures[destination] = _signature(destination)
                original_paths[recording.id] = copied[recording.source_sha256]
                job = job.model_copy(update={'completed_items': job.completed_items + 1})
                store.update_preparation(db.get_db(), job)
            job = job.model_copy(update={'phase': 'cropping'})
            store.update_preparation(db.get_db(), job)
            completed: list[RegionalPreparedExcerpt] = []
            for excerpt in review.request.excerpts:
                split = splits[excerpt.recording_id]
                if split == 'excluded':
                    continue
                facts = by_id[excerpt.recording_id]
                descriptor = RegionalPreparedExcerpt(id=excerpt.id, recording_id=excerpt.recording_id,
                    split=split, source_start_frame=excerpt.start_frame, source_end_frame=excerpt.end_frame,
                    frames=excerpt.end_frame - excerpt.start_frame, sample_rate_hz=facts.sample_rate_hz,
                    channels=facts.channels, sha256='0' * 64)
                partial, target = excerpts_dir / (excerpt.id + '.partial.wav'), excerpts_dir / (excerpt.id + '.wav')
                source_copy = original_paths[excerpt.recording_id]
                await run_owned_thread(_crop_excerpt, source_copy, partial, descriptor, signatures[source_copy])
                checksum = await hash_file(partial, max_bytes=1024**3)
                os.link(partial, target, follow_symlinks=False)
                try:
                    partial.unlink()
                except OSError:
                    logger.warning('Retained completed regional crop after partial cleanup failed')
                completed.append(descriptor.model_copy(update={'sha256': checksum}))
                job = job.model_copy(update={'completed_items': job.completed_items + 1})
                store.update_preparation(db.get_db(), job)
            _selected_review(job.request)
            job = job.model_copy(update={'phase': 'publishing'})
            store.update_preparation(db.get_db(), job)
            manifest = RegionalDatasetManifest(id=job.id, dataset_id=job.request.dataset_id,
                review_revision=review.revision, review_sha256=review.sha256, created_at=store.now(),
                tradition=review.request.tradition, split_seed=review.request.split_seed, split=forecast.split,
                recordings=[RegionalManifestRecording(review=recording, inspection=by_id[recording.id]) for recording in review.request.recordings], excerpts=completed)
            evidence = _acoustic_evidence(review.request, inspections)
            if evidence is not None:
                manifest = manifest.model_copy(update={'acoustic_report': evidence, 'acoustic_review': review.request.acoustic_review})
            for checksum, path in copied.items():
                if _signature(path) != signatures[path] or await hash_file(path, max_bytes=512 * 1024**2) != checksum:
                    raise RegionalDatasetError('regional_dataset_source_changed')
            checksum = await run_owned_thread(_write_candidate, workspace, manifest)
            job = job.model_copy(update={'prepared_manifest_sha256': checksum})
            store.update_preparation(db.get_db(), job)
            store.publish_manifest(db.get_db(), job, manifest)
    except asyncio.CancelledError:
        job = get(identifier)
        store.update_preparation(db.get_db(), job.model_copy(update={'status': 'cancelled', 'error_code': 'regional_dataset_cancelled'}))
        raise
    except Exception as error:
        logger.warning('Regional dataset preparation retained after failure', exc_info=True)
        job = get(identifier)
        code = error.code if isinstance(error, RegionalDatasetError) else 'regional_dataset_source_changed' if isinstance(error, VideoProjectError) else 'insufficient_disk' if isinstance(error, OSError) and error.errno == errno.ENOSPC else 'regional_dataset_storage_unavailable'
        store.update_preparation(db.get_db(), job.model_copy(update={'status': 'failed', 'error_code': code}))
    finally:
        _tasks.pop(identifier, None)


async def start(request: RegionalPreparationRequest) -> RegionalPreparationJob:
    from .resource_admission import admission_lock, require_setup_idle
    async with admission_lock:
        async with _lock:
            previous = db.get_db().execute('SELECT id FROM regional_dataset_preparations WHERE submission_key=?', (request.submission_key,)).fetchone()
            if previous is not None:
                job, _ = store.create_preparation(db.get_db(), request, 0)
                return get(job.id)
            require_setup_idle()
            if _closing or _tasks or _acoustic_unverified:
                raise RegionalDatasetError('regional_dataset_busy')
            review = _selected_review(request)
            job, created = store.create_preparation(db.get_db(), request, len(review.request.recordings) + len(review.request.excerpts))
            if created:
                _tasks[job.id] = asyncio.create_task(_run(job.id))
            return job


async def _verify_retained(manifest: RegionalDatasetManifest) -> None:
    workspace = _workspace(manifest.id)
    for folder in (workspace / 'sources', workspace / 'excerpts'):
        if folder.is_symlink() or folder.resolve() != folder or not folder.is_dir():
            raise RegionalDatasetError('regional_dataset_source_changed')
    copied: set[str] = set()
    splits = {item.recording_id: item.split for item in manifest.split.recordings}
    for recording in manifest.recordings:
        checksum = recording.review.source_sha256
        if splits[recording.review.id] == 'excluded' or checksum in copied:
            continue
        candidates = [workspace / 'sources' / (recording.review.id + suffix) for suffix in ('.wav', '.mp3', '.flac')]
        present = [path for path in candidates if path.exists() or path.is_symlink()]
        if len(present) != 1 or present[0].is_symlink() or present[0].resolve() != present[0] or await hash_file(present[0], max_bytes=512 * 1024**2) != checksum:
            raise RegionalDatasetError('regional_dataset_source_changed')
        copied.add(checksum)
    for excerpt in manifest.excerpts:
        path = workspace / 'excerpts' / (excerpt.id + '.wav')
        if path.is_symlink() or path.resolve() != path or await hash_file(path, max_bytes=512 * 1024**2) != excerpt.sha256:
            raise RegionalDatasetError('regional_dataset_source_changed')


async def _retry_save(identifier: str) -> None:
    try:
        job = get(identifier)
        manifest = _candidate(job)
        _selected_review(job.request)
        await _verify_acoustic_distinct(_selected_review(job.request).request)
        await _verify_retained(manifest)
        _selected_review(job.request)
        store.publish_manifest(db.get_db(), get(identifier), manifest)
    except asyncio.CancelledError:
        job = get(identifier)
        store.update_preparation(db.get_db(), job.model_copy(update={'status': 'cancelled', 'error_code': 'regional_dataset_cancelled'}))
        raise
    except Exception as error:
        logger.warning('Regional dataset save retained after failure', exc_info=True)
        job = get(identifier)
        code = error.code if isinstance(error, RegionalDatasetError) else 'regional_dataset_source_changed' if isinstance(error, (VideoProjectError, OSError)) else 'regional_dataset_storage_unavailable'
        store.update_preparation(db.get_db(), job.model_copy(update={'status': 'failed', 'error_code': code}))
    finally:
        _tasks.pop(identifier, None)


async def retry_save(identifier: str) -> RegionalPreparationJob:
    from .resource_admission import admission_lock, require_setup_idle
    async with admission_lock:
        async with _lock:
            job = get(identifier)
            if job.status == 'done' or identifier in _tasks:
                return job
            require_setup_idle()
            if _closing or _tasks or _acoustic_unverified:
                raise RegionalDatasetError('regional_dataset_busy')
            if not job.can_retry_save:
                raise RegionalDatasetError('regional_dataset_source_changed')
            _selected_review(job.request)
            job = job.model_copy(update={'status': 'running', 'phase': 'publishing', 'error_code': None, 'can_retry_save': False})
            store.update_preparation(db.get_db(), job, retry_save=True)
            _tasks[identifier] = asyncio.create_task(_retry_save(identifier))
            return job


async def cancel(identifier: str) -> RegionalPreparationJob:
    task = _tasks.get(identifier)
    await cancel_and_wait(task)
    job = get(identifier)
    if task is not None and job.status in ('queued', 'running'):
        store.update_preparation(db.get_db(), job.model_copy(update={'status': 'cancelled', 'error_code': 'regional_dataset_cancelled'}))
    return get(identifier)


def training_excerpt_source(identifier: str, manifest_sha256: str, excerpt_id: str) -> RegionalExcerptSource:
    """Metadata projection only; the owned trainer must verify returned expected SHA."""
    wrapped = store.get_manifest(db.get_db(), identifier)
    if wrapped.sha256 != manifest_sha256:
        raise RegionalDatasetError('regional_dataset_source_changed')
    item = next((value for value in wrapped.manifest.excerpts if value.id == excerpt_id), None)
    if item is None:
        raise RegionalDatasetError('regional_dataset_source_missing', 404)
    if item.split != 'train':
        raise RegionalDatasetError('regional_dataset_holdout')
    path = _workspace(identifier) / 'excerpts' / (item.id + '.wav')
    if path.parent.is_symlink() or path.is_symlink() or path.resolve() != path or not path.is_file():
        raise RegionalDatasetError('regional_dataset_source_changed')
    facts = sf.info(path)
    if (facts.frames, facts.samplerate, facts.channels, facts.subtype) != (item.frames, item.sample_rate_hz, item.channels, 'FLOAT'):
        raise RegionalDatasetError('regional_dataset_source_changed')
    recording = next(value for value in wrapped.manifest.recordings if value.review.id == item.recording_id)
    return RegionalExcerptSource(path, item.sha256, recording.review.caption, wrapped.sha256)


def training_selection(selection: RegionalPreparedSelection) -> RegionalTrainingSelection:
    wrapped = get_manifest(selection.manifest_id)
    manifest = wrapped.manifest
    if wrapped.sha256 != selection.manifest_sha256:
        raise RegionalDatasetError('regional_dataset_source_changed')
    _selected_review(RegionalManifestSelection(dataset_id=manifest.dataset_id, review_revision=manifest.review_revision,
        review_sha256=manifest.review_sha256))
    samples: list[StableLoraSample] = []
    for excerpt in manifest.excerpts:
        if excerpt.split != 'train':
            continue
        source = training_excerpt_source(manifest.id, wrapped.sha256, excerpt.id)
        samples.append(StableLoraSample(regional_manifest=MusicLoraRegionalSource(manifest_id=manifest.id,
            manifest_sha256=wrapped.sha256, excerpt_id=excerpt.id), caption=source.caption))
    if not samples:
        raise RegionalDatasetError('regional_dataset_rights_required')
    return RegionalTrainingSelection(**selection.model_dump(), samples=samples,
        held_out_excerpts=sum(item.split == 'holdout' for item in manifest.excerpts),
        evaluation_sufficient=manifest.split.evaluation_sufficient,
        adapter_distribution_permitted=all(item.adapter_distribution_permitted for item in manifest.split.recordings if item.split == 'train'))


async def submit_training(submission: RegionalTrainingSubmission) -> StableLoraTrainingJob:
    from . import stable_lora
    selected = training_selection(RegionalPreparedSelection(manifest_id=submission.manifest_id, manifest_sha256=submission.manifest_sha256))
    if submission.request.samples != selected.samples:
        raise RegionalDatasetError('regional_dataset_holdout')
    return await stable_lora.submit(submission.request)


async def excerpt_audio(identifier: str, excerpt_id: str) -> Path:
    """Listening may read either split; training goes through the stricter projection."""
    manifest = get_manifest(identifier).manifest
    item = next((value for value in manifest.excerpts if value.id == excerpt_id), None)
    if item is None:
        raise RegionalDatasetError('regional_dataset_source_missing', 404)
    path = _workspace(identifier) / 'excerpts' / (item.id + '.wav')
    if path.parent.is_symlink() or path.is_symlink() or path.resolve() != path:
        raise RegionalDatasetError('regional_dataset_source_changed')
    if await hash_file(path, max_bytes=512 * 1024**2) != item.sha256:
        raise RegionalDatasetError('regional_dataset_source_changed')
    return path


async def recover() -> None:
    global _closing
    _closing = True
    for identifier in store.acoustic_scan_ids(db.get_db(), active=True):
        if identifier not in _tasks:
            stopped = await _stop_acoustic_identity(identifier)
            scan_job = get_acoustic_scan(identifier)
            if scan_job.status in ('queued', 'running'):
                store.update_acoustic_scan(db.get_db(), scan_job.model_copy(update={'status': 'interrupted' if stopped else 'failed',
                    'error_code': 'regional_dataset_interrupted' if stopped else 'regional_dataset_worker_unverified'}))
    for identifier in store.active_preparation_ids(db.get_db()):
        job = get(identifier)
        if job.id not in _tasks:
            store.update_preparation(db.get_db(), job.model_copy(update={'status': 'interrupted', 'error_code': 'regional_dataset_interrupted'}))
    _closing = False
    from .regional_evaluation import recover as recover_evaluations
    await recover_evaluations()


async def shutdown() -> None:
    global _closing
    _closing = True
    if _tasks:
        await await_cleanup(asyncio.gather(*(cancel_acoustic_scan(identifier) if identifier in _acoustic_ids else cancel(identifier)
            for identifier in tuple(_tasks)), return_exceptions=True))
    for identifier in tuple(_acoustic_unverified):
        await _stop_acoustic_identity(identifier)
    from .regional_evaluation import shutdown as shutdown_evaluations
    await shutdown_evaluations()


def _permission(recording: RegionalRecordingReview, use: Literal['reference', 'training', 'source_redistribution', 'adapter_distribution']) -> tuple[bool, bool]:
    values = [{'reference': item.permissions.reference, 'training': item.permissions.training,
        'source_redistribution': item.permissions.source_redistribution,
        'adapter_distribution': item.permissions.adapter_distribution}[use] for item in recording.rights]
    conflict = 'allowed' in values and 'blocked' in values
    permitted = 'blocked' not in values and any(permission == 'allowed'
        and item.restriction in ('conditional', 'unrestricted_declared') for item, permission in zip(recording.rights, values))
    return permitted, conflict


def split_recordings(review: RegionalDatasetReviewRequest, inspections: Sequence[RegionalRecordingInspection],
                     acoustic: RegionalAcousticResult | None = None) -> RegionalSplitPlan:
    """Cluster exact hashes and declared shared provenance before assigning any crop."""
    by_id = {item.recording_id: item for item in inspections}
    if len(by_id) != len(inspections) or set(by_id) != {item.id for item in review.recordings}:
        raise ValueError('regional_dataset_invalid')
    if any(by_id[item.id].source_sha256 != item.source_sha256 for item in review.recordings):
        raise ValueError('regional_dataset_source_changed')
    for excerpt in review.excerpts:
        source = by_id[excerpt.recording_id]
        if (excerpt.end_frame > source.frames
                or not math.ceil(source.sample_rate_hz * .1) <= excerpt.end_frame - excerpt.start_frame <= source.sample_rate_hz * 600
                or (excerpt.end_frame - excerpt.start_frame) * source.channels * 4 + 4096 > 512 * 1024**2):
            raise ValueError('regional_dataset_invalid')
    parent = {item.id: item.id for item in review.recordings}
    def root(identifier: str) -> str:
        while parent[identifier] != identifier:
            identifier = parent[identifier]
        return identifier
    keys: dict[tuple[str, str], str] = {}
    identities_by_id: dict[str, list[tuple[str, str]]] = {}
    for item in sorted(review.recordings, key=lambda value: value.id):
        identities = [('file', item.source_sha256), ('pcm', by_id[item.id].decoded_pcm_sha256),
            *[(name, value) for name, value in (('original', item.original_recording_sha256),
                ('performer', item.performer_group_sha256), ('session', item.session_group_sha256),
                ('declared_near_copy', item.declared_near_copy_group_sha256)) if value is not None]]
        identities_by_id[item.id] = identities
        for identity in identities:
            previous = keys.setdefault(identity, item.id)
            a, b = root(item.id), root(previous)
            parent[max(a, b)] = min(a, b)
    acoustic_edges = 0
    if review.acoustic_review is not None:
        if acoustic is None or store.canonical_sha256(acoustic.report) != acoustic.sha256 or acoustic.sha256 != review.acoustic_review.scan_sha256:
            raise ValueError('regional_dataset_source_changed')
        _check_acoustic_selection(review, acoustic.report, inspections)
        ignored = {item.candidate_id for item in review.acoustic_review.decisions if item.decision == 'distinct_after_review'}
        if any(item.candidate_id not in {candidate.id for candidate in acoustic.report.candidates} for item in review.acoustic_review.decisions):
            raise ValueError('regional_dataset_source_changed')
        recordings = {item.id: item.recording_id for item in review.excerpts}
        for candidate in acoustic.report.candidates:
            if candidate.id in ignored:
                continue
            left, right = recordings[candidate.left_excerpt_id], recordings[candidate.right_excerpt_id]
            a, b = root(left), root(right)
            parent[max(a, b)] = min(a, b)
            # These are split-control edges, never evidence of equal rights or
            # equal recording identity; rights propagation excludes this key.
            for identifier in (left, right):
                identities_by_id[identifier].append(('acoustic', candidate.id))
            acoustic_edges += 1
    elif acoustic is not None:
        raise ValueError('regional_dataset_source_changed')
    groups: dict[str, list[RegionalRecordingReview]] = {}
    for item in review.recordings:
        groups.setdefault(root(item.id), []).append(item)
    cluster_hashes = {key: hashlib.sha256(','.join(sorted({name + ':' + value for item in members
        for name, value in identities_by_id[item.id]})).encode('ascii')).hexdigest() for key, members in groups.items()}
    excluded: dict[str, Literal['rights_unknown', 'rights_conflict', 'restricted_license', 'caption_unreviewed']] = {}
    for item in review.recordings:
        reference, conflict_reference = _permission(item, 'reference')
        training, conflict_training = _permission(item, 'training')
        if any(value.restriction in ('noncommercial', 'no_derivatives') for value in item.rights):
            excluded[item.id] = 'restricted_license'
        elif conflict_reference or not item.evaluation_only and conflict_training:
            excluded[item.id] = 'rights_conflict'
        elif not reference or not item.evaluation_only and not training:
            excluded[item.id] = 'rights_unknown'
        elif not item.captions_reviewed:
            excluded[item.id] = 'caption_unreviewed'
    blocked_reference = {identity for item in review.recordings
        if excluded.get(item.id) == 'restricted_license' or _permission(item, 'reference')[1]
            or any(value.permissions.reference == 'blocked' for value in item.rights)
        for identity in identities_by_id[item.id] if identity[0] in ('file', 'pcm', 'original')}
    blocked_training = {identity for item in review.recordings
        if _permission(item, 'training')[1] or any(value.permissions.training == 'blocked' for value in item.rights)
        for identity in identities_by_id[item.id] if identity[0] in ('file', 'pcm', 'original')}
    for item in review.recordings:
        if item.id not in excluded and any(identity in blocked_reference or not item.evaluation_only and identity in blocked_training for identity in identities_by_id[item.id]):
            excluded[item.id] = 'rights_conflict'
    eligible = {key: [item for item in members if item.id not in excluded] for key, members in groups.items()}
    eligible = {key: members for key, members in eligible.items() if members}
    forced = {key for key in eligible if any(item.evaluation_only for item in groups[key])}
    ordered = sorted(eligible, key=lambda key: hashlib.sha256(f'{review.split_seed}:{cluster_hashes[key]}'.encode('ascii')).hexdigest())
    desired = min(len(eligible) - 1, max(1, math.ceil(len(eligible) * .15))) if len(eligible) > 1 else 0
    holdout = set(forced)
    strata_by_group = {key: {(item.instrument.strip().casefold(), item.technique.strip().casefold()) for item in members
        if item.instrument.strip() and item.technique.strip()} for key, members in eligible.items()}
    stratum_groups: dict[tuple[str, str], set[str]] = {}
    for key, strata in strata_by_group.items():
        for stratum in strata:
            stratum_groups.setdefault(stratum, set()).add(key)
    while len(holdout) < desired:
        remaining = [key for key in ordered if key not in holdout]
        safe = [key for key in remaining if all(stratum_groups[stratum] - holdout - {key} for stratum in strata_by_group[key])]
        represented = {stratum for key in holdout for stratum in strata_by_group[key]}
        choice = min(safe or remaining, key=lambda key: (-len(strata_by_group[key] - represented), ordered.index(key)))
        holdout.add(choice)
    rows = [RegionalSplitRecording(recording_id=item.id, cluster_sha256=cluster_hashes[root(item.id)],
        split='excluded' if item.id in excluded else 'holdout' if root(item.id) in holdout else 'train',
        exclusion_reason=excluded.get(item.id), adapter_distribution_permitted=_permission(item, 'adapter_distribution')[0],
        source_redistribution_permitted=_permission(item, 'source_redistribution')[0]) for item in review.recordings]
    train_count = sum(item.split == 'train' for item in rows)
    holdout_count = sum(item.split == 'holdout' for item in rows)
    provenance = all(item.original_recording_sha256 is not None and item.performer_group_sha256 is not None
        and item.session_group_sha256 is not None for item in review.recordings if item.id not in excluded)
    sufficient = len(eligible) >= 2 and train_count > 0 and holdout_count > 0 and provenance
    warnings: list[RegionalDatasetWarning] = ['cultural_labels_user_declared',
        'rights_declaration_unverified', 'holdout_fraction_approximate']
    acoustic_status: Literal['legacy_unscanned', 'screened', 'insufficient_information'] = 'legacy_unscanned'
    if acoustic is None:
        warnings.insert(0, 'near_copy_detection_unavailable')
    else:
        acoustic_status = 'screened'
        warnings.insert(0, 'acoustic_screening_limited')
        if acoustic_edges:
            warnings.append('acoustic_candidates_grouped')
        if any(item.coverage != 'screened' for item in acoustic.report.excerpts):
            acoustic_status = 'insufficient_information'; sufficient = False
            warnings.append('acoustic_information_insufficient')
    if (any(not item.instrument.strip() or not item.technique.strip() for members in eligible.values() for item in members)
            or any(not group.intersection(holdout) or not group.difference(holdout) for group in stratum_groups.values())):
        warnings.append('stratification_incomplete')
    if not provenance:
        warnings.append('provenance_incomplete')
    if not sufficient:
        warnings.append('independent_groups_insufficient')
    if any(not item.adapter_distribution_permitted for item in rows if item.split != 'excluded'):
        warnings.append('distribution_permission_missing')
    if any(not item.source_redistribution_permitted for item in rows if item.split != 'excluded'):
        warnings.append('source_redistribution_not_permitted')
    return RegionalSplitPlan(recordings=rows, independent_groups=len(eligible), training_recordings=train_count,
        holdout_recordings=holdout_count, evaluation_sufficient=sufficient, warnings=warnings, acoustic_status=acoustic_status)


def acoustic_selection_sha256(review: RegionalDatasetReviewRequest) -> str:
    """Caption/rights edits may reuse evidence; source IDs/hashes/spans may not."""
    selected = {'dataset_id': review.dataset_id, 'dataset_revision': review.dataset_revision,
        'recordings': [{'id': item.id, 'source': item.source.model_dump(mode='json'), 'sha256': item.source_sha256}
            for item in sorted(review.recordings, key=lambda item: item.id)],
        'excerpts': [item.model_dump(mode='json') for item in sorted(review.excerpts, key=lambda item: item.id)]}
    return hashlib.sha256(json.dumps(selected, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _check_acoustic_selection(review: RegionalDatasetReviewRequest, report: RegionalAcousticReport,
                              inspections: Sequence[RegionalRecordingInspection] | None = None) -> None:
    validate_report(report)
    if report.selection_sha256 != acoustic_selection_sha256(review):
        raise ValueError('regional_dataset_source_changed')
    excerpts = {item.excerpt_id: item for item in report.excerpts}
    recordings = {item.id: item for item in review.recordings}
    facts = {item.recording_id: item for item in inspections} if inspections is not None else None
    if set(excerpts) != {item.id for item in review.excerpts}:
        raise ValueError('regional_dataset_source_changed')
    for item in review.excerpts:
        evidence = excerpts[item.id]
        if (evidence.recording_id != item.recording_id or evidence.source_sha256 != recordings[item.recording_id].source_sha256
                or evidence.start_frame != item.start_frame or evidence.end_frame != item.end_frame):
            raise ValueError('regional_dataset_source_changed')
        if facts is not None:
            actual = facts[item.recording_id]
            if (evidence.source_sha256, evidence.decoded_pcm_sha256, evidence.sample_rate_hz, evidence.channels) != (
                    actual.source_sha256, actual.decoded_pcm_sha256, actual.sample_rate_hz, actual.channels):
                raise ValueError('regional_dataset_source_changed')


def _acoustic_evidence(review: RegionalDatasetReviewRequest,
                       inspections: Sequence[RegionalRecordingInspection] | None = None) -> RegionalAcousticResult | None:
    selection = review.acoustic_review
    if selection is None:
        return None
    job = get_acoustic_scan(selection.scan_id)
    if (job.status != 'done' or job.result is None or job.result.sha256 != selection.scan_sha256
            or job.request.dataset_id != review.dataset_id):
        raise RegionalDatasetError('regional_dataset_source_changed')
    try:
        _check_acoustic_selection(review, job.result.report, inspections)
        if any(item.candidate_id not in {candidate.id for candidate in job.result.report.candidates} for item in selection.decisions):
            raise ValueError('regional_dataset_source_changed')
        candidates = {item.id: item for item in job.result.report.candidates}
        if any(item.decision == 'distinct_after_review' and (candidates[item.candidate_id].left_audition is None
                or candidates[item.candidate_id].right_audition is None) for item in selection.decisions):
            raise ValueError('regional_dataset_source_changed')
    except ValueError as error:
        raise RegionalDatasetError('regional_dataset_source_changed') from error
    return job.result


def get_acoustic_scan(identifier: str) -> RegionalAcousticJob:
    return store.get_acoustic_scan(db.get_db(), identifier)


def list_acoustic_scans(dataset_id: str | None = None) -> RegionalAcousticJobs:
    return RegionalAcousticJobs(jobs=[get_acoustic_scan(identifier)
        for identifier in store.acoustic_scan_ids(db.get_db(), dataset_id)])


def _scan_selection(request: RegionalAcousticScanRequest) -> RegionalManifestSelection:
    return RegionalManifestSelection(dataset_id=request.dataset_id, review_revision=request.review_revision, review_sha256=request.review_sha256)


async def _stop_acoustic_identity(identifier: str) -> bool:
    try:
        process = _acoustic_processes.get(identifier)
        identity = store.acoustic_identity(db.get_db(), identifier)
        job = get_acoustic_scan(identifier)
        if process is None and identity is None and (job.status in ('queued', 'running')
                or job.error_code == 'regional_dataset_worker_unverified'):
            # Spawn cleanup may fail before its identity callback is persisted.
            receipt = _workspace(identifier) / 'acoustic-worker.json'
            if receipt.exists() or receipt.is_symlink():
                if receipt.is_symlink() or receipt.resolve() != receipt or receipt.stat().st_size > 65536:
                    raise RegionalDatasetError('regional_dataset_worker_unverified')
                with receipt.open('rb') as stream:
                    content = stream.read(65537)
                if len(content) > 65536:
                    raise RegionalDatasetError('regional_dataset_worker_unverified')
                recovered = WorkerReceipt.model_validate_json(content)
                identity = AcousticProcessIdentity(pid=recovered.pid, token=recovered.token)
                store.set_acoustic_identity(db.get_db(), identifier, identity)
        if process is not None:
            await await_cleanup(kill_process_tree(process))
        elif identity is not None:
            worker = WorkerIdentity(pid=identity.pid, token=identity.token, receipt=str(_workspace(identifier) / 'acoustic-worker.json'))
            if not await terminate_verified(worker):
                raise RegionalDatasetError('regional_dataset_worker_unverified')
        store.set_acoustic_identity(db.get_db(), identifier, None)
        _acoustic_processes.pop(identifier, None); _acoustic_unverified.discard(identifier)
        return True
    except (OSError, RuntimeError, ValueError, RegionalDatasetError, asyncio.CancelledError):
        _acoustic_unverified.add(identifier)
        return False


async def _execute_acoustic_worker(identifier: str, request: AcousticWorkerRequest) -> RegionalAcousticReport:
    workspace = _workspace(identifier, create=True)
    request_path, result_path = workspace / 'acoustic-request.json', workspace / 'acoustic-result.json'
    if any(path.exists() or path.is_symlink() for path in (request_path, result_path, workspace / 'acoustic-worker.json')):
        raise RegionalDatasetError('regional_dataset_source_changed')
    await run_owned_thread(write_object, request_path, request.model_dump(mode='json'))

    def registered(identity: WorkerIdentity) -> None:
        store.set_acoustic_identity(db.get_db(), identifier, AcousticProcessIdentity(pid=identity.pid, token=identity.token))

    process = await spawn_owned([sys.executable, '-I', str(Path(__file__).resolve().parents[1] / 'scripts/regional_acoustic_worker.py'),
        '--request', str(request_path), '--result', str(result_path)], receipt_path=workspace / 'acoustic-worker.json',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, on_identity=registered)
    _acoustic_processes[identifier] = process
    try:
        await read_owned_output(process, max_bytes=65536, timeout=915)
        if process.returncode != 0:
            raise RegionalDatasetError('regional_dataset_invalid')
    finally:
        if not await _stop_acoustic_identity(identifier):
            raise RegionalDatasetError('regional_dataset_worker_unverified')

    def read_result() -> AcousticWorkerOutcome:
        if (result_path.is_symlink() or result_path.resolve() != result_path or not result_path.is_file()
                or result_path.stat().st_size > 8 * 1024**2):
            raise RegionalDatasetError('regional_dataset_limit')
        with result_path.open('rb') as stream:
            content = stream.read(8 * 1024**2 + 1)
        if len(content) > 8 * 1024**2:
            raise RegionalDatasetError('regional_dataset_limit')
        return AcousticWorkerOutcome.model_validate_json(content)

    outcome = await run_owned_thread(read_result)
    if outcome.error_code is not None:
        raise RegionalDatasetError(outcome.error_code)
    if outcome.report is None:
        raise RegionalDatasetError('regional_dataset_invalid')
    return outcome.report


async def _run_acoustic_scan(identifier: str) -> None:
    try:
        async with lora_datasets.native_lock:
            job = get_acoustic_scan(identifier)
            review = _selected_review(_scan_selection(job.request))
            inspections = await _inspections(review)
            # Validate exact existing grouping and all crop bounds before decode.
            split_recordings(review.request.model_copy(update={'acoustic_review': None}), inspections)
            free = available_bytes(db.FILES_DIR)
            if free is None or free < _RESERVE + AUDITION_BYTES:
                raise RegionalDatasetError('insufficient_disk')
            facts = {item.recording_id: item for item in inspections}
            sources: list[AcousticWorkerSource] = []
            for excerpt in review.request.excerpts:
                recording = next(item for item in review.request.recordings if item.id == excerpt.recording_id)
                info = facts[recording.id]
                path = await _source_path(RegionalDatasetInspectionRequest(recording_id=recording.id, source=recording.source))
                sources.append(AcousticWorkerSource(source_path=str(path), source_frames=info.frames,
                    excerpt=RegionalAcousticExcerpt(excerpt_id=excerpt.id, recording_id=recording.id,
                        source_sha256=info.source_sha256, decoded_pcm_sha256=info.decoded_pcm_sha256,
                        sample_rate_hz=info.sample_rate_hz, channels=info.channels, start_frame=excerpt.start_frame,
                        end_frame=excerpt.end_frame, coverage='insufficient_information', landmark_count=0)))
            request = AcousticWorkerRequest(selection_sha256=acoustic_selection_sha256(review.request),
                configuration_sha256=configuration_sha256(), sources=sources)
            store.update_acoustic_scan(db.get_db(), job.model_copy(update={'status': 'running'}))
            report = await _execute_acoustic_worker(identifier, request)
            _check_acoustic_selection(review.request, report, await _inspections(review))
            _selected_review(_scan_selection(job.request))
            result = RegionalAcousticResult(sha256=store.canonical_sha256(report), report=report)
            store.update_acoustic_scan(db.get_db(), job.model_copy(update={'status': 'done', 'result': result}))
    except asyncio.CancelledError:
        job = get_acoustic_scan(identifier)
        stopped = identifier not in _acoustic_unverified and await _stop_acoustic_identity(identifier)
        store.update_acoustic_scan(db.get_db(), job.model_copy(update={'status': 'cancelled' if stopped else 'failed',
            'error_code': 'regional_dataset_cancelled' if stopped else 'regional_dataset_worker_unverified'}))
        raise
    except Exception as error:
        logger.warning('Regional acoustic scan retained after failure', exc_info=True)
        job = get_acoustic_scan(identifier)
        stopped = identifier not in _acoustic_unverified and await _stop_acoustic_identity(identifier)
        code = 'regional_dataset_worker_unverified' if not stopped else error.code if isinstance(error, RegionalDatasetError) else 'regional_dataset_limit' if isinstance(error, TimeoutError) else 'regional_dataset_source_changed' if isinstance(error, (ValueError, VideoProjectError)) else 'regional_dataset_storage_unavailable'
        store.update_acoustic_scan(db.get_db(), job.model_copy(update={'status': 'failed', 'error_code': code}))
    finally:
        _tasks.pop(identifier, None); _acoustic_ids.discard(identifier)


async def start_acoustic_scan(request: RegionalAcousticScanRequest) -> RegionalAcousticJob:
    from .resource_admission import admission_lock, require_setup_idle
    async with admission_lock:
        async with _lock:
            previous = db.get_db().execute('SELECT id FROM regional_acoustic_scans WHERE submission_key=?', (request.submission_key,)).fetchone()
            if previous is not None:
                return store.create_acoustic_scan(db.get_db(), request)[0]
            require_setup_idle()
            if _closing or _tasks or _acoustic_unverified:
                raise RegionalDatasetError('regional_dataset_busy')
            job, created = store.create_acoustic_scan(db.get_db(), request)
            if created:
                _acoustic_ids.add(job.id)
                _tasks[job.id] = asyncio.create_task(_run_acoustic_scan(job.id))
            return job


async def cancel_acoustic_scan(identifier: str) -> RegionalAcousticJob:
    get_acoustic_scan(identifier)
    task = _tasks.get(identifier)
    await cancel_and_wait(task)
    stopped = await _stop_acoustic_identity(identifier)
    if task is not None and task.done():
        _tasks.pop(identifier, None); _acoustic_ids.discard(identifier)
    job = get_acoustic_scan(identifier)
    if job.status in ('queued', 'running') or job.error_code == 'regional_dataset_worker_unverified' and stopped:
        store.update_acoustic_scan(db.get_db(), job.model_copy(update={'status': 'cancelled' if stopped else 'failed',
            'error_code': 'regional_dataset_cancelled' if stopped else 'regional_dataset_worker_unverified'}), verified_stop=stopped)
    return get_acoustic_scan(identifier)


async def acoustic_audition(identifier: str, candidate_id: str, side: Literal['left', 'right']) -> Path:
    job = get_acoustic_scan(identifier)
    if job.status != 'done' or job.result is None:
        raise RegionalDatasetError('regional_dataset_not_found', 404)
    candidate = next((item for item in job.result.report.candidates if item.id == candidate_id), None)
    if candidate is None:
        raise RegionalDatasetError('regional_dataset_not_found', 404)
    artifact = candidate.left_audition if side == 'left' else candidate.right_audition
    if artifact is None:
        raise RegionalDatasetError('regional_dataset_not_found', 404)
    root = _workspace(identifier)
    directory = root / 'auditions'
    path = directory / (candidate.id + '-' + side + '.wav')
    if (directory.is_symlink() or directory.resolve() != directory or path.is_symlink()
            or path.resolve() != path or not path.is_file() or path.stat().st_size != artifact.bytes):
        raise RegionalDatasetError('regional_dataset_source_changed')
    try:
        before = _signature(path)
        if await hash_file(path, max_bytes=AUDITION_BYTES) != artifact.sha256:
            raise RegionalDatasetError('regional_dataset_source_changed')
        facts = await run_owned_thread(sf.info, path)
        if (facts.samplerate, facts.channels, facts.frames, facts.subtype) != (RATE, artifact.channels, artifact.frames, 'FLOAT') or before != _signature(path):
            raise RegionalDatasetError('regional_dataset_source_changed')
    except (OSError, VideoProjectError) as error:
        raise RegionalDatasetError('regional_dataset_source_changed') from error
    return path


async def _verify_acoustic_distinct(review: RegionalDatasetReviewRequest) -> None:
    selection = review.acoustic_review
    if selection is not None:
        for decision in selection.decisions:
            if decision.decision == 'distinct_after_review':
                await acoustic_audition(selection.scan_id, decision.candidate_id, 'left')
                await acoustic_audition(selection.scan_id, decision.candidate_id, 'right')
