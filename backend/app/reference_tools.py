"""Small, explicitly bounded lyric and ABC transformations, implemented independently.

Supported ABC is one voice, major/minor keys, notes/rests/chords, ordinary
durations, ties/slurs, and bars. Other syntax is rejected instead of guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
import html
import re
from .reference_contracts import (
    ReferenceAbcRequest, ReferenceAbcResponse, ReferenceErrorCode,
    ReferenceLyricsRequest, ReferenceLyrics, ReferenceLyricLine,
)


class ReferenceToolError(Exception):
    def __init__(self, code: ReferenceErrorCode) -> None:
        self.code = code
        super().__init__(code)


_PC = dict(zip('CDEFGAB', (0, 2, 4, 5, 7, 9, 11)))
_MAJOR = {'C': 0, 'G': 1, 'D': 2, 'A': 3, 'E': 4, 'B': 5, 'F#': 6, 'C#': 7,
          'F': -1, 'Bb': -2, 'Eb': -3, 'Ab': -4, 'Db': -5, 'Gb': -6, 'Cb': -7}
_MINOR = {'A': 0, 'E': 1, 'B': 2, 'F#': 3, 'C#': 4, 'G#': 5, 'D#': 6, 'A#': 7,
          'D': -1, 'G': -2, 'C': -3, 'F': -4, 'Bb': -5, 'Eb': -6, 'Ab': -7}
_SHARPS = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')
_FLATS = ('C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B')
_NOTE = re.compile(r'(?P<acc>\^\^|__|\^|_|=)?(?P<letter>[A-Ga-g])(?P<oct>[,\x27]*)(?P<duration>[0-9]+(?:/[0-9]+)?|/[0-9]+|/)?(?P<tie>-)?')
_DURATION = re.compile(r'[0-9]+(?:/[0-9]+)?|/[0-9]+|/')
_KEY = re.compile(r'([A-G])([#b]?)(m|min|minor|maj|major)?')
_CHORD = re.compile(r'([A-G])([#b]?)(m|maj|min|dim|aug|sus[0-9]*|add[0-9]*|[0-9]*)?(?:/([A-G])([#b]?))?')


@dataclass(frozen=True)
class _Note:
    start: int
    end: int
    midi: int
    duration: str
    tie: str


@dataclass(frozen=True)
class _Score:
    text: str
    body_offset: int
    key: str
    minor: bool
    notes: list[_Note]
    annotations: list[tuple[int, int, str]]
    headers: dict[str, tuple[int, int]]


def _duration(value: str) -> None:
    for part in value.split('/'):
        if part and (len(part) > 4 or not 1 <= int(part) <= 1024):
            raise ReferenceToolError('unsupported_abc')


def _parse_score(text: str) -> _Score:
    if not text.strip() or len(text) > 100_000:
        raise ReferenceToolError('invalid_abc')
    if re.search(r'^\s*%%', text, re.MULTILINE):
        raise ReferenceToolError('unsupported_abc')
    key = ''
    minor = False
    offset = 0
    seen: set[str] = set()
    headers: dict[str, tuple[int, int]] = {}
    for line in text.splitlines(keepends=True):
        raw = line.strip()
        if not raw or raw.startswith('%'):
            offset += len(line)
            continue
        header = re.fullmatch(r'([A-Z]):(.*)', raw)
        if header is None:
            raise ReferenceToolError('unsupported_abc')
        name, value = header.groups()
        if name not in ('X', 'T', 'C', 'O', 'R', 'S', 'N', 'H', 'M', 'L', 'Q', 'K'):
            raise ReferenceToolError('unsupported_abc')
        if name in ('K', 'M', 'L', 'Q') and name in seen:
            raise ReferenceToolError('unsupported_abc')
        seen.add(name)
        headers[name] = (offset, offset + len(line.rstrip('\r\n')))
        if name in ('L', 'M') and value.strip() not in ('C', 'C|'):
            if not re.fullmatch(r'[0-9]+/[0-9]+', value.strip()):
                raise ReferenceToolError('unsupported_abc')
            _duration(value.strip())
        if name == 'Q' and not re.fullmatch(r'(?:1/[1248]=)?[0-9]{1,3}', value.strip()):
            raise ReferenceToolError('unsupported_abc')
        offset += len(line)
        if name == 'K':
            match = _KEY.fullmatch(value.strip())
            if match is None:
                raise ReferenceToolError('unsupported_abc')
            key = match[1] + match[2]
            minor = match[3] in ('m', 'min', 'minor')
            break
    fifths = (_MINOR if minor else _MAJOR).get(key)
    if fifths is None:
        raise ReferenceToolError('unsupported_abc')
    key_acc = {letter: 0 for letter in 'ABCDEFG'}
    for letter in 'FCGDAEB'[:max(fifths, 0)]:
        key_acc[letter] = 1
    for letter in 'BEADGCF'[:max(-fifths, 0)]:
        key_acc[letter] = -1
    state: dict[str, int] = {}
    pending_tie: tuple[str, int, int] | None = None
    slur_depth = 0
    notes: list[_Note] = []
    annotations: list[tuple[int, int, str]] = []
    index = offset
    chord_open = False
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
        elif char == '%':
            newline = text.find('\n', index)
            index = len(text) if newline < 0 else newline + 1
        elif char == '"':
            end = text.find('"', index + 1)
            if end < 0 or end - index > 200 or '\n' in text[index:end]:
                raise ReferenceToolError('unsupported_abc')
            annotations.append((index, end + 1, text[index + 1:end]))
            index = end + 1
        elif char in '|:':
            if chord_open:
                raise ReferenceToolError('unsupported_abc')
            state.clear()
            index += 1
        elif char == '[':
            if chord_open or index + 1 >= len(text) or text[index + 1] not in '^_=ABCDEFGabcdefg':
                raise ReferenceToolError('unsupported_abc')
            chord_open = True
            index += 1
        elif char == ']':
            if not chord_open:
                # The common final bar |] is harmless; every other unmatched close is rejected.
                if index == 0 or text[index - 1] != '|':
                    raise ReferenceToolError('unsupported_abc')
                index += 1
                continue
            chord_open = False
            index += 1
            length = _DURATION.match(text, index)
            if length:
                _duration(length[0])
                index = length.end()
            if index < len(text) and text[index] == '-':
                raise ReferenceToolError('unsupported_abc')
        elif char in '()':
            if char == '(' and index + 1 < len(text) and text[index + 1].isdigit():
                raise ReferenceToolError('unsupported_abc')
            if chord_open:
                raise ReferenceToolError('unsupported_abc')
            slur_depth += 1 if char == '(' else -1
            if not 0 <= slur_depth <= 16:
                raise ReferenceToolError('invalid_abc')
            index += 1
        elif char in 'zZxX':
            if chord_open or pending_tie is not None:
                raise ReferenceToolError('unsupported_abc')
            index += 1
            length = _DURATION.match(text, index)
            if length:
                _duration(length[0])
                index = length.end()
        else:
            match = _NOTE.match(text, index)
            if match is None:
                raise ReferenceToolError('unsupported_abc')
            letter, octave_marks, accidental = match['letter'], match['oct'], match['acc']
            octave = (5 if letter.islower() else 4) + octave_marks.count("'") - octave_marks.count(',')
            identity = letter.upper()
            if accidental:
                state[identity] = {'=': 0, '^': 1, '^^': 2, '_': -1, '__': -2}[accidental]
            midi = 12 * (octave + 1) + _PC[letter.upper()] + state.get(identity, key_acc[letter.upper()])
            if pending_tie is not None:
                tied_letter, tied_octave, tied_midi = pending_tie
                if chord_open or tied_letter != identity or tied_octave != octave or (accidental is not None and midi != tied_midi):
                    raise ReferenceToolError('unsupported_abc')
                midi = tied_midi
            tie = match['tie'] or ''
            if tie and chord_open:
                raise ReferenceToolError('unsupported_abc')
            pending_tie = (identity, octave, midi) if tie else None
            duration = match['duration'] or ''
            _duration(duration)
            if not 0 <= midi <= 127 or len(notes) >= 20_000:
                raise ReferenceToolError('unsupported_abc')
            notes.append(_Note(index, match.end(), midi, duration, tie))
            index = match.end()
    if chord_open or slur_depth != 0 or pending_tie is not None or not notes:
        raise ReferenceToolError('invalid_abc')
    return _Score(text, offset, key, minor, notes, annotations, headers)


def score_pitches(text: str) -> list[int]:
    return [note.midi for note in _parse_score(text).notes]


def _pc(root: str) -> int:
    return (_PC[root[0]] + (1 if root.endswith('#') else -1 if root.endswith('b') else 0)) % 12


def _abc_note(note: _Note, shift: int, prefer_flats: bool) -> str:
    pitch = note.midi + shift
    if not 0 <= pitch <= 127:
        raise ReferenceToolError('range_unavailable')
    name = (_FLATS if prefer_flats else _SHARPS)[pitch % 12]
    prefix = '_' if name.endswith('b') else '^' if name.endswith('#') else '='
    octave = pitch // 12 - 1
    letter = name[0].lower() if octave >= 5 else name[0]
    marks = "'" * (octave - 5) if octave >= 5 else ',' * (4 - octave)
    # Every note is explicit, including naturals; new key signatures never silently re-sharpen them.
    return prefix + letter + marks + note.duration + note.tie


def transform_abc(request: ReferenceAbcRequest) -> ReferenceAbcResponse:
    score = _parse_score(request.abc)
    pitches = [note.midi for note in score.notes]
    shift = request.transpose_semitones
    if request.target_min_midi is not None and request.target_max_midi is not None:
        valid = [extra for extra in range(-24, 25)
                 if min(pitches) + shift + extra >= request.target_min_midi
                 and max(pitches) + shift + extra <= request.target_max_midi]
        if not valid:
            raise ReferenceToolError('range_unavailable')
        shift += min(valid, key=lambda amount: (abs(amount), amount))
    prefer_flats = 'b' in score.key or shift < 0
    names = _FLATS if prefer_flats else _SHARPS
    replacements = [(note.start, note.end, _abc_note(note, shift, prefer_flats)) for note in score.notes]
    for start, end, annotation in score.annotations:
        match = _CHORD.fullmatch(annotation)
        if match:
            chord = names[(_pc(match[1] + match[2]) + shift) % 12] + (match[3] or '')
            if match[4]:
                chord += '/' + names[(_pc(match[4] + (match[5] or '')) + shift) % 12]
            replacements.append((start, end, f'"{chord}"'))
    key = names[(_pc(score.key) + shift) % 12] + ('m' if score.minor else '')
    key_text = 'K:' + key
    if request.tempo_bpm is not None:
        tempo = f'Q:1/4={request.tempo_bpm}'
        tempo_span = score.headers.get('Q')
        if tempo_span is not None:
            replacements.append((*tempo_span, tempo))
        else:
            key_text = tempo + '\n' + key_text
    replacements.append((*score.headers['K'], key_text))
    output = score.text
    for start, end, replacement in sorted(replacements, reverse=True):
        output = output[:start] + replacement + output[end:]
    changed = score_pitches(output)
    if changed != [pitch + shift for pitch in pitches]:
        raise ReferenceToolError('invalid_abc')
    return ReferenceAbcResponse(abc=output, note_count=len(changed), min_midi=min(changed), max_midi=max(changed), applied_transpose_semitones=shift)


_TIME = re.compile(r'(?:(\d{2,}):)?(\d{2}):(\d{2})[.,](\d{3})')


def _seconds(text: str) -> float:
    match = _TIME.fullmatch(text)
    if match is None or int(match[2]) >= 60 or int(match[3]) >= 60:
        raise ReferenceToolError('unsupported_subtitles')
    result = int(match[1] or 0) * 3600 + int(match[2]) * 60 + int(match[3]) + int(match[4]) / 1000
    if result > 600:
        raise ReferenceToolError('duration_limit')
    return result


def transform_lyrics(request: ReferenceLyricsRequest) -> ReferenceLyrics:
    text = request.text.replace('\r\n', '\n').lstrip('\ufeff')
    lines: list[ReferenceLyricLine] = []
    reviewed_sections = False
    if request.format == 'plain':
        reviewed_sections = re.search(r'^\s*\[(?:verse|chorus|bridge|intro|outro|interlude|pre-chorus)\]\s*$', text, re.IGNORECASE | re.MULTILINE) is not None
        for raw in text.splitlines():
            cleaned = raw.strip()
            if cleaned and not re.fullmatch(r'\[(?:verse|chorus|bridge|intro|outro|interlude|pre-chorus)\]', cleaned, re.IGNORECASE):
                if len(cleaned) > 2000:
                    raise ReferenceToolError('size_limit')
                lines.append(ReferenceLyricLine(text=cleaned, language=request.language))
    else:
        for block in re.split(r'\n\s*\n', text.strip()):
            raw_lines = block.splitlines()
            if not raw_lines or raw_lines[0].startswith(('WEBVTT', 'NOTE')):
                continue
            position = next((index for index, line in enumerate(raw_lines) if '-->' in line), None)
            if position is None:
                raise ReferenceToolError('unsupported_subtitles')
            time = re.fullmatch(r'\s*(\S+)\s+-->\s+(\S+)(?:\s+[^\n]+)?\s*', raw_lines[position])
            if time is None:
                raise ReferenceToolError('unsupported_subtitles')
            start, end = _seconds(time[1]), _seconds(time[2])
            cleaned = html.unescape(re.sub(r'<[^>]*>', '', ' '.join(raw_lines[position + 1:]))).strip()
            if end <= start or not cleaned:
                raise ReferenceToolError('unsupported_subtitles')
            if len(cleaned) > 2000:
                raise ReferenceToolError('size_limit')
            lines.append(ReferenceLyricLine(text=cleaned, start_seconds=start, end_seconds=end,
                                           language=request.language, provenance='provided_subtitles'))
    if not lines:
        raise ReferenceToolError('lyrics_empty')
    if len(lines) > 4096:
        raise ReferenceToolError('size_limit')
    if reviewed_sections:
        return ReferenceLyrics(lyrics=text.strip(), lines=lines)
    output: list[str] = []
    for index, line in enumerate(lines):
        if index % request.section_size == 0:
            if index:
                output.append('')
            output.append('[verse]')
        output.append(line.text)
    lyrics = '\n'.join(output)
    if len(lyrics) > 100_000:
        raise ReferenceToolError('size_limit')
    return ReferenceLyrics(lyrics=lyrics, lines=lines)
