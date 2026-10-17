"""Reviewed beat/song plans and caller-owned durable rendering.

Manual anchors and role choices remain declarations. No inferred beat grid,
instrument detector, musical compatibility score or cleared-rights assertion.
"""
from __future__ import annotations

from datetime import datetime, timezone
import asyncio
from collections.abc import Mapping
import errno
import hashlib
import logging
import math
import os
from pathlib import Path
import shutil
import sqlite3
import uuid

from pydantic import BaseModel, ValidationError

from . import audio_versions, beat_projects, beat_render, beat_song_mix_store as store, beat_song_render as renderer, db, music_dsp_runtime, song_sources
from .beat_contracts import BeatDocument, BeatError, BeatInstrumentManifest, canonical_json
from .beat_project_contracts import MusicProjectProvenance, MusicProjectPublication, MusicProjectSource, RenderedProjectLane
from .beat_song_mix_contracts import (BeatSongLane, BeatSongLaneControl, BeatSongMixDraft, BeatSongMixError,
    BeatSongMixPlan, BeatSongMixPreview, BeatSongMixUpdate, BeatSongMixStartRequest, BeatSongMixJob,
    BeatSongMixProgress, BeatSongMixFailure, BeatSongMixManifest, BeatSongMixArtifact,
    BeatSongTransposeRequest, BeatSongTransposePreview, BeatSongSymbolicOrigin, MixWarning, SongSourceInfo)
from .contracts import JsonObject, JsonValue
from .export_provenance_contracts import ContentOrigin, ProvenanceComponent
from .job_lifecycle import await_cleanup, cancel_and_wait, kill_process_tree, run_owned_thread
from .music_dsp import MusicDspError
from .music_evidence import serialize_music_evidence
from .stems import separate_file
from .video_process import WorkerIdentity, drain_receipt, read_owned_output, spawn_owned, terminate_verified
from .voice_separation import OfflineDemucs, VoiceSeparationError, offline_demucs

logger = logging.getLogger(__name__)
_tasks: dict[str, asyncio.Task[None]] = {}
_processes: dict[str, asyncio.subprocess.Process] = {}
_unverified: dict[str, WorkerIdentity] = {}
_spawn_quarantine: set[str] = set()
_lock = asyncio.Lock()
_semaphore = asyncio.Semaphore(1)
_closing = False
_retaining = 0


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def musical_hash(document: BeatDocument) -> str:
    return hashlib.sha256(canonical_json(document.model_copy(update={'id': '0' * 32, 'revision': 0, 'title': 'musical identity'}))).hexdigest()


def transpose_document(document: BeatDocument, semitones: int, *, manifest: BeatInstrumentManifest | None = None) -> BeatDocument:
    if type(semitones) is not int or not -12 <= semitones <= 12:
        raise BeatSongMixError('beat_mix_key_review_required')
    instruments = {} if manifest is None else {item.instrument_id: item for item in manifest.instruments}
    value = document.model_dump()
    patterns = []
    for pattern in document.patterns:
        lanes = []
        for lane in pattern.lanes:
            steps = []
            for step in lane.steps:
                changes: dict[str, object] = {}
                if lane.role != 'drum' and semitones:
                    instrument = instruments.get(lane.instrument_id)
                    note = step.note_midi if step.note_midi is not None else None if instrument is None else instrument.midi_note
                    if note is None or not 0 <= note + semitones <= 127:
                        raise BeatSongMixError('beat_mix_key_review_required')
                    changes['note_midi'] = note + semitones
                steps.append({**step.model_dump(), **changes})
            lanes.append({**lane.model_dump(), 'steps': steps})
        patterns.append({**pattern.model_dump(), 'lanes': lanes})
    value['patterns'] = patterns
    if document.scale is not None:
        root = document.scale.root_note_midi + semitones
        if not 0 <= root <= 127:
            raise BeatSongMixError('beat_mix_key_review_required')
        value['scale'] = {**document.scale.model_dump(), 'root_note_midi': root}
    return BeatDocument.model_validate(value)


def expanded_document(document: BeatDocument, repeats: int) -> BeatDocument:
    """Recompile the entire chain so cross-occurrence chokes remain meaningful."""
    if not 1 <= repeats <= 128 or len(document.chain) * repeats > 128:
        raise BeatSongMixError('beat_mix_budget_exceeded')
    try:
        return BeatDocument.model_validate({**document.model_dump(), 'chain': [
            placement.model_dump() for _ in range(repeats) for placement in document.chain]})
    except ValueError as error:
        raise BeatSongMixError('beat_mix_budget_exceeded') from error


def lane_id(side: str, identity: str) -> str:
    return hashlib.sha256((side + ':' + identity).encode('utf-8')).hexdigest()[:32]


def plan_digest(plan: BeatSongMixPlan) -> str:
    return hashlib.sha256(canonical_json(plan.model_copy(update={'sha256': '0' * 64}))).hexdigest()


def build_plan(draft: BeatSongMixDraft, beat: SongSourceInfo, song: SongSourceInfo, *, plan_id: str,
               revision: int, separator_ready: bool, native_ready: bool,
               symbolic_original: BeatDocument | None = None,
               symbolic_manifest: BeatInstrumentManifest | None = None) -> BeatSongMixPlan:
    for requested, observed in ((draft.beat, beat), (draft.song, song)):
        if requested.source != observed.source or requested.sha256 != observed.sha256:
            raise BeatSongMixError('song_source_changed')
        if requested.excerpt_end_sec > observed.duration_sec + 1 / observed.sample_rate_hz:
            raise BeatSongMixError('song_conditioning_invalid')
    if song.source.kind != 'version':
        raise BeatSongMixError('song_conditioning_invalid')
    if beat.beat_document is not None:
        if draft.beat.excerpt_start_sec != 0 or beat.musical_duration_sec is None or draft.beat.excerpt_end_sec < beat.musical_duration_sec - 1 / 48000:
            raise BeatSongMixError('beat_mix_alignment_invalid')
        expanded_document(beat.beat_document, draft.beat_repeats)
        beat_duration = beat.musical_duration_sec * draft.beat_repeats
    else:
        beat_duration = (draft.beat.excerpt_end_sec - draft.beat.excerpt_start_sec) * draft.beat_repeats
        if (draft.beat_repeats != 1 or draft.audio_pitch_semitones != 0) and not draft.audio_approximation:
            raise BeatSongMixError('beat_mix_key_review_required')
    if beat.beat_document is not None and draft.audio_pitch_semitones != 0:
        raise BeatSongMixError('beat_mix_key_review_required')
    if draft.symbolic_origin is not None:
        origin = draft.symbolic_origin
        if symbolic_original is None or beat.beat_document is None:
            raise BeatSongMixError('beat_mix_key_review_required')
        expected = transpose_document(symbolic_original, origin.semitones, manifest=symbolic_manifest)
        if musical_hash(expected) != origin.musical_sha256 or musical_hash(beat.beat_document) != origin.musical_sha256:
            raise BeatSongMixError('beat_mix_key_review_required')
    song_duration = draft.song.excerpt_end_sec - draft.song.excerpt_start_sec
    source_duration, target_duration = (beat_duration, song_duration) if draft.tempo_strategy == 'beat_to_song' else (song_duration, beat_duration)
    anchors = draft.anchors
    if (not math.isclose(anchors[-1].source_sec, source_duration, abs_tol=1 / 48000)
            or not math.isclose(anchors[-1].target_sec, target_duration, abs_tol=1 / 48000)
            or not 0 < target_duration <= 600 or not 0 < source_duration <= 600):
        raise BeatSongMixError('beat_mix_alignment_invalid')
    ratios = [(right.target_sec - left.target_sec) / (right.source_sec - left.source_sec)
        for left, right in zip(anchors, anchors[1:])]
    if any(not .75 - 1e-12 <= ratio <= 1.5 + 1e-12 for ratio in ratios):
        raise BeatSongMixError('beat_mix_alignment_invalid')
    available: list[BeatSongLane] = []
    controls = {item.lane_id: item for item in draft.controls}

    def append(side: str, identity: str, label: str, role: str, original_id: str | None = None) -> None:
        identifier = lane_id(side, identity)
        default = BeatSongLaneControl(lane_id=identifier, muted=side == 'song' and identity == 'drums' and draft.mode == 'replace_drums')
        available.append(BeatSongLane.model_validate({'id': identifier, 'label': label, 'source': side,
            'role': role, 'beat_lane_id': original_id, 'control': controls.get(identifier, default).model_dump()}))

    if beat.beat_document is None:
        append('beat', 'mix', 'Beat mix', 'mix')
    else:
        for authored_lane in beat.beat_document.patterns[0].lanes:
            append('beat', authored_lane.lane_id, authored_lane.label, authored_lane.role, authored_lane.lane_id)
    if draft.mode == 'replace_drums':
        for stem in ('vocals', 'drums', 'bass', 'other'):
            append('song', stem, 'Song ' + stem, 'drum' if stem == 'drums' else stem)
    else:
        append('song', 'mix', 'Song mix', 'mix')
    by_id = {item.id: item for item in available}
    if len(available) > 32 or set(controls) - by_id.keys():
        raise BeatSongMixError('beat_mix_invalid')
    # Detect only actual isolated, operator-selected percussion. A full mix is
    # never relabelled as a kick detector. DSP validates cycles as well.
    for lane in available:
        if draft.mode == 'replace_drums' and lane.source == 'song' and lane.role == 'drum' and not lane.control.muted:
            raise BeatSongMixError('beat_mix_drums_required')
        visited = {lane.id}
        current = lane
        while current.control.duck is not None:
            detector = by_id.get(current.control.duck.sidechain_input_id)
            if detector is None or detector.role != 'drum' or detector.id in visited:
                raise BeatSongMixError('beat_mix_invalid')
            visited.add(detector.id)
            current = detector
    warnings: list[MixWarning] = ['manual_anchors', 'rights_unknown', 'processed_stems_before_master']
    if draft.key_review == 'unknown':
        warnings.append('key_unknown')
    if draft.mode == 'replace_drums':
        warnings.append('separation_bleed_possible')
    if draft.audio_pitch_semitones:
        warnings.append('audio_pitch_approximation')
    if draft.beat_repeats > 1:
        warnings.append('authored_repeats_recompiled' if beat.beat_document is not None else 'audio_loop_approximation')
    if beat.beat_document is not None:
        warnings.append('tails_trimmed_to_song')
    needs_native = draft.audio_pitch_semitones != 0 or any(abs(ratio - 1) > 1e-9 for ratio in ratios)
    if needs_native and source_duration > 30 and max(ratios) - min(ratios) > 1e-9:
        context = float(draft.project_meter.quarters_per_bar) * 60 / draft.project_bpm
        if beat.beat_document is not None:
            context = max(context, max(float(pattern.meter.quarters_per_bar) * 60 / beat.beat_document.bpm
                for pattern in beat.beat_document.patterns) * (max(ratios) if draft.tempo_strategy == 'beat_to_song' else 1))
        renderer.warp_windows(anchors, context)
        warnings.append('chunked_warp_review')
    plan = BeatSongMixPlan(id=plan_id, revision=revision, draft=draft, beat=beat, song=song, lanes=available,
        source_duration_sec=source_duration, target_duration_sec=target_duration,
        minimum_ratio=max(.75, min(ratios)), maximum_ratio=min(1.5, max(ratios)), needs_native=needs_native,
        separator_ready=separator_ready, native_ready=native_ready, warnings=warnings, sha256='0' * 64, created_at=now())
    return plan.model_copy(update={'sha256': plan_digest(plan)})


def _checksum(value: BaseModel) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


async def transpose_preview(request: BeatSongTransposeRequest) -> BeatSongTransposePreview:
    info = await song_sources.inspect_source(request.source, expected_sha256=request.source.sha256)
    document = info.beat_document
    if document is None:
        raise BeatSongMixError('beat_mix_key_review_required')
    kit = beat_render.load_kit(document.kit_id, document.kit_manifest_sha256)
    candidate = transpose_document(document, request.semitones, manifest=kit)
    # This remains a candidate: existing createBeat assigns its fresh identity.
    return BeatSongTransposePreview(document=candidate, symbolic_origin=BeatSongSymbolicOrigin(
        original=request.source, semitones=request.semitones, musical_sha256=musical_hash(candidate)))


async def _preview(draft: BeatSongMixDraft, plan_id: str, revision: int) -> BeatSongMixPreview:
    beat = await song_sources.inspect_source(draft.beat.source, expected_sha256=draft.beat.sha256)
    song = await song_sources.inspect_source(draft.song.source, expected_sha256=draft.song.sha256)
    ready = None
    if draft.mode == 'replace_drums':
        try:
            ready = await run_owned_thread(offline_demucs, 'fast')
        except VoiceSeparationError:
            pass
    original = None
    kit = None
    if draft.symbolic_origin is not None:
        original = (await song_sources.inspect_source(draft.symbolic_origin.original)).beat_document
        if original is not None:
            kit = beat_render.load_kit(original.kit_id, original.kit_manifest_sha256)
    native = await run_owned_thread(music_dsp_runtime.native)
    plan = build_plan(draft, beat, song, plan_id=plan_id, revision=revision, separator_ready=ready is not None,
        native_ready=native is not None, symbolic_original=original, symbolic_manifest=kit)
    storage = await run_owned_thread(renderer.storage, plan, model_bytes=0 if ready is None else ready.model_bytes)
    return BeatSongMixPreview(plan=plan, storage=storage)


async def preview(draft: BeatSongMixDraft) -> BeatSongMixPreview:
    result = await _preview(draft, uuid.uuid4().hex, 0)
    store.save_plan(result.plan)
    return result


async def update_plan(plan_id: str, request: BeatSongMixUpdate) -> BeatSongMixPreview:
    current = store.get_plan(plan_id)
    if current.revision != request.expected_revision:
        raise BeatSongMixError('beat_mix_revision_conflict')
    result = await _preview(request.draft, plan_id, request.expected_revision + 1)
    store.save_plan(result.plan, expected_revision=request.expected_revision)
    return result


def get_job(job_id: str) -> BeatSongMixJob:
    return store.get_job(job_id).job


def list_jobs() -> list[BeatSongMixJob]:
    return store.list_jobs()


def _change(job_id: str, **changes: object) -> BeatSongMixJob:
    return store.save_job(BeatSongMixJob.model_validate({**get_job(job_id).model_dump(), **changes}))


def _admit() -> None:
    if _closing or _unverified or _spawn_quarantine or len(_tasks) >= 8:
        raise BeatSongMixError('beat_mix_busy')


def _start_task(job_id: str) -> None:
    task = asyncio.create_task(_run(job_id), name='beat-song:' + job_id)
    _tasks[job_id] = task
    task.add_done_callback(lambda finished: _tasks.pop(job_id, None) if _tasks.get(job_id) is finished else None)


async def start(request: BeatSongMixStartRequest) -> BeatSongMixJob:
    global _retaining
    _retaining += 1
    try:
        async with _lock:
            previous = store.submission(request.submission_key, _checksum(request))
            if previous is not None:
                return previous.job
            _admit()
            plan = store.get_plan(request.plan_id)
            if plan.revision != request.expected_revision or plan.sha256 != request.plan_sha256 or plan_digest(plan) != plan.sha256:
                raise BeatSongMixError('beat_mix_revision_conflict')
            if request.preview_end_sec is not None and request.preview_end_sec > plan.target_duration_sec:
                raise BeatSongMixError('beat_mix_alignment_invalid')
            ready = None
            if plan.draft.mode == 'replace_drums':
                try:
                    ready = await run_owned_thread(offline_demucs, 'fast')
                except VoiceSeparationError as error:
                    raise BeatSongMixError('beat_mix_drums_required') from error
            if plan.needs_native and await run_owned_thread(music_dsp_runtime.native) is None:
                raise BeatSongMixError('beat_mix_stretch_required')
            forecast = await run_owned_thread(renderer.storage, plan, model_bytes=0 if ready is None else ready.model_bytes)
            if not forecast.can_start:
                raise BeatSongMixError('insufficient_disk')
            job_id = uuid.uuid4().hex
            root = song_sources.owned_root(job_id, create=True)
            try:
                snapshots = [await song_sources.retain_source(item.source, root, source_id=uuid.uuid4().hex,
                    expected_sha256=item.sha256) for item in (plan.draft.beat, plan.draft.song)]
                # Facts/evidence were captured at plan review. Identity changes
                # require a new review; never silently upgrade a reviewed plan.
                for snapshot, reviewed in zip(snapshots, (plan.beat, plan.song)):
                    if SongSourceInfo.model_validate(snapshot.model_dump(exclude={'source_id', 'raw_filename'})) != reviewed:
                        raise BeatSongMixError('song_source_changed')
                await renderer.retain_kit(plan, root)
                renderer.job_dir(job_id, create=True)
                job = BeatSongMixJob(id=job_id, plan=plan, request=request, status='queued',
                    progress=BeatSongMixProgress(phase='queued'), created_at=now())
                stored = store.insert_job(job, snapshots, _checksum(request))
            except BaseException:
                await run_owned_thread(shutil.rmtree, root)
                raise
            if stored.id != job_id:
                await run_owned_thread(shutil.rmtree, root)
                return stored
            _start_task(job.id)
            return job
    finally:
        _retaining -= 1


async def _drain(job_id: str, process: asyncio.subprocess.Process) -> None:
    owned = store.get_job(job_id).identity
    try:
        await await_cleanup(kill_process_tree(process))
        if process.returncode is None:
            raise OSError('process_exit_unverified')
    except BaseException:
        identity = store.get_job(job_id).identity
        if identity is not None:
            _unverified[job_id] = identity
        raise
    else:
        if process.stdin is not None:
            process.stdin.close()
        _processes.pop(job_id, None)
        _unverified.pop(job_id, None)
        store.identity(job_id, None)
        if owned is not None and _valid_receipt(job_id, owned):
            await run_owned_thread(shutil.rmtree, Path(owned.receipt).parent)


async def _spawn(job_id: str, argv: list[str], *, cwd: Path | None = None,
                 stdout: int = asyncio.subprocess.PIPE) -> asyncio.subprocess.Process:
    folder = renderer.job_dir(job_id, create=True) / 'commands' / uuid.uuid4().hex
    folder.mkdir(parents=True)
    try:
        process = await spawn_owned(argv, receipt_path=folder / 'worker.json', cwd=cwd,
            env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1'},
            stdout=stdout, stderr=asyncio.subprocess.STDOUT, on_identity=lambda value: store.identity(job_id, value))
    except BaseException:
        identity = store.get_job(job_id).identity
        if identity is not None:
            _unverified[job_id] = identity
        else:
            _spawn_quarantine.add(job_id)
        raise
    _processes[job_id] = process
    return process


async def _command(job_id: str, argv: list[str], timeout: float) -> bytes:
    process = await _spawn(job_id, argv)
    try:
        output = await read_owned_output(process, max_bytes=2 * 1024**2, timeout=timeout)
        if process.returncode != 0:
            raise BeatSongMixError('beat_mix_render_failed')
        return output
    finally:
        await _drain(job_id, process)


async def _separate(job_id: str, audio: Path, output: Path) -> dict[str, Path]:
    ready: OfflineDemucs = await run_owned_thread(offline_demucs, 'fast')
    process: asyncio.subprocess.Process | None = None
    runtime = output.parent / ('separator-runtime-' + uuid.uuid4().hex)

    async def spawn(_argv: list[str], _cwd: Path, _env: Mapping[str, str], stdout: int) -> asyncio.subprocess.Process:
        nonlocal process
        argv = await run_owned_thread(ready.command, audio, runtime)
        argv[argv.index('-o') + 1] = str(output)
        process = await _spawn(job_id, argv, cwd=ready.engine, stdout=stdout)
        return process

    try:
        async with asyncio.timeout(3600):
            result = await separate_file(audio, output, quality='fast', log_name='beat-song-' + job_id,
                spawn=spawn, gpu_label='Beat/song drum replacement')
        if set(result) != {'vocals', 'drums', 'bass', 'other'}:
            raise BeatSongMixError('beat_mix_drums_required')
        return result
    finally:
        if process is not None:
            await _drain(job_id, process)


async def _execute(job_id: str) -> BeatSongMixManifest:
    record = store.get_job(job_id)
    plan = record.job.plan
    ready = await run_owned_thread(offline_demucs, 'fast') if plan.draft.mode == 'replace_drums' else None
    sample_bytes = 0
    if plan.beat.beat_document is not None:
        kit = renderer.retained_kit(plan, song_sources.owned_root(job_id))
        sample_bytes = sum(region.byte_size for instrument in kit.instruments for region in instrument.regions)
    forecast = await run_owned_thread(renderer.storage, plan, model_bytes=0 if ready is None else ready.model_bytes,
        sample_bytes=sample_bytes)
    if not forecast.can_start:
        raise BeatSongMixError('insufficient_disk')
    native = await run_owned_thread(music_dsp_runtime.native)

    async def command(argv: list[str], timeout: float) -> bytes:
        return await _command(job_id, argv, timeout)

    async def separate(audio: Path, output: Path) -> dict[str, Path]:
        _change(job_id, progress=BeatSongMixProgress(phase='separating').model_dump())
        return await _separate(job_id, audio, output)

    def progress(phase: str, completed: int, total: int) -> None:
        _change(job_id, progress={'phase': phase, 'completed': completed, 'total': total})

    return await renderer.render(record.job, record.sources, renderer.job_dir(job_id) / 'output', command,
        native=native, separate=separate, progress=progress)


def _matches(record: store.StoredJob, manifest: BeatSongMixManifest) -> None:
    from .music_evidence import merge_music_evidence, shareable_music_evidence
    job, plan = record.job, record.job.plan
    expected_evidence = shareable_music_evidence(merge_music_evidence([item.music_evidence for item in record.sources] +
        ([None] if plan.draft.mode == 'replace_drums' else [])))
    if (manifest.job_id != job.id or manifest.plan_id != plan.id or manifest.plan_revision != plan.revision
            or manifest.plan_sha256 != plan.sha256 or plan_digest(plan) != plan.sha256 or manifest.mode != job.request.mode
            or manifest.sources != record.sources or manifest.music_evidence != expected_evidence):
        raise BeatSongMixError('song_source_changed')
    duration = plan.target_duration_sec if job.request.mode == 'full' else (job.request.preview_end_sec or 0) - (job.request.preview_start_sec or 0)
    by_id = {item.id: item for item in manifest.artifacts}
    mixes = [item for item in manifest.artifacts if item.kind == 'mix']
    if (len(by_id) != len(manifest.artifacts) or len(mixes) != 1 or mixes[0].frames != manifest.frames
            or manifest.frames != round(duration * renderer.RATE) or manifest.metrics.source_sha256 != mixes[0].sha256
            or abs(manifest.metrics.duration_sec - duration) > 1 / renderer.RATE
            or {lane.lane_id for lane in manifest.lanes} != {lane.id for lane in plan.lanes}
            or len(manifest.lanes) != len(plan.lanes)):
        raise BeatSongMixError('song_source_changed')
    for lane in manifest.lanes:
        processed, dry = by_id.get(lane.artifact_id), by_id.get(lane.dry_artifact_id)
        if (processed is None or dry is None or processed.kind != 'processed' or dry.kind != 'dry'
                or processed.lane_id != lane.lane_id or dry.lane_id != lane.lane_id or processed.frames != manifest.frames):
            raise BeatSongMixError('song_source_changed')


def _origin(record: store.StoredJob) -> ContentOrigin:
    origins = {item.content_origin for item in record.sources}
    return 'unknown' if 'unknown' in origins else 'mixed' if len(origins) > 1 else record.sources[0].content_origin


async def _publish(job_id: str) -> BeatSongMixJob:
    record = store.get_job(job_id)
    if record.identity is not None or job_id in _processes or job_id in _unverified or job_id in _spawn_quarantine:
        raise BeatSongMixError('song_cleanup_failed')
    manifest = await run_owned_thread(renderer.validate_manifest, job_id)
    _matches(record, manifest)
    if record.job.request.mode == 'full':
        plan = record.job.plan
        mix = next(item for item in manifest.artifacts if item.kind == 'mix')
        path = renderer.contained(renderer.job_dir(job_id) / 'output', mix.filename)
        track_id = record.track_id
        if track_id is None:
            parents: list[JsonValue] = [item.track_id for item in record.sources]
            params: JsonObject = {'source': 'beat_song', 'beat_song_mix_job_id': job_id, 'beat_song_mix_plan_id': plan.id,
                'beat_song_mix_revision': plan.revision, 'beat_song_mix_plan_sha256': plan.sha256,
                'source_track_ids': parents, 'source_ancestry_complete': manifest.music_evidence.lineage_complete,
                '_music_evidence': serialize_music_evidence(manifest.music_evidence)}
            track_id = db.insert_track(model='beat_song', title=plan.draft.title, lyrics='', seed=None,
                duration_ms=manifest.frames * 1000 / renderer.RATE, wall_ms=None, params=params,
                audio_path=path, abc_path=None, beat_song_mix_job_id=job_id)
        audio_versions.retain_original(track_id, path)
        base = manifest.model_copy(update={'track_id': None, 'project_id': None})
        provenance = MusicProjectProvenance(workflow='beat_song', workflow_id=plan.id, workflow_revision=plan.revision,
            source_manifest_sha256=_checksum(base), sources=[MusicProjectSource(track_id=item.track_id,
                version_id=item.version_id, sha256=item.sha256) for item in record.sources])
        by_id = {item.id: item for item in manifest.artifacts}
        lanes = {item.id: item for item in plan.lanes}
        editor_lanes = [RenderedProjectLane(lane_id=item.lane_id, label=lanes[item.lane_id].label,
            artifact_id=item.artifact_id, sha256=by_id[item.artifact_id].sha256,
            source_url=f'/api/beat-song-mixes/{job_id}/artifacts/{item.artifact_id}', trim_end_sec=manifest.frames / renderer.RATE)
            for item in manifest.lanes]
        # Actual dry assets remain downloadable individually. Two muted dry
        # comparisons and the exact master fit the canonical 32-lane budget.
        for side in ('beat', 'song'):
            lane = next(item for item in plan.lanes if item.source == side)
            mapping = next(item for item in manifest.lanes if item.lane_id == lane.id)
            dry = by_id[mapping.dry_artifact_id]
            editor_lanes.append(RenderedProjectLane(lane_id='dry-' + side, label='Dry ' + lane.label,
                artifact_id=dry.id, sha256=dry.sha256, source_url=f'/api/beat-song-mixes/{job_id}/artifacts/{dry.id}',
                trim_end_sec=(dry.frames or 0) / renderer.RATE, muted=True))
        editor_lanes.append(RenderedProjectLane(lane_id='master-reference', label='Rendered master (reference)',
            artifact_id=mix.id, sha256=mix.sha256, source_url=f'/api/beat-song-mixes/{job_id}/artifacts/{mix.id}',
            trim_end_sec=manifest.frames / renderer.RATE, muted=True))
        built = beat_projects.build_audio_project(plan.draft.title, plan.draft.project_bpm, editor_lanes, provenance)
        project_id = beat_projects.publish_audio_project(built, 'beat-song-render:' + job_id)
        store.attach_project(job_id, project_id)
        manifest = manifest.model_copy(update={'track_id': track_id, 'project_id': project_id})
        await run_owned_thread(renderer.write_model, renderer.job_dir(job_id) / 'output' / 'manifest.json', manifest)
    return _change(job_id, status='done', manifest=manifest.model_dump(), error=None, finished_at=now(),
        progress=BeatSongMixProgress(phase='publishing', completed=1, total=1).model_dump())


def _failure(error: Exception) -> BeatSongMixFailure:
    if isinstance(error, BeatSongMixError):
        return BeatSongMixFailure(code=error.code)
    if isinstance(error, OSError) and error.errno == errno.ENOSPC:
        return BeatSongMixFailure(code='insufficient_disk')
    if isinstance(error, MusicDspError):
        return BeatSongMixFailure(code='insufficient_disk' if error.code == 'dsp_insufficient_disk' else
            'beat_mix_stretch_required' if error.code in ('dsp_stretch_unavailable', 'dsp_runtime_unverified') else 'beat_mix_render_failed')
    if isinstance(error, VoiceSeparationError):
        return BeatSongMixFailure(code='beat_mix_drums_required')
    return BeatSongMixFailure(code='beat_mix_render_failed')


async def _cleanup(job_id: str) -> None:
    record = store.get_job(job_id)
    if record.identity is not None or job_id in _processes or job_id in _unverified or job_id in _spawn_quarantine or record.job.status in ('saving', 'done'):
        return
    folder = renderer.job_dir(job_id)
    for item in folder.iterdir():
        if item.name == 'output' or item.name.startswith('dsp-'):
            if item.is_symlink() or item.resolve() != item:
                raise BeatSongMixError('song_cleanup_failed')
            await run_owned_thread(shutil.rmtree, item)


async def _run(job_id: str) -> None:
    try:
        async with _semaphore:
            _change(job_id, status='running', started_at=now())
            manifest = await _execute(job_id)
            _change(job_id, status='saving', manifest=manifest.model_dump(), progress=BeatSongMixProgress(phase='publishing').model_dump())
            await _publish(job_id)
    except asyncio.CancelledError:
        if job_id not in _unverified and job_id not in _spawn_quarantine:
            try:
                manifest = await run_owned_thread(renderer.validate_manifest, job_id)
                _matches(store.get_job(job_id), manifest)
            except (OSError, BeatSongMixError, ValidationError):
                pass
            else:
                _change(job_id, status='saving', manifest=manifest.model_dump(), error=None)
                return
        blocked = job_id in _unverified or job_id in _spawn_quarantine
        _change(job_id, status='blocked' if blocked else 'cancelled', finished_at=now(),
            error=BeatSongMixFailure(code='song_cleanup_failed').model_dump() if blocked else None)
        await _cleanup(job_id)
    except Exception as error:
        logger.exception('Beat/song render failed')
        identity = store.get_job(job_id).identity
        if identity is not None:
            _unverified[job_id] = identity
        status = 'blocked' if job_id in _unverified or job_id in _spawn_quarantine else 'saving' if get_job(job_id).status == 'saving' else 'failed'
        failure = BeatSongMixFailure(code='song_cleanup_failed') if status == 'blocked' else _failure(error)
        if status == 'saving' and failure.code != 'insufficient_disk':
            failure = BeatSongMixFailure(code='song_save_failed')
        _change(job_id, status=status, error=failure.model_dump(), finished_at=now())
        await _cleanup(job_id)


async def cancel(job_id: str) -> BeatSongMixJob:
    async with _lock:
        job = get_job(job_id)
        if job.status not in ('queued', 'running', 'cancelling'):
            return job
        _change(job_id, status='cancelling')
        task = _tasks.get(job_id)
        if task is not None:
            await cancel_and_wait(task)
        if get_job(job_id).status == 'cancelling':
            _change(job_id, status='cancelled', finished_at=now())
            await _cleanup(job_id)
        return get_job(job_id)


async def retry_save(job_id: str) -> BeatSongMixJob:
    async with _lock:
        job = get_job(job_id)
        if job.status == 'done':
            return job
        if job.status != 'saving':
            raise BeatSongMixError('song_save_failed')
        return await _publish(job_id)


def artifact_metadata(job_id: str, artifact_id: str) -> BeatSongMixArtifact:
    job = get_job(job_id)
    if job.status != 'done' or job.manifest is None:
        raise BeatSongMixError('beat_mix_not_found')
    item = next((item for item in job.manifest.artifacts if item.id == artifact_id), None)
    if item is None:
        raise BeatSongMixError('beat_mix_not_found')
    return item


def artifact(job_id: str, artifact_id: str) -> tuple[Path, BeatSongMixArtifact]:
    item = artifact_metadata(job_id, artifact_id)
    return renderer.resolve_artifact(job_id, item), item


def project(job_id: str) -> MusicProjectPublication:
    record = store.get_job(job_id)
    if record.job.status != 'done' or record.job.request.mode != 'full' or record.project_id is None:
        raise BeatSongMixError('beat_mix_not_found')
    row = db.get_db().execute('SELECT provenance_json FROM music_project_publications WHERE project_id=?', (record.project_id,)).fetchone()
    if row is None:
        raise BeatSongMixError('song_save_failed')
    return MusicProjectPublication(project_id=record.project_id,
        provenance=MusicProjectProvenance.model_validate_json(row['provenance_json']))


def publication_component(track_id: int, source_sha256: str, params: JsonObject) -> ProvenanceComponent | None:
    identifier = params.get('beat_song_mix_job_id')
    if not isinstance(identifier, str):
        return None
    try:
        record = store.get_job(identifier)
        manifest, plan = record.job.manifest, record.job.plan
        if (record.job.status != 'done' or record.job.request.mode != 'full' or record.track_id != track_id
                or manifest is None or manifest.track_id != track_id or params.get('beat_song_mix_plan_id') != plan.id
                or params.get('beat_song_mix_revision') != plan.revision):
            return None
        _matches(record, manifest)
        mix = next(item for item in manifest.artifacts if item.kind == 'mix')
        if mix.sha256 != source_sha256:
            return None
        origin = _origin(record)
        return ProvenanceComponent(role='audio', content_origin=origin, source_id='beat-song:' + record.job.id,
            source_sha256=source_sha256, source_records_sha256=plan.sha256, source_record_count=2,
            music_evidence=manifest.music_evidence, engine='openfabric-cpu-dsp',
            classification_basis='unverified' if origin == 'unknown' else 'app_workflow')
    except (OSError, sqlite3.Error, ValueError, BeatSongMixError, StopIteration):
        return None


def work_busy() -> bool:
    return bool(_retaining or _tasks or _processes or _unverified or _spawn_quarantine)


def _valid_receipt(job_id: str, identity: WorkerIdentity) -> bool:
    path = Path(identity.receipt)
    commands = renderer.job_dir(job_id) / 'commands'
    return path.name == 'worker.json' and path.parent.parent == commands and not path.is_symlink() and path.resolve() == path


async def _recover_commands(job_id: str) -> bool:
    commands = renderer.job_dir(job_id) / 'commands'
    if not commands.exists():
        _spawn_quarantine.discard(job_id)
        return True
    if commands.is_symlink() or commands.resolve() != commands:
        return False
    for folder in commands.iterdir():
        if folder.is_symlink() or folder.resolve() != folder or not folder.is_dir():
            return False
        try:
            await drain_receipt(folder / 'worker.json')
            await run_owned_thread(shutil.rmtree, folder)
        except (OSError, ValueError):
            _spawn_quarantine.add(job_id)
            return False
    _spawn_quarantine.discard(job_id)
    return True


async def recover() -> None:
    global _closing
    _closing = True
    try:
        for job in store.list_jobs(active_only=True):
            if job.id in _tasks:
                continue
            record = store.get_job(job.id)
            if record.identity is not None:
                if not _valid_receipt(job.id, record.identity) or not await terminate_verified(record.identity):
                    _unverified[job.id] = record.identity
                    _change(job.id, status='blocked', error=BeatSongMixFailure(code='song_cleanup_failed').model_dump())
                    continue
                store.identity(job.id, None)
                _unverified.pop(job.id, None)
            if not await _recover_commands(job.id):
                _spawn_quarantine.add(job.id)
                _change(job.id, status='blocked', error=BeatSongMixFailure(code='song_cleanup_failed').model_dump())
                continue
            try:
                manifest = await run_owned_thread(renderer.validate_manifest, job.id)
                _matches(store.get_job(job.id), manifest)
            except (OSError, BeatSongMixError, ValidationError):
                _change(job.id, status='interrupted', finished_at=now())
                await _cleanup(job.id)
            else:
                _change(job.id, status='saving', manifest=manifest.model_dump())
                try:
                    await _publish(job.id)
                except Exception as error:
                    logger.exception('Beat/song recovery could not finish publication')
                    _change(job.id, status='saving', error=_failure(error).model_dump())
    finally:
        _closing = False


async def shutdown() -> None:
    global _closing
    async with _lock:
        _closing = True
    for job_id, task in list(_tasks.items()):
        if get_job(job_id).status == 'saving':
            await await_cleanup(asyncio.shield(task))
        else:
            await cancel_and_wait(task)
    for job_id, identity in list(_unverified.items()):
        if _valid_receipt(job_id, identity) and await terminate_verified(identity):
            _unverified.pop(job_id, None)
            _processes.pop(job_id, None)
            store.identity(job_id, None)
    for job_id in list(_spawn_quarantine):
        await _recover_commands(job_id)
