"""Bounded mashup documents. Musical timing is declared, never inferred here.

Frame coordinates refer to each retained 48 kHz excerpt; target ticks use PPQ
960 and quarter-note BPM. Scores are versioned heuristics, not confidence.
"""
from __future__ import annotations

from typing import Annotated, Literal, Self
from pydantic import BaseModel, Field, model_validator

from .audio_quality_contracts import AudioMetrics
from .beat_contracts import BeatContract, BeatId, BeatIdentifier, BeatMeter, BeatRevision, BeatSha256
from .export_provenance_contracts import ContentOrigin, MusicEvidence
from .music_analysis_contracts import KeyEstimate, StemMusicSource, VersionMusicSource

SourceSide = Literal['a', 'b']
MashupStem = Literal['vocals', 'drums', 'bass', 'other']
Orientation = Literal['a_vocals_b_backing', 'b_vocals_a_backing']
TempoAlias = Literal['half', 'declared', 'double']
Frame = Annotated[int, Field(ge=0, le=57600000)]
Tick = Annotated[int, Field(ge=0, le=5760000)]
Timestamp = Annotated[str, Field(min_length=20, max_length=64)]
Submission = Annotated[str, Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')]


def _declared_alias() -> list[TempoAlias]:
    return ['declared']
MashupErrorCode = Literal['mashup_invalid', 'mashup_not_found', 'mashup_job_not_found',
    'mashup_revision_conflict', 'mashup_submission_conflict', 'mashup_source_changed',
    'mashup_source_missing', 'mashup_analysis_incomplete', 'mashup_analysis_changed',
    'mashup_alignment_unsafe', 'mashup_pitch_override_required', 'mashup_lock_conflict',
    'mashup_separation_needed', 'mashup_separation_failed', 'mashup_stretch_needed',
    'mashup_busy', 'mashup_budget_exceeded', 'mashup_worker_unverified',
    'mashup_render_failed', 'mashup_save_failed', 'mashup_storage_unavailable', 'insufficient_disk']
MashupWarning = Literal['analysis_incomplete', 'manual_timing', 'key_unknown', 'meter_mismatch',
    'large_pitch_shift', 'stretch_risk', 'transition_review', 'operator_stems_unverified',
    'separation_bleed_possible', 'rights_unknown', 'hard_clip_risk', 'tempo_alias_selected']


class MashupError(Exception):
    def __init__(self, code: MashupErrorCode, conflicting_ids: list[str] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.conflicting_ids = conflicting_ids or []


class MashupFailure(BeatContract):
    code: MashupErrorCode
    conflicting_ids: list[BeatIdentifier] = Field(default_factory=list, max_length=256)


class MashupAnalysisRef(BeatContract):
    job_id: BeatId
    result_id: BeatId
    override_revision: BeatRevision
    configuration_sha256: BeatSha256


class MashupSourcePreviewRequest(BeatContract):
    source: VersionMusicSource
    excerpt_start_sec: float = Field(default=0, ge=0, le=21600)
    excerpt_end_sec: float = Field(gt=0, le=21600)

    @model_validator(mode='after')
    def excerpt(self) -> Self:
        if not 0 < self.excerpt_end_sec - self.excerpt_start_sec <= 600:
            raise ValueError('mashup_invalid')
        return self


class MashupSourceInput(MashupSourcePreviewRequest):
    sha256: BeatSha256
    analysis: MashupAnalysisRef | None = None
    # An explicit operator association is recorded as unverified. A cached
    # stems_json entry is never implicitly associated with a selected version.
    operator_stems: list[StemMusicSource] = Field(default_factory=list, max_length=4)

    @model_validator(mode='after')
    def stems(self) -> Self:
        if self.operator_stems and ({item.stem for item in self.operator_stems} != {'vocals', 'drums', 'bass', 'other'}
                or len(self.operator_stems) != 4):
            raise ValueError('mashup_invalid')
        return self


class MashupSourcePreview(BeatContract):
    sha256: BeatSha256
    sample_rate_hz: int = Field(ge=8000, le=192000)
    channels: int = Field(ge=1, le=2)
    duration_sec: float = Field(gt=0, le=21600)
    source_bytes: int = Field(ge=1, le=8589934592)
    retained_bytes: int = Field(ge=0, le=68719476736)
    temporary_bytes: int = Field(ge=0, le=68719476736)
    required_free_bytes: int = Field(ge=0, le=137438953472)
    free_bytes: int | None = Field(default=None, ge=0)
    can_start: bool
    separator_ready: bool


class MashupCreateRequest(BeatContract):
    title: str = Field(min_length=1, max_length=120)
    submission_key: Submission
    source_a: MashupSourceInput
    source_b: MashupSourceInput
    separation_quality: Literal['fast', 'high'] = 'fast'

    @model_validator(mode='after')
    def different_sources(self) -> Self:
        if self.source_a.source == self.source_b.source:
            raise ValueError('mashup_invalid')
        return self


class MashupTimingSection(BeatContract):
    id: BeatIdentifier
    start_frame: Frame
    end_frame: Frame
    bars: int = Field(ge=1, le=256)
    musical_label: Literal['intro', 'verse', 'chorus', 'bridge', 'outro', 'instrumental'] | None = None

    @model_validator(mode='after')
    def forward(self) -> Self:
        if self.end_frame <= self.start_frame:
            raise ValueError('mashup_invalid')
        return self


class MashupTiming(BeatContract):
    basis: Literal['operator_confirmed'] = 'operator_confirmed'
    bpm: float = Field(ge=20, le=300)
    meter: BeatMeter
    downbeat_frames: list[Frame] = Field(min_length=2, max_length=4096)
    sections: list[MashupTimingSection] = Field(min_length=1, max_length=64)
    key: KeyEstimate = Field(default_factory=KeyEstimate)
    supersedes_analysis: MashupAnalysisRef | None = None

    @model_validator(mode='after')
    def boundaries(self) -> Self:
        boundaries = self.downbeat_frames
        if boundaries[0] != 0 or any(a >= b for a, b in zip(boundaries, boundaries[1:])):
            raise ValueError('mashup_invalid')
        indexes = {frame: index for index, frame in enumerate(boundaries)}
        if len({section.id for section in self.sections}) != len(self.sections):
            raise ValueError('mashup_invalid')
        end = 0
        for section in self.sections:
            if (section.start_frame != end or section.start_frame not in indexes or section.end_frame not in indexes
                    or indexes[section.end_frame] - indexes[section.start_frame] != section.bars):
                raise ValueError('mashup_invalid')
            end = section.end_frame
        if end != boundaries[-1]:
            raise ValueError('mashup_invalid')
        return self


class MashupTimingUpdate(BeatContract):
    expected_revision: BeatRevision
    source: SourceSide
    timing: MashupTiming


class MashupAudioArtifact(BeatContract):
    id: BeatId
    filename: str = Field(min_length=1, max_length=160, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
    sha256: BeatSha256
    byte_count: int = Field(ge=1, le=8589934592)
    frames: Frame = Field(ge=1)
    sample_rate_hz: Literal[48000] = 48000
    channels: Literal[2] = 2


class MashupStemSnapshot(BeatContract):
    stem: MashupStem
    artifact: MashupAudioArtifact


class MashupSourceSnapshot(BeatContract):
    side: SourceSide
    input: MashupSourceInput
    raw_filename: str = Field(min_length=1, max_length=80, pattern=r'^raw\.(wav|mp3|flac|ogg|m4a)$')
    source_bytes: int = Field(ge=1, le=8589934592)
    duration_sec: float = Field(gt=0, le=21600)
    prepared: MashupAudioArtifact | None = None
    stems: list[MashupStemSnapshot] = Field(default_factory=list, max_length=4)
    association: Literal['fresh_separation', 'operator_declared_unverified']
    content_origin: ContentOrigin = 'unknown'
    preparation_recipe: Literal['ffmpeg-excerpt-demucs-local-v1', 'ffmpeg-excerpt-operator-stems-v1'] = 'ffmpeg-excerpt-demucs-local-v1'
    separation_quality: Literal['fast', 'high'] = 'fast'
    music_evidence: MusicEvidence
    analysis_key: KeyEstimate = Field(default_factory=KeyEstimate)
    timing: MashupTiming | None = None

    @model_validator(mode='after')
    def snapshot_bounds(self) -> Self:
        if self.input.excerpt_end_sec > self.duration_sec + .001:
            raise ValueError('mashup_invalid')
        if len({item.stem for item in self.stems}) != len(self.stems):
            raise ValueError('mashup_invalid')
        if self.prepared is not None:
            if any(item.artifact.frames != self.prepared.frames for item in self.stems):
                raise ValueError('mashup_invalid')
            if self.timing is not None and self.timing.downbeat_frames[-1] != self.prepared.frames:
                raise ValueError('mashup_invalid')
        return self


class MashupCompatibilityComponent(BeatContract):
    available: bool
    score: float | None = Field(default=None, ge=0, le=100)
    measurement: float | None = None
    reason: Literal['available', 'timing_missing', 'key_missing', 'vocal_activity_missing', 'meter_mismatch']


class MashupCandidate(BeatContract):
    orientation: Orientation
    scoring_version: Literal['mashup-heuristic-v1'] = 'mashup-heuristic-v1'
    score: float | None = Field(default=None, ge=0, le=100)
    key: MashupCompatibilityComponent
    tempo: MashupCompatibilityComponent
    section_fit: MashupCompatibilityComponent
    energy_clash: MashupCompatibilityComponent
    vocal_pitch_semitones: int | None = Field(default=None, ge=-12, le=12)
    duration_ratio: float | None = Field(default=None, gt=0, le=15)
    tempo_aliases: list[TempoAlias] = Field(default_factory=_declared_alias, max_length=3)
    warnings: list[MashupWarning] = Field(default_factory=list, max_length=16)


class MashupPlacement(BeatContract):
    id: BeatIdentifier
    source: SourceSide
    stem: MashupStem
    section_id: BeatIdentifier
    source_start_frame: Frame
    source_end_frame: Frame
    target_start_tick: Tick
    target_end_tick: Tick
    pitch_semitones: float = Field(default=0, ge=-12, le=12)
    gain_db: float = Field(default=0, ge=-12.0412, le=12.0412)
    fade_in_sec: float = Field(default=0, ge=0, le=10)
    fade_out_sec: float = Field(default=0, ge=0, le=10)
    locked: bool = False

    @model_validator(mode='after')
    def forward(self) -> Self:
        if self.source_end_frame <= self.source_start_frame or self.target_end_tick <= self.target_start_tick:
            raise ValueError('mashup_invalid')
        return self


class MashupPlanLock(BeatContract):
    placement_id: BeatIdentifier
    target_start_tick: Tick


class MashupGeneratePlanRequest(BeatContract):
    expected_revision: BeatRevision
    orientation: Orientation
    target_bpm: float = Field(ge=20, le=300)
    meter: BeatMeter
    target_key: KeyEstimate = Field(default_factory=KeyEstimate)
    vocal_tempo_alias: TempoAlias = 'declared'
    backing_tempo_alias: TempoAlias = 'declared'
    vocal_pitch_semitones: float = Field(default=0, ge=-12, le=12)
    backing_pitch_semitones: float = Field(default=0, ge=-12, le=12)
    allow_large_pitch_shift: bool = False
    locks: list[MashupPlanLock] = Field(default_factory=list, max_length=256)

    @model_validator(mode='after')
    def explicit_pitch(self) -> Self:
        if max(abs(self.vocal_pitch_semitones), abs(self.backing_pitch_semitones)) > 3 and not self.allow_large_pitch_shift:
            raise ValueError('mashup_pitch_override_required')
        if len({lock.placement_id for lock in self.locks}) != len(self.locks):
            raise ValueError('mashup_invalid')
        return self


class MashupPlan(BeatContract):
    id: BeatId
    revision: BeatRevision
    settings: MashupGeneratePlanRequest
    placements: list[MashupPlacement] = Field(min_length=1, max_length=256)
    duration_ticks: Tick = Field(gt=0)
    warnings: list[MashupWarning] = Field(default_factory=list, max_length=16)
    algorithm: Literal['monotone-sections-v1'] = 'monotone-sections-v1'


class MashupEditPlanRequest(BeatContract):
    expected_revision: BeatRevision
    placements: list[MashupPlacement] = Field(min_length=1, max_length=256)


class MashupState(BeatContract):
    id: BeatId
    revision: BeatRevision
    title: str = Field(min_length=1, max_length=120)
    status: Literal['preparing', 'ready', 'failed', 'cancelled', 'interrupted']
    source_a: MashupSourceSnapshot
    source_b: MashupSourceSnapshot
    candidates: list[MashupCandidate] = Field(default_factory=list, max_length=2)
    plan: MashupPlan | None = None
    active_job_id: BeatId | None = None
    created_at: Timestamp
    updated_at: Timestamp
    error: MashupFailure | None = None

    @model_validator(mode='after')
    def source_identity(self) -> Self:
        if (self.source_a.side != 'a' or self.source_b.side != 'b'
                or self.source_a.input.source == self.source_b.input.source
                or (self.plan is not None and self.plan.revision > self.revision)):
            raise ValueError('mashup_invalid')
        return self


class MashupStates(BeatContract):
    documents: list[MashupState] = Field(max_length=100)


class MashupJobRequest(BeatContract):
    expected_revision: BeatRevision
    submission_key: Submission
    mode: Literal['preview', 'full'] = 'full'
    preview_start_tick: Tick | None = None
    preview_end_tick: Tick | None = None

    @model_validator(mode='after')
    def preview_range(self) -> Self:
        if self.mode == 'preview':
            if self.preview_start_tick is None or self.preview_end_tick is None or self.preview_end_tick <= self.preview_start_tick:
                raise ValueError('mashup_invalid')
        elif self.preview_start_tick is not None or self.preview_end_tick is not None:
            raise ValueError('mashup_invalid')
        return self


class MashupStoragePreview(BeatContract):
    retained_bytes: int = Field(ge=0, le=68719476736)
    temporary_bytes: int = Field(ge=0, le=68719476736)
    required_free_bytes: int = Field(ge=0, le=137438953472)
    free_bytes: int | None = Field(default=None, ge=0)
    can_start: bool
    frames: Frame = Field(ge=1)
    duration_sec: float = Field(gt=0, le=1200)
    stretch_ready: bool


class MashupRenderArtifact(BeatContract):
    id: BeatId
    kind: Literal['mix', 'stem', 'dry', 'placement', 'plan', 'provenance', 'bundle']
    filename: str = Field(min_length=1, max_length=160, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
    sha256: BeatSha256
    byte_count: int = Field(ge=1, le=17179869184)
    media_type: Literal['audio/wav', 'application/json', 'application/zip']
    source: SourceSide | None = None
    stem: MashupStem | None = None
    placement_id: BeatIdentifier | None = None
    frames: Frame | None = None


class MashupRenderedPlacement(BeatContract):
    placement_id: BeatIdentifier
    artifact_id: BeatId
    source_start_frame: Frame
    source_end_frame: Frame
    target_start_frame: Frame
    target_end_frame: Frame
    artifact_start_frame: Frame = 0


class MashupRenderManifest(BeatContract):
    schema_version: Literal[1] = 1
    mashup_id: BeatId
    mashup_revision: BeatRevision
    job_id: BeatId
    plan_sha256: BeatSha256
    source_a_sha256: BeatSha256
    source_b_sha256: BeatSha256
    mode: Literal['preview', 'full']
    frames: Frame = Field(ge=1)
    sample_rate_hz: Literal[48000] = 48000
    channels: Literal[2] = 2
    start_tick: Tick = 0
    artifacts: list[MashupRenderArtifact] = Field(min_length=1, max_length=1024)
    placements: list[MashupRenderedPlacement] = Field(max_length=256)
    metrics: AudioMetrics
    warnings: list[MashupWarning] = Field(default_factory=list, max_length=16)
    music_evidence: MusicEvidence
    track_id: int | None = Field(default=None, gt=0, le=9007199254740991)
    project_id: int | None = Field(default=None, gt=0, le=9007199254740991)


class MashupProgress(BeatContract):
    phase: Literal['queued', 'retaining', 'decoding', 'separating', 'transforming', 'mixing', 'publishing']
    completed: int = Field(default=0, ge=0, le=1024)
    total: int | None = Field(default=None, ge=1, le=1024)


class MashupJob(BeatContract):
    id: BeatId
    mashup_id: BeatId
    mashup_revision: BeatRevision
    kind: Literal['preparation', 'preview', 'full']
    status: Literal['queued', 'running', 'cancelling', 'done', 'failed', 'cancelled', 'interrupted', 'blocked', 'saving']
    request: MashupJobRequest | None = None
    progress: MashupProgress
    manifest: MashupRenderManifest | None = None
    error: MashupFailure | None = None
    created_at: Timestamp
    started_at: Timestamp | None = None
    finished_at: Timestamp | None = None

    @model_validator(mode='after')
    def snapshot_identity(self) -> Self:
        if self.kind == 'preparation':
            if self.request is not None or self.manifest is not None:
                raise ValueError('mashup_invalid')
        elif self.request is None or self.request.mode != self.kind or self.request.expected_revision != self.mashup_revision:
            raise ValueError('mashup_invalid')
        if self.manifest is not None and (self.manifest.job_id != self.id or self.manifest.mashup_id != self.mashup_id
                or self.manifest.mashup_revision != self.mashup_revision or self.manifest.mode != self.kind):
            raise ValueError('mashup_invalid')
        return self


class MashupJobs(BeatContract):
    jobs: list[MashupJob] = Field(max_length=100)


MASHUP_CLIENT_MODELS: list[type[BaseModel]] = [MashupSourcePreviewRequest, MashupSourcePreview,
    MashupCreateRequest, MashupState, MashupStates, MashupTimingUpdate, MashupGeneratePlanRequest,
    MashupEditPlanRequest, MashupJobRequest, MashupJob, MashupJobs, MashupStoragePreview,
    MashupRenderManifest, MashupFailure, MashupTiming, MashupPlacement]
