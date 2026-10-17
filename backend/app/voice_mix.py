"""Measured conversion mixing within the existing voice-build owner.

All commands use its drained callback. Immutable artifacts and typed reports
live beside the audio version, independently of the ephemeral apply workspace.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
from typing import Literal
import uuid

from pydantic import TypeAdapter, ValidationError
import soundfile as sf

from . import artist_production as evidence, db
from .artist_production_contracts import ModelFileIdentity, SourceAsset, VoiceModelSnapshot
from .atomic_files import document_lock, write_object
from .audio_quality import RunCommand
from .contracts import JsonObject
from .job_lifecycle import run_owned_thread
from .music_analysis_contracts import ProductionMusicSource, VersionMusicSource
from .music_dsp import (MAX_SOURCE_BYTES, MusicDspError, ResolvedDspSource, _disk, _metrics, _pcm_bytes,
    level_match_gain, render_mix, render_transform)
from .music_dsp_contracts import DspMixInput, DspMixRequest, DspTransformRequest, DspWarning, WarpAnchor
from .video_io import copy_verified, hash_file
from .video_projects import VideoProjectError
from .voice_mix_contracts import VoiceMixArtifact, VoiceMixReport, VoiceMixSettings
from .voice_artifacts import ModelArtifact

_json = TypeAdapter(JsonObject)


async def _hash(path: Path) -> str:
    try:
        return await hash_file(path, max_bytes=MAX_SOURCE_BYTES)
    except VideoProjectError as error:
        raise MusicDspError('dsp_source_changed') from error


ModelRole = Literal['checkpoint', 'configuration', 'voice_reference', 'engine_source']


@dataclass(frozen=True)
class PinnedConversionFile:
    path: Path
    role: ModelRole
    signature: tuple[int, int, int, int, int]


@dataclass(frozen=True)
class PinnedConversion:
    voice_id: str
    model_revision_id: str
    training_steps: int
    diffusion_steps: int
    files: tuple[PinnedConversionFile, ...]


def _signature(path: Path) -> tuple[int, int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def pin_conversion(artifact: ModelArtifact, voice_id: str, model_revision_id: str,
                   engine_source: Path, diffusion_steps: int) -> PinnedConversion:
    if artifact.checkpoint is None or artifact.config is None:
        raise MusicDspError('dsp_runtime_unverified')
    files: list[PinnedConversionFile] = []
    for path, role in ((artifact.checkpoint, 'checkpoint'), (artifact.config, 'configuration'),
            (artifact.reference, 'voice_reference'), (engine_source, 'engine_source')):
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_SOURCE_BYTES:
            raise MusicDspError('dsp_runtime_unverified')
        parsed_role: ModelRole = TypeAdapter(ModelRole).validate_python(role)
        files.append(PinnedConversionFile(path.resolve(), parsed_role, _signature(path)))
    return PinnedConversion(voice_id, model_revision_id, artifact.steps, diffusion_steps, tuple(files))


async def observe_conversion(pinned: PinnedConversion) -> VoiceModelSnapshot:
    files: list[ModelFileIdentity] = []
    for item in pinned.files:
        if item.path.is_symlink() or _signature(item.path) != item.signature:
            raise MusicDspError('dsp_source_changed')
        digest = await _hash(item.path)
        if _signature(item.path) != item.signature:
            raise MusicDspError('dsp_source_changed')
        files.append(ModelFileIdentity(role=item.role, name=item.path.name, sha256=digest))
    return VoiceModelSnapshot(voice_id=pinned.voice_id, model_revision_id=pinned.model_revision_id,
        training_steps=pinned.training_steps, diffusion_steps=pinned.diffusion_steps, files=files,
        software_version='OpenFabric measured conversion v1')


def _root(destination: Path, version_id: str, *, create: bool = False) -> Path:
    if re.fullmatch(r'[0-9a-f]{32}', version_id) is None or destination.name != version_id + '.voice.wav':
        raise MusicDspError('dsp_invalid_source')
    root = destination.parent / (version_id + '.mix')
    if (destination.resolve() != destination or root.resolve() != root
            or not root.is_relative_to(db.FILES_DIR.resolve()) or root.is_symlink()):
        raise MusicDspError('dsp_invalid_source')
    if create:
        root.mkdir(exist_ok=True)
    return root


def read_settings(destination: Path, version_id: str) -> VoiceMixSettings | None:
    path = _root(destination, version_id) / 'settings.json'
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 16384:
        raise MusicDspError('dsp_invalid_source')
    try:
        return VoiceMixSettings.model_validate_json(path.read_bytes())
    except (ValidationError, OSError) as error:
        raise MusicDspError('dsp_invalid_source') from error


def configure(destination: Path, version_id: str, settings: VoiceMixSettings) -> None:
    path = _root(destination, version_id, create=True) / 'settings.json'
    with document_lock(path):
        current = read_settings(destination, version_id)
        if current is not None and current != settings:
            raise MusicDspError('dsp_source_changed')
        if current is None:
            write_object(path, _json.validate_json(settings.model_dump_json()))


def read_conversion(destination: Path, version_id: str) -> VoiceModelSnapshot | None:
    path = _root(destination, version_id) / 'conversion.json'
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 512 * 1024:
        raise MusicDspError('dsp_runtime_unverified')
    try:
        return VoiceModelSnapshot.model_validate_json(path.read_bytes(), strict=True)
    except (ValidationError, OSError) as error:
        raise MusicDspError('dsp_runtime_unverified') from error


def configure_conversion(destination: Path, version_id: str, snapshot: VoiceModelSnapshot) -> None:
    path = _root(destination, version_id, create=True) / 'conversion.json'
    with document_lock(path):
        current = read_conversion(destination, version_id)
        if current is not None and current != snapshot:
            raise MusicDspError('dsp_source_changed')
        if current is None:
            write_object(path, _json.validate_json(snapshot.model_dump_json()))


def read_report(destination: Path, version_id: str) -> VoiceMixReport | None:
    path = _root(destination, version_id) / 'current.json'
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 512 * 1024:
        raise MusicDspError('dsp_output_invalid')
    try:
        report = VoiceMixReport.model_validate_json(path.read_bytes())
    except (ValidationError, OSError) as error:
        raise MusicDspError('dsp_output_invalid') from error
    if (report.audio_version_id != version_id or report.settings != read_settings(destination, version_id)
            or report.conversion != read_conversion(destination, version_id)):
        raise MusicDspError('dsp_output_invalid')
    from .audio_version_store import get
    record = get(db.get_db(), version_id)
    if (record is None or record.path != destination or record.public.kind != 'voice'
            or report.source.track_id != record.public.track_id
            or report.source.version_id != record.public.source_version_id):
        raise MusicDspError('dsp_output_invalid')
    return report


async def artifact_path(destination: Path, version_id: str, artifact_id: str) -> Path:
    report = read_report(destination, version_id)
    if report is None:
        raise MusicDspError('dsp_output_invalid')
    artifact = next((item for item in report.artifacts if item.id == artifact_id), None)
    if artifact is None:
        raise MusicDspError('dsp_output_invalid')
    path = _root(destination, version_id) / report.id / (artifact.id + '.wav')
    if path.resolve() != path or path.is_symlink() or not path.is_file():
        raise MusicDspError('dsp_output_invalid')
    if await _hash(path) != artifact.sha256:
        raise MusicDspError('dsp_source_changed')
    return path


async def _audio(path: Path, asset: SourceAsset) -> ResolvedDspSource:
    try:
        info = await run_owned_thread(sf.info, path)
    except (OSError, RuntimeError) as error:
        raise MusicDspError('dsp_invalid_source') from error
    if (path.resolve() != path or path.is_symlink() or not path.is_file()
            or not 8000 <= info.samplerate <= 192000 or info.channels not in {1, 2}
            or not 0 < info.frames <= info.samplerate * 7200):
        raise MusicDspError('dsp_invalid_source')
    return ResolvedDspSource(path, ProductionMusicSource(asset_id=asset.id, sha256=asset.sha256),
        asset.sha256, info.samplerate, info.channels, info.frames)


def _artifact(identifier: str, role: Literal['original_vocal', 'converted_dry', 'backing', 'matched_vocal', 'master'],
              audio: ResolvedDspSource, asset: SourceAsset, source: VersionMusicSource, version_id: str) -> VoiceMixArtifact:
    if audio.channels == 1:
        channels: Literal[1, 2] = 1
    elif audio.channels == 2:
        channels = 2
    else:
        raise MusicDspError('dsp_invalid_source')
    return VoiceMixArtifact(id=identifier, role=role, sha256=audio.sha256, evidence_asset_id=asset.id,
        sample_rate_hz=audio.sample_rate_hz, channels=channels, duration_frames=audio.duration_frames,
        audio_url=f'/api/tracks/{source.track_id}/versions/{version_id}/mix/artifacts/{identifier}')


def _sync_artifacts(root: Path, artifacts: list[VoiceMixArtifact]) -> None:
    for artifact in artifacts:
        with (root / (artifact.id + '.wav')).open('rb') as stream:
            os.fsync(stream.fileno())


async def render_converted_mix(settings: VoiceMixSettings, source: VersionMusicSource, source_path: Path,
                               reference_vocal: Path, converted_vocal: Path, backing_paths: list[Path],
                               version_id: str, destination: Path, conversion: VoiceModelSnapshot,
                               run: RunCommand) -> VoiceMixReport:
    if not 1 <= len(backing_paths) <= 3:
        raise MusicDspError('dsp_invalid_source')
    original_paths = [reference_vocal, converted_vocal, *backing_paths]
    for path in [source_path, *original_paths]:
        if path.resolve() != path or path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
            raise MusicDspError('dsp_invalid_source')
    try:
        infos = [await run_owned_thread(sf.info, path) for path in original_paths]
    except (OSError, RuntimeError) as error:
        raise MusicDspError('dsp_invalid_source') from error
    if any(info.channels not in {1, 2} or not 8000 <= info.samplerate <= 192000
            or not 0 < info.frames <= info.samplerate * 7200 for info in infos):
        raise MusicDspError('dsp_invalid_source')
    maximum = max(_pcm_bytes(info.frames, max(2, info.channels)) for info in infos)
    root = _root(destination, version_id, create=True)
    _disk(root, sum(path.stat().st_size for path in original_paths) + 4 * maximum)
    configure(destination, version_id, settings)
    configure_conversion(destination, version_id, conversion)
    identifier = uuid.uuid4().hex
    work = root / identifier
    await run_owned_thread(work.mkdir)
    source_digest = await _hash(source_path)
    parent = await run_owned_thread(evidence.register_version_reference, source.track_id, source.version_id, source_digest)
    retained: list[ResolvedDspSource] = []
    artifacts: list[VoiceMixArtifact] = []
    for index, path in enumerate(original_paths):
        role: Literal['original_vocal', 'converted_dry', 'backing'] = 'original_vocal' if index == 0 else 'converted_dry' if index == 1 else 'backing'
        artifact_id = uuid.uuid4().hex
        copy = work / (artifact_id + '.wav')
        digest = await _hash(path)
        try:
            await copy_verified(path, copy, digest, max_bytes=path.stat().st_size)
        except VideoProjectError as error:
            raise MusicDspError('dsp_source_changed') from error
        parents = [artifacts[0].evidence_asset_id] if index == 1 else [parent.id]
        asset = await run_owned_thread(evidence.create_transform, copy, 'voice_converted' if index == 1 else 'stem',
            parents, 'Seed-VC converted dry vocal; observed model files recorded in voice mix report' if index == 1
            else 'Retained separator output from exact library source version; no rights upgrade')
        audio = await _audio(copy, asset)
        audio = replace(audio, metrics=await _metrics(audio, work, run, artifact_id))
        retained.append(audio)
        artifacts.append(_artifact(artifact_id, role, audio, asset, source, version_id))
    reference, converted, *backing = retained
    if reference.metrics is None or converted.metrics is None:
        raise MusicDspError('dsp_invalid_source')
    expected = reference.duration_frames / reference.sample_rate_hz
    converted_seconds = converted.duration_frames / converted.sample_rate_hz
    if abs(converted_seconds - expected) > max(.25, .01 * expected):
        raise MusicDspError('dsp_output_invalid')
    match = level_match_gain(reference.metrics, converted.metrics)
    gain = match.gain_db if settings.vocal_level == 'match_original' else settings.manual_gain_db
    warnings: list[DspWarning] = list(match.warnings) if settings.vocal_level == 'match_original' else []
    if not math.isclose(converted_seconds, expected, abs_tol=1 / reference.sample_rate_hz, rel_tol=0):
        warnings.append('anchor_boundary_uncertain')
    if converted.source.kind != 'production_asset':
        raise MusicDspError('dsp_invalid_source')
    transform = await render_transform(DspTransformRequest(source=converted.source, source_sha256=converted.sha256,
        revision=0, anchors=[WarpAnchor(source_sec=0.0, target_sec=0.0),
            WarpAnchor(source_sec=converted_seconds, target_sec=converted_seconds)], output_duration_sec=converted_seconds,
        gain_db=gain, eq_bands=settings.eq_bands, compression=settings.compression), converted, work, run)
    matched_path = work / transform.artifact_name
    matched_asset = await run_owned_thread(evidence.create_transform, matched_path, 'stem',
        [artifacts[1].evidence_asset_id], 'Measured vocal level/effects; configuration ' + transform.configuration_sha256)
    matched = replace(await _audio(matched_path, matched_asset), metrics=transform.output_metrics)
    artifacts.append(_artifact(transform.id, 'matched_vocal', matched, matched_asset, source, version_id))
    mix_sources: dict[str, ResolvedDspSource] = {}
    inputs: list[DspMixInput] = []
    for audio in [matched, *backing]:
        item_id = uuid.uuid4().hex
        inputs.append(DspMixInput(id=item_id, source=audio.source, source_sha256=audio.sha256,
            duration_sec=min(expected, audio.duration_frames / audio.sample_rate_hz)))
        mix_sources[item_id] = audio
    rate: Literal[44100, 48000] = 44100 if reference.sample_rate_hz == 44100 else 48000
    mix = await render_mix(DspMixRequest(revision=0, sample_rate_hz=rate, channels=2, duration_sec=expected,
        inputs=inputs, loudness=settings.loudness), mix_sources, work, run)
    master_path = work / mix.artifact_name
    master_asset = await run_owned_thread(evidence.create_transform, master_path, 'master',
        [matched_asset.id, *(item.evidence_asset_id for item in artifacts if item.role == 'backing')],
        'Measured converted-vocal mix; configuration ' + mix.configuration_sha256)
    master = replace(await _audio(master_path, master_asset), metrics=mix.output_metrics)
    artifacts.append(_artifact(mix.id, 'master', master, master_asset, source, version_id))
    if await _hash(source_path) != source_digest:
        raise MusicDspError('dsp_source_changed')
    report = VoiceMixReport(id=identifier, audio_version_id=version_id, source=source, source_sha256=source_digest,
        settings=settings, conversion=conversion, reference_metrics=reference.metrics, converted_metrics=converted.metrics,
        applied_gain_db=gain, residual_lu=match.residual_lu if settings.vocal_level == 'match_original' else None,
        warnings=sorted(set([*warnings, *transform.warnings, *mix.warnings])), transform=transform, mix=mix,
        artifacts=artifacts, created_at=datetime.now(timezone.utc).isoformat())
    await run_owned_thread(_sync_artifacts, work, artifacts)
    await run_owned_thread(write_object, work / 'report.json', _json.validate_json(report.model_dump_json()))
    await run_owned_thread(write_object, root / 'current.json', _json.validate_json(report.model_dump_json()))
    return report
