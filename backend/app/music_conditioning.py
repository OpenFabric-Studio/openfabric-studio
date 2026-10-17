"""Retain conditioned inputs for existing local-music jobs; no inference scheduler."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
import math
import os
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import numpy as np
from pydantic import Field
import soundfile as sf

from . import db
from .audio_quality import INPUT, RunCommand
from .export_provenance_contracts import MusicEvidence
from .job_lifecycle import run_owned_thread
from .module_catalog import ModuleEnvironment, available_bytes, configured_environment, supported
from .module_evidence import environment_verified
from .module_music_models import ModelArtifact, stable_generation_artifacts, stable_training_artifacts
from .music_conditioning_contracts import (ConditioningContract, ConditioningErrorCode, MusicConditioningCapabilities,
    ConditioningMode, MusicConditioningCapability, MusicConditioningPreview, MusicConditioningRecipe, MusicConditioningSource,
    MusicConditioningSourceInfo)
from .video_io import copy_verified, hash_file
from .video_media import tool
from .video_projects import VideoProjectError
from .queued_music_source_contracts import QueuedMusicSource

if TYPE_CHECKING:
    from .local_music_contracts import LocalMusicRequest

SOURCE_COMMIT = '77e0398c26f7d22d801ec1bb036307e8b231f741'
SOURCE_SCRIPT_SHA256 = '312c2833fac5ca270cbeebe7037b3fbeacd87b10c9b5d057587c5eaf824fb951'
MAX_SOURCE_BYTES = 512 * 1024**2
_RESERVE = 64 * 1024**2
_sources: dict[str, tuple[MusicConditioningSource, int | None]] = {}
_deleting: set[int] = set()
_replacing: set[int] = set()
_lock = asyncio.Lock()


@dataclass(frozen=True)
class ConditioningSourceProjection:
    path: Path
    track_id: int | None
    music_evidence: MusicEvidence | None


@dataclass(frozen=True)
class ConditioningRuntimeProof:
    script_sha256: str
    encoder_sha256: str


@dataclass(frozen=True)
class _Facts:
    rate: int
    channels: Literal[1, 2]
    frames: int
    size: int
    format: str
    subtype: str
    peak: float


class RetainedConditioning(ConditioningContract):
    original_name: str = Field(pattern=r'^conditioning-original\.(wav|flac|mp3|ogg)$', max_length=40)
    recipe: MusicConditioningRecipe
    source_music_evidence: MusicEvidence | None = None


def _error(code: ConditioningErrorCode, status: int = 409) -> Exception:
    from .local_music_contracts import LocalMusicError
    return LocalMusicError(code, status)


def _resolve(source: MusicConditioningSource) -> ConditioningSourceProjection:
    from fastapi import HTTPException
    from .artist_production import ProductionError, asset_path, get_asset
    from .audio_versions import resolve_source
    from .audio_exports import stem_source
    from .beat_contracts import BeatError
    from .music_evidence import track_music_evidence
    try:
        if source.kind == 'version':
            path, track_id = resolve_source(source.track_id, source.version_id), source.track_id
        elif source.kind == 'stem':
            path, track_id = stem_source(source.track_id, source.stem), source.track_id
        elif source.kind == 'production_asset':
            if get_asset(source.asset_id).sha256 != source.sha256:
                raise _error('conditioning_source_changed')
            path, track_id = asset_path(source.asset_id), None
        else:
            from .beat_jobs import render_source_metadata
            rendered = render_source_metadata(source.beat_id, source.beat_revision, source.render_id, source.sha256)
            path, track_id = rendered.path, rendered.track_id
        if (path.is_symlink() or path.resolve() != path or not path.is_file()
                or not path.is_relative_to(db.FILES_DIR.resolve())):
            raise _error('conditioning_source_missing', 404)
        return ConditioningSourceProjection(path, track_id, track_music_evidence(track_id) if track_id is not None else None)
    except (OSError, HTTPException, BeatError, ProductionError) as error:
        raise _error('conditioning_source_missing', 404) from error


def _facts(path: Path, *, peak_limit: float = 16.0, max_seconds: int = 380) -> _Facts:
    try:
        size = path.stat().st_size
        info = sf.info(path)
        if (path.is_symlink() or not 0 < size <= MAX_SOURCE_BYTES or info.samplerate < 8000 or info.samplerate > 192000
                or info.channels not in (1, 2) or not 0 < info.frames <= info.samplerate * max_seconds):
            raise _error('conditioning_source_invalid', 422)
        peak, count = 0.0, 0
        with sf.SoundFile(path) as audio:
            while len(block := audio.read(65536, dtype='float32', always_2d=True)):
                count += len(block)
                if count > info.frames or not np.all(np.isfinite(block)):
                    raise _error('conditioning_source_invalid', 422)
                peak = max(peak, float(np.max(np.abs(block))))
        if count != info.frames or peak > peak_limit:
            raise _error('conditioning_source_invalid', 422)
        channels: Literal[1, 2] = 1 if info.channels == 1 else 2
        return _Facts(info.samplerate, channels, info.frames, size, info.format, info.subtype, peak)
    except (OSError, RuntimeError, ValueError) as error:
        raise _error('conditioning_source_invalid', 422) from error


def _validate_region(request: LocalMusicRequest, facts: _Facts) -> None:
    options = request.conditioning
    if options is None:
        raise _error('conditioning_invalid_request', 422)
    if options.mode != 'inpaint':
        return
    if facts.format not in ('WAV', 'WAVEX') or facts.subtype not in ('PCM_16', 'PCM_24', 'FLOAT') or facts.rate not in (44100, 48000):
        raise _error('conditioning_inpaint_format_unsupported', 422)
    if request.duration_seconds * facts.rate != facts.frames:
        raise _error('conditioning_inpaint_duration_mismatch', 422)
    start, end = round(options.start_sec * facts.rate), round(options.end_sec * facts.rate)
    if (not 0 <= start < end <= facts.frames or round(options.crossfade_sec * facts.rate) * 2 > end - start
            or round(options.start_sec * 44100 / 4096) >= round(options.end_sec * 44100 / 4096)):
        raise _error('conditioning_invalid_region', 422)


def _budget(request: LocalMusicRequest, size: int) -> int:
    return (request.duration_seconds + 10) * 192000 * 2 * 4 * 2 + size + request.duration_seconds * 44100 * 4 + _RESERVE


def _encoder(variant: Literal['small-music', 'medium']) -> ModelArtifact:
    suffix = 'same_l_encoder_f32.npz' if variant == 'medium' else 'same_s_encoder_f32.npz'
    return next(item for item in stable_training_artifacts(variant) if item.path.endswith('/' + suffix))


def conditioning_weights_present(environment: ModuleEnvironment, variant: Literal['small-music', 'medium']) -> bool:
    try:
        item = _encoder(variant)
        path = environment.paths['stable_audio3'] / item.path
        return not path.is_symlink() and path.is_file() and path.stat().st_size == item.bytes
    except (OSError, ValueError):
        return False


async def verify_runtime(variant: Literal['small-music', 'medium']) -> ConditioningRuntimeProof:
    env = configured_environment()
    if not supported('stable_audio3', env) or not environment_verified(env, 'stable_audio3'):
        raise _error('conditioning_runtime_unavailable')
    root = env.paths['stable_audio3'].resolve()
    script = root / 'optimized/mlx/scripts/sa3_mlx.py'
    try:
        if script.is_symlink() or not script.is_file() or await hash_file(script, max_bytes=1024**2) != SOURCE_SCRIPT_SHA256:
            raise _error('conditioning_runtime_changed')
    except (OSError, VideoProjectError) as error:
        raise _error('conditioning_runtime_changed') from error
    encoder = _encoder(variant)
    for item in [*stable_generation_artifacts(variant), encoder]:
        path = root / item.path
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
                raise _error('conditioning_encoder_missing' if item == encoder else 'conditioning_model_changed')
            if path.stat().st_size != item.bytes or await hash_file(path, max_bytes=item.bytes) != item.sha256:
                raise _error('conditioning_model_changed')
        except (OSError, VideoProjectError) as error:
            raise _error('conditioning_model_changed') from error
    return ConditioningRuntimeProof(SOURCE_SCRIPT_SHA256, encoder.sha256)


async def register(identifier: str, source: MusicConditioningSource) -> None:
    projection = _resolve(source)
    async with _lock:
        if projection.track_id in _deleting or source.kind == 'stem' and projection.track_id in _replacing:
            raise _error('conditioning_source_busy')
        _sources[identifier] = (source, projection.track_id)


def release(identifier: str) -> None:
    _sources.pop(identifier, None)


@asynccontextmanager
async def _protect(track_id: int, *, stems_only: bool) -> AsyncIterator[None]:
    marker = _replacing if stems_only else _deleting
    async with _lock:
        if track_id in marker:
            raise _error('conditioning_source_busy')
        marker.add(track_id)
        identifiers = [identifier for identifier, (source, parent) in _sources.items()
            if parent == track_id and (not stems_only or source.kind == 'stem')]
    try:
        from .local_music import cancel
        for identifier in identifiers:
            await cancel(identifier)
        if any(identifier in _sources for identifier in identifiers):
            raise _error('conditioning_source_busy')
        yield
    finally:
        marker.discard(track_id)


def protect_source_removal(track_id: int) -> AbstractAsyncContextManager[None]:
    return _protect(track_id, stems_only=False)


def protect_stem_replacement(track_id: int) -> AbstractAsyncContextManager[None]:
    return _protect(track_id, stems_only=True)


async def prepare(request: LocalMusicRequest, workspace: Path, *, run: RunCommand,
                  proof: ConditioningRuntimeProof, retained_input: QueuedMusicSource | None = None) -> RetainedConditioning:
    options = request.conditioning
    if options is None or workspace.is_symlink() or workspace.resolve() != workspace or not workspace.is_dir():
        raise _error('conditioning_invalid_request', 422)
    async def projection() -> ConditioningSourceProjection:
        if retained_input is None:
            return _resolve(options.source)
        from .queued_music_sources import resolve
        from .music_queue_contracts import MusicQueueError
        try:
            path = await resolve(retained_input, options)
        except MusicQueueError as error:
            if error.code == 'music_queue_cleanup_failed':
                from .local_music_contracts import LocalMusicError
                raise LocalMusicError('local_music_worker_unverified') from error
            raise _error('conditioning_source_changed') from error
        return ConditioningSourceProjection(path, retained_input.snapshot.track_id, retained_input.snapshot.music_evidence)
    source = await projection()
    facts = await run_owned_thread(_facts, source.path)
    _validate_region(request, facts)
    free = available_bytes(workspace)
    if free is None or free < _budget(request, facts.size):
        raise _error('insufficient_disk')
    original = workspace / ('conditioning-original' + source.path.suffix.lower())
    if original.name not in ('conditioning-original.wav', 'conditioning-original.flac', 'conditioning-original.mp3', 'conditioning-original.ogg'):
        raise _error('conditioning_source_invalid', 422)
    prepared = workspace / 'conditioning.wav'
    resampled = workspace / 'conditioning-resampled.wav'
    if any(path.exists() or path.is_symlink() for path in (original, prepared, resampled)):
        raise _error('conditioning_snapshot_invalid')
    try:
        if await hash_file(source.path, max_bytes=MAX_SOURCE_BYTES) != options.source_sha256:
            raise _error('conditioning_source_changed')
        await copy_verified(source.path, original, options.source_sha256, max_bytes=MAX_SOURCE_BYTES)
        if (await projection()).path != source.path or await hash_file(source.path, max_bytes=MAX_SOURCE_BYTES) != options.source_sha256:
            raise _error('conditioning_source_changed')
    except VideoProjectError as error:
        raise _error('conditioning_source_changed') from error
    with original.open('rb') as handle:
        os.fsync(handle.fileno())
    frames = request.duration_seconds * 44100
    await run([tool('ffmpeg'), '-v', 'error', '-nostdin', '-xerror', *INPUT, '-i', str(original),
        '-map', '0:a:0', '-vn', '-af', f'aresample=44100,apad=whole_len={frames},atrim=end_sample={frames}',
        '-ar', '44100', '-ac', '2', '-c:a', 'pcm_f32le', '-fs', str(frames * 8 + 4096), str(resampled)], 120.0)
    resampled_facts = await run_owned_thread(_facts, resampled, peak_limit=128.0)
    if (resampled_facts.rate, resampled_facts.channels, resampled_facts.frames, resampled_facts.subtype) != (44100, 2, frames, 'FLOAT'):
        raise _error('conditioning_snapshot_invalid')
    gain = min(1.0, .99 / resampled_facts.peak) if resampled_facts.peak else 1.0
    await run([tool('ffmpeg'), '-v', 'error', '-nostdin', '-xerror', *INPUT, '-i', str(resampled),
        '-map', '0:a:0', '-vn', '-af', f'volume={gain:.17g}:precision=double',
        '-ar', '44100', '-ac', '2', '-c:a', 'pcm_s16le', '-fs', str(frames * 4 + 4096), str(prepared)], 120.0)
    prepared_facts = await run_owned_thread(_facts, prepared)
    if (prepared_facts.rate, prepared_facts.channels, prepared_facts.frames, prepared_facts.subtype) != (44100, 2, frames, 'PCM_16'):
        raise _error('conditioning_snapshot_invalid')
    with prepared.open('rb') as handle:
        os.fsync(handle.fileno())
    recipe = MusicConditioningRecipe(source_sha256=options.source_sha256,
        prepared_sha256=await hash_file(prepared, max_bytes=MAX_SOURCE_BYTES), source_rate_hz=facts.rate,
        source_channels=facts.channels, source_frames=facts.frames, prepared_frames=frames,
        prepared_gain_db=20 * math.log10(gain), mode=options.mode,
        noise_level=options.noise_level if options.mode == 'audio_to_audio' else None,
        region_start_frame=round(options.start_sec * facts.rate) if options.mode == 'inpaint' else None,
        region_end_frame=round(options.end_sec * facts.rate) if options.mode == 'inpaint' else None,
        crossfade_frames=round(options.crossfade_sec * facts.rate) if options.mode == 'inpaint' else 0,
        source_commit=SOURCE_COMMIT, source_script_sha256=proof.script_sha256, encoder_sha256=proof.encoder_sha256,
        output_semantics='original_pcm_outside_region' if options.mode == 'inpaint' else 'generated_full_mix')
    return RetainedConditioning(original_name=original.name, recipe=recipe, source_music_evidence=source.music_evidence)


async def preview(request: LocalMusicRequest, *, retained_input: QueuedMusicSource | None = None) -> MusicConditioningPreview:
    if request.conditioning is None:
        raise _error('conditioning_invalid_request', 422)
    if retained_input is None:
        source = _resolve(request.conditioning.source)
    else:
        from .queued_music_sources import resolve
        from .music_queue_contracts import MusicQueueError
        try:
            path = await resolve(retained_input, request.conditioning)
        except MusicQueueError as error:
            if error.code == 'music_queue_cleanup_failed':
                from .local_music_contracts import LocalMusicError
                raise LocalMusicError('local_music_worker_unverified') from error
            raise _error('conditioning_source_changed') from error
        source = ConditioningSourceProjection(path, retained_input.snapshot.track_id, retained_input.snapshot.music_evidence)
    facts = await run_owned_thread(_facts, source.path)
    _validate_region(request, facts)
    try:
        if await hash_file(source.path, max_bytes=MAX_SOURCE_BYTES) != request.conditioning.source_sha256:
            raise _error('conditioning_source_changed')
    except (OSError, VideoProjectError) as error:
        raise _error('conditioning_source_changed') from error
    required, free = _budget(request, facts.size), available_bytes(db.DATA_DIR)
    code: ConditioningErrorCode | None = None
    if free is None or free < required:
        code = 'insufficient_disk'
    else:
        from .local_music_contracts import LocalMusicError
        try:
            await verify_runtime(request.variant)
        except LocalMusicError as error:
            codes: dict[str, ConditioningErrorCode] = {'conditioning_encoder_missing': 'conditioning_encoder_missing',
                'conditioning_model_changed': 'conditioning_model_changed', 'conditioning_runtime_changed': 'conditioning_runtime_changed'}
            code = codes.get(error.code, 'conditioning_runtime_unavailable')
    return MusicConditioningPreview(source=request.conditioning.source,
        source_sha256=request.conditioning.source_sha256, source_bytes=facts.size, source_rate_hz=facts.rate,
        source_channels=facts.channels, source_frames=facts.frames, duration_sec=facts.frames / facts.rate,
        required_free_bytes=required, free_bytes=min(free, 9007199254740991) if free is not None else None,
        can_start=code is None, error_code=code)


def _source_signature(path: Path) -> tuple[int, int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


async def inspect_source(source: MusicConditioningSource) -> MusicConditioningSourceInfo:
    """Read current retained bytes; cancellation drains both header and hash readers."""
    try:
        projection = _resolve(source)
        before = _source_signature(projection.path)
        facts = await run_owned_thread(_facts, projection.path, max_seconds=7200)
        digest = await hash_file(projection.path, max_bytes=MAX_SOURCE_BYTES)
        if (_resolve(source).path != projection.path or _source_signature(projection.path) != before
                or source.kind != 'version' and source.sha256 != digest):
            raise _error('conditioning_source_changed')
        return MusicConditioningSourceInfo(source=source, source_sha256=digest, source_bytes=facts.size,
            source_rate_hz=facts.rate, source_channels=facts.channels, source_frames=facts.frames,
            duration_sec=facts.frames / facts.rate)
    except (OSError, VideoProjectError) as error:
        raise _error('conditioning_source_changed') from error


def capabilities() -> MusicConditioningCapabilities:
    from .local_music_runtime import engine_status
    state = engine_status().engines[0]
    small = state.supported and state.installed and state.small_weights_ready and state.small_conditioning_weights_ready
    medium = state.supported and state.installed and state.medium_weights_ready and state.medium_conditioning_weights_ready
    modes: tuple[ConditioningMode, ...] = ('audio_to_audio', 'inpaint')
    rows = [MusicConditioningCapability(engine='stable_audio3', mode=mode, proof_status='source_verified',
        enabled_small=small, enabled_medium=medium, source_commit=SOURCE_COMMIT, proof_sha256=SOURCE_SCRIPT_SHA256,
        output_semantics='original_pcm_outside_region' if mode == 'inpaint' else 'generated_full_mix',
        reason=None if small or medium else 'conditioning_encoder_missing',
        notes='Pinned MLX source encodes init audio. Runtime quality is unverified; preflight checks exact model/encoder hashes. '
            'Audio-to-audio rewrites the mixture; it does not provide isolated additions or guaranteed harmonic control. '
            'Inpaint accepts matching-duration PCM16/24/FLOAT WAV at44.1/48k and splices original PCM outside the reviewed region.')
        for mode in modes]
    from .song_capabilities import capabilities as ace_capabilities
    for ace in ace_capabilities().modes:
        rows.append(MusicConditioningCapability(engine='ace_step', mode=ace.mode, proof_status=ace.proof_status,
            enabled_small=False, enabled_medium=False, source_commit=ace.source_commit, proof_sha256=ace.proof_sha256,
            output_semantics=ace.output_semantics, reason=ace.reason,
            notes='Uses the separately reviewed ACE base model through the typed music queue; Small/Medium selectors apply only to Stable Audio. ' + ace.notes))
    disabled: tuple[tuple[Literal['ace_step', 'yue2'], Literal['ace_cover', 'yue_audio_conditioning']], ...] = (
        ('ace_step', 'ace_cover'), ('yue2', 'yue_audio_conditioning'))
    for engine, disabled_mode in disabled:
        rows.append(MusicConditioningCapability(engine=engine, mode=disabled_mode, proof_status='unverified',
            enabled_small=False, enabled_medium=False, output_semantics='unverified', reason='conditioning_task_unsupported',
            notes='This retained-source workflow does not yet implement or verify this native adapter/output contract. No fallback is submitted.'))
    return MusicConditioningCapabilities(modes=rows)
