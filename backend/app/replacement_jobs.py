"""Durable replacement workflow over existing separator, converter and DSP owners.

No model installation, independent engine server, or browser-owned work. All
paths below are private, derived from typed catalog identities and random IDs.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
from typing import IO, Literal, Self
import uuid

from fastapi import HTTPException
from pydantic import Field, TypeAdapter, ValidationError, model_validator
import soundfile as sf

from . import artist_production as evidence, db, replacement_store as store
from .artist_production_contracts import SourceAsset, VoiceModelSnapshot
from .atomic_files import write_object
from .audio_quality import INPUT, AudioQualityError, RunCommand
from .contracts import JsonObject
from .export_provenance_contracts import ContentOrigin, MusicComponentEvidence, MusicEvidence, ProvenanceComponent
from .module_contracts import ModuleId
from .job_lifecycle import await_cleanup, cancel_and_wait, kill_process_tree, run_owned_thread
from .music_analysis import MusicAnalysisError, _resolve_source, parse_audio_header, probe_command
from .music_analysis_contracts import MusicAnalysisContract, MusicAnalysisId, MusicAnalysisSha256, MusicSourceRef, ProductionMusicSource
from .music_alignment import align_prepared_vocal
from .music_dsp import (MusicDspError, NativeStretch, ResolvedDspSource, _metrics, render_mix, render_transform)
from .music_dsp_contracts import (AlignmentRequest, AlignmentResult, DspMixInput, DspMixRequest,
    DspTransformRequest, WarpAnchor, validate_anchors)
from .replacement_contracts import (AlignmentEditRequest, ReplacementActionRequest, ReplacementAssetImport, ReplacementAssetResponse, ReplacementArtifact,
    ReplacementArtifactRole, ReplacementCapabilities, ReplacementErrorCode, ReplacementMixEditRequest,
    ReplacementPhase, ReplacementPlanResponse, ReplacementPlansResponse, ReplacementProcessing, ReplacementRender,
    ReplacementRenderRequest, ReplacementRequest, ReplacementStoragePreview, ReplacementUpdateRequest,
    ReplacementMixSettings, RvcRegisteredModel, SelectedSource, SeedTarget)
from .rvc_models import RvcModelError
from .replacement_mix import CLEANUP_RECIPE, fit_prepared, mix_replacement, prepare_performance_file
from .replacement_diagnostics import DiagnosticWorkerUnavailable, DiagnosticsError, measure_diagnostics
from .replacement_pitch_contracts import DiagnosticArtifactIdentity
from .video_io import _signature, copy_verified, hash_file
from .video_media import tool
from .video_process import (WorkerIdentity, read_owned_output, run_owned_command, spawn_owned,
    terminate_verified)
from .video_projects import VideoProjectError
from .voice_mix import observe_conversion, pin_conversion
from .voice_mix import PinnedConversion
from .voice_artifacts import ModelArtifact
from .voice_separation import VoiceSeparationError, offline_demucs

logger = logging.getLogger(__name__)
ReplacementError = store.ReplacementStoreError
MAX_SOURCE_BYTES = 256 * 1024**2
MAX_PCM_BYTES = 48000 * 2 * 4 * 7200 + 8192
RESERVE_BYTES = 1024**3
RECIPE = 'replacement-v2-cpu-fit-v1'
_json = TypeAdapter(JsonObject)
_code: TypeAdapter[ReplacementErrorCode] = TypeAdapter(ReplacementErrorCode)
_lock = asyncio.Lock()
_tasks: dict[str, asyncio.Task[None]] = {}
_processes: dict[str, asyncio.subprocess.Process] = {}
_unverified: dict[str, WorkerIdentity] = {}
_deleting: set[int] = set()
_replacing_stems: set[int] = set()
_closing = False
_intake_lock = asyncio.Lock()


class _AssetReceipt(MusicAnalysisContract):
    request: ReplacementAssetImport
    sha256: MusicAnalysisSha256
    filename: str = Field(pattern=r'^source\.(wav|flac|mp3|m4a|ogg|opus|aif|aiff)$')
    response: ReplacementAssetResponse | None = None

    @model_validator(mode='after')
    def same_upload(self) -> Self:
        if self.response is not None and (self.response.request_id != self.request.request_id
                or self.response.role != self.request.role or self.response.selection.audio_sha256 != self.sha256):
            raise ValueError('replacement_artifact_changed')
        return self


class _DecodedSnapshot(MusicAnalysisContract):
    recipe: Literal['float48k-v1'] = 'float48k-v1'
    source_sha256: MusicAnalysisSha256
    audio_sha256: MusicAnalysisSha256
    evidence_asset_id: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    db.get_db()  # application migration owner initializes the catalog once
    connection = sqlite3.connect(db.DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    try:
        yield connection
    finally:
        connection.close()


def get(identifier: str) -> ReplacementPlanResponse:
    with _connection() as connection:
        return store.get_plan(connection, identifier)


def list_plans(*, limit: int = 100, cursor: str | None = None,
               source: MusicSourceRef | None = None) -> ReplacementPlansResponse:
    with _connection() as connection:
        return store.list_plans(connection, limit=limit, cursor=cursor, source=source)


def publication_component(track_id: int, source_sha256: str, params: JsonObject) -> ProvenanceComponent | None:
    """Only an atomically attached catalog render can classify a v2 output."""
    identifier, plan_id, revision = params.get('replacement_render_id'), params.get('replacement_plan_id'), params.get('replacement_revision')
    if not isinstance(identifier, str) or not isinstance(plan_id, str) or type(revision) is not int:
        return None
    try:
        with _connection() as connection:
            rendered = store.get_render(connection, identifier)
            if (rendered.status != 'done' or rendered.track_id != track_id or rendered.plan_id != plan_id
                    or rendered.revision != revision or rendered.master_artifact_id is None):
                return None
            master = store.get_artifact(connection, plan_id, rendered.master_artifact_id)
            snapshot = store.get_revision(connection, plan_id, revision)
        if (master.public.role != 'master' or master.public.sha256 != source_sha256
                or master.public.settings_sha256 != rendered.settings_sha256 or master.public.measurement is None):
            return None
        return ProvenanceComponent(role='audio', content_origin=master.content_origin,
            source_id='replacement:' + rendered.id, source_sha256=source_sha256,
            engine=snapshot.request.target.engine if snapshot.request.mode != 'prepared' else 'openfabric-cpu-dsp',
            music_evidence=master.music_evidence or MusicEvidence(),
            classification_basis='app_workflow' if master.content_origin != 'unknown' else 'unverified')
    except (ReplacementError, evidence.ProductionError, OSError, sqlite3.Error, ValueError):
        return None


def _workspace(identifier: str, *, create: bool = False) -> Path:
    from .music_analysis_contracts import MusicAnalysisId
    TypeAdapter(MusicAnalysisId).validate_python(identifier)
    root = db.FILES_DIR / 'replacements' / identifier
    if (db.FILES_DIR.is_symlink() or root.is_symlink() or root.resolve() != root
            or not root.is_relative_to(db.FILES_DIR.resolve())):
        raise ReplacementError('replacement_storage_failed', 503)
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def _directory(root: Path, path: Path) -> Path:
    if path.is_symlink() or path.resolve() != path or not path.is_relative_to(root):
        raise ReplacementError('replacement_storage_failed', 503)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _selections(request: ReplacementRequest) -> tuple[SelectedSource, ...]:
    return (request.source,) if request.mode == 'convert' else (request.source, request.prepared)


def _source(selected: SelectedSource) -> Path:
    try:
        path = _resolve_source(selected.source)
        if not 0 < path.stat().st_size <= MAX_SOURCE_BYTES:
            raise ReplacementError('upload_too_large', 413)
        return path
    except MusicAnalysisError as error:
        raise ReplacementError('replacement_source_missing', 404) from error


async def _verify(selected: SelectedSource) -> Path:
    path = _source(selected)
    before = _signature(path)
    if await hash_file(path, max_bytes=before[2]) != selected.audio_sha256 or _signature(path) != before:
        raise ReplacementError('replacement_source_changed')
    return path


async def storage_preview(request: ReplacementRequest) -> ReplacementStoragePreview:
    source_bytes, decoded = 0, 0
    for selection in _selections(request):
        path = _source(selection)
        source_bytes += path.stat().st_size
        result = await run_owned_command(probe_command(path), timeout=30, max_bytes=65536)
        if result.returncode:
            raise ReplacementError('invalid_audio', 422)
        try:
            header = parse_audio_header(result.stdout)
        except MusicAnalysisError as error:
            raise ReplacementError('invalid_audio', 422) from error
        if header.duration > 7200:
            raise ReplacementError('audio_too_long', 422)
        decoded += (round(header.duration * 48000) + 4800) * 2 * 4 + 8192
    model_bytes = 0
    if request.separator in {'fast', 'high'}:
        try:
            model_bytes = offline_demucs('fast' if request.separator == 'fast' else 'high').model_bytes
        except VoiceSeparationError:
            pass
    # Retain raw snapshots, dry stems, aligned/converted/matched/master, plus
    # explicit phrase outputs. Sequential DSP scratch is at most four PCM files.
    # Model repository copies are temporary; no weight download is included.
    conversion_bytes = 0
    if request.mode != 'prepared' and request.target.engine == 'seed-vc':
        try:
            _, pinned = _target_files(request.target)
            conversion_bytes = sum(item.signature[2] for item in pinned.files)
        except (HTTPException, ReplacementError, MusicDspError):
            pass  # readiness is independent from the audio storage forecast
    elif request.mode != 'prepared' and request.target.engine == 'rvc':
        from .rvc_models import get as rvc_model, model_root
        try:
            model = rvc_model(request.target.registered_model_id)
            conversion_bytes = (model_root(model.id) / 'model.pth').stat().st_size
            if model.index_sha256 is not None:
                conversion_bytes += (model_root(model.id) / 'added.index').stat().st_size
        except (RvcModelError, OSError):
            pass
    retained = source_bytes + 9 * decoded + conversion_bytes
    temporary = 5 * decoded + model_bytes + 80 * 1024**2
    required = retained + temporary + RESERVE_BYTES
    try:
        free: int | None = shutil.disk_usage(db.FILES_DIR).free
    except OSError:
        free = None
    return ReplacementStoragePreview(source_bytes=source_bytes, retained_bytes=retained,
        temporary_bytes=temporary, required_free_bytes=required, free_bytes=free,
        can_start=free is not None and free >= required)


async def capabilities() -> ReplacementCapabilities:
    from .music_dsp import MAX_NATIVE_SECONDS, MAX_MAPPED_NATIVE_SECONDS
    from .music_dsp_runtime import native
    from .voice_contracts import VoiceSeparationOption, VoiceSeparationOptionsResponse
    options: list[VoiceSeparationOption] = []
    for quality in ('fast', 'high'):
        parsed: Literal['fast', 'high'] = 'fast' if quality == 'fast' else 'high'
        try:
            offline_demucs(parsed)
            available = True
        except VoiceSeparationError:
            available = False
        options.append(VoiceSeparationOption(id=parsed, available=available,
            reason='' if available else 'demucs_offline_models_missing'))
    options.append(VoiceSeparationOption(id='roformer', available=False, reason='paired_backing_unverified'))
    ready = native() is not None
    ir_supported = False
    try:
        support = await run_owned_command([tool('ffmpeg'), '-hide_banner', '-h', 'filter=afir'], timeout=10, max_bytes=65536)
        ir_supported = support.returncode == 0 and all(option in support.stdout for option in (b'irnorm', b'irfmt', b'precision', b'maxir'))
    except (OSError, TimeoutError):
        pass
    from .rvc_models import list_models
    return ReplacementCapabilities(native_stretch_available=ready, owned_ir_supported=ir_supported, cleanup_supported=True,
        rvc_inference_ready=any(model.ready for model in list_models().models),
        native_uniform_max_input_sec=float(MAX_NATIVE_SECONDS) if ready else 0.0,
        native_mapped_max_input_sec=float(MAX_MAPPED_NATIVE_SECONDS) if ready else 0.0,
        separation=VoiceSeparationOptionsResponse(options=options))


async def create(request: ReplacementRequest, *, request_id: str | None = None) -> ReplacementPlanResponse:
    try:
        identifier = TypeAdapter(MusicAnalysisId).validate_python(request_id) if request_id is not None else uuid.uuid4().hex
    except ValidationError as error:
        raise ReplacementError('replacement_invalid_request', 422) from error
    async with _lock:
        with _connection() as connection:
            if connection.execute('SELECT id FROM replacement_plans WHERE id=?', (identifier,)).fetchone() is not None:
                if store.get_revision(connection, identifier, 1).request != request:
                    raise ReplacementError('replacement_request_conflict')
                return store.get_plan(connection, identifier)
        if _closing:
            raise ReplacementError('replacement_busy')
        for selection in _selections(request):
            _source(selection)
        with _connection() as connection:
            if connection.execute('SELECT COUNT(*) FROM replacement_plans').fetchone()[0] >= 1000:
                raise ReplacementError('replacement_storage_failed', 503)
            return store.create_plan(connection, ReplacementPlanResponse(id=identifier,
                request=request, updated_at=_now()))


async def import_asset(request: ReplacementAssetImport, stream: IO[bytes], *, filename: str) -> ReplacementAssetResponse:
    """Thin intake into existing evidence/tracks; a sidecar owns upload replay."""
    from .rvc_models import copy_upload, hash_upload
    from .music_analysis_contracts import VersionMusicSource
    from .audio_versions import retain_original
    name = filename.replace('\\', '/').rsplit('/', 1)[-1]
    extension = Path(name).suffix.lower().removeprefix('.')
    if extension not in {'wav', 'flac', 'mp3', 'm4a', 'ogg', 'opus', 'aif', 'aiff'} or not 0 < len(name) <= 200:
        raise ReplacementError('unsupported_audio_format', 422)
    maximum = 16 * 1024**2 if request.role == 'impulse_response' else MAX_SOURCE_BYTES
    if request.role == 'impulse_response' and extension not in {'wav', 'flac'}:
        raise ReplacementError('unsupported_audio_format', 422)
    checksum, size = await run_owned_thread(hash_upload, stream, maximum)
    async with _intake_lock:
        base = db.FILES_DIR / 'artist-production' / 'sources' / 'replacement-intake'
        root = _directory(db.FILES_DIR, base / request.request_id)
        receipt_path = root / 'receipt.json'
        receipt = _AssetReceipt(request=request, sha256=checksum, filename='source.' + extension)
        if receipt_path.exists():
            if receipt_path.is_symlink() or receipt_path.stat().st_size > 65536:
                raise ReplacementError('replacement_artifact_changed')
            try:
                receipt = _AssetReceipt.model_validate_json(receipt_path.read_bytes())
            except ValidationError as error:
                raise ReplacementError('replacement_artifact_changed') from error
            if receipt.request != request or receipt.sha256 != checksum:
                raise ReplacementError('replacement_request_conflict')
            if receipt.response is not None:
                await _verify(receipt.response.selection)
                return receipt.response
        if _closing:
            raise ReplacementError('replacement_busy')
        from .resource_admission import admission_lock, require_setup_idle
        async with admission_lock:
            require_setup_idle()
        target = root / receipt.filename
        if target.exists():
            if target.is_symlink() or await hash_file(target, max_bytes=maximum) != checksum:
                raise ReplacementError('replacement_artifact_changed')
        else:
            if shutil.disk_usage(root).free < size + RESERVE_BYTES:
                raise ReplacementError('replacement_disk_full')
            partial = root / (uuid.uuid4().hex + '.partial')
            copied, copied_size = await run_owned_thread(copy_upload, stream, partial, maximum)
            if copied != checksum or copied_size != size:
                raise ReplacementError('replacement_source_changed')
            partial.replace(target)
        await run_owned_thread(write_object, receipt_path, _json.validate_json(receipt.model_dump_json()))
        result = await run_owned_command(probe_command(target), timeout=30, max_bytes=65536)
        try:
            header = parse_audio_header(result.stdout)
        except MusicAnalysisError as error:
            raise ReplacementError('invalid_audio', 422) from error
        if result.returncode or header.channels not in (1, 2):
            raise ReplacementError('invalid_audio', 422)
        if header.duration > (10 if request.role == 'impulse_response' else 7200):
            raise ReplacementError('audio_too_long', 422)
        try:
            asset = await run_owned_thread(evidence.register_source, target,
                request.declaration.model_copy(update={'original_filename': name}))
        except evidence.ProductionError as error:
            raise ReplacementError('replacement_evidence_required', 422) from error
        if asset.sha256 != checksum:
            raise ReplacementError('replacement_source_changed')
        track_id: int | None = None
        if request.role == 'impulse_response':
            selection = SelectedSource(source=ProductionMusicSource(asset_id=asset.id, sha256=checksum), audio_sha256=checksum)
        else:
            row = db.get_db().execute('SELECT id FROM tracks WHERE audio_path=? ORDER BY id LIMIT 1', (str(target),)).fetchone()
            track_id = (int(row['id']) if row is not None else db.insert_track(model='upload', title=request.title,
                lyrics='', seed=None, duration_ms=header.duration * 1000, wall_ms=None,
                params={'source': 'replacement_intake', 'role': request.role}, audio_path=target, abc_path=None))
            version = retain_original(track_id, target)
            selection = SelectedSource(source=VersionMusicSource(track_id=track_id, version_id=version.id), audio_sha256=checksum)
        response = ReplacementAssetResponse(request_id=request.request_id, role=request.role,
            selection=selection, evidence_asset_id=asset.id, track_id=track_id)
        receipt = _AssetReceipt.model_validate({**receipt.model_dump(), 'response': response})
        await run_owned_thread(write_object, receipt_path, _json.validate_json(receipt.model_dump_json()))
        return response


def _assert_revision(plan: ReplacementPlanResponse, expected: int) -> None:
    if plan.revision != expected:
        raise ReplacementError('replacement_revision_conflict', current_revision=plan.revision)
    if plan.active_attempt_id is not None:
        raise ReplacementError('replacement_busy')


async def update(identifier: str, request: ReplacementUpdateRequest) -> ReplacementPlanResponse:
    async with _lock:
        plan = get(identifier)
        _assert_revision(plan, request.expected_revision)
        for selection in _selections(request.request):
            _source(selection)
        # Artifacts remain immutable, but changed selections/fit cannot reuse an
        # alignment proposal or its review acknowledgement.
        next_plan = ReplacementPlanResponse(id=plan.id, revision=plan.revision + 1, request=request.request,
            mix=plan.mix, artifacts=plan.artifacts, renders=plan.renders, updated_at=_now())
        with _connection() as connection:
            return store.replace_plan(connection, plan.revision, next_plan)


async def edit_mix(identifier: str, request: ReplacementMixEditRequest) -> ReplacementPlanResponse:
    async with _lock:
        plan = get(identifier)
        _assert_revision(plan, request.expected_revision)
        next_plan = ReplacementPlanResponse.model_validate({**plan.model_dump(), 'revision': plan.revision + 1,
            'mix': request.mix, 'status': 'needs_review', 'phase': 'mixing', 'diagnostics': None, 'error_code': None, 'updated_at': _now()})
        with _connection() as connection:
            return store.replace_plan(connection, plan.revision, next_plan)


def _require_prepare_ready(request: ReplacementRequest) -> None:
    if request.separator == 'roformer':
        raise ReplacementError('replacement_separator_unavailable')
    try:
        offline_demucs(request.separator)
    except VoiceSeparationError as error:
        raise ReplacementError('replacement_separator_unavailable') from error
    if request.mode != 'prepared' and request.target.engine == 'rvc':
        from .rvc_models import get as rvc_model
        model = rvc_model(request.target.registered_model_id)
        if not model.ready or model.blocked_reasons:
            raise ReplacementError(model.blocked_reasons[0] if model.blocked_reasons else 'replacement_engine_unavailable')
    if request.mode != 'prepared' and request.target.engine == 'seed-vc':
        _target_files(request.target)


def _source_in_use(request: ReplacementRequest, extra: tuple[SelectedSource, ...] = ()) -> bool:
    return bool(_track_refs(request, extra) & _deleting or _stem_refs(request, extra) & _replacing_stems)


def _stem_refs(request: ReplacementRequest, extra: tuple[SelectedSource, ...] = ()) -> set[int]:
    refs: set[int] = set()
    for selection in (*_selections(request), *extra):
        if selection.source.kind == 'stem':
            refs.add(selection.source.track_id)
        elif selection.source.kind == 'production_asset':
            path = evidence.asset_path(selection.source.asset_id)
            rows = db.get_db().execute("SELECT tracks.id FROM tracks,json_each(CASE WHEN json_valid(stems_json) THEN stems_json ELSE '{}' END) AS stem WHERE stem.value=?",
                (str(path),)).fetchall()
            refs.update(int(row['id']) for row in rows)
    return refs


def _track_refs(request: ReplacementRequest, extra: tuple[SelectedSource, ...] = ()) -> set[int]:
    refs: set[int] = set()
    for item in (*_selections(request), *extra):
        if item.source.kind != 'production_asset':
            refs.add(item.source.track_id)
            continue
        try:
            path = evidence.asset_path(item.source.asset_id)
        except evidence.ProductionError as error:
            raise ReplacementError('replacement_source_missing', 404) from error
        rows = db.get_db().execute('SELECT id AS track_id FROM tracks WHERE audio_path=? UNION SELECT track_id FROM audio_versions WHERE audio_path=?',
            (str(path), str(path))).fetchall()
        refs.update(int(row['track_id']) for row in rows)
    return refs | _stem_refs(request, extra)


def _ir_selection(settings: ReplacementMixSettings) -> SelectedSource | None:
    if settings.space == 'off':
        return None
    if settings.ir_asset_id is None:
        raise ReplacementError('replacement_evidence_required')
    try:
        asset = evidence.get_asset(settings.ir_asset_id)
        path = evidence.asset_path(asset.id)
    except evidence.ProductionError as error:
        raise ReplacementError('replacement_source_missing', 404) from error
    declaration = asset.declaration
    if (declaration is None or asset.origin not in {'human_recorded', 'licensed_third_party'}
            or not declaration.rights_holder.strip() or 'production' not in declaration.permitted_uses
            or 'production' in declaration.blocked_uses or not asset.reviews or asset.reviews[-1].status != 'approved'):
        raise ReplacementError('replacement_evidence_required')
    if path.suffix.lower() not in {'.wav', '.flac'} or path.stat().st_size > 16 * 1024**2:
        raise ReplacementError('replacement_invalid_request', 422)
    try:
        info = sf.info(path)
    except RuntimeError as error:
        raise ReplacementError('replacement_invalid_request', 422) from error
    if info.channels not in {1, 2} or not 8000 <= info.samplerate <= 192000 or not 0 < info.frames <= info.samplerate * 10:
        raise ReplacementError('replacement_invalid_request', 422)
    return SelectedSource(source=ProductionMusicSource(asset_id=asset.id, sha256=asset.sha256), audio_sha256=asset.sha256)


async def _start(identifier: str, action: Literal['prepare', 'preview', 'render', 'remix'],
                 request: ReplacementActionRequest | ReplacementRenderRequest) -> ReplacementPlanResponse:
    from .resource_admission import admission_lock, require_setup_idle
    async with admission_lock, _lock:
        plan = get(identifier)
        candidate = store.StoredReplacementAttempt(id=uuid.uuid4().hex, plan_id=identifier,
            revision=request.expected_revision, action=action, request=request,
            phase='inspecting' if action == 'prepare' else 'mixing', created_at=_now())
        with _connection() as connection:
            old = connection.execute('SELECT id FROM replacement_attempts WHERE plan_id=? AND request_id=?',
                (identifier, request.request_id)).fetchone()
            if old is not None:
                store.claim_attempt(connection, candidate)  # validates identical idempotent body first
                return get(identifier)
        _assert_revision(plan, request.expected_revision)
        if _closing or work_busy() or _source_in_use(plan.request):
            raise ReplacementError('replacement_busy')
        require_setup_idle()
        if action == 'prepare':
            _require_prepare_ready(plan.request)
        else:
            if not isinstance(request, ReplacementRenderRequest):
                raise ReplacementError('replacement_invalid_request', 422)
            if action == 'preview' and request.preview_window is None or action != 'preview' and request.preview_window is not None:
                raise ReplacementError('replacement_invalid_request', 422)
            impulse = await run_owned_thread(_ir_selection, request.mix)
            candidate = store.StoredReplacementAttempt.model_validate({**candidate.model_dump(), 'impulse_response': impulse})
            if _source_in_use(plan.request, (impulse,) if impulse is not None else ()):
                raise ReplacementError('replacement_source_in_use')
            if plan.request.mode != 'convert' and (plan.alignment is None or not plan.alignment_reviewed
                    or set(plan.reviewed_phrase_ids) != {item.phrase_id for item in plan.alignment.phrases}):
                raise ReplacementError('replacement_alignment_invalid')
        estimate = await storage_preview(plan.request)
        if not estimate.can_start:
            raise ReplacementError('replacement_disk_full')
        for selected in _selections(plan.request):
            await _verify(selected)
        if plan.request.mode != 'prepared' and plan.request.target.engine == 'seed-vc':
            from .voice_authorization import authorize
            candidate = store.StoredReplacementAttempt.model_validate({**candidate.model_dump(),
                'authorization': authorize(plan.request.target.voice_id, 'convert')})
        elif plan.request.mode != 'prepared' and plan.request.target.engine == 'rvc':
            from .rvc_models import get as rvc_model
            from .voice_authorization import authorize
            model = rvc_model(plan.request.target.registered_model_id)
            if not model.ready or model.blocked_reasons:
                raise ReplacementError(model.blocked_reasons[0] if model.blocked_reasons else 'replacement_engine_unavailable')
            if model.weights_license.restriction == 'noncommercial':
                from .music_terms import snapshot
                if not snapshot('ace_step', 'generation').noncommercial_enabled:
                    raise HTTPException(409, 'music_noncommercial_disabled')
            candidate = store.StoredReplacementAttempt.model_validate({**candidate.model_dump(),
                'authorization': authorize(model.voice_id, 'convert')})
        with _connection() as connection:
            owned = store.claim_attempt(connection, candidate, admit=require_setup_idle)
        _tasks[owned.id] = asyncio.create_task(_run(owned.id), name=f'replacement-{owned.id}')
        return get(identifier)


async def prepare(identifier: str, request: ReplacementActionRequest) -> ReplacementPlanResponse:
    return await _start(identifier, 'prepare', request)


async def render(identifier: str, request: ReplacementRenderRequest, *,
                 action: Literal['preview', 'render', 'remix'] = 'render') -> ReplacementPlanResponse:
    return await _start(identifier, action, request)


def _attempt(identifier: str) -> store.StoredReplacementAttempt:
    with _connection() as connection:
        return store.get_attempt(connection, identifier)


def _phase(identifier: str, phase: ReplacementPhase, worker: WorkerIdentity | None = None) -> None:
    attempt = _attempt(identifier)
    running = store.StoredReplacementAttempt.model_validate({**attempt.model_dump(), 'status': 'running',
        'phase': phase, 'worker': worker})
    with _connection() as connection:
        store.save_attempt(connection, running)


async def _drain(identifier: str) -> None:
    proc = _processes.get(identifier)
    if proc is not None:
        await await_cleanup(kill_process_tree(proc))
        if proc.stdin is not None:
            proc.stdin.close()
        _processes.pop(identifier, None)
    attempt = _attempt(identifier)
    if attempt.worker is not None:
        if proc is None and not await terminate_verified(attempt.worker):
            _unverified[identifier] = attempt.worker
            raise ReplacementError('replacement_cleanup_pending')
        if attempt.status in {'queued', 'running'}:
            _phase(identifier, attempt.phase)
        _unverified.pop(identifier, None)


async def _spawn(identifier: str, argv: list[str], cwd: Path, env: Mapping[str, str], stdout: int) -> asyncio.subprocess.Process:
    attempt = _attempt(identifier)
    owner_root = _workspace(attempt.plan_id, create=True)
    root = _directory(owner_root, owner_root / 'attempts' / identifier)
    def remember(identity: WorkerIdentity) -> None:
        _phase(identifier, _attempt(identifier).phase, identity)
    proc = await spawn_owned(argv, receipt_path=root / 'worker.json', cwd=cwd, env=env,
        stdout=stdout, on_identity=remember)
    _processes[identifier] = proc
    return proc


async def _command(identifier: str, argv: list[str], timeout: float) -> bytes:
    proc = await _spawn(identifier, argv, _workspace(_attempt(identifier).plan_id), os.environ.copy(), asyncio.subprocess.PIPE)
    try:
        output = await read_owned_output(proc, max_bytes=4 * 1024**2, timeout=timeout)
        if proc.returncode:
            raise ReplacementError('replacement_failed')
        return output
    finally:
        await _drain(identifier)


async def _diagnostic_command(identifier: str, argv: list[str], timeout: float) -> bytes:
    try:
        return await _command(identifier, argv, timeout)
    except (TimeoutError, OSError, ReplacementError) as error:
        if isinstance(error, ReplacementError) and error.code != 'replacement_failed':
            raise
        await _drain(identifier)
        attempt = _attempt(identifier)
        if attempt.worker is not None or attempt.cleanup_pending or identifier in _unverified or identifier in _processes:
            raise ReplacementError('replacement_cleanup_pending') from error
        logger.warning('Replacement diagnostic worker unavailable after verified drain', exc_info=True)
        raise DiagnosticWorkerUnavailable() from error


async def _audio(path: Path, asset: SourceAsset) -> ResolvedDspSource:
    info = await run_owned_thread(sf.info, path)
    if info.channels not in (1, 2) or not 8000 <= info.samplerate <= 192000 or not 0 < info.frames <= 7200 * info.samplerate:
        raise ReplacementError('replacement_output_invalid')
    channels: Literal[1, 2] = 1 if info.channels == 1 else 2
    return ResolvedDspSource(path, ProductionMusicSource(asset_id=asset.id, sha256=asset.sha256),
        asset.sha256, info.samplerate, channels, info.frames)


def _music_source(audio: ResolvedDspSource) -> MusicSourceRef:
    if audio.source.kind == 'transform':
        raise ReplacementError('replacement_output_invalid')
    return audio.source


def _parent(selected: SelectedSource, path: Path) -> SourceAsset:
    source = selected.source
    if source.kind == 'version':
        return evidence.register_version_reference(source.track_id, source.version_id, selected.audio_sha256)
    if source.kind == 'production_asset':
        asset = evidence.get_asset(source.asset_id)
        if asset.sha256 != selected.audio_sha256:
            raise ReplacementError('replacement_source_changed')
        return asset
    from . import audio_versions
    versions = audio_versions.list_versions(source.track_id).versions
    original = next((item for item in versions if item.kind == 'original'), None)
    if original is None:
        raise ReplacementError('replacement_source_missing', 404)
    original_path = audio_versions.resolve_source(source.track_id, original.id)
    from .export_provenance import digest
    parent = evidence.register_version_reference(source.track_id, original.id, digest(original_path))
    return evidence.create_transform(path, 'stem', [parent.id],
        f'Existing catalog stem {source.stem}; hash-bound operator association, separator association unverified.')


async def _snapshot(identifier: str, selection: SelectedSource) -> ResolvedDspSource:
    attempt = _attempt(identifier)
    owner_root = _workspace(attempt.plan_id, create=True)
    root = _directory(owner_root, owner_root / 'sources')
    source = await _verify(selection)
    signature = _signature(source)
    target = root / (selection.audio_sha256 + source.suffix.lower())
    if target.exists():
        if target.is_symlink() or await hash_file(target, max_bytes=MAX_SOURCE_BYTES) != selection.audio_sha256:
            raise ReplacementError('replacement_artifact_changed')
    else:
        estimate = await storage_preview(get(attempt.plan_id).request)
        if not estimate.can_start:
            raise ReplacementError('replacement_disk_full')
        await copy_verified(source, target, selection.audio_sha256, max_bytes=signature[2])
    if _signature(source) != signature:
        raise ReplacementError('replacement_source_changed')
    parent = await run_owned_thread(_parent, selection, source)
    decoded = root / (selection.audio_sha256 + '-decoded-v1.wav')
    receipt = decoded.with_suffix('.json')
    header = parse_audio_header(await _command(identifier, probe_command(target), 30))
    if header.duration > 7200:
        raise ReplacementError('audio_too_long', 422)
    frame_limit = round(header.duration * 48000) + 4800
    limit = frame_limit * header.channels * 4 + 8192
    if decoded.exists() and not receipt.exists():
        # A crash may leave a validated decode before its typed receipt. Keep
        # that unexplained file and rebuild from the retained raw snapshot.
        await run_owned_thread(decoded.replace, root / (uuid.uuid4().hex + '-unverified.wav'))
    if not decoded.exists():
        partial = root / (identifier + '-decoded.partial.wav')
        partial.unlink(missing_ok=True)  # no live worker: admission/recovery owns this attempt
        await _command(identifier, [tool('ffmpeg'), '-v', 'error', '-xerror', '-n', *INPUT,
            '-i', str(target), '-map', '0:a:0', '-vn', '-sn', '-dn', '-t', str(header.duration + .1),
            '-ar', '48000', '-c:a', 'pcm_f32le', '-fs', str(limit), str(partial)], 300)
        info = await run_owned_thread(sf.info, partial)
        if (abs(info.frames / info.samplerate - header.duration) > .05
                or info.frames >= frame_limit or partial.stat().st_size >= limit):
            raise ReplacementError('replacement_output_duration_mismatch')
        await run_owned_thread(partial.replace, decoded)
        asset = await run_owned_thread(evidence.create_transform, decoded, 'stem', [parent.id],
            'Bounded float PCM decode of immutable source snapshot; original raw source retained unchanged.')
        recorded = _DecodedSnapshot(source_sha256=selection.audio_sha256,
            audio_sha256=asset.sha256, evidence_asset_id=asset.id)
        await run_owned_thread(write_object, receipt, _json.validate_json(recorded.model_dump_json()))
    if decoded.is_symlink() or decoded.resolve() != decoded:
        raise ReplacementError('replacement_artifact_changed')
    info = await run_owned_thread(sf.info, decoded)
    if (info.samplerate != 48000 or info.channels != header.channels
            or abs(info.frames / info.samplerate - header.duration) > .05 or decoded.stat().st_size >= limit):
        raise ReplacementError('replacement_artifact_changed')
    if receipt.is_symlink() or receipt.stat().st_size > 4096:
        raise ReplacementError('replacement_artifact_changed')
    recorded = _DecodedSnapshot.model_validate_json(receipt.read_bytes())
    asset = evidence.get_asset(recorded.evidence_asset_id)
    if (recorded.source_sha256 != selection.audio_sha256 or recorded.audio_sha256 != asset.sha256
            or await hash_file(decoded, max_bytes=MAX_PCM_BYTES) != recorded.audio_sha256
            or evidence.asset_path(asset.id) != decoded):
        raise ReplacementError('replacement_artifact_changed')
    return await _audio(decoded, asset)


async def _retained(identifier: str, role: ReplacementArtifactRole, path: Path,
                    parents: list[str], source_hashes: list[str], key: str, description: str,
                    *, alignment_revision: int | None = None, modules: tuple[ModuleId, ...] = (),
                    processing: ReplacementProcessing | None = None) -> store.StoredReplacementArtifact:
    attempt = _attempt(identifier)
    if (path.is_symlink() or path.resolve() != path or not path.is_file()
            or not path.is_relative_to(_workspace(attempt.plan_id))):
        raise ReplacementError('replacement_output_invalid')
    owner_root = _workspace(attempt.plan_id, create=True)
    root = _directory(owner_root, owner_root / 'artifacts')
    digest = await hash_file(path, max_bytes=MAX_PCM_BYTES)
    with _connection() as connection:
        cached = store.find_artifact(connection, attempt.plan_id, role, key)
    if cached is not None:
        await artifact_path(attempt.plan_id, cached.public.id)
        if cached.public.sha256 != digest:
            raise ReplacementError('replacement_artifact_changed')
        return cached
    artifact_id = uuid.uuid4().hex
    destination = root / (artifact_id + '.wav')
    await copy_verified(path, destination, digest, max_bytes=path.stat().st_size)
    asset = await run_owned_thread(evidence.create_transform, destination,
        'voice_converted' if role == 'converted_dry' else 'master' if role == 'master' else 'stem',
        list(dict.fromkeys(parents)), description[:1000])
    audio = await _audio(destination, asset)
    async def command(argv: list[str], timeout: float) -> bytes:
        return await _command(identifier, argv, timeout)
    metrics = await _metrics(audio, _workspace(attempt.plan_id), command, artifact_id)
    public = ReplacementArtifact(id=artifact_id, evidence_asset_id=asset.id, role=role, sha256=digest,
        source_sha256s=list(dict.fromkeys(source_hashes)), settings_sha256=key,
        duration_sec=audio.duration_frames / audio.sample_rate_hz, sample_rate_hz=audio.sample_rate_hz,
        channels=1 if audio.channels == 1 else 2, alignment_revision=alignment_revision, measurement=metrics,
        processing=processing,
        audio_url=f'/api/voices/replace/plans/{attempt.plan_id}/artifacts/{artifact_id}/audio')
    from .music_evidence import merge_music_evidence
    plan = get(attempt.plan_id)
    inherited: list[MusicEvidence | None] = [_source_music(selection) for selection in
        (*_selections(plan.request), *((attempt.impulse_response,) if attempt.impulse_response else ()))
        if selection.audio_sha256 in source_hashes]
    origins: set[ContentOrigin] = {_source_origin(selection) for selection in
        (*_selections(plan.request), *((attempt.impulse_response,) if attempt.impulse_response else ()))
        if selection.audio_sha256 in source_hashes}
    with _connection() as connection:
        for parent_id in parents:
            row = connection.execute("SELECT id FROM replacement_artifacts WHERE plan_id=? AND json_extract(body_json,'$.evidence_asset_id')=? LIMIT 1",
                (plan.id, parent_id)).fetchone()
            if row is not None:
                parent_artifact = store.get_artifact(connection, plan.id, str(row['id']))
                inherited.append(parent_artifact.music_evidence)
                origins.add(parent_artifact.content_origin)
    if role in {'source_vocal', 'backing'}:
        modules = (*modules, 'separation')
    if role == 'converted_dry':
        modules = (*modules, 'singing' if plan.request.mode != 'prepared' and plan.request.target.engine == 'seed-vc' else 'rvc')
        if plan.request.mode != 'prepared' and plan.request.target.engine == 'rvc':
            from .rvc_models import get as rvc_model
            model = rvc_model(plan.request.target.registered_model_id)
            inherited.append(_rvc_music(model))
    inherited.extend(_module_music(module) for module in modules)
    if role == 'converted_dry':
        content_origin: ContentOrigin = 'generated' if origins == {'generated'} else 'mixed'
    else:
        content_origin = 'generated' if origins == {'generated'} else 'mixed' if origins & {'generated', 'mixed'} else 'unknown'
    stored = store.StoredReplacementArtifact(plan_id=attempt.plan_id, public=public,
        relative_path=f'replacements/{attempt.plan_id}/artifacts/{artifact_id}.wav',
        duration_frames=audio.duration_frames, created_at=_now(), music_evidence=merge_music_evidence(inherited), content_origin=content_origin)
    # A bounded typed stage receipt precedes catalog insertion. Retry can adopt
    # completed work after a crash between these two operations.
    receipt = root / (artifact_id + '.json')
    await run_owned_thread(write_object, receipt, _json.validate_json(stored.model_dump_json()))
    with _connection() as connection:
        return store.add_artifact(connection, stored)


async def artifact_path(plan_id: str, identifier: str) -> Path:
    with _connection() as connection:
        store.get_plan(connection, plan_id)
        artifact = store.get_artifact(connection, plan_id, identifier)
    path = db.FILES_DIR / artifact.relative_path
    if (path.is_symlink() or path.resolve() != path or not path.is_file()
            or await hash_file(path, max_bytes=MAX_PCM_BYTES) != artifact.public.sha256):
        raise ReplacementError('replacement_artifact_changed')
    return path


async def _resolved(artifact: store.StoredReplacementArtifact) -> ResolvedDspSource:
    path = await artifact_path(artifact.plan_id, artifact.public.id)
    asset = evidence.get_asset(artifact.public.evidence_asset_id)
    if asset.sha256 != artifact.public.sha256:
        raise ReplacementError('replacement_artifact_changed')
    return replace(await _audio(path, asset), metrics=artifact.public.measurement)


def _prepare_key(request: ReplacementRequest) -> str:
    return _digest({'source': request.source.model_dump(), 'separator': request.separator, 'recipe': RECIPE})


def _rvc_music(model: RvcRegisteredModel) -> MusicEvidence:
    declaration = model.weights_license
    return MusicEvidence(components=[MusicComponentEvidence(component_id='rvc-voice:' + model.id,
        scope='model', restriction=declaration.restriction, redistribution=declaration.redistribution,
        copyleft=declaration.copyleft, declared_license=declaration.declared_license,
        source_url=declaration.source_url, evidence=declaration.evidence,
        identities=declaration.identities)], lineage_complete=True)


async def _cached_artifact(plan: ReplacementPlanResponse, role: ReplacementArtifactRole,
                           key: str) -> store.StoredReplacementArtifact | None:
    with _connection() as connection:
        artifact = store.find_artifact(connection, plan.id, role, key)
    if artifact is None:
        # Only a fully validated retained stage receipt can be adopted. A raw
        # model output or unexplained partial is never sufficient recovery proof.
        root = _workspace(plan.id) / 'artifacts'
        receipts = list(root.glob('*.json'))[:129] if root.is_dir() else []
        if len(receipts) > 128:
            raise ReplacementError('replacement_storage_failed', 503)
        for receipt in receipts:
            if receipt.is_symlink() or receipt.stat().st_size > 1024**2:
                raise ReplacementError('replacement_artifact_changed')
            try:
                candidate = store.StoredReplacementArtifact.model_validate_json(receipt.read_bytes())
            except ValidationError as error:
                raise ReplacementError('replacement_artifact_changed') from error
            if candidate.plan_id != plan.id or receipt.stem != candidate.public.id:
                raise ReplacementError('replacement_artifact_changed')
            if candidate.public.role != role or candidate.public.settings_sha256 != key:
                continue
            path = db.FILES_DIR / candidate.relative_path
            asset = evidence.get_asset(candidate.public.evidence_asset_id)
            if (path.is_symlink() or path.resolve() != path or not path.is_file()
                    or evidence.asset_path(asset.id) != path or asset.sha256 != candidate.public.sha256
                    or await hash_file(path, max_bytes=MAX_PCM_BYTES) != candidate.public.sha256):
                raise ReplacementError('replacement_artifact_changed')
            with _connection() as connection:
                artifact = store.add_artifact(connection, candidate)
            break
    if artifact is not None:
        extra = _attempt(plan.active_attempt_id).impulse_response if plan.active_attempt_id else None
        if not set(artifact.public.source_sha256s).issubset({selection.audio_sha256 for selection in
                (*_selections(plan.request), *((extra,) if extra is not None else ())) }):
            raise ReplacementError('replacement_artifact_changed')
        await _resolved(artifact)
    return artifact


def _module_music(module: ModuleId) -> MusicEvidence:
    from .module_licenses import declarations
    return MusicEvidence(components=[MusicComponentEvidence(component_id=item.component_id,
        scope=item.scope, restriction=item.restriction, redistribution=item.redistribution,
        copyleft=item.copyleft, declared_license=item.declared_license, source_url=item.source_url,
        evidence=item.evidence, identities=item.identities) for item in declarations(module)], lineage_complete=True)


def _source_music(selection: SelectedSource) -> MusicEvidence:
    from .music_evidence import merge_music_evidence, track_music_evidence
    source = selection.source
    if source.kind != 'production_asset':
        return track_music_evidence(source.track_id)
    inherited: list[MusicEvidence | None] = []
    # Match exact authoritative paths through a bounded existing asset graph;
    # never hash/scan every track or infer a provider from free-form notes.
    for asset in evidence.graph(source.asset_id):
        path = evidence.asset_path(asset.id)
        rows = db.get_db().execute('SELECT track_id FROM audio_versions WHERE audio_path=? LIMIT 2', (str(path),)).fetchall()
        if rows:
            inherited.extend(track_music_evidence(int(row['track_id'])) for row in rows)
        elif not asset.parent_ids:
            inherited.append(None)
    return merge_music_evidence(inherited)


def _source_origin(selection: SelectedSource) -> ContentOrigin:
    source = selection.source
    if source.kind == 'production_asset':
        roots = [asset for asset in evidence.graph(source.asset_id) if not asset.parent_ids]
        return 'generated' if roots and all(asset.origin == 'ai_generated' for asset in roots) else 'mixed' if any(asset.origin == 'ai_generated' for asset in roots) else 'unknown'
    if source.kind == 'stem':
        return 'unknown'  # operator association does not prove separator provenance
    from .export_provenance import track_component
    return track_component(source.track_id, source.version_id, selection.audio_sha256).content_origin


async def _prepare(identifier: str) -> None:
    from . import stems
    attempt = _attempt(identifier)
    plan = get(attempt.plan_id)
    root = _workspace(plan.id, create=True)
    work = _directory(root, root / 'attempts' / identifier)
    if plan.request.mode != 'prepared' and plan.request.target.engine == 'seed-vc':
        await _model_snapshot(identifier, plan.request.target)
    elif plan.request.mode != 'prepared' and plan.request.target.engine == 'rvc':
        await _rvc_snapshot(identifier, plan.request.target.registered_model_id)
    source = await _snapshot(identifier, plan.request.source)
    key = _prepare_key(plan.request)
    vocal = await _cached_artifact(plan, 'source_vocal', key)
    backing = await _cached_artifact(plan, 'backing', key)
    prepared: store.StoredReplacementArtifact | None = None
    async def command(argv: list[str], timeout: float) -> bytes:
        return await _command(identifier, argv, timeout)
    if vocal is None or backing is None:
        if plan.request.separator == 'roformer':
            raise ReplacementError('replacement_separator_unavailable')
        ready = offline_demucs(plan.request.separator)
        _phase(identifier, 'separating')
        async def spawn(argv: list[str], cwd: Path, env: Mapping[str, str], stdout: int) -> asyncio.subprocess.Process:
            offline = await run_owned_thread(ready.command, source.path, work / 'separation')
            return await _spawn(identifier, offline, ready.engine,
                {**env, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'UV_OFFLINE': '1'}, stdout)
        try:
            separated = await stems.separate_file(source.path, work / 'separation', quality=plan.request.separator,
                log_name='replacement_' + identifier, spawn=spawn, gpu_reason='voice_preparation', gpu_label=plan.request.title)
        finally:
            await _drain(identifier)
        # Custom spawn uses a private local repository and its own output root.
        required = {'vocals', 'drums', 'bass', 'other'}
        if set(separated) != required or any(path.is_symlink() or path.resolve() != path
                or not path.is_relative_to(work) for path in separated.values()):
            raise ReplacementError('replacement_output_invalid')
        parent_id = source.source.asset_id if source.source.kind == 'production_asset' else ''
        vocal = await _retained(identifier, 'source_vocal', separated['vocals'], [parent_id],
            [plan.request.source.audio_sha256], key, 'Fresh offline Demucs vocal from retained exact source; weights/data rights unreviewed.')
        inputs: list[DspMixInput] = []
        sources: dict[str, ResolvedDspSource] = {}
        for name in ('drums', 'bass', 'other'):
            asset = await run_owned_thread(evidence.create_transform, separated[name], 'stem', [parent_id], 'Fresh offline Demucs ' + name)
            audio = await _audio(separated[name], asset)
            item_id = uuid.uuid4().hex
            sources[item_id] = audio
            inputs.append(DspMixInput(id=item_id, source=audio.source, source_sha256=audio.sha256,
                duration_sec=min(source.duration_frames / source.sample_rate_hz, audio.duration_frames / audio.sample_rate_hz)))
        combined = await render_mix(DspMixRequest(revision=plan.revision, sample_rate_hz=48000,
            channels=1 if source.channels == 1 else 2, duration_sec=source.duration_frames / source.sample_rate_hz, inputs=inputs), sources, work, command)
        backing = await _retained(identifier, 'backing', work / combined.artifact_name, [parent_id],
            [plan.request.source.audio_sha256], key, 'Unnormalized sum of fresh drums/bass/other; no vocal subtraction or reused catalog stems.')
    if plan.request.mode != 'convert':
        prepared_key = _digest({'prepared': plan.request.prepared.model_dump(), 'recipe': RECIPE})
        prepared = await _cached_artifact(plan, 'prepared_dry', prepared_key)
        if prepared is None:
            take = await _snapshot(identifier, plan.request.prepared)
            _phase(identifier, 'cleaning')
            duration = take.duration_frames / take.sample_rate_hz
            converted = await render_transform(DspTransformRequest(source=_music_source(take),
                source_sha256=take.sha256, revision=plan.revision, sample_rate_hz=48000,
                anchors=[WarpAnchor(source_sec=0.0, target_sec=0.0), WarpAnchor(source_sec=duration, target_sec=duration)],
                output_duration_sec=duration), take, work, command)
            parent_id = take.source.asset_id if take.source.kind == 'production_asset' else ''
            prepared = await _retained(identifier, 'prepared_dry', work / converted.artifact_name,
                [parent_id], [plan.request.prepared.audio_sha256], prepared_key, 'Retained dry prepared performance; no silence removal or rights declaration.')
        prepared = await _processed_performance(identifier, prepared, work)
        reference, take = await _resolved(vocal), await _resolved(prepared)
        current = get(plan.id)
        if (current.alignment is not None
                and current.alignment.request.reference == _music_source(reference)
                and current.alignment.request.reference_sha256 == reference.sha256
                and current.alignment.request.prepared == _music_source(take)
                and current.alignment.request.prepared_sha256 == take.sha256):
            await _measure_diagnostics(identifier, vocal, backing, fit_input=prepared)
            return  # Refreshing unchanged retained inputs must preserve reviewed timing.
        _phase(identifier, 'aligning')
        proposal = await align_prepared_vocal(AlignmentRequest(reference=_music_source(reference),
            reference_sha256=reference.sha256, prepared=_music_source(take), prepared_sha256=take.sha256,
            revision=plan.revision, min_duration_ratio=max(.75, 1 / plan.request.fit.max_stretch),
            max_duration_ratio=plan.request.fit.max_stretch), reference, take, work, command)
        if len(proposal.anchors) > 1000 or len(proposal.phrases) > 1000:
            raise ReplacementError('replacement_alignment_capacity')
        current = get(plan.id)
        state = ReplacementPlanResponse.model_validate({**current.model_dump(), 'alignment': proposal,
            'alignment_reviewed': False, 'reviewed_phrase_ids': [], 'warnings': proposal.warnings, 'updated_at': _now()})
        with _connection() as connection:
            store.save_state(connection, state, expected_attempt_id=identifier)
    await _measure_diagnostics(identifier, vocal, backing, fit_input=prepared)


async def _measure_diagnostics(identifier: str, reference: store.StoredReplacementArtifact,
                               backing: store.StoredReplacementArtifact, *,
                               fit_input: store.StoredReplacementArtifact | None = None, fitted: bool = False,
                               converted: store.StoredReplacementArtifact | None = None,
                               matched: store.StoredReplacementArtifact | None = None) -> None:
    plan = get(_attempt(identifier).plan_id)
    async def identity(artifact: store.StoredReplacementArtifact | None) -> DiagnosticArtifactIdentity | None:
        if artifact is None:
            return None
        await artifact_path(plan.id, artifact.public.id)
        return DiagnosticArtifactIdentity(artifact_id=artifact.public.id, sha256=artifact.public.sha256,
            sample_rate_hz=artifact.public.sample_rate_hz, channels=artifact.public.channels,
            duration_frames=artifact.duration_frames)
    reference_identity, backing_identity = await identity(reference), await identity(backing)
    if reference_identity is None or backing_identity is None:
        raise ReplacementError('replacement_artifact_missing')
    fit_identity, converted_identity, matched_identity = await identity(fit_input), await identity(converted), await identity(matched)
    _phase(identifier, 'analyzing')
    async def command(argv: list[str], timeout: float) -> bytes:
        return await _diagnostic_command(identifier, argv, timeout)
    root = _workspace(plan.id)
    try:
        report = await measure_diagnostics(plan, identifier, root / 'artifacts', root / 'attempts' / identifier,
            reference_identity, backing_identity, command, fit_input=fit_identity, fitted=fitted,
            converted=converted_identity, matched=matched_identity)
    except DiagnosticsError as error:
        raise ReplacementError(error.code) from error
    current = get(plan.id)
    with _connection() as connection:
        store.save_state(connection, ReplacementPlanResponse.model_validate({**current.model_dump(),
            'diagnostics': report, 'updated_at': _now()}), expected_attempt_id=identifier)


async def _processed_performance(identifier: str, source: store.StoredReplacementArtifact,
                                 root: Path) -> store.StoredReplacementArtifact:
    plan = get(source.plan_id)
    fit = plan.request.fit
    if fit.cleanup == 'off' and fit.keep_breaths:
        return source
    key = _digest({'dry': source.public.sha256, 'cleanup': fit.cleanup,
        'keep_breaths': fit.keep_breaths, 'recipe': CLEANUP_RECIPE})
    cached = await _cached_artifact(plan, 'prepared_clean', key)
    if cached is not None:
        return cached
    if _attempt(identifier).action == 'remix':
        raise ReplacementError('replacement_artifact_missing', 404)
    output = root / (uuid.uuid4().hex + '-clean.wav')
    _phase(identifier, 'cleaning')
    await run_owned_thread(prepare_performance_file, await artifact_path(plan.id, source.public.id), output,
        cleanup=fit.cleanup, keep_breaths=fit.keep_breaths)
    return await _retained(identifier, 'prepared_clean', output, [source.public.evidence_asset_id],
        source.public.source_sha256s, key,
        'Conservative audition tail attenuation with explicit bounded context; optional quiet-content attenuation <=3dB. '
        'No breath detection, cropping, normalization, or dereverberation guarantee.',
        processing=ReplacementProcessing(cleanup_recipe=CLEANUP_RECIPE if fit.cleanup == 'light' else None,
            low_level_attenuation_db=3.0 if not fit.keep_breaths else None))


async def edit_alignment(identifier: str, request: AlignmentEditRequest) -> ReplacementPlanResponse:
    async with _lock:
        plan = get(identifier)
        _assert_revision(plan, request.expected_revision)
        current = plan.alignment
        if current is None or request.alignment.id != current.id or request.alignment.request != current.request:
            raise ReplacementError('replacement_alignment_invalid')
        if (request.alignment.phrases != current.phrases
                or not set(request.reviewed_phrase_ids).issubset({item.phrase_id for item in current.phrases})):
            raise ReplacementError('replacement_alignment_invalid')
        try:
            validate_anchors(request.alignment.anchors, max(.75, 1 / plan.request.fit.max_stretch), plan.request.fit.max_stretch)
        except ValueError as error:
            raise ReplacementError('replacement_alignment_invalid', 422) from error
        # Validate frame rounding and endpoint containment against owned files,
        # never client-supplied duration/status/confidence.
        from .music_dsp import frame_anchors
        prepared_id = current.request.prepared.asset_id if current.request.prepared.kind == 'production_asset' else ''
        prepared = next((item for item in plan.artifacts if item.evidence_asset_id == prepared_id), None)
        if prepared is None:
            raise ReplacementError('replacement_alignment_invalid')
        with _connection() as connection:
            audio = await _resolved(store.get_artifact(connection, plan.id, prepared.id))
        anchors = request.alignment.anchors
        reference_id = current.request.reference.asset_id if current.request.reference.kind == 'production_asset' else ''
        reference = next((item for item in plan.artifacts if item.evidence_asset_id == reference_id), None)
        if reference is None:
            raise ReplacementError('replacement_alignment_invalid')
        with _connection() as connection:
            reference_audio = await _resolved(store.get_artifact(connection, plan.id, reference.id))
        if round(anchors[-1].target_sec * reference_audio.sample_rate_hz) > reference_audio.duration_frames:
            raise ReplacementError('replacement_alignment_invalid', 422)
        duration = anchors[-1].target_sec - anchors[0].target_sec
        frame_anchors(DspTransformRequest(source=current.request.prepared,
            source_sha256=current.request.prepared_sha256, revision=plan.revision + 1, anchors=anchors,
            min_duration_ratio=max(.75, 1 / plan.request.fit.max_stretch), max_duration_ratio=plan.request.fit.max_stretch,
            output_duration_sec=duration), audio)
        proposal = AlignmentResult.model_validate({**current.model_dump(), 'anchors': anchors,
            'status': 'needs_review', 'request': {**current.request.model_dump(), 'revision': plan.revision + 1,
                'manual_anchors': anchors}, 'matcher_method': 'manual-reviewed-anchors-v1',
            'configuration_sha256': _digest({'anchors': [item.model_dump() for item in anchors], 'recipe': RECIPE}),
            'created_at': _now()})
        reviewed = set(request.reviewed_phrase_ids) == {item.phrase_id for item in current.phrases}
        state = ReplacementPlanResponse.model_validate({**plan.model_dump(), 'revision': plan.revision + 1,
            'status': 'needs_review', 'phase': 'aligning', 'alignment': proposal,
            'reviewed_phrase_ids': request.reviewed_phrase_ids, 'alignment_reviewed': reviewed, 'diagnostics': None, 'error_code': None, 'updated_at': _now()})
        with _connection() as connection:
            return store.replace_plan(connection, plan.revision, state)


async def _finish(identifier: str, status: Literal['done', 'failed', 'cancelled', 'interrupted'],
                  error: ReplacementErrorCode | None = None) -> None:
    attempt = _attempt(identifier)
    plan = get(attempt.plan_id)
    if plan.active_attempt_id != identifier:
        return
    pending = identifier in _processes or identifier in _unverified
    if not pending:
        with _connection() as connection:
            published = connection.execute('SELECT 1 FROM replacement_renders WHERE id=? AND status=?', (identifier, 'done')).fetchone()
        if published is not None:
            status, error = 'done', None
    terminal = store.StoredReplacementAttempt.model_validate({**attempt.model_dump(), 'status': status,
        'cleanup_pending': pending, 'worker': attempt.worker if pending else None,
        'finished_at': _now(), 'error_code': 'replacement_cleanup_pending' if pending else error})
    plan_status = 'failed' if pending else ('needs_review' if status == 'done' and attempt.action in {'prepare', 'preview'} else status)
    state = ReplacementPlanResponse.model_validate({**plan.model_dump(), 'status': plan_status,
        'phase': 'complete' if plan_status == 'done' else attempt.phase,
        'active_attempt_id': identifier if pending else None, 'error_code': terminal.error_code, 'updated_at': _now()})
    with _connection() as connection:
        store.finish_attempt(connection, terminal, state)
    if attempt.action != 'prepare' and plan.request.mode != 'prepared' and plan.request.target.engine == 'seed-vc':
        from .voice_authorization import record
        record(attempt.authorization, 'convert', status, identifier,
            voice_id=plan.request.target.voice_id, error_code=terminal.error_code or '')
    elif attempt.action != 'prepare' and plan.request.mode != 'prepared' and plan.request.target.engine == 'rvc':
        from .rvc_models import get as rvc_model
        from .voice_authorization import record
        record(attempt.authorization, 'convert', status, identifier,
            voice_id=rvc_model(plan.request.target.registered_model_id).voice_id, error_code=terminal.error_code or '')


async def _run(identifier: str) -> None:
    try:
        async with asyncio.timeout(7200):
            _phase(identifier, 'inspecting')
            if _attempt(identifier).action == 'prepare':
                await _prepare(identifier)
            else:
                await _render(identifier)
            await _finish(identifier, 'done')
    except asyncio.CancelledError:
        await _finish(identifier, 'cancelled')
    except Exception as error:
        logger.exception('Replacement attempt failed: %s', identifier)
        if isinstance(error, (ReplacementError, RvcModelError)):
            code = error.code
        elif isinstance(error, TimeoutError):
            code = 'replacement_timeout'
        elif isinstance(error, (MusicDspError, AudioQualityError)):
            code = 'replacement_engine_unavailable' if getattr(error, 'code', '') in {'dsp_stretch_unavailable', 'dsp_budget_exceeded'} else 'replacement_output_invalid'
        elif isinstance(error, (VideoProjectError, OSError)):
            code = 'replacement_storage_failed'
        else:
            code = 'replacement_failed'
        await _finish(identifier, 'failed', _code.validate_python(code))
    finally:
        try:
            await _drain(identifier)
            final = _attempt(identifier)
            if final.cleanup_pending:
                await _finish(identifier, 'interrupted', 'replacement_interrupted')
        except Exception:
            logger.exception('Replacement worker remains owned: %s', identifier)
            await _finish(identifier, 'failed', 'replacement_cleanup_pending')
        if identifier not in _processes and identifier not in _unverified:
            _tasks.pop(identifier, None)


async def cancel(plan_id: str) -> ReplacementPlanResponse:
    plan = get(plan_id)
    identifier = plan.active_attempt_id
    if identifier is None:
        return plan
    task = _tasks.get(identifier)
    if task is not asyncio.current_task():
        await cancel_and_wait(task)
    try:
        await _drain(identifier)
    except Exception:
        logger.exception('Replacement cancellation retains uncertain writer: %s', identifier)
        await _finish(identifier, 'failed', 'replacement_cleanup_pending')
        return get(plan_id)
    await _finish(identifier, 'cancelled')
    _tasks.pop(identifier, None)
    return get(plan_id)


async def retry(plan_id: str, request: ReplacementActionRequest) -> ReplacementPlanResponse:
    plan = get(plan_id)
    if plan.active_attempt_id is not None:
        return plan
    with _connection() as connection:
        row = connection.execute('SELECT id FROM replacement_attempts WHERE plan_id=? ORDER BY rowid DESC LIMIT 1', (plan_id,)).fetchone()
        previous = store.get_attempt(connection, str(row['id'])) if row is not None else None
    if previous is None:
        return await prepare(plan_id, request)
    if previous.status == 'done':
        return plan
    if previous.action == 'prepare':
        return await prepare(plan_id, request)
    if not isinstance(previous.request, ReplacementRenderRequest):
        raise ReplacementError('replacement_invalid_request', 422)
    render_request = ReplacementRenderRequest.model_validate({**previous.request.model_dump(),
        'expected_revision': request.expected_revision, 'request_id': request.request_id})
    return await render(plan_id, render_request, action=previous.action)


def work_busy() -> bool:
    return bool(_tasks or _processes or _unverified or _intake_lock.locked())


def require_voice_idle(voice_id: str) -> None:
    """Training/model replacement must not alter an admitted target."""
    with _connection() as connection:
        for attempt in store.active_attempts(connection):
            request = store.get_plan(connection, attempt.plan_id).request
            if _target_voice(request) == voice_id:
                raise HTTPException(409, 'replacement_model_in_use')


async def cancel_for_voice(voice_id: str) -> None:
    with _connection() as connection:
        identifiers = {attempt.plan_id for attempt in store.active_attempts(connection)
            if _target_voice(store.get_plan(connection, attempt.plan_id).request) == voice_id}
    for identifier in identifiers:
        if (await cancel(identifier)).active_attempt_id is not None:
            raise ReplacementError('replacement_cleanup_pending')


def _target_voice(request: ReplacementRequest) -> str | None:
    if request.mode == 'prepared':
        return None
    if request.target.engine == 'seed-vc':
        return request.target.voice_id
    from .rvc_models import get as rvc_model
    return rvc_model(request.target.registered_model_id).voice_id


async def delete(identifier: str) -> None:
    async with _lock:
        with _connection() as connection:
            store.delete_plan(connection, identifier, _now())


async def recover() -> None:
    global _closing
    _closing = True
    with _connection() as connection:
        attempts = store.active_attempts(connection)
    for attempt in attempts:
        try:
            if attempt.worker is not None:
                receipt = Path(attempt.worker.receipt)
                if (receipt.is_symlink() or receipt.resolve() != receipt
                        or not receipt.is_relative_to(_workspace(attempt.plan_id) / 'attempts' / attempt.id)
                        or not await terminate_verified(attempt.worker)):
                    _unverified[attempt.id] = attempt.worker
                    await _finish(attempt.id, 'failed', 'replacement_cleanup_pending')
                    continue
            _unverified.pop(attempt.id, None)
            with _connection() as connection:
                row = connection.execute('SELECT id FROM replacement_renders WHERE id=? AND status=?', (attempt.id, 'done')).fetchone()
            await _finish(attempt.id, 'done' if row is not None else 'interrupted',
                None if row is not None else 'replacement_interrupted')
        except Exception:
            logger.exception('Replacement recovery failed: %s', attempt.id)
            if attempt.worker is not None:
                _unverified[attempt.id] = attempt.worker
    _closing = False


async def shutdown() -> None:
    global _closing
    _closing = True
    async with _intake_lock:
        pass  # Request-owned intake drains its bounded copy/probe before shutdown returns.
    with _connection() as connection:
        plans = {attempt.plan_id for attempt in store.active_attempts(connection)}
    for plan_id in plans:
        try:
            await await_cleanup(cancel(plan_id))
        except Exception:
            logger.exception('Replacement shutdown retains uncertain worker: %s', plan_id)


@asynccontextmanager
async def protect_track_removal(track_id: int) -> AsyncIterator[None]:
    async with _lock:
        if track_id in _deleting:
            raise ReplacementError('replacement_source_in_use')
        _deleting.add(track_id)
        with _connection() as connection:
            identifiers = {attempt.plan_id for attempt in store.active_attempts(connection)
                if track_id in _track_refs(store.get_plan(connection, attempt.plan_id).request,
                    (attempt.impulse_response,) if attempt.impulse_response else ())}
    try:
        for identifier in identifiers:
            if (await cancel(identifier)).active_attempt_id is not None:
                raise ReplacementError('replacement_cleanup_pending')
        yield
    finally:
        async with _lock:
            _deleting.discard(track_id)


@asynccontextmanager
async def protect_stem_replacement(track_id: int) -> AsyncIterator[None]:
    async with _lock:
        if track_id in _deleting or track_id in _replacing_stems:
            raise ReplacementError('replacement_source_in_use')
        _replacing_stems.add(track_id)
        with _connection() as connection:
            identifiers = {attempt.plan_id for attempt in store.active_attempts(connection)
                if track_id in _stem_refs(store.get_plan(connection, attempt.plan_id).request,
                    (attempt.impulse_response,) if attempt.impulse_response else ())}
    try:
        for identifier in identifiers:
            if (await cancel(identifier)).active_attempt_id is not None:
                raise ReplacementError('replacement_cleanup_pending')
        yield
    finally:
        async with _lock:
            _replacing_stems.discard(track_id)


def _target_files(target: SeedTarget) -> tuple[ModelArtifact, PinnedConversion]:
    from . import voice_authorization, voice_build
    from .voice_artifacts import ModelArtifact
    from .voice_mix import PinnedConversion
    voice_authorization.authorize(target.voice_id, 'convert')
    voice_build.require_ready_voice(target.voice_id)
    if not voice_build._engine_python().is_file():
        raise ReplacementError('replacement_engine_unavailable')
    artifact: ModelArtifact = voice_build.resolve_model_artifact(voice_build.voice_dir(target.voice_id), target.model_revision_id)
    pinned: PinnedConversion = pin_conversion(artifact, target.voice_id, target.model_revision_id,
        voice_build.SEED_VC_DIR / 'inference.py', voice_build.DIFFUSION_STEPS)
    return artifact, pinned


async def _model_snapshot(identifier: str, target: SeedTarget) -> VoiceModelSnapshot:
    _, pinned = _target_files(target)
    snapshot = await observe_conversion(pinned)
    owner_root = _workspace(_attempt(identifier).plan_id, create=True)
    root = _directory(owner_root, owner_root / 'models')
    path = root / (_digest(target.model_dump()) + '.json')
    if path.exists():
        if path.is_symlink() or path.stat().st_size > 1024**2:
            raise ReplacementError('replacement_model_changed')
        previous = VoiceModelSnapshot.model_validate_json(path.read_bytes())
        if snapshot != previous:
            raise ReplacementError('replacement_model_changed')
    else:
        await run_owned_thread(write_object, path, _json.validate_json(snapshot.model_dump_json()))
    copies = _directory(owner_root, root / _digest(target.model_dump()))
    for item, observed in zip(pinned.files, snapshot.files):
        suffix = '.pth' if item.role == 'checkpoint' else '.yml' if item.role == 'configuration' else '.wav' if item.role == 'voice_reference' else '.py'
        destination = copies / (item.role + suffix)
        if destination.exists():
            if destination.is_symlink() or await hash_file(destination, max_bytes=8 * 1024**3) != observed.sha256:
                raise ReplacementError('replacement_model_changed')
        else:
            if shutil.disk_usage(copies).free < item.signature[2] + RESERVE_BYTES:
                raise ReplacementError('replacement_disk_full')
            await copy_verified(item.path, destination, observed.sha256, max_bytes=item.signature[2])
    return snapshot


async def _rvc_snapshot(identifier: str, model_id: str) -> RvcRegisteredModel:
    from .rvc_models import resolve
    from .voice_authorization import recheck
    resolved = await run_owned_thread(resolve, model_id)
    model = resolved.public
    recheck(model.voice_id, _attempt(identifier).authorization, 'convert')
    owner_root = _workspace(_attempt(identifier).plan_id, create=True)
    root = _directory(owner_root, owner_root / 'models' / ('rvc-' + model_id))
    for path, name, checksum in ((resolved.checkpoint, 'model.pth', model.checkpoint_sha256),
            (resolved.index, 'added.index', model.index_sha256)):
        if path is None or checksum is None:
            continue
        destination = root / name
        if destination.exists():
            if destination.is_symlink() or await hash_file(destination, max_bytes=MAX_SOURCE_BYTES) != checksum:
                raise ReplacementError('replacement_model_changed')
        else:
            if shutil.disk_usage(root).free < path.stat().st_size + RESERVE_BYTES:
                raise ReplacementError('replacement_disk_full')
            await copy_verified(path, destination, checksum, max_bytes=path.stat().st_size)
    receipt = root / 'model.json'
    if receipt.exists():
        if receipt.is_symlink() or receipt.stat().st_size > 65536:
            raise ReplacementError('replacement_model_changed')
        previous = RvcRegisteredModel.model_validate_json(receipt.read_bytes())
        if previous != model:
            raise ReplacementError('replacement_model_changed')
    else:
        await run_owned_thread(write_object, receipt, _json.validate_json(model.model_dump_json()))
    return model


async def _convert_rvc(identifier: str, model_id: str, source: store.StoredReplacementArtifact,
                       root: Path) -> store.StoredReplacementArtifact:
    from . import optional_engines, voice_authorization
    from .gpu_lease import gpu_lease
    from .stems import gpu_lock
    from .voice_conversion import phase_safe_mono_file
    from .rvc_models import resolve
    model = await _rvc_snapshot(identifier, model_id)
    key = _digest({'source': source.public.sha256, 'model': model.model_dump(), 'recipe': RECIPE})
    plan = get(source.plan_id)
    cached = await _cached_artifact(plan, 'converted_dry', key)
    if cached is not None:
        return cached
    audio = await _resolved(source)
    if audio.duration_frames / audio.sample_rate_hz > 600:
        raise ReplacementError('replacement_engine_unavailable')
    mono = root / 'rvc-source.wav'
    await run_owned_thread(phase_safe_mono_file, audio.path, mono)
    copies = _workspace(source.plan_id) / 'models' / ('rvc-' + model_id)
    output = root / 'rvc-output.wav'
    argv, engine_root = optional_engines.prepare_rvc_command(copies / 'model.pth', mono, output,
        index=copies / 'added.index' if model.index_sha256 else None,
        speaker_id=model.speaker_id, index_rate=model.index_rate)
    _phase(identifier, 'converting')
    async with gpu_lease(gpu_lock, 'voice_conversion', plan.request.title):
        voice_authorization.recheck(model.voice_id, _attempt(identifier).authorization, 'convert')
        if (await run_owned_thread(resolve, model_id)).public != model:
            raise ReplacementError('replacement_model_changed')
        with (root / 'conversion.log').open('w', encoding='utf-8') as log:
            proc = await _spawn(identifier, argv, engine_root, optional_engines._child_env(), log.fileno())
            try:
                async with asyncio.timeout(3600):
                    code = await proc.wait()
            finally:
                await _drain(identifier)
    if code or (await run_owned_thread(resolve, model_id)).public != model:
        raise ReplacementError('replacement_failed' if code else 'replacement_model_changed')
    info = await run_owned_thread(sf.info, output)
    if abs(info.frames / info.samplerate - audio.duration_frames / audio.sample_rate_hz) > max(.25, .01 * audio.duration_frames / audio.sample_rate_hz):
        raise ReplacementError('replacement_output_duration_mismatch')
    voice_authorization.recheck(model.voice_id, _attempt(identifier).authorization, 'convert')
    return await _retained(identifier, 'converted_dry', output, [source.public.evidence_asset_id],
        source.public.source_sha256s, key, 'Trusted-local experimental RVC dry conversion; pinned model/runtime ' +
        (model.observed_runtime.configuration_sha256 if model.observed_runtime else 'unavailable') +
        '; upstream level handling/output quantization retained, no quality or rights clearance.', processing=source.public.processing)


async def _convert(identifier: str, target: SeedTarget, source: store.StoredReplacementArtifact,
                   root: Path) -> store.StoredReplacementArtifact:
    from . import voice_authorization, voice_build
    from .gpu_lease import gpu_lease
    from .stems import gpu_lock
    from .voice_conversion import phase_safe_mono_file, seed_command
    snapshot = await _model_snapshot(identifier, target)
    key = _digest({'source': source.public.sha256, 'conversion': snapshot.model_dump(), 'recipe': RECIPE})
    plan = get(source.plan_id)
    cached = await _cached_artifact(plan, 'converted_dry', key)
    if cached is not None:
        return cached
    audio = await _resolved(source)
    if audio.duration_frames / audio.sample_rate_hz > 600:
        raise ReplacementError('replacement_engine_unavailable')
    artifact, pinned = _target_files(target)
    authorization = _attempt(identifier).authorization
    voice_authorization.recheck(target.voice_id, authorization, 'convert')
    copies = _workspace(source.plan_id) / 'models' / _digest(target.model_dump())
    mono_source, mono_reference = root / 'seed-source.wav', root / 'seed-reference.wav'
    await run_owned_thread(phase_safe_mono_file, audio.path, mono_source)
    await run_owned_thread(phase_safe_mono_file, copies / 'voice_reference.wav', mono_reference)
    info = await run_owned_thread(sf.info, mono_reference)
    if info.frames > info.samplerate * 30:
        raise ReplacementError('replacement_engine_incompatible')
    output = root / 'seed-output'
    output.mkdir()
    command = seed_command(voice_build._engine_python(), mono_source, mono_reference, output,
        diffusion_steps=voice_build.DIFFUSION_STEPS, checkpoint=copies / 'checkpoint.pth', configuration=copies / 'configuration.yml')
    _phase(identifier, 'converting')
    async with gpu_lease(gpu_lock, 'voice_conversion', plan.request.title):
        voice_authorization.recheck(target.voice_id, authorization, 'convert')
        if await observe_conversion(pinned) != snapshot:
            raise ReplacementError('replacement_model_changed')
        with (root / 'conversion.log').open('w', encoding='utf-8') as log:
            env = {**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                'HF_HUB_DISABLE_TELEMETRY': '1', 'HF_HUB_CACHE': str(voice_build.SEED_VC_DIR / 'checkpoints')}
            proc = await _spawn(identifier, command, voice_build.SEED_VC_DIR, env, log.fileno())
            try:
                async with asyncio.timeout(3600):
                    code = await proc.wait()
            finally:
                await _drain(identifier)
    if code or await observe_conversion(pinned) != snapshot:
        raise ReplacementError('replacement_model_changed' if code == 0 else 'replacement_failed')
    outputs = list(output.glob('vc_*.wav'))
    if len(outputs) != 1:
        raise ReplacementError('replacement_output_invalid')
    converted = await run_owned_thread(sf.info, outputs[0])
    if abs(converted.frames / converted.samplerate - audio.duration_frames / audio.sample_rate_hz) > max(.25, .01 * audio.duration_frames / audio.sample_rate_hz):
        raise ReplacementError('replacement_output_duration_mismatch')
    voice_authorization.recheck(target.voice_id, authorization, 'convert')
    return await _retained(identifier, 'converted_dry', outputs[0], [source.public.evidence_asset_id],
        source.public.source_sha256s, key, 'Seed-VC dry conversion; exact observed model files/configuration ' + _digest(snapshot.model_dump()),
        processing=source.public.processing)


async def _convert_performance(identifier: str, source: store.StoredReplacementArtifact, root: Path,
                                run: RunCommand, native: NativeStretch | None) -> store.StoredReplacementArtifact:
    source = await _processed_performance(identifier, source, root)
    plan = get(source.plan_id)
    pitch = plan.request.fit.pitch_semitones
    if pitch == 0:
        return source
    key = _digest({'source': source.public.sha256, 'pitch': pitch, 'recipe': 'convert-pitch-v1'})
    cached = await _cached_artifact(plan, 'aligned_dry', key)
    if cached is not None:
        return cached
    if _attempt(identifier).action == 'remix':
        raise ReplacementError('replacement_artifact_missing', 404)
    audio = await _resolved(source)
    duration = audio.duration_frames / audio.sample_rate_hz
    result = await render_transform(DspTransformRequest(source=_music_source(audio), source_sha256=audio.sha256,
        revision=plan.revision, anchors=[WarpAnchor(source_sec=0.0, target_sec=0.0),
            WarpAnchor(source_sec=duration, target_sec=duration)], output_duration_sec=duration,
        pitch_shift_semitones=pitch, sample_rate_hz=48000), audio, root, run, native=native)
    return await _retained(identifier, 'aligned_dry', root / result.artifact_name,
        [source.public.evidence_asset_id], source.public.source_sha256s, key,
        'Explicit native pitch before timbre conversion, timeline preserved; no automatic pitch correction.',
        modules=('music_dsp',), processing=ReplacementProcessing.model_validate({
            **(source.public.processing.model_dump() if source.public.processing else {}),
            'native_recipes': [engine.recipe for engine in result.engines if engine.name == 'rubberband'],
            'native_executable_sha256s': [engine.executable_sha256 for engine in result.engines
                if engine.name == 'rubberband' and engine.executable_sha256 is not None]}))


async def _conversion_dry(identifier: str, source: store.StoredReplacementArtifact,
                           root: Path) -> store.StoredReplacementArtifact:
    plan = get(source.plan_id)
    if plan.request.mode == 'prepared':
        return source
    target = plan.request.target
    if _attempt(identifier).action != 'remix':
        return (await _convert(identifier, target, source, root) if target.engine == 'seed-vc'
            else await _convert_rvc(identifier, target.registered_model_id, source, root))
    owner = _workspace(plan.id)
    receipt = (owner / 'models' / (_digest(target.model_dump()) + '.json') if target.engine == 'seed-vc'
        else owner / 'models' / ('rvc-' + target.registered_model_id) / 'model.json')
    if receipt.is_symlink() or not receipt.is_file() or receipt.stat().st_size > 1024**2:
        raise ReplacementError('replacement_artifact_missing', 404)
    if target.engine == 'seed-vc':
        seed = VoiceModelSnapshot.model_validate_json(receipt.read_bytes())
        if seed.voice_id != target.voice_id or seed.model_revision_id != target.model_revision_id:
            raise ReplacementError('replacement_artifact_changed')
        key = _digest({'source': source.public.sha256, 'conversion': seed.model_dump(), 'recipe': RECIPE})
    else:
        rvc = RvcRegisteredModel.model_validate_json(receipt.read_bytes())
        if rvc.id != target.registered_model_id:
            raise ReplacementError('replacement_artifact_changed')
        key = _digest({'source': source.public.sha256, 'model': rvc.model_dump(), 'recipe': RECIPE})
    retained = await _cached_artifact(plan, 'converted_dry', key)
    if retained is None:
        raise ReplacementError('replacement_artifact_missing', 404)
    return retained


async def _render(identifier: str) -> None:
    from .music_dsp_runtime import native
    attempt = _attempt(identifier)
    plan = get(attempt.plan_id)
    request = attempt.request
    if not isinstance(request, ReplacementRenderRequest):
        raise ReplacementError('replacement_invalid_request', 422)
    owner_root = _workspace(plan.id, create=True)
    root = _directory(owner_root, owner_root / 'attempts' / identifier)
    key = _prepare_key(plan.request)
    reference = await _cached_artifact(plan, 'source_vocal', key)
    backing = await _cached_artifact(plan, 'backing', key)
    if reference is None or backing is None:
        raise ReplacementError('replacement_artifact_missing', 404)
    original = await _snapshot(identifier, plan.request.source)
    async def command(argv: list[str], timeout: float) -> bytes:
        return await _command(identifier, argv, timeout)
    if plan.request.mode == 'convert':
        vocal = await _convert_performance(identifier, reference, root, command, native())
    else:
        alignment = plan.alignment
        if alignment is None or alignment.request.prepared.kind != 'production_asset':
            raise ReplacementError('replacement_alignment_invalid')
        dry = next((item for item in plan.artifacts if item.evidence_asset_id == alignment.request.prepared.asset_id), None)
        if dry is None:
            raise ReplacementError('replacement_artifact_missing', 404)
        with _connection() as connection:
            prepared = store.get_artifact(connection, plan.id, dry.id)
        aligned_key = _digest({'dry': dry.sha256, 'alignment': alignment.model_dump(),
            'pitch': plan.request.fit.pitch_semitones, 'recipe': RECIPE})
        aligned = await _cached_artifact(plan, 'aligned_dry', aligned_key)
        if aligned is None:
            if attempt.action == 'remix':
                raise ReplacementError('replacement_artifact_missing', 404)
            _phase(identifier, 'aligning')
            fitted = await fit_prepared(await _resolved(prepared), alignment.anchors,
                original.duration_frames / original.sample_rate_hz, plan.revision,
                plan.request.fit.pitch_semitones, root, command, native())
            await run_owned_thread(write_object, root / 'fit.json', _json.validate_python({
                'recipe': fitted.recipe, 'result': fitted.result.model_dump(mode='json'),
                'phrases': [item.model_dump(mode='json') for item in fitted.phrases]}))
            aligned = await _retained(identifier, 'aligned_dry', root / fitted.result.artifact_name,
                [prepared.public.evidence_asset_id], prepared.public.source_sha256s, aligned_key,
                'Explicit reviewed fit recipe ' + fitted.recipe + '; phase transforms and native identities retained in fit.json',
                alignment_revision=alignment.request.revision,
                modules=('music_dsp',) if any(engine.name == 'rubberband' for phrase in fitted.phrases for engine in phrase.engines) else (),
                processing=ReplacementProcessing.model_validate({**(prepared.public.processing.model_dump() if prepared.public.processing else {}),
                    'stitch_recipe': fitted.recipe, 'stitched_phrases': len(fitted.phrases),
                    'native_recipes': sorted({engine.recipe for phrase in fitted.phrases for engine in phrase.engines if engine.name == 'rubberband'}),
                    'native_executable_sha256s': sorted({engine.executable_sha256 for phrase in fitted.phrases for engine in phrase.engines
                        if engine.name == 'rubberband' and engine.executable_sha256 is not None})}))
        vocal = aligned
    fit_input = vocal
    if plan.request.mode != 'prepared':
        vocal = await _conversion_dry(identifier, vocal, root)
    settings_key = _digest({'vocal': vocal.public.sha256, 'backing': backing.public.sha256,
        'mix': request.mix.model_dump(), 'revision': plan.revision, 'recipe': RECIPE})
    impulse_artifact: store.StoredReplacementArtifact | None = None
    if attempt.impulse_response is not None:
        impulse_key = _digest({'ir': attempt.impulse_response.model_dump(), 'recipe': RECIPE})
        impulse_artifact = await _cached_artifact(plan, 'impulse_response', impulse_key)
        if impulse_artifact is None:
            impulse_audio = await _snapshot(identifier, attempt.impulse_response)
            impulse_artifact = await _retained(identifier, 'impulse_response', impulse_audio.path,
                [impulse_audio.source.asset_id] if impulse_audio.source.kind == 'production_asset' else [],
                [attempt.impulse_response.audio_sha256], impulse_key, 'Retained user-owned/reviewed impulse response; bounded ten seconds.')
    master = await _cached_artifact(plan, 'master', settings_key)
    matched = await _cached_artifact(plan, 'matched_vocal', settings_key)
    if master is None:
        _phase(identifier, 'mixing')
        mixed = await mix_replacement(request.mix, plan.revision, original, await _resolved(reference),
            await _resolved(vocal), await _resolved(backing), root, command,
            impulse=await _resolved(impulse_artifact) if impulse_artifact is not None else None)
        processing = ReplacementProcessing.model_validate({**(vocal.public.processing.model_dump() if vocal.public.processing else {}),
            'applied_gain_db': mixed.gain_db, 'residual_lu': mixed.residual_lu,
            'ir_sha256': mixed.convolution.ir_sha256 if mixed.convolution else None,
            'ir_gain': mixed.convolution.ir_gain if mixed.convolution else None,
            'wet': mixed.convolution.wet if mixed.convolution else None})
        extra_parents = [impulse_artifact.public.evidence_asset_id] if impulse_artifact is not None else []
        extra_hashes = impulse_artifact.public.source_sha256s if impulse_artifact is not None else []
        matched = await _retained(identifier, 'matched_vocal', mixed.processed.path,
            [vocal.public.evidence_asset_id, *extra_parents], [*vocal.public.source_sha256s, *extra_hashes], settings_key,
            'Measured vocal gain/EQ/compression and optional reviewed IR; configuration ' + mixed.vocal.configuration_sha256,
            processing=processing)
        master = await _retained(identifier, 'master', root / mixed.master.artifact_name,
            [matched.public.evidence_asset_id, backing.public.evidence_asset_id],
            [*vocal.public.source_sha256s, *backing.public.source_sha256s, *extra_hashes], settings_key,
            'Replacement measured mix; general derivative output, not strict production clearance. Configuration ' + mixed.master.configuration_sha256,
            processing=processing)
        await run_owned_thread(write_object, root / 'mix.json', _json.validate_python({
            'vocal': mixed.vocal.model_dump(mode='json'), 'master': mixed.master.model_dump(mode='json'),
            'gain_db': mixed.gain_db, 'residual_lu': mixed.residual_lu, 'warnings': list(mixed.warnings),
            'convolution': mixed.convolution.model_dump(mode='json') if mixed.convolution else None}))
        current = get(plan.id)
        with _connection() as connection:
            store.save_state(connection, ReplacementPlanResponse.model_validate({**current.model_dump(),
                'warnings': sorted(set([*current.warnings, *mixed.warnings])), 'updated_at': _now()}), expected_attempt_id=identifier)
    await _measure_diagnostics(identifier, reference, backing, fit_input=fit_input, fitted=True,
        converted=vocal if plan.request.mode != 'prepared' else None, matched=matched)
    if attempt.action == 'preview':
        window = request.preview_window
        if window is None:
            raise ReplacementError('replacement_invalid_request', 422)
        audio = await _resolved(master)
        end = min(window.start_sec + window.duration_sec, audio.duration_frames / audio.sample_rate_hz)
        if window.start_sec >= end:
            raise ReplacementError('replacement_invalid_request', 422)
        preview_key = _digest({'master': master.public.sha256, 'window': window.model_dump()})
        result = await render_transform(DspTransformRequest(source=_music_source(audio), source_sha256=audio.sha256,
            revision=plan.revision, anchors=[WarpAnchor(source_sec=window.start_sec, target_sec=0.0),
                WarpAnchor(source_sec=end, target_sec=end - window.start_sec)],
            output_duration_sec=end - window.start_sec), audio, root, command)
        await _retained(identifier, 'preview', root / result.artifact_name,
            [master.public.evidence_asset_id], master.public.source_sha256s, preview_key, 'Server-rendered excerpt from exact measured replacement master.')
        return
    _phase(identifier, 'publishing')
    for selection in _selections(plan.request):
        await _verify(selection)
    if attempt.impulse_response is not None:
        if await run_owned_thread(_ir_selection, request.mix) != attempt.impulse_response:
            raise ReplacementError('replacement_evidence_required')
        await _verify(attempt.impulse_response)
    if plan.request.mode != 'prepared' and plan.request.target.engine == 'seed-vc':
        from .voice_authorization import recheck
        recheck(plan.request.target.voice_id, attempt.authorization, 'convert')
    elif plan.request.mode != 'prepared' and plan.request.target.engine == 'rvc':
        from .rvc_models import get as rvc_model
        from .voice_authorization import recheck
        recheck(rvc_model(plan.request.target.registered_model_id).voice_id, attempt.authorization, 'convert')
    with _connection() as connection:
        rendered = store.create_render(connection, ReplacementRender(id=identifier, plan_id=plan.id,
            revision=plan.revision, request_id=request.request_id, status='running',
            master_artifact_id=master.public.id, settings_sha256=settings_key, created_at=attempt.created_at), request)
    if rendered.status == 'done':
        return
    path = await artifact_path(plan.id, master.public.id)
    from .music_evidence import serialize_music_evidence
    track_id = db.insert_track(model='upload', title=plan.request.title, lyrics='', seed=None,
        duration_ms=master.public.duration_sec * 1000, wall_ms=None, params=_json.validate_python({
            'source': 'voice_replacement_v2', 'replacement_plan_id': plan.id, 'replacement_revision': plan.revision,
            'replacement_render_id': rendered.id, 'origin': 'derivative',
            'parent_audio_sha256s': master.public.source_sha256s,
            '_music_evidence': serialize_music_evidence(master.music_evidence or MusicEvidence())}), audio_path=path, abc_path=None,
        replacement_render_id=rendered.id)
    from .audio_versions import retain_original
    try:
        retain_original(track_id, path)
    except Exception:
        # The track and render are already committed atomically. A recoverable
        # audio-version sidecar failure must not turn retry into a duplicate track.
        logger.exception('Published replacement track %s needs original-version recovery', track_id)
