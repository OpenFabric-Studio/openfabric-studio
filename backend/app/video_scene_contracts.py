"""Public local scene-cutter contracts; filesystem provenance stays private."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, model_validator
from .video_contracts import VideoContract, VideoId


class VideoSceneReadiness(VideoContract):
    ffmpeg_ready: bool
    native_picker_available: bool
    max_upload_bytes: Literal[2147483648] = 2147483648
    max_native_bytes: Literal[8589934592] = 8589934592
    max_cut_seconds: Literal[600] = 600
    preview_seconds: Literal[15] = 15


class VideoSceneSource(VideoContract):
    """Visible dimensions include sample aspect ratio and quarter-turn rotation."""
    id: VideoId
    name: str = Field(min_length=1, max_length=255)
    origin: Literal['native', 'upload']
    duration_sec: float = Field(gt=0, le=21600)
    width: int = Field(ge=2, le=4096)
    height: int = Field(ge=2, le=4096)
    fps: float = Field(ge=1, le=120)
    has_audio: bool
    bytes: int = Field(gt=0, le=8589934592)
    created_at: str


class VideoSceneSourcesResponse(VideoContract):
    sources: list[VideoSceneSource]


class VideoScenePreviewRequest(VideoContract):
    start_sec: float = Field(ge=0, le=21600)


class VideoScenePreview(VideoContract):
    id: VideoId
    source_id: VideoId
    start_sec: float = Field(ge=0, le=21600)
    duration_sec: float = Field(gt=0, le=15)
    width: int = Field(ge=2, le=960)
    height: int = Field(ge=2, le=960)
    url: str = Field(pattern=r'^/api/videos/scenes/sources/[0-9a-f]{32}/previews/[0-9a-f]{32}$')

    @model_validator(mode='after')
    def owned_url(self) -> VideoScenePreview:
        if self.url != f'/api/videos/scenes/sources/{self.source_id}/previews/{self.id}':
            raise ValueError('invalid_media_url')
        return self


class VideoSceneCutRequest(VideoContract):
    name: str = Field(min_length=1, max_length=120)
    start_sec: float = Field(ge=0, le=21600)
    end_sec: float = Field(gt=0, le=21600)

    @model_validator(mode='after')
    def valid_interval(self) -> VideoSceneCutRequest:
        if not 0 < self.end_sec-self.start_sec <= 600:
            raise ValueError('invalid_interval')
        if not self.name.strip():
            raise ValueError('invalid_name')
        return self


class VideoSceneCut(VideoContract):
    id: VideoId
    source_id: VideoId
    name: str = Field(min_length=1, max_length=120)
    start_sec: float = Field(ge=0, le=21600)
    end_sec: float = Field(gt=0, le=21600)
    seconds: float = Field(gt=0, le=600)
    status: Literal['queued', 'cutting', 'ready', 'failed', 'cancelled'] = 'queued'
    error_code: str = Field(default='', max_length=80)
    created_at: str
    updated_at: str
    width: int = Field(ge=2, le=4096)
    height: int = Field(ge=2, le=4096)
    has_audio: bool
    output_url: str = Field(default='', pattern=r'^(?:/api/videos/scenes/sources/[0-9a-f]{32}/cuts/[0-9a-f]{32}/media)?$')
    output_sha256: str = Field(default='', pattern=r'^(?:[0-9a-f]{64})?$')
    bytes: int = Field(default=0, ge=0, le=2147483648)

    @model_validator(mode='after')
    def matching_interval(self) -> VideoSceneCut:
        if abs(self.end_sec-self.start_sec-self.seconds) > .000001:
            raise ValueError('invalid_interval')
        if self.output_url and self.output_url != f'/api/videos/scenes/sources/{self.source_id}/cuts/{self.id}/media':
            raise ValueError('invalid_media_url')
        return self


class VideoSceneCutsResponse(VideoContract):
    cuts: list[VideoSceneCut]


class DeletedVideoSceneCutResponse(VideoContract):
    source_id: VideoId
    deleted: VideoId


VIDEO_SCENE_CLIENT_MODELS: list[type[BaseModel]] = [VideoSceneReadiness, VideoSceneSource,
    VideoSceneSourcesResponse, VideoScenePreviewRequest, VideoScenePreview, VideoSceneCutRequest,
    VideoSceneCut, VideoSceneCutsResponse, DeletedVideoSceneCutResponse]
