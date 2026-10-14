"""Confirmed ranges remain declarations; pitch observations never become singer ability."""
from __future__ import annotations

import hashlib
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy as np
from pydantic import ValidationError

from app import artists, db, guide_range, singer_ranges as ranges, voice_coverage
from app.artist_contracts import CreateArtistRequest
from app.artist_production_test_fixtures import tone_wav_bytes
from app.prep_vocal import AudioArray
from app.singer_range_contracts import (GuideRangeInput, PlannedNote, RangeApproval, RangeAssessment,
    RangePolicy, SingerRangeRequest)
from app.voice_contracts import VoiceCoverageMeasurement, VoicePitchBin


class SingerRangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        for name, value in (('DATA_DIR', self.root), ('DB_PATH', self.root / 'catalog.db'), ('_db', None)):
            self.enterContext(patch.object(db, name, value))
        artists.migrate(db.get_db())
        ranges.migrate(db.get_db())
        self.addCleanup(self.close_database)
        self.artist = artists.create_artist(CreateArtistRequest(name='Synthetic fixture'))

    def close_database(self) -> None:
        if db._db is not None:
            db._db.close()
            db._db = None

    def request(self, policy: RangePolicy = 'warn') -> SingerRangeRequest:
        return SingerRangeRequest(comfortable_low_midi=60, comfortable_high_midi=72,
            extended_low_midi=57, extended_high_midi=76, policy=policy, chosen_key='C major',
            operator_label='Local operator', operator_declaration='I recorded the range explicitly confirmed by the artist.',
            artist_confirmation_declaration='The artist confirms that these are their current comfortable and extended ranges.')

    def notes(self) -> list[PlannedNote]:
        return [PlannedNote(start_sec=index, end_sec=index + 1, midi_note=note)
            for index, note in enumerate((60, 72, 57, 81))]

    def test_confirmation_boundaries_and_note_times_are_validated(self) -> None:
        valid = self.request().model_dump()
        for fields in ({'comfortable_low_midi': 73}, {'comfortable_high_midi': 128},
                       {'extended_low_midi': 61}, {'extended_high_midi': None},
                       {'artist_confirmation_declaration': True}, {'artist_confirmation_declaration': ''},
                       {'operator_declaration': ''}, {'authenticated_actor': 'invented user'}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                SingerRangeRequest.model_validate({**valid, **fields})
        for values in ({'start_sec': 1, 'end_sec': 1, 'midi_note': 60},
                       {'start_sec': 0, 'end_sec': float('nan'), 'midi_note': 60},
                       {'start_sec': 0, 'end_sec': 1, 'midi_note': True}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                PlannedNote.model_validate(values)

    def test_profiles_are_append_only_and_confirmed_bounds_survive_reopen(self) -> None:
        first = ranges.save_profile(self.artist.id, self.request())
        second = ranges.save_profile(self.artist.id, self.request('block'))
        self.assertNotEqual(first.id, second.id)
        self.assertEqual((first.revision, second.revision), (1, 2))
        self.assertIsNone(second.authenticated_actor)
        self.assertFalse(second.inferred_from_pitch)
        for sql in ("UPDATE singer_range_profiles SET payload_json='{}' WHERE id=?",
                    'DELETE FROM singer_range_profiles WHERE id=?'):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                db.get_db().execute(sql, (first.id,))
            db.get_db().rollback()
        self.close_database()
        ranges.migrate(db.get_db())
        self.assertEqual(ranges.get_profile(self.artist.id), second)

    def test_each_policy_reports_exact_out_of_range_notes_without_transposition(self) -> None:
        for policy, decision in (('warn', 'allow'), ('block', 'blocked'), ('needs_approval', 'needs_approval')):
            with self.subTest(policy=policy):
                request = SingerRangeRequest.model_validate({**self.request().model_dump(), 'policy': policy})
                ranges.save_profile(self.artist.id, request)
                result = ranges.assess_notes(self.artist.id, self.notes())
                self.assertEqual(result.decision, decision)
                self.assertEqual([note.midi_note for note in result.out_of_range_notes], [57, 81])
                self.assertEqual([note.outside_extended_range for note in result.out_of_range_notes], [False, True])
                self.assertEqual(result.chosen_key, 'C major')
                self.assertFalse(result.audio_transposed)
        profile = ranges.save_profile(self.artist.id, self.request('block'))
        result = ranges.assess_notes(self.artist.id, self.notes()[:2])
        self.assertEqual(result.decision, 'allow')
        self.assertEqual(result.profile_version_id, profile.id)

    def test_approval_binds_exact_profile_and_assessment_and_cannot_override_block(self) -> None:
        profile = ranges.save_profile(self.artist.id, self.request('needs_approval'))
        initial = ranges.assess_notes(self.artist.id, self.notes())
        approval = RangeApproval(profile_version_id=profile.id, assessment_sha256=initial.assessment_sha256,
            evidence_ref='review:artist-listening', operator_label='Local operator',
            artist_confirmation_declaration='The artist reviewed these exact notes and confirms permission to proceed.')
        approved = ranges.assess_notes(self.artist.id, self.notes(), approval)
        self.assertEqual(approved.decision, 'allow')
        self.assertTrue(approved.approval_applied)
        changed_notes = [PlannedNote(start_sec=0, end_sec=1, midi_note=82)]
        self.assertEqual(ranges.assess_notes(self.artist.id, changed_notes, approval).decision, 'needs_approval')
        ranges.save_profile(self.artist.id, self.request('block'))
        self.assertEqual(ranges.assess_notes(self.artist.id, self.notes(), approval).decision, 'blocked')

    def test_unknown_notes_fail_closed_for_strict_policies_and_warn_explicitly(self) -> None:
        missing = ranges.assess_notes(self.artist.id, [])
        self.assertEqual((missing.analysis_status, missing.decision), ('unknown', 'needs_approval'))
        for policy in ('warn', 'block', 'needs_approval'):
            with self.subTest(policy=policy):
                ranges.save_profile(self.artist.id, SingerRangeRequest.model_validate({**self.request().model_dump(), 'policy': policy}))
                result = ranges.assess_notes(self.artist.id, [])
                self.assertEqual(result.analysis_status, 'unknown')
                self.assertEqual(result.decision, 'allow' if policy == 'warn' else 'blocked' if policy == 'block' else 'needs_approval')
                self.assertIn('no_reliable_pitch', result.warnings)

    def test_observed_coverage_is_a_suggestion_and_never_updates_confirmed_bounds(self) -> None:
        profile = ranges.save_profile(self.artist.id, self.request())
        observed = VoiceCoverageMeasurement(duration_sec=4, analyzed_sec=3.8, voiced_sec=3, reliable_voiced_sec=3,
            source_sample_rate=16000, pitch_bins=[VoicePitchBin(midi_note=57, seconds=1), VoicePitchBin(midi_note=81, seconds=2)])
        result = ranges.assess_coverage(self.artist.id, observed, start_sec=20)
        self.assertEqual((result.observed_low_midi, result.observed_high_midi), (57, 81))
        self.assertTrue(all(note.timing_basis == 'observed_window' for note in result.out_of_range_notes))
        self.assertTrue(all(note.start_sec == 20 and note.end_sec == 24 for note in result.out_of_range_notes))
        self.assertEqual([note.observed_seconds for note in result.out_of_range_notes], [1, 2])
        self.assertIn('observed_pitch_is_not_full_vocal_range', result.warnings)
        self.assertEqual(ranges.get_profile(self.artist.id), profile)

    def manifest(self, policy: RangePolicy = 'block') -> GuideRangeInput:
        path = self.root / 'guide.wav'
        audio = tone_wav_bytes(seconds=12, rate=16000)
        path.write_bytes(audio)
        return GuideRangeInput(artist_id=self.artist.id, profile=ranges.save_profile(self.artist.id, self.request(policy)),
            guide_path=str(path), guide_sha256=hashlib.sha256(audio).hexdigest())

    def test_guide_streams_existing_pyin_windows_and_keeps_original_offsets(self) -> None:
        manifest = self.manifest()
        calls: list[float] = []
        def estimator(audio: AudioArray, *, sr: int, fmin: float, fmax: float, frame_length: int,
                      hop_length: int, center: bool, resolution: float, fill_na: float) -> object:
            calls.append(len(audio) / sr)
            count = (len(audio) - frame_length) // hop_length + 1
            frequency = (220, 440, 880)[len(calls) - 1]
            return (np.full(count, frequency, dtype=np.float64), np.ones(count, dtype=np.bool_),
                    np.full(count, .95, dtype=np.float64))
        original = Path(manifest.guide_path).read_bytes()
        with patch.object(voice_coverage, '_pitch_estimator', return_value=estimator):
            result = guide_range.analyze_guide(manifest)
        self.assertEqual(calls, [5, 5, 2])
        self.assertEqual([(note.start_sec, note.end_sec, note.midi_note) for note in result.out_of_range_notes],
            [(0, 5, 57), (10, 12, 81)])
        self.assertEqual(result.decision, 'blocked')
        self.assertEqual(Path(manifest.guide_path).read_bytes(), original)

    def test_failed_analysis_wrong_hash_and_over_limit_guides_return_explicit_unknown(self) -> None:
        for policy in ('warn', 'block'):
            request = self.manifest('warn' if policy == 'warn' else 'block')
            with patch.object(voice_coverage, '_pitch_estimator', side_effect=voice_coverage.CoverageError('engine_incompatible')):
                result = guide_range.analyze_guide(request)
            self.assertEqual(result.analysis_status, 'unknown')
            self.assertEqual(result.decision, 'allow' if policy == 'warn' else 'blocked')
            self.assertIn('analysis_failed', result.warnings)
        manifest = self.manifest()
        changed = GuideRangeInput.model_validate({**manifest.model_dump(), 'guide_sha256': '0' * 64})
        with patch.object(voice_coverage, 'analyze_coverage') as analysis:
            result = guide_range.analyze_guide(changed)
        analysis.assert_not_called()
        self.assertEqual((result.analysis_status, result.decision), ('unknown', 'blocked'))
        with patch.object(guide_range, 'MAX_GUIDE_SECONDS', 1):
            self.assertEqual(guide_range.analyze_guide(manifest).analysis_status, 'unknown')

    def test_cli_emits_a_typed_unknown_assessment_on_decode_failure(self) -> None:
        manifest = self.manifest()
        path = Path(manifest.guide_path)
        path.write_bytes(b'invalid audio')
        manifest = GuideRangeInput.model_validate({**manifest.model_dump(), 'guide_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        file = self.root / 'manifest.json'
        file.write_text(manifest.model_dump_json())
        output = io.StringIO()
        with redirect_stdout(output):
            code = guide_range.main(['guide_range', '--manifest', str(file)])
        self.assertEqual(code, 0)
        result = RangeAssessment.model_validate_json(output.getvalue())
        self.assertEqual((result.analysis_status, result.decision), ('unknown', 'blocked'))
        self.assertNotIn(str(self.root), output.getvalue())


if __name__ == '__main__':
    unittest.main()
