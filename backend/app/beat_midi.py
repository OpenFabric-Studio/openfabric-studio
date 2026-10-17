"""Bounded SMF type-1 export; audio timbre and authoring remain separate artifacts.

Protocol references: https://midi.org/standard-midi-files and
https://midi.org/midi-1-0-control-change-messages . No native engine is involved.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from fractions import Fraction

from .beat_compile import CompiledBeat, CompiledEvent, CompiledPitchPoint, round_half_up
from .beat_contracts import (MAX_MIDI_MESSAGES, PPQ, BeatError, BeatMidiNoteMapping, BeatMidiTuning,
    BeatPitchPoint, BeatWarning)


@dataclass(frozen=True)
class MidiExport:
    data: bytes
    tuning: BeatMidiTuning


@dataclass(frozen=True)
class _Trajectory:
    cents: float
    curve: tuple[CompiledPitchPoint, ...] = ()
    origin: Fraction = Fraction(0)
    gate: Fraction = Fraction(0)
    stop: int = 0


@dataclass
class _Channel:
    number: int
    program: int | None = None
    trajectory: _Trajectory | None = None
    notes: dict[int, int] = field(default_factory=dict)
    initialized: bool = False
    available_after_tick: int = -1


@dataclass(frozen=True)
class _Voice:
    serial: int
    channel: int
    note: int
    onset: int
    end: int
    mapping_index: int


class _Messages:
    def __init__(self) -> None:
        self.tracks: list[list[tuple[int, int, int, bytes]]] = [[], []]
        self.count = 0

    def add(self, track: int, tick: int, priority: int, data: bytes) -> None:
        if self.count >= MAX_MIDI_MESSAGES:
            raise BeatError('beat_render_limit')
        self.tracks[track].append((tick, priority, self.count, data))
        self.count += 1


def _variable(value: int) -> bytes:
    if not 0 <= value <= 0x0fffffff:
        raise BeatError('beat_render_limit')
    result = [value & 0x7f]
    while value >> 7:
        value >>= 7
        result.insert(0, (value & 0x7f) | 0x80)
    return bytes(result)


def _meta(kind: int, data: bytes) -> bytes:
    return bytes([0xff, kind]) + _variable(len(data)) + data


def _encode_track(messages: list[tuple[int, int, int, bytes]], end_tick: int) -> bytes:
    output = bytearray()
    previous = 0
    for tick, _, _, data in sorted(messages):
        output.extend(_variable(tick - previous))
        output.extend(data)
        previous = tick
    output.extend(_variable(max(previous, end_tick) - previous))
    output.extend(b'\xff\x2f\x00')
    return b'MTrk' + len(output).to_bytes(4, 'big') + bytes(output)


def _bounds(event: CompiledEvent) -> tuple[int, int]:
    onset = round_half_up(event.onset_ticks)
    minimum = onset if event.end_ticks == event.onset_ticks else onset + 1
    return onset, max(minimum, round_half_up(event.end_ticks))


def _trajectory(event: CompiledEvent, end: int, approximation: bool) -> _Trajectory:
    if approximation or not event.pitched:
        return _Trajectory(0)
    curve = event.pitch_curve
    if not curve or all(point.cents == curve[0].cents for point in curve):
        return _Trajectory(event.cents + (curve[0].cents if curve else 0))
    return _Trajectory(event.cents, curve, event.onset_ticks, event.gate_ticks, end)


def _pitch_at(event: CompiledEvent, tick: int) -> float:
    if not event.pitch_curve:
        return event.cents
    position = max(Fraction(0), min(Fraction(1), (tick - event.onset_ticks) / event.gate_ticks))
    for left, right in zip(event.pitch_curve, event.pitch_curve[1:]):
        if position <= right.position:
            share = float((position - left.position) / (right.position - left.position))
            return event.cents + left.cents + share * (right.cents - left.cents)
    return event.cents + event.pitch_curve[-1].cents


def _bend(channel: int, cents: float, bend_range: int) -> bytes:
    units = 8191 if cents >= 0 else 8192
    offset = round_half_up(Fraction(str(cents)) * units / (bend_range * 100))
    value = max(0, min(16383, 8192 + offset))
    return bytes([0xe0 | channel, value & 127, value >> 7])


def _controller_step(compiled: CompiledBeat) -> int:
    # Floor tick spacing so controller intervals never exceed 5 ms.
    return max(1, int(Fraction(str(compiled.bpm)) * PPQ / 12000))


def _preflight(compiled: CompiledBeat, approximation: bool, interval: int) -> None:
    count = 260 + len(compiled.placements) * 3 + len(compiled.events) * 8
    for event in compiled.events:
        if event.pitched and event.pitch_curve and not approximation:
            onset, end = _bounds(event)
            count += (end - onset + interval - 1) // interval + len(event.pitch_curve) + 1
        if count > MAX_MIDI_MESSAGES:
            raise BeatError('beat_render_limit')


class _Exporter:
    def __init__(self, compiled: CompiledBeat, approximation: bool, bend_range: int, interval: int) -> None:
        self.compiled, self.approximation = compiled, approximation
        self.bend_range, self.interval = bend_range, interval
        self.messages = _Messages()
        self.channels = [_Channel(number) for number in range(16)]
        self.voices: dict[int, _Voice] = {}
        self.ends: list[tuple[int, int]] = []
        self.mappings: list[BeatMidiNoteMapping] = []
        self.warnings: set[BeatWarning] = set(compiled.warnings)
        self.warnings.add('beat_midi_generic_sounds')
        if approximation:
            self.warnings.add('beat_midi_approximation')

    def release(self, serial: int, tick: int, *, retrigger: bool = False) -> None:
        voice = self.voices.pop(serial, None)
        if voice is None:
            return
        channel = self.channels[voice.channel]
        channel.notes.pop(voice.note)
        # Zero-length notes still require on→off ordering at this tick.
        priority = 6 if tick == voice.onset else 1
        self.messages.add(1, tick, priority, bytes([0x80 | channel.number, voice.note, 0]))
        if retrigger:
            self.mappings[voice.mapping_index] = self.mappings[voice.mapping_index].model_copy(
                update={'end_tick': tick, 'percussion_retriggered': True})
            self.warnings.add('beat_midi_percussion_retrigger')
        if not channel.notes:
            channel.trajectory = None
            if channel.number != 9:
                if tick == voice.onset:
                    channel.available_after_tick = tick
                self.messages.add(1, tick, 7 if tick == voice.onset else 2, bytes([0xe0 | channel.number, 0, 64]))

    def drain(self, tick: int) -> None:
        while self.ends and self.ends[0][0] <= tick:
            end, serial = heapq.heappop(self.ends)
            self.release(serial, end)

    def channel(self, event: CompiledEvent, note: int, onset: int, trajectory: _Trajectory) -> _Channel:
        if not event.pitched:
            channel = self.channels[9]
            previous = channel.notes.get(note)
            if previous is not None:
                if not self.approximation:
                    raise BeatError('beat_midi_percussion_overlap')
                self.release(previous, onset, retrigger=True)
            if channel.notes and channel.program != event.midi_program:
                raise BeatError('beat_midi_channels_exhausted')
            return channel
        for channel in self.channels:
            if (channel.number != 9 and channel.notes and channel.program == event.midi_program
                    and channel.trajectory == trajectory and note not in channel.notes):
                return channel
        for channel in self.channels:
            if channel.number != 9 and not channel.notes and channel.available_after_tick < onset:
                return channel
        raise BeatError('beat_midi_channels_exhausted')

    def configure(self, channel: _Channel, event: CompiledEvent, onset: int, trajectory: _Trajectory) -> None:
        if channel.program != event.midi_program:
            self.messages.add(1, onset, 3, bytes([0xc0 | channel.number, event.midi_program]))
            channel.program = event.midi_program
        if channel.number != 9:
            if not channel.initialized:
                for control, value in ((101, 0), (100, 0), (6, self.bend_range), (38, 0), (101, 127), (100, 127)):
                    self.messages.add(1, onset, 4, bytes([0xb0 | channel.number, control, value]))
                channel.initialized = True
            if not channel.notes:
                cents = 0 if self.approximation else _pitch_at(event, onset)
                self.messages.add(1, onset, 5, _bend(channel.number, cents, self.bend_range))
                if trajectory.curve:
                    self.curve(channel.number, event, onset, trajectory.stop)
        channel.trajectory = trajectory

    def curve(self, channel: int, event: CompiledEvent, onset: int, end: int) -> None:
        ticks = set(range(onset + self.interval, end + 1, self.interval))
        ticks.add(end)
        for point in event.pitch_curve:
            tick = round_half_up(event.onset_ticks + point.position * event.gate_ticks)
            if onset < tick <= end:
                ticks.add(tick)
        for tick in sorted(ticks):
            if tick > onset:
                self.messages.add(1, tick, 0, _bend(channel, _pitch_at(event, tick), self.bend_range))

    def note(self, event: CompiledEvent) -> None:
        onset, end = _bounds(event)
        self.drain(onset)
        initial_cents = event.cents + (event.pitch_curve[0].cents if event.pitch_curve else 0)
        note = max(0, min(127, event.note_midi + math.floor(initial_cents / 100 + 0.5))) if self.approximation else event.note_midi
        trajectory = _trajectory(event, end, self.approximation)
        channel = self.channel(event, note, onset, trajectory)
        self.configure(channel, event, onset, trajectory)
        serial = len(self.mappings)
        voice = _Voice(serial, channel.number, note, onset, end, serial)
        self.voices[serial] = voice
        channel.notes[note] = serial
        heapq.heappush(self.ends, (end, serial))
        self.messages.add(1, onset, 6, bytes([0x90 | channel.number, note, event.velocity]))
        quantized = (Fraction(onset) != event.onset_ticks or Fraction(end) != event.end_ticks
            or any((event.onset_ticks + point.position * event.gate_ticks).denominator != 1 for point in event.pitch_curve))
        if quantized:
            self.warnings.add('beat_midi_timing_quantized')
        self.mappings.append(BeatMidiNoteMapping(event_id=event.event_id, lane_id=event.lane_id,
            instrument_id=event.instrument_id, asset_id=event.asset_id, sample_sha256=event.sample_sha256,
            channel=channel.number, program=event.midi_program, note_midi=event.note_midi, cents=event.cents,
            pitch_curve=[BeatPitchPoint(position=float(point.position), cents=point.cents) for point in event.pitch_curve],
            output_note_midi=note, onset_tick=onset, end_tick=end,
            bend_range_semitones=self.bend_range if event.pitched and not self.approximation else 0,
            timing_quantized=quantized, tuning_approximated=self.approximation and (
                event.cents != 0 or any(point.cents != 0 for point in event.pitch_curve))))

    def export(self) -> MidiExport:
        conductor = self.messages
        conductor.add(0, 0, 0, _meta(3, b'Beat conductor'))
        conductor.add(1, 0, -1, _meta(3, b'Beat events'))
        tempo = Fraction(60000000) / Fraction(str(self.compiled.bpm))
        if tempo.denominator != 1:
            self.warnings.add('beat_midi_timing_quantized')
        for placement in self.compiled.placements:
            tick = round_half_up(placement.start_ticks)
            conductor.add(0, tick, 0, _meta(0x51, round_half_up(tempo).to_bytes(3, 'big')))
            conductor.add(0, tick, 1, _meta(0x58, bytes([placement.meter.numerator,
                placement.meter.denominator.bit_length() - 1, 24, 8])))
            conductor.add(0, tick, 2, _meta(6, (placement.section_label or placement.pattern_id).encode('utf-8')))
        for event in self.compiled.events:
            self.note(event)
        while self.ends:
            self.drain(self.ends[0][0])
        duration = round_half_up(self.compiled.duration_ticks)
        end = max((note.end_tick for note in self.mappings), default=duration)
        header = b'MThd\x00\x00\x00\x06\x00\x01\x00\x02' + PPQ.to_bytes(2, 'big')
        data = header + _encode_track(self.messages.tracks[0], duration) + _encode_track(self.messages.tracks[1], max(duration, end))
        return MidiExport(data, BeatMidiTuning(beat_id=self.compiled.beat_id, beat_revision=self.compiled.beat_revision,
            document_sha256=self.compiled.document_sha256, events_sha256=self.compiled.events_sha256,
            approximation=self.approximation, warnings=sorted(self.warnings), notes=self.mappings))


def write_midi(compiled: CompiledBeat, *, approximation: bool = False) -> MidiExport:
    """Return real MIDI bytes and exact tuning mappings; never silently approximate."""
    interval = _controller_step(compiled)
    _preflight(compiled, approximation, interval)
    maximum = max((abs(event.cents + point) for event in compiled.events if event.pitched
        for point in ([value.cents for value in event.pitch_curve] if event.pitch_curve else [0])), default=0)
    bend_range = 1 if approximation else max(1, math.ceil(maximum / 100))
    if bend_range > 25:
        raise BeatError('beat_render_limit')
    return _Exporter(compiled, approximation, bend_range, interval).export()
