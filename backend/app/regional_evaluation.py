"""Saved matched comparisons using the existing generator and retained CPU copies."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import hashlib
import os
from pathlib import Path
import secrets
from typing import Annotated, Literal
import uuid

from pydantic import Field, ValidationError

from . import db, local_music, music_lora, regional_datasets as datasets, regional_dataset_store as store, stable_lora
from .contracts import JsonObject
from .job_lifecycle import await_cleanup, run_owned_thread
from .local_music_contracts import LocalMusicRequest
from .module_catalog import available_bytes
from .music_evidence import capture_music_evidence
from .regional_dataset_contracts import RegionalContract, RegionalDatasetError, RegionalDatasetManifest, RegionalPreparedSelection
from .regional_evaluation_contracts import (RegionalAttributionNotice, RegionalBlindClip, RegionalBlindLabel,
    RegionalBlindReveal, RegionalBlindReview, RegionalBlindSession, RegionalComparisonArm, RegionalComparisonArmId,
    RegionalComparisonPlan, RegionalComparisonSpec, RegionalComparisonState, RegionalComparisons, RegionalEvaluationRequest,
    RegionalHashNotice, RegionalListeningReview, RegionalMeasuredModel, RegionalMeasuredRuntime, RegionalModelCard,
    RegionalTrainingReceipt, regional_model_card_notices)
from .stable_lora_contracts import StableLoraCompletion
from .stable_lora_runtime import fingerprint
from .video_io import copy_verified, hash_file

_lock = asyncio.Lock()
_reading = 0
_closing = False
_idle = asyncio.Event()
_idle.set()


class _BlindAssignment(RegionalContract):
    comparison_sha256: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    label_a_arm: RegionalComparisonArmId
    label_a_sha256: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    label_b_sha256: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')


class _BlindReviewRecord(RegionalContract):
    review: RegionalBlindReview
    label_a_arm: RegionalComparisonArmId
    decoded_preference: Literal['base', 'adapter', 'tie', 'neither']


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
    blind = db.get_db().execute('SELECT COUNT(*) FROM regional_blind_reviews WHERE comparison_id=?', (identifier,)).fetchone()[0]
    return RegionalComparisonState(plan=plan, arms=arms, listener_reviews=count, blind_reviews=blind)


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


def blind_download_name(label: RegionalBlindLabel) -> str:
    return f'comparison-{label}.wav'


def _other_arm(arm: RegionalComparisonArmId) -> RegionalComparisonArmId:
    return 'adapter' if arm == 'base' else 'base'


def _decoded_preference(preference: Literal['a', 'b', 'tie', 'neither'], label_a_arm: RegionalComparisonArmId) -> Literal['base', 'adapter', 'tie', 'neither']:
    if preference == 'a':
        return label_a_arm
    if preference == 'b':
        return _other_arm(label_a_arm)
    return preference


def _blind_session(identifier: str, reviewer: str, plan_sha: str, assignment: _BlindAssignment, digest: str) -> RegionalBlindSession:
    def clip(label: RegionalBlindLabel, checksum: str) -> RegionalBlindClip:
        return RegionalBlindClip(label=label, output_sha256=checksum,
            audio_url=f'/api/music/regional-datasets/comparisons/{identifier}/blind/{reviewer}/audio/{label}')
    clips = [clip('a', assignment.label_a_sha256), clip('b', assignment.label_b_sha256)]
    return RegionalBlindSession(comparison_id=identifier, comparison_sha256=plan_sha, reviewer_reference=reviewer,
        assignment_sha256=digest, clips=clips)


def _load_assignment(identifier: str, reviewer: str, plan_sha: str, checksums: dict[RegionalComparisonArmId, str]) -> tuple[_BlindAssignment, str]:
    row = db.get_db().execute('SELECT assignment_sha256,body_json FROM regional_blind_assignments WHERE comparison_id=? AND reviewer_reference=?', (identifier, reviewer)).fetchone()
    if row is None:
        raise RegionalDatasetError('regional_dataset_not_found', 404)
    assignment = _BlindAssignment.model_validate_json(row['body_json'])
    other = _other_arm(assignment.label_a_arm)
    if (store.canonical_sha256(assignment) != row['assignment_sha256'] or assignment.comparison_sha256 != plan_sha
            or assignment.label_a_sha256 != checksums[assignment.label_a_arm] or assignment.label_b_sha256 != checksums[other]):
        raise RegionalDatasetError('regional_dataset_source_changed')
    return assignment, row['assignment_sha256']


async def open_blind_session(identifier: str, reviewer_reference: str) -> RegionalBlindSession:
    async with _reading_files(), _lock:
        state = get(identifier)
        stored = {item.arm: item.output_sha256 for item in state.arms}
        if stored['base'] is None or stored['adapter'] is None:
            raise RegionalDatasetError('regional_dataset_busy')
        checksums: dict[RegionalComparisonArmId, str] = {'base': stored['base'], 'adapter': stored['adapter']}
        await audio(identifier, 'base')
        await audio(identifier, 'adapter')
        connection = db.get_db()
        existing = connection.execute('SELECT 1 FROM regional_blind_assignments WHERE comparison_id=? AND reviewer_reference=?', (identifier, reviewer_reference)).fetchone()
        if existing is None:
            label_a: RegionalComparisonArmId = 'base' if secrets.choice(('base', 'adapter')) == 'base' else 'adapter'
            assignment = _BlindAssignment(comparison_sha256=state.plan.sha256, label_a_arm=label_a,
                label_a_sha256=checksums[label_a], label_b_sha256=checksums[_other_arm(label_a)])
            digest = store.canonical_sha256(assignment)
            connection.execute('INSERT INTO regional_blind_assignments(comparison_id,reviewer_reference,assignment_sha256,body_json) VALUES(?,?,?,?)',
                (identifier, reviewer_reference, digest, assignment.model_dump_json()))
            connection.commit()
        else:
            assignment, digest = _load_assignment(identifier, reviewer_reference, state.plan.sha256, checksums)
        return _blind_session(identifier, reviewer_reference, state.plan.sha256, assignment, digest)


async def blind_audio(identifier: str, reviewer_reference: str, label: RegionalBlindLabel) -> Path:
    state = get(identifier)
    stored = {item.arm: item.output_sha256 for item in state.arms}
    if stored['base'] is None or stored['adapter'] is None:
        raise RegionalDatasetError('regional_dataset_source_changed')
    checksums: dict[RegionalComparisonArmId, str] = {'base': stored['base'], 'adapter': stored['adapter']}
    assignment, _digest = _load_assignment(identifier, reviewer_reference, state.plan.sha256, checksums)
    arm = assignment.label_a_arm if label == 'a' else _other_arm(assignment.label_a_arm)
    return await audio(identifier, arm)


def _reveal(identifier: str, reviewer: str, record: _BlindReviewRecord, count: int) -> RegionalBlindReveal:
    return RegionalBlindReveal(comparison_id=identifier, reviewer_reference=reviewer, label_a_arm=record.label_a_arm,
        label_b_arm=_other_arm(record.label_a_arm), decoded_preference=record.decoded_preference,
        blind_reviews=count)


async def submit_blind_review(identifier: str, body: RegionalBlindReview) -> RegionalBlindReveal:
    async with _reading_files(), _lock:
        state = get(identifier)
        stored = {item.arm: item.output_sha256 for item in state.arms}
        if stored['base'] is None or stored['adapter'] is None:
            raise RegionalDatasetError('regional_dataset_source_changed')
        checksums: dict[RegionalComparisonArmId, str] = {'base': stored['base'], 'adapter': stored['adapter']}
        assignment, digest = _load_assignment(identifier, body.reviewer_reference, state.plan.sha256, checksums)
        if (body.comparison_sha256 != state.plan.sha256 or body.assignment_sha256 != digest
                or body.label_a_sha256 != assignment.label_a_sha256 or body.label_b_sha256 != assignment.label_b_sha256):
            raise RegionalDatasetError('regional_dataset_source_changed')
        await blind_audio(identifier, body.reviewer_reference, 'a')
        await blind_audio(identifier, body.reviewer_reference, 'b')
        decoded = _decoded_preference(body.preference, assignment.label_a_arm)
        record = _BlindReviewRecord(review=body, label_a_arm=assignment.label_a_arm, decoded_preference=decoded)
        connection = db.get_db()
        old = connection.execute('SELECT body_json FROM regional_blind_reviews WHERE comparison_id=? AND reviewer_reference=?', (identifier, body.reviewer_reference)).fetchone()
        if old is not None:
            if _BlindReviewRecord.model_validate_json(old['body_json']) != record:
                raise RegionalDatasetError('regional_dataset_revision_conflict')
            count = connection.execute('SELECT COUNT(*) FROM regional_blind_reviews WHERE comparison_id=?', (identifier,)).fetchone()[0]
            return _reveal(identifier, body.reviewer_reference, record, count)
        count = connection.execute('SELECT COUNT(*) FROM regional_blind_reviews WHERE comparison_id=?', (identifier,)).fetchone()[0]
        if count >= 10000:
            raise RegionalDatasetError('regional_dataset_limit', 422)
        connection.execute('INSERT INTO regional_blind_reviews(comparison_id,reviewer_reference,body_json) VALUES(?,?,?)',
            (identifier, body.reviewer_reference, record.model_dump_json()))
        connection.commit()
        return _reveal(identifier, body.reviewer_reference, record, count + 1)


def _card_attribution(notices: list[RegionalAttributionNotice], seen: set[tuple[str, str]], source: Literal['rights_record', 'listening_review', 'publisher_declaration'], text: str) -> None:
    cleaned = text.strip()
    if not cleaned or (source, cleaned) in seen:
        return
    seen.add((source, cleaned))
    notices.append(RegionalAttributionNotice(source=source, text=cleaned))


def _card_attributions(identifier: str, manifest: RegionalDatasetManifest, declarations: list[str]) -> list[RegionalAttributionNotice]:
    notices: list[RegionalAttributionNotice] = []
    seen: set[tuple[str, str]] = set()
    try:
        for recording in sorted(manifest.recordings, key=lambda item: item.review.id):
            for rights in sorted(recording.review.rights, key=lambda item: item.id):
                _card_attribution(notices, seen, 'rights_record', rights.public_attribution)
        connection = db.get_db()
        for row in connection.execute('SELECT body_json FROM regional_listening_reviews WHERE comparison_id=? ORDER BY reviewer_reference', (identifier,)):
            review = RegionalListeningReview.model_validate_json(row['body_json'])
            if review.attribution_consented:
                _card_attribution(notices, seen, 'listening_review', review.public_attribution)
        for row in connection.execute('SELECT body_json FROM regional_blind_reviews WHERE comparison_id=? ORDER BY reviewer_reference', (identifier,)):
            record = _BlindReviewRecord.model_validate_json(row['body_json'])
            if record.review.attribution_consented:
                _card_attribution(notices, seen, 'listening_review', record.review.public_attribution)
        for text in declarations:
            _card_attribution(notices, seen, 'publisher_declaration', text)
    except ValidationError:
        raise RegionalDatasetError('regional_dataset_storage_unavailable', 503) from None
    return notices


def _card_hashes(manifest: RegionalDatasetManifest, outputs: dict[RegionalComparisonArmId, str | None]) -> list[RegionalHashNotice]:
    notices: list[RegionalHashNotice] = []
    seen: set[tuple[str, str]] = set()

    def add(role: Literal['source_recording', 'rights_evidence', 'prepared_excerpt', 'comparison_output'], checksum: str) -> None:
        if (role, checksum) in seen:
            return
        seen.add((role, checksum))
        notices.append(RegionalHashNotice(role=role, sha256=checksum))

    for recording in sorted(manifest.recordings, key=lambda item: item.review.id):
        add('source_recording', recording.review.source_sha256)
        for rights in sorted(recording.review.rights, key=lambda item: item.id):
            add('rights_evidence', rights.evidence_sha256)
    for excerpt in sorted(manifest.excerpts, key=lambda item: item.id):
        add('prepared_excerpt', excerpt.sha256)
    for arm in ('base', 'adapter'):
        checksum = outputs[arm]
        if checksum is not None:
            add('comparison_output', checksum)
    return notices


async def model_card(identifier: str) -> RegionalModelCard:
    async with _reading_files(), _lock:
        state = get(identifier)
        spec = state.plan.spec
        prepared = store.get_manifest(db.get_db(), spec.request.manifest_id)
        if prepared.sha256 != spec.request.manifest_sha256:
            raise RegionalDatasetError('regional_dataset_source_changed')
        manifest = prepared.manifest
        if {item.id for item in manifest.excerpts if item.split == 'holdout'} != set(spec.held_out_excerpt_ids):
            raise RegionalDatasetError('regional_dataset_source_changed')
        outputs: dict[RegionalComparisonArmId, str | None] = {}
        for arm in state.arms:
            if arm.output_sha256 is None:
                outputs[arm.arm] = None
                continue
            await audio(identifier, arm.arm)
            outputs[arm.arm] = arm.output_sha256
        if set(outputs) != {'base', 'adapter'}:
            raise RegionalDatasetError('regional_dataset_storage_unavailable', 503)
        training = [item for item in manifest.split.recordings if item.split == 'train']
        if spec.adapter.target == 'small-music':
            adapter_target: Literal['small-music', 'medium'] = 'small-music'
        elif spec.adapter.target == 'medium':
            adapter_target = 'medium'
        else:
            raise RegionalDatasetError('regional_dataset_source_changed')
        return RegionalModelCard(comparison_id=identifier, comparison_sha256=state.plan.sha256, notices=regional_model_card_notices(),
            tradition=manifest.tradition, manifest_id=manifest.id, manifest_sha256=prepared.sha256,
            training_recordings=manifest.split.training_recordings, holdout_recordings=manifest.split.holdout_recordings,
            excluded_recordings=sum(item.split == 'excluded' for item in manifest.split.recordings),
            training_excerpts=sum(item.split == 'train' for item in manifest.excerpts), held_out_excerpts=len(spec.held_out_excerpt_ids),
            declared_adapter_distribution_permitted=bool(training) and all(item.adapter_distribution_permitted for item in training),
            declared_source_redistribution_permitted=bool(training) and all(item.source_redistribution_permitted for item in training),
            adapter_id=spec.adapter.id, adapter_name=spec.adapter.name, adapter_sha256=spec.adapter.sha256, adapter_target=adapter_target,
            training_rank=spec.training_request.rank, training_max_steps=spec.training_request.max_steps,
            training_learning_rate=spec.training_request.learning_rate, training_seed=spec.training_request.seed,
            training_request_sha256=spec.training_receipt.request_sha256, training_dataset_sha256=spec.training_receipt.dataset_sha256,
            training_identity_status=spec.training_receipt.identity_status, runtime=spec.training_receipt.runtime, prompt=spec.request.request.prompt,
            comparison_seed=spec.request.request.seed, duration_seconds=spec.request.request.duration_seconds,
            base_output_sha256=outputs['base'], adapter_output_sha256=outputs['adapter'],
            listener_reviews=state.listener_reviews, blind_reviews=state.blind_reviews,
            attributions=_card_attributions(identifier, manifest, list(spec.publisher_declarations.public_attributions)),
            hashes=_card_hashes(manifest, outputs))


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
