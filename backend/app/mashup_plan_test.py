from __future__ import annotations

import unittest
from app.export_provenance_contracts import MusicEvidence
from app.mashup_contracts import (MashupAudioArtifact, MashupError, MashupGeneratePlanRequest,
    MashupSourceSnapshot, MashupState, MashupTiming)
from app.mashup_plan import candidates, generate, validate_plan


def source_fixture(side: str, *, bars: tuple[int, ...] = (2, 2), bpm: float = 120) -> MashupSourceSnapshot:
    frames_per_bar = round(48000 * 240 / bpm)
    ends = [0]
    sections = []
    for index, length in enumerate(bars):
        start = ends[-1]
        ends.extend(start + frames_per_bar * n for n in range(1, length + 1))
        sections.append({'id': f'S{index}', 'start_frame': start, 'end_frame': ends[-1], 'bars': length})
    track = 1 if side == 'a' else 2
    prepared = MashupAudioArtifact(id=str(track) * 32, filename='prepared.wav', sha256=str(track) * 64,
        byte_count=ends[-1] * 8 + 88, frames=ends[-1])
    return MashupSourceSnapshot.model_validate({'side': side,
        'input': {'source': {'kind': 'version', 'track_id': track, 'version_id': str(track) * 32},
            'sha256': str(track) * 64, 'excerpt_end_sec': ends[-1] / 48000},
        'raw_filename': 'raw.wav', 'source_bytes': 100, 'duration_sec': ends[-1] / 48000,
        'prepared': prepared.model_dump(), 'association': 'fresh_separation',
        'music_evidence': MusicEvidence().model_dump(), 'timing': MashupTiming.model_validate({
            'bpm': bpm, 'meter': {'numerator': 4, 'denominator': 4}, 'downbeat_frames': ends, 'sections': sections}).model_dump()})


def state_fixture() -> MashupState:
    return MashupState(id='a' * 32, revision=0, title='Pair', status='ready',
        source_a=source_fixture('a'), source_b=source_fixture('b', bars=(1, 2)),
        created_at='2026-10-17T00:00:00+00:00', updated_at='2026-10-17T00:00:00+00:00')


class MashupPlanTest(unittest.TestCase):
    def request(self, **changes: object) -> MashupGeneratePlanRequest:
        return MashupGeneratePlanRequest.model_validate({'expected_revision': 0, 'orientation': 'a_vocals_b_backing',
            'target_bpm': 120, 'meter': {'numerator': 4, 'denominator': 4}, **changes})

    def test_monotone_vocals_all_once_and_repeat_backing_at_reviewed_bars(self) -> None:
        state = state_fixture()
        first = generate(state, self.request())
        self.assertEqual(first, generate(state, self.request()))
        vocals = [item for item in first.placements if item.stem == 'vocals']
        self.assertEqual([item.section_id for item in vocals], ['S0', 'S1'])
        self.assertEqual([(item.source_start_frame, item.source_end_frame) for item in vocals],
            [(0, 192000), (192000, 384000)])
        self.assertEqual(first.duration_ticks, 16 * 960)
        validate_plan(state, first)
        source_b = state.source_b.timing
        self.assertIsNotNone(source_b)
        if source_b is None:
            self.fail('fixture timing')
        for item in first.placements:
            if item.source == 'b':
                self.assertIn(item.source_start_frame, source_b.downbeat_frames)
                self.assertIn(item.source_end_frame, source_b.downbeat_frames)

    def test_ratio_direction_and_bounds_roundtrip(self) -> None:
        state = state_fixture()
        for bpm, expected in ((160, .75), (80, 1.5)):
            plan = generate(state, self.request(target_bpm=bpm))
            item = plan.placements[0]
            target_sec = (item.target_end_tick - item.target_start_tick) * 60 / (960 * bpm)
            source_sec = (item.source_end_frame - item.source_start_frame) / 48000
            self.assertAlmostEqual(target_sec / source_sec, expected)
        for bpm in (79, 161):
            with self.subTest(bpm=bpm), self.assertRaises(MashupError):
                generate(state, self.request(target_bpm=bpm))

    def test_locks_are_hard_and_report_conflicting_ids(self) -> None:
        state = state_fixture()
        initial = generate(state, self.request())
        locked = initial.placements[0]
        with self.assertRaises(MashupError) as caught:
            generate(state, self.request(locks=[{'placement_id': locked.id, 'target_start_tick': 960}]))
        self.assertEqual(caught.exception.code, 'mashup_lock_conflict')
        self.assertEqual(caught.exception.conflicting_ids, [locked.id])

    def test_unknown_evidence_never_becomes_a_rescaled_quality_score(self) -> None:
        result = candidates(state_fixture())
        self.assertEqual(len(result), 2)
        self.assertTrue(all(item.score is None and not item.energy_clash.available for item in result))
        self.assertTrue(all(item.key.score is None for item in result))

    def test_missing_timing_and_drifting_grid_are_explicit(self) -> None:
        state = state_fixture()
        missing = state.model_copy(update={'source_a': state.source_a.model_copy(update={'timing': None})})
        with self.assertRaises(MashupError) as caught:
            generate(missing, self.request())
        self.assertEqual(caught.exception.code, 'mashup_analysis_incomplete')
        timing = state.source_a.timing
        if timing is None:
            self.fail('fixture timing')
        drift = timing.model_copy(update={'downbeat_frames': [0, 97000, 192000, 288000, 384000]})
        state = state.model_copy(update={'source_a': state.source_a.model_copy(update={'timing': drift})})
        with self.assertRaises(MashupError) as caught:
            generate(state, self.request())
        self.assertEqual(caught.exception.code, 'mashup_alignment_unsafe')

    def test_explicit_unlock_is_separate_from_moving_a_locked_placement(self) -> None:
        state = state_fixture()
        plan = generate(state, self.request())
        first = plan.placements[0].model_copy(update={'locked': True})
        pinned = plan.model_copy(update={'placements': [first, *plan.placements[1:]]})
        state = state.model_copy(update={'plan': pinned})
        unlocked = first.model_copy(update={'locked': False})
        validate_plan(state, pinned.model_copy(update={'placements': [unlocked, *pinned.placements[1:]]}))
        with self.assertRaises(MashupError):
            validate_plan(state, pinned.model_copy(update={'placements': [
                unlocked.model_copy(update={'target_start_tick': 960}), *pinned.placements[1:]]}))

    def test_backing_reordering_and_excessive_repetition_are_rejected(self) -> None:
        state = state_fixture().model_copy(update={'source_a': source_fixture('a', bars=(1, 1)),
            'source_b': source_fixture('b', bars=(1, 1))})
        plan = generate(state, self.request())
        changed = [item.model_copy(update={'section_id': 'S1', 'source_start_frame': 96000,
            'source_end_frame': 192000}) if item.source == 'b' and item.target_start_tick == 0 else item
            for item in plan.placements]
        with self.assertRaises(MashupError):
            validate_plan(state, plan.model_copy(update={'placements': changed}))
        repeated = state.model_copy(update={'source_a': source_fixture('a', bars=(1,) * 9),
            'source_b': source_fixture('b', bars=(1,))})
        with self.assertRaises(MashupError):
            generate(repeated, self.request())

    def test_half_double_clock_requires_explicit_alias_and_maps_actual_frames(self) -> None:
        state = state_fixture().model_copy(update={'source_a': source_fixture('a', bars=(1,), bpm=60),
            'source_b': source_fixture('b', bars=(1,))})
        with self.assertRaises(MashupError):
            generate(state, self.request())
        plan = generate(state, self.request(vocal_tempo_alias='double'))
        vocal = plan.placements[0]
        self.assertEqual(vocal.target_end_tick, 8 * 960)
        self.assertEqual(vocal.source_end_frame, 192000)
        self.assertIn('tempo_alias_selected', plan.warnings)

    def test_relative_keys_are_a_measurement_not_a_confidence_score(self) -> None:
        state = state_fixture()
        a = state.source_a.timing
        b = state.source_b.timing
        if a is None or b is None:
            self.fail('fixture timing')
        from app.music_analysis_contracts import KeyEstimate
        major = KeyEstimate(status='known', tonic_pc=0, mode='major', method='manual', unknown_reason=None)
        minor = KeyEstimate(status='known', tonic_pc=9, mode='minor', method='manual', unknown_reason=None)
        state = state.model_copy(update={'source_a': state.source_a.model_copy(update={'timing': a.model_copy(update={'key': major})}),
            'source_b': state.source_b.model_copy(update={'timing': b.model_copy(update={'key': minor})})})
        result = candidates(state)[0]
        self.assertEqual(result.key.measurement, 0)
        self.assertEqual(result.key.score, 100)
        self.assertIsNone(result.score)


if __name__ == '__main__':
    unittest.main()
