"""Canonical newly built audio projects; historical project JSON stays opaque.

Mixer limits/defaults match frontend api/editorValidation.ts and mixerEngine.ts.
These are backend-owned builder inputs, never an arbitrary-file import API.
"""
from __future__ import annotations
from typing import Annotated, Literal, Self
from pydantic import BaseModel, Field, field_validator, model_validator
from .beat_contracts import BeatContract, BeatId, BeatIdentifier, BeatRevision, BeatSha256

Seconds = Annotated[float, Field(ge=0, le=21600)]
SafeTrackId = Annotated[int, Field(gt=0, le=9007199254740991)]
LocalAudioUrl = Annotated[str, Field(min_length=6, max_length=500, pattern=r'^/api/[A-Za-z0-9][A-Za-z0-9/_-]*$')]


class EditorEq(BeatContract):
    low: float = Field(default=0, ge=-12, le=12)
    mid: float = Field(default=0, ge=-12, le=12)
    high: float = Field(default=0, ge=-12, le=12)


class EditorComp(BeatContract):
    threshold: float = Field(default=-24, ge=-60, le=0)
    ratio: float = Field(default=1, ge=1, le=20)


class EditorReverb(BeatContract):
    mix: float = Field(default=0, ge=0, le=1)


class EditorFilter(BeatContract):
    enabled: bool = False
    type: Literal['lowpass', 'highpass'] = 'lowpass'
    frequency: float = Field(default=22000, ge=20, le=22000)
    resonance: float = Field(default=1, ge=.1, le=20)


class EditorDistortion(BeatContract):
    enabled: bool = False
    amount: float = Field(default=.5, ge=0, le=1)
    mix: float = Field(default=0, ge=0, le=1)


class EditorChorus(BeatContract):
    enabled: bool = False
    rate: float = Field(default=1.5, ge=.1, le=5)
    depth: float = Field(default=.002, ge=.001, le=.01)
    mix: float = Field(default=0, ge=0, le=1)


class EditorBitcrusher(BeatContract):
    enabled: bool = False
    bits: int = Field(default=8, ge=2, le=16)
    mix: float = Field(default=0, ge=0, le=1)


class EditorDelay(BeatContract):
    enabled: bool = False
    time: float = Field(default=.3, ge=.01, le=2)
    feedback: float = Field(default=.4, ge=0, le=.95)
    mix: float = Field(default=0, ge=0, le=1)


class EditorMasterSettings(BeatContract):
    volume: float = Field(default=1, ge=0, le=1.5)
    eq: EditorEq = Field(default_factory=EditorEq)
    comp: EditorComp = Field(default_factory=EditorComp)
    reverb: EditorReverb = Field(default_factory=EditorReverb)
    filter: EditorFilter = Field(default_factory=EditorFilter)
    distortion: EditorDistortion = Field(default_factory=EditorDistortion)
    chorus: EditorChorus = Field(default_factory=EditorChorus)
    bitcrusher: EditorBitcrusher = Field(default_factory=EditorBitcrusher)
    delay: EditorDelay = Field(default_factory=EditorDelay)


class EditorChannelSettings(EditorMasterSettings):
    pan: float = Field(default=0, ge=-1, le=1)
    muted: bool = False
    solo: bool = False


class BeatClipLink(BeatContract):
    beat_id: BeatId
    beat_revision: BeatRevision
    render_id: BeatId
    lane_id: BeatIdentifier
    # None means the complete chain, not an invented individual occurrence.
    occurrence_index: int | None = Field(default=None, ge=0, le=4095)


class MusicProjectSource(BeatContract):
    track_id: SafeTrackId
    version_id: BeatId
    sha256: BeatSha256


class MusicProjectProvenance(BeatContract):
    workflow: Literal['beat', 'beat_song', 'mashup']
    workflow_id: BeatId
    workflow_revision: BeatRevision
    source_manifest_sha256: BeatSha256
    sources: list[MusicProjectSource] = Field(default_factory=list, max_length=32)


class MusicProjectArtifact(BeatContract):
    artifact_id: BeatId
    sha256: BeatSha256


class RenderedProjectLane(BeatContract):
    lane_id: BeatIdentifier
    label: str = Field(min_length=1, max_length=120)
    artifact_id: BeatId
    sha256: BeatSha256
    source_url: LocalAudioUrl
    start_sec: Seconds = 0
    trim_start_sec: Seconds = 0
    trim_end_sec: Seconds
    fade_in_sec: Seconds = 0
    fade_out_sec: Seconds = 0
    settings: EditorChannelSettings = Field(default_factory=EditorChannelSettings)
    muted: bool = False
    beat_link: BeatClipLink | None = None

    @model_validator(mode='after')
    def coherent_trim(self) -> Self:
        if self.trim_end_sec < self.trim_start_sec or self.start_sec + self.trim_end_sec - self.trim_start_sec > 21600:
            raise ValueError('music_project_invalid')
        return self


class EditorAudioClip(BeatContract):
    id: BeatId
    type: Literal['audio'] = 'audio'
    sourceUrl: LocalAudioUrl
    sourceLabel: str = Field(min_length=1, max_length=120)
    timelineStart: Seconds
    trimStart: Seconds
    trimEnd: Seconds
    fadeInDuration: Seconds = 0
    fadeOutDuration: Seconds = 0
    warpEnabled: bool = False
    originalBpm: float = Field(ge=20, le=999)
    muted: bool = False
    solo: bool = False
    beatLink: BeatClipLink | None = None
    sourceArtifact: MusicProjectArtifact | None = None

    @model_validator(mode='after')
    def coherent_trim(self) -> Self:
        if self.trimEnd < self.trimStart or self.timelineStart + self.trimEnd - self.trimStart > 21600:
            raise ValueError('music_project_invalid')
        return self


class EditorLane(BeatContract):
    id: BeatIdentifier
    name: str = Field(min_length=1, max_length=120)
    clips: list[EditorAudioClip] = Field(max_length=4096)
    settings: EditorChannelSettings = Field(default_factory=EditorChannelSettings)


class EditorLoopRegion(BeatContract):
    start: Seconds
    end: Seconds
    enabled: bool

    @model_validator(mode='after')
    def coherent_range(self) -> Self:
        if self.end < self.start:
            raise ValueError('music_project_invalid')
        return self


class EditorProjectDocument(BeatContract):
    version: Literal[1] = 1
    lanes: list[EditorLane] = Field(max_length=32)
    master: EditorMasterSettings = Field(default_factory=EditorMasterSettings)
    pxPerSecond: float = Field(default=40, ge=5, le=400)
    bpm: float = Field(ge=20, le=999)
    snapEnabled: bool = True
    loopRegion: EditorLoopRegion | None = None
    musicProvenance: MusicProjectProvenance | None = None

    @field_validator('version', mode='before')
    @classmethod
    def strict_version(cls, value: object) -> object:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError('music_project_invalid')
        return value

    @model_validator(mode='after')
    def unique_identifiers(self) -> Self:
        clips = [clip.id for lane in self.lanes for clip in lane.clips]
        if (len({lane.id for lane in self.lanes}) != len(self.lanes) or len(clips) > 4096 or len(set(clips)) != len(clips)):
            raise ValueError('music_project_invalid')
        return self


class BuiltMusicProject(BeatContract):
    name: str = Field(min_length=1, max_length=200)
    data: EditorProjectDocument
    provenance: MusicProjectProvenance


class MusicProjectPublication(BeatContract):
    project_id: SafeTrackId
    provenance: MusicProjectProvenance


MUSIC_PROJECT_CLIENT_MODELS: list[type[BaseModel]] = [BuiltMusicProject, MusicProjectPublication,
    BeatClipLink, MusicProjectArtifact, MusicProjectProvenance]
