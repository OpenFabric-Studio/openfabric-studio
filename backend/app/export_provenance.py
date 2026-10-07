"""Hash-bound adjacent manifests using current owned publication directories."""
from __future__ import annotations
import hashlib
from datetime import datetime,timezone
from pathlib import Path
from typing import Literal, TYPE_CHECKING
from pydantic import TypeAdapter, ValidationError
from .contracts import JsonObject
from .export_provenance_contracts import ContentOrigin, ExportProvenance, ProvenanceComponent
from .audio_quality_contracts import AudioMetrics,LoudnessSettings,TargetResult
from .video_projects import atomic_text
if TYPE_CHECKING:
    from .video_projects import StoredVideoProject


MAX_MANIFEST_BYTES=8*1024*1024


class ProvenanceError(Exception):
    def __init__(self,code: str) -> None:
        self.code=code;super().__init__(code)


def digest(path: Path) -> str:
    if path.is_symlink() or not path.is_file():raise ProvenanceError('manifest_unavailable')
    checksum=hashlib.sha256()
    with path.open('rb') as source:
        while block:=source.read(1024*1024):checksum.update(block)
    return checksum.hexdigest()


def origin(components: list[ProvenanceComponent]) -> ContentOrigin:
    kinds={item.content_origin for item in components if item.role!='conditioning_reference'}
    if len(kinds)==1:return next(iter(kinds))
    if 'generated' in kinds or 'mixed' in kinds:return 'mixed'
    return 'unknown'


def manifest(export_id: str,subject: Literal['track_audio','stem','speech_trial','audiobook','video'],
    media: Path,components: list[ProvenanceComponent],*,transformations: list[str] | None=None,
    measured_audio: AudioMetrics | None=None,visible_ai_label: bool=False,
    audio_target: LoudnessSettings | None=None,audio_target_result: TargetResult | None=None) -> ExportProvenance:
    return ExportProvenance(export_id=export_id,subject=subject,created_at=datetime.now(timezone.utc).isoformat(),
        artifact_sha256=digest(media),content_origin=origin(components),components=components,
        classification_basis='contains_user_declaration' if any(item.classification_basis=='user_declared' for item in components) else 'app_workflow',
        transformations=transformations or [],measured_audio=measured_audio,visible_ai_label=visible_ai_label,
        audio_target=audio_target,audio_target_result=audio_target_result)


def text(value: ExportProvenance) -> str:
    lines=['OpenFabric export provenance',f'Export: {value.export_id}',f'Content origin: {value.content_origin}',
        f'Created: {value.created_at}',f'Media SHA-256: {value.artifact_sha256}',
        'Classification describes app workflow records; it is not proof of ownership, a signature or certification.','', 'Sources:']
    for item in value.components:
        lines.append(f'- {item.role}: {item.content_origin}; source={item.source_id}; SHA-256={item.source_sha256}')
        if item.engine:lines.append(f'  Engine: {item.engine}; model={item.model_id or "unknown"}; identity={item.engine_fingerprint or "unknown"}')
        if item.provider_receipt_id:lines.append(f'  Provider receipt: {item.provider_receipt_id}; job={item.provider_job_id or "unavailable"}')
    lines.extend(['','Processing:',*(f'- {step}' for step in value.transformations)])
    if value.audio_target is not None and value.audio_target_result is not None:
        lines.append(f'Audio target: {value.audio_target.profile}; LUFS/dBTP={value.audio_target.target}; assessment={value.audio_target_result}')
    if value.measured_audio:
        metrics=value.measured_audio
        lines.append(f'Measured audio: LUFS={metrics.integrated_lufs}; true peak dBTP={metrics.true_peak_dbtp}; sample peak dBFS={metrics.sample_peak_dbfs}')
    return '\n'.join(lines)+'\n'


def path_for(media: Path,format: Literal['json','txt']) -> Path:
    return media.with_name(media.name+f'.provenance.{format}')


def write(media: Path,value: ExportProvenance) -> None:
    if digest(media)!=value.artifact_sha256:raise ProvenanceError('manifest_media_changed')
    documents=((path_for(media,'json'),value.model_dump_json(indent=2)+'\n'),(path_for(media,'txt'),text(value)))
    if any(len(body.encode('utf-8'))>MAX_MANIFEST_BYTES for _,body in documents):
        raise ProvenanceError('manifest_capacity_exceeded')
    for target,body in documents:
        if target.is_symlink():raise ProvenanceError('manifest_unavailable')
        atomic_text(target,body)


def read(media: Path) -> ExportProvenance:
    target=path_for(media,'json')
    try:
        if target.is_symlink() or target.stat().st_size>MAX_MANIFEST_BYTES:raise ValueError('invalid manifest')
        value=ExportProvenance.model_validate_json(target.read_bytes())
    except (OSError,ValidationError,ValueError):raise ProvenanceError('manifest_unavailable') from None
    if digest(media)!=value.artifact_sha256:raise ProvenanceError('manifest_media_changed')
    return value


def metadata_args(export_id: str,content_origin: ContentOrigin,previous_comment: str='') -> list[str]:
    note=f'OpenFabric provenance v1; export={export_id}; content_origin={content_origin}; informative app workflow record'
    return ['-metadata',f'OPENFABRIC_CONTENT_ORIGIN={content_origin}','-metadata',f'OPENFABRIC_EXPORT_ID={export_id}',
        '-metadata',f'comment={previous_comment+"; " if previous_comment else ""}{note}']


def track_component(track_id: int,version_id: str,source_sha256: str,*,is_voice: bool=False) -> ProvenanceComponent:
    from . import db
    row=db.get_track(track_id)
    raw_model: object=row['model'] if row is not None and 'model' in row.keys() else None
    model=raw_model if isinstance(raw_model,str) else 'upload'
    params: JsonObject={}
    if row is not None and 'params_json' in row.keys():
        raw_params: object=row['params_json']
        if isinstance(raw_params,str):
            try:params=TypeAdapter(JsonObject).validate_json(raw_params)
            except ValidationError:pass
    content: ContentOrigin='generated' if model in {'ace_step','yue2','openrouter'} else 'unknown'
    if row is not None and model=='editor':
        ancestors=params.get('source_track_ids')
        if isinstance(ancestors,list) and 0<len(ancestors)<=100:
            kinds: set[ContentOrigin]={'unknown'} if params.get('source_ancestry_complete') is False else set()
            for identifier in ancestors:
                if not isinstance(identifier,int) or isinstance(identifier,bool) or identifier==track_id:
                    kinds.add('unknown');continue
                parent=db.get_track(identifier)
                raw_parent: object=parent['model'] if parent is not None and 'model' in parent.keys() else None
                parent_model=raw_parent if isinstance(raw_parent,str) else ''
                kinds.add('generated' if parent_model in {'ace_step','yue2','openrouter'} else 'unknown')
            if kinds=={'generated'}:content='generated'
            elif 'generated' in kinds:content='mixed'
    if is_voice:content='generated' if content=='generated' else 'mixed'
    component=ProvenanceComponent(role='audio',content_origin=content,source_id=f'track:{track_id}:version:{version_id}',
        source_sha256=source_sha256,engine='seed-vc' if is_voice else model if content!='unknown' else None)
    if content=='unknown':component.classification_basis='unverified'
    if row is not None and model=='openrouter':
        from .openrouter_contracts import OpenRouterReceipt
        receipt=params.get('receipt')
        if isinstance(receipt,dict):
            try:parsed=OpenRouterReceipt.model_validate(receipt)
            except ValidationError:pass
            else:
                component.model_id=parsed.model_id;component.provider_receipt_id=parsed.id
                component.provider_job_id=parsed.remote_id
    return component


def video_components(document: 'StoredVideoProject',*,attach_speech: bool) -> list[ProvenanceComponent]:
    from . import video_render as render, video_projects as store
    project=document.project;components: list[ProvenanceComponent]=[]
    for shot in project.shots:
        selected=next((item for item in shot.variants if item.id==shot.approved_variant_id and item.status=='ready'),None)
        if selected is None:raise ProvenanceError('manifest_unavailable')
        component=ProvenanceComponent(role='video',content_origin='generated' if selected.mode=='generated' else 'unknown',
            source_id=f'shot:{shot.id}:variant:{selected.id}',source_sha256=digest(render.variant_path(project.id,shot.id,selected.id)),
            engine=selected.provider_config.provider if selected.mode=='generated' else None,engine_fingerprint=selected.engine_fingerprint or None)
        if selected.cloud is not None:
            component.model_id=selected.cloud.receipt.model_id;component.provider_receipt_id=selected.cloud.receipt.id
            component.provider_job_id=selected.cloud.receipt.remote_id
        elif selected.mode=='generated':component.model_id=selected.settings.engine_pack
        components.append(component)
    if project.track_id is not None and document.source is not None:
        components.append(track_component(project.track_id,'original',document.source.sha256))
    elif project.speech_clip is not None and attach_speech:
        if document.retained_audio is not None:
            from . import retained_audio
            store.require_retained_consent(document)
            components.extend(retained_audio.video_components(document.retained_audio, project.speech_clip.sha256))
        elif project.dialogue_cues:
            for cue in project.dialogue_cues:
                component=ProvenanceComponent(role='audio',content_origin='generated' if cue.renderer is not None else 'unknown',source_id=f'passage:{cue.passage_id}:take:{cue.render_identity}',
                    source_sha256=cue.audio_sha256,hash_scope='pcm',engine=cue.renderer,
                    model_id=cue.cloud_provenance.model if cue.cloud_provenance else 'gpt-sovits' if cue.renderer=='local' else None,
                    engine_fingerprint=cue.cloud_provenance.model_fingerprint if cue.cloud_provenance else None,
                    provider_receipt_id=cue.cloud_provenance.receipt_id if cue.cloud_provenance else None,
                    provider_job_id=cue.cloud_provenance.generation_id if cue.cloud_provenance else None)
                components.append(component)
        else:
            clip=project.speech_clip
            components.append(ProvenanceComponent(role='audio',content_origin='generated' if clip.kind=='voice' else 'unknown',
                source_id=f'speech:{clip.id}',source_sha256=clip.sha256,engine='gpt-sovits' if clip.kind=='voice' else None))
    for reference in project.references:
        components.append(ProvenanceComponent(role='conditioning_reference',content_origin='unknown',classification_basis='unverified',
            source_id=f'reference:{reference.id}',source_sha256=digest(store.reference_file(project.id,reference.id))))
    return components
