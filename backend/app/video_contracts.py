"""Typed persisted video workspace and public API contracts."""

from __future__ import annotations
import math
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .contracts import Contract
from .openrouter_contracts import OpenRouterReceipt, OpenRouterQuote
from .audio_quality_contracts import LoudnessSettings
from .export_provenance_contracts import ExportProvenance
from .voice_profile_contracts import CloudSpeechProvenance

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


class LocalVideoProviderConfig(VideoContract):
    provider: Literal['local'] = 'local'


class OpenRouterVideoProviderConfig(VideoContract):
    provider: Literal['openrouter'] = 'openrouter'
    model_id: str = Field(min_length=3, max_length=160, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.:-]*$')
    size: str = Field(min_length=7, max_length=15, pattern=r'^[0-9]{2,4}x[0-9]{2,4}$')
    # The studio keeps its original song/cast soundtrack. Provider audio is
    # disabled where supported and removed during checked local conformance.
    generate_audio: Literal[False] = False

    @field_validator('size')
    @classmethod
    def bounded_size(cls, value: str) -> str:
        width, height = (int(part) for part in value.split('x'))
        if not all(64 <= dimension <= 4096 and dimension % 2 == 0 for dimension in (width, height)):
            raise ValueError('cloud_size_unsupported')
        return value


VideoProviderConfig = Annotated[LocalVideoProviderConfig | OpenRouterVideoProviderConfig, Field(discriminator='provider')]


class VideoExportSettings(VideoContract):
    aspect: Literal["landscape", "portrait", "square"] = "landscape"
    quality: Literal["fast", "standard", "high"] = "standard"
    include_overlays: bool = True
    # Silent stays the default. Speech is muxed at export and is not a model input.
    attach_speech: bool = False
    loudness: LoudnessSettings = Field(default_factory=LoudnessSettings)
    visible_ai_label: bool = False


class VideoShotDraft(VideoContract):
    id: VideoId
    start_sec: float = Field(ge=0, le=21600)
    seconds: int = Field(default=4, ge=1, le=60)
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


class VideoCloudProvenance(VideoContract):
    receipt: OpenRouterReceipt
    remote_duration_sec: int = Field(ge=1,le=60)
    slot_duration_sec: int = Field(ge=1,le=60)
    trim_confirmed: bool = False
    source_duration_sec: float | None = Field(default=None,gt=0,le=61)
    received_sha256: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')
    requested_seed: int | None = Field(default=None,ge=0,le=2147483647)


class VideoVariant(VideoContract):
    id: VideoId
    seed: int = Field(ge=0, le=2147483647)
    status: Literal["queued", "running", "ready", "failed", "cancelled"] = "queued"
    error_code: str = ""
    fingerprint: str = ""
    file_url: str = ""
    poster_url: str = ""
    filmstrip_url: str = ""
    created_at: str
    prompt: str = ""
    settings: VideoProjectSettings = Field(default_factory=VideoProjectSettings)
    provider_config: VideoProviderConfig = Field(default_factory=LocalVideoProviderConfig)
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
    cloud: VideoCloudProvenance | None = None


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


class VideoDialogueCue(VideoContract):
    """Copied, completed cast audio; model generation stays silent."""

    shot_id: VideoId
    book_id: VideoId
    chapter_index: int = Field(ge=0, le=99)
    passage_id: VideoId
    source_revision: int = Field(ge=1)
    render_identity: str = Field(min_length=1, max_length=256)
    profile_id: VideoId
    speaker: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1200)
    language: str = Field(default="", max_length=35)
    source_start_ms: int = Field(ge=0)
    source_duration_ms: int | None = Field(default=None, ge=0, le=600000)
    source_end_ms: int = Field(gt=0)
    start_sec: float = Field(ge=0, le=15)
    end_sec: float = Field(gt=0, le=15)
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    waveform_peaks: list[Annotated[float, Field(ge=0, le=1)]] = Field(default_factory=list, max_length=160)
    renderer: Literal['local','openrouter'] | None = None
    cloud_provenance: CloudSpeechProvenance | None = None


class DialogueReelSelection(VideoContract):
    passage_id: VideoId
    clip_start_ms: int = Field(default=0, ge=0, le=600000)
    clip_end_ms: int | None = Field(default=None, gt=0, le=600000)
    prompt: str = Field(default="A character speaking naturally", min_length=1, max_length=2000)
    caption: str | None = Field(default=None, min_length=1, max_length=500)


class CreateDialogueReelRequest(VideoContract):
    book_id: VideoId
    chapter_index: int = Field(ge=0, le=99)
    revision: int = Field(ge=1)
    name: str = Field(default="Dialogue reel", min_length=1, max_length=120)
    selections: list[DialogueReelSelection] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def unique_passages(self) -> CreateDialogueReelRequest:
        if len({selection.passage_id for selection in self.selections}) != len(self.selections):
            raise ValueError("duplicate_passage")
        return self


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
    undo_available: bool = False
    redo_available: bool = False
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
    provider_config: VideoProviderConfig = Field(default_factory=LocalVideoProviderConfig)
    export_settings: VideoExportSettings = Field(default_factory=VideoExportSettings)
    shots: list[VideoProjectShot] = Field(default_factory=list, max_length=40)
    references: list[VideoReference] = Field(default_factory=list, max_length=6)
    speech_clip: VideoSpeechClip | None = None
    dialogue_cues: list[VideoDialogueCue] = Field(default_factory=list, max_length=4)
    overlays: list[VideoOverlay] = Field(default_factory=list, max_length=100)
    markers: list[VideoMarker] = Field(default_factory=list, max_length=4000)
    analysis: VideoSongAnalysis | None = None
    job: VideoProjectJob | None = None
    file_url: str = ""
    poster_url: str = ""
    output_version: str = ""
    export_provenance: ExportProvenance | None = None
    provenance_url: str = ''
    manifest_url: str = ''
    warnings: list[str] = Field(default_factory=list, max_length=30)


    @property
    def frame_size(self) -> tuple[int,int]:
        if self.provider_config.provider == 'openrouter':
            width,height = self.provider_config.size.split('x')
            return int(width),int(height)
        return self.settings.width,self.settings.height


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
    provider_config: VideoProviderConfig | None = None
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


class RefreshDialogueCueRequest(VideoRevisionRequest):
    source: CreateDialogueReelRequest

    @model_validator(mode="after")
    def one_cue(self) -> RefreshDialogueCueRequest:
        if len(self.source.selections) != 1:
            raise ValueError("one_dialogue_cue_required")
        return self


class VideoRenderRequest(VideoRevisionRequest):
    shot_ids: list[VideoId] = Field(default_factory=list, max_length=40)
    variants_per_shot: int = Field(default=1, ge=1, le=3)
    reuse_completed: bool = True


class VideoCloudQuoteRequest(VideoRevisionRequest):
    shot_id: VideoId
    remote_duration_sec: int = Field(ge=1,le=60)


class VideoCloudQuoteResponse(VideoContract):
    project_id: VideoId
    revision: int = Field(ge=1)
    shot_id: VideoId
    remote_duration_sec: int = Field(ge=1,le=60)
    slot_duration_sec: int = Field(ge=1,le=60)
    trim_required: bool
    quote: OpenRouterQuote


class VideoCloudSubmitRequest(VideoCloudQuoteRequest):
    quote_id: VideoId
    transfers_confirmed: bool = False
    trim_confirmed: bool = False


class VideoCloudResumeRequest(VideoRevisionRequest):
    variant_id: VideoId



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


class CharacterTrainingSettings(VideoContract):
    base_profile: Literal["ltx23"] = "ltx23"
    steps: int = Field(default=800, ge=100, le=3000)
    rank: int = Field(default=32, ge=8, le=64)


class CharacterDatasetItem(VideoContract):
    upload_index: int = Field(ge=0, le=17)
    caption: str = Field(min_length=1, max_length=500)
    role: Literal["training", "held_out"] = "training"

    @field_validator("caption")
    @classmethod
    def clean_caption(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("caption_required")
        return value.strip()


class CharacterDatasetReview(VideoContract):
    reviewed: bool
    items: list[CharacterDatasetItem] = Field(min_length=4, max_length=18)
    settings: CharacterTrainingSettings = Field(default_factory=CharacterTrainingSettings)

    @model_validator(mode="after")
    def reviewed_items(self) -> CharacterDatasetReview:
        if not self.reviewed:
            raise ValueError("dataset_review_required")
        if len({item.upload_index for item in self.items}) != len(self.items):
            raise ValueError("duplicate_dataset_item")
        if not any(item.role == "held_out" for item in self.items):
            raise ValueError("held_out_required")
        return self


class CharacterDatasetArtifact(VideoContract):
    path: str = Field(min_length=1, max_length=160)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    caption: str = Field(min_length=1, max_length=500)
    role: Literal["training", "held_out"]
    kind: Literal["photo", "clip"]


class CharacterTrainingRecipe(VideoContract):
    """Effective built-in recipe, rather than an inferred hardware quality tier."""
    width: int = Field(default=960, ge=64, le=4096)
    height: int = Field(default=544, ge=64, le=4096)
    frames: int = Field(default=97, ge=1, le=1000)
    frame_rate: int = Field(default=24, ge=1, le=60)
    learning_rate: float = Field(default=0.0002, gt=0, le=1)
    batch_size: int = Field(default=1, ge=1, le=64)
    optimizer: Literal['adamw'] = 'adamw'
    memory_mode: Literal['low_ram'] = 'low_ram'
    gradient_checkpointing: bool = True
    generate_audio: Literal[False] = False


class CharacterTrainingProvenance(VideoContract):
    engine_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    base_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    settings_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    settings: CharacterTrainingSettings
    recipe: CharacterTrainingRecipe | None = None
    artifacts: list[CharacterDatasetArtifact] = Field(min_length=4, max_length=18)
    comparison_prompts: list[str] = Field(min_length=1, max_length=4)
    evaluated: bool = False
    evaluation_notes: str = Field(default="", max_length=2000)
    evaluation_updated_at: str = ""


class VideoCharacterComparison(VideoContract):
    id: VideoId
    baseline_project_id: VideoId
    adapted_project_id: VideoId
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    held_out_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompts: list[str] = Field(min_length=1, max_length=4)
    seeds: list[int] = Field(min_length=1, max_length=4)
    created_at: str
    baseline_revision: int | None = None
    adapted_revision: int | None = None
    reviewed_media_sha256: list[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]] = Field(default_factory=list, max_length=8)
    reviewed_variant_ids: list[VideoId] = Field(default_factory=list, max_length=8)


class ReviewCharacterAdapterRequest(VideoContract):
    comparison_id: VideoId
    notes: str = Field(min_length=1, max_length=2000)
    reviewed: bool


class VideoCharacterTrainingJob(VideoContract):
    """A local LoRA job. mock means the photos were saved and nothing was trained."""

    id: VideoId
    name: str = Field(min_length=1, max_length=80)
    status: Literal["queued", "running", "completed", "mock_completed", "failed", "cancelled"]
    consent_confirmed: bool
    photo_count: int = Field(ge=3, le=12)
    clip_count: int = Field(ge=0, le=6)
    adapter_ready: bool = False
    provenance: CharacterTrainingProvenance | None = None
    comparison: VideoCharacterComparison | None = None
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
    dependencies_ready: bool = False
    reason: str = ""


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
    CreateDialogueReelRequest,
    RefreshDialogueCueRequest,
    VideoProjectsResponse,
    CreateVideoProjectRequest,
    UpdateVideoProjectRequest,
    VideoRevisionRequest,
    VideoRenderRequest,
    VideoCloudQuoteRequest,
    VideoCloudQuoteResponse,
    VideoCloudSubmitRequest,
    VideoCloudResumeRequest,
    ApproveVideoVariantRequest,
    VideoExportRequest,
    VideoSpeechLineRequest,
    VideoCharacter,
    VideoCharactersResponse,
    ApplyVideoCharacterRequest,
    VideoCharacterTrainingJob,
    CharacterDatasetReview,
    ReviewCharacterAdapterRequest,
    VideoCharacterTrainingResponse,
    VideoCharacterTrainerStatus,
    VideoCharacterTrainerSettingsRequest,
    ApplyVideoCharacterAdapterRequest,
    VideoSongAnalysis,
    VideoReadinessResponse,
]
