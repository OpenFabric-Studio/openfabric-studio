"""Bounded retained-source assembly through the existing sampler and CPU DSP."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import shutil
import threading
import uuid
import zipfile

import numpy as np
import soundfile as sf
from pydantic import BaseModel, Field, TypeAdapter

from . import beat_render, db, music_dsp, song_sources
from .atomic_files import write_object
from .audio_quality import INPUT, RunCommand, measure
from .beat_compile import compile_events
from .beat_contracts import BeatContract, BeatId, BeatInstrumentManifest, BeatJobRequest, canonical_json, manifest_sha256
from .beat_song_mix_contracts import (BeatSongMixArtifact, BeatSongMixError, BeatSongMixJob, BeatSongMixManifest,
    BeatSongMixPlan, BeatSongMixStorage, BeatSongRenderedLane, SongSourceSnapshot)
from .contracts import JsonObject
from .job_lifecycle import await_cleanup, run_owned_thread
from .music_analysis_contracts import VersionMusicSource
from .music_dsp_contracts import DspArtifactRef, DspMixInput, DspMixRequest, DspTransformRequest, WarpAnchor
from .music_evidence import merge_music_evidence, shareable_music_evidence
from .video_io import copy_verified, hash_file
from .video_media import tool

RATE = 48000
_json = TypeAdapter(JsonObject)
_id = TypeAdapter(BeatId)
Separate = Callable[[Path, Path], Awaitable[dict[str, Path]]]
Progress = Callable[[str, int, int], None]


def root() -> Path:
    if db.FILES_DIR.is_symlink():
        raise BeatSongMixError('song_storage_unavailable')
    folder = db.FILES_DIR.resolve() / '_song_mixes'
    if folder.is_symlink() or folder.resolve() != folder:
        raise BeatSongMixError('song_storage_unavailable')
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def job_dir(job_id: str, *, create: bool = False) -> Path:
    folder = root() / _id.validate_python(job_id)
    if folder.is_symlink() or folder.resolve() != folder:
        raise BeatSongMixError('song_storage_unavailable')
    if create:
        folder.mkdir(exist_ok=True)
    return folder


def contained(folder: Path, filename: str) -> Path:
    path = folder / filename
    if (path.name != filename or path.is_symlink() or path.resolve() != path
            or not path.is_file() or not path.is_relative_to(root())):
        raise BeatSongMixError('song_source_missing')
    return path


def write_model(path: Path, model: BaseModel) -> None:
    if path.is_symlink() or path.parent.is_symlink() or path.parent.resolve() != path.parent:
        raise BeatSongMixError('song_storage_unavailable')
    write_object(path, _json.validate_json(canonical_json(model)))


def _sync(path: Path) -> None:
    with path.open('rb') as handle:
        os.fsync(handle.fileno())


def _pcm(path: Path) -> int:
    info = sf.info(path)
    if (info.samplerate != RATE or info.channels != 2 or info.subtype != 'FLOAT'
            or info.format not in ('WAV', 'WAVEX') or not 0 < info.frames <= RATE * 600):
        raise BeatSongMixError('beat_mix_render_failed')
    with sf.SoundFile(path) as handle:
        while len(block := handle.read(65536, dtype='float32', always_2d=True)):
            if not np.all(np.isfinite(block)):
                raise BeatSongMixError('beat_mix_render_failed')
    return info.frames


async def _decode(path: Path, output: Path, start: float, duration: float, run: RunCommand) -> None:
    frames = round(duration * RATE)
    await run([tool('ffmpeg'), '-v', 'error', '-nostdin', '-n', *INPUT, '-i', str(path), '-map', '0:a:0', '-vn',
        '-af', f'atrim=start={start:.12g}:end={start + duration:.12g},asetpts=PTS-STARTPTS,aresample=48000,apad=whole_len={frames},atrim=end_sample={frames}',
        '-ar', '48000', '-ac', '2', '-c:a', 'pcm_f32le', '-fs', str(frames * 8 + 1048576), str(output)], 3600)
    if await run_owned_thread(_pcm, output) != frames:
        raise BeatSongMixError('beat_mix_render_failed')


async def artifact(path: Path, kind: str, *, lane_id: str | None = None) -> BeatSongMixArtifact:
    frames = await run_owned_thread(_pcm, path) if path.suffix == '.wav' else None
    await run_owned_thread(_sync, path)
    return BeatSongMixArtifact.model_validate({'id': hashlib.sha256(path.name.encode()).hexdigest()[:32],
        'kind': kind, 'filename': path.name, 'sha256': await hash_file(path, max_bytes=16 * 1024**3),
        'byte_count': path.stat().st_size, 'media_type': 'audio/wav' if frames is not None else
            'application/zip' if path.suffix == '.zip' else 'application/json', 'lane_id': lane_id, 'frames': frames})


def storage(plan: BeatSongMixPlan, *, model_bytes: int = 0, sample_bytes: int | None = None) -> BeatSongMixStorage:
    pcm = math.ceil(plan.target_duration_sec * RATE) * 8
    lane_count = len(plan.lanes)
    samples = sample_bytes or 0
    if plan.beat.beat_document is not None and sample_bytes is None:
        doc = plan.beat.beat_document
        kit = beat_render.load_kit(doc.kit_id, doc.kit_manifest_sha256)
        samples = sum(region.byte_size for instrument in kit.instruments for region in instrument.regions)
    retained = plan.beat.source_bytes + plan.song.source_bytes + samples + pcm * (lane_count * 3 + 6)
    # Raw copies, whole-song native context, aligned assets, master, archive and
    # local separator checkpoint copies are all charged before admission.
    temporary = pcm * (lane_count * 10 + 16) + model_bytes
    required = math.ceil((retained + temporary) * 1.25) + 512 * 1024**2
    try:
        available = shutil.disk_usage(root()).free
    except OSError:
        available = None
    return BeatSongMixStorage(retained_bytes=retained, temporary_bytes=temporary, required_free_bytes=required,
        free_bytes=available, can_start=available is not None and available >= required)


async def retain_kit(plan: BeatSongMixPlan, owner_root: Path) -> None:
    """Protect exact used sampler inputs before accepting queued mix work."""
    from .beat_song_mix import expanded_document
    document = plan.beat.beat_document
    if document is None:
        return
    kit = beat_render.load_kit(document.kit_id, document.kit_manifest_sha256)
    compiled = compile_events(expanded_document(document, plan.draft.beat_repeats), RATE, kit)
    used = {event.asset_id for event in compiled.events}
    regions = {region.asset_id: region for instrument in kit.instruments for region in instrument.regions if region.asset_id in used}
    if sum(item.byte_size for item in regions.values()) > 512 * 1024**2:
        raise BeatSongMixError('beat_mix_budget_exceeded')
    folder = owner_root / 'kit'
    folder.mkdir(exist_ok=False)
    for asset, region in regions.items():
        target = folder / (hashlib.sha256(asset.encode()).hexdigest() + '.wav')
        await copy_verified(beat_render.resolve_sample(kit, asset), target, region.sha256, max_bytes=512 * 1024**2)
        await run_owned_thread(_sync, target)
    await run_owned_thread(write_model, folder / 'manifest.json', kit)


def retained_kit(plan: BeatSongMixPlan, owner_root: Path) -> BeatInstrumentManifest:
    document = plan.beat.beat_document
    path = owner_root / 'kit' / 'manifest.json'
    if (document is None or path.is_symlink() or path.resolve() != path or path.stat().st_size > 8 * 1024**2):
        raise BeatSongMixError('song_source_changed')
    kit = BeatInstrumentManifest.model_validate_json(path.read_bytes())
    if manifest_sha256(kit) != document.kit_manifest_sha256:
        raise BeatSongMixError('song_source_changed')
    return kit


class _PreparedLane(BeatContract):
    lane_id: BeatId
    filename: str = Field(pattern=r'^[0-9a-f]{32}\.wav$')
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    frames: int = Field(ge=1, le=28800000)


class _Prepared(BeatContract):
    plan_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    lanes: list[_PreparedLane] = Field(min_length=2, max_length=32)


async def _sampler(job: BeatSongMixJob, folder: Path) -> dict[str, Path]:
    from .beat_song_mix import expanded_document
    document = job.plan.beat.beat_document
    if document is None:
        raise BeatSongMixError('beat_mix_invalid')
    owner = song_sources.owned_root(job.id)
    kit_root = owner / 'kit'
    kit = retained_kit(job.plan, owner)
    internal_id = hashlib.sha256(('song-sampler:' + job.id).encode()).hexdigest()[:32]
    output = beat_render.workspace(internal_id, create=True) / 'output'
    stop = threading.Event()

    def resolve(_kit: BeatInstrumentManifest, asset: str) -> Path:
        path = kit_root / (hashlib.sha256(asset.encode()).hexdigest() + '.wav')
        if path.is_symlink() or path.resolve() != path:
            raise BeatSongMixError('song_source_changed')
        return path

    def sample() -> dict[str, Path]:
        manifest = beat_render.render(expanded_document(document, job.plan.draft.beat_repeats),
            BeatJobRequest(beat_id=document.id, expected_revision=document.revision, mode='full', submission_key=job.id),
            kit, internal_id, output, cancelled=stop.is_set, sample_resolver=resolve)
        result: dict[str, Path] = {}
        for item in manifest.artifacts:
            if item.kind == 'stem' and item.lane_id is not None:
                target = folder / (hashlib.sha256(('beat:' + item.lane_id).encode()).hexdigest()[:32] + '.wav')
                shutil.copyfile(beat_render.resolve_artifact(internal_id, item), target)
                result[item.lane_id] = target
        return result

    task = asyncio.create_task(asyncio.to_thread(sample))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.set()
        await await_cleanup(asyncio.gather(task, return_exceptions=True))
        raise
    finally:
        await run_owned_thread(shutil.rmtree, output.parent)


async def prepare(job: BeatSongMixJob, sources: list[SongSourceSnapshot], run: RunCommand,
                  separate: Separate) -> dict[str, Path]:
    folder = job_dir(job.id) / 'prepared'
    receipt = folder / 'preparation.json'
    if receipt.is_file() and not receipt.is_symlink() and receipt.stat().st_size <= 1024**2:
        prior = _Prepared.model_validate_json(receipt.read_bytes())
        if prior.plan_sha256 != job.plan.sha256 or {item.lane_id for item in prior.lanes} != {lane.id for lane in job.plan.lanes}:
            raise BeatSongMixError('song_source_changed')
        result = {}
        for item in prior.lanes:
            path = contained(folder, item.filename)
            if await hash_file(path, max_bytes=song_sources.MAX_BYTES) != item.sha256 or await run_owned_thread(_pcm, path) != item.frames:
                raise BeatSongMixError('song_source_changed')
            result[item.lane_id] = path
        return result
    if folder.exists():
        await run_owned_thread(shutil.rmtree, folder)
    folder.mkdir()
    paths = [await song_sources.resolve_snapshot(song_sources.owned_root(job.id), source) for source in sources]
    draft = job.plan.draft
    result = {}
    if job.plan.beat.beat_document is not None:
        sampled = await _sampler(job, folder)
        for lane in job.plan.lanes:
            if lane.source == 'beat' and lane.beat_lane_id is not None:
                # The sampler retains natural tails; the reviewed moving span
                # is the musical chain. Warp/trim at the explicit final end.
                source = sampled[lane.beat_lane_id]
                duration = job.plan.beat.musical_duration_sec
                if duration is None:
                    raise BeatSongMixError('beat_mix_invalid')
                target = folder / (lane.id + '.wav')
                if target == source:
                    source.rename(folder / ('sampled-' + lane.id + '.wav'))
                    source = folder / ('sampled-' + lane.id + '.wav')
                await _decode(source, target, 0, duration * draft.beat_repeats, run)
                result[lane.id] = target
    else:
        lane = next(item for item in job.plan.lanes if item.source == 'beat')
        single = folder / 'single-beat.wav'
        duration = draft.beat.excerpt_end_sec - draft.beat.excerpt_start_sec
        await _decode(paths[0], single, draft.beat.excerpt_start_sec, duration, run)
        target = folder / (lane.id + '.wav')
        if draft.beat_repeats == 1:
            single.rename(target)
        else:
            await run_owned_thread(_audio_loop, single, target, draft.beat_repeats)
        result[lane.id] = target
    song = folder / 'song-source.wav'
    song_duration = draft.song.excerpt_end_sec - draft.song.excerpt_start_sec
    await _decode(paths[1], song, draft.song.excerpt_start_sec, song_duration, run)
    separated = await separate(song, folder / 'separated') if draft.mode == 'replace_drums' else {'mix': song}
    if set(separated) != ({'vocals', 'drums', 'bass', 'other'} if draft.mode == 'replace_drums' else {'mix'}):
        raise BeatSongMixError('beat_mix_drums_required')
    for lane in job.plan.lanes:
        if lane.source != 'song':
            continue
        stem = 'drums' if lane.role == 'drum' else lane.role
        source = separated[stem]
        if source.is_symlink() or source.resolve() != source or not source.is_relative_to(folder):
            raise BeatSongMixError('beat_mix_drums_required')
        facts = await song_sources.probe(source)
        if abs(facts.duration_sec - song_duration) > .1:
            raise BeatSongMixError('beat_mix_drums_required')
        target = folder / (lane.id + '.wav')
        await _decode(source, target, 0, song_duration, run)
        result[lane.id] = target
    for snapshot in sources:
        await song_sources.resolve_snapshot(song_sources.owned_root(job.id), snapshot)
    prepared = _Prepared(plan_sha256=job.plan.sha256, lanes=[_PreparedLane(lane_id=identifier,
        filename=path.name, sha256=await hash_file(path, max_bytes=song_sources.MAX_BYTES),
        frames=await run_owned_thread(_pcm, path)) for identifier, path in result.items()])
    await run_owned_thread(write_model, receipt, prepared)
    return result


def context_bar(plan: BeatSongMixPlan) -> float:
    """At least the declared bar, including the longest authored mapped bar."""
    result = float(plan.draft.project_meter.quarters_per_bar) * 60 / plan.draft.project_bpm
    document = plan.beat.beat_document
    if document is not None:
        authored = max(float(pattern.meter.quarters_per_bar) * 60 / document.bpm for pattern in document.patterns)
        result = max(result, authored * (plan.maximum_ratio if plan.draft.tempo_strategy == 'beat_to_song' else 1))
    return result


def _audio_loop(source: Path, target: Path, repeats: int) -> None:
    """Explicit audio approximation: 5ms boundary fades, fixed reviewed period."""
    frames = _pcm(source)
    with sf.SoundFile(source) as original, sf.SoundFile(target, mode='w', samplerate=RATE,
            channels=2, format='WAV', subtype='FLOAT') as output:
        for _ in range(repeats):
            original.seek(0)
            position = 0
            while len(block := original.read(65536, dtype='float32', always_2d=True)):
                indexes = np.arange(position, position + len(block))
                weights = np.minimum(1, np.minimum(indexes / 239, (frames - 1 - indexes) / 239)).clip(0, 1)
                output.write(block * weights[:, None])
                position += len(block)


def _source_at(anchors: list[WarpAnchor], target: float) -> float:
    for left, right in zip(anchors, anchors[1:]):
        if target <= right.target_sec + 1e-10:
            return left.source_sec + (target - left.target_sec) * (right.source_sec - left.source_sec) / (right.target_sec - left.target_sec)
    return anchors[-1].source_sec


@dataclass(frozen=True)
class WarpWindow:
    anchors: list[WarpAnchor]
    core_start: float
    core_end: float


def warp_windows(anchors: list[WarpAnchor], context_bar_sec: float) -> list[WarpWindow]:
    """Mapped calls use <=30s source with a whole reviewed context bar per join."""
    duration = anchors[-1].target_sec
    ratios = [(b.target_sec - a.target_sec) / (b.source_sec - a.source_sec) for a, b in zip(anchors, anchors[1:])]
    uniform = max(ratios) - min(ratios) < 1e-9
    if uniform or anchors[-1].source_sec <= 30:
        return [WarpWindow(anchors, 0, duration)]
    capacity = 30 * min(ratios) - 2 * context_bar_sec
    if capacity < 1:
        raise BeatSongMixError('beat_mix_alignment_invalid')
    count = math.ceil(duration / capacity)
    if count > 256:
        raise BeatSongMixError('beat_mix_budget_exceeded')
    windows = []
    for index in range(count):
        core_start, core_end = duration * index / count, duration * (index + 1) / count
        start, end = max(0, core_start - context_bar_sec), min(duration, core_end + context_bar_sec)
        selected = [WarpAnchor(source_sec=_source_at(anchors, start), target_sec=start),
            *[item for item in anchors if start < item.target_sec < end],
            WarpAnchor(source_sec=_source_at(anchors, end), target_sec=end)]
        windows.append(WarpWindow(selected, core_start, core_end))
    return windows


def _stitch(chunks: Sequence[tuple[Path, WarpWindow]], output: Path, frames: int) -> None:
    """Complementary linear 10ms joins after contextual native processing."""
    with sf.SoundFile(output, mode='w', samplerate=RATE, channels=2, format='WAV', subtype='FLOAT') as target:
        for position in range(0, frames, 65536):
            end = min(frames, position + 65536)
            block = np.zeros((end - position, 2), dtype=np.float32)
            for index, (path, window) in enumerate(chunks):
                start_frame = round(window.core_start * RATE) - (240 if index else 0)
                end_frame = round(window.core_end * RATE) + (240 if index + 1 < len(chunks) else 0)
                left, right = max(position, start_frame), min(end, end_frame)
                if left >= right:
                    continue
                with sf.SoundFile(path) as source:
                    source.seek(left - round(window.anchors[0].target_sec * RATE))
                    data = source.read(right - left, dtype='float32', always_2d=True)
                if len(data) != right - left or not np.all(np.isfinite(data)):
                    raise BeatSongMixError('beat_mix_render_failed')
                indexes = np.arange(left, right)
                weights = np.ones(len(data))
                if index:
                    weights *= np.clip((indexes - start_frame) / 479, 0, 1)
                if index + 1 < len(chunks):
                    weights *= np.clip((end_frame - 1 - indexes) / 479, 0, 1)
                block[left - position:right - position] += data * weights[:, None]
            target.write(block)


async def fit_lane(source: music_dsp.ResolvedDspSource, anchors: list[WarpAnchor], duration: float,
                   context_bar_sec: float, revision: int, pitch: float, work: Path,
                   run: RunCommand, native: music_dsp.NativeStretch | None) -> Path:
    if source.source.kind != 'version':
        raise BeatSongMixError('beat_mix_invalid')
    windows = warp_windows(anchors, context_bar_sec)
    chunks = []
    for window in windows:
        result = await music_dsp.render_transform(DspTransformRequest(source=source.source, source_sha256=source.sha256,
            revision=revision, anchors=window.anchors, output_duration_sec=window.anchors[-1].target_sec - window.anchors[0].target_sec,
            sample_rate_hz=48000, channels=2, pitch_shift_semitones=pitch), source, work, run, native=native)
        chunks.append((work / result.artifact_name, window))
    if len(chunks) == 1:
        return chunks[0][0]
    final = work / (uuid.uuid4().hex + '-stitched.wav')
    await run_owned_thread(_stitch, chunks, final, round(duration * RATE))
    return final


async def _resolved(path: Path, snapshot: SongSourceSnapshot) -> music_dsp.ResolvedDspSource:
    return music_dsp.ResolvedDspSource(path, VersionMusicSource(track_id=snapshot.track_id, version_id=snapshot.version_id),
        await hash_file(path, max_bytes=song_sources.MAX_BYTES), RATE, 2, await run_owned_thread(_pcm, path))


async def render(job: BeatSongMixJob, snapshots: list[SongSourceSnapshot], output: Path, run: RunCommand, *,
                 native: music_dsp.NativeStretch | None, separate: Separate,
                 progress: Progress = lambda _phase, _done, _total: None) -> BeatSongMixManifest:
    from .beat_song_mix import plan_digest
    plan = job.plan
    if plan_digest(plan) != plan.sha256 or output != job_dir(job.id) / 'output' or len(snapshots) != 2:
        raise BeatSongMixError('beat_mix_invalid')
    output.mkdir(exist_ok=False)
    work = job_dir(job.id) / ('dsp-' + uuid.uuid4().hex)
    work.mkdir()
    progress('warping', 0, len(plan.lanes))
    prepared = await prepare(job, snapshots, run, separate)
    duration = plan.target_duration_sec
    bar = context_bar(plan)
    fitted: dict[str, music_dsp.ResolvedDspSource] = {}
    artifacts: list[BeatSongMixArtifact] = []
    dry_ids: dict[str, str] = {}
    for index, lane in enumerate(plan.lanes):
        progress('warping', index, len(plan.lanes))
        snapshot = snapshots[0 if lane.source == 'beat' else 1]
        source = await _resolved(prepared[lane.id], snapshot)
        moving = lane.source == ('beat' if plan.draft.tempo_strategy == 'beat_to_song' else 'song')
        anchors = plan.draft.anchors if moving else [WarpAnchor(source_sec=0, target_sec=0), WarpAnchor(source_sec=duration, target_sec=duration)]
        pitch = plan.draft.audio_pitch_semitones if lane.source == 'beat' else 0
        path = await fit_lane(source, anchors, duration, bar, plan.revision, pitch, work, run, native)
        fitted_source = await _resolved(path, snapshot)
        if fitted_source.source.kind != 'version':
            raise BeatSongMixError('beat_mix_invalid')
        # Effects run after contextual stitching; context boundaries do not
        # restart the EQ/filter state of the assembled lane.
        effects = await music_dsp.render_transform(DspTransformRequest(source=fitted_source.source,
            source_sha256=fitted_source.sha256, revision=plan.revision, sample_rate_hz=48000, channels=2,
            anchors=[WarpAnchor(source_sec=0, target_sec=0), WarpAnchor(source_sec=duration, target_sec=duration)],
            output_duration_sec=duration, highpass_hz=lane.control.highpass_hz, eq_bands=lane.control.eq_bands),
            fitted_source, work, run)
        fitted[lane.id] = await _resolved(work / effects.artifact_name, snapshot)
        dry_path = output / ('dry-' + lane.id + '.wav')
        await copy_verified(source.path, dry_path, source.sha256, max_bytes=song_sources.MAX_BYTES)
        dry = await artifact(dry_path, 'dry', lane_id=lane.id)
        artifacts.append(dry)
        dry_ids[lane.id] = dry.id
    start = job.request.preview_start_sec or 0
    end = job.request.preview_end_sec if job.request.mode == 'preview' else duration
    if end is None or end > duration:
        raise BeatSongMixError('beat_mix_invalid')
    selected_duration = end - start
    aligned: dict[str, music_dsp.ResolvedDspSource] = {}
    mappings = []
    for index, lane in enumerate(plan.lanes):
        progress('mixing', index, len(plan.lanes))
        source = fitted[lane.id]
        ref = DspArtifactRef(id=lane.id, revision=plan.revision, sha256=source.sha256)
        sources = {lane.id: music_dsp.ResolvedDspSource(source.path, ref, source.sha256, RATE, 2, source.duration_frames)}
        inputs = [DspMixInput(id=lane.id, source=ref, source_sha256=source.sha256, duration_sec=duration,
            gain_db=lane.control.gain_db, pan=lane.control.pan, muted=lane.control.muted, duck=lane.control.duck)]
        duck = lane.control.duck
        if duck is not None:
            detector = fitted[duck.sidechain_input_id]
            detector_ref = DspArtifactRef(id=duck.sidechain_input_id, revision=plan.revision, sha256=detector.sha256)
            sources[duck.sidechain_input_id] = music_dsp.ResolvedDspSource(detector.path, detector_ref, detector.sha256,
                RATE, 2, detector.duration_frames)
            inputs.append(DspMixInput(id=duck.sidechain_input_id, source=detector_ref, source_sha256=detector.sha256,
                duration_sec=duration, muted=True))
        result = await music_dsp.render_mix(DspMixRequest(revision=plan.revision, duration_sec=duration, inputs=inputs), sources, work, run)
        full_path = work / result.artifact_name
        aligned[lane.id] = await _resolved(full_path, snapshots[0 if lane.source == 'beat' else 1])
        target = output / ('processed-' + lane.id + '.wav')
        await _decode(full_path, target, start, selected_duration, run)
        item = await artifact(target, 'processed', lane_id=lane.id)
        artifacts.append(item)
        mappings.append(BeatSongRenderedLane(lane_id=lane.id, artifact_id=item.id, dry_artifact_id=dry_ids[lane.id]))
    inputs = []
    sources = {}
    for identifier, source in aligned.items():
        ref = DspArtifactRef(id=identifier, revision=plan.revision, sha256=source.sha256)
        sources[identifier] = music_dsp.ResolvedDspSource(source.path, ref, source.sha256, RATE, 2, source.duration_frames)
        inputs.append(DspMixInput(id=identifier, source=ref, source_sha256=source.sha256, duration_sec=duration))
    mastered = await music_dsp.render_mix(DspMixRequest(revision=plan.revision, duration_sec=duration,
        inputs=inputs, loudness=plan.draft.loudness), sources, work, run)
    master = output / 'mix.wav'
    await _decode(work / mastered.artifact_name, master, start, selected_duration, run)
    master_artifact = await artifact(master, 'mix')
    artifacts.append(master_artifact)
    metrics = (await measure(master, work, run, name='master-measurement')).metrics
    warnings = list(plan.warnings)
    if metrics.full_scale_fraction > 0 or metrics.true_peak_dbtp is not None and metrics.true_peak_dbtp > 0:
        warnings.append('hard_clip_risk')
    evidence = shareable_music_evidence(merge_music_evidence([source.music_evidence for source in snapshots] +
        ([None] if plan.draft.mode == 'replace_drums' else [])))
    await run_owned_thread(write_model, output / 'plan.json', plan)
    await run_owned_thread(write_model, output / 'provenance.json', evidence)
    artifacts.extend([await artifact(output / 'plan.json', 'plan'), await artifact(output / 'provenance.json', 'provenance')])
    bundle = output / 'beat-song.zip'
    await run_owned_thread(_bundle, bundle, output, artifacts)
    artifacts.append(await artifact(bundle, 'bundle'))
    manifest = BeatSongMixManifest(job_id=job.id, plan_id=plan.id, plan_revision=plan.revision, plan_sha256=plan.sha256,
        mode=job.request.mode, frames=round(selected_duration * RATE), sources=snapshots, artifacts=artifacts,
        lanes=mappings, metrics=metrics, music_evidence=evidence, warnings=list(dict.fromkeys(warnings)))
    await run_owned_thread(write_model, output / 'manifest.json', manifest)
    await run_owned_thread(shutil.rmtree, work)
    return manifest


def _bundle(target: Path, output: Path, artifacts: Sequence[BeatSongMixArtifact]) -> None:
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_STORED) as archive:
        for item in artifacts:
            archive.write(contained(output, item.filename), item.filename)


def resolve_artifact(job_id: str, item: BeatSongMixArtifact) -> Path:
    from .export_provenance import digest
    path = contained(job_dir(job_id) / 'output', item.filename)
    if path.stat().st_size != item.byte_count or digest(path) != item.sha256:
        raise BeatSongMixError('song_source_changed')
    if item.frames is not None and _pcm(path) != item.frames:
        raise BeatSongMixError('song_source_changed')
    return path


def validate_manifest(job_id: str) -> BeatSongMixManifest:
    path = contained(job_dir(job_id) / 'output', 'manifest.json')
    if path.stat().st_size > 8 * 1024**2:
        raise BeatSongMixError('beat_mix_budget_exceeded')
    manifest = BeatSongMixManifest.model_validate_json(path.read_bytes())
    if manifest.job_id != job_id:
        raise BeatSongMixError('song_source_changed')
    for item in manifest.artifacts:
        resolve_artifact(job_id, item)
    return manifest
