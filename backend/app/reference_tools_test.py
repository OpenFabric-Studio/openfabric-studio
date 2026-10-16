from __future__ import annotations

import unittest
from pydantic import ValidationError
from app.reference_contracts import ReferenceAbcRequest, ReferenceLyricsRequest
from app.reference_tools import ReferenceToolError, score_pitches, transform_abc, transform_lyrics


class ReferenceToolsTests(unittest.TestCase):
    def test_target_key_transposes_notes_and_chord_roots_by_nearest_interval(self) -> None:
        cases = [
            ('C', 'D', '"C/G" C2 [EGc]2 | =F F z2', 2, '"D/A"'),
            ('G', 'F', '"G/D" F2 =F2 | F z2', -2, '"F/C"'),
            ('C', 'F#', '"C" C E G', -6, '"F#"'),
            ('C', 'Bb', '"C7/G" C2 E/2 G', -2, '"Bb7/F"'),
            ('Dm', 'F#m', '"Dm/A" D2 [FAc]2 | =B, B,', 4, '"F#m/C#"'),
            ('F#m', 'Ebm', '"F#m/C#" F2 A c', -3, '"Ebm/Bb"'),
        ]
        for source_key, target_key, body, shift, chord in cases:
            with self.subTest(source_key=source_key, target_key=target_key):
                source = f'X:1\nL:1/8\nK:{source_key}\n{body}\n'
                result = transform_abc(ReferenceAbcRequest.model_validate({'abc': source, 'target_key': target_key}))
                self.assertEqual(result.applied_transpose_semitones, shift)
                self.assertEqual(score_pitches(result.abc), [pitch + shift for pitch in score_pitches(source)])
                self.assertIn(f'K:{target_key}\n', result.abc)
                self.assertIn(chord, result.abc)
                for duration in ('2', '/2'):
                    if duration in body:
                        self.assertIn(duration, result.abc)

    def test_target_key_preserves_enharmonic_signature_and_noop_pitch(self) -> None:
        for original, target in [('F#', 'Gb'), ('Gb', 'F#'), ('C#', 'Db'), ('B', 'Cb'), ('A#m', 'Bbm'), ('Ebm', 'D#m'), ('Am', 'Am')]:
            with self.subTest(original=original, target=target):
                source = f'X:1\nK:{original}\nC =F F | F\n'
                result = transform_abc(ReferenceAbcRequest.model_validate({'abc': source, 'target_key': target}))
                self.assertEqual(result.applied_transpose_semitones, 0)
                self.assertEqual(score_pitches(result.abc), score_pitches(source))
                self.assertIn(f'K:{target}\n', result.abc)

    def test_tempo_only_preserves_original_key_header_spelling(self) -> None:
        for key in ['F#', 'Gb', 'Cb', 'C#minor', 'A#min', 'Dbmajor']:
            with self.subTest(key=key):
                source = f'X:1\n K:{key}\nC2 =F | F\n'
                result = transform_abc(ReferenceAbcRequest(abc=source, tempo_bpm=100))
                self.assertIn(f' K:{key}\n', result.abc)
                self.assertEqual(score_pitches(result.abc), score_pitches(source))
                self.assertIn('Q:1/4=100\n', result.abc)

    def test_target_key_refuses_major_minor_conversion(self) -> None:
        for source_key, target_key in [('C', 'Am'), ('Dm', 'D')]:
            with self.subTest(source_key=source_key):
                with self.assertRaises(ReferenceToolError) as error:
                    transform_abc(ReferenceAbcRequest.model_validate({'abc': f'X:1\nK:{source_key}\nC D E', 'target_key': target_key}))
                self.assertEqual(error.exception.code, 'key_mode_mismatch')

    def test_target_key_cannot_be_combined_with_manual_transposition_or_range(self) -> None:
        for settings in [{'transpose_semitones': 1}, {'target_min_midi': 48, 'target_max_midi': 72}]:
            with self.subTest(settings=settings), self.assertRaises(ValidationError):
                ReferenceAbcRequest.model_validate({'abc': 'X:1\nK:C\nC', 'target_key': 'D', **settings})

    def test_meter_is_replaced_or_inserted_before_key_without_rebarring(self) -> None:
        body = 'C2 [EG]2 | z/2 C/2 C-C | C\n'
        for headers in ['X:1\nL:1/8\nK:C\n', 'X:1\n M:C\nL:1/8\n Q:1/8=90\n K:C\n']:
            for meter in ['2/4', '3/4', '4/4', '6/8', '9/8', '12/8']:
                with self.subTest(headers=headers, meter=meter):
                    result = transform_abc(ReferenceAbcRequest.model_validate({'abc': headers + body, 'time_signature': meter, 'tempo_bpm': 120}))
                    self.assertEqual(result.abc.count('M:'), 1)
                    self.assertLess(result.abc.index(f'M:{meter}'), result.abc.index('K:'))
                    self.assertEqual(result.abc.split('K:C\n', 1)[1], body)
                    self.assertEqual(result.abc.count('Q:'), 1)
                    self.assertIn('Q:1/4=120\n', result.abc)
                    self.assertEqual(score_pitches(result.abc), score_pitches(headers + body))

    def test_meter_changes_preserve_implicit_default_note_lengths(self) -> None:
        body = 'C2 D2 E2 F2 | z/2 G/2\n'
        cases = [('M:4/4\n', '2/4', '1/8'), ('M:2/4\n', '4/4', '1/16'),
                 ('M:3/4\n', '2/4', '1/8'), ('M:C\n', '2/4', '1/8'),
                 ('M:C|\n', '2/4', '1/8'), ('', '2/4', '1/8')]
        for original_meter, target_meter, original_length in cases:
            with self.subTest(original_meter=original_meter, target_meter=target_meter):
                result = transform_abc(ReferenceAbcRequest.model_validate({'abc': 'X:1\n' + original_meter + 'K:C\n' + body, 'time_signature': target_meter}))
                self.assertIn(f'L:{original_length}\n', result.abc)
                self.assertLess(result.abc.index('L:'), result.abc.index('K:'))
                self.assertEqual(result.abc.split('K:C\n', 1)[1], body)

    def test_meter_does_not_duplicate_explicit_note_length(self) -> None:
        source = 'X:1\nM:4/4\nL:1/4\nK:C\nC2 D2 E2 F2\n'
        result = transform_abc(ReferenceAbcRequest.model_validate({'abc': source, 'time_signature': '2/4'}))
        self.assertEqual(result.abc.count('L:'), 1)
        self.assertIn('L:1/4\n', result.abc)

    def test_target_key_and_meter_keep_unsupported_notation_rejected(self) -> None:
        for body in ['(3CDE', '[K:D] C', 'C & E']:
            with self.subTest(body=body):
                with self.assertRaises(ReferenceToolError) as error:
                    transform_abc(ReferenceAbcRequest.model_validate({'abc': 'X:1\nK:C\n' + body, 'target_key': 'D', 'time_signature': '3/4'}))
                self.assertEqual(error.exception.code, 'unsupported_abc')

    def test_common_extended_chords_transpose_with_notes(self) -> None:
        source = 'X:1\nK:C\n"Am7/E" C "Cmaj7" E "Gdim7" G\n'
        settings_cases: list[dict[str, object]] = [{'target_key': 'D'}, {'transpose_semitones': 2}]
        for settings in settings_cases:
            with self.subTest(settings=settings):
                result = transform_abc(ReferenceAbcRequest.model_validate({'abc': source, **settings}))
                self.assertIn('"Bm7/F#"', result.abc)
                self.assertIn('"Dmaj7"', result.abc)
                self.assertIn('"Adim7"', result.abc)
                self.assertEqual(score_pitches(result.abc), [62, 66, 69])

    def test_unsupported_chord_symbols_refuse_transposition_but_allow_header_edits(self) -> None:
        settings_cases: list[dict[str, object]] = [{'target_key': 'D'}, {'transpose_semitones': 2}]
        for chord in ['C7#9', 'C#m7b5', 'C(add9)', 'G+', 'Bø7', 'C6/9', 'CM7', 'Cminmaj7', 'Cmmaj7', 'CΔ7', 'C-7']:
            source = f'X:1\nK:C\n"{chord}" C E G\n'
            for settings in settings_cases:
                with self.subTest(chord=chord, settings=settings):
                    with self.assertRaises(ReferenceToolError) as error:
                        transform_abc(ReferenceAbcRequest.model_validate({'abc': source, **settings}))
                    self.assertEqual(error.exception.code, 'unsupported_abc')
            unchanged = transform_abc(ReferenceAbcRequest.model_validate({'abc': source, 'target_key': 'C', 'tempo_bpm': 120, 'time_signature': '3/4'}))
            self.assertIn(f'"{chord}"', unchanged.abc)

    def test_ordinary_text_annotations_survive_transposition(self) -> None:
        source = 'X:1\nK:C\n"Chorus" C "Andante" E "Amore" G "A melody" c\n'
        result = transform_abc(ReferenceAbcRequest.model_validate({'abc': source, 'target_key': 'D'}))
        for annotation in ['Chorus', 'Andante', 'Amore', 'A melody']:
            self.assertIn(f'"{annotation}"', result.abc)
        self.assertEqual(score_pitches(result.abc), [62, 66, 69, 74])

    def test_score_transformations_return_typed_errors_at_output_bounds(self) -> None:
        source = 'X:1\nK:C\nC%'
        source += 'x' * (100_000 - len(source))
        with self.assertRaises(ReferenceToolError) as error:
            transform_abc(ReferenceAbcRequest(abc=source, tempo_bpm=120))
        self.assertEqual(error.exception.code, 'size_limit')
        with self.assertRaises(ReferenceToolError) as error:
            transform_abc(ReferenceAbcRequest.model_validate({'abc': 'X:1\nK:C\nC,,,,,', 'target_key': 'B'}))
        self.assertEqual(error.exception.code, 'range_unavailable')

    def test_key_naturals_and_bar_resets(self) -> None:
        source = 'X:1\nM:4/4\nL:1/8\nQ:1/4=90\nK:G\nF =F F | F ^C C | C\n'
        self.assertEqual(score_pitches(source), [66, 65, 65, 66, 61, 61, 60])
        changed = transform_abc(ReferenceAbcRequest(abc=source, transpose_semitones=2, tempo_bpm=120))
        self.assertEqual(score_pitches(changed.abc), [p + 2 for p in score_pitches(source)])
        self.assertIn('K:A\n', changed.abc)
        self.assertIn('Q:1/4=120\n', changed.abc)

    def test_minor_keys_octaves_duration_and_chords(self) -> None:
        source = 'X:1\nK:Dm\n"Dm" D2 [FAc]2 =B, B, | B, z2\n'
        transformed = transform_abc(ReferenceAbcRequest(abc=source, transpose_semitones=-2))
        self.assertEqual(score_pitches(transformed.abc), [p - 2 for p in score_pitches(source)])
        self.assertIn('K:Cm', transformed.abc)
        self.assertIn('"Cm"', transformed.abc)
        self.assertIn(']2', transformed.abc)

    def test_range_transposes_whole_melody_or_refuses(self) -> None:
        source = 'X:1\nK:C\nc d e\n'
        moved = transform_abc(ReferenceAbcRequest(abc=source, target_min_midi=48, target_max_midi=64))
        self.assertEqual(score_pitches(moved.abc), [60, 62, 64])
        self.assertEqual(moved.applied_transpose_semitones, -12)
        with self.assertRaises(ReferenceToolError) as caught:
            transform_abc(ReferenceAbcRequest(abc=source, target_min_midi=60, target_max_midi=61))
        self.assertEqual(caught.exception.code, 'range_unavailable')

    def test_unsupported_syntax_is_explicitly_rejected(self) -> None:
        for body in ['(3CDE', '[K:D] C', 'C & E', 'C !trill! D', 'C/0', 'K:Ddor\nC', 'V:2\nC']:
            with self.subTest(body=body), self.assertRaises(ReferenceToolError):
                transform_abc(ReferenceAbcRequest(abc='X:1\nK:C\n' + body))

    def test_subtitle_text_preserves_selected_language_and_times(self) -> None:
        subtitles = '1\n00:00:01,250 --> 00:00:03,000\nFirst <i>line</i>\nSecond line\n\n2\n00:00:04,000 --> 00:00:05,500\nFirst line\n'
        result = transform_lyrics(ReferenceLyricsRequest(text=subtitles, format='srt', language='de', section_size=2))
        self.assertEqual(result.lines[0].text, 'First line Second line')
        self.assertEqual(result.lines[0].start_seconds, 1.25)
        self.assertEqual(result.lines[0].end_seconds, 3)
        self.assertEqual(result.lines[0].language, 'de')
        self.assertEqual(result.lines[0].provenance, 'provided_subtitles')
        self.assertEqual(len(result.lines), 2, 'Repeated lyrics are preserved')

    def test_plain_lyrics_have_no_fabricated_timestamps(self) -> None:
        result = transform_lyrics(ReferenceLyricsRequest(text='One\nTwo\nOne'))
        self.assertTrue(all(line.start_seconds is None and line.end_seconds is None for line in result.lines))
        self.assertIn('[verse]', result.lyrics)
        self.assertEqual(len(result.lines), 3)

    def test_invalid_subtitle_timestamps_are_rejected(self) -> None:
        for source in ['00:01:00,000 --> 00:00:50,000\nbackwards', '00:99:00,000 --> 00:99:05,000\ninvalid', 'unrecognised subtitle text']:
            with self.assertRaises(ReferenceToolError):
                transform_lyrics(ReferenceLyricsRequest(text=source, format='srt', language='en'))

    def test_reviewed_plain_section_tags_are_preserved(self) -> None:
        text = '[verse]\nOne\nTwo\n\n[chorus]\nOne again'
        result = transform_lyrics(ReferenceLyricsRequest(text=text, section_size=1))
        self.assertEqual(result.lyrics, text)
        self.assertEqual([line.text for line in result.lines], ['One', 'Two', 'One again'])

    def test_default_abc_accidentals_propagate_across_octaves_until_bar(self) -> None:
        source = 'X:1\nK:C\n^C C, c c\' | C C, c'
        self.assertEqual(score_pitches(source), [61, 49, 73, 85, 60, 48, 72])
        result = transform_abc(ReferenceAbcRequest(abc=source, transpose_semitones=-1))
        self.assertEqual(score_pitches(result.abc), [60, 48, 72, 84, 59, 47, 71])

    def test_tied_note_keeps_its_pitch_across_bar_without_changing_later_notes(self) -> None:
        source = 'X:1\nK:G\n=F-|F F'
        self.assertEqual(score_pitches(source), [65, 65, 66])
        result = transform_abc(ReferenceAbcRequest(abc=source, transpose_semitones=2))
        self.assertEqual(score_pitches(result.abc), [67, 67, 68])

    def test_semantic_directives_and_malformed_chords_are_not_silently_ignored(self) -> None:
        for source in ['%%MIDI transpose 2\nX:1\nK:C\nC', 'X:1\nK:C\n%%propagate-accidentals octave\nC', 'X:1\nK:C\n[C|E]', 'X:1\nK:C\nC-', 'X:1\nK:C\n(C D']:
            with self.subTest(source=source), self.assertRaises(ReferenceToolError):
                score_pitches(source)

    def test_lyric_output_bounds_return_typed_errors(self) -> None:
        for request in [ReferenceLyricsRequest(text='x' * 2001), ReferenceLyricsRequest(text='x\n' * 4097)]:
            with self.assertRaises(ReferenceToolError) as error:
                transform_lyrics(request)
            self.assertEqual(error.exception.code, 'size_limit')

    def test_indented_headers_are_transformed_using_the_parsed_header_spans(self) -> None:
        for source in [' X:1\n K:C\nC D E F|', ' X:1\n Q:1/4=60\n K:C\nC D E F|']:
            with self.subTest(source=source):
                result = transform_abc(ReferenceAbcRequest(abc=source, transpose_semitones=2, tempo_bpm=90))
                self.assertIn('K:D\n', result.abc)
                self.assertEqual(result.abc.count('Q:'), 1)
                self.assertIn('Q:1/4=90\n', result.abc)
                self.assertEqual(score_pitches(result.abc), [62, 64, 66, 67])
