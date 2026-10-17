"""Retained mashup audio and shared-DSP rendering, owned by the calling job.

Only relative artifact names leave this module. Preview transforms the same
whole placements as full rendering, then selects the requested time range;
it does not invent new fades or a different stretch at the preview boundary.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import math
import os
from pathlib import Path
import shutil
import uuid
import zipfile

import numpy as np
from pydantic import BaseModel, Field, TypeAdapter
import soundfile as sf

from . import db, music_dsp
from .atomic_files import write_object
from .audio_quality import INPUT, RunCommand
from .beat_contracts import canonical_json
from .contracts import JsonObject
from .export_provenance_contracts import ExportProvenance, ProvenanceComponent
from .job_lifecycle import run_owned_thread
from .mashup_contracts import (MashupAudioArtifact, MashupError, MashupJob, MashupJobRequest,
    MashupPlacement, MashupRenderArtifact, MashupRenderedPlacement, MashupRenderManifest,
    MashupSourceSnapshot, MashupState, MashupStoragePreview, MashupStem, MashupWarning, SourceSide)
from .mashup_plan import RATE, tick_frame, validate_plan
from .music_analysis_contracts import StemMusicSource
from .music_dsp_contracts import (DspArtifactRef, DspMixInput, DspMixRequest, DspTransformRequest,
    DspTransformResult, FadeSettings, WarpAnchor)
from .music_evidence import merge_music_evidence, shareable_music_evidence
from .video_io import hash_file
from .video_media import tool

MAX_BYTES = 8 * 1024**3
RESERVE = 1024**3
_json = TypeAdapter(JsonObject)


def root() -> Path:
    # A configured library may use an OS parent alias (macOS /var or /tmp).
    # Normalize that trusted root once; private children must remain exact.
    if db.FILES_DIR.is_symlink():
        raise MashupError('mashup_source_missing')
    base = db.FILES_DIR.resolve()
    folder = base / '_mashups'
    folder.mkdir(parents=True, exist_ok=True)
    if folder.is_symlink() or folder.resolve() != folder:
        raise MashupError('mashup_source_missing')
    return folder


def _directory(folder: Path, *, create: bool) -> Path:
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    if folder.is_symlink() or folder.resolve() != folder or not folder.is_relative_to(root()):
        raise MashupError('mashup_source_missing')
    return folder


def source_dir(mashup_id: str, side: SourceSide, *, create: bool = False) -> Path:
    from .mashup_store import identifier
    return _directory(root() / 'documents' / identifier(mashup_id) / 'sources' / side, create=create)


def job_dir(job_id: str, *, create: bool = False) -> Path:
    from .mashup_store import identifier
    return _directory(root() / 'jobs' / identifier(job_id), create=create)


def contained(folder: Path, filename: str) -> Path:
    candidate = folder / filename
    if (candidate.name != filename or candidate.is_symlink() or candidate.resolve() != candidate
            or not candidate.is_relative_to(root()) or not candidate.is_file()):
        raise MashupError('mashup_source_missing')
    return candidate


def write_model(path: Path, model: BaseModel) -> None:
    if path.parent.is_symlink() or path.parent.resolve() != path.parent or path.is_symlink():
        raise MashupError('mashup_source_missing')
    write_object(path, _json.validate_json(canonical_json(model)))


def _digest(path: Path, maximum: int = 16 * 1024**3) -> str:
    if path.is_symlink() or path.resolve() != path or not path.is_file():
        raise MashupError('mashup_source_missing')
    before = path.stat()
    if not 0 < before.st_size <= maximum:
        raise MashupError('mashup_budget_exceeded')
    digest = hashlib.sha256()
    total = 0
    with path.open('rb') as stream:
        while block := stream.read(65536):
            total += len(block)
            if total > maximum:
                raise MashupError('mashup_budget_exceeded')
            digest.update(block)
    after = path.stat()
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
        raise MashupError('mashup_source_changed')
    return digest.hexdigest()


def _pcm(path: Path) -> tuple[int, int]:
    info = sf.info(path)
    if (info.samplerate != RATE or info.channels != 2 or info.subtype != 'FLOAT'
            or not 0 < info.frames <= 57600000 or path.stat().st_size > MAX_BYTES):
        raise MashupError('mashup_source_changed')
    with sf.SoundFile(path) as audio:
        count = 0
        while True:
            block = audio.read(4096, dtype='float32', always_2d=True)
            if not len(block):
                break
            if not np.isfinite(block).all():
                raise MashupError('mashup_source_changed')
            count += len(block)
            if count > info.frames:
                raise MashupError('mashup_source_changed')
        if count != info.frames:
            raise MashupError('mashup_source_changed')
    return info.frames, path.stat().st_size


async def audio_artifact(path: Path) -> MashupAudioArtifact:
    frames, size = await run_owned_thread(_pcm, path)
    checksum = await hash_file(path, max_bytes=MAX_BYTES)
    return MashupAudioArtifact(id=uuid.uuid4().hex, filename=path.name, sha256=checksum,
        byte_count=size, frames=frames)


async def decode_excerpt(source: Path, output: Path, start: float, end: float, run: RunCommand) -> MashupAudioArtifact:
    frames = round((end - start) * RATE)
    if not 0 < frames <= 28800000 or output.exists() or output.is_symlink():
        raise MashupError('mashup_invalid')
    await run([tool('ffmpeg'), '-v', 'error', '-nostdin', '-n', '-ss', format(start, '.12g'),
        *INPUT, '-i', str(source), '-map', '0:a:0', '-vn', '-ar', str(RATE), '-ac', '2',
        '-af', f'aresample={RATE},atrim=end_sample={frames},asetpts=PTS-STARTPTS',
        '-c:a', 'pcm_f32le', '-fs', str(frames * 8 + 1048576), str(output)], 3600)
    result = await audio_artifact(output)
    if result.frames != frames:
        raise MashupError('mashup_source_changed')
    return result


class _ProbeStream(BaseModel):
    sample_rate: str = Field(max_length=12)
    channels: int = Field(ge=1, le=2)
    duration: str | None = Field(default=None, max_length=64)
    duration_ts: int | None = Field(default=None, ge=1)
    time_base: str | None = Field(default=None, max_length=64)


class _ProbeFormat(BaseModel):
    duration: str | None = Field(default=None, max_length=64)


class _Probe(BaseModel):
    streams: list[_ProbeStream] = Field(min_length=1, max_length=1)
    format: _ProbeFormat = Field(default_factory=_ProbeFormat)


@dataclass(frozen=True)
class SourceFacts:
    sample_rate_hz: int
    channels: int
    duration_sec: float
    byte_count: int


async def probe(path: Path, run: RunCommand) -> SourceFacts:
    output = await run([tool('ffprobe'), '-v', 'error', *INPUT, '-select_streams', 'a:0',
        '-show_entries', 'stream=sample_rate,channels,duration,duration_ts,time_base:format=duration', '-of', 'json', str(path)], 60)
    value = _Probe.model_validate_json(output)
    stream = value.streams[0]
    duration = (float(stream.duration_ts * Fraction(stream.time_base)) if stream.duration_ts is not None and stream.time_base is not None
        else float(stream.duration or value.format.duration or '0'))
    rate, size = int(stream.sample_rate), path.stat().st_size
    if not 8000 <= rate <= 192000 or not math.isfinite(duration) or not 0 < duration <= 21600 or not 0 < size <= MAX_BYTES:
        raise MashupError('mashup_budget_exceeded')
    return SourceFacts(rate, stream.channels, duration, size)


def free_bytes(path: Path) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def preparation_budget(size: int, duration: float, *, model_bytes: int = 0) -> tuple[int, int, int]:
    pcm = math.ceil(duration * RATE) * 8 + 1048576
    retained = size + pcm * 5
    temporary = pcm * 7 + model_bytes
    return retained, temporary, math.ceil((retained + temporary) * 1.25) + RESERVE


@dataclass(frozen=True)
class SelectedPlacement:
    placement: MashupPlacement
    full_frames: int
    trim_start: int
    trim_end: int
    start_frame: int


def selected(state: MashupState, request: MashupJobRequest) -> tuple[int, int, list[SelectedPlacement]]:
    plan = state.plan
    if plan is None:
        raise MashupError('mashup_analysis_incomplete')
    validate_plan(state, plan)
    start_tick = request.preview_start_tick if request.mode == 'preview' else 0
    end_tick = request.preview_end_tick if request.mode == 'preview' else plan.duration_ticks
    if start_tick is None or end_tick is None or not 0 <= start_tick < end_tick <= plan.duration_ticks:
        raise MashupError('mashup_invalid')
    start, end = tick_frame(start_tick, plan.settings.target_bpm), tick_frame(end_tick, plan.settings.target_bpm)
    if end <= start or (request.mode == 'preview' and end - start > 60 * RATE):
        raise MashupError('mashup_budget_exceeded')
    items: list[SelectedPlacement] = []
    for placement in plan.placements:
        begin = tick_frame(placement.target_start_tick, plan.settings.target_bpm)
        finish = tick_frame(placement.target_end_tick, plan.settings.target_bpm)
        left, right = max(begin, start), min(finish, end)
        if right > left:
            items.append(SelectedPlacement(placement, finish - begin, left - begin, right - begin, left - start))
    if not items:
        raise MashupError('mashup_alignment_unsafe')
    return start_tick, end - start, items


def forecast(state: MashupState, request: MashupJobRequest, *, native_ready: bool) -> MashupStoragePreview:
    _, frames, items = selected(state, request)
    source_frames = sum(item.placement.source_end_frame - item.placement.source_start_frame for item in items)
    transformed_frames = sum(item.full_frames for item in items)
    # Dry/processed placements, four aligned stems, full mix, uncompressed
    # bundle and the library's retained original are all distinct copies.
    retained = (source_frames + transformed_frames + 5 * frames) * 8 * 2 + frames * 8 + 16 * 1024**2
    temporary = (max((source.prepared.frames for source in (state.source_a, state.source_b) if source.prepared is not None), default=0)
        + max((item.full_frames for item in items), default=0) + 4 * frames) * 8 * 4 + 16 * 1024**2
    required = math.ceil((retained + temporary) * 1.25) + RESERVE
    available = free_bytes(root())
    identity = all(item.full_frames == item.placement.source_end_frame - item.placement.source_start_frame
        and item.placement.pitch_semitones == 0 for item in items)
    return MashupStoragePreview(retained_bytes=retained, temporary_bytes=temporary,
        required_free_bytes=required, free_bytes=available, can_start=available is not None and available >= required,
        frames=frames, duration_sec=frames / RATE, stretch_ready=identity or native_ready)


async def _artifact(path: Path, kind: str, *, source: SourceSide | None = None,
    stem: MashupStem | None = None, placement_id: str | None = None, frames: int | None = None) -> MashupRenderArtifact:
    await run_owned_thread(_sync, path)
    return MashupRenderArtifact.model_validate({'id': uuid.uuid4().hex, 'kind': kind,
        'filename': path.name, 'sha256': await hash_file(path, max_bytes=16 * 1024**3),
        'byte_count': path.stat().st_size, 'media_type': 'audio/wav' if path.suffix == '.wav' else 'application/zip' if path.suffix == '.zip' else 'application/json',
        'source': source, 'stem': stem, 'placement_id': placement_id, 'frames': frames})


def _sync(path: Path) -> None:
    with path.open('rb') as stream:
        os.fsync(stream.fileno())


def _bundle(output: Path, artifacts: Sequence[MashupRenderArtifact]) -> Path:
    target = output / 'mashup.zip'
    with target.open('xb') as stream, zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for artifact in artifacts:
            source = contained(output, artifact.filename)
            if _digest(source) != artifact.sha256:
                raise MashupError('mashup_source_changed')
            archive.write(source, artifact.filename)
    with target.open('rb') as synced:
        os.fsync(synced.fileno())
    return target


async def render(state: MashupState, job: MashupJob, output: Path, run: RunCommand, *,
    native: music_dsp.NativeStretch | None, progress: Callable[[int, int], None] | None = None) -> MashupRenderManifest:
    request, plan = job.request, state.plan
    if request is None or plan is None or job.mashup_id != state.id or job.mashup_revision != state.revision:
        raise MashupError('mashup_invalid')
    start_tick, frames, selected_items = selected(state, request)
    output.mkdir(parents=True, exist_ok=False)
    if output.resolve() != output or not output.is_relative_to(job_dir(job.id)):
        raise MashupError('mashup_invalid')
    work = output.parent / ('dsp-' + uuid.uuid4().hex)
    work.mkdir()
    artifacts: list[MashupRenderArtifact] = []
    mappings: list[MashupRenderedPlacement] = []
    transforms: list[DspTransformResult] = []
    lane_inputs: dict[tuple[SourceSide, MashupStem], list[tuple[DspMixInput, music_dsp.ResolvedDspSource]]] = {}
    sources = {'a': state.source_a, 'b': state.source_b}
    resolved: dict[tuple[str, str], music_dsp.ResolvedDspSource] = {}
    for index, selected_item in enumerate(selected_items):
        item = selected_item.placement
        snapshot = sources[item.source]
        stem_snapshot = next((entry for entry in snapshot.stems if entry.stem == item.stem), None)
        if stem_snapshot is None:
            raise MashupError('mashup_source_missing')
        key = (item.source, item.stem)
        source = resolved.get(key)
        if source is None:
            path = contained(source_dir(state.id, item.source), stem_snapshot.artifact.filename)
            fact = stem_snapshot.artifact
            if await hash_file(path, max_bytes=MAX_BYTES) != fact.sha256:
                raise MashupError('mashup_source_changed')
            stem_reference = StemMusicSource(track_id=snapshot.input.source.track_id, stem=item.stem, sha256=fact.sha256)
            source = music_dsp.ResolvedDspSource(path, stem_reference, fact.sha256, RATE, 2, fact.frames)
            resolved[key] = source
        target_frames = selected_item.full_frames
        duration = target_frames / RATE
        retained_reference = source.source
        if not isinstance(retained_reference, StemMusicSource):
            raise MashupError('mashup_invalid')
        transform = DspTransformRequest(source=retained_reference, source_sha256=source.sha256,
            revision=state.revision, sample_rate_hz=48000, channels=2,
            anchors=[WarpAnchor(source_sec=item.source_start_frame / RATE, target_sec=0),
                WarpAnchor(source_sec=item.source_end_frame / RATE, target_sec=duration)],
            output_duration_sec=duration, pitch_shift_semitones=item.pitch_semitones,
            gain_db=item.gain_db, fades=FadeSettings(fade_in_sec=item.fade_in_sec, fade_out_sec=item.fade_out_sec))
        result = await music_dsp.render_transform(transform, source, work, run, native=native)
        resolved[key] = music_dsp.ResolvedDspSource(source.path, source.source, source.sha256, RATE, 2,
            source.duration_frames, metrics=result.input_metrics)
        transforms.append(result)
        basename = 'placement-' + hashlib.sha256(item.id.encode('ascii')).hexdigest()[:32]
        final = output / (basename + '.wav')
        await run_owned_thread(shutil.copyfile, work / result.artifact_name, final)
        artifact = await _artifact(final, 'placement', source=item.source, stem=item.stem, placement_id=item.id, frames=target_frames)
        artifacts.append(artifact)
        dry_output = output / (basename + '-dry.wav')
        # The shared DSP finalizer moves the dry file when it is also the
        # identity output. Retain a separate, explicit source-grid crop rather
        # than relying on a private intermediate filename surviving effects.
        await run([tool('ffmpeg'), '-v', 'error', '-nostdin', '-n', *INPUT, '-i', str(source.path),
            '-map', '0:a:0', '-vn', '-af', f'atrim=start_sample={item.source_start_frame}:end_sample={item.source_end_frame},asetpts=PTS-STARTPTS',
            '-c:a', 'pcm_f32le', '-fs', str((item.source_end_frame - item.source_start_frame) * 8 + 1048576), str(dry_output)], 3600)
        dry_frames, _ = await run_owned_thread(_pcm, dry_output)
        if dry_frames != item.source_end_frame - item.source_start_frame:
            raise MashupError('mashup_source_changed')
        artifacts.append(await _artifact(dry_output, 'dry', source=item.source, stem=item.stem, placement_id=item.id,
            frames=item.source_end_frame - item.source_start_frame))
        artifact_ref = DspArtifactRef(id=result.id, revision=state.revision, sha256=artifact.sha256)
        processed = music_dsp.ResolvedDspSource(final, artifact_ref, artifact.sha256, RATE, 2, target_frames, result.output_metrics)
        mix_input = DspMixInput(id=uuid.uuid4().hex, source=artifact_ref, source_sha256=artifact.sha256,
            source_start_sec=selected_item.trim_start / RATE, start_sec=selected_item.start_frame / RATE,
            duration_sec=(selected_item.trim_end - selected_item.trim_start) / RATE)
        lane_inputs.setdefault((item.source, item.stem), []).append((mix_input, processed))
        source_span = item.source_end_frame - item.source_start_frame
        mappings.append(MashupRenderedPlacement(placement_id=item.id, artifact_id=artifact.id,
            source_start_frame=item.source_start_frame + round(selected_item.trim_start * source_span / target_frames),
            source_end_frame=item.source_start_frame + round(selected_item.trim_end * source_span / target_frames),
            target_start_frame=selected_item.start_frame,
            target_end_frame=selected_item.start_frame + selected_item.trim_end - selected_item.trim_start,
            artifact_start_frame=selected_item.trim_start))
        if progress is not None:
            progress(index + 1, len(selected_items))
    final_inputs: list[DspMixInput] = []
    final_sources: dict[str, music_dsp.ResolvedDspSource] = {}
    for (side, stem), inputs in lane_inputs.items():
        chunks: list[tuple[DspMixInput, music_dsp.ResolvedDspSource]] = []
        for offset in range(0, len(inputs), 32):
            group = inputs[offset:offset + 32]
            mixed = await music_dsp.render_mix(DspMixRequest(revision=state.revision, duration_sec=frames / RATE,
                inputs=[entry[0] for entry in group]), {entry[0].id: entry[1] for entry in group}, work, run)
            reference = DspArtifactRef(id=mixed.id, revision=state.revision, sha256=mixed.audio_sha256)
            resolved_chunk = music_dsp.ResolvedDspSource(work / mixed.artifact_name, reference, mixed.audio_sha256,
                RATE, 2, frames, mixed.output_metrics)
            chunks.append((DspMixInput(id=mixed.id, source=reference, source_sha256=mixed.audio_sha256,
                duration_sec=frames / RATE), resolved_chunk))
        if len(chunks) == 1:
            lane_source = chunks[0][1]
        else:
            mixed = await music_dsp.render_mix(DspMixRequest(revision=state.revision, duration_sec=frames / RATE,
                inputs=[entry[0] for entry in chunks]), {entry[0].id: entry[1] for entry in chunks}, work, run)
            reference = DspArtifactRef(id=mixed.id, revision=state.revision, sha256=mixed.audio_sha256)
            lane_source = music_dsp.ResolvedDspSource(work / mixed.artifact_name, reference, mixed.audio_sha256, RATE, 2, frames, mixed.output_metrics)
        filename = output / f'{side}-{stem}.wav'
        await run_owned_thread(shutil.copyfile, lane_source.path, filename)
        artifacts.append(await _artifact(filename, 'stem', source=side, stem=stem, frames=frames))
        identifier = uuid.uuid4().hex
        final_inputs.append(DspMixInput(id=identifier, source=lane_source.source,
            source_sha256=lane_source.sha256, duration_sec=frames / RATE))
        final_sources[identifier] = lane_source
    final_mix = await music_dsp.render_mix(DspMixRequest(revision=state.revision, duration_sec=frames / RATE,
        inputs=final_inputs), final_sources, work, run)
    mix_path = output / 'mix.wav'
    await run_owned_thread(shutil.copyfile, work / final_mix.artifact_name, mix_path)
    artifacts.append(await _artifact(mix_path, 'mix', frames=frames))
    evidence = shareable_music_evidence(merge_music_evidence([state.source_a.music_evidence, state.source_b.music_evidence, None]))
    warnings: list[MashupWarning] = list(plan.warnings)
    if any(source.association == 'operator_declared_unverified' for source in sources.values()):
        warnings.append('operator_stems_unverified')
    if evidence.restriction == 'unknown':
        warnings.append('rights_unknown')
    if final_mix.output_metrics.full_scale_fraction > 0 or (final_mix.output_metrics.true_peak_dbtp or -1) > 0:
        warnings.append('hard_clip_risk')
    plan_path = output / 'plan.json'
    await run_owned_thread(write_model, plan_path, plan)
    artifacts.append(await _artifact(plan_path, 'plan'))
    transforms_path = output / 'transforms.json'
    await run_owned_thread(write_object, transforms_path, _json.validate_python({'transforms': [item.model_dump(mode='json') for item in transforms]}))
    artifacts.append(await _artifact(transforms_path, 'plan'))
    provenance = ExportProvenance(export_id=job.id, subject='track_audio', created_at=job.created_at,
        artifact_sha256=final_mix.audio_sha256,
        content_origin='generated' if all(source.content_origin == 'generated' for source in sources.values()) else
            'recorded' if all(source.content_origin == 'recorded' for source in sources.values()) else
            'mixed' if any(source.content_origin in ('generated', 'mixed') for source in sources.values()) else 'unknown',
        components=[ProvenanceComponent(role='audio', content_origin=source.content_origin,
            source_id=f'track:{source.input.source.track_id}:version:{source.input.source.version_id}',
            source_sha256=source.input.sha256, music_evidence=shareable_music_evidence(source.music_evidence)) for source in sources.values()],
        transformations=['retained-excerpt-48000-stereo-v1', 'monotone-sections-v1', 'shared-dsp-v1'],
        measured_audio=final_mix.output_metrics, music_evidence=evidence)
    provenance_path = output / 'provenance.json'
    await run_owned_thread(write_model, provenance_path, provenance)
    artifacts.append(await _artifact(provenance_path, 'provenance'))
    archive = await run_owned_thread(_bundle, output, artifacts)
    artifacts.append(await _artifact(archive, 'bundle'))
    manifest = MashupRenderManifest(mashup_id=state.id, mashup_revision=state.revision, job_id=job.id,
        plan_sha256=hashlib.sha256(canonical_json(plan)).hexdigest(),
        source_a_sha256=state.source_a.input.sha256, source_b_sha256=state.source_b.input.sha256,
        mode=request.mode, frames=frames, start_tick=start_tick, artifacts=artifacts, placements=mappings,
        metrics=final_mix.output_metrics, warnings=list(dict.fromkeys(warnings)), music_evidence=evidence)
    await run_owned_thread(shutil.rmtree, work)
    await run_owned_thread(write_model, output / 'manifest.json', manifest)
    return manifest


def resolve_artifact(job_id: str, artifact: MashupRenderArtifact) -> Path:
    path = contained(job_dir(job_id) / 'output', artifact.filename)
    if path.stat().st_size != artifact.byte_count or _digest(path) != artifact.sha256:
        raise MashupError('mashup_source_changed')
    if artifact.media_type == 'audio/wav':
        frames, _ = _pcm(path)
        if frames != artifact.frames:
            raise MashupError('mashup_source_changed')
    return path


def validate_manifest(job_id: str) -> MashupRenderManifest:
    output = job_dir(job_id) / 'output'
    path = contained(output, 'manifest.json')
    if path.stat().st_size > 8 * 1024**2:
        raise MashupError('mashup_budget_exceeded')
    manifest = MashupRenderManifest.model_validate_json(path.read_bytes())
    if (manifest.job_id != job_id or len({item.id for item in manifest.artifacts}) != len(manifest.artifacts)
            or len({item.filename for item in manifest.artifacts}) != len(manifest.artifacts)):
        raise MashupError('mashup_source_changed')
    for item in manifest.artifacts:
        resolve_artifact(job_id, item)
    return manifest
