from __future__ import annotations

import unittest
from app.reference_contracts import ReferenceAbcRequest, ReferenceLyricsRequest
from app.reference_tools import ReferenceToolError, score_pitches, transform_abc, transform_lyrics


class ReferenceToolsTests(unittest.TestCase):
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
