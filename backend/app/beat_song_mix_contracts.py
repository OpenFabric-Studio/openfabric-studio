"""Reviewed beat/song assembly; source seconds map into destination seconds.

Roles and leadership are operator choices, not automatic instrument detection.
Public source snapshots contain relative asset names and recorded evidence only.
"""
from __future__ import annotations

from typing import Annotated, Literal, Self
from pydantic import Field, model_validator

from .audio_quality_contracts import AudioMetrics, LoudnessSettings
from .beat_contracts import BeatContract, BeatDocument, BeatId, BeatIdentifier, BeatMeter, BeatRevision, BeatSha256
from .export_provenance_contracts import ContentOrigin, MusicEvidence
from .music_analysis_contracts import VersionMusicSource
from .music_conditioning_contracts import BeatRenderConditioningSource
from .music_dsp_contracts import DuckSettings, EqBand, WarpAnchor, validate_anchors

SongSource = Annotated[VersionMusicSource | BeatRenderConditioningSource, Field(discriminator='kind')]
MixErrorCode = Literal['song_source_missing', 'song_source_changed', 'song_conditioning_invalid',
    'beat_mix_alignment_invalid', 'beat_mix_key_review_required', 'beat_mix_drums_required',
    'beat_mix_stretch_required', 'beat_mix_invalid', 'beat_mix_not_found', 'beat_mix_revision_conflict',
    'beat_mix_submission_conflict', 'beat_mix_busy', 'beat_mix_budget_exceeded', 'beat_mix_render_failed',
    'song_save_failed', 'song_cleanup_failed', 'song_storage_unavailable', 'insufficient_disk']
MixWarning = Literal['manual_anchors', 'key_unknown', 'audio_pitch_approximation', 'audio_loop_approximation',
    'separation_bleed_possible', 'rights_unknown', 'hard_clip_risk', 'processed_stems_before_master',
    'authored_repeats_recompiled', 'tails_trimmed_to_song', 'chunked_warp_review']
Submission = Annotated[str, Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')]
Seconds = Annotated[float, Field(ge=0, le=600)]
LaneRole = Literal['drum', 'bass', 'pitched', 'vocals', 'other', 'mix']


class BeatSongMixError(Exception):
    def __init__(self, code: MixErrorCode) -> None:
        self.code = code
        super().__init__(code)


class BeatSongMixFailure(BeatContract):
    code: MixErrorCode


class SongSourceRequest(BeatContract):
    source: SongSource


class SongSourceInput(SongSourceRequest):
    sha256: BeatSha256
    excerpt_start_sec: float = Field(default=0, ge=0, le=21600)
    excerpt_end_sec: float = Field(gt=0, le=21600)

    @model_validator(mode='after')
    def excerpt(self) -> Self:
        if not 0 < self.excerpt_end_sec - self.excerpt_start_sec <= 600:
            raise ValueError('song_conditioning_invalid')
        if self.source.kind == 'beat_render' and self.source.sha256 != self.sha256:
            raise ValueError('song_source_changed')
        return self


class SongSourceInfo(SongSourceRequest):
    sha256: BeatSha256
    source_bytes: int = Field(ge=1, le=8589934592)
    sample_rate_hz: int = Field(ge=8000, le=192000)
    channels: int = Field(ge=1, le=2)
    frames: int = Field(ge=1, le=4147200000)
    duration_sec: float = Field(gt=0, le=21600)
    musical_duration_sec: float | None = Field(default=None, gt=0, le=600)
    track_id: int = Field(ge=1, le=9007199254740991)
    version_id: BeatId
    music_evidence: MusicEvidence
    content_origin: ContentOrigin
    beat_document: BeatDocument | None = None


class SongSourceSnapshot(SongSourceInfo):
    source_id: BeatId
    raw_filename: str = Field(pattern=r'^raw\.(wav|mp3|flac|ogg|m4a)$', max_length=16)


class BeatSongLaneControl(BeatContract):
    lane_id: BeatId
    muted: bool = False
    gain_db: float = Field(default=0, ge=-12.0412, le=12.0412)
    pan: float = Field(default=0, ge=-1, le=1)
    highpass_hz: float | None = Field(default=None, ge=20, le=500)
    eq_bands: list[EqBand] = Field(default_factory=list, max_length=10)
    duck: DuckSettings | None = None


class BeatSongLane(BeatContract):
    id: BeatId
    label: str = Field(min_length=1, max_length=100)
    source: Literal['beat', 'song']
    role: LaneRole
    beat_lane_id: BeatIdentifier | None = None
    control: BeatSongLaneControl


class BeatSongSymbolicOrigin(BeatContract):
    original: BeatRenderConditioningSource
    semitones: int = Field(ge=-12, le=12)
    musical_sha256: BeatSha256


class BeatSongTransposeRequest(BeatContract):
    source: BeatRenderConditioningSource
    semitones: int = Field(ge=-12, le=12)


class BeatSongTransposePreview(BeatContract):
    document: BeatDocument
    symbolic_origin: BeatSongSymbolicOrigin


class BeatSongMixDraft(BeatContract):
    title: str = Field(min_length=1, max_length=120, pattern=r'\S')
    beat: SongSourceInput
    song: SongSourceInput
    mode: Literal['layer', 'replace_drums'] = 'layer'
    tempo_strategy: Literal['beat_to_song', 'song_to_beat'] = 'beat_to_song'
    beat_repeats: int = Field(default=1, ge=1, le=128)
    anchors: list[WarpAnchor] = Field(min_length=2, max_length=4096)
    project_bpm: float = Field(ge=20, le=300)
    project_meter: BeatMeter
    bass_leader: Literal['song', 'beat', 'none'] = 'none'
    kick_leader: Literal['song', 'beat', 'none'] = 'none'
    controls: list[BeatSongLaneControl] = Field(default_factory=list, max_length=32)
    loudness: LoudnessSettings = Field(default_factory=LoudnessSettings)
    audio_pitch_semitones: float = Field(default=0, ge=-12, le=12)
    audio_approximation: bool = False
    key_review: Literal['unknown', 'operator_reviewed'] = 'unknown'
    symbolic_origin: BeatSongSymbolicOrigin | None = None

    @model_validator(mode='after')
    def map(self) -> Self:
        validate_anchors(self.anchors)
        if self.anchors[0].source_sec != 0 or self.anchors[0].target_sec != 0:
            raise ValueError('beat_mix_alignment_invalid')
        if self.anchors[-1].source_sec > 600 or self.anchors[-1].target_sec > 600:
            raise ValueError('beat_mix_alignment_invalid')
        if len({item.lane_id for item in self.controls}) != len(self.controls):
            raise ValueError('beat_mix_invalid')
        return self


class BeatSongMixPlan(BeatContract):
    id: BeatId
    revision: BeatRevision
    draft: BeatSongMixDraft
    beat: SongSourceInfo
    song: SongSourceInfo
    lanes: list[BeatSongLane] = Field(min_length=2, max_length=32)
    source_duration_sec: float = Field(gt=0, le=600)
    target_duration_sec: float = Field(gt=0, le=600)
    minimum_ratio: float = Field(ge=.75, le=1.5)
    maximum_ratio: float = Field(ge=.75, le=1.5)
    needs_native: bool
    separator_ready: bool
    native_ready: bool
    warnings: list[MixWarning] = Field(max_length=16)
    sha256: BeatSha256
    created_at: str = Field(min_length=20, max_length=40)


class BeatSongMixStorage(BeatContract):
    retained_bytes: int = Field(ge=0, le=137438953472)
    temporary_bytes: int = Field(ge=0, le=137438953472)
    required_free_bytes: int = Field(ge=0, le=274877906944)
    free_bytes: int | None = Field(default=None, ge=0)
    can_start: bool


class BeatSongMixPreview(BeatContract):
    plan: BeatSongMixPlan
    storage: BeatSongMixStorage


class BeatSongMixUpdate(BeatContract):
    expected_revision: BeatRevision
    draft: BeatSongMixDraft


class BeatSongMixStartRequest(BeatContract):
    plan_id: BeatId
    expected_revision: BeatRevision
    plan_sha256: BeatSha256
    submission_key: Submission
    mode: Literal['preview', 'full'] = 'full'
    preview_start_sec: Seconds | None = None
    preview_end_sec: Seconds | None = None

    @model_validator(mode='after')
    def region(self) -> Self:
        if self.mode == 'preview':
            if (self.preview_start_sec is None or self.preview_end_sec is None
                    or not 0 < self.preview_end_sec - self.preview_start_sec <= 60):
                raise ValueError('beat_mix_invalid')
        elif self.preview_start_sec is not None or self.preview_end_sec is not None:
            raise ValueError('beat_mix_invalid')
        return self


class BeatSongMixArtifact(BeatContract):
    id: BeatId
    kind: Literal['mix', 'processed', 'dry', 'plan', 'provenance', 'bundle']
    filename: str = Field(min_length=1, max_length=160, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
    sha256: BeatSha256
    byte_count: int = Field(ge=1, le=17179869184)
    media_type: Literal['audio/wav', 'application/json', 'application/zip']
    lane_id: BeatId | None = None
    frames: int | None = Field(default=None, ge=1, le=28800000)


class BeatSongRenderedLane(BeatContract):
    lane_id: BeatId
    artifact_id: BeatId
    dry_artifact_id: BeatId


class BeatSongMixManifest(BeatContract):
    job_id: BeatId
    plan_id: BeatId
    plan_revision: BeatRevision
    plan_sha256: BeatSha256
    mode: Literal['preview', 'full']
    frames: int = Field(ge=1, le=28800000)
    sample_rate_hz: Literal[48000] = 48000
    channels: Literal[2] = 2
    sources: list[SongSourceSnapshot] = Field(min_length=2, max_length=2)
    artifacts: list[BeatSongMixArtifact] = Field(min_length=4, max_length=100)
    lanes: list[BeatSongRenderedLane] = Field(min_length=2, max_length=32)
    metrics: AudioMetrics
    music_evidence: MusicEvidence
    warnings: list[MixWarning] = Field(max_length=16)
    recipe: Literal['reviewed-beat-song-v1'] = 'reviewed-beat-song-v1'
    track_id: int | None = Field(default=None, ge=1, le=9007199254740991)
    project_id: int | None = Field(default=None, ge=1, le=9007199254740991)


class BeatSongMixProgress(BeatContract):
    phase: Literal['queued', 'snapshotting', 'separating', 'warping', 'mixing', 'publishing']
    completed: int = Field(default=0, ge=0, le=10000)
    total: int | None = Field(default=None, ge=1, le=10000)


class BeatSongMixJob(BeatContract):
    id: BeatId
    plan: BeatSongMixPlan
    request: BeatSongMixStartRequest
    status: Literal['queued', 'running', 'cancelling', 'cancelled', 'done', 'failed', 'interrupted', 'blocked', 'saving']
    progress: BeatSongMixProgress
    manifest: BeatSongMixManifest | None = None
    error: BeatSongMixFailure | None = None
    created_at: str = Field(min_length=20, max_length=40)
    started_at: str | None = Field(default=None, min_length=20, max_length=40)
    finished_at: str | None = Field(default=None, min_length=20, max_length=40)

    @model_validator(mode='after')
    def identity(self) -> Self:
        if (self.request.plan_id != self.plan.id or self.request.expected_revision != self.plan.revision
                or self.request.plan_sha256 != self.plan.sha256):
            raise ValueError('beat_mix_invalid')
        if self.manifest is not None and (self.manifest.job_id != self.id or self.manifest.plan_id != self.plan.id
                or self.manifest.plan_revision != self.plan.revision or self.manifest.plan_sha256 != self.plan.sha256
                or self.manifest.mode != self.request.mode):
            raise ValueError('beat_mix_invalid')
        return self


class BeatSongMixJobs(BeatContract):
    jobs: list[BeatSongMixJob] = Field(max_length=100)


BEAT_SONG_MIX_CLIENT_MODELS = (SongSourceRequest, SongSourceInput, SongSourceInfo, SongSourceSnapshot,
    BeatSongMixDraft, BeatSongMixUpdate, BeatSongMixPreview, BeatSongMixPlan, BeatSongMixStartRequest,
    BeatSongMixJob, BeatSongMixJobs, BeatSongMixManifest, BeatSongMixFailure,
    BeatSongTransposeRequest, BeatSongTransposePreview)
