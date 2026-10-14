"""Durable local CPU exports of immutable track versions.

Each export owns a private directory, captured profile, supervised children and
an atomic publication. No browser connection owns the job or its source.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import shutil
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from fastapi import HTTPException

from . import db
from . import audio_quality, export_provenance
from .atomic_files import write_object
from .audio_encoding import (
    AudioEncodingSettings,
    AudioExportFormat,
    AudioExportResponse,
    AudioExportsResponse,
    StemName,
    StemAudioExportResponse,
    StemAudioExportsResponse,
    encoding_args,
    load_settings,
)
from .audio_version_contracts import AudioVersionId
from .contracts import JsonObject
from .artist_contracts import ArtistConsentVersion
from .artist_production_contracts import ProductionProvenance
from .job_lifecycle import await_cleanup, kill_process_tree, request_cancel
from .video_media import VideoMediaError, tool
from .video_process import (
    WorkerIdentity,
    read_owned_output,
    spawn_owned,
    terminate_verified,
)

logger = logging.getLogger(__name__)
_json = TypeAdapter(JsonObject)
_id = TypeAdapter(AudioVersionId)
_lock = asyncio.Lock()
_tasks: dict[str, asyncio.Task[None]] = {}
_running: dict[str, ExportDocument] = {}
_processes: dict[str, asyncio.subprocess.Process] = {}
_deleting: set[int] = set()
_stem_deleting: set[int] = set()
_stem_name: TypeAdapter[StemName] = TypeAdapter(StemName)
_stem_paths = TypeAdapter(dict[StemName, str])
_unverified: set[str] = set()
_invalid: set[tuple[int, str]] = set()
_capacity = asyncio.Semaphore(2)
_INPUT = [
    "-protocol_whitelist",
    "file,pipe",
    "-format_whitelist",
    "wav,mp3,flac,ogg,mov",
]
_MAX_BYTES = 8 * 1024**3
_MAX_DOCUMENT_BYTES = export_provenance.MAX_MANIFEST_BYTES


class AudioExportError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class OutputIdentity(BaseModel):
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int


class ExportDocument(AudioExportResponse):
    schema_version: Literal[1] = 1
    source_relative: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_primary: str | None = None
    worker: WorkerIdentity | None = None
    output_identity: OutputIdentity | None = None
    output_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    stem_name: StemName | None = None
    artist_permission: ArtistConsentVersion | None = None
    artist_voice_id: str | None = None
    production_context: ProductionProvenance | None = None
    production_export_asset_id: str | None = None


class ProbeStream(BaseModel):
    codec_type: str = ""
    codec_name: str = ""
    sample_rate: str = "0"
    channels: int = 0
    bits_per_sample: int = 0
    bits_per_raw_sample: str = "0"
    bit_rate: str = "0"


class ProbeFormat(BaseModel):
    duration: str = "0"
    tags: dict[str,str] = Field(default_factory=dict)


class AudioProbe(BaseModel):
    streams: list[ProbeStream]
    format: ProbeFormat


def resolve_source(track_id: int, version_id: str) -> Path:
    from .audio_versions import resolve_source as resolve_version

    return resolve_version(track_id, version_id)


def _identifier(value: str) -> str:
    try:
        return _id.validate_python(value)
    except ValidationError as exc:
        raise AudioExportError("export_not_found") from exc


def _root(track_id: int) -> Path:
    if track_id < 1:
        raise AudioExportError("track_not_found")
    root = db.FILES_DIR.resolve()
    result = root / "_exports" / str(track_id)
    if result.resolve() != result:
        raise AudioExportError("export_not_found")
    return result


def _directory(track_id: int, identifier: str) -> Path:
    result = _root(track_id) / _identifier(identifier)
    if result.resolve() != result:
        raise AudioExportError("export_not_found")
    return result


def _artifact(document: ExportDocument, *, partial: bool = False) -> Path:
    result = _directory(document.track_id, document.id) / (
        f"partial.{document.format}" if partial else f"export.{document.format}"
    )
    if result.is_symlink() or result.resolve() != result:
        raise AudioExportError("export_not_found")
    return result


def load_document(track_id: int, identifier: str) -> ExportDocument:
    path = _directory(track_id, identifier) / "export.json"
    try:
        if path.is_symlink() or path.stat().st_size > _MAX_DOCUMENT_BYTES:
            raise ValueError("invalid document")
        document = ExportDocument.model_validate_json(path.read_bytes())
        if document.id != identifier or document.track_id != track_id:
            raise ValueError("incorrect identity")
        return document
    except (OSError, ValueError, ValidationError) as exc:
        raise AudioExportError("export_not_found") from exc


def save_document(document: ExportDocument) -> None:
    path = _directory(document.track_id, document.id) / "export.json"
    if path.is_symlink():
        raise AudioExportError("export_write_failed")
    payload = _json.validate_json(document.model_dump_json())
    if len(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')) > _MAX_DOCUMENT_BYTES:
        raise AudioExportError('export_record_capacity_exceeded')
    try:
        write_object(path, payload)
    except OSError as exc:
        raise AudioExportError("export_write_failed") from exc


def _manifest_url(document: ExportDocument,kind: Literal['provenance','manifest']) -> str:
    source=f'stems/{document.stem_name}' if document.stem_name is not None else f'versions/{document.version_id}'
    return f'/api/tracks/{document.track_id}/{source}/exports/{document.id}/{kind}'


def _response(document: ExportDocument) -> AudioExportResponse:
    # Explicit projection keeps paths/worker proof out of the public contract.
    available = document.status != "done" or _output_current(document)
    ready = document.status == "done" and available
    return AudioExportResponse(
        id=document.id,
        track_id=document.track_id,
        version_id=document.version_id,
        format=document.format,
        status=document.status if available else "failed",
        error_code=document.error_code if available else "export_modified",
        created_at=document.created_at,
        filename=document.filename if ready else None,
        audio_url=document.audio_url if ready else None,
        settings=document.settings,
        operation=document.operation,
        source_origin=document.source_origin,
        strict_release=document.strict_release,
        input_metrics=document.input_metrics,
        output_metrics=document.output_metrics,
        target_result=document.target_result,
        normalization_mode=document.normalization_mode,
        warnings=document.warnings,
        provenance=document.provenance,
        provenance_url=_manifest_url(document,'provenance') if ready and document.provenance is not None else None,
        manifest_url=_manifest_url(document,'manifest') if ready and document.provenance is not None else None,
    )


def get_export(track_id: int, version_id: str, identifier: str) -> AudioExportResponse:
    document = load_document(track_id, identifier)
    if document.stem_name is not None or document.version_id != _identifier(version_id):
        raise AudioExportError("export_not_found")
    return _response(document)


def _documents(track_id: int, *, strict: bool = True) -> list[ExportDocument]:
    root = _root(track_id)
    if not root.exists():
        return []
    result: list[ExportDocument] = []
    for directory in root.iterdir():
        try:
            result.append(load_document(track_id, directory.name))
            _invalid.discard((track_id, directory.name))
        except AudioExportError:
            logger.warning(
                "Ignoring invalid audio export record for track %s", track_id
            )
            if len(directory.name) == 32 and all(
                char in "0123456789abcdef" for char in directory.name
            ):
                _invalid.add((track_id, directory.name))
                if strict:
                    raise AudioExportError("export_record_invalid")
    return sorted(result, key=lambda document: document.created_at)


def list_exports(track_id: int, version_id: str) -> AudioExportsResponse:
    version_id = _identifier(version_id)
    return AudioExportsResponse(
        exports=[
            _response(item)
            for item in _documents(track_id, strict=False)
            if item.stem_name is None and item.version_id == version_id
        ]
    )


def export_file(track_id: int, version_id: str, identifier: str) -> Path:
    document = load_document(track_id, identifier)
    if document.stem_name is not None or document.version_id != _identifier(version_id):
        raise AudioExportError("export_not_found")
    if document.status != "done" or document.operation != 'export':
        raise AudioExportError("export_not_ready")
    path = _artifact(document)
    if not _output_current(document):
        raise AudioExportError("export_not_found")
    _production_proof(document)
    return path


def manifest_file(track_id: int,version_id: str,identifier: str,format: Literal['json','txt']) -> Path:
    media=export_file(track_id,version_id,identifier)
    document=load_document(track_id,identifier)
    return _published_manifest(document,media,format)


def _published_manifest(document: ExportDocument,media: Path,format: Literal['json','txt']) -> Path:
    if document.provenance is None:raise AudioExportError('manifest_unavailable')
    try:
        if export_provenance.digest(media)!=document.provenance.artifact_sha256:
            raise AudioExportError('manifest_media_changed')
        export_provenance.write(media,document.provenance)
    except (OSError,export_provenance.ProvenanceError) as exc:
        raise AudioExportError('manifest_unavailable') from exc
    return export_provenance.path_for(media,format)


def _output_identity(path: Path) -> OutputIdentity:
    stat = path.stat()
    return OutputIdentity(
        device=stat.st_dev,
        inode=stat.st_ino,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        ctime_ns=stat.st_ctime_ns,
    )


def _output_current(document: ExportDocument) -> bool:
    if document.operation == 'analyze':
        return document.input_metrics is not None
    try:
        path = _artifact(document)
        return (
            path.is_file()
            and document.output_identity is not None
            and 0 < path.stat().st_size <= _MAX_BYTES
            and _output_identity(path) == document.output_identity
        )
    except (OSError, AudioExportError):
        return False


def _source(document: ExportDocument) -> Path:
    relative = Path(document.source_relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise AudioExportError("source_unavailable")
    root = db.FILES_DIR.resolve()
    path = root / relative
    if (
        path.resolve() != path
        or not path.is_file()
        or not 0 < path.stat().st_size <= _MAX_BYTES
    ):
        raise AudioExportError("source_unavailable")
    return path


def _digest(path: Path) -> str:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    after = path.stat()
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise AudioExportError("source_changed")
    return digest.hexdigest()


async def _command(document: ExportDocument, argv: list[str], timeout: float) -> bytes:
    directory = _directory(document.track_id, document.id)

    def identity(worker: WorkerIdentity) -> None:
        document.worker = worker
        save_document(document)

    proc = await spawn_owned(
        argv,
        receipt_path=directory / "worker.json",
        stdout=asyncio.subprocess.PIPE,
        on_identity=identity,
    )
    _processes[document.id] = proc
    try:
        output = await read_owned_output(proc, max_bytes=1024 * 1024, timeout=timeout)
        if proc.returncode != 0:
            logger.warning(
                "Audio export child failed: %s", output[-2000:].decode(errors="replace")
            )
            raise AudioExportError("encode_failed")
        return output
    finally:
        # Keep the live handle and durable proof if termination itself fails.
        await await_cleanup(kill_process_tree(proc))
        if proc.stdin is not None:
            proc.stdin.close()
        if _processes.get(document.id) is proc:
            _processes.pop(document.id)
        document.worker = None
        save_document(document)


async def _probe(document: ExportDocument, path: Path) -> tuple[AudioProbe, float]:
    output = await _command(
        document,
        [
            tool("ffprobe"),
            "-v",
            "error",
            *_INPUT,
            "-show_entries",
            "stream=codec_type,codec_name,sample_rate,channels,bits_per_sample,bits_per_raw_sample,bit_rate:format=duration:format_tags",
            "-of",
            "json",
            str(path),
        ],
        30,
    )
    try:
        probe = AudioProbe.model_validate_json(output)
        duration = float(probe.format.duration)
        if (
            not math.isfinite(duration)
            or not 0 < duration <= 7200
            or not any(stream.codec_type == "audio" for stream in probe.streams)
        ):
            raise ValueError("invalid duration or audio stream")
        return probe, duration
    except (ValueError, ValidationError) as exc:
        raise AudioExportError("invalid_audio") from exc


async def _validate(
    document: ExportDocument, path: Path, source_duration: float
) -> None:
    if not path.is_file() or not 0 < path.stat().st_size <= _MAX_BYTES:
        raise AudioExportError("invalid_audio")
    probe, duration = await _probe(document, path)
    stream = next(item for item in probe.streams if item.codec_type == "audio")
    profile = (
        document.settings.mp3
        if document.format == "mp3"
        else (
            document.settings.wav
            if document.format == "wav"
            else document.settings.flac
        )
    )
    codec = (
        "mp3"
        if document.format == "mp3"
        else (
            "flac"
            if document.format == "flac"
            else {16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_f32le"}[
                document.settings.wav.bit_depth
            ]
        )
    )
    if (
        stream.codec_name != codec
        or stream.sample_rate != str(profile.sample_rate)
        or stream.channels != profile.channels
        or abs(duration - source_duration) > 0.15
    ):
        raise AudioExportError("invalid_audio")
    if document.format != "mp3":
        bits = int(stream.bits_per_raw_sample or "0") or stream.bits_per_sample
        expected_bits = (
            document.settings.wav.bit_depth
            if document.format == "wav"
            else document.settings.flac.bit_depth
        )
        if bits != expected_bits:
            raise AudioExportError("invalid_audio")
    elif document.settings.mp3.mode == "cbr" and stream.bit_rate != str(
        document.settings.mp3.bitrate_kbps * 1000
    ):
        raise AudioExportError("invalid_audio")
    await _command(
        document,
        [
            tool("ffmpeg"),
            "-v",
            "error",
            "-nostdin",
            *_INPUT,
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        ],
        180,
    )


def _publish(document: ExportDocument) -> None:
    proof = _production_proof(document)
    document.status = "done"
    document.error_code = ""
    if document.operation == 'analyze':
        document.filename = None
        document.audio_url = None
        save_document(document)
        return
    document.filename = f"{document.version_id}.{document.format}"
    document.audio_url = f"/api/tracks/{document.track_id}/versions/{document.version_id}/exports/{document.id}/audio"
    if document.stem_name is not None:
        document.filename = f"{document.stem_name}.{document.format}"
        document.audio_url = f"/api/tracks/{document.track_id}/stems/{document.stem_name}/exports/{document.id}/audio"
    document.output_identity = _output_identity(_artifact(document))
    if proof is not None and document.production_export_asset_id is None:
        from .artist_production import create_transform
        asset = create_transform(_artifact(document), 'export', [proof.graph_root_id], f'encode:{document.format}')
        document.production_export_asset_id = asset.id
    if document.provenance is not None:
        export_provenance.write(_artifact(document),document.provenance)
        document.provenance_url=_manifest_url(document,'provenance')
        document.manifest_url=_manifest_url(document,'manifest')
    save_document(document)
    _usage(document, 'done')
    if document.expected_primary is not None:
        connection = db.get_db()
        connection.execute(
            "UPDATE tracks SET audio_path=? WHERE id=? AND audio_path=?",
            (str(_artifact(document)), document.track_id, document.expected_primary),
        )
        connection.commit()


def _export_voice_id(track_id: int, version_id: str) -> str | None:
    from .audio_version_store import get as get_version
    version = get_version(db.get_db(), version_id)
    if version is not None and version.public.voice_id is not None:
        return version.public.voice_id
    row = db.get_track(track_id)
    raw: object = row['params_json'] if row is not None else None
    try:
        params = _json.validate_json(raw) if isinstance(raw, str) else {}
    except ValidationError as exc:
        raise AudioExportError('track_context_invalid') from exc
    voice_id = params.get('voice_id')
    if voice_id is not None and not isinstance(voice_id, str):
        raise AudioExportError('artist_binding_invalid')
    return voice_id


def _production_proof(document: ExportDocument) -> ProductionProvenance | None:
    from . import artist_production, artists, voice_authorization
    try:
        voice_id = _export_voice_id(document.track_id, document.version_id)
        if document.artist_voice_id is not None and document.artist_voice_id != voice_id:
            raise AudioExportError('artist_binding_changed')
        if voice_id is not None:
            voice_authorization.recheck(voice_id, document.artist_permission, 'export',
                use='commercial' if document.strict_release else 'production')
        proof = artist_production.production_provenance(document.version_id, require_release=document.strict_release)
        if proof is not None and proof.assets[-1].sha256 != document.source_sha256:
            raise AudioExportError('production_output_changed')
        return proof
    except (artist_production.ProductionError, artists.ArtistError) as exc:
        raise AudioExportError(exc.code) from exc
    except HTTPException as exc:
        raise AudioExportError(str(exc.detail)) from exc


def _usage(document: ExportDocument, outcome: str, *, recovery: bool = False) -> None:
    if document.artist_permission is None or document.operation != 'export':
        return
    from .artist_production import append_usage, usage_event
    from .artist_production_contracts import UsageEventInput
    proof = (document.provenance.production if document.provenance is not None else None) or document.production_context
    model = next((item for item in proof.model.files if item.role == 'checkpoint'), None) if proof is not None else None
    prior = usage_event(f'export:{document.id}:done') if recovery else None
    append_usage(UsageEventInput.model_validate({'idempotency_key': f'export:{document.id}:' + (f'recovery:{document.error_code}:' if recovery else '') + outcome,
        'operation': 'export', 'outcome': outcome, 'artist_id': document.artist_permission.artist_id,
        'consent_version_id': document.artist_permission.id, 'track_id': document.track_id,
        'source_graph_id': proof.graph_root_id if proof else None,
        'voice_model_revision': proof.model.model_revision_id if proof else None,
        'voice_model_sha256': model.sha256 if model else None,
        'inputs': [{'id': document.version_id, 'sha256': document.source_sha256}],
        'outputs': [{'id': document.id, 'sha256': document.output_sha256}] if outcome == 'done' and document.output_sha256 else [],
        'error_code': document.error_code, 'supersedes_event_id': prior.id if prior is not None else None}))


async def _run(document: ExportDocument) -> None:
    try:
        async with _capacity:
            document.status = "running"
            save_document(document)
            source = _source(document)
            if await asyncio.to_thread(_digest, source) != document.source_sha256:
                raise AudioExportError("source_changed")
            _production_proof(document)
            source_probe, duration = await _probe(document, source)
            from .audio_version_store import get as get_version
            version = get_version(db.get_db(),document.version_id)
            component = export_provenance.track_component(document.track_id,document.version_id,document.source_sha256,
                is_voice=version is not None and version.public.kind=='voice')
            if document.source_origin is not None and component.content_origin=='unknown':
                component.content_origin=document.source_origin
                component.classification_basis='user_declared'
            prior_comment=next((value for key,value in source_probe.format.tags.items() if key.casefold()=='comment'),'')
            async def quality_command(argv: list[str], timeout: float) -> bytes:
                return await _command(document, argv, timeout)
            measurement: audio_quality.Measurement | None = None
            try:
                measurement = await audio_quality.measure(source, _directory(document.track_id,document.id), quality_command)
                document.input_metrics = measurement.metrics
            except (audio_quality.AudioQualityError, AudioExportError, VideoMediaError) as exc:
                if isinstance(exc,AudioExportError) and exc.code != 'encode_failed':raise
                if document.operation == 'analyze':
                    raise AudioExportError('audio_measurement_failed') from exc
                document.warnings.append('audio_measurement_failed')
            if document.operation == 'analyze':
                if await asyncio.to_thread(_digest,source) != document.source_sha256:
                    raise AudioExportError('source_changed')
                document.target_result = audio_quality.target_result(document.settings.loudness,document.input_metrics)
                _publish(document)
                return
            processing = audio_quality.normalization_filter(document.settings.loudness,measurement) if measurement is not None else None
            partial = _artifact(document, partial=True)
            partial.unlink(missing_ok=True)
            encoded_report = await _command(
                document,
                [
                    tool("ffmpeg"),
                    "-v",
                    "info" if processing is not None else "error",
                    "-nostats",
                    "-nostdin",
                    "-y",
                    *_INPUT,
                    "-i",
                    str(source),
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-map_metadata",
                    "0",
                    *export_provenance.metadata_args(document.id,component.content_origin,prior_comment),
                    *(['-af',processing] if processing is not None else []),
                    *encoding_args(document.format, document.settings),
                    "-fs",
                    str(_MAX_BYTES),
                    str(partial),
                ],
                3600,
            )
            await _validate(document, partial, duration)
            if processing is not None:
                normalized = audio_quality.report(encoded_report).normalization_type
                if normalized == 'linear':document.normalization_mode = 'linear'
                elif normalized == 'dynamic':document.normalization_mode = 'dynamic'
                else:document.normalization_mode = 'unknown'
            try:
                document.output_metrics = (await audio_quality.measure(partial,_directory(document.track_id,document.id),quality_command,name='output')).metrics
            except (audio_quality.AudioQualityError, AudioExportError, VideoMediaError) as exc:
                if isinstance(exc,AudioExportError) and exc.code != 'encode_failed':raise
                document.warnings.append('audio_measurement_failed')
            document.target_result = audio_quality.target_result(document.settings.loudness,document.output_metrics)
            if document.target_result in {'warning','inconclusive'}:
                document.warnings.append('loudness_target_'+document.target_result)
            if await asyncio.to_thread(_digest, source) != document.source_sha256:
                raise AudioExportError("source_changed")
            with partial.open("rb") as handle:
                os.fsync(handle.fileno())
            document.output_sha256 = await asyncio.to_thread(_digest, partial)
            document.provenance = export_provenance.manifest(document.id,'stem' if document.stem_name else 'track_audio',partial,[component],
                transformations=[f'encode:{document.format}',f'loudness:{document.settings.loudness.profile}:{document.normalization_mode}'],
                measured_audio=document.output_metrics,audio_target=document.settings.loudness,audio_target_result=document.target_result)
            document.provenance.production = _production_proof(document)
            document.production_context = None
            if document.provenance.production is None:
                document.warnings.append('legacy_lineage_unreviewed')
            # This proof survives a crash between the final rename and the
            # terminal metadata switch; completed identity changes can then
            # be distinguished from a byte-identical library migration.
            save_document(document)
            partial.replace(_artifact(document))
            _publish(document)
    except asyncio.CancelledError:
        document.status = "cancelled"
        document.error_code = "cancelled"
        if document.id not in _processes:
            save_document(document)
            _usage(document, 'cancelled')
        raise
    except Exception as exc:
        logger.warning("Audio export failed", exc_info=True)
        document.status = "failed"
        document.error_code = (
            exc.code
            if isinstance(exc, (AudioExportError, VideoMediaError))
            else "encode_failed"
        )
        try:
            save_document(document)
            _usage(document, 'failed')
        except AudioExportError:
            logger.exception("Unable to persist failed audio export")
    finally:
        if document.id not in _processes:
            _artifact(document, partial=True).unlink(missing_ok=True)
            for temporary in _directory(document.track_id,document.id).glob('*.analysis.f32'):
                temporary.unlink(missing_ok=True)
            if _tasks.get(document.id) is asyncio.current_task():
                _tasks.pop(document.id)
                _running.pop(document.id, None)


async def create_export(
    track_id: int,
    version_id: str,
    format: AudioExportFormat,
    settings: AudioEncodingSettings | None = None,
    *,
    update_default: bool = False,
    _stem: StemName | None = None,
    operation: Literal['export','analyze'] = 'export',
    source_origin: Literal['recorded','unknown'] | None = None,
    strict_release: bool = False,
) -> AudioExportResponse:
    version_id = _identifier(version_id)
    profile = AudioEncodingSettings.model_validate_json(
        (settings or load_settings()).model_dump_json()
    )
    if format not in {"mp3", "wav", "flac"}:
        raise AudioExportError("invalid_format")
    async with _lock:
        if track_id in _deleting or (_stem is not None and track_id in _stem_deleting):
            raise AudioExportError("track_busy")
        row = db.get_track(track_id)
        if row is None:
            raise AudioExportError("track_not_found")
        try:
            source = resolve_source(track_id, version_id) if _stem is None else stem_source(track_id, _stem)
            root = db.FILES_DIR.resolve()
            relative = source.relative_to(root)
            if (
                source.resolve() != source
                or not 0 < source.stat().st_size <= _MAX_BYTES
            ):
                raise ValueError("invalid source")
        except (OSError, ValueError) as exc:
            raise AudioExportError("source_unavailable") from exc
        digest = await asyncio.to_thread(_digest, source)
        from .audio_version_store import get as get_version
        from .voice_authorization import authorize
        version = get_version(db.get_db(), version_id)
        try:
            voice_id = _export_voice_id(track_id, version_id)
            permission = authorize(voice_id, 'export', use='commercial' if strict_release else 'production') if voice_id is not None else None
        except HTTPException as exc:
            raise AudioExportError(str(exc.detail)) from exc
        if source_origin is not None:
            from .audio_version_store import get as get_version
            version=get_version(db.get_db(),version_id)
            content=export_provenance.track_component(track_id,version_id,digest,is_voice=version is not None and version.public.kind=='voice')
            if content.content_origin not in {'unknown',source_origin}:
                raise AudioExportError('source_origin_conflict')
        expected_primary = str(row["audio_path"]) if update_default else None
        matching = [
            _running.get(stored.id, stored)
            for stored in _documents(track_id)
            if stored.version_id == version_id
            and stored.stem_name == _stem
            and stored.format == format
            and stored.settings == profile
            and stored.source_sha256 == digest
            and stored.operation == operation
            and stored.source_origin == source_origin
            and stored.strict_release == strict_release
        ]
        if update_default:
            # Find the prior guard before reusing a newer manual export; record
            # ordering must not let a save retry overwrite a later voice.
            expected_primary = next(
                (
                    item.expected_primary
                    for item in matching
                    if item.expected_primary is not None
                ),
                expected_primary,
            )
        for existing in matching:
            _production_proof(existing)
            if (
                existing.status in {"queued", "running", "done"}
                and (existing.worker is None or existing.id in _tasks)
                and existing.id not in _unverified
                and (existing.status != "done" or _output_current(existing))
            ):
                if update_default and existing.expected_primary is None:
                    existing.expected_primary = expected_primary
                    save_document(existing)
                    if existing.status == "done":
                        _publish(existing)
                return _response(existing)
        from .resource_admission import require_setup_idle
        require_setup_idle()
        document = ExportDocument(
            id=uuid.uuid4().hex,
            track_id=track_id,
            version_id=version_id,
            format=format,
            status="queued",
            created_at=datetime.now(timezone.utc).isoformat(),
            settings=profile,
            source_relative=str(relative),
            source_sha256=digest,
            expected_primary=expected_primary,
            stem_name=_stem,
            operation=operation,
            source_origin=source_origin,
            strict_release=strict_release,
            artist_permission=permission,
            artist_voice_id=voice_id,
        )
        document.production_context = _production_proof(document)
        directory = _directory(track_id, document.id)
        try:
            directory.mkdir(parents=True)
            save_document(document)
        except (OSError, AudioExportError) as exc:
            shutil.rmtree(directory, ignore_errors=True)
            raise AudioExportError("export_write_failed") from exc
        _running[document.id] = document
        _usage(document, 'requested')
        _tasks[document.id] = asyncio.create_task(_run(document))
        return _response(document)


async def _drain(document: ExportDocument) -> None:
    task = _tasks.get(document.id)
    request_cancel(task)
    if task is not None:
        outcomes = await await_cleanup(asyncio.gather(task, return_exceptions=True))
        for outcome in outcomes:
            if isinstance(outcome, Exception):
                logger.error(
                    "Audio export task failed during cleanup", exc_info=outcome
                )
    proc = _processes.get(document.id)
    if proc is not None:
        await await_cleanup(kill_process_tree(proc))
        if proc.stdin is not None:
            proc.stdin.close()
        _processes.pop(document.id, None)
    document = load_document(document.track_id, document.id)
    if document.worker is not None:
        receipt = _directory(document.track_id, document.id) / "worker.json"
        if Path(
            document.worker.receipt
        ).resolve() != receipt or not await terminate_verified(document.worker):
            _unverified.add(document.id)
            raise AudioExportError("worker_identity_unverified")
        document.worker = None
        _unverified.discard(document.id)
    _tasks.pop(document.id, None)
    _running.pop(document.id, None)
    if document.status in {"queued", "running"}:
        document.status = "cancelled"
        document.error_code = "cancelled"
    save_document(document)
    if document.status == 'cancelled':
        _usage(document, 'cancelled')
    _artifact(document, partial=True).unlink(missing_ok=True)


async def cancel_export(
    track_id: int, version_id: str, identifier: str
) -> AudioExportResponse:
    async with _lock:
        document = load_document(track_id, identifier)
        if document.stem_name is not None or document.version_id != _identifier(version_id):
            raise AudioExportError("export_not_found")
        await await_cleanup(_drain(document))
        return get_export(track_id, version_id, identifier)


async def retry_export(
    track_id: int, version_id: str, identifier: str
) -> AudioExportResponse:
    document = load_document(track_id, identifier)
    if document.stem_name is not None or document.version_id != _identifier(version_id):
        raise AudioExportError("export_not_found")
    if (
        document.worker is not None
        or document.id in _processes
        or document.id in _unverified
    ):
        raise AudioExportError("worker_identity_unverified")
    response = await create_export(
        track_id, version_id, document.format, document.settings, operation=document.operation,source_origin=document.source_origin,
        strict_release=document.strict_release
    )
    if document.expected_primary is not None:
        async with _lock:
            replacement = _running.get(response.id) or load_document(
                track_id, response.id
            )
            if replacement.expected_primary is None:
                # Preserve the original conditional-update guard; a retry must
                # never capture a voice file that became primary meanwhile.
                replacement.expected_primary = document.expected_primary
                save_document(replacement)
                if replacement.status == "done":
                    _publish(replacement)
    return response


def work_busy() -> bool:
    return bool(_tasks or _processes or _unverified or _invalid)


def active_track_ids() -> set[int]:
    """Project owned jobs without scanning export files across the library."""
    return {document.track_id for document in _running.values()
            if document.status in {"queued", "running"}}


def _validated_stem(name: str) -> StemName:
    try:
        return _stem_name.validate_python(name)
    except ValidationError as exc:
        raise AudioExportError('stem_not_found') from exc


def stem_source(track_id: int, name: str) -> Path:
    """Resolve only catalogued stems inside the library, without symlinks."""
    from . import stems
    name = _validated_stem(name)
    if stems.is_active(track_id):
        raise AudioExportError('stem_busy')
    row = db.get_track(track_id)
    if row is None:
        raise AudioExportError('track_not_found')
    try:
        encoded = row['stems_json']
        if not isinstance(encoded, str) or len(encoded) > 65536:
            raise ValueError('invalid stem catalog')
        paths = _stem_paths.validate_json(encoded)
        source = Path(paths[name])
        root = db.FILES_DIR.resolve()
        if source.resolve() != source or not source.is_relative_to(root) or not source.is_file():
            raise ValueError('invalid stem path')
        return source
    except (KeyError, OSError, ValueError, ValidationError) as exc:
        raise AudioExportError('stem_not_found') from exc


def _stem_document(track_id: int, name: str, identifier: str) -> ExportDocument:
    document = load_document(track_id, identifier)
    if document.stem_name != _validated_stem(name):
        raise AudioExportError('export_not_found')
    return document


def _stem_response(document: ExportDocument) -> StemAudioExportResponse:
    if document.stem_name is None:
        raise AudioExportError('export_not_found')
    response = _response(document)
    return StemAudioExportResponse.model_validate({
        **response.model_dump(exclude={'version_id'}), 'stem_name': document.stem_name,
    })


async def create_stem_export(track_id: int, name: str, format: AudioExportFormat) -> StemAudioExportResponse:
    stem = _validated_stem(name)
    # Legacy documents use an internal source key. Stem APIs expose the stem
    # identity and never present this key as a generated song version.
    source_key = hashlib.sha256(f'stem:{stem}'.encode()).hexdigest()[:32]
    response = await create_export(track_id, source_key, format, _stem=stem)
    return get_stem_export(track_id, stem, response.id)


def get_stem_export(track_id: int, name: str, identifier: str) -> StemAudioExportResponse:
    return _stem_response(_stem_document(track_id, name, identifier))


def list_stem_exports(track_id: int, name: str) -> StemAudioExportsResponse:
    stem = _validated_stem(name)
    return StemAudioExportsResponse(exports=[_stem_response(doc) for doc in reversed(_documents(track_id, strict=False)) if doc.stem_name == stem])


def stem_export_file(track_id: int, name: str, identifier: str) -> Path:
    document = _stem_document(track_id, name, identifier)
    if document.status != 'done' or not _output_current(document):
        raise AudioExportError('export_not_ready')
    _production_proof(document)
    return _artifact(document)


def stem_manifest_file(track_id: int,name: str,identifier: str,format: Literal['json','txt']) -> Path:
    media=stem_export_file(track_id,name,identifier)
    return _published_manifest(_stem_document(track_id,name,identifier),media,format)


async def cancel_stem_export(track_id: int, name: str, identifier: str) -> StemAudioExportResponse:
    async with _lock:
        document = _stem_document(track_id, name, identifier)
        await await_cleanup(_drain(document))
        return get_stem_export(track_id, name, identifier)


async def retry_stem_export(track_id: int, name: str, identifier: str) -> StemAudioExportResponse:
    document = _stem_document(track_id, name, identifier)
    if document.worker is not None or document.id in _processes or document.id in _unverified:
        raise AudioExportError('worker_identity_unverified')
    response = await create_export(track_id, document.version_id, document.format, document.settings, _stem=document.stem_name)
    return get_stem_export(track_id, name, response.id)


@asynccontextmanager
async def protect_stem_exports_removal(track_id: int) -> AsyncIterator[None]:
    async with _lock:
        if track_id in _stem_deleting or track_id in _deleting:
            raise AudioExportError('track_busy')
        _stem_deleting.add(track_id)
        try:
            documents = [doc for doc in _documents(track_id) if doc.stem_name is not None]
            await await_cleanup(_drain_all(documents, propagate=True))
            for document in documents:
                shutil.rmtree(_directory(track_id, document.id))
        except BaseException:
            _stem_deleting.discard(track_id)
            raise
    try:
        yield
    finally:
        _stem_deleting.discard(track_id)


async def cancel_track_exports(track_id: int) -> None:
    async with _lock:
        documents = _documents(track_id)
        for document in documents:
            request_cancel(_tasks.get(document.id))
        await await_cleanup(_drain_all(documents, propagate=True))


async def _drain_all(documents: list[ExportDocument], *, propagate: bool) -> None:
    outcomes = await asyncio.gather(
        *(_drain(document) for document in documents), return_exceptions=True
    )
    first: Exception | None = None
    for outcome in outcomes:
        if isinstance(outcome, Exception):
            logger.error("Unable to drain audio export", exc_info=outcome)
            if first is None:
                first = outcome
    if propagate and first is not None:
        raise first


@asynccontextmanager
async def protect_track_exports_removal(track_id: int) -> AsyncIterator[None]:
    async with _lock:
        if track_id in _deleting:
            raise AudioExportError("track_busy")
        _deleting.add(track_id)
    try:
        await cancel_track_exports(track_id)
        yield
    finally:
        _deleting.discard(track_id)


def delete_track_exports(track_id: int) -> None:
    if track_id not in _deleting:
        raise AudioExportError("track_busy")
    shutil.rmtree(_root(track_id), ignore_errors=False) if _root(
        track_id
    ).exists() else None


def _all_documents() -> list[ExportDocument]:
    root = db.FILES_DIR.resolve() / "_exports"
    if not root.exists() or root.resolve() != root:
        return []
    return [
        document
        for track in root.iterdir()
        if track.name.isdecimal() and track.is_dir()
        for document in _documents(int(track.name), strict=False)
    ]


async def shutdown_exports() -> None:
    for task in list(_tasks.values()):
        request_cancel(task)
    await await_cleanup(_drain_all(_all_documents(), propagate=False))


def _recover_publication(document: ExportDocument) -> None:
    completed = document.status == 'done'
    try:
        _publish(document)
    except Exception as exc:
        logger.warning('Audio export recovery publication blocked', exc_info=True)
        if completed:
            # Revocation prevents new promotion without rewriting a completed output.
            return
        document.status = 'failed'
        document.error_code = exc.code if isinstance(exc, AudioExportError) else 'export_interrupted'
        save_document(document)
        _usage(document, 'interrupted', recovery=True)


async def recover_exports() -> None:
    async with _lock:
        for document in _all_documents():
            if document.id in _tasks:
                continue
            if document.worker is not None:
                try:
                    await _drain(document)
                except AudioExportError:
                    document.status = "failed"
                    document.error_code = "worker_identity_unverified"
                    save_document(document)
                    _usage(document, 'interrupted', recovery=True)
                    continue
                document = load_document(document.track_id, document.id)
            if document.operation == 'analyze':
                if document.status not in {'queued','running','done'}:continue
                try:
                    valid_analysis=document.input_metrics is not None and await asyncio.to_thread(_digest,_source(document)) == document.source_sha256
                except (AudioExportError,OSError):valid_analysis=False
                if valid_analysis:
                    _recover_publication(document)
                elif document.status in {'queued','running'}:
                    document.status='failed';document.error_code='export_interrupted';save_document(document)
                continue
            if document.status == "done" and _output_current(document):
                if (
                    document.expected_primary is not None
                    and _artifact(document).is_file()
                ):
                    _recover_publication(document)
                _usage(document, 'done')
                continue
            if (
                document.status not in {"queued", "running"}
                and not _artifact(document).is_file()
            ):
                continue
            if document.output_identity is not None:
                try:
                    identical = (
                        document.output_sha256 is not None
                        and await asyncio.to_thread(_digest, _artifact(document))
                        == document.output_sha256
                    )
                except (OSError, AudioExportError):
                    identical = False
                if not identical:
                    document.status = "failed"
                    document.error_code = "export_modified"
                    save_document(document)
                    _usage(document, 'interrupted', recovery=True)
                    continue
                _recover_publication(document)
                continue
            try:
                source = _source(document)
                if await asyncio.to_thread(_digest, source) != document.source_sha256:
                    raise AudioExportError("source_changed")
                _, duration = await _probe(document, source)
                await _validate(document, _artifact(document), duration)
                digest = await asyncio.to_thread(_digest, _artifact(document))
                if (
                    document.output_sha256 is not None
                    and digest != document.output_sha256
                ):
                    raise AudioExportError("export_modified")
                document.output_sha256 = digest
                _recover_publication(document)
            except Exception as exc:
                logger.exception("Unable to recover interrupted audio export")
                document.status = "failed"
                document.error_code = (
                    "export_modified"
                    if isinstance(exc, AudioExportError)
                    and exc.code == "export_modified"
                    else "export_interrupted"
                )
                save_document(document)
                _usage(document, 'interrupted', recovery=True)
            finally:
                _artifact(document, partial=True).unlink(missing_ok=True)
