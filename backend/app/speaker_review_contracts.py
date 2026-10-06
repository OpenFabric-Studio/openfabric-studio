"""Explicit human-approved local speaker screening; never an identity verdict."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel,Field
from .contracts import Contract


class SpeakerEncoderIdentity(Contract):
    family:Literal["speechbrain-ecapa-voxceleb"]="speechbrain-ecapa-voxceleb"
    pipeline:Literal["ecapa-voxceleb-16k-fbank80-sentence-mean-l2-v1"]="ecapa-voxceleb-16k-fbank80-sentence-mean-l2-v1"
    weights_sha256:str=Field(pattern=r"^[0-9a-f]{64}$")
    speechbrain_version:str=Field(min_length=1,max_length=100)
    torch_version:str=Field(min_length=1,max_length=100)
    torchaudio_version:str=Field(min_length=1,max_length=100)


class SpeakerReviewCapability(Contract):
    available:bool
    configured:bool=False
    deps_available:bool=False
    weights_available:bool=False
    encoder:SpeakerEncoderIdentity|None=None
    reason:str=Field(default="",max_length=100)
    setup_hint:str=Field(default="",max_length=500)


class SpeakerReferenceCandidate(Contract):
    id:str=Field(pattern=r"^[0-9a-f]{64}$")
    kind:Literal["passage","audition"]
    source_id:str=Field(pattern=r"^[0-9a-f]{32}$")
    chapter_index:int=Field(ge=0,le=99)
    clip_index:int|None=Field(default=None,ge=0,le=16)
    revision:int|None=Field(default=None,ge=1)
    source_identity:str=Field(pattern=r"^[0-9a-f]{64}$")
    render_snapshot_identity:str=Field(pattern=r"^[0-9a-f]{64}$")
    audio_sha256:str=Field(pattern=r"^[0-9a-f]{64}$")
    profile_id:str=Field(pattern=r"^[0-9a-f]{32}$")
    speaker:str=Field(min_length=1,max_length=40)
    renderer:Literal["local","openrouter"]
    label:str=Field(min_length=1,max_length=200)
    audio_url:str=Field(max_length=300,pattern=r"^/api/audiobooks/")
    duration_ms:int=Field(ge=0,le=600000)


class SpeakerReferencesResponse(Contract):
    references:list[SpeakerReferenceCandidate]=Field(default_factory=list,max_length=100)


class SpeakerReviewRequest(Contract):
    revision:int=Field(ge=1)
    render_identity:str=Field(pattern=r"^[0-9a-f]{64}$")
    reference_id:str=Field(pattern=r"^[0-9a-f]{64}$")
    reference_reviewed:Literal[True]
    threshold:float=Field(ge=-1,le=1)


class SpeakerReview(Contract):
    id:str=Field(pattern=r"^[0-9a-f]{32}$")
    book_id:str=Field(pattern=r"^[0-9a-f]{32}$")
    chapter_index:int=Field(ge=0,le=99)
    passage_id:str=Field(pattern=r"^[0-9a-f]{32}$")
    revision:int=Field(ge=1)
    render_identity:str=Field(pattern=r"^[0-9a-f]{64}$")
    audio_sha256:str=Field(pattern=r"^[0-9a-f]{64}$")
    reference:SpeakerReferenceCandidate
    reference_reviewed:Literal[True]
    encoder:SpeakerEncoderIdentity|None=None
    threshold:float=Field(ge=-1,le=1)
    renderer_identity_verified:bool=False
    warnings:list[Literal["speaker_renderer_unverified","partial_analysis","activity_not_speech_detection"]]=Field(default_factory=list,max_length=3)
    score:float|None=Field(default=None,ge=-1,le=1)
    below_threshold:bool|None=None
    analyzed_ms:int=Field(default=0,ge=0,le=30000)
    reference_analyzed_ms:int=Field(default=0,ge=0,le=30000)
    state:Literal["queued","running","completed","unavailable","skipped","failed","canceled","stale"]
    reason:str=Field(default="",max_length=100)
    created_at:str=Field(min_length=1,max_length=64)
    updated_at:str=Field(min_length=1,max_length=64)


class SpeakerReviewsResponse(Contract):
    reviews:list[SpeakerReview]=Field(default_factory=list,max_length=100)


SPEAKER_REVIEW_CLIENT_MODELS:list[type[BaseModel]]=[SpeakerEncoderIdentity,SpeakerReviewCapability,SpeakerReferenceCandidate,
    SpeakerReferencesResponse,SpeakerReviewRequest,SpeakerReview,SpeakerReviewsResponse]
