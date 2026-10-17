"""Deterministic fixed-grid, order-preserving section planning.

No detector is hidden here. Every source boundary is operator-confirmed. The
dynamic programme chooses nondecreasing backing sections; it never permutes
the vocal sections to improve a score.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import math

from .beat_contracts import PPQ, canonical_json
from .mashup_contracts import (MashupCandidate, MashupCompatibilityComponent, MashupError,
    MashupGeneratePlanRequest, MashupPlacement, MashupPlan, MashupSourceSnapshot, MashupState,
    MashupStem, MashupTiming, MashupTimingSection, MashupWarning, Orientation, TempoAlias)

RATE = 48000
MAX_SECONDS = 1200
MAX_REPEATS = 8


def tick_seconds(ticks: int, bpm: float) -> float:
    return ticks * 60 / (PPQ * bpm)


def tick_frame(ticks: int, bpm: float) -> int:
    return round(tick_seconds(ticks, bpm) * RATE)


def _factor(alias: TempoAlias) -> Fraction:
    return Fraction(1, 2) if alias == 'half' else Fraction(2) if alias == 'double' else Fraction(1)


def _sources(state: MashupState, orientation: Orientation) -> tuple[MashupSourceSnapshot, MashupSourceSnapshot]:
    return (state.source_a, state.source_b) if orientation == 'a_vocals_b_backing' else (state.source_b, state.source_a)


def _component(score: float | None, measurement: float | None, reason: str) -> MashupCompatibilityComponent:
    return MashupCompatibilityComponent.model_validate({'available': score is not None, 'score': score,
        'measurement': measurement, 'reason': reason})


def candidates(state: MashupState) -> list[MashupCandidate]:
    result: list[MashupCandidate] = []
    for orientation in ('a_vocals_b_backing', 'b_vocals_a_backing'):
        # Annotating the collection preserves the literal orientation boundary.
        chosen: Orientation = 'a_vocals_b_backing' if orientation == 'a_vocals_b_backing' else 'b_vocals_a_backing'
        vocal, backing = _sources(state, chosen)
        key_a = vocal.timing.key if vocal.timing is not None else vocal.analysis_key
        key_b = backing.timing.key if backing.timing is not None else backing.analysis_key
        shift: int | None = None
        if key_a.status == key_b.status == 'known' and key_a.tonic_pc is not None and key_b.tonic_pc is not None:
            # Relative modes share a pitch collection. This is only a tonal
            # distance heuristic; it does not establish melodic compatibility.
            a_major = (key_a.tonic_pc + (3 if key_a.mode == 'minor' else 0)) % 12
            b_major = (key_b.tonic_pc + (3 if key_b.mode == 'minor' else 0)) % 12
            shift = (b_major - a_major + 6) % 12 - 6
        key = _component(None if shift is None else max(0, 100 - abs(shift) * 15),
            None if shift is None else float(shift), 'key_missing' if shift is None else 'available')
        ratio: float | None = None
        if vocal.timing is not None and backing.timing is not None:
            ratio = vocal.timing.bpm / backing.timing.bpm
        tempo = _component(None if ratio is None else max(0, 100 - abs(math.log2(ratio)) * 100), ratio,
            'timing_missing' if ratio is None else 'available')
        same_meter = (vocal.timing is not None and backing.timing is not None
            and vocal.timing.meter == backing.timing.meter)
        section_ratio: float | None = None
        if same_meter and vocal.timing is not None and backing.timing is not None:
            section_ratio = sum(section.bars for section in vocal.timing.sections) / sum(section.bars for section in backing.timing.sections)
        section = _component(None if section_ratio is None else max(0, 100 - abs(math.log2(section_ratio)) * 40),
            section_ratio, 'available' if section_ratio is not None else 'meter_mismatch' if ratio is not None else 'timing_missing')
        # Whole-mix energy does not establish vocal presence or vocal bleed.
        energy = _component(None, None, 'vocal_activity_missing')
        warnings: list[MashupWarning] = ['analysis_incomplete', 'separation_bleed_possible']
        if shift is None:
            warnings.append('key_unknown')
        if ratio is not None and not same_meter:
            warnings.append('meter_mismatch')
        if shift is not None and abs(shift) > 2:
            warnings.append('large_pitch_shift')
        aliases: list[TempoAlias] = ['declared']
        if ratio is not None:
            alternatives: tuple[TempoAlias, ...] = ('half', 'double')
            aliases += [alias for alias in alternatives if .75 <= ratio * float(_factor(alias)) <= 1.5]
        # Weights are key .35, tempo .30, section .20, clash .15. Missing
        # clash evidence intentionally leaves the aggregate unavailable.
        result.append(MashupCandidate(orientation=chosen, key=key, tempo=tempo, section_fit=section,
            energy_clash=energy, score=None, vocal_pitch_semitones=shift, duration_ratio=ratio,
            tempo_aliases=aliases, warnings=warnings))
    return result


def _timing(source: MashupSourceSnapshot) -> MashupTiming:
    timing, prepared = source.timing, source.prepared
    if timing is None or prepared is None:
        raise MashupError('mashup_analysis_incomplete')
    if timing.downbeat_frames[-1] != prepared.frames:
        raise MashupError('mashup_alignment_unsafe')
    frames_per_bar = RATE * 60 * float(timing.meter.quarters_per_bar) / timing.bpm
    # The first version deliberately supports a fixed grid. Drifting live
    # timing needs a reviewed warp map, rather than silently discarding taps.
    if any(abs(frame - round(index * frames_per_bar)) > 2 for index, frame in enumerate(timing.downbeat_frames)):
        raise MashupError('mashup_alignment_unsafe')
    return timing


def _whole(value: Fraction) -> int:
    if value.denominator != 1:
        raise MashupError('mashup_alignment_unsafe')
    return value.numerator


def _placement_id(kind: str, section: str, index: int = 0, stem: str = '') -> str:
    return hashlib.sha256(f'{kind}:{section}:{index}:{stem}'.encode('utf-8')).hexdigest()[:32]


def _assign(vocal: MashupTiming, backing: MashupTiming, vocal_factor: Fraction,
    backing_factor: Fraction) -> tuple[int, ...]:
    # State is the latest backing index. Stable tuple ordering resolves ties.
    states: dict[tuple[int, int], tuple[float, tuple[int, ...]]] = {(-1, 0): (0, ())}
    for section in vocal.sections:
        following: dict[tuple[int, int], tuple[float, tuple[int, ...]]] = {}
        desired = section.bars * vocal_factor
        for index, candidate in enumerate(backing.sections):
            available = candidate.bars * backing_factor
            repeats = math.ceil(desired / available)
            if repeats > MAX_REPEATS or (desired / backing_factor).denominator != 1:
                continue
            label_cost = .15 if section.musical_label is None or candidate.musical_label is None else float(section.musical_label != candidate.musical_label)
            cost = abs(math.log2(float(desired / available))) + label_cost + .25 * (repeats - 1)
            for (previous, used), (old_cost, path) in states.items():
                count = used + repeats if previous == index else repeats
                if previous > index or count > MAX_REPEATS:
                    continue
                key = (index, count)
                option = (old_cost + cost, (*path, index))
                if key not in following or option < following[key]:
                    following[key] = option
        if not following:
            raise MashupError('mashup_alignment_unsafe')
        states = following
    return min(states.values())[1]


def generate(state: MashupState, request: MashupGeneratePlanRequest) -> MashupPlan:
    if state.revision != request.expected_revision:
        raise MashupError('mashup_revision_conflict')
    vocal_source, backing_source = _sources(state, request.orientation)
    vocal, backing = _timing(vocal_source), _timing(backing_source)
    if vocal.meter != request.meter or backing.meter != request.meter:
        raise MashupError('mashup_alignment_unsafe')
    vocal_factor, backing_factor = _factor(request.vocal_tempo_alias), _factor(request.backing_tempo_alias)
    assignments = _assign(vocal, backing, vocal_factor, backing_factor)
    bar_ticks = request.meter.quarters_per_bar * PPQ
    placements: list[MashupPlacement] = []
    cursor = 0
    for section, backing_index in zip(vocal.sections, assignments):
        ticks = _whole(section.bars * vocal_factor * bar_ticks)
        placements.append(MashupPlacement(id=_placement_id('vocal', section.id), source=vocal_source.side,
            stem='vocals', section_id=section.id, source_start_frame=section.start_frame, source_end_frame=section.end_frame,
            target_start_tick=cursor, target_end_tick=cursor + ticks, pitch_semitones=request.vocal_pitch_semitones))
        chosen = backing.sections[backing_index]
        start_index = backing.downbeat_frames.index(chosen.start_frame)
        remaining, offset, repeat = ticks, 0, 0
        while remaining:
            span = min(remaining, _whole(chosen.bars * backing_factor * bar_ticks))
            source_bars = _whole(Fraction(span, 1) / bar_ticks / backing_factor)
            for stem in ('drums', 'bass', 'other'):
                typed_stem: MashupStem = 'drums' if stem == 'drums' else 'bass' if stem == 'bass' else 'other'
                placements.append(MashupPlacement(id=_placement_id('backing', section.id, repeat, stem),
                    source=backing_source.side, stem=typed_stem, section_id=chosen.id,
                    source_start_frame=chosen.start_frame, source_end_frame=backing.downbeat_frames[start_index + source_bars],
                    target_start_tick=cursor + offset, target_end_tick=cursor + offset + span,
                    pitch_semitones=0 if stem == 'drums' else request.backing_pitch_semitones,
                    fade_in_sec=min(.01, tick_seconds(span, request.target_bpm) / 2),
                    fade_out_sec=min(.01, tick_seconds(span, request.target_bpm) / 2)))
            remaining -= span
            offset += span
            repeat += 1
            if len(placements) > 256:
                raise MashupError('mashup_budget_exceeded')
        cursor += ticks
    by_id = {placement.id: placement for placement in placements}
    conflicts = [lock.placement_id for lock in request.locks
        if lock.placement_id not in by_id or by_id[lock.placement_id].target_start_tick != lock.target_start_tick]
    if conflicts:
        raise MashupError('mashup_lock_conflict', conflicts)
    locked_ids = {lock.placement_id for lock in request.locks}
    placements = [item.model_copy(update={'locked': item.id in locked_ids}) for item in placements]
    warnings: list[MashupWarning] = ['manual_timing', 'transition_review', 'separation_bleed_possible']
    if request.target_key.status == 'unknown':
        warnings.append('key_unknown')
    if max(abs(request.vocal_pitch_semitones), abs(request.backing_pitch_semitones)) > 2:
        warnings.append('large_pitch_shift')
    if request.vocal_tempo_alias != 'declared' or request.backing_tempo_alias != 'declared':
        warnings.append('tempo_alias_selected')
    identifier = hashlib.sha256(state.id.encode('ascii') + canonical_json(request)).hexdigest()[:32]
    plan = MashupPlan(id=identifier, revision=state.revision + 1, settings=request,
        placements=placements, duration_ticks=cursor, warnings=warnings)
    validate_plan(state, plan)
    return plan


def validate_plan(state: MashupState, plan: MashupPlan) -> None:
    vocal_source, backing_source = _sources(state, plan.settings.orientation)
    vocal, backing = _timing(vocal_source), _timing(backing_source)
    if len({item.id for item in plan.placements}) != len(plan.placements):
        raise MashupError('mashup_invalid')
    if tick_seconds(plan.duration_ticks, plan.settings.target_bpm) > MAX_SECONDS:
        raise MashupError('mashup_budget_exceeded')
    vocals = sorted((item for item in plan.placements if item.stem == 'vocals'), key=lambda item: (item.target_start_tick, item.id))
    if len(vocals) != len(vocal.sections):
        raise MashupError('mashup_alignment_unsafe')
    for placement, section in zip(vocals, vocal.sections):
        if (placement.source != vocal_source.side or placement.section_id != section.id
                or placement.source_start_frame != section.start_frame or placement.source_end_frame != section.end_frame):
            raise MashupError('mashup_alignment_unsafe')
    lanes: dict[tuple[str, str], list[MashupPlacement]] = {}
    for item in plan.placements:
        source = vocal_source if item.stem == 'vocals' else backing_source
        timing = vocal if item.stem == 'vocals' else backing
        checked_section = next((section for section in timing.sections if section.id == item.section_id), None)
        if (source.side != item.source or source.prepared is None or checked_section is None
                or item.source_start_frame < checked_section.start_frame or item.source_end_frame > checked_section.end_frame
                or item.source_end_frame > source.prepared.frames
                or item.source_start_frame not in timing.downbeat_frames or item.source_end_frame not in timing.downbeat_frames
                or item.target_end_tick > plan.duration_ticks):
            raise MashupError('mashup_alignment_unsafe')
        duration = tick_seconds(item.target_end_tick - item.target_start_tick, plan.settings.target_bpm)
        ratio = duration / ((item.source_end_frame - item.source_start_frame) / RATE)
        if not .75 - 1e-12 <= ratio <= 1.5 + 1e-12 or item.fade_in_sec + item.fade_out_sec > duration:
            raise MashupError('mashup_alignment_unsafe')
        if abs(item.pitch_semitones) > 3 and not plan.settings.allow_large_pitch_shift:
            raise MashupError('mashup_pitch_override_required')
        lanes.setdefault((item.source, item.stem), []).append(item)
    if {stem for _, stem in lanes} != {'vocals', 'drums', 'bass', 'other'}:
        raise MashupError('mashup_alignment_unsafe')
    for lane in lanes.values():
        ordered = sorted(lane, key=lambda item: (item.target_start_tick, item.id))
        if any(first.target_end_tick > second.target_start_tick for first, second in zip(ordered, ordered[1:])):
            raise MashupError('mashup_alignment_unsafe')
        if ordered[0].stem != 'vocals':
            indexes = {section.id: index for index, section in enumerate(backing.sections)}
            sequence = [indexes[item.section_id] for item in ordered]
            if any(first > second for first, second in zip(sequence, sequence[1:])):
                raise MashupError('mashup_alignment_unsafe')
            if any(sequence.count(index) > MAX_REPEATS for index in set(sequence)):
                raise MashupError('mashup_budget_exceeded')
            if (ordered[0].target_start_tick != 0 or ordered[-1].target_end_tick != plan.duration_ticks
                    or any(first.target_end_tick != second.target_start_tick for first, second in zip(ordered, ordered[1:]))):
                raise MashupError('mashup_alignment_unsafe')
    if state.plan is not None:
        current = {item.id: item for item in plan.placements}
        # An explicit unlock may change only the lock flag. Moving the same
        # item requires a subsequent revision, so edits cannot silently erase
        # a constraint to make an otherwise conflicting placement fit.
        conflicts = [item.id for item in state.plan.placements if item.locked
            and (item.id not in current or current[item.id].model_copy(update={'locked': True}) != item)]
        if conflicts:
            raise MashupError('mashup_lock_conflict', conflicts)
