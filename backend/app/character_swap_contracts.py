"""Persisted character-swap jobs and app-owned API contracts."""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, StrictBool, StringConstraints, model_validator

from .openrouter_contracts import OpenRouterQuote
from .video_contracts import VideoContract, VideoId


CharacterSwapStatus = Literal[
    'prepared', 'submitting', 'submission_unknown', 'queued', 'running', 'ready', 'failed', 'stopped'
]


class CharacterSwapJob(VideoContract):
    id: VideoId
    revision: int = Field(default=1, ge=1)
    name: str = Field(min_length=1, max_length=120)
    status: CharacterSwapStatus = 'prepared'
    stage: Literal['frame', 'video'] = 'frame'
    quote: OpenRouterQuote | None = None
    resume_available: bool = False
    requote_available: bool = False
    frame_receipt_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    video_receipt_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    phase: str = Field(default='prepared', max_length=64)
    error_code: str = Field(default='', max_length=80)
    created_at: str
    updated_at: str
    start_sec: float = Field(ge=0, le=21600)
    seconds: int = Field(ge=2, le=8)
    source_name: str = Field(max_length=255)
    image_name: str = Field(max_length=255)
    source_duration_sec: float = Field(gt=0, le=21600)
    width: int = Field(ge=2, le=4096)
    height: int = Field(ge=2, le=4096)
    frames: int = Field(ge=48, le=192)
    has_audio: bool
    estimated_usd: float | None = Field(default=None, ge=0)
    quote_expires_at: str = ''
    seed: int = Field(ge=0, le=2147483647)
    source_url: str = Field(pattern=r'^/api/videos/character-swaps/[0-9a-f]{32}/source$')
    clip_url: str = Field(pattern=r'^/api/videos/character-swaps/[0-9a-f]{32}/clip$')
    image_url: str = Field(pattern=r'^/api/videos/character-swaps/[0-9a-f]{32}/image$')
    output_url: str = Field(default='', pattern=r'^(?:/api/videos/character-swaps/[0-9a-f]{32}/output)?$')
    output_sha256: str = Field(default='', pattern=r'^(?:[0-9a-f]{64})?$')
    remote_id: str | None = Field(default=None, min_length=1, max_length=160, pattern=r'^[A-Za-z0-9_-]+$')
    provider: Literal['openrouter', 'fal'] = 'openrouter'
    model_id: Literal['bytedance-seed/seedream-4.5', 'runway/aleph-2', 'fal-ai/wan/v2.2-14b/animate/replace'] = 'bytedance-seed/seedream-4.5'
    price_checked_at: str = Field(default='', max_length=32)
    source_frame_url: str = Field(default='', pattern=r'^(?:/api/videos/character-swaps/[0-9a-f]{32}/frame-original)?$')
    edited_frame_url: str = Field(default='', pattern=r'^(?:/api/videos/character-swaps/[0-9a-f]{32}/frame-edited)?$')
    filmstrip_url: str = Field(default='', pattern=r'^(?:/api/videos/character-swaps/[0-9a-f]{32}/filmstrip)?$')
    prompt: str = Field(default='', max_length=2000)
    review_fingerprint: str = Field(default='', pattern=r'^(?:[0-9a-f]{64})?$')
    preparation_reviewed: bool = False

    @model_validator(mode='after')
    def own_media(self) -> Self:
        base = f'/api/videos/character-swaps/{self.id}'
        if (self.source_url != base + '/source' or self.clip_url != base + '/clip'
                or self.image_url != base + '/image' or self.output_url not in ('', base + '/output')
                or self.source_frame_url not in ('', base + '/frame-original')
                or self.edited_frame_url not in ('', base + '/frame-edited')
                or self.filmstrip_url not in ('', base + '/filmstrip')):
            raise ValueError('media_path_mismatch')
        return self


class CharacterSwapsResponse(VideoContract):
    jobs: list[CharacterSwapJob]


class CharacterSwapReadiness(VideoContract):
    configured: bool
    enabled: bool = False
    credential_configured: bool = False
    clip_host_configured: bool = False
    ffmpeg_ready: bool


class SubmitCharacterSwapRequest(VideoContract):
    revision: int = Field(ge=1, strict=True)
    quote_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    transfers_confirmed: StrictBool = False
    consent_confirmed: StrictBool = False


class CharacterSwapRevisionRequest(VideoContract):
    revision: int = Field(ge=1, strict=True)


class ReviewCharacterSwapRequest(CharacterSwapRevisionRequest):
    review_fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')


class RenameCharacterSwapRequest(CharacterSwapRevisionRequest):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class DeletedCharacterSwapResponse(VideoContract):
    deleted: VideoId


CHARACTER_SWAP_CLIENT_MODELS: list[type[BaseModel]] = [
    CharacterSwapJob, CharacterSwapsResponse, CharacterSwapReadiness,
    SubmitCharacterSwapRequest, CharacterSwapRevisionRequest,
    ReviewCharacterSwapRequest, RenameCharacterSwapRequest, DeletedCharacterSwapResponse,
]
