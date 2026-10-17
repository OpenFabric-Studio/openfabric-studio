"""Exact timing, stable probability and bounded resolved beat events."""
from __future__ import annotations

import hashlib
import unittest
from fractions import Fraction
from unittest.mock import patch

from app import beat_compile
from app.beat_compile import compile_events, compiled_events_json, event_fraction
from app.beat_contracts import (BeatDocument, BeatError, BeatMeter, BeatPattern, BeatPitchPoint,
    BeatStep, PatternPlacement, manifest_sha256)
from app.beat_contracts_test import document_fixture, manifest_fixture


class BeatCompileTests(unittest.TestCase):
    def test_coordinate_randomness_is_stable_and_purpose_separated(self) -> None:
        key = ('A', 'kick', 3, 0)
        value = event_fraction(4471, key, 'probability')
        self.assertEqual(value, event_fraction(4471, key, 'probability'))
        self.assertTrue(0 <= value < 1)
        self.assertNotEqual(value, event_fraction(4471, key, 'velocity'))
        self.assertNotEqual(value, event_fraction(4471, ('A', 'kick', 4, 0), 'probability'))

    def test_binary_and_triplet_onsets_are_measured_at_exact_frames(self) -> None:
        kit = manifest_fixture()
        source = document_fixture(kit)
        for grid, count in ((4, 16), (8, 32), (3, 12), (6, 24)):
            with self.subTest(grid=grid):
                pattern = source.patterns[0].model_copy(update={'steps_per_quarter': grid})
                pattern.lanes[0].steps[:] = [BeatStep(index=index) for index in range(count)]
                compiled = compile_events(source.model_copy(update={'patterns': [pattern]}), 48000, kit)
                expected = [(index * 24000 * 2 + grid) // (2 * grid) for index in range(count)]
                self.assertEqual([event.onset_frame for event in compiled.events], expected)
                self.assertEqual(compiled.duration_frames, 96000)
                impulse = bytearray(compiled.duration_frames)
                for event in compiled.events:
                    impulse[event.onset_frame] = 1
                self.assertEqual([index for index, value in enumerate(impulse) if value], expected)

    def test_mixed_meter_chain_and_preview_keep_occurrence_random_coordinates(self) -> None:
        kit = manifest_fixture()
        source = document_fixture(kit)
        a = source.patterns[0]
        b = a.model_copy(update={'id': 'B', 'meter': BeatMeter(numerator=9, denominator=8)})
        document = source.model_copy(update={'patterns': [a, b], 'chain': [PatternPlacement(pattern_id='A'),
            PatternPlacement(pattern_id='B', repeats=2, section_label='Verse')]})
        compiled = compile_events(document, 48000, kit)
        self.assertEqual([placement.start_ticks for placement in compiled.placements], [0, 3840, 8160])
        self.assertEqual(compiled.duration_frames, 312000)
        self.assertEqual([event.occurrence_index for event in compiled.events], [0, 1, 2])
        preview = compile_events(document, 48000, kit, preview_pattern_id='B', occurrence_index=2)
        self.assertEqual(preview.events[0].occurrence_index, 2)
        self.assertEqual(preview.duration_frames, 108000)
        with self.assertRaises(BeatError):
            compile_events(document, 48000, kit, preview_pattern_id='A', occurrence_index=2)

    def test_ratchet_probability_swing_and_source_grid_gate_units(self) -> None:
        kit = manifest_fixture()
        source = document_fixture(kit)
        source.patterns[0].lanes[0].steps[:] = [BeatStep(index=1, ratchet=4, gate_steps=1, offset_steps=0.25)]
        compiled = compile_events(source.model_copy(update={'global_swing': 0.75}), 48000, kit)
        self.assertEqual([event.onset_frame for event in compiled.events], [10500, 11250, 12000, 12750])
        self.assertEqual([event.gate_frames for event in compiled.events], [6000] * 4)
        self.assertEqual([event.gate_ticks for event in compiled.events], [Fraction(240)] * 4)
        source.patterns[0].lanes[0].steps[:] = [BeatStep(index=1, ratchet=4, probability=0)]
        self.assertEqual(compile_events(source, 48000, kit).events, ())

    def test_negative_group_offset_clamps_without_changing_ratchet_spacing(self) -> None:
        kit = manifest_fixture()
        document = document_fixture(kit)
        document.patterns[0].lanes[0].steps[:] = [BeatStep(index=0, ratchet=4, offset_steps=-0.5)]
        compiled = compile_events(document, 48000, kit)
        self.assertEqual([event.onset_frame for event in compiled.events], [0, 1500, 3000, 4500])
        self.assertEqual(compiled.warnings, ('beat_event_clamped',))

    def test_unsupported_swing_and_unpitched_controls_are_rejected(self) -> None:
        kit = manifest_fixture()
        document = document_fixture(kit)
        triplet = document.patterns[0].model_copy(update={'steps_per_quarter': 3})
        with self.assertRaises(BeatError) as caught:
            compile_events(document.model_copy(update={'patterns': [triplet], 'global_swing': 0.6}), 48000, kit)
        self.assertEqual(caught.exception.code, 'beat_swing_grid_unsupported')
        for step in (BeatStep(index=0, note_midi=36), BeatStep(index=0, cents=10),
                     BeatStep(index=0, pitch_curve=[BeatPitchPoint(position=0, cents=0), BeatPitchPoint(position=1, cents=20)])):
            document.patterns[0].lanes[0].steps[:] = [step]
            with self.subTest(step=step), self.assertRaises(BeatError):
                compile_events(document, 48000, kit)

    def test_manifest_identity_missing_region_and_polyphony_fail_before_render(self) -> None:
        kit = manifest_fixture(pitched=True)
        document = document_fixture(kit)
        with self.assertRaises(BeatError) as changed:
            compile_events(document.model_copy(update={'kit_manifest_sha256': 'f' * 64}), 48000, kit)
        self.assertEqual(changed.exception.code, 'beat_sample_changed')
        document.patterns[0].lanes[0].steps[:] = [BeatStep(index=0, articulation_id='absent')]
        with self.assertRaises(BeatError) as missing:
            compile_events(document, 48000, kit)
        self.assertEqual(missing.exception.code, 'beat_instrument_missing')
        for lane in document.patterns[0].lanes:
            lane.steps[:] = [BeatStep(index=index, ratchet=4, gate_steps=8) for index in range(16)]
        with self.assertRaises(BeatError) as dense:
            compile_events(document, 48000, kit)
        self.assertEqual(dense.exception.code, 'beat_render_limit')

    def test_resolved_events_have_detached_identity_and_canonical_hash(self) -> None:
        kit = manifest_fixture(pitched=True)
        document = document_fixture(kit)
        document.patterns[0].lanes[0].steps[:] = [BeatStep(index=0, note_midi=61, cents=75)]
        compiled = compile_events(document, 48000, kit)
        event = compiled.events[0]
        self.assertEqual((event.note_midi, event.cents), (62, -25))
        self.assertEqual((event.asset_id, event.sample_sha256), ('sample', 'b' * 64))
        self.assertEqual(hashlib.sha256(compiled_events_json(compiled)).hexdigest(), compiled.events_sha256)
        self.assertEqual(compiled, compile_events(document, 48000, kit))
        document.patterns[0].lanes[0].steps.clear()
        self.assertEqual(len(compiled.events), 1)

    def test_probability_is_once_per_step_and_preview_matches_each_occurrence(self) -> None:
        kit = manifest_fixture()
        document = document_fixture(kit)
        document.patterns[0].lanes[0].steps[:] = [BeatStep(index=0, ratchet=4, probability=0.5)]
        document = document.model_copy(update={'chain': [PatternPlacement(pattern_id='A', repeats=8)]})
        compiled = compile_events(document, 48000, kit)
        counts = []
        for occurrence in range(8):
            expected = [event for event in compiled.events if event.occurrence_index == occurrence]
            counts.append(len(expected))
            preview = compile_events(document, 48000, kit, preview_pattern_id='A', occurrence_index=occurrence)
            self.assertEqual([event.event_id for event in preview.events], [event.event_id for event in expected])
        self.assertEqual(set(counts), {0, 4})
        original_ids = [event.event_id for event in compiled.events]
        document.patterns[0].lanes.insert(0, document.patterns[0].lanes[-1].model_copy(update={'lane_id': 'new-lane'}))
        self.assertEqual([event.event_id for event in compile_events(document, 48000, kit).events], original_ids)

    def test_tempo_extremes_asymmetric_meters_and_block_boundary(self) -> None:
        kit = manifest_fixture()
        document = document_fixture(kit)
        for bpm, expected in ((20, 576000), (300, 38400)):
            self.assertEqual(compile_events(document.model_copy(update={'bpm': bpm}), 48000, kit).duration_frames, expected)
        for numerator, expected in ((12, 144000), (9, 108000), (10, 120000), (18, 216000)):
            pattern = document.patterns[0].model_copy(update={'meter': BeatMeter(numerator=numerator, denominator=8)})
            self.assertEqual(compile_events(document.model_copy(update={'patterns': [pattern]}), 48000, kit).duration_frames, expected)
        document.patterns[0].lanes[0].steps[:] = [BeatStep(index=0), BeatStep(index=4)]
        compiled = compile_events(document, 8192, kit)
        self.assertEqual([event.onset_frame for event in compiled.events], [0, 4096])

    def test_natural_sample_tail_and_block_density_are_bounded(self) -> None:
        kit = manifest_fixture(pitched=True)
        region = kit.instruments[0].regions[0].model_copy(update={'root_note_midi': 127})
        kit = kit.model_copy(update={'instruments': [kit.instruments[0].model_copy(update={'regions': [region]})]})
        document = document_fixture(kit)
        document.patterns[0].lanes[0].steps[:] = [BeatStep(index=0, note_midi=0)]
        with self.assertRaises(BeatError) as tail:
            compile_events(document, 48000, kit)
        self.assertEqual(tail.exception.code, 'beat_render_limit')
        kit = manifest_fixture()
        document = document_fixture(kit)
        document.patterns[0].lanes[0].steps[:] = [BeatStep(index=0, ratchet=4)]
        with patch.object(beat_compile, 'MAX_EVENTS_PER_BLOCK', 2), self.assertRaises(BeatError) as density:
            compile_events(document, 48000, kit)
        self.assertEqual(density.exception.code, 'beat_render_limit')

    def test_lane_swing_override_can_preserve_straight_timing(self) -> None:
        kit = manifest_fixture()
        document = document_fixture(kit)
        lane = document.patterns[0].lanes[0].model_copy(update={'swing': 0.5, 'steps': [BeatStep(index=1)]})
        pattern = document.patterns[0].model_copy(update={'lanes': [lane, *document.patterns[0].lanes[1:]]})
        compiled = compile_events(document.model_copy(update={'patterns': [pattern], 'global_swing': 0.75}), 48000, kit)
        self.assertEqual(compiled.events[0].onset_frame, 6000)

    def test_unchained_pattern_preview_still_obeys_the_total_duration_budget(self) -> None:
        kit = manifest_fixture()
        document = document_fixture(kit)
        a = document.patterns[0]
        b = a.model_copy(update={'id': 'B', 'bars': 4, 'meter': BeatMeter(numerator=32, denominator=2), 'steps_per_quarter': 2})
        document = document.model_copy(update={'patterns': [a, b], 'bpm': 20})
        with self.assertRaises(BeatError) as caught:
            compile_events(document, 48000, kit, preview_pattern_id='B', occurrence_index=0)
        self.assertEqual(caught.exception.code, 'beat_render_limit')


if __name__ == '__main__':
    unittest.main()
