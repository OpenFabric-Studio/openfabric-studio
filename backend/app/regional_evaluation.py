"""Saved matched comparisons using the existing generator and retained CPU copies."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import hashlib
import os
from pathlib import Path
from typing import Annotated
import uuid

from pydantic import Field

from . import db, local_music, music_lora, regional_datasets as datasets, regional_dataset_store as store, stable_lora
from .contracts import JsonObject
from .job_lifecycle import await_cleanup, run_owned_thread
from .local_music_contracts import LocalMusicRequest
from .module_catalog import available_bytes
from .music_evidence import capture_music_evidence
from .regional_dataset_contracts import RegionalContract, RegionalDatasetError, RegionalPreparedSelection
from .regional_evaluation_contracts import (RegionalComparisonArm, RegionalComparisonArmId, RegionalComparisonPlan,
    RegionalComparisonSpec, RegionalComparisonState, RegionalComparisons, RegionalEvaluationRequest,
    RegionalListeningReview, RegionalMeasuredModel, RegionalMeasuredRuntime, RegionalTrainingReceipt)
from .stable_lora_contracts import StableLoraCompletion
from .stable_lora_runtime import fingerprint
from .video_io import copy_verified, hash_file

_lock = asyncio.Lock()
_reading = 0
_closing = False
_idle = asyncio.Event()
_idle.set()


class _RuntimeModel(RegionalContract):
    path: str = Field(min_length=1, max_length=300, pattern=r'^[A-Za-z0-9_./-]+$')
    sha256: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    bytes: int = Field(ge=1, le=100_000_000_000)


class _RuntimeReceipt(RegionalContract):
    models: list[_RuntimeModel] = Field(min_length=1, max_length=6)
    source_sha256: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    source_file_count: int = Field(ge=1, le=500)
    python_executable_sha256: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    python_version: str = Field(min_length=1, max_length=80)
    platform: str = Field(min_length=1, max_length=40)
    machine: str = Field(max_length=80)
    packages: dict[Annotated[str, Field(max_length=80)], Annotated[str, Field(max_length=120)]] = Field(max_length=4)


class _DatasetReceipt(RegionalContract):
    request_sha256: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    samples: list[JsonObject] = Field(min_length=1, max_length=100)
    runtime_identity: _RuntimeReceipt | None = None


def work_busy() -> bool:
    return _reading > 0


@asynccontextmanager
async def _reading_files() -> AsyncIterator[None]:
    global _reading
    from .resource_admission import admission_lock, require_setup_idle
    async with admission_lock:
        require_setup_idle()
        if _closing:
            raise RegionalDatasetError('regional_dataset_busy')
        _reading += 1; _idle.clear()
    try:
        yield
    finally:
        _reading -= 1
        if _reading == 0:
            _idle.set()


def _confined(path: Path, root: Path) -> Path:
    if root.is_symlink() or root.resolve() != root or path.is_symlink() or path.resolve() != path or not path.is_relative_to(root):
        raise RegionalDatasetError('regional_dataset_source_changed')
    current = path
    while current != root:
        if current.is_symlink():
            raise RegionalDatasetError('regional_dataset_source_changed')
        current = current.parent
    return path


async def training_receipt(identifier: str) -> RegionalTrainingReceipt:
    job = stable_lora.get(identifier)
    if job.status != 'done' or job.adapter is None or job.adapter.source_job_id != job.id:
        raise RegionalDatasetError('regional_dataset_source_changed')
    root = db.DATA_DIR.resolve()
    directory = _confined(root / 'stable-lora-training' / job.id, root)
    completion_path = _confined(directory / 'completion.json', root)
    dataset_path = _confined(directory / 'dataset.json', root)
    if completion_path.stat().st_size > 16384 or dataset_path.stat().st_size > 2 * 1024**2:
        raise RegionalDatasetError('regional_dataset_limit', 422)
    completion = StableLoraCompletion.model_validate_json(await run_owned_thread(_read_bounded, completion_path, 16384))
    content = await run_owned_thread(_read_bounded, dataset_path, 2 * 1024**2)
    checksum = hashlib.sha256(content).hexdigest()
    receipt = _DatasetReceipt.model_validate_json(content)
    request_sha = fingerprint(job.request)
    if (completion.job_id != job.id or completion.request_sha256 != request_sha or receipt.request_sha256 != request_sha
            or completion.dataset_sha256 != checksum or completion.variant != job.request.variant or completion.max_steps != job.request.max_steps
            or completion.sha256 != job.adapter.sha256):
        raise RegionalDatasetError('regional_dataset_source_changed')
    runtime = receipt.runtime_identity
    measured = None if runtime is None else RegionalMeasuredRuntime(models=[RegionalMeasuredModel(
        file_name=Path(model.path).name, sha256=model.sha256, bytes=model.bytes) for model in runtime.models],
        source_sha256=runtime.source_sha256, source_file_count=runtime.source_file_count,
        python_executable_sha256=runtime.python_executable_sha256, python_version=runtime.python_version,
        platform=runtime.platform, machine=runtime.machine, mlx_version=runtime.packages.get('mlx', 'unavailable'),
        numpy_version=runtime.packages.get('numpy', 'unavailable'))
    return RegionalTrainingReceipt(job_id=job.id, request_sha256=request_sha, dataset_sha256=checksum,
        runtime=measured, identity_status='unavailable' if measured is None else 'worker_measured')


def _read_bounded(path: Path, limit: int) -> bytes:
    with path.open('rb') as handle:
        result = handle.read(limit + 1)
    if len(result) > limit:
        raise RegionalDatasetError('regional_dataset_limit', 422)
    return result


def get_plan(identifier: str) -> RegionalComparisonPlan:
    row = db.get_db().execute('SELECT * FROM regional_comparisons WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise RegionalDatasetError('regional_dataset_not_found', 404)
    spec = RegionalComparisonSpec.model_validate_json(row['body_json'])
    checksum = store.canonical_sha256(spec)
    if (spec.id != identifier or checksum != row['spec_sha256'] or spec.request.manifest_id != row['manifest_id']
            or spec.request.submission_key != row['submission_key'] or store.canonical_sha256(spec.request) != row['request_sha256']):
        raise RegionalDatasetError('regional_dataset_storage_unavailable', 503)
    return RegionalComparisonPlan(sha256=checksum, spec=spec)


def arm_request(plan: RegionalComparisonPlan, arm: RegionalComparisonArmId) -> LocalMusicRequest:
    return plan.spec.request.request.model_copy(update={'title': f'Regional comparison {plan.spec.id} {arm}',
        'adapter': plan.spec.request.request.adapter if arm == 'adapter' else None})


def get(identifier: str) -> RegionalComparisonState:
    plan = get_plan(identifier)
    rows = db.get_db().execute('SELECT * FROM regional_comparison_arms WHERE comparison_id=? ORDER BY arm DESC', (identifier,)).fetchall()
    arms: list[RegionalComparisonArm] = []
    for row in rows:
        arm: RegionalComparisonArmId = 'base' if row['arm'] == 'base' else 'adapter'
        job = local_music.get(row['job_id']) if row['job_id'] is not None else None
        if job is not None and job.request != arm_request(plan, arm):
            raise RegionalDatasetError('regional_dataset_source_changed')
        if job is not None and job.adapter != (plan.spec.adapter if arm == 'adapter' else None):
            raise RegionalDatasetError('regional_dataset_source_changed')
        checksum: object = row['output_sha256']
        arms.append(RegionalComparisonArm(arm=arm, job=job, output_sha256=checksum if isinstance(checksum, str) else None,
            audio_url=f'/api/music/regional-datasets/comparisons/{identifier}/audio/{arm}' if checksum is not None else None,
            submission_unverified=bool(row['submitting'])))
    count = db.get_db().execute('SELECT COUNT(*) FROM regional_listening_reviews WHERE comparison_id=?', (identifier,)).fetchone()[0]
    return RegionalComparisonState(plan=plan, arms=arms, listener_reviews=count)


def list_comparisons(manifest_id: str) -> RegionalComparisons:
    rows = db.get_db().execute('SELECT id FROM regional_comparisons WHERE manifest_id=? ORDER BY created_at DESC LIMIT 100', (manifest_id,)).fetchall()
    return RegionalComparisons(comparisons=[get(row['id']) for row in rows])


async def create(request: RegionalEvaluationRequest) -> RegionalComparisonPlan:
    async with _reading_files(), _lock:
        connection = db.get_db()
        old = connection.execute('SELECT id,request_sha256 FROM regional_comparisons WHERE submission_key=?', (request.submission_key,)).fetchone()
        if old is not None:
            if old['request_sha256'] != store.canonical_sha256(request):
                raise RegionalDatasetError('regional_dataset_revision_conflict')
            return get_plan(old['id'])
        selected = datasets.training_selection(RegionalPreparedSelection(manifest_id=request.manifest_id, manifest_sha256=request.manifest_sha256))
        manifest = datasets.get_manifest(request.manifest_id).manifest
        if not selected.evaluation_sufficient:
            raise RegionalDatasetError('regional_dataset_split_insufficient')
        adapter = music_lora.get(request.adapter_id)
        if (adapter.sha256 != request.adapter_sha256 or adapter.engine != 'stable_audio3'
                or adapter.target != request.request.variant or adapter.source_job_id is None):
            raise RegionalDatasetError('regional_dataset_source_changed')
        trained = stable_lora.get(adapter.source_job_id)
        if trained.status != 'done' or trained.adapter != adapter or trained.request.samples != selected.samples:
            raise RegionalDatasetError('regional_dataset_holdout')
        receipt = await training_receipt(trained.id)
        if receipt.job_id != trained.id or receipt.request_sha256 != fingerprint(trained.request):
            raise RegionalDatasetError('regional_dataset_source_changed')
        spec = RegionalComparisonSpec(id=uuid.uuid4().hex, created_at=store.now(), request=request, adapter=adapter,
            training_job_id=trained.id, training_request=trained.request, training_receipt=receipt,
            held_out_excerpt_ids=[item.id for item in manifest.excerpts if item.split == 'holdout'],
            publisher_declarations=capture_music_evidence('stable_audio3'))
        connection.execute('BEGIN IMMEDIATE')
        try:
            connection.execute('INSERT INTO regional_comparisons(id,manifest_id,submission_key,request_sha256,spec_sha256,body_json,created_at) VALUES(?,?,?,?,?,?,?)',
                (spec.id, request.manifest_id, request.submission_key, store.canonical_sha256(request), store.canonical_sha256(spec), spec.model_dump_json(), spec.created_at))
            for arm in ('base', 'adapter'):
                connection.execute('INSERT INTO regional_comparison_arms(comparison_id,arm) VALUES(?,?)', (spec.id, arm))
            connection.commit()
        except BaseException:
            connection.rollback(); raise
        return get_plan(spec.id)


async def submit_arm(identifier: str, arm: RegionalComparisonArmId) -> RegionalComparisonState:
    async with _lock:
        state = get(identifier)
        row = next(item for item in state.arms if item.arm == arm)
        if row.job is not None:
            return state
        if row.submission_unverified or _closing:
            raise RegionalDatasetError('regional_dataset_worker_unverified')
        current = music_lora.get(state.plan.spec.adapter.id)
        if current != state.plan.spec.adapter:
            raise RegionalDatasetError('regional_dataset_source_changed')
        connection = db.get_db()
        connection.execute('UPDATE regional_comparison_arms SET submitting=1 WHERE comparison_id=? AND arm=?', (identifier, arm)); connection.commit()
        try:
            job = await local_music.submit(arm_request(state.plan, arm))
        except BaseException:
            connection.execute('UPDATE regional_comparison_arms SET submitting=0 WHERE comparison_id=? AND arm=?', (identifier, arm)); connection.commit()
            raise
        # No await between accepted native job and its durable comparison link.
        try:
            connection.execute('UPDATE regional_comparison_arms SET submitting=0,job_id=? WHERE comparison_id=? AND arm=?', (job.id, identifier, arm)); connection.commit()
        except BaseException:
            connection.rollback()
            raise
        return get(identifier)


def _audio_path(identifier: str, arm: RegionalComparisonArmId, *, create: bool = False) -> Path:
    root = db.FILES_DIR.resolve()
    directory = _confined(root / '_regional_evaluations' / identifier, root)
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return _confined(directory / (arm + '.wav'), root)


async def collect(identifier: str, arm: RegionalComparisonArmId) -> RegionalComparisonState:
    async with _reading_files(), _lock:
        state = get(identifier)
        row = next(item for item in state.arms if item.arm == arm)
        if row.output_sha256 is not None:
            await audio(identifier, arm)
            return state
        if row.job is None or row.job.status != 'done':
            raise RegionalDatasetError('regional_dataset_busy')
        source = local_music.completed_audio(row.job.id)
        free = available_bytes(db.FILES_DIR)
        if free is None or free < source.path.stat().st_size * 2 + 64 * 1024**2:
            raise RegionalDatasetError('insufficient_disk')
        target = _audio_path(identifier, arm, create=True)
        pending = target.with_suffix('.partial.wav')
        if target.exists():
            if await hash_file(target, max_bytes=512 * 1024**2) != source.sha256:
                raise RegionalDatasetError('regional_dataset_source_changed')
        else:
            if pending.exists() or pending.is_symlink():
                if pending.is_symlink() or await hash_file(pending, max_bytes=512 * 1024**2) != source.sha256:
                    raise RegionalDatasetError('regional_dataset_source_changed')
            else:
                await copy_verified(source.path, pending, source.sha256, max_bytes=512 * 1024**2)
            with pending.open('rb') as handle:
                os.fsync(handle.fileno())
            os.link(pending, target, follow_symlinks=False)
            try:
                pending.unlink()
            except OSError:
                pass  # Completed verified audio survives an unsuccessful partial cleanup.
        connection = db.get_db()
        connection.execute('UPDATE regional_comparison_arms SET output_sha256=? WHERE comparison_id=? AND arm=?', (source.sha256, identifier, arm)); connection.commit()
        return get(identifier)


async def audio(identifier: str, arm: RegionalComparisonArmId) -> Path:
    state = get(identifier)
    expected = next(item.output_sha256 for item in state.arms if item.arm == arm)
    path = _audio_path(identifier, arm)
    if expected is None or await hash_file(path, max_bytes=512 * 1024**2) != expected:
        raise RegionalDatasetError('regional_dataset_source_changed')
    return path


async def review(identifier: str, body: RegionalListeningReview) -> RegionalComparisonState:
    async with _reading_files(), _lock:
        state = get(identifier)
        checksums = {item.arm: item.output_sha256 for item in state.arms}
        if (body.comparison_sha256 != state.plan.sha256 or checksums['base'] != body.base_output_sha256
                or checksums['adapter'] != body.adapter_output_sha256):
            raise RegionalDatasetError('regional_dataset_source_changed')
        await audio(identifier, 'base'); await audio(identifier, 'adapter')
        connection = db.get_db()
        old = connection.execute('SELECT body_json FROM regional_listening_reviews WHERE comparison_id=? AND reviewer_reference=?', (identifier, body.reviewer_reference)).fetchone()
        if old is not None:
            if RegionalListeningReview.model_validate_json(old['body_json']) != body:
                raise RegionalDatasetError('regional_dataset_revision_conflict')
            return state
        if state.listener_reviews >= 10000:
            raise RegionalDatasetError('regional_dataset_limit', 422)
        connection.execute('INSERT INTO regional_listening_reviews(comparison_id,reviewer_reference,body_json) VALUES(?,?,?)', (identifier, body.reviewer_reference, body.model_dump_json())); connection.commit()
        return get(identifier)


async def recover() -> None:
    global _closing
    _closing = True
    rows = db.get_db().execute('SELECT comparison_id,arm FROM regional_comparison_arms WHERE submitting=1').fetchall()
    for row in rows:
        plan = get_plan(row['comparison_id'])
        arm: RegionalComparisonArmId = 'base' if row['arm'] == 'base' else 'adapter'
        expected = arm_request(plan, arm)
        matches = db.get_db().execute("SELECT id FROM local_music_jobs WHERE json_extract(payload_json,'$.request.title')=? LIMIT 2", (expected.title,)).fetchall()
        if len(matches) == 1 and local_music.get(matches[0]['id']).request == expected:
            connection = db.get_db()
            connection.execute('UPDATE regional_comparison_arms SET job_id=?,submitting=0 WHERE comparison_id=? AND arm=?', (matches[0]['id'], row['comparison_id'], arm)); connection.commit()
        # Ambiguous/no child stays explicitly unverified; recovery never starts inference.
    _closing = False


async def shutdown() -> None:
    global _closing
    _closing = True
    await await_cleanup(_idle.wait())
