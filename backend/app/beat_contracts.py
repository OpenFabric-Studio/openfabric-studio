"""Strict beat authoring, sample identity and MIDI metadata contracts."""
from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from .contracts import Contract, JsonObject
from .module_contracts import LicenseRevision, ModuleLicenseDeclaration

PPQ = 960
MAX_EVENTS = 262144
MAX_POLYPHONY = 128
BLOCK_FRAMES = 4096
MAX_EVENTS_PER_BLOCK = 4096
MAX_MIDI_MESSAGES = 1000000
MAX_SECONDS = 600
MAX_TAIL_SECONDS = 30
BeatId = Annotated[str, Field(min_length=32, max_length=32, pattern=r'^[0-9a-f]{32}$')]
BeatSha256 = Annotated[str, Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')]
BeatIdentifier = Annotated[str, Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')]
BeatRevision = Annotated[int, Field(ge=0, le=9007199254740991)]
BeatPatternId = Literal['A', 'B', 'C', 'D']
BeatRole = Literal['drum', 'bass', 'pitched']
BeatErrorCode = Literal['beat_pattern_invalid', 'beat_render_limit', 'beat_swing_grid_unsupported',
    'beat_sample_changed', 'beat_instrument_missing', 'beat_midi_channels_exhausted', 'beat_midi_percussion_overlap',
    'beat_not_found', 'beat_job_not_found', 'beat_revision_conflict', 'beat_submission_conflict', 'beat_queue_full',
    'beat_disk_full', 'beat_worker_failed', 'beat_cleanup_failed', 'beat_save_failed', 'beat_storage_unavailable',
    'beat_kit_invalid', 'beat_cancelled', 'beat_interrupted']
BeatWarning = Literal['beat_event_clamped', 'beat_midi_timing_quantized', 'beat_midi_approximation',
    'beat_midi_generic_sounds', 'beat_midi_percussion_retrigger']
_json = TypeAdapter(JsonObject)


class BeatError(Exception):
    def __init__(self, code: BeatErrorCode) -> None:
        self.code = code
        super().__init__(code)


class BeatContract(Contract):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False, frozen=True)

    @field_validator('schema_version', 'channels', 'denominator', 'steps_per_quarter', 'ppq', mode='before', check_fields=False)
    @classmethod
    def strict_integer_literals(cls, value: object) -> object:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError('beat_pattern_invalid')
        return value


class BeatMeter(BeatContract):
    numerator: int = Field(ge=1, le=32)
    denominator: Literal[2, 4, 8, 16]

    @property
    def quarters_per_bar(self) -> Fraction:
        return Fraction(self.numerator * 4, self.denominator)


class BeatPitchPoint(BeatContract):
    position: float = Field(ge=0, le=1)
    cents: float = Field(ge=-2400, le=2400)


class BeatStep(BeatContract):
    index: int = Field(ge=0, le=1023)
    velocity: int = Field(default=100, ge=1, le=127)
    probability: float = Field(default=1, ge=0, le=1)
    offset_steps: float = Field(default=0, ge=-0.5, le=0.5)
    ratchet: int = Field(default=1, ge=1, le=4)
    note_midi: int | None = Field(default=None, ge=0, le=127)
    cents: float = Field(default=0, ge=-100, le=100)
    gate_steps: float = Field(default=1, ge=0.05, le=8)
    articulation_id: BeatIdentifier | None = None
    pitch_curve: list[BeatPitchPoint] = Field(default_factory=list, max_length=16)

    @model_validator(mode='after')
    def ordered_curve(self) -> Self:
        if self.pitch_curve and (self.pitch_curve[0].position != 0 or self.pitch_curve[-1].position != 1
                or any(right.position <= left.position for left, right in zip(self.pitch_curve, self.pitch_curve[1:]))):
            raise ValueError('beat_pattern_invalid')
        return self


class BeatScale(BeatContract):
    scale_id: BeatIdentifier
    root_note_midi: int = Field(ge=0, le=127)
    degrees_cents: list[Annotated[float, Field(ge=0, lt=1200)]] = Field(min_length=1, max_length=24)
    reference_hz: float = Field(default=440, ge=400, le=480)

    @model_validator(mode='after')
    def ordered_degrees(self) -> Self:
        if any(right <= left for left, right in zip(self.degrees_cents, self.degrees_cents[1:])):
            raise ValueError('beat_pattern_invalid')
        return self


class BeatLane(BeatContract):
    lane_id: BeatIdentifier
    label: str = Field(min_length=1, max_length=80, pattern=r'\S')
    instrument_id: BeatIdentifier
    role: BeatRole
    muted: bool = False
    gain_db: float = Field(default=0, ge=-60, le=12)
    pan: float = Field(default=0, ge=-1, le=1)
    swing: float | None = Field(default=None, ge=0.5, le=0.75)
    steps: list[BeatStep] = Field(default_factory=list, max_length=1024)


class BeatPattern(BeatContract):
    id: BeatPatternId
    bars: int = Field(default=1, ge=1, le=4)
    meter: BeatMeter
    steps_per_quarter: Literal[2, 3, 4, 6, 8] = 4
    lanes: list[BeatLane] = Field(min_length=8, max_length=16)

    @staticmethod
    def grid_size(meter: BeatMeter, steps_per_quarter: int) -> int:
        value = meter.quarters_per_bar * steps_per_quarter
        if steps_per_quarter not in (2, 3, 4, 6, 8) or value.denominator != 1 or value > 256:
            raise ValueError('beat_pattern_invalid')
        return int(value)

    @model_validator(mode='after')
    def coherent_grid(self) -> Self:
        count = self.grid_size(self.meter, self.steps_per_quarter) * self.bars
        if len({lane.lane_id for lane in self.lanes}) != len(self.lanes):
            raise ValueError('beat_pattern_invalid')
        for lane in self.lanes:
            indexes = [step.index for step in lane.steps]
            if len(set(indexes)) != len(indexes) or any(index >= count for index in indexes):
                raise ValueError('beat_pattern_invalid')
        return self


class PatternPlacement(BeatContract):
    pattern_id: BeatPatternId
    repeats: int = Field(default=1, ge=1, le=64)
    section_label: str = Field(default='', max_length=80)


BeatPresetWarning = Literal['candidate_arrangement_unreviewed', 'scale_is_report_approximation',
    'fixed_grid_free_time_approximation', 'raga_phrase_rules_not_implemented']


class BeatPresetOrigin(BeatContract):
    """Candidate metadata, not authentication of the caller's claimed origin."""
    preset_id: BeatIdentifier
    preset_sha256: BeatSha256
    catalog_version: BeatIdentifier
    review_status: Literal['unreviewed'] = 'unreviewed'
    warnings: list[BeatPresetWarning] = Field(min_length=1, max_length=4)


class BeatDocument(BeatContract):
    schema_version: Literal[1] = 1
    id: BeatId
    revision: BeatRevision
    title: str = Field(min_length=1, max_length=120, pattern=r'\S')
    bpm: float = Field(ge=20, le=300)
    seed: int = Field(ge=0, le=2147483647)
    kit_id: BeatIdentifier
    kit_manifest_sha256: BeatSha256
    patterns: list[BeatPattern] = Field(min_length=1, max_length=4)
    chain: list[PatternPlacement] = Field(min_length=1, max_length=128)
    global_swing: float = Field(default=0.5, ge=0.5, le=0.75)
    scale: BeatScale | None = None
    renderer_version: BeatIdentifier = 'beat-render-v1'
    preset_origin: BeatPresetOrigin | None = None

    @model_validator(mode='after')
    def coherent_chain(self) -> Self:
        patterns = {pattern.id: pattern for pattern in self.patterns}
        if len(patterns) != len(self.patterns):
            raise ValueError('beat_pattern_invalid')
        lane_ids = [lane.lane_id for lane in self.patterns[0].lanes]
        if any([lane.lane_id for lane in pattern.lanes] != lane_ids for pattern in self.patterns):
            raise ValueError('beat_pattern_invalid')
        quarters = Fraction(0)
        bars = events = 0
        for placement in self.chain:
            pattern = patterns.get(placement.pattern_id)
            if pattern is None:
                raise ValueError('beat_pattern_invalid')
            bars += pattern.bars * placement.repeats
            quarters += pattern.meter.quarters_per_bar * pattern.bars * placement.repeats
            events += sum(step.ratchet for lane in pattern.lanes for step in lane.steps) * placement.repeats
        if bars > 4096 or quarters * 60 / Fraction(str(self.bpm)) > MAX_SECONDS or events > MAX_EVENTS:
            raise ValueError('beat_render_limit')
        return self


class BeatRenderRequest(BeatContract):
    beat_id: BeatId
    expected_revision: BeatRevision
    mode: Literal['preview', 'full']
    preview_pattern_id: BeatPatternId | None = None
    # Chosen zero-based expanded chain occurrence, preserving probability outcomes.
    occurrence_index: int = Field(default=0, ge=0, le=4095)
    submission_key: BeatId

    @model_validator(mode='after')
    def coherent_preview(self) -> Self:
        if (self.mode == 'preview' and self.preview_pattern_id is None
                or self.mode == 'full' and (self.preview_pattern_id is not None or self.occurrence_index != 0)):
            raise ValueError('beat_pattern_invalid')
        return self


class BeatSampleRegion(BeatContract):
    region_id: BeatIdentifier
    asset_id: BeatIdentifier
    sha256: BeatSha256
    byte_size: int = Field(ge=1, le=512 * 1024**2)
    sample_rate_hz: int = Field(ge=8000, le=192000)
    channels: Literal[1, 2]
    frames: int = Field(ge=1, le=5760000)
    root_note_midi: int = Field(ge=0, le=127)
    root_cents: float = Field(default=0, ge=-100, le=100)
    note_min: int = Field(default=0, ge=0, le=127)
    note_max: int = Field(default=127, ge=0, le=127)
    velocity_min: int = Field(default=1, ge=1, le=127)
    velocity_max: int = Field(default=127, ge=1, le=127)
    articulation_id: BeatIdentifier | None = None
    one_shot: bool = True
    choke_group: BeatIdentifier | None = None
    gain_db: float = Field(default=0, ge=-60, le=12)
    attack_ms: float = Field(default=2, ge=0, le=1000)
    release_ms: float = Field(default=20, ge=1, le=2000)
    terms: ModuleLicenseDeclaration
    attribution: str = Field(max_length=2000)
    source_revision: LicenseRevision | None = None

    @model_validator(mode='after')
    def coherent_sample(self) -> Self:
        if (self.frames > self.sample_rate_hz * MAX_TAIL_SECONDS or self.note_min > self.note_max
                or self.velocity_min > self.velocity_max or self.terms.scope != 'samples'):
            raise ValueError('beat_pattern_invalid')
        return self


class BeatInstrument(BeatContract):
    instrument_id: BeatIdentifier
    label: str = Field(min_length=1, max_length=80, pattern=r'\S')
    role: BeatRole
    pitched: bool = False
    supports_pitch_curve: bool = False
    midi_program: int = Field(default=0, ge=0, le=127)
    midi_note: int = Field(default=36, ge=0, le=127)
    regions: list[BeatSampleRegion] = Field(min_length=1, max_length=128)

    @model_validator(mode='after')
    def unambiguous_regions(self) -> Self:
        if (len({region.region_id for region in self.regions}) != len(self.regions)
                or not self.pitched and (self.role != 'drum' or self.supports_pitch_curve)):
            raise ValueError('beat_pattern_invalid')
        for index, left in enumerate(self.regions):
            for right in self.regions[index + 1:]:
                if (left.articulation_id == right.articulation_id
                        and left.note_min <= right.note_max and right.note_min <= left.note_max
                        and left.velocity_min <= right.velocity_max and right.velocity_min <= left.velocity_max):
                    raise ValueError('beat_pattern_invalid')
        return self


class BeatInstrumentManifest(BeatContract):
    schema_version: Literal[1] = 1
    kit_id: BeatIdentifier
    revision: BeatIdentifier
    instruments: list[BeatInstrument] = Field(min_length=1, max_length=128)

    @model_validator(mode='after')
    def bounded_kit(self) -> Self:
        if len({item.instrument_id for item in self.instruments}) != len(self.instruments):
            raise ValueError('beat_pattern_invalid')
        assets: dict[str, tuple[str, int, int, int, int]] = {}
        for instrument in self.instruments:
            for region in instrument.regions:
                identity = (region.sha256, region.byte_size, region.sample_rate_hz, region.channels, region.frames)
                if region.asset_id in assets and assets[region.asset_id] != identity:
                    raise ValueError('beat_sample_changed')
                assets[region.asset_id] = identity
        if sum(channels * frames * 4 for _, _, _, channels, frames in assets.values()) > 512 * 1024**2:
            raise ValueError('beat_render_limit')
        return self


class BeatMidiNoteMapping(BeatContract):
    event_id: str = Field(min_length=1, max_length=240)
    lane_id: BeatIdentifier
    instrument_id: BeatIdentifier
    asset_id: BeatIdentifier
    sample_sha256: BeatSha256
    channel: int = Field(ge=0, le=15)
    program: int = Field(ge=0, le=127)
    note_midi: int = Field(ge=0, le=127)
    cents: float = Field(ge=-100, le=100)
    pitch_curve: list[BeatPitchPoint] = Field(default_factory=list, max_length=16)
    output_note_midi: int = Field(ge=0, le=127)
    onset_tick: int = Field(ge=0)
    end_tick: int = Field(ge=0)
    bend_range_semitones: int = Field(ge=0, le=25)
    timing_quantized: bool
    tuning_approximated: bool
    percussion_retriggered: bool = False


class BeatMidiTuning(BeatContract):
    schema_version: Literal[1] = 1
    beat_id: BeatId
    beat_revision: BeatRevision
    document_sha256: BeatSha256
    events_sha256: BeatSha256
    ppq: Literal[960] = 960
    approximation: bool
    warnings: list[BeatWarning] = Field(max_length=5)
    notes: list[BeatMidiNoteMapping] = Field(max_length=MAX_EVENTS)


def canonical_json(value: BaseModel) -> bytes:
    return json.dumps(_json.validate_json(value.model_dump_json()), sort_keys=True,
        separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def document_sha256(document: BeatDocument) -> str:
    return hashlib.sha256(canonical_json(document)).hexdigest()


def manifest_sha256(manifest: BeatInstrumentManifest) -> str:
    return hashlib.sha256(canonical_json(manifest)).hexdigest()


BEAT_CLIENT_MODELS: list[type[BaseModel]] = [BeatDocument, BeatRenderRequest, BeatInstrumentManifest, BeatMidiTuning]


class BeatCreateRequest(BeatContract):
    title: str = Field(default='Untitled beat', min_length=1, max_length=120, pattern=r'\S')
    bpm: float = Field(default=120, ge=20, le=300)
    seed: int = Field(default=4471, ge=0, le=2147483647)
    kit_id: BeatIdentifier = 'procedural-core'
    document: BeatDocument | None = None


class BeatUpdateRequest(BeatContract):
    expected_revision: BeatRevision
    document: BeatDocument


class BeatRestoreRequest(BeatContract):
    expected_revision: BeatRevision
    revision: BeatRevision


class BeatVariationRequest(BeatContract):
    expected_revision: BeatRevision
    pattern_id: BeatPatternId
    seed: int = Field(ge=0, le=2147483647)


class BeatHumanizeRequest(BeatContract):
    expected_revision: BeatRevision
    pattern_id: BeatPatternId
    lane_ids: list[BeatIdentifier] = Field(min_length=1, max_length=16)
    timing_steps: float = Field(default=0.05, ge=0, le=0.1)
    velocity: int = Field(default=6, ge=0, le=12)


class BeatDocumentSummary(BeatContract):
    id: BeatId
    revision: BeatRevision
    title: str = Field(min_length=1, max_length=120)
    bpm: float = Field(ge=20, le=300)
    updated_at: str = Field(min_length=20, max_length=40)


class BeatDocuments(BeatContract):
    documents: list[BeatDocumentSummary] = Field(max_length=1000)


class BeatRevisionInfo(BeatContract):
    revision: BeatRevision
    document_sha256: BeatSha256
    title: str = Field(min_length=1, max_length=120)
    created_at: str = Field(min_length=20, max_length=40)


class BeatRevisions(BeatContract):
    revisions: list[BeatRevisionInfo] = Field(max_length=1000)


class BeatArchiveResponse(BeatContract):
    beat_id: BeatId
    archived: bool = True


class BeatKitInfo(BeatContract):
    manifest: BeatInstrumentManifest
    sha256: BeatSha256


class BeatKits(BeatContract):
    kits: list[BeatKitInfo] = Field(max_length=100)


class BeatJobRequest(BeatRenderRequest):
    midi_approximation: bool = False


class BeatArtifact(BeatContract):
    id: BeatId
    kind: Literal['mix', 'stem', 'pattern', 'events', 'midi', 'tuning', 'provenance', 'bundle']
    lane_id: BeatIdentifier | None = None
    sha256: BeatSha256
    byte_size: int = Field(ge=1, le=8 * 1024**3)
    media_type: str = Field(min_length=1, max_length=80)
    filename: str = Field(min_length=1, max_length=160, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')


BeatRenderWarning = BeatWarning | Literal['beat_silent', 'beat_peak_over_one', 'beat_midi_unavailable']


class BeatRenderManifest(BeatContract):
    schema_version: Literal[1] = 1
    beat_id: BeatId
    beat_revision: BeatRevision
    document_sha256: BeatSha256
    render_id: BeatId
    seed: int = Field(ge=0, le=2147483647)
    renderer_version: BeatIdentifier
    algorithm_version: BeatIdentifier
    sample_rate_hz: int = Field(default=48000, ge=48000, le=48000)
    channels: int = Field(default=2, ge=2, le=2)
    frames: int = Field(ge=1, le=30240000)
    musical_frames: int = Field(ge=1, le=28800000)
    master_gain: float = Field(default=1, ge=1, le=1)
    peak: float = Field(ge=0)
    kit_manifest_sha256: BeatSha256
    events_sha256: BeatSha256
    artifacts: list[BeatArtifact] = Field(min_length=1, max_length=64)
    warnings: list[BeatRenderWarning] = Field(default_factory=list, max_length=8)
    midi_error_code: BeatErrorCode | None = None
    track_id: int | None = Field(default=None, ge=1, le=9007199254740991)

    @model_validator(mode='after')
    def aligned_artifacts(self) -> Self:
        if self.musical_frames > self.frames or len({item.id for item in self.artifacts}) != len(self.artifacts):
            raise ValueError('beat_pattern_invalid')
        return self


class BeatStoragePreview(BeatContract):
    output_bytes: int = Field(ge=0)
    temporary_bytes: int = Field(ge=0)
    required_free_bytes: int = Field(ge=0)
    free_bytes: int | None = Field(default=None, ge=0)
    can_start: bool
    frames: int = Field(ge=1, le=30240000)
    duration_sec: float = Field(gt=0, le=630)


BeatJobStatus = Literal['queued', 'running', 'cancelling', 'done', 'failed', 'cancelled', 'interrupted', 'blocked', 'saving']
BeatJobPhase = Literal['queued', 'compiling', 'rendering', 'publishing', 'done', 'cancelling',
    'failed', 'cancelled', 'interrupted', 'blocked', 'saving']


class BeatJob(BeatContract):
    id: BeatId
    beat_id: BeatId
    beat_revision: BeatRevision
    request: BeatJobRequest
    status: BeatJobStatus
    phase: BeatJobPhase
    created_at: str = Field(min_length=20, max_length=40)
    started_at: str | None = Field(default=None, max_length=40)
    finished_at: str | None = Field(default=None, max_length=40)
    progress_frames: int | None = Field(default=None, ge=0, le=30240000)
    total_frames: int | None = Field(default=None, ge=1, le=30240000)
    error_code: BeatErrorCode | None = None
    manifest: BeatRenderManifest | None = None


class BeatJobs(BeatContract):
    jobs: list[BeatJob] = Field(max_length=1000)


BEAT_CLIENT_MODELS.extend([BeatCreateRequest, BeatUpdateRequest, BeatRestoreRequest, BeatVariationRequest,
    BeatHumanizeRequest, BeatDocuments, BeatRevisions, BeatArchiveResponse, BeatKits, BeatJobRequest,
    BeatJob, BeatJobs, BeatStoragePreview, BeatRenderManifest])
