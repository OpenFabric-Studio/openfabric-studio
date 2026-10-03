"""App response envelopes shared with the generated browser contracts.

Project and mixer documents are deliberately opaque JSON to the backend;
their editor-owned domain shapes are validated by the browser before use.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .contracts import Contract, JsonValue, SavedTrack
from .orchestrator.state import ModelStatus
from .voice_contracts import (
    PrepareVoiceRequest, SelectVoiceSamplesRequest, VoicePreparationResponse,
    VoiceModelChoice, SelectVoiceModelRequest, VoiceTrialSourcesResponse, VoiceTrialSource,
    VoiceComparisonRequest, VoiceComparisonResponse, VoiceComparisonsResponse, VoiceTrialRating,
    VoiceSeparationOptionsResponse, AnalyzeVoiceCoverageRequest, VoiceCoverageSummary, VoiceCoverageMeasurement, VoicePitchBin,
    VoiceJobProgress,
)
from .video_contracts import VIDEO_CLIENT_MODELS
from .audio_version_contracts import AudioVersion, TrackAudioVersionsResponse, CreateAudioVersionRequest
from .audio_encoding import CLIENT_MODELS as AUDIO_CLIENT_MODELS
from .voice_profile_contracts import VOICE_PROFILE_CLIENT_MODELS
from .audiobook_contracts import AUDIOBOOK_CLIENT_MODELS

WorkStatus = Literal["idle", "queued", "running", "done", "failed", "cancelled"]
VoiceStatus = Literal["idle", "queued", "extracting", "cleaning", "preparing", "merging", "training", "ready", "failed", "cancelled"]
VideoStatus = Literal["queued", "running", "ready", "failed", "cancelled"]
MidiSource = Literal["full", "vocals", "drums", "bass", "other"]
ModelId = Literal["ace_step", "yue2"]


class CreateVoiceRequest(Contract):
    name: str = Field(min_length=1, max_length=80)


class ApplyVoiceRequest(Contract):
    voice_id: str
    track_id: int


class BuildVoiceRequest(Contract):
    clean: bool = False
    preparation_revision: str | None = Field(default=None, max_length=128)
    training_steps: Literal[0, 200, 500, 1000] = 200
    resume: bool = False
    compare_checkpoints: bool = False

    @model_validator(mode="after")
    def valid_training_mode(self) -> BuildVoiceRequest:
        if self.resume and (self.compare_checkpoints or self.training_steps == 0):
            raise ValueError("invalid_resume_mode")
        if self.compare_checkpoints and self.training_steps == 0:
            raise ValueError("invalid_comparison_mode")
        return self


class PlanRequest(Contract):
    track_id: int


class ShotRequest(Contract):
    start_sec: float = 0
    seconds: int = 4
    prompt: str = ""


class CreateVideoRequest(Contract):
    track_id: int
    prompt: str = ""
    seconds: int = 4
    start_sec: float = 0
    shots: list[ShotRequest] | None = None
    stage1_steps: int = 30
    stage2_steps: int = Field(default=3, ge=1, le=3)
    cfg_scale: float = 3.0
    width: int = 704
    height: int = 448


class LibraryRequest(Contract):
    data_dir: str


class SwitchRequest(Contract):
    model: str


class ProjectCreateRequest(Contract):
    name: str = "Untitled project"
    data: dict[str, JsonValue] = Field(default_factory=dict)


class ProjectUpdateRequest(Contract):
    name: str | None = None
    data: dict[str, JsonValue] | None = None


class VoiceRecording(Contract):
    filename: str
    bytes: int = Field(ge=0)


class VoiceExtractReport(Contract):
    songs: int = Field(ge=0)
    skipped: int = Field(ge=0)
    cleaned: bool
    gaps_shortened: bool
    extracted_sec: float = Field(ge=0)
    kept_sec: float = Field(ge=0)
    kept_pieces: int = Field(ge=0)
    dropped: int = Field(ge=0)
    trimmed: int = Field(ge=0)
    max_gap_sec: float = Field(ge=0)
    level_db: float
    pitch: float = Field(ge=0)
    peak: float = Field(ge=0)
    quality: Literal["clear", "usable", "weak"]
    notes: list[str]


class VoiceProfileResponse(Contract):
    id: str
    name: str
    created_at: str
    recordings: list[VoiceRecording]
    status: VoiceStatus
    stage: str
    detail: str
    progress_current: int = Field(ge=0)
    progress_total: int = Field(ge=0)
    error: str
    error_code: str
    trained_steps: int = Field(ge=0)
    built_from: list[str]
    has_preview: bool
    usable: bool
    clean_vocals: bool = False
    prepared: bool = False
    prep_kept_sec: float = Field(default=0, ge=0)
    prep_total_sec: float = Field(default=0, ge=0)
    extract_report: VoiceExtractReport | None = None
    file_percent: float | None = Field(default=None, ge=0, le=100)
    models: list[VoiceModelChoice] = Field(default_factory=list)
    active_model_id: str = "trained"
    job_progress: VoiceJobProgress | None = None


class VoicesResponse(Contract):
    voices: list[VoiceProfileResponse]


class VoiceUploadResponse(Contract):
    voice: VoiceProfileResponse
    saved: list[str]
    skipped: list[str]


class ApplyStatusResponse(Contract):
    status: WorkStatus
    error: str
    error_code: str
    audio_url: str
    phase: Literal["", "waiting", "separating", "preparing", "loading", "analyzing", "converting", "mixing"] = ""
    voice_id: str = ""
    voice_name: str = ""
    started_at: float = Field(default=0, ge=0)
    duration_sec: float = Field(default=0, ge=0)
    job_progress: VoiceJobProgress | None = None


class VoiceReplacementResponse(Contract):
    track: SavedTrack
    source_track: SavedTrack
    application: ApplyStatusResponse


class VideoShot(Contract):
    start_sec: float = Field(ge=0)
    seconds: int = Field(ge=1)
    prompt: str


class VideoPlanResponse(Contract):
    track_id: int
    title: str
    duration_sec: float = Field(ge=0)
    shots: list[VideoShot]


class VideoJobResponse(Contract):
    id: str
    created_at: str
    track_id: int
    title: str
    prompt: str
    seconds: int = Field(ge=1)
    start_sec: float = Field(ge=0)
    status: VideoStatus
    error: str
    error_code: str
    file_url: str
    shots: list[VideoShot]
    shot_index: int = Field(ge=0)
    shot_count: int = Field(ge=0)
    progress_current: int = Field(ge=0)
    progress_total: int = Field(ge=0)
    phase: Literal["", "download", "denoise", "mux", "starting"]
    stage1_steps: int = Field(default=30, ge=1)
    stage2_steps: int = Field(default=3, ge=1)
    cfg_scale: float = Field(default=3, ge=0)
    width: int = Field(default=704, ge=1)
    height: int = Field(default=448, ge=1)


class VideosResponse(Contract):
    videos: list[VideoJobResponse]


class VideoActivityResponse(Contract):
    busy: bool


class StemsStatusResponse(Contract):
    status: WorkStatus
    error: str | None
    stems: dict[str, str] | None = None


class MidiSourceState(Contract):
    status: WorkStatus
    error: str | None


class MidiSources(Contract):
    full: MidiSourceState
    vocals: MidiSourceState
    drums: MidiSourceState
    bass: MidiSourceState
    other: MidiSourceState


class MidiStatusResponse(Contract):
    sources: MidiSources
    urls: dict[str, str]
    available: list[MidiSource]


class ProjectSummaryResponse(Contract):
    id: int
    created_at: str
    updated_at: str
    name: str


class ProjectFullResponse(ProjectSummaryResponse):
    data: dict[str, JsonValue]


class ProjectsResponse(Contract):
    data: list[ProjectSummaryResponse]


class MixSettingsResponse(Contract):
    settings: dict[str, JsonValue] | None


class ModelStatusEntry(Contract):
    id: ModelId
    label: str
    status: ModelStatus
    error: str | None


class ModelStates(Contract):
    ace_step: ModelStatusEntry
    yue2: ModelStatusEntry


class OrchestratorStatusResponse(Contract):
    active_model: ModelId | None
    models: ModelStates


class Yue2ModelSpecConfig(Contract):
    id: str
    family: str
    path: str
    task: str
    mode: str
    model_spec_override: str | None = None


class Yue2ModelSpecs(Contract):
    yue2: Yue2ModelSpecConfig
    sheetsage2: Yue2ModelSpecConfig
    muscriptor: Yue2ModelSpecConfig


class OrchestratorConfigResponse(Contract):
    yue2_specs: Yue2ModelSpecs


class UploadDatasetFilesResponse(Contract):
    audio_dir: str
    saved: list[str]
    skipped: list[str]


class LibraryFolderInfo(Contract):
    path: str
    key: Literal[
        "tracks", "voices", "videos", "audiobooks", "speechProfiles",
        "speechTrials", "models", "logs", "database",
    ]


class LibraryStatusResponse(Contract):
    data_dir: str
    pending_data_dir: str
    restart_required: bool
    error: str
    can_pick: bool = False
    folders: list[LibraryFolderInfo]


class LibraryPickResponse(Contract):
    path: str


EXTRA_CLIENT_MODELS: list[type[BaseModel]] = [
    AudioVersion, TrackAudioVersionsResponse, CreateAudioVersionRequest,
    *AUDIO_CLIENT_MODELS,
    *VIDEO_CLIENT_MODELS,
    PrepareVoiceRequest, SelectVoiceSamplesRequest, VoicePreparationResponse,
    VoiceModelChoice, SelectVoiceModelRequest, VoiceTrialSourcesResponse, VoiceTrialSource,
    VoiceComparisonRequest, VoiceComparisonResponse, VoiceComparisonsResponse, VoiceTrialRating,
    VoiceSeparationOptionsResponse, AnalyzeVoiceCoverageRequest, VoiceCoverageSummary, VoiceCoverageMeasurement, VoicePitchBin,
    VoiceJobProgress,
    CreateVoiceRequest, ApplyVoiceRequest, BuildVoiceRequest, PlanRequest, ShotRequest,
    CreateVideoRequest, LibraryRequest, SwitchRequest, ProjectCreateRequest, ProjectUpdateRequest,
    VoiceProfileResponse, VoicesResponse, VoiceUploadResponse, ApplyStatusResponse, VoiceReplacementResponse,
    VideoJobResponse, VideosResponse, VideoPlanResponse, VideoActivityResponse,
    StemsStatusResponse, MidiStatusResponse, ProjectSummaryResponse, ProjectFullResponse,
    ProjectsResponse, MixSettingsResponse, OrchestratorStatusResponse, OrchestratorConfigResponse,
    UploadDatasetFilesResponse, LibraryStatusResponse, LibraryPickResponse,
    *VOICE_PROFILE_CLIENT_MODELS,
    *AUDIOBOOK_CLIENT_MODELS,
]
