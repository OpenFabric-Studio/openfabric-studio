"""Isolated human guide plus independent accompaniment, on the owned apply path."""
from __future__ import annotations

import asyncio
import importlib.metadata
import shutil
import subprocess
import uuid
from pathlib import Path

from fastapi import HTTPException

from . import artist_production as evidence, db, export_provenance, voice_build
from .artist_production_contracts import (CleanRenderRequest, ModelFileIdentity, ProductionRun,
    UsageArtifact, UsageEventInput, VoiceModelSnapshot)
from .voice_artifacts import ModelArtifact
from .job_lifecycle import run_owned_thread


def _identity(path: Path, role: str) -> ModelFileIdentity:
    return ModelFileIdentity.model_validate({'role': role, 'name': path.name,
        'sha256': export_provenance.digest(path)})


def model_snapshot(voice_id: str, model_id: str) -> VoiceModelSnapshot:
    artifact = voice_build.resolve_model_artifact(voice_build.voice_dir(voice_id), model_id)
    if artifact.checkpoint is None or artifact.config is None:
        raise evidence.ProductionError('pinned_checkpoint_required')
    files = [_identity(artifact.checkpoint, 'checkpoint'), _identity(artifact.config, 'configuration'),
        _identity(artifact.reference, 'voice_reference')]
    source = voice_build.SEED_VC_DIR / 'inference.py'
    if not source.is_file() or source.is_symlink():
        raise evidence.ProductionError('engine_missing')
    files.append(_identity(source, 'engine_source'))
    # These are observable installed candidates, not an attestation of loaded/authentic models.
    cache = voice_build.SEED_VC_DIR / 'checkpoints'
    if cache.is_dir():
        root = cache.resolve()
        for path in sorted(root.rglob('*')):
            if path.is_file() and path.resolve().is_relative_to(root) and path.suffix in {'.pth', '.pt', '.bin', '.safetensors', '.ckpt'}:
                files.append(ModelFileIdentity(role='installed_auxiliary_candidate', name=str(path.relative_to(root))[:300],
                    sha256=export_provenance.digest(path.resolve())))
                if len(files) > 512:
                    raise evidence.ProductionError('dependency_inventory_capacity_exceeded')
    try:
        commit = subprocess.run(['git', '-C', str(voice_build.SEED_VC_DIR), 'rev-parse', 'HEAD'],
            capture_output=True, text=True, timeout=5, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = 'unknown'
    try:
        version = importlib.metadata.version('fastapi')
    except importlib.metadata.PackageNotFoundError:
        version = 'unknown'
    from .voice_artifacts import load_registry
    record = next((item for item in load_registry(voice_build.voice_dir(voice_id)).models if item.id == model_id), None)
    return VoiceModelSnapshot(voice_id=voice_id, model_revision_id=model_id, files=files,
        engine_commit=commit, software_version='OpenFabric artist production v1; FastAPI ' + version,
        diffusion_steps=voice_build.DIFFUSION_STEPS, training_steps=artifact.steps,
        baseline_recorded_sha256=record.base_sha256 or None if record else None,
        selection_fingerprint=record.selection_fingerprint or None if record else None,
        preparation_revision=record.preparation_revision or None if record else None)


def require_run(run: ProductionRun) -> ModelArtifact:
    from . import artists
    from .voice_retention import require_not_frozen
    require_not_frozen(run.request.voice_id)
    artist = artists.get_artist(run.request.artist_id)
    if artist.voice_id != run.request.voice_id:
        raise evidence.ProductionError('artist_voice_mismatch')
    artists.require_authorized(run.request.artist_id, run.request.consent_version_id, 'convert')
    from .singer_ranges import get_profile
    current_range = get_profile(run.request.artist_id)
    if (current_range.id if current_range else None) != (run.range_profile.id if run.range_profile else None):
        raise evidence.ProductionError('singer_range_changed')
    if evidence.get_asset(run.request.guide_asset_id).sha256 == evidence.get_asset(run.request.accompaniment_asset_id).sha256:
        raise evidence.ProductionError('independent_accompaniment_required')
    for identifier, role in ((run.request.guide_asset_id, 'guide_vocal'), (run.request.accompaniment_asset_id, 'accompaniment')):
        asset = evidence.get_asset(identifier)
        if asset.role != role:
            raise evidence.ProductionError('production_source_role_mismatch')
        gate = evidence.validate_graph(identifier)
        if not gate.technical_eligible:
            raise evidence.ProductionError(gate.blockers[0])
    artifact = voice_build.resolve_model_artifact(voice_build.voice_dir(run.request.voice_id), run.model.model_revision_id)
    paths = {'checkpoint': artifact.checkpoint, 'configuration': artifact.config,
        'voice_reference': artifact.reference, 'engine_source': voice_build.SEED_VC_DIR / 'inference.py'}
    for item in run.model.files:
        path = paths.get(item.role) if item.role != 'installed_auxiliary_candidate' else voice_build.SEED_VC_DIR / 'checkpoints' / item.name
        if item.role == 'installed_auxiliary_candidate' and path is not None:
            if not path.resolve().is_relative_to((voice_build.SEED_VC_DIR / 'checkpoints').resolve()):
                raise evidence.ProductionError('model_identity_changed')
            path = path.resolve()
        if path is None or not path.is_file() or export_provenance.digest(path) != item.sha256:
            raise evidence.ProductionError('model_identity_changed')
    return artifact


def usage(run: ProductionRun, outcome: str, *, output_id: str | None = None, error_code: str = '',
          recovery: bool = False, supersedes_event_id: str | None = None) -> None:
    checkpoint = next(item for item in run.model.files if item.role == 'checkpoint')
    inputs: list[UsageArtifact] = []
    for identifier in (run.request.guide_asset_id, run.request.accompaniment_asset_id):
        try:
            inputs.append(UsageArtifact(id=identifier, sha256=evidence.get_asset(identifier).sha256))
        except evidence.ProductionError:
            continue
    outputs = [UsageArtifact(id=output_id, sha256=evidence.get_asset(output_id).sha256)] if output_id else []
    event = UsageEventInput.model_validate({'idempotency_key': f'production:{run.id}:' + ('recovery:' if recovery else '') + outcome,
        'operation': 'convert', 'outcome': outcome, 'artist_id': run.request.artist_id,
        'consent_version_id': run.request.consent_version_id, 'project_id': run.project_id,
        'track_id': run.track_id, 'source_graph_id': output_id or run.request.guide_asset_id,
        'voice_model_revision': run.model.model_revision_id, 'voice_model_sha256': checkpoint.sha256,
        'inputs': [item.model_dump() for item in inputs], 'outputs': [item.model_dump() for item in outputs],
        'error_code': error_code, 'supersedes_event_id': supersedes_event_id})
    evidence.append_usage(event)


async def assess_guide(job: voice_build.ApplyJob, run: ProductionRun) -> None:
    from pydantic import TypeAdapter
    from .atomic_files import write_object
    from .contracts import JsonObject
    from .singer_range_contracts import GuideRangeInput, RangeAssessment
    from .singer_ranges import evaluate_range
    if run.range_profile is None:
        run.range_assessment = evaluate_range(run.request.artist_id, None, [], analysis_complete=False,
            source_sha256=evidence.get_asset(run.request.guide_asset_id).sha256, chosen_key=run.request.chosen_key)
    else:
        if job.work_dir is None:
            raise evidence.ProductionError('range_analysis_unavailable')
        manifest = GuideRangeInput(artist_id=run.request.artist_id, profile=run.range_profile,
            guide_path=str(evidence.asset_path(run.request.guide_asset_id)),
            guide_sha256=evidence.get_asset(run.request.guide_asset_id).sha256,
            chosen_key=run.request.chosen_key, approval=run.request.range_approval)
        target = job.work_dir / 'guide-range.json'
        write_object(target, TypeAdapter(JsonObject).validate_python(manifest.model_dump(mode='json')))
        log_name = f'guide_range_{run.id}'
        code = await voice_build._spawn([str(voice_build._engine_python()), '-m', 'app.guide_range', '--manifest', str(target)],
            cwd=Path(__file__).parents[1], log_name=log_name, slot=job.slot, offline=True)
        try:
            if code != 0:
                raise ValueError('analysis failed')
            report = (voice_build.LOG_DIR / f'{log_name}.log').read_text(encoding='utf-8').splitlines()[-1]
            run.range_assessment = RangeAssessment.model_validate_json(report)
        except (OSError, ValueError, IndexError):
            run.range_assessment = evaluate_range(run.request.artist_id, run.range_profile, [], analysis_complete=False,
                source_sha256=manifest.guide_sha256, chosen_key=run.request.chosen_key)
    evidence.save_run(run)
    require_run(run)
    if run.range_assessment.decision != 'allow':
        raise evidence.ProductionError('singer_range_' + run.range_assessment.decision)


async def start_clean_render(request: CleanRenderRequest) -> ProductionRun:
    from . import artists
    from .voice_replacement import _decode
    voice_build.require_ready_voice(request.voice_id)
    artists.require_authorized(request.artist_id, request.consent_version_id, 'convert')
    model = await run_owned_thread(model_snapshot, request.voice_id, request.model_revision_id)
    run = ProductionRun(id=uuid.uuid4().hex, project_id=1, request=request, model=model,
        created_at=evidence.now(), status='queued')
    from .singer_ranges import get_profile
    run.range_profile = get_profile(request.artist_id)
    require_run(run)
    run.project_id = db.insert_project(name=request.title, data={'workflow': 'clean_master', 'artist_id': request.artist_id})
    evidence.save_run(run)
    usage(run, 'requested')
    directory = db.FILES_DIR.resolve() / 'artist-production' / 'renders' / run.id
    placeholder = directory / 'guide-preview.wav'
    try:
        if directory.resolve() != directory:
            raise evidence.ProductionError('invalid_storage')
        directory.mkdir(parents=True)
        duration = await _decode(evidence.asset_path(request.guide_asset_id), placeholder)
        from .voice_replacement import _duration
        backing_duration = await _duration(evidence.asset_path(request.accompaniment_asset_id))
        if abs(backing_duration - duration) > max(.25, .01 * duration):
            raise evidence.ProductionError('accompaniment_duration_mismatch')
        require_run(run)
        track_id = db.insert_track(model='upload', title=request.title, lyrics='', seed=None,
            duration_ms=duration * 1000, wall_ms=None,
            params={'source': 'artist_clean_master', 'production_run_id': run.id, 'voice_id': request.voice_id},
            audio_path=placeholder, abc_path=None)
        run.track_id = track_id
        evidence.save_run(run)
        voice_build.start_apply(request.voice_id, track_id, production_run_id=run.id)
        run = evidence.get_run(run.id)
        return run
    except BaseException as exc:
        run = evidence.get_run(run.id)
        run.status = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'failed'
        run.error_code = exc.code if isinstance(exc, evidence.ProductionError) else 'production_start_failed'
        evidence.save_run(run)
        usage(run, run.status, error_code=run.error_code)
        raise


def converted_master(job: voice_build.ApplyJob, converted: Path, partial_master: Path) -> None:
    if job.production_run_id is None:
        return
    run = evidence.get_run(job.production_run_id)
    require_run(run)
    if job.audio_version_id is None:
        raise evidence.ProductionError('production_version_missing')
    destination = db.FILES_DIR.resolve() / 'artist-production' / 'renders' / run.id
    retained = destination / f'{job.audio_version_id}.converted.wav'
    if retained.resolve() != retained or retained.exists():
        raise evidence.ProductionError('invalid_storage')
    shutil.copyfile(converted, retained)
    vocal = evidence.create_transform(retained, 'voice_converted', [run.request.guide_asset_id], 'seed-vc: pinned model; inherited guide performance')
    # Bind the graph to the final immutable version path after atomic publication.
    run.audio_version_id = job.audio_version_id
    run.status = 'running'
    evidence.save_run(run)
    job.production_vocal_asset_id = vocal.id


def complete_run(job: voice_build.ApplyJob, master: Path) -> None:
    if job.production_run_id is None:
        return
    run = evidence.get_run(job.production_run_id)
    require_run(run)
    if job.production_vocal_asset_id is None:
        raise evidence.ProductionError('converted_vocal_lineage_missing')
    output = evidence.create_transform(master, 'master', [job.production_vocal_asset_id, run.request.accompaniment_asset_id],
        'level-matched converted vocal + independent accompaniment; limiter')
    run.output_asset_id = output.id
    run.status = 'done'
    run.gate = evidence.validate_graph(output.id)
    evidence.save_run(run)
    usage(run, 'done', output_id=output.id)


def record_terminal(job: voice_build.ApplyJob) -> None:
    if job.production_run_id is None:
        return
    run = evidence.get_run(job.production_run_id)
    if job.status in {'failed', 'cancelled'}:
        run.status = 'cancelled' if job.status == 'cancelled' else 'failed'
        run.error_code = job.error_code
        evidence.save_run(run)
        usage(run, run.status, error_code=job.error_code)


def recover() -> None:
    for run in evidence.list_runs():
        if run.output_asset_id is not None and run.audio_version_id is not None and run.track_id is not None:
            from .audio_version_store import get, set_state
            version = get(db.get_db(), run.audio_version_id)
            try:
                output = evidence.get_asset(run.output_asset_id)
                path = evidence.asset_path(output.id)
                if version is None or version.path != path or export_provenance.digest(path) != output.sha256:
                    raise evidence.ProductionError('production_output_changed')
                if version.public.status in {'queued', 'running', 'failed'} and version.public.error_code in {'', 'interrupted'}:
                    set_state(db.get_db(), version.public.id, 'done', '')
                    if version.captured_source is not None:
                        connection = db.get_db()
                        connection.execute('UPDATE tracks SET audio_path=? WHERE id=? AND audio_path=?',
                            (str(path), run.track_id, str(version.captured_source)))
                        connection.commit()
                if run.status in {'queued', 'running', 'done'}:
                    run.status = 'done'
                    run.error_code = ''
                    evidence.save_run(run)
                    usage(run, 'done', output_id=run.output_asset_id)
                continue
            except (OSError, evidence.ProductionError):
                run.status = 'failed'
                run.error_code = 'production_recovery_review_required'
                evidence.save_run(run)
                prior = evidence.usage_event(f'production:{run.id}:done')
                usage(run, 'interrupted', error_code=run.error_code, recovery=True,
                    supersedes_event_id=prior.id if prior is not None else None)
        if run.status in {'queued', 'running'} and (run.track_id is None or not voice_build.application_active(run.track_id)):
            run.status = 'failed'
            run.error_code = 'interrupted'
            evidence.save_run(run)
            usage(run, 'interrupted', error_code='interrupted')
