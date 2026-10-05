"""Fixed-prompt held-out comparison drafts; likeness remains a human review."""
from __future__ import annotations
import shutil
import asyncio
import logging
import threading
import uuid
from pathlib import Path
from PIL import Image
from . import video_character_training as training, video_projects as store
from .video_contracts import VideoCharacterComparison, ReviewCharacterAdapterRequest, VideoCharacterTrainingJob, VideoProject, VideoProjectShot, VideoReference, VideoVariant

_LOCK = threading.RLock()
_REVIEWS: dict[str, asyncio.Task[VideoCharacterTrainingJob]] = {}
_STOPPING = False
_LOG = logging.getLogger(__name__)


def work_busy() -> bool:
    return bool(_REVIEWS)


async def recover() -> None:
    global _STOPPING
    _STOPPING = False


async def shutdown() -> None:
    global _STOPPING
    from .job_lifecycle import await_cleanup
    _STOPPING = True
    tasks = list(_REVIEWS.values())
    for task in tasks:
        if not task.done() and not task.cancelling():
            task.cancel()
    await await_cleanup(asyncio.gather(*tasks, return_exceptions=True))


async def review(job_id: str, body: ReviewCharacterAdapterRequest) -> VideoCharacterTrainingJob:
    from .job_lifecycle import await_cleanup
    from .resource_admission import admission_lock, require_setup_idle
    async with admission_lock:
        require_setup_idle()
        if _STOPPING or _REVIEWS:
            raise store.VideoProjectError('busy')
        task = asyncio.create_task(_review(job_id, body))
        _REVIEWS[job_id] = task
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if not task.done() and not task.cancelling():
            task.cancel()
        await await_cleanup(asyncio.gather(task, return_exceptions=True))
        raise
    except OSError as exc:
        _LOG.exception('Could not verify character comparison media')
        raise store.VideoProjectError('character_comparison_required') from exc
    finally:
        if task.done() and _REVIEWS.get(job_id) is task:
            del _REVIEWS[job_id]



def create(job_id: str) -> VideoCharacterTrainingJob:
    with _LOCK, store._lock:
        job = training.get_job(job_id)
        provenance = job.provenance
        if provenance is None or training.ready_adapter_file(job_id) is None:
            raise store.VideoProjectError('character_dataset_unreviewed')
        if job.comparison is not None:
            return job
        held_out = next((item for item in provenance.artifacts if item.role == 'held_out' and item.kind == 'photo'), None)
        if held_out is None:
            raise store.VideoProjectError('held_out_required')
        source = training._artifact(job_id, held_out.path)
        if store.file_hash(source) != held_out.sha256:
            raise store.VideoProjectError('source_changed')
        with Image.open(source) as image:
            width, height = image.size
        ids = [uuid.uuid4().hex, uuid.uuid4().hex]
        created: list[Path] = []
        try:
            for index, project_id in enumerate(ids):
                root = store.project_dir(project_id)
                root.mkdir(parents=True, exist_ok=False)
                created.append(root)
                reference_id = uuid.uuid4().hex
                relative = f'references/{reference_id}.png'
                dest = store.artifact(project_id, relative)
                dest.parent.mkdir()
                shutil.copyfile(source, dest)
                project = VideoProject(id=project_id, revision=1, name=f'{job.name[:80]} · {"LoRA" if index else "Still baseline"}',
                    track_title='', duration_sec=len(provenance.comparison_prompts)*4,
                    source_fingerprint='', created_at=store.now(), updated_at=store.now(), character_lock=True,
                    character_adapter_id=job_id if index else None,
                    references=[VideoReference(id=reference_id,name='Held-out reference',bytes=dest.stat().st_size,width=width,height=height,
                        url=f'/api/videos/projects/{project_id}/references/{reference_id}')],
                    shots=[VideoProjectShot(id=uuid.uuid4().hex,start_sec=position*4,seconds=4,prompt=prompt,seed=42+position,
                        reference_id=reference_id,reference_strength=store.CHARACTER_LOCK_STRENGTH) for position,prompt in enumerate(provenance.comparison_prompts)],
                    warnings=['visual_quality_unverified'])
                store.save(store.StoredVideoProject(project=project,reference_paths={reference_id:relative}))
            comparison = VideoCharacterComparison(id=uuid.uuid4().hex, baseline_project_id=ids[0], adapted_project_id=ids[1],
                dataset_sha256=provenance.dataset_sha256, held_out_sha256=held_out.sha256,
                prompts=list(provenance.comparison_prompts),seeds=[42+index for index in range(len(provenance.comparison_prompts))],created_at=store.now())
            updated=job.model_copy(update={'comparison':comparison,'updated_at':training.now()})
            training._save(updated)
            return updated
        except BaseException:
            for root in created:
                shutil.rmtree(root)
            raise


async def _review(job_id: str, body: ReviewCharacterAdapterRequest) -> VideoCharacterTrainingJob:
    from . import video_render as render
    with _LOCK, store._lock:
        job = training.get_job(job_id)
        provenance, comparison = job.provenance, job.comparison
        if provenance is None or comparison is None or comparison.id != body.comparison_id or not body.reviewed or not body.notes.strip():
            raise store.VideoProjectError('character_comparison_required')
        documents = [store.load(comparison.baseline_project_id), store.load(comparison.adapted_project_id)]
        baseline, adapted = (document.project for document in documents)
        if provenance.dataset_sha256 != comparison.dataset_sha256 or baseline.settings != adapted.settings or baseline.direction or adapted.direction:
            raise store.VideoProjectError('character_comparison_required')
        if baseline.character_adapter_id is not None or adapted.character_adapter_id != job_id:
            raise store.VideoProjectError('character_comparison_required')
        selected: list[tuple[store.StoredVideoProject, VideoProjectShot, VideoVariant]] = []
        for document in documents:
            project = document.project
            if document.worker is not None or project.job is not None and project.job.status in {'queued','running'}:
                raise store.VideoProjectError('busy')
            if project.mode != 'generated' or len(project.references) != 1 or not project.character_lock or store.file_hash(store.reference_file(project.id, project.references[0].id)) != comparison.held_out_sha256:
                raise store.VideoProjectError('character_comparison_required')
            if not project.file_url or len(project.shots) != len(comparison.prompts) or project.settings.engine_pack != 'ltx23':
                raise store.VideoProjectError('character_comparison_required')
            for index, shot in enumerate(project.shots):
                variant = next((item for item in shot.variants if item.id == shot.approved_variant_id and item.status == 'ready'), None)
                if shot.start_sec != index * 4 or shot.seconds != 4 or shot.reference_id != project.references[0].id or shot.reference_strength != store.CHARACTER_LOCK_STRENGTH:
                    raise store.VideoProjectError('character_comparison_required')
                if variant is None or variant.mode != 'generated' or variant.seed != shot.seed or shot.prompt != comparison.prompts[index] or shot.seed != comparison.seeds[index] or variant.prompt != shot.prompt or variant.settings != project.settings:
                    raise store.VideoProjectError('character_comparison_required')
                selected.append((document, shot, variant))
    # Hashing/decoding owns bounded CPU children through the existing media
    # validator. Never hold a thread RLock over awaits: another request on the
    # same event-loop thread could otherwise re-enter it and mutate underneath.
    media_hashes: list[str] = []
    try:
        engines = {document.project.id: await render._engine_fingerprint(document.project) for document in documents}
        for document, shot, variant in selected:
            engine = engines[document.project.id]
            expected = render.fingerprint(document, shot, variant.seed, engine)
            if variant.engine_fingerprint != engine or not await render._valid_variant(document, shot, variant, expected):
                raise store.VideoProjectError('character_comparison_required')
            media_hashes.append(store.file_hash(render.variant_path(document.project.id, shot.id, variant.id)))
    except store.VideoProjectError as exc:
        raise store.VideoProjectError('character_comparison_required') from exc
    with _LOCK, store._lock:
        current = training.get_job(job_id)
        if current.comparison != comparison or current.provenance != provenance or any(store.load(document.project.id) != document for document in documents):
            raise store.VideoProjectError('revision_conflict')
        # A final content check also catches a changed adapter or reference
        # while decoding. App-generated clips and receipts are immutable.
        for document, shot, variant in selected:
            if render.fingerprint(document, shot, variant.seed, engines[document.project.id]) != variant.fingerprint:
                raise store.VideoProjectError('character_comparison_required')
        updated_provenance = provenance.model_copy(update={'evaluated': True, 'evaluation_notes': body.notes.strip(), 'evaluation_updated_at': training.now()})
        updated_comparison = comparison.model_copy(update={'baseline_revision': baseline.revision, 'adapted_revision': adapted.revision,
            'reviewed_variant_ids': [variant.id for _document, _shot, variant in selected], 'reviewed_media_sha256': media_hashes})
        updated = current.model_copy(update={'provenance': updated_provenance, 'comparison': updated_comparison, 'updated_at': training.now()})
        training._save(updated)
        return updated
