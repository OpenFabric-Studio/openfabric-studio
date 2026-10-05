"""One approved quote creates one durable silent cloud shot; recovery only polls."""
from __future__ import annotations
import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from pathlib import Path
import uuid
from . import openrouter_catalog as catalog, openrouter_requests as ledger, video_projects as store, video_render as render
from .openrouter_client import OpenRouterClient
from .openrouter_contracts import OpenRouterReceipt, OpenRouterVideoRequest
from .openrouter_errors import OpenRouterError
from .resource_admission import admission_lock, require_setup_idle
from .video_cloud_media import conform
from .video_contracts import VideoCloudProvenance, VideoCloudQuoteRequest, VideoCloudQuoteResponse, VideoCloudSubmitRequest, VideoCloudResumeRequest, VideoProject, VideoProjectShot, VideoProjectJob, VideoVariant
from .video_io import hash_file
from .video_media import VideoMediaError, tool

logger=logging.getLogger(__name__)
POLL_INTERVAL=3.0


@dataclass(frozen=True)
class _QuoteBinding:
    project_id: str
    revision: int
    shot_id: str
    remote_duration_sec: int
    expires_at: float


_QUOTES: dict[str, _QuoteBinding] = {}


def _context(project_id: str, body: VideoCloudQuoteRequest) -> tuple[store.StoredVideoProject,VideoProjectShot,OpenRouterVideoRequest]:
    store.ensure_open(project_id)
    document=store.load(project_id); project=document.project
    if body.revision!=project.revision: raise store.VideoProjectError('revision_conflict')
    if project_id in render._tasks or project_id in render._cancelling or document.worker is not None or project.job is not None and project.job.status in {'queued','running'}:
        raise store.VideoProjectError('busy')
    if store.source_changed(document): raise store.VideoProjectError('source_changed')
    if project.provider_config.provider!='openrouter': raise store.VideoProjectError('cloud_provider_required')
    if project.character_adapter_id: raise store.VideoProjectError('cloud_lora_unsupported')
    if project.mode!='generated': raise store.VideoProjectError('cloud_mode_unsupported')
    from .video_dialogue import require_consent
    require_consent(project)
    store.ensure_character_lock(project)
    shot=next((item for item in project.shots if item.id==body.shot_id),None)
    if shot is None: raise store.VideoProjectError('shot_not_found')
    if body.remote_duration_sec < shot.seconds: raise store.VideoProjectError('cloud_duration_unsupported')
    model=catalog.require_model(project.provider_config.model_id,'video')
    reference_id=render._still_id(project,shot)
    image: str | None=None
    if reference_id is not None:
        path=store.reference_file(project_id,reference_id)
        if path.is_symlink() or not path.is_file() or path.stat().st_size>15*1024*1024:
            raise store.VideoProjectError('reference_too_large')
        data=path.read_bytes()
        if not data.startswith(b'\x89PNG\r\n\x1a\n'): raise store.VideoProjectError('invalid_reference')
        image='data:image/png;base64,'+base64.b64encode(data).decode('ascii')
    prompt=render._prompt(project,shot)
    if not 1<=len(prompt)<=4000: raise store.VideoProjectError('bad_prompt')
    request=OpenRouterVideoRequest(model=model.id,prompt=prompt,duration=body.remote_duration_sec,
        size=project.provider_config.size,generate_audio=False,seed=shot.seed if model.supports_seed else None,reference_image=image)
    return document,shot,request


def quote(project_id: str, body: VideoCloudQuoteRequest) -> VideoCloudQuoteResponse:
    with store._lock:
        document,shot,request=_context(project_id,body)
        result=catalog.quote_video(request)
        for identifier, binding in tuple(_QUOTES.items()):
            if binding.expires_at < time.time(): del _QUOTES[identifier]
        if len(_QUOTES) >= 1000: raise OpenRouterError('quote_limit',429)
        _QUOTES[result.id]=_QuoteBinding(project_id,body.revision,shot.id,request.duration,result.expires_at)
        return VideoCloudQuoteResponse(project_id=project_id,revision=document.project.revision,shot_id=shot.id,
            remote_duration_sec=request.duration,slot_duration_sec=shot.seconds,trim_required=request.duration!=shot.seconds,quote=result)


def _sync(project_id: str, shot_id: str, variant_id: str, receipt: OpenRouterReceipt) -> None:
    def change(variant: VideoVariant) -> None:
        if variant.cloud is None or variant.cloud.receipt.id!=receipt.id: raise store.VideoProjectError('cloud_request_mismatch')
        variant.cloud.receipt=receipt
    render._set_variant(project_id,shot_id,variant_id,change)


async def submit(project_id: str, body: VideoCloudSubmitRequest) -> VideoProject:
    async with admission_lock:
        require_setup_idle()
        if render._cloud_projects: raise store.VideoProjectError('busy')
        if not body.transfers_confirmed: raise store.VideoProjectError('cloud_transfer_confirmation_required')
        with store._lock:
            document,shot,request=_context(project_id,body)
            if request.duration!=shot.seconds and not body.trim_confirmed: raise store.VideoProjectError('cloud_trim_confirmation_required')
            tool('ffmpeg');tool('ffprobe')
            binding=_QUOTES.get(body.quote_id)
            if binding is None or (binding.project_id,binding.revision,binding.shot_id,binding.remote_duration_sec)!=(project_id,body.revision,shot.id,request.duration):
                raise OpenRouterError('quote_mismatch',409)
            approved=catalog.validate_quote(body.quote_id,catalog.video_parameters(request))
            variant_id=uuid.uuid4().hex
            receipt=ledger.prepare(f'video-{project_id}-{shot.id}-{variant_id}',approved)
            engine=await render._engine_fingerprint(document.project)
            variant=VideoVariant(id=variant_id,seed=shot.seed,status='queued',created_at=store.now(),prompt=request.prompt,
                settings=document.project.settings,provider_config=document.project.provider_config,engine_fingerprint=engine,
                source_fingerprint=document.source.sha256 if document.source is not None else '',reference_id=render._still_id(document.project,shot),
                fingerprint=render.fingerprint(document,shot,shot.seed,engine),
                cloud=VideoCloudProvenance(receipt=receipt,remote_duration_sec=request.duration,slot_duration_sec=shot.seconds,
                    trim_confirmed=body.trim_confirmed,requested_seed=request.seed))
            def reserve(saved: store.StoredVideoProject) -> None:
                target=next(item for item in saved.project.shots if item.id==shot.id)
                target.variants.append(variant)
                saved.project.job=VideoProjectJob(id=uuid.uuid4().hex,operation='preview',status='queued',shot_ids=[shot.id],shot_count=1)
                saved.render_request=None; saved.pending_export=None; saved.cloud_variant_id=variant_id
            result=store.mutate(project_id,reserve,revision=body.revision)
        # No await between durable intent publication and task ownership.
        render._cloud_projects.add(project_id)
        render._tasks[project_id]=asyncio.create_task(_run(project_id,shot.id,variant_id,request=request))
        return result


def _variant(project_id: str, variant_id: str) -> tuple[store.StoredVideoProject,VideoProjectShot,VideoVariant]:
    document=store.load(project_id)
    for shot in document.project.shots:
        for variant in shot.variants:
            if variant.id==variant_id and variant.cloud is not None: return document,shot,variant
    raise store.VideoProjectError('variant_not_found')


def _owned_receipt(document: store.StoredVideoProject, shot: VideoProjectShot, variant: VideoVariant) -> OpenRouterReceipt:
    if variant.cloud is None or variant.provider_config.provider != 'openrouter': raise store.VideoProjectError('cloud_request_mismatch')
    receipt=ledger.get(variant.cloud.receipt.id)
    expected=f'video-{document.project.id}-{shot.id}-{variant.id}'
    if receipt.owner_id!=expected or receipt.kind!='video' or receipt.model_id!=variant.provider_config.model_id or receipt.quote_id!=variant.cloud.receipt.quote_id or receipt.request_fingerprint!=variant.cloud.receipt.request_fingerprint:
        raise store.VideoProjectError('cloud_request_mismatch')
    return receipt


async def resume(project_id: str, body: VideoCloudResumeRequest) -> VideoProject:
    async with admission_lock:
        require_setup_idle();store.ensure_open(project_id)
        document,shot,variant=_variant(project_id,body.variant_id)
        if document.project.revision!=body.revision: raise store.VideoProjectError('revision_conflict')
        if project_id in render._tasks or document.worker is not None: raise store.VideoProjectError('busy')
        if variant.cloud is None: raise store.VideoProjectError('variant_not_found')
        receipt=_owned_receipt(document,shot,variant)
        if receipt.remote_id is None: raise store.VideoProjectError('cloud_submission_unknown')
        if receipt.state not in {'submitted','canceled_tracking','completed'} or variant.status=='ready': raise store.VideoProjectError('nothing_to_resume')
        engine=await render._engine_fingerprint(document.project)
        if variant.fingerprint!=render.fingerprint(document,shot,variant.seed,engine): raise store.VideoProjectError('stale_variant')
        from .video_dialogue import require_consent
        require_consent(document.project)
        if receipt.state=='canceled_tracking': receipt=ledger.update(receipt.id,state='submitted',remote_id=receipt.remote_id)
        def reserve(saved: store.StoredVideoProject) -> None:
            current=next(item for item in saved.project.shots if item.id==shot.id)
            target=next(item for item in current.variants if item.id==variant.id)
            target.status='queued';target.error_code='';target.cloud=variant.cloud.model_copy(update={'receipt':receipt}) if variant.cloud is not None else None
            saved.project.job=VideoProjectJob(id=uuid.uuid4().hex,operation='preview',status='queued',shot_ids=[shot.id],shot_count=1)
            saved.cloud_variant_id=variant.id
        result=store.mutate(project_id,reserve,revision=body.revision)
        render._cloud_projects.add(project_id)
        render._tasks[project_id]=asyncio.create_task(_run(project_id,shot.id,variant.id))
        return result


async def _run(project_id: str, shot_id: str, variant_id: str, *, request: OpenRouterVideoRequest | None=None) -> None:
    raw: Path | None=None;partial: Path | None=None
    try:
        document,shot,variant=_variant(project_id,variant_id)
        if variant.cloud is None: raise store.VideoProjectError('cloud_request_mismatch')
        cloud=variant.cloud;receipt=_owned_receipt(document,shot,variant);client=OpenRouterClient()
        def running(saved: store.StoredVideoProject) -> None:
            if saved.project.job is not None:
                saved.project.job.status='running';saved.project.job.started_at=store.now()
            current=next(item for item in saved.project.shots if item.id==shot_id)
            target=next(item for item in current.variants if item.id==variant_id)
            target.status='running';target.started_at=store.now()
        store.mutate(project_id,running,busy_ok=True,bump=False)
        if request is not None:
            remote=await client.submit_video(receipt.id,request,receipt.quote_id)
            receipt=ledger.get(receipt.id)
            _sync(project_id,shot_id,variant_id,receipt)
        elif receipt.remote_id is not None:
            remote=await client.poll_video(receipt.remote_id)
        else: raise store.VideoProjectError('cloud_submission_unknown')
        async with asyncio.timeout(3600):
            while remote.status not in {'completed','failed','cancelled'}:
                render._job_phase(project_id,'cloud_polling',variant_id,int(remote.progress or 0),100)
                # The first GET follows durable remote ID publication; no POST is retried.
                remote=await client.poll_video(remote.id)
                if remote.status not in {'completed','failed','cancelled'}: await asyncio.sleep(POLL_INTERVAL)
        if remote.status!='completed':
            receipt=ledger.update(receipt.id,state='failed',actual_cost_usd=remote.actual_cost_usd,error_code='provider_video_failed')
            _sync(project_id,shot_id,variant_id,receipt)
            raise store.VideoProjectError('provider_video_failed')
        receipt=ledger.update(receipt.id,state=receipt.state if receipt.state=='completed' else 'submitted',actual_cost_usd=remote.actual_cost_usd)
        _sync(project_id,shot_id,variant_id,receipt)
        document,shot,variant=_variant(project_id,variant_id)
        if document.project.job is None: raise store.VideoProjectError('job_not_found')
        run=store.artifact(project_id,f'runs/{document.project.job.id}');run.mkdir(parents=True,exist_ok=True)
        raw=run/'cloud-source.mp4';partial=run/'cloud.partial.mp4'
        download=await client.download_video(remote.id,raw)
        info=await conform(project_id,raw,partial,source_seconds=cloud.remote_duration_sec,target_seconds=cloud.slot_duration_sec,
            size=document.project.frame_size,trim_confirmed=cloud.trim_confirmed)
        current=store.load(project_id)
        engine=await render._engine_fingerprint(current.project)
        if render.fingerprint(current,shot,variant.seed,engine)!=variant.fingerprint: raise store.VideoProjectError('stale_variant')
        # Persist checked source provenance before the immutable clip rename.
        # A crash during optional poster work can then adopt this local output.
        receipt=ledger.update(receipt.id,state='completed',actual_cost_usd=remote.actual_cost_usd)
        def checked(target: VideoVariant) -> None:
            if target.cloud is None: raise store.VideoProjectError('cloud_request_mismatch')
            target.cloud.receipt=receipt
            target.cloud.received_sha256=download.sha256
            target.cloud.source_duration_sec=info.video_duration
        render._set_variant(project_id,shot_id,variant_id,checked)
        path=render.variant_path(project_id,shot_id,variant_id);path.parent.mkdir(parents=True,exist_ok=True)
        width,height=current.project.frame_size
        media_receipt=render.VariantReceipt(variant_id=variant_id,shot_id=shot_id,fingerprint=variant.fingerprint,
            output_sha256=await hash_file(partial),seconds=cloud.slot_duration_sec,width=width,height=height)
        store.atomic_text(render._receipt_path(path),media_receipt.model_dump_json());partial.replace(path)
        poster=path.with_suffix('.png');strip=path.with_name(path.stem+'.filmstrip.png')
        await render._poster(project_id,path,poster)
        try: await render._filmstrip(project_id,path,strip,shot.seconds)
        except store.VideoProjectError: logger.warning('Optional cloud filmstrip unavailable',exc_info=True)
        receipt=ledger.update(receipt.id,state='completed',actual_cost_usd=remote.actual_cost_usd)
        def publish(saved: store.StoredVideoProject) -> None:
            current_shot=next(item for item in saved.project.shots if item.id==shot_id)
            target=next(item for item in current_shot.variants if item.id==variant_id)
            if target.cloud is None: raise store.VideoProjectError('cloud_request_mismatch')
            target.cloud.receipt=receipt;target.cloud.received_sha256=download.sha256;target.cloud.source_duration_sec=info.video_duration
            target.status='ready';target.duration_sec=shot.seconds;target.finished_at=store.now();target.error_code=''
            prefix=f'/api/videos/projects/{project_id}/shots/{shot_id}/variants/{variant_id}'
            target.file_url=prefix+'/file';target.poster_url=prefix+'/poster'
            target.filmstrip_url=prefix+'/filmstrip' if strip.is_file() else ''
        render._publish_checked(project_id,publish);render._finish(project_id,'ready','')
    except (asyncio.CancelledError,OpenRouterError,store.VideoProjectError,VideoMediaError,TimeoutError,OSError,ValueError) as error:
        code='interrupted' if isinstance(error,asyncio.CancelledError) else error.code if isinstance(error,(OpenRouterError,store.VideoProjectError,VideoMediaError)) else 'cloud_polling_timeout' if isinstance(error,TimeoutError) else 'cloud_processing_failed'
        try:
            _document,_shot,variant=_variant(project_id,variant_id)
            if variant.cloud is not None:
                receipt=ledger.get(variant.cloud.receipt.id);_sync(project_id,shot_id,variant_id,receipt)
                if receipt.state=='canceled_tracking' or variant.status=='cancelled': code='cloud_tracking_stopped'
            def failed(target: VideoVariant) -> None:
                target.status='cancelled' if isinstance(error,asyncio.CancelledError) else 'failed';target.error_code=code;target.finished_at=store.now()
            render._set_variant(project_id,shot_id,variant_id,failed)
            render._finish(project_id,'cancelled' if isinstance(error,asyncio.CancelledError) else 'failed',code)
        except (store.VideoProjectError,OpenRouterError): logger.exception('Could not persist cloud job outcome')
        if not isinstance(error,asyncio.CancelledError): logger.warning('Cloud shot failed (%s)',code)
    finally:
        for staging_path in (partial,raw):
            if staging_path is not None:
                try: staging_path.unlink(missing_ok=True)
                except OSError: logger.warning('Cloud staging cleanup deferred',exc_info=True)
        if render._tasks.get(project_id) is asyncio.current_task():
            render._tasks.pop(project_id,None)
            render._cloud_projects.discard(project_id)


def stop_tracking(project_id: str) -> None:
    document=store.load(project_id)
    for shot in document.project.shots:
        for variant in shot.variants:
            if variant.id==document.cloud_variant_id and variant.cloud is not None and variant.status in {'queued','running'}:
                receipt=ledger.get(variant.cloud.receipt.id)
                if receipt.state in {'submitted','submission_unknown','submitting','canceled_tracking'}:
                    _sync(project_id,shot.id,variant.id,ledger.mark_tracking_canceled(receipt.id))
                def stopped(target: VideoVariant) -> None:
                    target.status='cancelled';target.error_code='cloud_tracking_stopped';target.finished_at=store.now()
                render._set_variant(project_id,shot.id,variant.id,stopped)


async def recover(project_id: str) -> bool:
    """A known remote job can be followed; missing IDs require manual investigation."""
    document=store.load(project_id)
    if document.project.provider_config.provider!='openrouter' or document.project.job is None or document.project.job.operation=='export': return False
    if document.project.job.status not in {'queued','running'} and document.project.job.error_code!='interrupted': return False
    for shot in document.project.shots:
        for variant in reversed(shot.variants):
            if variant.id!=document.cloud_variant_id or variant.cloud is None or variant.status=='ready': continue
            try:
                receipt=_owned_receipt(document,shot,variant);_sync(project_id,shot.id,variant.id,receipt)
            except (OpenRouterError,store.VideoProjectError):
                logger.warning('Cloud recovery receipt is unavailable',exc_info=True)
                render._finish(project_id,'failed','cloud_request_mismatch')
                return True
            if receipt.state=='completed':
                engine=await render._engine_fingerprint(document.project)
                expected=render.fingerprint(document,shot,variant.seed,engine)
                if await render._valid_variant(document,shot,variant,expected):
                    def adopt(saved: store.StoredVideoProject) -> None:
                        target_shot=next(item for item in saved.project.shots if item.id==shot.id)
                        target=next(item for item in target_shot.variants if item.id==variant.id)
                        target.status='ready';target.error_code='';target.duration_sec=shot.seconds;target.finished_at=store.now()
                        prefix=f'/api/videos/projects/{project_id}/shots/{shot.id}/variants/{variant.id}'
                        path=render.variant_path(project_id,shot.id,variant.id)
                        target.file_url=prefix+'/file'
                        target.poster_url=prefix+'/poster' if path.with_suffix('.png').is_file() else ''
                        target.filmstrip_url=prefix+'/filmstrip' if path.with_name(path.stem+'.filmstrip.png').is_file() else ''
                    render._publish_checked(project_id,adopt);render._finish(project_id,'ready','')
                    return True
            if receipt.state in {'submitted','completed'} and receipt.remote_id is not None:
                try:
                    await resume(project_id,VideoCloudResumeRequest(revision=document.project.revision,variant_id=variant.id))
                except (store.VideoProjectError,OpenRouterError): logger.warning('Cloud recovery requires user action',exc_info=True)
                return True
            if variant.status in {'queued','running'}:
                render._finish(project_id,'failed','cloud_submission_unknown' if receipt.state in {'submission_unknown','submitting'} else 'cloud_request_not_submitted')
                render._set_variant(project_id,shot.id,variant.id,lambda item: setattr(item,'status','failed'))
                return True
    return False
