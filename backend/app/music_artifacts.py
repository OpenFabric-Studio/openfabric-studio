"""Reviewed removal of redundant analysis copies; every other music asset is retained."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from typing import Literal
import uuid

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, model_validator

from . import db, music_analysis
from .atomic_files import write_object
from .contracts import JsonObject
from .job_lifecycle import await_cleanup, run_owned_thread
from .music_analysis_contracts import MusicAnalysisId, MusicSourceRef
from .music_beats import LICENSE_EVIDENCE_IDS
from .module_contracts import ModuleId
from .music_artifact_contracts import (ArtifactContract, ArtifactErrorCode, ArtifactReason,
    ConfirmMusicArtifactCleanup, MusicArtifactCleanupItem, MusicArtifactCleanupOutcome, MusicArtifactCleanupPlan,
    MusicArtifactCleanupReceipt, MusicArtifactCleanupRequest, MusicArtifactDependency, MusicArtifactGroup, MusicArtifactInventory)
from .video_io import hash_file
from .video_projects import VideoProjectError

logger = logging.getLogger(__name__)
MAX_DOCUMENT = 2 * 1024**2
MAX_ENTRIES = 50000
MAX_DEPTH = 12
SCAN_SECONDS = 2.0
_id = TypeAdapter(MusicAnalysisId)
_json = TypeAdapter(JsonObject)
_lock = asyncio.Lock()
_closing = False
_executions: dict[str, asyncio.Task[MusicArtifactCleanupReceipt]] = {}
_planning: set[asyncio.Task[MusicArtifactCleanupPlan]] = set()
_scans: set[asyncio.Task[MusicArtifactInventory]] = set()


class MusicArtifactError(Exception):
    def __init__(self, code: ArtifactErrorCode, status: int = 409) -> None:
        self.code, self.status = code, status
        super().__init__(code)


class _Stamp(ArtifactContract):
    device: int
    inode: int
    size: int = Field(ge=0, le=music_analysis.MAX_SOURCE_BYTES)
    modified_ns: int
    changed_ns: int


class _File(ArtifactContract):
    name: str = Field(min_length=1, max_length=128, pattern=r'^[a-zA-Z0-9_.-]+$')
    stamp: _Stamp
    sha256: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')


class _Proof(ArtifactContract):
    job_id: MusicAnalysisId
    job_revision: int = Field(ge=0)
    attempt: int = Field(ge=1)
    source: MusicSourceRef
    result_id: MusicAnalysisId
    audio_sha256: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    snapshot_name: str = Field(min_length=1, max_length=128, pattern=r'^source\.(wav|mp3|flac|ogg|m4a|aac|aif|aiff)$')
    source_stamp: _Stamp
    files: list[_File] = Field(min_length=1, max_length=128)

    @model_validator(mode='after')
    def unique_files(self) -> _Proof:
        names = [file.name for file in self.files]
        if len(set(names)) != len(names) or any(name in {'.', '..'} for name in names):
            raise ValueError('artifact_invalid_manifest')
        snapshot = next((file for file in self.files if file.name == self.snapshot_name), None)
        if snapshot is None or snapshot.sha256 != self.audio_sha256:
            raise ValueError('artifact_snapshot_proof_mismatch')
        return self


class _Plan(ArtifactContract):
    plan: MusicArtifactCleanupPlan
    proofs: list[_Proof] = Field(max_length=20)


class _Receipt(ArtifactContract):
    receipt: MusicArtifactCleanupReceipt
    staged_job_ids: list[MusicAnalysisId] = Field(default_factory=list, max_length=20)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(path: Path, parent: Path) -> Path:
    if parent.is_symlink() or path.is_symlink() or path.resolve().parent != parent.resolve():
        raise MusicArtifactError('music_artifact_unsafe_path')
    return path


def _root() -> Path:
    if db.DATA_DIR.is_symlink():
        raise MusicArtifactError('music_artifact_unsafe_path')
    return _safe(db.DATA_DIR / 'music-artifacts', db.DATA_DIR)


def _directory(identifier: str, *, create: bool = False) -> Path:
    try:
        _id.validate_python(identifier)
    except ValidationError as error:
        raise MusicArtifactError('music_artifact_not_found', 404) from error
    root = _root()
    path = _safe(root / identifier, root)
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.mkdir(mode=0o700)
    return path


def _stamp(path: Path) -> _Stamp:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise MusicArtifactError('music_artifact_unsafe_path')
    return _Stamp(device=info.st_dev, inode=info.st_ino, size=info.st_size,
        modified_ns=info.st_mtime_ns, changed_ns=info.st_ctime_ns)


def _write(path: Path, document: BaseModel) -> None:
    encoded = document.model_dump_json()
    if len(encoded.encode()) > MAX_DOCUMENT:
        raise MusicArtifactError('music_artifact_limit')
    _safe(path, path.parent)
    write_object(path, _json.validate_json(encoded))


def _read(path: Path, maximum: int = MAX_DOCUMENT) -> bytes:
    _safe(path, path.parent)
    with path.open('rb') as reader:
        encoded = reader.read(maximum + 1)
    if len(encoded) > maximum:
        raise MusicArtifactError('music_artifact_limit')
    return encoded


def _plan_digest(document: _Plan) -> str:
    body = _json.validate_json(document.model_dump_json())
    plan = body['plan']
    if not isinstance(plan, dict):
        raise MusicArtifactError('music_artifact_invalid')
    del plan['plan_sha256'], plan['status']
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _load(identifier: str) -> _Plan:
    try:
        stored = _Plan.model_validate_json(_read(_directory(identifier) / 'plan.json'))
        if stored.plan.id != identifier or stored.plan.plan_sha256 != _plan_digest(stored):
            raise ValueError('invalid plan identity')
        selected = {item.job_id for item in stored.plan.items if item.disposition == 'eligible'}
        if (len({item.job_id for item in stored.plan.items}) != len(stored.plan.items)
                or len({proof.job_id for proof in stored.proofs}) != len(stored.proofs)
                or selected != {proof.job_id for proof in stored.proofs}):
            raise ValueError('invalid plan proof selection')
        return stored
    except FileNotFoundError as error:
        raise MusicArtifactError('music_artifact_not_found', 404) from error
    except (ValueError, ValidationError) as error:
        raise MusicArtifactError('music_artifact_storage_unavailable', 503) from error


def get_plan(identifier: str) -> MusicArtifactCleanupPlan:
    return _load(identifier).plan


def _receipt(identifier: str) -> _Receipt:
    stored = _load(identifier)
    receipt = _Receipt.model_validate_json(_read(_directory(identifier) / 'receipt.json'))
    if (receipt.receipt.plan_id != identifier or receipt.receipt.plan_sha256 != stored.plan.plan_sha256
            or [item.job_id for item in receipt.receipt.items] != [item.job_id for item in stored.plan.items]
            or not set(receipt.staged_job_ids).issubset({proof.job_id for proof in stored.proofs})):
        raise MusicArtifactError('music_artifact_storage_unavailable', 503)
    amounts = {proof.job_id: sum(file.stamp.size for file in proof.files) for proof in stored.proofs}
    for item in receipt.receipt.items:
        if (item.removed_bytes > amounts.get(item.job_id, 0)
                or item.status == 'removed' and (item.job_id not in receipt.staged_job_ids or item.removed_bytes != amounts.get(item.job_id))
                or item.status != 'removed' and item.removed_bytes != 0):
            raise MusicArtifactError('music_artifact_storage_unavailable', 503)
    expected_status = 'completed' if all(item.status == 'removed' for item in receipt.receipt.items) else 'blocked' if all(item.status == 'retained' for item in receipt.receipt.items) else 'partial'
    if receipt.receipt.status != expected_status:
        raise MusicArtifactError('music_artifact_storage_unavailable', 503)
    return receipt


def get_receipt(identifier: str) -> MusicArtifactCleanupReceipt:
    try:
        return _receipt(identifier).receipt
    except FileNotFoundError as error:
        raise MusicArtifactError('music_artifact_not_found', 404) from error


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    db.get_db()
    connection = sqlite3.connect(f'{db.DB_PATH.as_uri()}?mode=ro', uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def _referenced(workspace: Path) -> bool:
    # These namespaces normally cannot reference private analysis copies. Also
    # reject legacy/catalogued paths explicitly instead of relying on that rule.
    if not db.DB_PATH.exists():
        return False
    with _connection() as connection:
        tables = {row[0] for row in connection.execute('SELECT name FROM sqlite_master WHERE type=?', ('table',))}
        for table, columns in (('tracks', ('audio_path', 'abc_path', 'stems_json')),
                ('audio_versions', ('audio_path', 'captured_source_path')),
                ('music_training_jobs', ('payload_json',)), ('stable_lora_jobs', ('payload_json',))):
            if table not in tables:
                continue
            available = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
            for column in columns:
                if column in available:
                    for needle in {str(workspace), json.dumps(str(workspace), ensure_ascii=True)[1:-1], json.dumps(str(workspace), ensure_ascii=False)[1:-1]}:
                        if connection.execute(f'SELECT 1 FROM {table} WHERE instr({column},?)>0 LIMIT 1', (needle,)).fetchone():
                            return True
    return False


def _beat_request(root: Path, snapshot_name: str) -> tuple[music_analysis.AnalysisWorkerRequest, str]:
    from .music_beats_runtime import PACKAGES, SOURCE_SHA256, WEIGHTS_SHA256
    encoded = _read(root / 'request.json', 131072)
    try:
        request = music_analysis.AnalysisWorkerRequest.model_validate_json(encoded)
    except ValidationError as error:
        raise MusicArtifactError('music_artifact_unsafe_path') from error
    beats = request.beats
    if (request.job_id != root.name or request.snapshot_name != snapshot_name or beats is None
            or not Path(beats.runtime_root).is_absolute() or beats.identity.source_sha256 != SOURCE_SHA256
            or beats.identity.weights_sha256 != WEIGHTS_SHA256 or not beats.identity.python_version.startswith('3.12.')
            or {name: version.split('+')[0] for name, version in beats.identity.packages.items()} != PACKAGES):
        raise MusicArtifactError('music_artifact_unsafe_path')
    return request, hashlib.sha256(encoded).hexdigest()


def _files(root: Path, snapshot_name: str, attempt: int) -> list[tuple[Path, _Stamp]]:
    from .music_beats_runtime import WEIGHTS_BYTES
    _safe(root, root.parent)
    found: list[tuple[Path, _Stamp]] = []
    with os.scandir(root) as entries:
        for entry in entries:
            if len(found) >= 128:
                raise MusicArtifactError('music_artifact_limit')
            worker = re.fullmatch(r'worker-([1-9][0-9]{0,15})\.json', entry.name)
            checkpoint = entry.name == 'beat-checkpoint.ckpt'
            if checkpoint:
                _beat_request(root, snapshot_name)
            if entry.name not in {snapshot_name, 'request.json', 'result.json', 'completion.json'} and not checkpoint and not (worker and int(worker.group(1)) <= attempt):
                raise MusicArtifactError('music_artifact_unsafe_path')
            path = _safe(root / entry.name, root)
            stamp = _stamp(path)
            maximum = WEIGHTS_BYTES if checkpoint else music_analysis.MAX_SOURCE_BYTES if entry.name == snapshot_name else music_analysis.MAX_DOCUMENT_BYTES if entry.name == 'result.json' else 131072 if entry.name == 'request.json' else 65536
            if stamp.size > maximum:
                raise MusicArtifactError('music_artifact_limit')
            if checkpoint and stamp.size != WEIGHTS_BYTES:
                raise MusicArtifactError('music_artifact_unsafe_path')
            found.append((path, stamp))
    return sorted(found, key=lambda item: item[0].name)


def _owner_error(error: music_analysis.MusicAnalysisError) -> ArtifactReason:
    if error.code == 'worker_unverified':
        return 'worker_unverified'
    if error.code == 'source_missing':
        return 'source_missing'
    if error.code == 'source_changed':
        return 'source_changed'
    return 'owner_busy' if error.code == 'analysis_busy' else 'metadata_invalid'


async def _proof(identifier: str) -> _Proof:
    owner = await music_analysis.completed_workspace_cleanup_projection(identifier)
    if _referenced(owner.workspace):
        raise MusicArtifactError('music_artifact_referenced')
    original = _stamp(owner.source_path)
    files = await run_owned_thread(_files, owner.workspace, owner.snapshot_name, owner.attempt)
    snapshot = next((stamp for path, stamp in files if path.name == owner.snapshot_name), None)
    if snapshot is None:
        raise MusicArtifactError('music_artifact_source_missing')
    if await hash_file(owner.source_path, max_bytes=original.size) != owner.audio_sha256:
        raise MusicArtifactError('music_artifact_source_changed')
    manifest: list[_File] = []
    for path, stamp in files:
        checksum = await hash_file(path, max_bytes=stamp.size)
        if _stamp(path) != stamp:
            raise MusicArtifactError('music_artifact_plan_changed')
        if path.name == owner.snapshot_name and checksum != owner.audio_sha256:
            raise MusicArtifactError('music_artifact_source_changed')
        manifest.append(_File(name=path.name, stamp=stamp, sha256=checksum))
    checkpoint = next((file for file in manifest if file.name == 'beat-checkpoint.ckpt'), None)
    if checkpoint is not None:
        request, request_hash = await run_owned_thread(_beat_request, owner.workspace, owner.snapshot_name)
        job = await run_owned_thread(music_analysis.get, identifier)
        result = job.result
        beats = request.beats
        engines = [engine for engine in result.engines if engine.task == 'beats'] if result is not None else []
        if (beats is None or result is None or job.status != 'done' or job.id != identifier or job.source != owner.source
                or request.source != owner.source or request.audio_sha256 != owner.audio_sha256
                or request.result_id != owner.result_id or result.id != owner.result_id
                or result.audio_sha256 != owner.audio_sha256 or result.configuration_sha256 != request.configuration_sha256
                or not any(file.name == 'request.json' and file.sha256 == request_hash for file in manifest)
                or checkpoint.sha256 != beats.identity.weights_sha256 or result.weights_sha256 != checkpoint.sha256
                or len(engines) != 1 or engines[0].weights_sha256 != checkpoint.sha256
                or engines[0].configuration_sha256 != beats.identity.configuration_sha256
                or engines[0].name != 'beat-this-small0' or engines[0].revision != beats.identity.revision
                or engines[0].precision != 'float32'
                or not set(LICENSE_EVIDENCE_IDS).issubset(result.license_evidence_ids)):
            raise MusicArtifactError('music_artifact_unsafe_path')
    if _stamp(owner.source_path) != original:
        raise MusicArtifactError('music_artifact_plan_changed')
    return _Proof(job_id=owner.job_id, job_revision=owner.job_revision, attempt=owner.attempt, source=owner.source,
        result_id=owner.result_id, audio_sha256=owner.audio_sha256, snapshot_name=owner.snapshot_name,
        source_stamp=original, files=manifest)


async def _build_plan(request: MusicArtifactCleanupRequest) -> MusicArtifactCleanupPlan:
    proofs: list[_Proof] = []
    items: list[MusicArtifactCleanupItem] = []
    for identifier in request.analysis_job_ids:
        try:
            proof = await _proof(identifier)
            proofs.append(proof)
            items.append(MusicArtifactCleanupItem(job_id=identifier, source=proof.source, result_id=proof.result_id,
                source_sha256=proof.audio_sha256, bytes=sum(file.stamp.size for file in proof.files), file_count=len(proof.files), disposition='eligible'))
        except (music_analysis.MusicAnalysisError, MusicArtifactError, OSError, VideoProjectError) as error:
            reason: ArtifactReason = _owner_error(error) if isinstance(error, music_analysis.MusicAnalysisError) else (
                'referenced' if isinstance(error, MusicArtifactError) and error.code == 'music_artifact_referenced' else
                'source_changed' if isinstance(error, VideoProjectError) or isinstance(error, MusicArtifactError) and error.code in {'music_artifact_source_changed', 'music_artifact_plan_changed'} else 'unsafe_path')
            items.append(MusicArtifactCleanupItem(job_id=identifier, disposition='blocked', reasons=[reason]))
    plan = MusicArtifactCleanupPlan(id=uuid.uuid4().hex, created_at=_now(), plan_sha256='0' * 64, items=items)
    stored = _Plan(plan=plan, proofs=proofs)
    plan = MusicArtifactCleanupPlan.model_validate({**plan.model_dump(), 'plan_sha256': _plan_digest(stored)})
    stored = _Plan(plan=plan, proofs=proofs)
    folder = await run_owned_thread(_directory, plan.id, create=True)
    await run_owned_thread(_write, folder / 'plan.json', stored)
    return plan


async def create_plan(request: MusicArtifactCleanupRequest) -> MusicArtifactCleanupPlan:
    if _closing or _planning or len(_scans) >= 4 or _executions:
        raise MusicArtifactError('music_artifact_busy')
    task = asyncio.create_task(_build_plan(request), name='music-artifact-plan')
    _planning.add(task)
    task.add_done_callback(_planning.discard)
    return await task


async def _verify(proof: _Proof, directory: Path, *, resumed: bool = False) -> music_analysis.CompletedAnalysisWorkspace:
    owner = await music_analysis.completed_workspace_cleanup_projection(proof.job_id)
    if (owner.job_revision != proof.job_revision or owner.source != proof.source or owner.result_id != proof.result_id
            or owner.audio_sha256 != proof.audio_sha256 or owner.snapshot_name != proof.snapshot_name
            or owner.attempt != proof.attempt or _stamp(owner.source_path) != proof.source_stamp or _referenced(owner.workspace)):
        raise MusicArtifactError('music_artifact_plan_changed')
    if await hash_file(owner.source_path, max_bytes=proof.source_stamp.size) != proof.audio_sha256:
        raise MusicArtifactError('music_artifact_source_changed')
    found = await run_owned_thread(_files, directory, proof.snapshot_name, proof.attempt)
    expected = {file.name: file for file in proof.files}
    if (not resumed and len(found) != len(expected)) or any(path.name not in expected for path, _ in found):
        raise MusicArtifactError('music_artifact_plan_changed')
    for path, stamp in found:
        file = expected[path.name]
        if stamp != file.stamp or await hash_file(path, max_bytes=stamp.size) != file.sha256:
            raise MusicArtifactError('music_artifact_plan_changed')
    if found and proof.snapshot_name not in {path.name for path, _ in found}:
        raise MusicArtifactError('music_artifact_plan_changed')
    return owner


def _stage(identifier: str, job_id: str) -> Path:
    folder = _directory(identifier)
    parent = _safe(folder / 'staged', folder)
    parent.mkdir(mode=0o700, exist_ok=True)
    return _safe(parent / job_id, parent)


def _remove_files(directory: Path, proof: _Proof) -> None:
    _safe(directory, directory.parent)
    known = {file.name for file in proof.files}
    if any(path.name not in known for path, _ in _files(directory, proof.snapshot_name, proof.attempt)):
        raise MusicArtifactError('music_artifact_plan_changed')
    for file in sorted(proof.files, key=lambda item: (item.name == proof.snapshot_name, item.name)):
        path = _safe(directory / file.name, directory)
        if path.exists():
            if _stamp(path) != file.stamp:
                raise MusicArtifactError('music_artifact_plan_changed')
            path.unlink()
    directory.rmdir()


def _record(stored: _Plan, outcomes: list[MusicArtifactCleanupOutcome], staged: set[str]) -> MusicArtifactCleanupReceipt:
    status: Literal['completed', 'blocked', 'partial'] = 'completed' if all(item.status == 'removed' for item in outcomes) else 'blocked' if all(item.status == 'retained' for item in outcomes) else 'partial'
    receipt = MusicArtifactCleanupReceipt(plan_id=stored.plan.id, plan_sha256=stored.plan.plan_sha256, status=status, items=outcomes)
    folder = _directory(stored.plan.id)
    _write(folder / 'receipt.json', _Receipt(receipt=receipt, staged_job_ids=sorted(staged)))
    plan = MusicArtifactCleanupPlan.model_validate({**stored.plan.model_dump(), 'status': 'completed' if status == 'completed' else 'partial'})
    _write(folder / 'plan.json', _Plan(plan=plan, proofs=stored.proofs))
    return receipt


async def _execute(identifier: str) -> MusicArtifactCleanupReceipt:
    stored = _load(identifier)
    previous = _receipt(identifier)
    outcomes = list(previous.receipt.items)
    staged = set(previous.staged_job_ids)
    index_by_id = {item.job_id: index for index, item in enumerate(outcomes)}
    try:
        # Check the entire selection before detaching even the first directory.
        for proof in stored.proofs:
            if outcomes[index_by_id[proof.job_id]].status == 'removed':
                continue
            stage = _stage(identifier, proof.job_id)
            owner = await music_analysis.completed_workspace_cleanup_projection(proof.job_id)
            if owner.workspace.exists() and (stage.exists() or proof.job_id in staged):
                raise MusicArtifactError('music_artifact_plan_changed')
            if proof.job_id in staged and not stage.exists():
                continue
            await _verify(proof, stage if stage.exists() else owner.workspace, resumed=stage.exists())
        for proof in stored.proofs:
            index = index_by_id[proof.job_id]
            if outcomes[index].status == 'removed':
                continue
            stage = _stage(identifier, proof.job_id)
            if not (proof.job_id in staged and not stage.exists()):
                async with music_analysis.protect_completed_workspace_cleanup(proof.job_id, proof.job_revision) as owner:
                    if stage.exists() and owner.workspace.exists():
                        raise MusicArtifactError('music_artifact_plan_changed')
                    if (_stamp(owner.source_path) != proof.source_stamp or owner.source != proof.source
                            or owner.audio_sha256 != proof.audio_sha256 or owner.result_id != proof.result_id
                            or owner.snapshot_name != proof.snapshot_name or _referenced(owner.workspace)):
                        raise MusicArtifactError('music_artifact_plan_changed')
                    directory = stage if stage.exists() else owner.workspace
                    files = _files(directory, proof.snapshot_name, proof.attempt)
                    expected = {file.name: file.stamp for file in proof.files}
                    if any(stamp != expected.get(path.name) for path, stamp in files):
                        raise MusicArtifactError('music_artifact_plan_changed')
                    if not stage.exists():
                        if len(files) != len(expected):
                            raise MusicArtifactError('music_artifact_plan_changed')
                        owner.workspace.rename(stage)
                staged.add(proof.job_id)
                await run_owned_thread(_record, stored, outcomes, staged)
                await run_owned_thread(_remove_files, stage, proof)
            outcomes[index] = MusicArtifactCleanupOutcome(job_id=proof.job_id, status='removed', removed_bytes=sum(file.stamp.size for file in proof.files))
            await run_owned_thread(_record, stored, outcomes, staged)
    except (asyncio.CancelledError, OSError, ValueError, MusicArtifactError, music_analysis.MusicAnalysisError, VideoProjectError) as error:
        code: ArtifactErrorCode = error.code if isinstance(error, MusicArtifactError) else 'music_artifact_worker_unverified' if isinstance(error, music_analysis.MusicAnalysisError) and error.code == 'worker_unverified' else 'music_artifact_plan_changed' if isinstance(error, (music_analysis.MusicAnalysisError, VideoProjectError)) else 'music_artifact_storage_unavailable'
        logger.warning('Reviewed music cleanup remains incomplete: %s', identifier)
        outcomes = [item if item.status == 'removed' else MusicArtifactCleanupOutcome(job_id=item.job_id,
            status='pending' if staged else 'retained', error_code=code) for item in outcomes]
    return await await_cleanup(run_owned_thread(_record, stored, outcomes, staged))


async def confirm_plan(identifier: str, confirmation: ConfirmMusicArtifactCleanup) -> MusicArtifactCleanupReceipt:
    async with _lock:
        if _closing:
            raise MusicArtifactError('music_artifact_busy')
        stored = _load(identifier)
        if confirmation.plan_sha256 != stored.plan.plan_sha256:
            raise MusicArtifactError('music_artifact_confirmation_required')
        if any(item.disposition != 'eligible' for item in stored.plan.items):
            return _record(stored, [MusicArtifactCleanupOutcome(job_id=item.job_id, status='retained', error_code='music_artifact_invalid') for item in stored.plan.items], set())
        task = _executions.get(identifier)
        if task is None:
            if _executions or _planning:
                raise MusicArtifactError('music_artifact_busy')
            try:
                prior = _receipt(identifier)
                if prior.receipt.status == 'completed':
                    return prior.receipt
            except FileNotFoundError:
                prior = _Receipt(receipt=MusicArtifactCleanupReceipt(plan_id=identifier, plan_sha256=stored.plan.plan_sha256,
                    status='partial', items=[MusicArtifactCleanupOutcome(job_id=item.job_id, status='pending') for item in stored.plan.items]))
                _write(_directory(identifier) / 'receipt.json', prior)
            task = asyncio.create_task(_execute(identifier), name=f'music-artifact-cleanup-{identifier}')
            _executions[identifier] = task
            task.add_done_callback(lambda finished: _executions.pop(identifier, None))
    # A disconnected browser does not abandon confirmed destructive work.
    return await asyncio.shield(task)


@dataclass
class _Scan:
    remaining: int
    deadline: float
    complete: bool = True

    def take(self) -> bool:
        if self.remaining <= 0 or time.monotonic() >= self.deadline:
            self.complete = False
            return False
        self.remaining -= 1
        return True


def _size(root: Path, scan: _Scan) -> tuple[int | None, bool]:
    if root.is_symlink() or root.resolve() != root.absolute():
        return None, False
    pending = [(root, 0)]
    total, complete = 0, True
    try:
        while pending:
            path, depth = pending.pop()
            if not scan.take():
                return total, False
            try:
                info = path.lstat()
            except FileNotFoundError:
                if path == root:
                    return 0, True
                complete = False
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
                if total > 9007199254740991:
                    return None, False
            elif stat.S_ISDIR(info.st_mode) and depth < MAX_DEPTH:
                with os.scandir(path) as children:
                    for child in children:
                        if len(pending) >= scan.remaining or not scan.take():
                            return total, False
                        pending.append((path / child.name, depth + 1))
            else:
                complete = False
        return total, complete
    except OSError:
        return None, False


def _children(root: Path, scan: _Scan) -> list[Path]:
    if not root.exists():
        return []
    if root.is_symlink() or root.resolve() != root.absolute():
        scan.complete = False
        return []
    found: list[Path] = []
    try:
        with os.scandir(root) as children:
            for child in children:
                if not scan.take():
                    break
                found.append(root / child.name)
    except OSError:
        scan.complete = False
    return sorted(found)


def _inventory() -> MusicArtifactInventory:
    from .module_catalog import configured_environment
    scan = _Scan(MAX_ENTRIES, time.monotonic() + SCAN_SECONDS)
    groups: list[MusicArtifactGroup] = []

    def group(identifier: str, kind: str, alias: str, path: Path, reasons: list[ArtifactReason],
              dependencies: list[MusicArtifactDependency], *, review: bool = False) -> None:
        if len(groups) >= 1100:
            scan.complete = False
            return
        size, complete = _size(path, scan)
        if not complete:
            scan.complete = False
        groups.append(MusicArtifactGroup.model_validate({'id': identifier, 'kind': kind, 'root_alias': alias,
            'relative_path': path.name, 'size_bytes': size, 'size_complete': complete,
            'disposition': 'review' if review and complete else 'blocked' if review else 'retained',
            'reasons': reasons + ([] if complete else ['incomplete_inventory']), 'dependencies': dependencies}))

    # Detached copies remain discoverable after restart, without resuming deletion.
    for folder in _children(_root(), scan):
        if re.fullmatch(r'[0-9a-f]{32}', folder.name) is None or folder.is_symlink():
            scan.complete = False
            continue
        for staged in _children(folder / 'staged', scan):
            if re.fullmatch(r'[0-9a-f]{32}', staged.name) is None:
                scan.complete = False
                continue
            group(f'staged.{folder.name}.{staged.name}', 'cleanup_staging', 'cleanup_staging', staged,
                ['cleanup_pending'], [MusicArtifactDependency(kind='cleanup_plan', id=folder.name),
                    MusicArtifactDependency(kind='analysis_job', id=staged.name)])

    owners: dict[str, tuple[str, str | None, bool]] = {}
    training_parents: set[str] = set()
    if db.DB_PATH.exists():
        with _connection() as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'music_analysis_jobs' in tables:
                for row in connection.execute('SELECT id,status,result_id,worker_identity_json FROM music_analysis_jobs LIMIT 1001'):
                    if not scan.take() or len(owners) >= 1000:
                        scan.complete = False
                        break
                    identifier, status, result_id, identity = row
                    if not isinstance(identifier, str) or re.fullmatch(r'[0-9a-f]{32}', identifier) is None or not isinstance(status, str):
                        scan.complete = False
                        continue
                    owners[identifier] = (status, result_id if isinstance(result_id, str) else None, identity is not None)
            if 'music_training_jobs' in tables:
                for row in connection.execute('SELECT payload_json FROM music_training_jobs LIMIT 1001'):
                    if not scan.take():
                        break
                    payload: object = row[0]
                    if not isinstance(payload, str) or len(payload) > 131072:
                        scan.complete = False
                        continue
                    try:
                        request = _json.validate_json(payload).get('request')
                        parent = request.get('yue_ar_source_job_id') if isinstance(request, dict) else None
                        if isinstance(parent, str) and re.fullmatch(r'[0-9a-f]{32}', parent):
                            training_parents.add(parent)
                    except ValidationError:
                        scan.complete = False
    for workspace in _children(db.DATA_DIR / 'music-analysis', scan):
        if re.fullmatch(r'[0-9a-f]{32}', workspace.name) is None:
            scan.complete = False
            continue
        owner = owners.get(workspace.name)
        dependencies = [MusicArtifactDependency(kind='analysis_job', id=workspace.name)]
        if owner is not None and owner[1] is not None:
            dependencies.append(MusicArtifactDependency(kind='analysis_result', id=owner[1]))
        reasons: list[ArtifactReason] = ['metadata_invalid'] if owner is None else ['worker_unverified'] if owner[2] else ['active_job'] if owner[0] != 'done' else []
        group(f'analysis.{workspace.name}', 'analysis_workspace', 'music_analysis', workspace, reasons,
            dependencies, review=owner is not None and owner[0] == 'done' and not owner[2])

    training_dependencies = [MusicArtifactDependency(kind='training_job', id=identifier) for identifier in sorted(training_parents)[:128]]
    if len(training_parents) > 128:
        scan.complete = False
    training_reasons: list[ArtifactReason] = ['protected_checkpoint']
    if training_parents:
        training_reasons.append('ar_training_parent')
    group('music-training', 'training_workspace', 'music_training', db.DATA_DIR / 'music-lora-training',
        training_reasons, training_dependencies)
    group('stable-training', 'training_workspace', 'stable_training', db.DATA_DIR / 'stable-lora-training', ['protected_checkpoint'], [])
    group('generation', 'generation_workspace', 'local_music', db.DATA_DIR / 'local-music', ['protected_media'], [])
    group('datasets-metadata', 'dataset', 'datasets', db.DATA_DIR / 'lora-datasets', ['protected_dataset'], [])
    group('registered-adapters', 'registered_adapter', 'registered_adapters', db.DATA_DIR / 'music-lora', ['protected_adapter'], [])
    group('library-media', 'original_media', 'library_files', db.FILES_DIR, ['protected_media'], [])
    group('library-models', 'model', 'library_models', db.DATA_DIR / 'models', ['protected_model'], [])
    environment = configured_environment()
    group('installer-cache', 'installer_cache', 'module_runtime', environment.root / 'cache', ['protected_model'], [])
    group('installer-workspaces', 'installer_cache', 'module_runtime', environment.root / '.setup' / 'staging', ['protected_model'], [])
    group('ace-datasets', 'dataset', 'datasets', environment.paths['ace_step'] / 'datasets', ['protected_dataset'],
        [MusicArtifactDependency(kind='module', id='ace_step')])
    model_folders: tuple[tuple[ModuleId, str], ...] = (('ace_step', 'checkpoints'), ('yue2', 'models'), ('stable_audio3', 'models'))
    for module, model_folder in model_folders:
        group(f'models.{module}', 'model', 'module_runtime', environment.paths[module] / model_folder, ['protected_model'],
            [MusicArtifactDependency(kind='module', id=module)])
    inventory = MusicArtifactInventory(generated_at=_now(), groups=groups, scan_complete=scan.complete)
    if len(inventory.model_dump_json().encode()) > MAX_DOCUMENT:
        raise MusicArtifactError('music_artifact_limit')
    return inventory


async def inventory() -> MusicArtifactInventory:
    if _closing or len(_scans) >= 4:
        raise MusicArtifactError('music_artifact_busy')
    task = asyncio.create_task(run_owned_thread(_inventory), name='music-artifact-inventory')
    _scans.add(task)
    task.add_done_callback(_scans.discard)
    return await task


def work_busy() -> bool:
    return bool(_planning or _scans or _executions)


async def recover() -> None:
    global _closing
    async with _lock:
        _closing = False


async def shutdown() -> None:
    global _closing
    async with _lock:
        _closing = True
        tasks = [*_planning, *_scans, *_executions.values()]
    for task in tasks:
        if not task.done():
            task.cancel()
    await await_cleanup(asyncio.gather(*tasks, return_exceptions=True))
