"""Pure, bounded beat compilation with rational musical time and stable randomness."""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Literal

from pydantic import ValidationError

from .beat_contracts import (BLOCK_FRAMES, MAX_EVENTS, MAX_EVENTS_PER_BLOCK, MAX_POLYPHONY,
    MAX_SECONDS, MAX_TAIL_SECONDS, PPQ, BeatDocument, BeatError, BeatInstrument, BeatInstrumentManifest,
    BeatLane, BeatMeter, BeatPattern, BeatPatternId, BeatRole, BeatSampleRegion, BeatStep,
    BeatWarning, document_sha256, manifest_sha256)
from .contracts import JsonObject, JsonValue

ALGORITHM_VERSION = 'beat-events-v1'
RANDOM_VERSION = 'beat-random-v1'
RandomPurpose = Literal['probability', 'timing', 'velocity', 'region']
RandomCoordinate = tuple[str, str, int, int]


@dataclass(frozen=True)
class CompiledPitchPoint:
    position: Fraction
    cents: float


@dataclass(frozen=True)
class CompiledPlacement:
    pattern_id: BeatPatternId
    occurrence_index: int
    start_ticks: Fraction
    duration_ticks: Fraction
    meter: BeatMeter
    section_label: str


@dataclass(frozen=True)
class CompiledEvent:
    event_id: str
    pattern_id: BeatPatternId
    occurrence_index: int
    lane_id: str
    instrument_id: str
    step_index: int
    ratchet_index: int
    region_id: str
    asset_id: str
    sample_sha256: str
    sample_rate_hz: int
    sample_frames: int
    root_note_midi: int
    root_cents: float
    onset_ticks: Fraction
    gate_ticks: Fraction
    onset_frame: int
    gate_frames: int
    velocity: int
    note_midi: int
    cents: float
    pitch_curve: tuple[CompiledPitchPoint, ...]
    role: BeatRole
    pitched: bool
    midi_program: int
    one_shot: bool
    choke_group: str | None
    choke_ticks: Fraction | None
    gain_db: float
    pan: float
    sample_gain_db: float
    attack_ms: float
    release_ms: float

    @property
    def end_ticks(self) -> Fraction:
        end = self.onset_ticks + self.gate_ticks
        return min(end, self.choke_ticks) if self.choke_ticks is not None else end


@dataclass(frozen=True)
class CompiledBeat:
    beat_id: str
    beat_revision: int
    document_sha256: str
    kit_manifest_sha256: str
    renderer_version: str
    algorithm_version: str
    seed: int
    bpm: float
    sample_rate: int
    duration_ticks: Fraction
    duration_frames: int
    maximum_render_frames: int
    events: tuple[CompiledEvent, ...]
    placements: tuple[CompiledPlacement, ...]
    warnings: tuple[BeatWarning, ...]
    events_sha256: str


def round_half_up(value: Fraction) -> int:
    return (value.numerator * 2 + value.denominator) // (value.denominator * 2)


def event_fraction(seed: int, coordinate: RandomCoordinate, purpose: RandomPurpose) -> float:
    if (isinstance(seed, bool) or not 0 <= seed <= 2147483647 or coordinate[0] not in ('A', 'B', 'C', 'D')
            or not coordinate[1] or coordinate[2] < 0 or coordinate[3] < 0
            or purpose not in ('probability', 'timing', 'velocity', 'region')):
        raise BeatError('beat_pattern_invalid')
    payload = {'algorithm': RANDOM_VERSION, 'seed': seed, 'pattern_id': coordinate[0],
        'lane_id': coordinate[1], 'chain_occurrence': coordinate[2], 'step_index': coordinate[3], 'purpose': purpose}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')).digest()
    # Keep the representable high 53 bits of the first 64; float rounding must never yield 1.
    return (int.from_bytes(digest[:8], 'big') >> 11) / 9007199254740992


def _placements(document: BeatDocument) -> tuple[CompiledPlacement, ...]:
    patterns = {pattern.id: pattern for pattern in document.patterns}
    placements: list[CompiledPlacement] = []
    start = Fraction(0)
    for placement in document.chain:
        pattern = patterns[placement.pattern_id]
        duration = pattern.meter.quarters_per_bar * pattern.bars * PPQ
        for _ in range(placement.repeats):
            placements.append(CompiledPlacement(pattern.id, len(placements), start, duration, pattern.meter, placement.section_label))
            start += duration
    return tuple(placements)


def _pitch(document: BeatDocument, instrument: BeatInstrument, step: BeatStep) -> tuple[int, float]:
    if not instrument.pitched:
        if step.note_midi is not None or step.cents != 0 or step.pitch_curve:
            raise BeatError('beat_pattern_invalid')
        return instrument.midi_note, 0
    if step.pitch_curve and not instrument.supports_pitch_curve:
        raise BeatError('beat_pattern_invalid')
    note = step.note_midi if step.note_midi is not None else instrument.midi_note
    reference = 1200 * math.log2(document.scale.reference_hz / 440) if document.scale is not None else 0
    total = note * 100 + step.cents + reference
    canonical = max(0, min(127, math.floor(total / 100 + 0.5)))
    cents = total - canonical * 100
    if abs(cents) > 100:
        raise BeatError('beat_pattern_invalid')
    return canonical, cents


def _region(instrument: BeatInstrument, step: BeatStep, note: int) -> BeatSampleRegion:
    for region in instrument.regions:
        if (region.articulation_id == step.articulation_id and region.note_min <= note <= region.note_max
                and region.velocity_min <= step.velocity <= region.velocity_max):
            return region
    raise BeatError('beat_instrument_missing')


def _lane_events(document: BeatDocument, pattern: BeatPattern, lane: BeatLane, instrument: BeatInstrument,
                 placement: CompiledPlacement, sample_rate: int, frames_per_tick: Fraction,
                 warnings: set[BeatWarning]) -> list[CompiledEvent]:
    if lane.role != instrument.role:
        raise BeatError('beat_pattern_invalid')
    swing = Fraction(str(lane.swing if lane.swing is not None else document.global_swing))
    steps_per_bar = pattern.grid_size(pattern.meter, pattern.steps_per_quarter)
    if swing != Fraction(1, 2) and (pattern.steps_per_quarter not in (2, 4, 8) or steps_per_bar % 2):
        raise BeatError('beat_swing_grid_unsupported')
    grid_ticks = Fraction(PPQ, pattern.steps_per_quarter)
    events: list[CompiledEvent] = []
    for step in sorted(lane.steps, key=lambda item: item.index):
        note, cents = _pitch(document, instrument, step)
        region = _region(instrument, step, note)
        if lane.muted or event_fraction(document.seed, (pattern.id, lane.lane_id, placement.occurrence_index, step.index), 'probability') >= step.probability:
            continue
        start = placement.start_ticks + step.index * grid_ticks
        span = grid_ticks
        if swing != Fraction(1, 2):
            if step.index % 2:
                start += grid_ticks * (2 * swing - 1)
                span *= 2 * (1 - swing)
            else:
                span *= 2 * swing
        start += Fraction(str(step.offset_steps)) * grid_ticks
        if start < 0:
            start = Fraction(0)
            warnings.add('beat_event_clamped')
        gate = Fraction(str(step.gate_steps)) * grid_ticks
        for ratchet in range(step.ratchet):
            onset = start + span * ratchet / step.ratchet
            onset_frame = round_half_up(onset * frames_per_tick)
            end_frame = round_half_up((onset + gate) * frames_per_tick)
            events.append(CompiledEvent(event_id=f'{placement.occurrence_index}:{pattern.id}:{lane.lane_id}:{step.index}:{ratchet}',
                pattern_id=pattern.id, occurrence_index=placement.occurrence_index, lane_id=lane.lane_id,
                instrument_id=instrument.instrument_id, step_index=step.index, ratchet_index=ratchet,
                region_id=region.region_id, asset_id=region.asset_id, sample_sha256=region.sha256,
                sample_rate_hz=region.sample_rate_hz, sample_frames=region.frames,
                root_note_midi=region.root_note_midi, root_cents=region.root_cents,
                onset_ticks=onset, gate_ticks=gate, onset_frame=onset_frame, gate_frames=max(1, end_frame - onset_frame),
                velocity=step.velocity, note_midi=note, cents=cents,
                pitch_curve=tuple(CompiledPitchPoint(Fraction(str(point.position)), point.cents) for point in step.pitch_curve),
                role=lane.role, pitched=instrument.pitched, midi_program=instrument.midi_program,
                one_shot=region.one_shot, choke_group=region.choke_group, choke_ticks=None,
                gain_db=lane.gain_db, pan=lane.pan, sample_gain_db=region.gain_db,
                attack_ms=region.attack_ms, release_ms=region.release_ms))
    return events


def _check_limits(events: list[CompiledEvent], sample_rate: int, frames_per_tick: Fraction, maximum_frame: int) -> None:
    if len(events) > MAX_EVENTS:
        raise BeatError('beat_render_limit')
    density = Counter(event.onset_frame // BLOCK_FRAMES for event in events)
    if any(count > MAX_EVENTS_PER_BLOCK for count in density.values()):
        raise BeatError('beat_render_limit')
    boundaries: list[tuple[int, int]] = []
    for event in events:
        end = round_half_up(event.end_ticks * frames_per_tick)
        if event.one_shot:
            minimum_curve = min((point.cents for point in event.pitch_curve), default=0)
            delta = (event.note_midi - event.root_note_midi) * 100 + event.cents - event.root_cents + minimum_curve if event.pitched else 0
            natural_frames = math.ceil(event.sample_frames * sample_rate / event.sample_rate_hz / 2**(delta / 1200))
            natural_end = event.onset_frame + natural_frames
            if event.choke_ticks is not None:
                natural_end = min(natural_end, round_half_up(event.choke_ticks * frames_per_tick) + math.ceil(event.release_ms * sample_rate / 1000))
            end = max(end, natural_end)
        else:
            end += math.ceil(event.release_ms * sample_rate / 1000)
        if end > maximum_frame:
            raise BeatError('beat_render_limit')
        if end > event.onset_frame:
            boundaries.extend(((event.onset_frame, 1), (end, -1)))
    active = 0
    for _, change in sorted(boundaries):
        active += change
        if active > MAX_POLYPHONY:
            raise BeatError('beat_render_limit')


def compile_events(document: BeatDocument, sample_rate: int, manifest: BeatInstrumentManifest, *,
                   preview_pattern_id: BeatPatternId | None = None, occurrence_index: int = 0) -> CompiledBeat:
    """Resolve immutable sample identities without reading assets or advancing global state."""
    try:
        document = BeatDocument.model_validate_json(document.model_dump_json())
        manifest = BeatInstrumentManifest.model_validate_json(manifest.model_dump_json())
    except ValidationError as exc:
        raise BeatError('beat_pattern_invalid') from exc
    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool) or not 8000 <= sample_rate <= 192000:
        raise BeatError('beat_render_limit')
    if document.kit_id != manifest.kit_id or document.kit_manifest_sha256 != manifest_sha256(manifest):
        raise BeatError('beat_sample_changed')
    patterns = {pattern.id: pattern for pattern in document.patterns}
    placements = _placements(document)
    if preview_pattern_id is not None:
        if not 0 <= occurrence_index <= 4095 or preview_pattern_id not in patterns:
            raise BeatError('beat_pattern_invalid')
        if occurrence_index < len(placements) and placements[occurrence_index].pattern_id == preview_pattern_id:
            placements = (replace(placements[occurrence_index], start_ticks=Fraction(0)),)
        elif occurrence_index == 0 and all(item.pattern_id != preview_pattern_id for item in placements):
            pattern = patterns[preview_pattern_id]
            placements = (CompiledPlacement(pattern.id, 0, Fraction(0), pattern.meter.quarters_per_bar * pattern.bars * PPQ, pattern.meter, ''),)
        else:
            raise BeatError('beat_pattern_invalid')
    elif occurrence_index != 0:
        raise BeatError('beat_pattern_invalid')
    instruments = {instrument.instrument_id: instrument for instrument in manifest.instruments}
    warnings: set[BeatWarning] = set()
    frames_per_tick = Fraction(sample_rate * 60, PPQ) / Fraction(str(document.bpm))
    duration = placements[-1].start_ticks + placements[-1].duration_ticks
    if duration * frames_per_tick > MAX_SECONDS * sample_rate:
        raise BeatError('beat_render_limit')
    duration_frames = round_half_up(duration * frames_per_tick)
    maximum_frame = duration_frames + MAX_TAIL_SECONDS * sample_rate
    events: list[CompiledEvent] = []
    for placement in placements:
        pattern = patterns[placement.pattern_id]
        for lane in pattern.lanes:
            instrument = instruments.get(lane.instrument_id)
            if instrument is None:
                raise BeatError('beat_instrument_missing')
            events.extend(_lane_events(document, pattern, lane, instrument, placement, sample_rate, frames_per_tick, warnings))
            if len(events) > MAX_EVENTS:
                raise BeatError('beat_render_limit')
    events.sort(key=lambda event: (event.onset_ticks, event.lane_id, event.step_index, event.ratchet_index, event.event_id))
    choke_groups: dict[str, int] = {}
    for index, event in enumerate(events):
        if event.choke_group is not None:
            previous = choke_groups.get(event.choke_group)
            if previous is not None:
                events[previous] = replace(events[previous], choke_ticks=event.onset_ticks)
            choke_groups[event.choke_group] = index
    _check_limits(events, sample_rate, frames_per_tick, maximum_frame)
    compiled = CompiledBeat(document.id, document.revision, document_sha256(document), document.kit_manifest_sha256,
        document.renderer_version, ALGORITHM_VERSION, document.seed, document.bpm, sample_rate,
        duration, duration_frames, maximum_frame, tuple(events), placements, tuple(sorted(warnings)), '')
    return replace(compiled, events_sha256=hashlib.sha256(compiled_events_json(compiled)).hexdigest())


def _tick(value: Fraction) -> list[JsonValue]:
    return [value.numerator, value.denominator]


def compiled_events_json(compiled: CompiledBeat) -> bytes:
    events: list[JsonValue] = []
    for event in compiled.events:
        events.append({'event_id': event.event_id, 'pattern_id': event.pattern_id, 'occurrence_index': event.occurrence_index,
            'lane_id': event.lane_id, 'instrument_id': event.instrument_id, 'step_index': event.step_index,
            'ratchet_index': event.ratchet_index, 'region_id': event.region_id, 'asset_id': event.asset_id,
            'sample_sha256': event.sample_sha256, 'sample_rate_hz': event.sample_rate_hz, 'sample_frames': event.sample_frames,
            'root_note_midi': event.root_note_midi, 'root_cents': event.root_cents,
            'onset_ticks': _tick(event.onset_ticks), 'gate_ticks': _tick(event.gate_ticks),
            'onset_frame': event.onset_frame, 'gate_frames': event.gate_frames, 'velocity': event.velocity,
            'note_midi': event.note_midi, 'cents': event.cents,
            'pitch_curve': [{'position': _tick(point.position), 'cents': point.cents} for point in event.pitch_curve],
            'role': event.role, 'pitched': event.pitched, 'midi_program': event.midi_program,
            'one_shot': event.one_shot, 'choke_group': event.choke_group,
            'choke_ticks': _tick(event.choke_ticks) if event.choke_ticks is not None else None,
            'gain_db': event.gain_db, 'pan': event.pan, 'sample_gain_db': event.sample_gain_db,
            'attack_ms': event.attack_ms, 'release_ms': event.release_ms})
    payload: JsonObject = {'algorithm_version': compiled.algorithm_version, 'ppq': PPQ, 'seed': compiled.seed,
        'bpm': compiled.bpm, 'sample_rate': compiled.sample_rate, 'duration_ticks': _tick(compiled.duration_ticks),
        'duration_frames': compiled.duration_frames, 'maximum_render_frames': compiled.maximum_render_frames,
        'events': events, 'warnings': list(compiled.warnings), 'placements': [
            {'pattern_id': placement.pattern_id, 'occurrence_index': placement.occurrence_index,
             'start_ticks': _tick(placement.start_ticks), 'duration_ticks': _tick(placement.duration_ticks),
             'meter': {'numerator': placement.meter.numerator, 'denominator': placement.meter.denominator},
             'section_label': placement.section_label} for placement in compiled.placements]}
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')
