"""Durable, single-worker CPU analysis of retained local audio snapshots.

No installer or accelerator lease is involved. Optional beats use a verified
isolated CPU model, selected explicitly. SQLite transactions never
span an await. Source readers remain owned until cancellation has drained them.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
from importlib.metadata import version
import json
import logging
import math
from pathlib import Path
import shutil
import sqlite3
import sys
from typing import Literal
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from . import db
from .audio_exports import AudioExportError
from .artist_production import ProductionError
from .audio_quality import INPUT, MAX_DECODED_BYTES
from .job_lifecycle import await_cleanup, cancel_and_wait, kill_process_tree, run_owned_thread
from .music_analysis_contracts import (AnalysisResult, MusicAnalysisContract, MusicAnalysisErrorCode,
    MusicAnalysisId, MusicAnalysisJob, MusicAnalysisOverrides, MusicAnalysisRequest, MusicAnalysisSha256,
    MusicAnalysisStoragePreview, MusicAnalysisTimestamp, MusicSourceRef)
from .music_beats import BeatAnalysisRecipe, BeatRuntimeIdentity, LICENSE_EVIDENCE_IDS
from .video_projects import VideoProjectError
from .video_io import _signature, copy_verified, hash_file
from .video_media import VideoMediaError, tool
from .video_process import (WorkerIdentity, WorkerOutputError, read_owned_output,
    run_owned_command, spawn_owned, terminate_verified)

logger = logging.getLogger(__name__)
ANALYZER_REVISION = 'numpy-summary-v1'
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_JOBS = 1000
MAX_ACTIVE = 32
DISK_RESERVE = 1024**3
MAX_SOURCE_BYTES = 8 * 1024**3
JOB_TIMEOUT = 3600.0
_id = TypeAdapter(MusicAnalysisId)
_lock = asyncio.Lock()
_semaphore = asyncio.Semaphore(1)
_tasks: dict[str, asyncio.Task[None]] = {}
_sources: dict[str, MusicSourceRef] = {}
_processes: dict[str, asyncio.subprocess.Process] = {}
_unverified: dict[str, WorkerIdentity] = {}
_deleting: set[int] = set()
_replacing_stems: set[int] = set()
_closing = False


class MusicAnalysisError(Exception):
    def __init__(self, code: MusicAnalysisErrorCode, status: int = 409) -> None:
        self.code, self.status = code, status
        super().__init__(code)


class AnalysisWorkerRequest(MusicAnalysisContract):
    schema_version: Literal[1] = 1
    job_id: MusicAnalysisId
    result_id: MusicAnalysisId
    source: MusicSourceRef
    snapshot_name: str = Field(pattern=r'^source\.(wav|mp3|flac|ogg|m4a|aac|aif|aiff)$')
    audio_sha256: MusicAnalysisSha256
    configuration_sha256: MusicAnalysisSha256
    ffmpeg_version: str = Field(min_length=1, max_length=200)
    ffmpeg_sha256: MusicAnalysisSha256
    created_at: MusicAnalysisTimestamp
    beats: BeatAnalysisRecipe | None = None


class AnalysisWorkerOutcome(MusicAnalysisContract):
    result: AnalysisResult | None = None
    error_code: MusicAnalysisErrorCode | None = None


class AnalysisWorkerReceipt(MusicAnalysisContract):
    schema_version: Literal[1] = 1
    result_sha256: MusicAnalysisSha256


class _Stream(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, strict=True)
    sample_rate: str = Field(max_length=12)
    channels: int = Field(ge=1, le=2)
    duration: str | None = Field(default=None, max_length=64)
    duration_ts: int | None = Field(default=None, ge=1)
    time_base: str | None = Field(default=None, max_length=64)


class _Format(BaseModel):
    duration: str | None = Field(default=None, max_length=64)


class _Probe(BaseModel):
    streams: list[_Stream] = Field(min_length=1, max_length=1)
    format: _Format = Field(default_factory=_Format)


@dataclass(frozen=True)
class AudioHeader:
    rate: int
    channels: int
    duration: float


def parse_audio_header(output: bytes) -> AudioHeader:
    try:
        document = _Probe.model_validate_json(output)
        stream = document.streams[0]
        rate = int(stream.sample_rate)
        duration = (float(stream.duration_ts * Fraction(stream.time_base))
            if stream.duration_ts is not None and stream.time_base is not None
            else float(stream.duration or document.format.duration or '0'))
        if not 8000 <= rate <= 192000 or not math.isfinite(duration) or not 0 < duration <= 7200:
            raise ValueError('invalid audio header')
        header = AudioHeader(rate, stream.channels, duration)
        storage_budget(header)
        return header
    except (ValidationError, ValueError, ZeroDivisionError, OverflowError) as error:
        raise MusicAnalysisError('invalid_audio', 422) from error


def decoded_byte_budget(header: AudioHeader) -> int:
    decoded = (math.ceil(header.duration * header.rate) + math.ceil(.1 * header.rate)) * header.channels * 4
    decoded += 1024 * header.channels * 4
    if decoded >= MAX_DECODED_BYTES:
        raise MusicAnalysisError('decode_budget_exceeded')
    return decoded


def storage_budget(header: AudioHeader) -> int:
    return decoded_byte_budget(header) + (120 * 22050 + math.ceil(.002 * 22050) + 1024) * header.channels * 4


def probe_command(path: Path) -> list[str]:
    return [tool('ffprobe'), '-v', 'error', *INPUT, '-select_streams', 'a:0',
        '-show_entries', 'stream=sample_rate,channels,duration,duration_ts,time_base:format=duration', '-of', 'json', str(path)]


async def preview(request: MusicAnalysisRequest) -> MusicAnalysisStoragePreview:
    selected = await run_owned_thread(_require_profile, request)
    path = _resolve_source(request.source)
    size = path.stat().st_size
    if not 0 < size <= MAX_SOURCE_BYTES:
        raise MusicAnalysisError('analysis_budget_exceeded')
    output = await run_owned_command(probe_command(path), timeout=60.0, max_bytes=65536)
    if output.returncode != 0:
        raise MusicAnalysisError('invalid_audio', 422)
    temporary = storage_budget(parse_audio_header(output.stdout))
    retained = size
    if selected is not None:
        from .music_beats_runtime import WEIGHTS_BYTES
        retained += WEIGHTS_BYTES
        temporary += 272 * 1024
    root = _root().parent
    try:
        free = shutil.disk_usage(root).free
    except OSError:
        free = None
    required = retained + temporary + DISK_RESERVE
    return MusicAnalysisStoragePreview(source_bytes=size, retained_bytes=retained, temporary_bytes=temporary,
        required_free_bytes=required, free_bytes=free, can_start=free is not None and free >= required)


@dataclass(frozen=True)
class _Record:
    job: MusicAnalysisJob
    revision: int
    request: MusicAnalysisRequest
    identity: WorkerIdentity | None
    snapshot_sha256: str | None
    snapshot_name: str | None


@dataclass(frozen=True)
class CompletedAnalysisWorkspace:
    job_id: str
    job_revision: int
    attempt: int
    source: MusicSourceRef
    result_id: str
    audio_sha256: str
    snapshot_name: str
    workspace: Path
    source_path: Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def configuration_digest(ffmpeg_version: str, ffmpeg_sha256: str | None = None, *, beats: BeatRuntimeIdentity | None = None) -> str:
    """Version every numerical recipe and the actual measurement executable."""
    backend = Path(__file__).resolve().parents[1]
    code = {name: hashlib.sha256((backend / name).read_bytes()).hexdigest() for name in (
        'app/music_analysis_features.py', 'app/audio_quality.py', 'scripts/music_analysis_worker.py')}
    configuration: dict[str, object] = {'schema': 1, 'analyzer': ANALYZER_REVISION, 'numpy': version('numpy'), 'code': code,
        'python': list(sys.version_info[:3]), 'ffmpeg': ffmpeg_version, 'measurement': 'ffmpeg-loudnorm-oversampled-v1',
        'ffmpeg_recipe_sha256': ffmpeg_sha256,
        'rate': 22050, 'window_sec': 120, 'overlap_sec': 10, 'fft': 4096, 'hop': 1024,
        'stitch': 'unknown-global-structure-v1', 'device': 'cpu', 'precision': 'float64'}
    if beats is not None:
        configuration['beats'] = beats.configuration_sha256
        configuration['beats_code'] = {name: hashlib.sha256((backend / name).read_bytes()).hexdigest() for name in (
            'app/music_beats.py', 'app/music_beats_runtime.py', 'scripts/music_beats_worker.py')}
    return _digest(configuration)


def cache_key(source_sha256: str, role: str, configuration_sha256: str) -> str:
    return _digest({'schema': 1, 'audio': source_sha256, 'role': role, 'configuration': configuration_sha256})


def migrate(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS music_analysis_results(
            id TEXT PRIMARY KEY CHECK(length(id)=32 AND id NOT GLOB '*[^0-9a-f]*'),
            cache_key TEXT NOT NULL CHECK(length(cache_key)=64 AND cache_key NOT GLOB '*[^0-9a-f]*'),
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            source_sha256 TEXT NOT NULL CHECK(length(source_sha256)=64 AND source_sha256 NOT GLOB '*[^0-9a-f]*'),
            configuration_sha256 TEXT NOT NULL CHECK(length(configuration_sha256)=64 AND configuration_sha256 NOT GLOB '*[^0-9a-f]*'),
            result_json TEXT NOT NULL CHECK(length(result_json)<=8388608), created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS music_analysis_cache ON music_analysis_results(cache_key);
        CREATE TABLE IF NOT EXISTS music_analysis_jobs(
            id TEXT PRIMARY KEY CHECK(length(id)=32 AND id NOT GLOB '*[^0-9a-f]*'),
            request_key TEXT NOT NULL CHECK(length(request_key)=64), cache_key TEXT,
            status TEXT NOT NULL CHECK(status IN ('queued','running','done','failed','cancelled','interrupted')),
            attempt INTEGER NOT NULL CHECK(attempt>=1), revision INTEGER NOT NULL CHECK(revision>=0),
            request_json TEXT NOT NULL CHECK(length(request_json)<=131072),
            job_json TEXT NOT NULL CHECK(length(job_json)<=8388608),
            worker_identity_json TEXT CHECK(length(worker_identity_json)<=65536),
            snapshot_sha256 TEXT CHECK(snapshot_sha256 IS NULL OR (length(snapshot_sha256)=64 AND snapshot_sha256 NOT GLOB '*[^0-9a-f]*')),
            snapshot_name TEXT, result_id TEXT REFERENCES music_analysis_results(id), updated_at TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS music_analysis_active ON music_analysis_jobs(request_key)
            WHERE status IN ('queued','running');
        CREATE INDEX IF NOT EXISTS music_analysis_status ON music_analysis_jobs(status,updated_at);
        CREATE TABLE IF NOT EXISTS music_analysis_overrides(
            analysis_id TEXT PRIMARY KEY REFERENCES music_analysis_results(id),
            revision INTEGER NOT NULL CHECK(revision>=1),
            overrides_json TEXT NOT NULL CHECK(length(overrides_json)<=2097152), updated_at TEXT NOT NULL);
    """)
    connection.commit()


@contextmanager
def _connection(*, write: bool = False) -> Iterator[sqlite3.Connection]:
    db.get_db()
    connection = sqlite3.connect(db.DB_PATH, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    try:
        if write:
            connection.execute('BEGIN IMMEDIATE')
        yield connection
        if write:
            connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _text(row: sqlite3.Row, name: str, maximum: int = MAX_DOCUMENT_BYTES) -> str:
    value: object = row[name]
    if not isinstance(value, str) or len(value.encode()) > maximum:
        raise MusicAnalysisError('analysis_storage_unavailable', 503)
    return value


def _parse(row: sqlite3.Row) -> _Record:
    job = MusicAnalysisJob.model_validate_json(_text(row, 'job_json'))
    request = MusicAnalysisRequest.model_validate_json(_text(row, 'request_json', 131072))
    revision: object = row['revision']
    snapshot_hash: object = row['snapshot_sha256']
    snapshot_name: object = row['snapshot_name']
    if (type(revision) is not int or revision < 0 or job.source != request.source
            or job.id != row['id'] or job.status != row['status'] or job.attempt != row['attempt']
            or (job.result.id if job.result is not None else None) != row['result_id']
            or not isinstance(snapshot_hash, (str, type(None))) or not isinstance(snapshot_name, (str, type(None)))):
        raise MusicAnalysisError('analysis_storage_unavailable', 503)
    identity = WorkerIdentity.model_validate_json(_text(row, 'worker_identity_json', 65536)) if row['worker_identity_json'] is not None else None
    return _Record(job, revision, request, identity, snapshot_hash, snapshot_name)


def _record(identifier: str, connection: sqlite3.Connection | None = None) -> _Record:
    try:
        _id.validate_python(identifier)
    except ValidationError as error:
        raise MusicAnalysisError('analysis_job_not_found', 404) from error
    if connection is None:
        with _connection() as opened:
            return _record(identifier, opened)
    row = connection.execute('SELECT * FROM music_analysis_jobs WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise MusicAnalysisError('analysis_job_not_found', 404)
    return _parse(row)


def _insert_job(job: MusicAnalysisJob, request: MusicAnalysisRequest, request_key: str,
                connection: sqlite3.Connection | None = None) -> None:
    if connection is None:
        with _connection(write=True) as opened:
            _insert_job(job, request, request_key, opened)
        return
    connection.execute('INSERT INTO music_analysis_jobs(id,request_key,status,attempt,revision,request_json,job_json,updated_at) VALUES(?,?,?,?,?,?,?,?)',
        (job.id, request_key, job.status, job.attempt, 0, request.model_dump_json(), job.model_dump_json(), _now()))


def _change(identifier: str, **changes: object) -> MusicAnalysisJob:
    with _connection(write=True) as connection:
        record = _record(identifier, connection)
        updated = MusicAnalysisJob.model_validate({**record.job.model_dump(), **changes})
        connection.execute('UPDATE music_analysis_jobs SET status=?,attempt=?,job_json=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?',
            (updated.status, updated.attempt, updated.model_dump_json(), _now(), identifier, record.revision))
        return updated


def _effective(raw: AnalysisResult, connection: sqlite3.Connection, source: MusicSourceRef | None = None) -> AnalysisResult:
    body = raw.model_dump()
    row = connection.execute('SELECT * FROM music_analysis_overrides WHERE analysis_id=?', (raw.id,)).fetchone()
    if row is not None:
        overrides = MusicAnalysisOverrides.model_validate_json(_text(row, 'overrides_json', 2 * 1024**2))
        if overrides.analysis_id != raw.id or overrides.expected_revision != row['revision']:
            raise MusicAnalysisError('analysis_storage_unavailable', 503)
        body.update(_override_fields(overrides))
        body['override_revision'] = overrides.expected_revision
        if overrides.key is not None and overrides.key.status == 'known':
            body['warnings'] = [warning for warning in raw.warnings if warning != 'key_unknown']
    if source is not None:
        body['source'] = source.model_dump()
    return AnalysisResult.model_validate(body)


def get(identifier: str) -> MusicAnalysisJob:
    with _connection() as connection:
        job = _record(identifier, connection).job
        if job.result is not None:
            row = connection.execute('SELECT * FROM music_analysis_results WHERE id=?', (job.result.id,)).fetchone()
            if row is None or _stored_result(row) != job.result:
                raise MusicAnalysisError('analysis_storage_unavailable', 503)
            return MusicAnalysisJob.model_validate({**job.model_dump(), 'result': _effective(job.result, connection, job.source).model_dump()})
        return job


def list_jobs(source: MusicSourceRef | None = None) -> list[MusicAnalysisJob]:
    where = ''
    parameters: tuple[str | int, ...] = ()
    if source is not None:
        if source.kind == 'version':
            where = "WHERE json_extract(request_json,'$.source.kind')='version' AND json_extract(request_json,'$.source.track_id')=? AND json_extract(request_json,'$.source.version_id')=?"
            parameters = (source.track_id, source.version_id)
        elif source.kind == 'stem':
            where = "WHERE json_extract(request_json,'$.source.kind')='stem' AND json_extract(request_json,'$.source.track_id')=? AND json_extract(request_json,'$.source.stem')=? AND json_extract(request_json,'$.source.sha256')=?"
            parameters = (source.track_id, source.stem, source.sha256)
        else:
            where = "WHERE json_extract(request_json,'$.source.kind')='production_asset' AND json_extract(request_json,'$.source.asset_id')=? AND json_extract(request_json,'$.source.sha256')=?"
            parameters = (source.asset_id, source.sha256)
    with _connection() as connection:
        identifiers = [_text(row, 'id', 32) for row in connection.execute(
            'SELECT id FROM music_analysis_jobs ' + where + ' ORDER BY updated_at DESC,id DESC LIMIT 100', parameters)]
    return [get(identifier) for identifier in identifiers]


def _role(source: MusicSourceRef) -> str:
    return 'stem:' + source.stem if source.kind == 'stem' else source.kind


def _stored_result(row: sqlite3.Row) -> AnalysisResult:
    raw = AnalysisResult.model_validate_json(_text(row, 'result_json'))
    if (raw.id != row['id'] or raw.audio_sha256 != row['source_sha256']
            or raw.configuration_sha256 != row['configuration_sha256'] or raw.schema_version != row['schema_version']
            or raw.override_revision != 0 or raw.analyzer_revision != ANALYZER_REVISION
            or cache_key(raw.audio_sha256, _role(raw.source), raw.configuration_sha256) != row['cache_key']):
        raise MusicAnalysisError('analysis_storage_unavailable', 503)
    return raw


def _cached(key: str, source: MusicSourceRef) -> AnalysisResult | None:
    with _connection() as connection:
        row = connection.execute('SELECT * FROM music_analysis_results WHERE cache_key=? ORDER BY created_at,id LIMIT 1', (key,)).fetchone()
        if row is None:
            return None
        raw = _stored_result(row)
        if _role(raw.source) != _role(source):
            raise MusicAnalysisError('analysis_storage_unavailable', 503)
        # Raw cache identity excludes user edits; those remain a separate layer.
        return AnalysisResult.model_validate({**raw.model_dump(), 'id': uuid.uuid4().hex, 'source': source.model_dump(),
            'created_at': _now()})


def _publish(identifier: str, revision: int, key: str, result: AnalysisResult) -> bool:
    encoded = result.model_dump_json()
    if len(encoded.encode()) > MAX_DOCUMENT_BYTES:
        raise MusicAnalysisError('worker_output_limit')
    with _connection(write=True) as connection:
        record = _record(identifier, connection)
        if record.revision != revision or record.job.status != 'running' or record.job.cancellation_requested:
            return False
        if (result.source != record.job.source or result.override_revision != 0
                or cache_key(result.audio_sha256, _role(result.source), result.configuration_sha256) != key):
            raise MusicAnalysisError('analysis_failed')
        connection.execute('INSERT INTO music_analysis_results VALUES(?,?,?,?,?,?,?)',
            (result.id, key, 1, result.audio_sha256, result.configuration_sha256, encoded, result.created_at))
        completed = MusicAnalysisJob.model_validate({**record.job.model_dump(), 'status': 'done', 'phase': 'complete',
            'finished_at': _now(), 'error_code': None, 'result': result.model_dump()})
        updated = connection.execute("UPDATE music_analysis_jobs SET status='done',job_json=?,result_id=?,cache_key=?,revision=revision+1,updated_at=? WHERE id=? AND revision=? AND status='running'",
            (completed.model_dump_json(), result.id, key, _now(), identifier, revision))
        return updated.rowcount == 1


def _override_fields(patch: MusicAnalysisOverrides) -> dict[str, object]:
    # Preserve nested nulls: a known key must explicitly retain unknown_reason=null.
    return {name: value for name, value in patch.model_dump().items()
        if value is not None and name not in {'analysis_id', 'expected_revision'}}


def put_overrides(identifier: str, patch: MusicAnalysisOverrides) -> AnalysisResult:
    if identifier != patch.analysis_id:
        raise MusicAnalysisError('analysis_job_not_found', 404)
    with _connection(write=True) as connection:
        row = connection.execute('SELECT * FROM music_analysis_results WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise MusicAnalysisError('analysis_job_not_found', 404)
        raw = _stored_result(row)
        current = _effective(raw, connection)
        if current.override_revision != patch.expected_revision:
            raise MusicAnalysisError('analysis_revision_conflict')
        previous = connection.execute('SELECT overrides_json FROM music_analysis_overrides WHERE analysis_id=?', (identifier,)).fetchone()
        fields = _override_fields(MusicAnalysisOverrides.model_validate_json(_text(previous, 'overrides_json', 2 * 1024**2))) if previous else {}
        fields.update(_override_fields(patch))
        fields['analysis_id'] = identifier
        fields['expected_revision'] = current.override_revision + 1
        for field, method in (('tempo', 'confidence_method'), ('meter', 'method'), ('key', 'method')):
            value = fields.get(field)
            if isinstance(value, dict):
                value[method] = 'manual'
                value['confidence'] = None
        try:
            merged = MusicAnalysisOverrides.model_validate(fields)
            body = {**raw.model_dump(), **_override_fields(merged),
                'override_revision': current.override_revision + 1}
            warnings = list(raw.warnings)
            if merged.key is not None and merged.key.status == 'known':
                warnings = [warning for warning in warnings if warning != 'key_unknown']
            body['warnings'] = warnings
            effective = AnalysisResult.model_validate(body)
        except ValidationError as error:
            raise MusicAnalysisError('invalid_audio', 422) from error
        # Include warning adjustment in reads without mutating the raw record.
        connection.execute('INSERT INTO music_analysis_overrides VALUES(?,?,?,?) ON CONFLICT(analysis_id) DO UPDATE SET revision=excluded.revision,overrides_json=excluded.overrides_json,updated_at=excluded.updated_at',
            (identifier, effective.override_revision, merged.model_dump_json(), _now()))
        return effective


def _root() -> Path:
    return db.DATA_DIR / 'music-analysis'


def _workspace(identifier: str) -> Path:
    _id.validate_python(identifier)
    configured_root = _root()
    if configured_root.is_symlink():
        raise MusicAnalysisError('analysis_storage_unavailable', 503)
    root = configured_root.resolve()
    path = root / identifier
    if path.is_symlink() or path.resolve() != path.absolute():
        raise MusicAnalysisError('analysis_storage_unavailable', 503)
    return path


def _resolve_source(source: MusicSourceRef) -> Path:
    try:
        if source.kind == 'version':
            from .audio_versions import resolve_source
            path = resolve_source(source.track_id, source.version_id)
        elif source.kind == 'stem':
            from .audio_exports import stem_source
            path = stem_source(source.track_id, source.stem)
        else:
            from .artist_production import asset_path, get_asset
            asset = get_asset(source.asset_id)
            if asset.sha256 != source.sha256:
                raise MusicAnalysisError('source_changed')
            path = asset_path(source.asset_id)
        root = db.FILES_DIR.resolve()
        if (db.FILES_DIR.is_symlink() or path.is_symlink() or path.resolve() != path.absolute()
                or not path.is_relative_to(root) or not path.is_file()):
            raise MusicAnalysisError('source_missing', 404)
        if path.suffix.lower() not in {'.wav', '.mp3', '.flac', '.ogg', '.m4a'}:
            raise MusicAnalysisError('unsupported_audio', 422)
        return path
    except (HTTPException, AudioExportError, ProductionError, OSError) as error:
        raise MusicAnalysisError('source_missing', 404) from error


def _disk(path: Path, required: int) -> None:
    if shutil.disk_usage(path).free < required + DISK_RESERVE:
        raise MusicAnalysisError('insufficient_disk')


def _save_identity(identifier: str, identity: WorkerIdentity | None) -> None:
    with _connection(write=True) as connection:
        connection.execute('UPDATE music_analysis_jobs SET worker_identity_json=? WHERE id=?',
            (identity.model_dump_json() if identity else None, identifier))


async def start(request: MusicAnalysisRequest) -> MusicAnalysisJob:
    from .resource_admission import admission_lock, require_setup_idle
    async with admission_lock, _lock:
        if _closing:
            raise MusicAnalysisError('analysis_busy')
        source = request.source
        if source.kind != 'production_asset' and (source.track_id in _deleting
                or source.kind == 'stem' and source.track_id in _replacing_stems):
            raise MusicAnalysisError('analysis_busy')
        request_key = _digest({'request': request.model_dump(), 'recipe': ANALYZER_REVISION})
        with _connection(write=True) as connection:
            row = connection.execute("SELECT * FROM music_analysis_jobs WHERE request_key=? AND status IN ('queued','running')", (request_key,)).fetchone()
            if row is not None:
                return _parse(row).job
            require_setup_idle()
            _require_profile(request)
            _resolve_source(source)
            count = connection.execute("SELECT COUNT(*) FROM music_analysis_jobs WHERE status IN ('queued','running')").fetchone()[0]
            total = connection.execute('SELECT COUNT(*) FROM music_analysis_jobs').fetchone()[0]
            if count >= MAX_ACTIVE or _unverified:
                raise MusicAnalysisError('analysis_busy')
            if total >= MAX_JOBS:
                raise MusicAnalysisError('analysis_budget_exceeded')
            job = MusicAnalysisJob(id=uuid.uuid4().hex, source=source, created_at=_now())
            _insert_job(job, request, request_key, connection)
        _sources[job.id] = source
        _tasks[job.id] = asyncio.create_task(_run(job.id), name=f'music-analysis-{job.id}')
        return job


async def _snapshot(identifier: str) -> tuple[Path, str]:
    record = _record(identifier)
    root = _workspace(identifier)
    await run_owned_thread(root.mkdir, parents=True, exist_ok=True)
    source = _resolve_source(record.job.source)
    if record.snapshot_sha256 and record.snapshot_name:
        target = root / record.snapshot_name
        if target.name != record.snapshot_name or target.is_symlink() or not target.is_file():
            raise MusicAnalysisError('source_missing', 404)
        if await hash_file(target, max_bytes=MAX_SOURCE_BYTES) != record.snapshot_sha256:
            raise MusicAnalysisError('source_changed')
        return target, record.snapshot_sha256
    before = _signature(source)
    estimate = await preview(record.request)
    if not estimate.can_start:
        raise MusicAnalysisError('insufficient_disk')
    if _signature(source) != before:
        raise MusicAnalysisError('source_changed')
    checksum = await hash_file(source, max_bytes=before[2])
    if _signature(source) != before:
        raise MusicAnalysisError('source_changed')
    if record.job.source.kind != 'version' and checksum != record.job.source.sha256:
        raise MusicAnalysisError('source_changed')
    target = root / ('source' + source.suffix.lower())
    # Never reuse an unexplained partial from a failed copy.
    await run_owned_thread(target.unlink, missing_ok=True)
    await copy_verified(source, target, checksum, max_bytes=before[2])
    with _connection(write=True) as connection:
        connection.execute('UPDATE music_analysis_jobs SET snapshot_sha256=?,snapshot_name=? WHERE id=?', (checksum, target.name, identifier))
    return target, checksum


def _read_private(path: Path, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise MusicAnalysisError('analysis_failed')
    with path.open('rb') as handle:
        content = handle.read(maximum + 1)
    if len(content) > maximum:
        raise MusicAnalysisError('worker_output_limit')
    return content


def _require_profile(request: MusicAnalysisRequest) -> BeatAnalysisRecipe | None:
    if request.profile == 'music-v1':
        return None
    from .music_beats_runtime import BeatRuntimeError, recipe
    try:
        root, identity = recipe()
        return BeatAnalysisRecipe(runtime_root=str(root), identity=identity)
    except BeatRuntimeError as error:
        raise MusicAnalysisError(error.code, 409) from error


def _check_beats_result(result: AnalysisResult, beats: BeatAnalysisRecipe | None) -> None:
    engines = [engine for engine in result.engines if engine.task == 'beats']
    if beats is None:
        if result.weights_sha256 is not None or engines:
            raise MusicAnalysisError('analysis_failed')
    elif (result.weights_sha256 != beats.identity.weights_sha256 or len(engines) != 1
            or engines[0].weights_sha256 != beats.identity.weights_sha256
            or engines[0].configuration_sha256 != beats.identity.configuration_sha256
            or not set(LICENSE_EVIDENCE_IDS).issubset(result.license_evidence_ids)):
        raise MusicAnalysisError('analysis_failed')


def _validate_outcome(request: AnalysisWorkerRequest, completion: AnalysisWorkerReceipt, content: bytes) -> AnalysisResult:
    if hashlib.sha256(content).hexdigest() != completion.result_sha256:
        raise MusicAnalysisError('analysis_failed')
    outcome = AnalysisWorkerOutcome.model_validate_json(content)
    if outcome.error_code is not None:
        raise MusicAnalysisError(outcome.error_code)
    result = outcome.result
    if (result is None or result.id != request.result_id or result.source != request.source
            or result.audio_sha256 != request.audio_sha256 or result.configuration_sha256 != request.configuration_sha256
            or result.analyzer_revision != ANALYZER_REVISION or result.override_revision != 0
            or result.created_at != request.created_at or result.analysis_sample_rate_hz != 22050
            or result.loudness.samples_analyzed != result.duration_frames * result.channels):
        raise MusicAnalysisError('analysis_failed')
    _check_beats_result(result, request.beats)
    return result


def _completed_result(identifier: str, checksum: str, configuration: str) -> AnalysisResult | None:
    """Consume only a durable receipt from a previously verified drained attempt."""
    root = _workspace(identifier)
    if not (root / 'completion.json').exists():
        return None
    try:
        request = AnalysisWorkerRequest.model_validate_json(_read_private(root / 'request.json', 131072))
        record = _record(identifier)
        if (request.job_id != identifier or request.source != record.job.source or request.audio_sha256 != checksum
                or request.snapshot_name != record.snapshot_name or request.configuration_sha256 != configuration
                or record.identity is not None or identifier in _unverified or identifier in _processes):
            return None
        receipt = AnalysisWorkerReceipt.model_validate_json(_read_private(root / 'completion.json', 65536))
        result = _validate_outcome(request, receipt, _read_private(root / 'result.json', MAX_DOCUMENT_BYTES))
        return AnalysisResult.model_validate({**result.model_dump(), 'id': uuid.uuid4().hex})
    except (OSError, ValueError, ValidationError, MusicAnalysisError):
        logger.warning('Ignoring invalid retained analysis completion: %s', identifier)
        return None


async def _drain_worker(identifier: str, proc: asyncio.subprocess.Process) -> None:
    try:
        await await_cleanup(kill_process_tree(proc))
    except BaseException:
        identity = _record(identifier).identity
        if identity is not None:
            _unverified[identifier] = identity
        raise
    else:
        _processes.pop(identifier, None)
        _save_identity(identifier, None)
        if proc.stdin is not None:
            proc.stdin.close()


async def _execute_worker(identifier: str, request: AnalysisWorkerRequest) -> AnalysisResult:
    root = _workspace(identifier)
    manifest, result_path = root / 'request.json', root / 'result.json'
    await run_owned_thread(manifest.write_text, request.model_dump_json(), encoding='utf-8')
    await run_owned_thread(result_path.unlink, missing_ok=True)
    await run_owned_thread((root / 'completion.json').unlink, missing_ok=True)
    attempt = _record(identifier).job.attempt
    receipt = root / f'worker-{attempt}.json'
    proc = await spawn_owned([sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts' / 'music_analysis_worker.py'),
        '--request', str(manifest), '--result', str(result_path)], receipt_path=receipt,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        on_identity=lambda identity: _save_identity(identifier, identity))
    _processes[identifier] = proc
    drain_started = False
    try:
        output = await read_owned_output(proc, max_bytes=65536, timeout=JOB_TIMEOUT)
        if proc.returncode != 0:
            raise MusicAnalysisError('analysis_failed')
        drain_started = True
        await _drain_worker(identifier, proc)
        completion = AnalysisWorkerReceipt.model_validate_json(output)
        durable = AnalysisWorkerReceipt.model_validate_json(await run_owned_thread(_read_private, root / 'completion.json', 65536))
        if durable != completion:
            raise MusicAnalysisError('analysis_failed')
        content = await run_owned_thread(_read_private, result_path, MAX_DOCUMENT_BYTES)
        return _validate_outcome(request, completion, content)
    finally:
        if not drain_started:
            await _drain_worker(identifier, proc)


async def _analyze(identifier: str) -> None:
    if _record(identifier).job.cancellation_requested:
        raise asyncio.CancelledError
    _change(identifier, status='running', phase='snapshot', started_at=_now(), finished_at=None, error_code=None)
    snapshot, checksum = await _snapshot(identifier)
    selected = await _analysis_beats(identifier)
    version_output = await run_owned_command([tool('ffmpeg'), '-version'], timeout=15, max_bytes=65536)
    if version_output.returncode != 0:
        raise MusicAnalysisError('analysis_unavailable')
    ffmpeg_version = version_output.stdout.decode(errors='replace').splitlines()[0][:200]
    ffmpeg_sha256 = hashlib.sha256(version_output.stdout).hexdigest()
    configuration = configuration_digest(ffmpeg_version, ffmpeg_sha256,
        beats=selected.identity if selected else None)
    source = _record(identifier).job.source
    key = cache_key(checksum, _role(source), configuration)
    result = _cached(key, source)
    if result is None:
        result = await run_owned_thread(_completed_result, identifier, checksum, configuration)
    if result is None:
        _change(identifier, phase='beats' if selected else 'decode')
        result = await _execute_worker(identifier, AnalysisWorkerRequest(job_id=identifier, result_id=uuid.uuid4().hex,
            source=source, snapshot_name=snapshot.name, audio_sha256=checksum,
            configuration_sha256=configuration, ffmpeg_version=ffmpeg_version,
            ffmpeg_sha256=ffmpeg_sha256, created_at=_now(), beats=selected))
    _check_beats_result(result, selected)
    _change(identifier, phase='publishing')
    revision = _record(identifier).revision
    # Re-resolve the original association and compare bytes before commit.
    if await hash_file(_resolve_source(source), max_bytes=snapshot.stat().st_size) != checksum:
        raise MusicAnalysisError('source_changed')
    _publish(identifier, revision, key, result)


async def _analysis_beats(identifier: str) -> BeatAnalysisRecipe | None:
    record = _record(identifier)
    if record.request.profile == 'music-v1':
        return None
    from .music_beats_runtime import BeatRuntimeError, WEIGHTS_BYTES, identity
    root = _workspace(identifier)
    checkpoint = root / 'beat-checkpoint.ckpt'
    if checkpoint.is_symlink() or checkpoint.resolve() != checkpoint:
        raise MusicAnalysisError('analysis_runtime_unverified')
    old: BeatAnalysisRecipe | None = None
    if (root / 'request.json').exists():
        previous = AnalysisWorkerRequest.model_validate_json(await run_owned_thread(_read_private, root / 'request.json', 131072))
        if previous.job_id != identifier or previous.source != record.job.source or previous.beats is None:
            raise MusicAnalysisError('analysis_runtime_unverified')
        old = previous.beats
    try:
        if old is not None and checkpoint.exists():
            if (await run_owned_thread(identity, Path(old.runtime_root), require_weights=False) != old.identity
                    or await hash_file(checkpoint, max_bytes=WEIGHTS_BYTES) != old.identity.weights_sha256):
                raise MusicAnalysisError('analysis_runtime_unverified')
            return old
        selected = await run_owned_thread(_require_profile, record.request)
        if selected is None:
            raise MusicAnalysisError('analysis_runtime_unverified')
        _disk(root, WEIGHTS_BYTES + DISK_RESERVE)
        if checkpoint.exists() or checkpoint.is_symlink():
            if checkpoint.is_symlink() or await hash_file(checkpoint, max_bytes=WEIGHTS_BYTES) != selected.identity.weights_sha256:
                raise MusicAnalysisError('analysis_runtime_unverified')
        else:
            await copy_verified(Path(selected.runtime_root) / 'weights/small0.ckpt', checkpoint,
                selected.identity.weights_sha256, max_bytes=WEIGHTS_BYTES)
        return selected
    except BeatRuntimeError as error:
        raise MusicAnalysisError(error.code) from error


async def _run(identifier: str) -> None:
    try:
        async with _semaphore:
            async with asyncio.timeout(JOB_TIMEOUT):
                await _analyze(identifier)
    except asyncio.CancelledError:
        _change(identifier, status='failed' if identifier in _unverified else 'cancelled',
            error_code='worker_unverified' if identifier in _unverified else 'analysis_cancelled',
            cancellation_requested=True, finished_at=_now(), result=None)
    except Exception as error:
        logger.exception('Music analysis attempt failed: %s', identifier)
        code: MusicAnalysisErrorCode = 'analysis_failed'
        if identifier in _unverified:
            code = 'worker_unverified'
        elif isinstance(error, MusicAnalysisError):
            code = error.code
        elif isinstance(error, (VideoProjectError, VideoMediaError)):
            code = 'source_changed' if error.code == 'source_changed' else 'invalid_audio'
        elif isinstance(error, WorkerOutputError):
            code = 'worker_output_limit'
        elif isinstance(error, TimeoutError):
            code = 'analysis_timeout'
        _change(identifier, status='failed', error_code=code, finished_at=_now(), result=None)
    finally:
        _tasks.pop(identifier, None)
        if identifier not in _unverified:
            _sources.pop(identifier, None)


async def cancel(identifier: str) -> MusicAnalysisJob:
    async with _lock:
        job = get(identifier)
        if job.status in {'queued', 'running'}:
            _change(identifier, cancellation_requested=True)
            task = _tasks.get(identifier)
            if task is not None and not task.done():
                task.cancel()
        else:
            task = _tasks.get(identifier)
    await cancel_and_wait(task)
    # A task canceled before its coroutine first runs never enters its finally.
    if task is not None and task.done() and _tasks.get(identifier) is task:
        _tasks.pop(identifier, None)
        if identifier not in _unverified:
            _sources.pop(identifier, None)
    if identifier in _unverified:
        identity = _unverified[identifier]
        if not await terminate_verified(identity):
            raise MusicAnalysisError('worker_unverified')
        _unverified.pop(identifier, None)
        _processes.pop(identifier, None)
        _sources.pop(identifier, None)
        _save_identity(identifier, None)
    if get(identifier).status in {'queued', 'running'}:
        _change(identifier, status='cancelled', cancellation_requested=True, error_code='analysis_cancelled', finished_at=_now())
    return get(identifier)


async def retry(identifier: str) -> MusicAnalysisJob:
    from .resource_admission import admission_lock, require_setup_idle
    async with admission_lock, _lock:
        if _closing:
            raise MusicAnalysisError('analysis_busy')
        record = _record(identifier)
        if record.job.status in {'queued', 'running', 'done'}:
            return get(identifier)
        if record.identity is not None or identifier in _unverified:
            raise MusicAnalysisError('worker_unverified')
        if _unverified or len(_tasks) >= MAX_ACTIVE:
            raise MusicAnalysisError('analysis_busy')
        request_key = _digest({'request': record.request.model_dump(), 'recipe': ANALYZER_REVISION})
        with _connection() as connection:
            sibling = connection.execute("SELECT id FROM music_analysis_jobs WHERE request_key=? AND status IN ('queued','running') AND id<>?", (request_key, identifier)).fetchone()
            if sibling is not None:
                raise MusicAnalysisError('analysis_busy')
        require_setup_idle()
        if record.request.profile == 'music-beats-v1' and not (_workspace(identifier) / 'beat-checkpoint.ckpt').is_file():
            _require_profile(record.request)
        source = record.job.source
        if source.kind != 'production_asset' and (source.track_id in _deleting
                or source.kind == 'stem' and source.track_id in _replacing_stems):
            raise MusicAnalysisError('analysis_busy')
        _resolve_source(source)
        job = _change(identifier, status='queued', phase='waiting', attempt=record.job.attempt + 1,
            cancellation_requested=False, error_code=None, started_at=None, finished_at=None)
        _sources[identifier] = source
        _tasks[identifier] = asyncio.create_task(_run(identifier), name=f'music-analysis-{identifier}')
        return job


def work_busy() -> bool:
    return bool(_tasks or _processes or _unverified)


def _completed_workspace(identifier: str) -> CompletedAnalysisWorkspace:
    record = _record(identifier)
    source = record.job.source
    if (_closing or record.job.status != 'done' or record.identity is not None
            or identifier in _tasks or identifier in _processes or identifier in _unverified or identifier in _sources
            or source.kind != 'production_asset' and (source.track_id in _deleting or source.track_id in _replacing_stems)):
        raise MusicAnalysisError('analysis_busy')
    result = get(identifier).result
    name = record.snapshot_name
    if (result is None or record.snapshot_sha256 != result.audio_sha256
            or name not in {'source.wav', 'source.mp3', 'source.flac', 'source.ogg', 'source.m4a',
                'source.aac', 'source.aif', 'source.aiff'}):
        raise MusicAnalysisError('analysis_storage_unavailable', 503)
    workspace = _workspace(identifier)
    snapshot = workspace / name
    # Cleanup may already have detached the directory; its journal owns the staged-path proof.
    if ((workspace.exists() and not workspace.is_dir()) or snapshot.is_symlink()
            or snapshot.exists() and not snapshot.is_file()):
        raise MusicAnalysisError('source_missing', 404)
    return CompletedAnalysisWorkspace(identifier, record.revision, record.job.attempt, source, result.id,
        result.audio_sha256, name, workspace, _resolve_source(source))


async def completed_workspace_cleanup_projection(identifier: str) -> CompletedAnalysisWorkspace:
    async with _lock:
        return _completed_workspace(identifier)


@asynccontextmanager
async def protect_completed_workspace_cleanup(identifier: str, expected_revision: int) -> AsyncIterator[CompletedAnalysisWorkspace]:
    async with _lock:
        projection = _completed_workspace(identifier)
        if type(expected_revision) is not int or projection.job_revision != expected_revision:
            raise MusicAnalysisError('analysis_revision_conflict')
        # Hash outside this guard; recheck signatures and atomically rename while source mutations wait.
        yield projection


def active_track_ids() -> set[int]:
    return {source.track_id for source in _sources.values() if source.kind != 'production_asset'}


async def cancel_for_track(track_id: int) -> None:
    identifiers = [identifier for identifier, source in _sources.items() if source.kind != 'production_asset' and source.track_id == track_id]
    for identifier in identifiers:
        await cancel(identifier)


@asynccontextmanager
async def protect_track_removal(track_id: int) -> AsyncIterator[None]:
    async with _lock:
        if track_id in _deleting:
            raise MusicAnalysisError('analysis_busy')
        _deleting.add(track_id)
    try:
        await await_cleanup(cancel_for_track(track_id))
        yield
    finally:
        _deleting.discard(track_id)


@asynccontextmanager
async def protect_stem_replacement(track_id: int) -> AsyncIterator[None]:
    async with _lock:
        if track_id in _deleting or track_id in _replacing_stems:
            raise MusicAnalysisError('analysis_busy')
        _replacing_stems.add(track_id)
    try:
        for identifier, source in list(_sources.items()):
            if source.kind == 'stem' and source.track_id == track_id:
                await await_cleanup(cancel(identifier))
        yield
    finally:
        _replacing_stems.discard(track_id)


async def recover() -> None:
    global _closing
    async with _lock:
        _closing = True
    with _connection() as connection:
        rows = connection.execute("SELECT * FROM music_analysis_jobs WHERE status IN ('queued','running') OR worker_identity_json IS NOT NULL").fetchall()
    for row in rows:
        record = _parse(row)
        identity = record.identity
        verified = identity is None
        if identity is not None:
            expected = _workspace(record.job.id) / f'worker-{record.job.attempt}.json'
            verified = Path(identity.receipt) == expected and not expected.is_symlink() and await terminate_verified(identity)
        if verified:
            _save_identity(record.job.id, None)
            _unverified.pop(record.job.id, None)
            _sources.pop(record.job.id, None)
            _change(record.job.id, status='interrupted', result=None, error_code='analysis_interrupted', finished_at=_now())
        else:
            if identity is not None:
                _unverified[record.job.id] = identity
            _sources[record.job.id] = record.job.source
            _change(record.job.id, status='failed', result=None, error_code='worker_unverified', finished_at=_now())
    async with _lock:
        _closing = False


async def shutdown() -> None:
    global _closing
    from .resource_admission import admission_lock
    async with admission_lock, _lock:
        _closing = True
        identifiers = list(_tasks)
    for identifier in identifiers:
        task = _tasks.get(identifier)
        if task is not None and not task.done():
            task.cancel()
    outcomes = await await_cleanup(asyncio.gather(*(cancel(identifier) for identifier in identifiers), return_exceptions=True))
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            logger.error('Music analysis shutdown retained an undrained owner', exc_info=outcome)
