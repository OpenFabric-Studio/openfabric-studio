"""Typed persisted video workspace and public API contracts."""

from __future__ import annotations
import math
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .contracts import Contract

VideoId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
VideoSeconds = Literal[2, 4, 6, 8, 10, 12]
VideoMode = Literal["generated", "cover", "visualizer"]
VideoProjectStatus = Literal[
    "idle", "queued", "running", "ready", "failed", "cancelled"
]


class VideoContract(Contract):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


VideoPreset = Literal["none", "reel"]
PORTRAIT_FRAME = (704, 1280)
PICTURE_SIZES = {(704, 448), (768, 512), (1280, 704), PORTRAIT_FRAME}


class VideoProjectSettings(VideoContract):
    engine_pack: Literal["ltx23", "ltx25"] = "ltx23"
    width: Literal[704, 768, 1280] = 704
    height: Literal[448, 512, 704, 1280] = 448
    stage1_steps: int = Field(default=30, ge=10, le=50)
    stage2_steps: int = Field(default=3, ge=1, le=3)
    cfg_scale: float = Field(default=3, ge=1, le=8)
    negative_prompt: str = Field(default="", max_length=400)

    @model_validator(mode="after")
    def valid_size(self) -> VideoProjectSettings:
        if (self.width, self.height) not in PICTURE_SIZES:
            raise ValueError("invalid_picture_size")
        return self


class VideoExportSettings(VideoContract):
    aspect: Literal["landscape", "portrait", "square"] = "landscape"
    quality: Literal["fast", "standard", "high"] = "standard"
    include_overlays: bool = True
    # Silent stays the default. Speech is muxed at export and is not a model input.
    attach_speech: bool = False


class VideoShotDraft(VideoContract):
    id: VideoId
    start_sec: float = Field(ge=0, le=21600)
    seconds: VideoSeconds = 4
    prompt: str = Field(min_length=1, max_length=2000)
    seed: int = Field(default=0, ge=0, le=2147483647)
    reference_id: VideoId | None = None
    reference_strength: float = Field(default=0.7, ge=0, le=1)
    locked: bool = False

    @field_validator("start_sec")
    @classmethod
    def frame_aligned(cls, value: float) -> float:
        if not math.isclose(value * 24, round(value * 24), abs_tol=1e-5):
            raise ValueError("start_not_frame_aligned")
        return value


class VideoOverlay(VideoContract):
    id: VideoId
    kind: Literal["title", "lyric"] = "title"
    text: str = Field(min_length=1, max_length=500)
    start_sec: float = Field(ge=0, le=21600)
    end_sec: float = Field(gt=0, le=21600)
    position: Literal["top", "center", "bottom"] = "bottom"
    font_size: int = Field(default=36, ge=14, le=96)
    color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")

    @model_validator(mode="after")
    def valid_interval(self) -> VideoOverlay:
        if self.end_sec <= self.start_sec:
            raise ValueError("invalid_overlay_interval")
        return self


class VideoMarker(VideoContract):
    id: VideoId
    time_sec: float = Field(ge=0, le=21600)
    kind: Literal["beat", "onset", "section", "manual"] = "manual"
    label: str = Field(default="", max_length=80)
    confidence: float = Field(default=0, ge=0, le=1)


class VideoEnergyPoint(VideoContract):
    time_sec: float = Field(ge=0, le=21600)
    value: float = Field(ge=0, le=1)


class VideoSongAnalysis(VideoContract):
    duration_sec: float = Field(gt=0, le=21600)
    sample_rate: int = Field(ge=1, le=384000)
    waveform_peaks: list[Annotated[float, Field(ge=0, le=1)]] = Field(
        default_factory=list, max_length=4000
    )
    waveform_step_sec: float = Field(default=1, gt=0)
    energy: list[VideoEnergyPoint] = Field(default_factory=list, max_length=4000)
    markers: list[VideoMarker] = Field(default_factory=list, max_length=4000)
    tempo_bpm: float | None = Field(default=None, ge=30, le=300)
    warnings: list[str] = Field(default_factory=list, max_length=20)


class VideoPhaseTiming(VideoContract):
    phase: str = Field(max_length=40)
    started_at: str
    finished_at: str = ""
    duration_sec: float = Field(default=0, ge=0)


class VideoVariant(VideoContract):
    id: VideoId
    seed: int = Field(ge=0, le=2147483647)
    status: Literal["queued", "running", "ready", "failed", "cancelled"] = "queued"
    error_code: str = ""
    fingerprint: str = ""
    file_url: str = ""
    poster_url: str = ""
    created_at: str
    prompt: str = ""
    settings: VideoProjectSettings = Field(default_factory=VideoProjectSettings)
    mode: VideoMode = "generated"
    reference_id: VideoId | None = None
    reference_strength: float = Field(default=0.7, ge=0, le=1)
    source_fingerprint: str = ""
    engine_fingerprint: str = ""
    phase: str = ""
    progress_current: int = Field(default=0, ge=0)
    progress_total: int = Field(default=0, ge=0)
    started_at: str = ""
    finished_at: str = ""
    timings: list[VideoPhaseTiming] = Field(default_factory=list)
    duration_sec: float = Field(default=0, ge=0)


class VideoProjectShot(VideoShotDraft):
    variants: list[VideoVariant] = Field(default_factory=list, max_length=20)
    approved_variant_id: VideoId | None = None


class VideoReference(VideoContract):
    id: VideoId
    name: str = Field(max_length=160)
    bytes: int = Field(ge=1, le=20971520)
    width: int = Field(ge=1, le=8192)
    height: int = Field(ge=1, le=8192)
    url: str


class VideoSpeechClip(VideoContract):
    """Speech mixed at export. It is never sent to the video model."""

    id: VideoId
    name: str = Field(max_length=160)
    bytes: int = Field(ge=1, le=83886080)
    duration_sec: float = Field(gt=0, le=600)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: Literal["upload", "voice"] = "upload"
    voice_profile_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    line: str = Field(default="", max_length=500)


class VideoProjectJob(VideoContract):
    id: VideoId
    operation: Literal["preview", "render", "export"]
    status: Literal["queued", "running", "ready", "failed", "cancelled"]
    shot_ids: list[VideoId] = Field(default_factory=list, max_length=40)
    phase: str = ""
    shot_index: int = Field(default=0, ge=0)
    shot_count: int = Field(default=0, ge=0)
    progress_current: int = Field(default=0, ge=0)
    progress_total: int = Field(default=0, ge=0)
    error_code: str = ""
    started_at: str = ""
    finished_at: str = ""
    timings: list[VideoPhaseTiming] = Field(default_factory=list)


class VideoProject(VideoContract):
    id: VideoId
    revision: int = Field(ge=1)
    track_id: int | None = Field(default=None, ge=1)
    track_title: str
    name: str = Field(min_length=1, max_length=120)
    preset: VideoPreset = "none"
    mode: VideoMode = "generated"
    direction: str = Field(default="", max_length=2000)
    character_lock: bool = False
    character_id: VideoId | None = None
    character_adapter_id: VideoId | None = None
    seed: int = Field(default=0, ge=0, le=2147483647)
    duration_sec: float = Field(gt=0, le=21600)
    source_fingerprint: str
    source_changed: bool = False
    created_at: str
    updated_at: str
    settings: VideoProjectSettings = Field(default_factory=VideoProjectSettings)
    export_settings: VideoExportSettings = Field(default_factory=VideoExportSettings)
    shots: list[VideoProjectShot] = Field(default_factory=list, max_length=40)
    references: list[VideoReference] = Field(default_factory=list, max_length=6)
    speech_clip: VideoSpeechClip | None = None
    overlays: list[VideoOverlay] = Field(default_factory=list, max_length=100)
    markers: list[VideoMarker] = Field(default_factory=list, max_length=4000)
    analysis: VideoSongAnalysis | None = None
    job: VideoProjectJob | None = None
    file_url: str = ""
    poster_url: str = ""
    warnings: list[str] = Field(default_factory=list, max_length=30)


class VideoProjectsResponse(VideoContract):
    projects: list[VideoProject]


class CreateVideoProjectRequest(VideoContract):
    track_id: int | None = Field(default=None, ge=1)
    name: str = Field(default="Untitled video", min_length=1, max_length=120)
    preset: VideoPreset = "none"
    mode: VideoMode = "generated"
    direction: str = Field(default="", max_length=2000)
    seed: int = Field(default=0, ge=0, le=2147483647)
    duration_sec: float | None = Field(default=None, ge=2, le=60)


class VideoRevisionRequest(VideoContract):
    revision: int = Field(ge=1)


class UpdateVideoProjectRequest(VideoRevisionRequest):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    mode: VideoMode | None = None
    direction: str | None = Field(default=None, max_length=2000)
    character_lock: bool | None = None
    seed: int | None = Field(default=None, ge=0, le=2147483647)
    settings: VideoProjectSettings | None = None
    export_settings: VideoExportSettings | None = None
    shots: list[VideoShotDraft] | None = Field(default=None, max_length=40)
    overlays: list[VideoOverlay] | None = Field(default=None, max_length=100)
    markers: list[VideoMarker] | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def unique_nonoverlapping(self) -> UpdateVideoProjectRequest:
        if self.shots is not None:
            ordered = sorted(self.shots, key=lambda shot: shot.start_sec)
            if len({shot.id for shot in ordered}) != len(ordered):
                raise ValueError("duplicate_shot_id")
            if any(
                nxt.start_sec < prev.start_sec + prev.seconds - 1e-6
                for prev, nxt in zip(ordered, ordered[1:])
            ):
                raise ValueError("overlap")
        return self


class VideoRenderRequest(VideoRevisionRequest):
    shot_ids: list[VideoId] = Field(default_factory=list, max_length=40)
    variants_per_shot: int = Field(default=1, ge=1, le=3)
    reuse_completed: bool = True


class ApproveVideoVariantRequest(VideoRevisionRequest):
    variant_id: VideoId


class VideoExportRequest(VideoRevisionRequest):
    settings: VideoExportSettings = Field(default_factory=VideoExportSettings)


class VideoSpeechLineRequest(VideoRevisionRequest):
    """Speak one line with a saved voice. The wav is an export track, not model audio."""

    profile_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    text: str = Field(min_length=1, max_length=500)


class VideoCharacter(VideoContract):
    """A local record: one consented voice plus one still. Not a video trainer."""

    id: VideoId
    name: str = Field(min_length=1, max_length=80)
    voice_profile_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    voice_name: str = Field(min_length=1, max_length=120)
    voice_ready: bool = False
    still_name: str = Field(min_length=1, max_length=160)
    still_width: int = Field(ge=1, le=8192)
    still_height: int = Field(ge=1, le=8192)
    still_url: str
    consent_confirmed: bool
    look: Literal["locked_still"] = "locked_still"
    created_at: str
    updated_at: str


class VideoCharactersResponse(VideoContract):
    characters: list[VideoCharacter]


class ApplyVideoCharacterRequest(VideoRevisionRequest):
    character_id: VideoId


class VideoCharacterTrainingJob(VideoContract):
    """A local LoRA job. mock means the photos were saved and nothing was trained."""

    id: VideoId
    name: str = Field(min_length=1, max_length=80)
    status: Literal["queued", "running", "completed", "mock_completed", "failed", "cancelled"]
    consent_confirmed: bool
    photo_count: int = Field(ge=3, le=12)
    clip_count: int = Field(ge=0, le=6)
    adapter_ready: bool = False
    mock: bool = False
    error_code: str = ""
    detail: str = Field(default="", max_length=500)
    created_at: str
    updated_at: str


class VideoCharacterTrainingResponse(VideoContract):
    jobs: list[VideoCharacterTrainingJob]


class VideoCharacterTrainerStatus(VideoContract):
    configured: bool
    source: Literal["env", "settings", "none"]
    command_name: str = ""
    missing: bool = False


class VideoCharacterTrainerSettingsRequest(VideoContract):
    command: str = Field(default="", max_length=500)


class ApplyVideoCharacterAdapterRequest(VideoRevisionRequest):
    training_id: VideoId


class VideoEngineOption(VideoContract):
    id: Literal["ltx23", "ltx25"]
    name: str
    available: bool
    reason: str
    total_bytes: int = Field(ge=0)
    uncached_bytes: int = Field(ge=0)
    free_bytes: int = Field(ge=0)
    model_revision: str
    text_revision: str
    warnings: list[str]


class VideoReadinessResponse(VideoContract):
    engine_ready: bool
    ffmpeg_ready: bool
    analysis_ready: bool
    overlay_ready: bool
    options: list[VideoEngineOption]
    modes: list[VideoMode]
    warnings: list[str]


VIDEO_CLIENT_MODELS: list[type[BaseModel]] = [
    VideoProject,
    VideoProjectsResponse,
    CreateVideoProjectRequest,
    UpdateVideoProjectRequest,
    VideoRevisionRequest,
    VideoRenderRequest,
    ApproveVideoVariantRequest,
    VideoExportRequest,
    VideoSpeechLineRequest,
    VideoCharacter,
    VideoCharactersResponse,
    ApplyVideoCharacterRequest,
    VideoCharacterTrainingJob,
    VideoCharacterTrainingResponse,
    VideoCharacterTrainerStatus,
    VideoCharacterTrainerSettingsRequest,
    ApplyVideoCharacterAdapterRequest,
    VideoSongAnalysis,
    VideoReadinessResponse,
]
