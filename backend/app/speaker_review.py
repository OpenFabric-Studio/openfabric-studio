"""Opt-in speaker screening bound to approved takes, live voices and offline encoder."""
from __future__ import annotations
import asyncio
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime,timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import uuid
import wave
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,TypeAdapter,ValidationError
from . import audiobooks,audiobook_workflows,voice_profiles
from .atomic_files import JsonObject,document_lock,write_object
from .job_lifecycle import await_cleanup,kill_process_tree
from .resource_admission import admission_lock,native_work_inflight,require_setup_idle
from .speech_references import SpeechRenderSnapshot,file_digest
from .speaker_review_contracts import SpeakerEncoderIdentity,SpeakerReviewCapability,SpeakerReferenceCandidate,SpeakerReferencesResponse,SpeakerReviewRequest,SpeakerReview,SpeakerReviewsResponse
from . import speaker_review_environment as environment,speaker_review_worker as protocol
from .video_process import WorkerIdentity,WorkerReceipt,WorkerOutputError,spawn_owned,read_owned_output,terminate_verified

_LOG=logging.getLogger(__name__)
_ID=re.compile(r"^[0-9a-f]{32}$")
_TASKS:dict[str,asyncio.Task[None]]={}
_PROCESSES:dict[str,asyncio.subprocess.Process]={}
_UNVERIFIED:set[str]=set()
_CLEANUP_PENDING:set[str]=set()
_NAMESPACE_UNAVAILABLE=False
_STOPPING=False
State=Literal["queued","running","completed","unavailable","skipped","failed","canceled","stale"]


class SpeakerReviewError(Exception):
    def __init__(self,code:str,status:int=400)->None:self.code,self.status=code,status;super().__init__(code)


def capability(*,python_path:Path|None=None,weights_path:Path|None=None)->SpeakerReviewCapability:
    python=python_path or (Path(value) if (value:=os.environ.get("OPENFABRIC_SPEAKER_REVIEW_PYTHON")) else None)
    weights=weights_path or (Path(value) if (value:=os.environ.get("OPENFABRIC_SPEAKER_REVIEW_WEIGHTS")) else None)
    result=SpeakerReviewCapability(available=False,configured=python is not None and weights is not None,
        setup_hint="Install the optional dedicated CPU environment, then configure OPENFABRIC_SPEAKER_REVIEW_PYTHON and OPENFABRIC_SPEAKER_REVIEW_WEIGHTS. See docs/speaker-review.md. No weights are downloaded by OpenFabric.")
    if _NAMESPACE_UNAVAILABLE:
        result.reason="speaker_storage_unavailable"
        return result
    try:
        packages=environment.versions(python) if python else None
        result.deps_available=packages is not None and environment.compatible(packages)
        result.weights_available=weights is not None and weights.is_file() and not weights.is_symlink()
        if python is None:result.reason="speaker_review_not_configured"
        elif packages is None:result.reason="speaker_dependencies_missing"
        elif not result.deps_available:result.reason="speaker_dependencies_incompatible"
        elif not result.weights_available:result.reason="speaker_weights_missing"
        elif weights is not None:
            result.encoder=SpeakerEncoderIdentity.model_validate(environment.verified_identity(python,weights).to_json())
            from .yue_upload import get_ffmpeg_bin
            if get_ffmpeg_bin() is None:result.reason="speaker_ffmpeg_missing"
            else:result.available=True
    except ValueError as error:result.reason=str(error)
    except (OSError,UnicodeError):result.reason="speaker_storage_unavailable"
    return result


@dataclass(frozen=True)
class _Target:
    book_id:str
    chapter_index:int
    passage_id:str
    revision:int
    render_identity:str
    audio_path:Path
    audio_sha256:str
    duration_ms:int
    snapshot:SpeechRenderSnapshot
    profile_fingerprint:str
    cast_fingerprint:str
    speaker:str


class _Stored(BaseModel):
    model_config=ConfigDict(extra="forbid",allow_inf_nan=False)
    public:SpeakerReview
    profile_fingerprint:str=Field(pattern=r"^[0-9a-f]{64}$")
    cast_fingerprint:str=Field(pattern=r"^[0-9a-f]{64}$")
    reference_embedding:list[float]|None=Field(default=None,min_length=192,max_length=192)
    passage_embedding:list[float]|None=Field(default=None,min_length=192,max_length=192)
    worker:WorkerIdentity|None=None


def _fingerprint(value:object)->str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def _stamp()->str:return datetime.now(timezone.utc).isoformat()


def _directory(identifier:str)->Path:
    if _ID.fullmatch(identifier) is None:raise SpeakerReviewError("speaker_review_not_found",404)
    root=audiobooks.books_root().resolve();parent=root/".speaker-reviews";path=parent/identifier
    if parent.is_symlink() or path.is_symlink() or not path.resolve().is_relative_to(root):raise SpeakerReviewError("speaker_storage_unavailable",503)
    try:parent.mkdir(exist_ok=True)
    except OSError as error:raise SpeakerReviewError("speaker_storage_unavailable",503) from error
    return path


def _path(identifier:str)->Path:
    path=_directory(identifier)/"review.json"
    if path.is_symlink():raise SpeakerReviewError("speaker_storage_unavailable",503)
    return path


def workspace_path(identifier:str)->Path:
    path=_directory(identifier)/"work"
    if path.is_symlink():raise SpeakerReviewError("speaker_storage_unavailable",503)
    return path


def _read(identifier:str)->_Stored:
    path=_path(identifier)
    try:
        with document_lock(path):
            if path.stat().st_size>1048576:raise SpeakerReviewError("speaker_storage_unavailable",503)
            stored=_Stored.model_validate_json(path.read_bytes())
            if stored.public.id!=identifier:raise SpeakerReviewError("speaker_storage_unavailable",503)
            return stored
    except FileNotFoundError as error:raise SpeakerReviewError("speaker_review_not_found",404) from error
    except (OSError,ValidationError) as error:raise SpeakerReviewError("speaker_storage_unavailable",503) from error


def _write(stored:_Stored)->None:
    path=_path(stored.public.id);path.parent.mkdir(exist_ok=True)
    write_object(path,TypeAdapter(JsonObject).validate_json(stored.model_dump_json()))


def _audio(path:Path)->tuple[str,int]:
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(audiobooks.books_root().resolve()):raise SpeakerReviewError("speaker_source_unavailable",409)
    try:
        with wave.open(str(path),"rb") as source:
            if source.getframerate()<1 or source.getnframes()<1:raise SpeakerReviewError("speaker_source_unavailable",409)
            duration=source.getnframes()*1000//source.getframerate()
            if duration>600000:raise SpeakerReviewError("speaker_audio_insufficient",409)
        return file_digest(path),duration
    except (OSError,EOFError,wave.Error) as error:raise SpeakerReviewError("speaker_source_unavailable",409) from error


def _profile(profile_id:str)->str:
    profile=voice_profiles.get_profile(profile_id)
    if not profile.consent_confirmed:raise SpeakerReviewError("consent_required",403)
    reference=""
    if profile.reference_audio_path:
        path=Path(profile.reference_audio_path);owned=voice_profiles.profiles_root()/profile_id
        if path.is_symlink() or owned.is_symlink() or not path.is_file() or path.resolve().parent!=owned.resolve():raise SpeakerReviewError("speaker_source_unavailable",409)
        reference=file_digest(path)
    return _fingerprint([profile.id,profile.renderer,profile.cloud.model_dump() if profile.cloud else None,reference,profile.reference_transcript,profile.reference_language])


def _snapshot(book_id:str,chapter_index:int,passage_id:str)->_Target:
    with audiobooks.publication_lock(book_id),audiobooks._LOCK,voice_profiles._LOCK:
        rows=audiobook_workflows.get_passages(book_id,chapter_index)
        passage=next((row for row in rows.passages if row.id==passage_id and row.status=="done" and row.render_identity),None)
        if passage is None or passage.render_identity is None:raise SpeakerReviewError("speaker_source_unavailable",409)
        book=audiobooks.get_book(book_id)
        assigned=book.profile_id if passage.speaker=="Narrator" else next((member.profile_id for member in book.cast if member.name.casefold()==passage.speaker.casefold()),None)
        if assigned!=passage.profile_id:raise SpeakerReviewError("speaker_cast_changed",409)
        snapshot=audiobook_workflows.passage_render_snapshot(book_id,chapter_index,passage_id,rows.revision)
        profile=voice_profiles.get_profile(snapshot.profile_id)
        if (snapshot.cloud is not None)!=(profile.renderer=="openrouter"):raise SpeakerReviewError("speaker_voice_changed",409)
        if snapshot.cloud is not None and profile.cloud is not None and snapshot.cloud.model_dump(exclude={"model_fingerprint"})!=profile.cloud.model_dump():raise SpeakerReviewError("speaker_voice_changed",409)
        path=audiobook_workflows.passage_audio_path(book_id,passage_id,rows.revision);digest,duration=_audio(path)
        return _Target(book_id,chapter_index,passage_id,rows.revision,passage.render_identity,path,digest,duration,snapshot,
            _profile(snapshot.profile_id),_fingerprint([book.profile_id,[member.model_dump() for member in book.cast]]),passage.speaker)


def _candidate(target:_Target,kind:Literal["passage","audition"],source_id:str,chapter_index:int,clip_index:int|None=None)->tuple[SpeakerReferenceCandidate,Path]:
    revision:int|None
    if kind=="passage":
        rows=audiobook_workflows.get_passages(target.book_id,chapter_index)
        source=next((item for item in rows.passages if item.id==source_id and item.status=="done" and item.render_identity),None)
        if source is None or source.render_identity is None or source.id==target.passage_id:raise SpeakerReviewError("speaker_reference_changed",409)
        snapshot=audiobook_workflows.passage_render_snapshot(target.book_id,chapter_index,source.id,rows.revision)
        path=audiobook_workflows.passage_audio_path(target.book_id,source.id,rows.revision)
        source_identity,revision,label,url=source.render_identity,rows.revision,f"Chapter {chapter_index+1}, passage {source.section_index+1}",source.audio_url
    else:
        audition=audiobook_workflows.get_audition(source_id)
        clip=next((item for item in audition.clips if item.index==clip_index and item.status=="done" and not item.mock),None)
        if audition.book_id!=target.book_id or audition.status!="done" or clip is None or clip_index is None:raise SpeakerReviewError("speaker_reference_changed",409)
        snapshot=audiobook_workflows.audition_clip_snapshot(source_id,clip_index)
        path=audiobook_workflows.audition_audio_path(source_id,clip_index)
        source_identity,revision,label,url=_fingerprint([source_id,clip_index,snapshot.identity]),audition.revision,f"Cast audition · {clip.speaker}",clip.audio_url
    if snapshot.identity!=target.snapshot.identity or url is None:raise SpeakerReviewError("speaker_reference_incompatible",409)
    digest,duration=_audio(path)
    if digest==target.audio_sha256:raise SpeakerReviewError("speaker_reference_same_audio",409)
    identifier=_fingerprint([kind,source_id,clip_index,chapter_index,revision,source_identity,snapshot.identity,digest])
    return SpeakerReferenceCandidate(id=identifier,kind=kind,source_id=source_id,chapter_index=chapter_index,clip_index=clip_index,
        revision=revision,source_identity=source_identity,render_snapshot_identity=snapshot.identity,audio_sha256=digest,
        profile_id=snapshot.profile_id,speaker=target.speaker,renderer="openrouter" if snapshot.cloud else "local",
        label=label,audio_url=url,duration_ms=duration),path


def references(book_id:str,chapter_index:int,passage_id:str,revision:int,render_identity:str)->SpeakerReferencesResponse:
    with audiobooks.publication_lock(book_id),audiobooks._LOCK,voice_profiles._LOCK,closing(audiobooks._connect()) as connection:
        target=_snapshot(book_id,chapter_index,passage_id)
        if target.revision!=revision or target.render_identity!=render_identity:raise SpeakerReviewError("passage_changed",409)
        rows=connection.execute("""SELECT j.chapter_index,s.passage_id FROM audiobook_sections s JOIN audiobook_jobs j ON j.id=s.job_id
            WHERE j.book_id=? AND s.profile_id=? AND s.status='done' AND s.passage_id!=? ORDER BY j.chapter_index,s.section_index LIMIT 100""",
            (book_id,target.snapshot.profile_id,passage_id)).fetchall()
        choices:list[SpeakerReferenceCandidate]=[]
        for row in rows:
            try:choice,_=_candidate(target,"passage",str(row[1]),int(row[0]));choices.append(choice)
            except (SpeakerReviewError,audiobooks.AudiobookError):continue
        for audition in audiobook_workflows.list_auditions(book_id):
            for clip in audition.clips:
                if len(choices)>=100:break
                if clip.profile_id!=target.snapshot.profile_id:continue
                try:choice,_=_candidate(target,"audition",audition.id,audition.chapter_index,clip.index);choices.append(choice)
                except (SpeakerReviewError,audiobooks.AudiobookError):continue
        return SpeakerReferencesResponse(references=choices)


def _current(stored:_Stored)->bool:
    public=stored.public
    try:
        target=_snapshot(public.book_id,public.chapter_index,public.passage_id)
        source,_=_candidate(target,public.reference.kind,public.reference.source_id,public.reference.chapter_index,public.reference.clip_index)
        available=capability()
        return (target.revision==public.revision and target.render_identity==public.render_identity and target.audio_sha256==public.audio_sha256
            and target.profile_fingerprint==stored.profile_fingerprint and target.cast_fingerprint==stored.cast_fingerprint
            and source.id==public.reference.id and available.available and available.encoder==public.encoder)
    except (SpeakerReviewError,audiobooks.AudiobookError,voice_profiles.VoiceProfileError,OSError,ValidationError):return False


def get(identifier:str)->SpeakerReview:
    stored=_read(identifier)
    if stored.public.state=="completed" and not _current(stored):
        return stored.public.model_copy(update={"state":"stale","reason":"speaker_inputs_changed","score":None,"below_threshold":None})
    return stored.public


def list_reviews(book_id:str)->SpeakerReviewsResponse:
    if _ID.fullmatch(book_id) is None:raise SpeakerReviewError("invalid_book_id",404)
    items:list[SpeakerReview]=[]
    for path in _directory("0"*32).parent.iterdir():
        if not _ID.fullmatch(path.name) or path.is_symlink():continue
        try:item=get(path.name)
        except SpeakerReviewError:
            _LOG.warning("Ignoring incomplete speaker review metadata: %s",path.name);continue
        if item.book_id==book_id:items.append(item)
    return SpeakerReviewsResponse(reviews=sorted(items,key=lambda item:item.created_at,reverse=True)[:100])


def _state(identifier:str,state:State,reason:str="",embeddings:tuple[protocol.Embedding,protocol.Embedding]|None=None)->None:
    with document_lock(_path(identifier)):
        stored=_read(identifier);public=stored.public
        public.state,public.reason,public.updated_at=state,reason,_stamp()
        public.score=public.below_threshold=None
        if embeddings is not None:
            reference,passage=embeddings
            public.score=protocol.compare(reference,passage);public.below_threshold=public.score<public.threshold
            public.analyzed_ms,public.reference_analyzed_ms=passage.duration_ms,reference.duration_ms
            stored.reference_embedding,stored.passage_embedding=reference.values,passage.values
        _write(stored)


def _worker(identifier:str,identity:WorkerIdentity|None)->None:
    with document_lock(_path(identifier)):
        stored=_read(identifier);stored.worker=identity;_write(stored)


async def _drain(identifier:str,proc:asyncio.subprocess.Process)->None:
    drain=asyncio.create_task(kill_process_tree(proc))
    try:await await_cleanup(drain)
    finally:
        if drain.done() and not drain.cancelled() and drain.exception() is None:
            _PROCESSES.pop(identifier,None);_UNVERIFIED.discard(identifier);_worker(identifier,None)
        else:_UNVERIFIED.add(identifier)


async def _run_tool(identifier:str,command:list[str],workspace:Path,timeout:float,*,encoder_boundary:bool=False)->None:
    proc:asyncio.subprocess.Process|None=None
    try:
        proc=await spawn_owned(command,receipt_path=_directory(identifier)/"worker.json",cwd=workspace,
            stdout=asyncio.subprocess.PIPE,on_identity=lambda value:_worker(identifier,value))
        _PROCESSES[identifier]=proc
        output=await read_owned_output(proc,max_bytes=65536,timeout=timeout)
        if proc.returncode!=0:
            code="speaker_runner_failed"
            if encoder_boundary:
                try:
                    parsed:object=json.loads(output.decode("utf-8",errors="replace").strip().splitlines()[-1])
                    if isinstance(parsed,dict) and set(parsed)=={"error"} and isinstance(parsed["error"],str) and parsed["error"] in {
                        "speaker_audio_insufficient","speaker_weights_unverified","speaker_dependencies_missing","speaker_dependencies_incompatible","speaker_output_invalid"}:
                        code=parsed["error"]
                except (ValueError,IndexError):pass
            _LOG.warning("Speaker tool failed: %s (%s diagnostic bytes)",code,len(output))
            raise SpeakerReviewError(code,503)
    except (OSError,TimeoutError,WorkerOutputError) as error:raise SpeakerReviewError("speaker_runner_failed",503) from error
    finally:
        if proc is not None:await _drain(identifier,proc)
        elif _read(identifier).worker is not None and not await _recover_worker(identifier):_UNVERIFIED.add(identifier)


async def _encode(identifier:str,workspace:Path)->tuple[protocol.Embedding,protocol.Embedding]:
    from .yue_upload import get_ffmpeg_bin
    ffmpeg=get_ffmpeg_bin();python=os.environ.get("OPENFABRIC_SPEAKER_REVIEW_PYTHON");weights=os.environ.get("OPENFABRIC_SPEAKER_REVIEW_WEIGHTS")
    if ffmpeg is None or not python or not weights:raise SpeakerReviewError("speaker_review_unavailable",503)
    for name in ("reference","passage"):
        await _run_tool(identifier,[ffmpeg,"-nostdin","-v","error","-y","-protocol_whitelist","file,pipe","-format_whitelist","wav",
            "-i",str(workspace/f"{name}-source.wav"),"-t","30","-vn","-ac","1","-ar","16000","-c:a","pcm_s16le",str(workspace/f"{name}.wav")],workspace,120)
    request=workspace/"request.json";output=workspace/"embeddings.json"
    write_object(request,{"reference":str(workspace/"reference.wav"),"passage":str(workspace/"passage.wav"),"weights":weights})
    await _run_tool(identifier,[python,str(Path(__file__).resolve().parents[1]/"scripts"/"speaker_review.py"),"--request",str(request),"--output",str(output)],workspace,180,encoder_boundary=True)
    if output.is_symlink() or not output.is_file() or output.stat().st_size>65536:raise SpeakerReviewError("speaker_output_invalid",503)
    result:object=json.loads(output.read_bytes())
    if not isinstance(result,dict) or set(result)!={"reference","passage"}:raise SpeakerReviewError("speaker_output_invalid",503)
    return protocol.parse_embedding(result["reference"]),protocol.parse_embedding(result["passage"])


async def _cleanup(identifier:str)->None:
    path=workspace_path(identifier)
    try:
        if path.exists():await await_cleanup(asyncio.to_thread(shutil.rmtree,path))
        _CLEANUP_PENDING.discard(identifier)
    except BaseException:_CLEANUP_PENDING.add(identifier);raise


async def _execute(identifier:str)->None:
    try:
        _state(identifier,"running")
        embeddings=await _encode(identifier,workspace_path(identifier))
        stored=_read(identifier)
        expected=stored.public.encoder
        if expected is None or any(embedding.encoder.to_json()!=expected.model_dump() for embedding in embeddings):raise SpeakerReviewError("speaker_encoder_changed",409)
        with audiobooks.publication_lock(stored.public.book_id),audiobooks._LOCK,voice_profiles._LOCK:
            if not _current(stored):_state(identifier,"skipped","speaker_inputs_changed")
            else:_state(identifier,"completed",embeddings=embeddings)
    except asyncio.CancelledError:
        _state(identifier,"canceled","canceled_by_user_or_shutdown");raise
    except (SpeakerReviewError,ValueError) as error:
        code=error.code if isinstance(error,SpeakerReviewError) else str(error)
        unavailable={"speaker_weights_unverified","speaker_dependencies_missing","speaker_dependencies_incompatible","speaker_review_unavailable"}
        if code not in unavailable|{"speaker_audio_insufficient","speaker_encoder_changed","speaker_output_invalid","speaker_runner_failed"}:code="speaker_review_failed"
        _state(identifier,"skipped" if code=="speaker_audio_insufficient" else "unavailable" if code in unavailable else "failed",code)
    except Exception:
        _LOG.exception("Local speaker review failed");_state(identifier,"failed","speaker_review_failed")
    finally:
        if identifier not in _UNVERIFIED:
            try:await _cleanup(identifier)
            except OSError:_LOG.exception("Speaker workspace cleanup retained")
        else:_CLEANUP_PENDING.add(identifier)


async def create(book_id:str,chapter_index:int,passage_id:str,request:SpeakerReviewRequest)->SpeakerReview:
    async with admission_lock:
        require_setup_idle()
        if _STOPPING or work_busy():raise SpeakerReviewError("speaker_review_busy",409)
        if native_work_inflight():raise SpeakerReviewError("native_model_busy",409)
        from .work_busy import local_work_busy
        if local_work_busy():raise SpeakerReviewError("speaker_review_busy",409)
        with audiobooks.publication_lock(book_id),audiobooks._LOCK,voice_profiles._LOCK:
            target=_snapshot(book_id,chapter_index,passage_id)
            choices=references(book_id,chapter_index,passage_id,request.revision,request.render_identity)
            reference=next((item for item in choices.references if item.id==request.reference_id),None)
            if reference is None:raise SpeakerReviewError("speaker_reference_changed",409)
            reference,source=_candidate(target,reference.kind,reference.source_id,reference.chapter_index,reference.clip_index)
            available=capability();stamp=_stamp()
            public=SpeakerReview(id=uuid.uuid4().hex,book_id=book_id,chapter_index=chapter_index,passage_id=passage_id,
                revision=target.revision,render_identity=target.render_identity,audio_sha256=target.audio_sha256,reference=reference,reference_reviewed=request.reference_reviewed,
                encoder=available.encoder,threshold=request.threshold,state="queued" if available.available else "unavailable",
                reason=available.reason,created_at=stamp,updated_at=stamp)
            public.renderer_identity_verified=target.snapshot.engine_identity is not None and target.snapshot.cloud is None
            public.warnings=["activity_not_speech_detection"]
            if not public.renderer_identity_verified:public.warnings.append("speaker_renderer_unverified")
            if target.duration_ms>30000 or reference.duration_ms>30000:public.warnings.append("partial_analysis")
            stored=_Stored(public=public,profile_fingerprint=target.profile_fingerprint,cast_fingerprint=target.cast_fingerprint)
            _write(stored)
            if available.available:
                workspace=workspace_path(public.id);workspace.mkdir()
                try:
                    shutil.copyfile(source,workspace/"reference-source.wav");shutil.copyfile(target.audio_path,workspace/"passage-source.wav")
                    if file_digest(workspace/"reference-source.wav")!=reference.audio_sha256 or file_digest(workspace/"passage-source.wav")!=target.audio_sha256:raise SpeakerReviewError("speaker_inputs_changed",409)
                except (OSError,SpeakerReviewError) as error:
                    _state(public.id,"failed","speaker_storage_unavailable")
                    try:shutil.rmtree(workspace)
                    except OSError:_CLEANUP_PENDING.add(public.id);_LOG.exception("Speaker capture cleanup retained")
                    if isinstance(error,SpeakerReviewError):raise
                    raise SpeakerReviewError("speaker_storage_unavailable",503) from error
                task=asyncio.create_task(_execute(public.id));_TASKS[public.id]=task
                task.add_done_callback(lambda finished:_TASKS.pop(public.id,None))
            return public


async def cancel(identifier:str)->SpeakerReview:
    task=_TASKS.get(identifier)
    if task is not None:task.cancel();await await_cleanup(asyncio.gather(task,return_exceptions=True))
    if _read(identifier).public.state in {"queued","running"}:_state(identifier,"canceled","canceled_by_user_or_shutdown")
    if identifier not in _UNVERIFIED and identifier not in _PROCESSES:await _cleanup(identifier)
    return get(identifier)


def work_busy()->bool:return _NAMESPACE_UNAVAILABLE or bool(_UNVERIFIED or _CLEANUP_PENDING or _PROCESSES) or any(not task.done() for task in _TASKS.values())


async def _recover_worker(identifier:str)->bool:
    try:stored=_read(identifier)
    except SpeakerReviewError:return await _recover_orphan(identifier)
    if stored.worker is None:return await _recover_orphan(identifier)
    receipt=_directory(identifier)/"worker.json"
    if receipt.is_symlink() or Path(stored.worker.receipt).resolve()!=receipt.resolve() or not await terminate_verified(stored.worker):return False
    _worker(identifier,None);_UNVERIFIED.discard(identifier);return True


async def _recover_orphan(identifier:str)->bool:
    """Missing metadata grants no PID ownership; retain all orphan evidence."""
    receipt=_directory(identifier)/"worker.json"
    if receipt.is_symlink():return False
    if not receipt.exists():return identifier not in _UNVERIFIED
    try:
        if not receipt.is_file() or receipt.stat().st_size>65536:return False
        value=WorkerReceipt.model_validate_json(receipt.read_bytes())
        identity=WorkerIdentity(pid=value.pid,token=value.token,receipt=str(receipt))
    except (OSError,ValidationError):return False
    return await terminate_verified(identity)


async def start()->None:
    global _STOPPING,_NAMESPACE_UNAVAILABLE
    _STOPPING=True
    try:paths=list(_directory("0"*32).parent.iterdir())
    except (SpeakerReviewError,OSError):
        _NAMESPACE_UNAVAILABLE=True;_STOPPING=False
        _LOG.exception("Speaker review namespace unavailable; admission remains quarantined")
        return
    _NAMESPACE_UNAVAILABLE=False
    for path in paths:
        if not _ID.fullmatch(path.name) or path.is_symlink():continue
        identifier=path.name
        try:stored=_read(identifier)
        except SpeakerReviewError:
            if not await _recover_orphan(identifier):_UNVERIFIED.add(identifier)
            else:_UNVERIFIED.discard(identifier)
            _LOG.warning("Retaining incomplete speaker review evidence: %s",identifier)
            continue
        if not await _recover_worker(identifier):
            _UNVERIFIED.add(identifier);_CLEANUP_PENDING.add(identifier);_state(identifier,"failed","speaker_worker_unverified");continue
        if stored.public.state in {"queued","running"}:_state(identifier,"canceled","interrupted_by_restart")
        try:await _cleanup(identifier)
        except OSError:_LOG.exception("Speaker startup cleanup retained for retry")
    _STOPPING=False


async def shutdown()->None:
    global _STOPPING
    _STOPPING=True
    identifiers=tuple(_TASKS)
    tasks=tuple(_TASKS.values())
    for task in tasks:
        if not task.done() and not task.cancelling():task.cancel()
    await await_cleanup(asyncio.gather(*tasks,return_exceptions=True))
    await await_cleanup(asyncio.gather(*(_drain(identifier,proc) for identifier,proc in tuple(_PROCESSES.items())),return_exceptions=True))
    for identifier in tuple(_UNVERIFIED):
        if identifier not in _PROCESSES and await _recover_worker(identifier):_UNVERIFIED.discard(identifier)
    for identifier in identifiers:
        if identifier not in _UNVERIFIED and identifier not in _PROCESSES:
            if _read(identifier).public.state in {"queued","running"}:_state(identifier,"canceled","canceled_by_user_or_shutdown")
            await _cleanup(identifier)
    await await_cleanup(asyncio.gather(*(_cleanup(identifier) for identifier in tuple(_CLEANUP_PENDING) if identifier not in _UNVERIFIED),return_exceptions=True))
