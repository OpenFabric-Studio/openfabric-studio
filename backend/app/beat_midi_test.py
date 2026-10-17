"""Decode actual SMF bytes independently of the beat writer's implementation."""
from __future__ import annotations

from dataclasses import dataclass
import unittest
from unittest.mock import patch

from app import beat_midi
from app.beat_compile import compile_events
from app.beat_contracts import BeatError, BeatMeter, BeatPitchPoint, BeatStep, PatternPlacement
from app.beat_contracts_test import document_fixture, manifest_fixture


@dataclass(frozen=True)
class DecodedEvent:
    tick: int
    status: int
    data: bytes


def decode_smf(data: bytes) -> tuple[int, list[list[DecodedEvent]]]:
    """Independent SMF reader: no writer functions or intermediate events reused."""
    if data[:4] != b'MThd' or int.from_bytes(data[4:8], 'big') != 6 or int.from_bytes(data[8:10], 'big') != 1:
        raise ValueError('Not SMF type 1')
    track_count, ppq = int.from_bytes(data[10:12], 'big'), int.from_bytes(data[12:14], 'big')
    offset = 14
    tracks: list[list[DecodedEvent]] = []
    for _ in range(track_count):
        if data[offset:offset + 4] != b'MTrk':
            raise ValueError('Missing track chunk')
        length = int.from_bytes(data[offset + 4:offset + 8], 'big')
        offset += 8
        end = offset + length
        if end > len(data):
            raise ValueError('Truncated track')
        absolute = running = 0
        events: list[DecodedEvent] = []
        def variable() -> int:
            nonlocal offset
            value = 0
            for _ in range(4):
                if offset >= end:
                    raise ValueError('Truncated variable number')
                byte = data[offset]; offset += 1
                value = (value << 7) | (byte & 0x7f)
                if byte < 128:
                    return value
            raise ValueError('Oversized variable number')
        while offset < end:
            absolute += variable()
            status = data[offset]
            if status >= 128:
                offset += 1
                if status < 240:
                    running = status
            else:
                status = running
            if status == 255:
                meta = data[offset]; offset += 1
                size = variable()
                payload = bytes([meta]) + data[offset:offset + size]
                offset += size
            elif 128 <= status <= 239:
                size = 1 if status & 0xf0 in (192, 208) else 2
                payload = data[offset:offset + size]
                if len(payload) != size or any(byte >= 128 for byte in payload):
                    raise ValueError('Invalid channel data')
                offset += size
            else:
                raise ValueError('Unexpected SMF message')
            events.append(DecodedEvent(absolute, status, payload))
        if offset != end or not events or events[-1].status != 255 or events[-1].data != b'\x2f':
            raise ValueError('Missing end-of-track')
        tracks.append(events)
    if offset != len(data):
        raise ValueError('Trailing bytes')
    return ppq, tracks


def note_ons(events: list[DecodedEvent]) -> list[DecodedEvent]:
    return [event for event in events if event.status & 0xf0 == 0x90 and event.data[1] != 0]


class BeatMidiTests(unittest.TestCase):
    def test_real_type_one_smf_has_quarter_clock_mixed_meters_and_gm_drums(self) -> None:
        kit = manifest_fixture()
        document = document_fixture(kit)
        a = document.patterns[0]
        b = a.model_copy(update={'id': 'B', 'meter': BeatMeter(numerator=9, denominator=8)})
        document = document.model_copy(update={'patterns': [a, b], 'chain': [PatternPlacement(pattern_id='A'), PatternPlacement(pattern_id='B')]})
        exported = beat_midi.write_midi(compile_events(document, 48000, kit))
        ppq, tracks = decode_smf(exported.data)
        self.assertEqual((ppq, len(tracks)), (960, 2))
        meters = [event for event in tracks[0] if event.data[:1] == b'\x58']
        self.assertEqual([(event.tick, event.data[1:3]) for event in meters], [(0, bytes([4, 2])), (3840, bytes([9, 3]))])
        tempos = [event for event in tracks[0] if event.data[:1] == b'\x51']
        self.assertEqual([int.from_bytes(event.data[1:], 'big') for event in tempos], [500000, 500000])
        self.assertTrue(all(event.status == 0x99 and event.data[0] == 36 for event in note_ons(tracks[1])))
        self.assertIn('beat_midi_generic_sounds', exported.tuning.warnings)

    def test_total_curve_pitch_sets_rpn_range_and_pitch_before_note_then_resets(self) -> None:
        kit = manifest_fixture(pitched=True)
        document = document_fixture(kit)
        document.patterns[0].lanes[0].steps[:] = [BeatStep(index=0, cents=25,
            pitch_curve=[BeatPitchPoint(position=0, cents=2400), BeatPitchPoint(position=1, cents=-2400)])]
        exported = beat_midi.write_midi(compile_events(document, 48000, kit))
        _, tracks = decode_smf(exported.data)
        events = tracks[1]
        onset = note_ons(events)[0]
        channel = onset.status & 15
        before = events[:events.index(onset)]
        controls = [event.data for event in before if event.status == 0xb0 + channel]
        for expected in (bytes([101, 0]), bytes([100, 0]), bytes([6, 25]), bytes([38, 0]), bytes([101, 127]), bytes([100, 127])):
            self.assertIn(expected, controls)
        bends = [event for event in events if event.status == 0xe0 + channel]
        initial = next(event for event in before if event.status == 0xe0 + channel)
        self.assertGreater(initial.data[0] | initial.data[1] << 7, 8192)
        self.assertEqual(bends[-1].data, bytes([0, 64]))
        self.assertEqual(exported.tuning.notes[0].bend_range_semitones, 25)

    def test_overlapping_distinct_bends_separate_channels_identical_bends_share(self) -> None:
        for same in (True, False):
            kit = manifest_fixture(pitched=True)
            document = document_fixture(kit)
            document.patterns[0].lanes[0].steps[:] = [BeatStep(index=0, note_midi=60, cents=25)]
            document.patterns[0].lanes[1].steps[:] = [BeatStep(index=0, note_midi=64, cents=25 if same else -25)]
            exported = beat_midi.write_midi(compile_events(document, 48000, kit))
            _, tracks = decode_smf(exported.data)
            channels = {event.status & 15 for event in note_ons(tracks[1])}
            self.assertEqual(len(channels), 1 if same else 2)
            self.assertNotIn(9, channels)

    def test_reused_channel_sends_note_off_before_next_note_on(self) -> None:
        kit = manifest_fixture(pitched=True)
        document = document_fixture(kit)
        document.patterns[0].lanes[0].steps[:] = [BeatStep(index=0, cents=25), BeatStep(index=1, cents=-25)]
        _, tracks = decode_smf(beat_midi.write_midi(compile_events(document, 48000, kit)).data)
        at_boundary = [event.status & 0xf0 for event in tracks[1] if event.tick == 240]
        self.assertLess(at_boundary.index(0x80), at_boundary.index(0x90))

    def test_channel_exhaustion_is_explicit_and_approximation_records_tuning_loss(self) -> None:
        kit = manifest_fixture(pitched=True)
        document = document_fixture(kit)
        pattern = document.patterns[0]
        lanes = [pattern.lanes[index % 8].model_copy(update={'lane_id': f'lane-{index}',
            'steps': [BeatStep(index=0, note_midi=60 + index, cents=index * 2)]}) for index in range(16)]
        compiled = compile_events(document.model_copy(update={'patterns': [pattern.model_copy(update={'lanes': lanes})]}), 48000, kit)
        with self.assertRaises(BeatError) as caught:
            beat_midi.write_midi(compiled)
        self.assertEqual(caught.exception.code, 'beat_midi_channels_exhausted')
        approximate = beat_midi.write_midi(compiled, approximation=True)
        _, tracks = decode_smf(approximate.data)
        self.assertEqual(len(note_ons(tracks[1])), 16)
        self.assertIn('beat_midi_approximation', approximate.tuning.warnings)
        self.assertEqual(sum(note.tuning_approximated for note in approximate.tuning.notes), 15)

    def test_curve_messages_include_knots_and_no_controller_gap_above_five_ms(self) -> None:
        kit = manifest_fixture(pitched=True)
        document = document_fixture(kit)
        document.patterns[0].lanes[0].steps[:] = [BeatStep(index=0, gate_steps=2, pitch_curve=[
            BeatPitchPoint(position=0, cents=0), BeatPitchPoint(position=0.33, cents=100), BeatPitchPoint(position=1, cents=-100)])]
        exported = beat_midi.write_midi(compile_events(document, 48000, kit))
        _, tracks = decode_smf(exported.data)
        ticks = sorted({event.tick for event in tracks[1] if event.status & 0xf0 == 0xe0})
        self.assertIn(158, ticks)
        self.assertLessEqual(max(right - left for left, right in zip(ticks, ticks[1:])) * 500000 / 960 / 1000, 5)
        self.assertTrue(exported.tuning.notes[0].timing_quantized)

    def test_curve_message_budget_is_checked_before_encoding(self) -> None:
        kit = manifest_fixture(pitched=True)
        document = document_fixture(kit)
        document.patterns[0].lanes[0].steps[:] = [BeatStep(index=0, gate_steps=8, pitch_curve=[
            BeatPitchPoint(position=0, cents=0), BeatPitchPoint(position=1, cents=100)])]
        compiled = compile_events(document, 48000, kit)
        with patch.object(beat_midi, 'MAX_MIDI_MESSAGES', 100), \
             patch.object(beat_midi, '_Exporter', side_effect=AssertionError('Must reject before output construction')), \
             self.assertRaises(BeatError) as caught:
            beat_midi.write_midi(compiled)
        self.assertEqual(caught.exception.code, 'beat_render_limit')

    def test_overlapping_percussion_gates_require_explicit_approximate_retrigger(self) -> None:
        kit = manifest_fixture()
        document = document_fixture(kit)
        document.patterns[0].lanes[0].steps[:] = [BeatStep(index=0, ratchet=4)]
        compiled = compile_events(document, 48000, kit)
        with self.assertRaises(BeatError) as caught:
            beat_midi.write_midi(compiled)
        self.assertEqual(caught.exception.code, 'beat_midi_percussion_overlap')
        approximate = beat_midi.write_midi(compiled, approximation=True)
        _, tracks = decode_smf(approximate.data)
        self.assertEqual([note.end_tick for note in approximate.tuning.notes], [60, 120, 180, 420])
        self.assertIn('beat_midi_percussion_retrigger', approximate.tuning.warnings)
        self.assertEqual(sum(note.percussion_retriggered for note in approximate.tuning.notes), 3)
        for tick in (60, 120, 180):
            at_tick = [event.status & 0xf0 for event in tracks[1] if event.tick == tick]
            self.assertLess(at_tick.index(0x80), at_tick.index(0x90))
        self.assertTrue(all(event.gate_ticks == 240 for event in compiled.events))

    def test_choke_ends_old_curve_before_reusing_channel(self) -> None:
        from app.beat_contracts import manifest_sha256
        kit = manifest_fixture(pitched=True)
        region = kit.instruments[0].regions[0].model_copy(update={'choke_group': 'closed'})
        instrument = kit.instruments[0].model_copy(update={'regions': [region]})
        kit = kit.model_copy(update={'instruments': [instrument]})
        document = document_fixture(kit).model_copy(update={'kit_manifest_sha256': manifest_sha256(kit)})
        document.patterns[0].lanes[0].steps[:] = [BeatStep(index=0, gate_steps=8, pitch_curve=[
            BeatPitchPoint(position=0, cents=0), BeatPitchPoint(position=1, cents=1200)]), BeatStep(index=2, cents=-25)]
        exported = beat_midi.write_midi(compile_events(document, 48000, kit))
        _, tracks = decode_smf(exported.data)
        self.assertEqual(exported.tuning.notes[0].end_tick, 480)
        self.assertTrue(all(event.data == bytes([0, 64]) for event in tracks[1]
            if event.tick > 480 and event.status & 0xf0 == 0xe0))

    def test_simultaneous_pitched_chokes_leave_each_released_channel_centered(self) -> None:
        kit = manifest_fixture(pitched=True)
        region = kit.instruments[0].regions[0].model_copy(update={'choke_group': 'closed'})
        kit = kit.model_copy(update={'instruments': [kit.instruments[0].model_copy(update={'regions': [region]})]})
        document = document_fixture(kit)
        document.patterns[0].lanes[0].steps[:] = [BeatStep(index=0, cents=25)]
        document.patterns[0].lanes[1].steps[:] = [BeatStep(index=0, cents=-25)]
        _, tracks = decode_smf(beat_midi.write_midi(compile_events(document, 48000, kit)).data)
        channels = {event.status & 15 for event in note_ons(tracks[1])}
        for channel in channels:
            bends = [event for event in tracks[1] if event.status == 0xe0 | channel]
            self.assertEqual(bends[-1].data, bytes([0, 64]))
        for index, onset in enumerate(note_ons(tracks[1])):
            before = tracks[1][:tracks[1].index(onset)]
            bend = [event for event in before if event.status == 0xe0 | (onset.status & 15)][-1]
            value = bend.data[0] | bend.data[1] << 7
            self.assertGreater(value, 8192) if index == 0 else self.assertLess(value, 8192)
        active: set[tuple[int, int]] = set()
        for event in tracks[1]:
            if event.status & 0xf0 == 0x90:
                active.add((event.status & 15, event.data[0]))
            elif event.status & 0xf0 == 0x80:
                active.discard((event.status & 15, event.data[0]))
        self.assertEqual(active, set())

    def test_program_mapping_and_pitched_percussion_use_melodic_channels(self) -> None:
        kit = manifest_fixture(pitched=True)
        instrument = kit.instruments[0].model_copy(update={'role': 'drum', 'midi_program': 71})
        kit = kit.model_copy(update={'instruments': [instrument]})
        document = document_fixture(kit)
        document.patterns[0].lanes[0].steps[:] = [BeatStep(index=0, cents=25)]
        compiled = compile_events(document, 48000, kit)
        exported = beat_midi.write_midi(compiled)
        _, tracks = decode_smf(exported.data)
        onset = note_ons(tracks[1])[0]
        channel = onset.status & 15
        self.assertNotEqual(channel, 9)
        before = tracks[1][:tracks[1].index(onset)]
        self.assertIn(DecodedEvent(0, 0xc0 | channel, bytes([71])), before)
        self.assertEqual(exported.tuning.notes[0].program, 71)
        self.assertEqual(exported.data, beat_midi.write_midi(compiled).data)
        self.assertFalse(exported.tuning.approximation)

    def test_approximate_simultaneous_percussion_retrigger_preserves_event_order(self) -> None:
        kit = manifest_fixture()
        document = document_fixture(kit)
        document.patterns[0].lanes[1].steps[:] = [BeatStep(index=0)]
        exported = beat_midi.write_midi(compile_events(document, 48000, kit), approximation=True)
        _, tracks = decode_smf(exported.data)
        ordered = [event.status & 0xf0 for event in tracks[1] if event.tick == 0 and event.status & 0xf0 in (0x80, 0x90)]
        self.assertEqual(ordered, [0x90, 0x80, 0x90])
        self.assertEqual(exported.tuning.notes[0].end_tick, 0)
        self.assertTrue(exported.tuning.notes[0].percussion_retriggered)


if __name__ == '__main__':
    unittest.main()
