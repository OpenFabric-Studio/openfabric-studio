"""Bounded phrase matching. Candidate timing is always subject to user review."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Protocol
import uuid

import numpy as np
from numpy.typing import NDArray

from .audio_quality import RunCommand
from .job_lifecycle import run_owned_thread
from .music_dsp import (MusicDspError, ResolvedDspSource, _disk, _metrics, _pcm_bytes, _root, _source, command_callback)
from .music_dsp_contracts import (AlignmentPhraseDiagnostic, AlignmentRequest, AlignmentResult,
    DspWarning, WarpAnchor, validate_anchors)

MAX_AUDIO_SECONDS = 120
MAX_PHRASE_FRAMES = 2000
MAX_DTW_CELLS = 4000000
MAX_ANCHORS = 4096
FEATURE_METHOD = 'numpy-chroma-mfcc-onset-20ms-v1'
MATCHER_METHOD = 'monotone-phrase-banded-dtw-v1'


@dataclass(frozen=True)
class AlignmentFeatures:
    values: NDArray[np.float32]
    energy_dbfs: NDArray[np.float32]
    hop_sec: float
    duration_sec: float
    sample_rate_hz: int
    boundaries_sec: tuple[float, ...] = ()

    def read(self, start: int, end: int) -> NDArray[np.float32]:
        if not 0 <= start < end <= len(self.values) or end - start > MAX_PHRASE_FRAMES:
            raise MusicDspError('alignment_budget_exceeded')
        return self.values[start:end]


class FeatureSequence(Protocol):
    @property
    def energy_dbfs(self) -> NDArray[np.float32]: ...
    @property
    def hop_sec(self) -> float: ...
    @property
    def duration_sec(self) -> float: ...
    @property
    def sample_rate_hz(self) -> int: ...
    @property
    def boundaries_sec(self) -> tuple[float, ...]: ...
    def read(self, start: int, end: int) -> NDArray[np.float32]: ...


@dataclass(frozen=True)
class StoredAlignmentFeatures:
    path: Path
    energy_dbfs: NDArray[np.float32]
    hop_sec: float
    duration_sec: float
    sample_rate_hz: int
    boundaries_sec: tuple[float, ...]

    def read(self, start: int, end: int) -> NDArray[np.float32]:
        if not 0 <= start < end <= len(self.energy_dbfs) or end - start > MAX_PHRASE_FRAMES:
            raise MusicDspError('alignment_budget_exceeded')
        if self.path.is_symlink():
            raise MusicDspError('dsp_invalid_source')
        size = (end - start) * 27 * 4
        with self.path.open('rb') as stream:
            stream.seek(start * 27 * 4)
            content = stream.read(size)
        if len(content) != size:
            raise MusicDspError('dsp_invalid_source')
        values = np.frombuffer(content, dtype=np.float32).reshape((end - start, 27))
        if not np.all(np.isfinite(values)):
            raise MusicDspError('dsp_invalid_source')
        return values


@dataclass(frozen=True)
class _Phrase:
    start: int
    end: int


def extract_features(samples: NDArray[np.float32], rate: int) -> AlignmentFeatures:
    if (type(rate) is not int or not 8000 <= rate <= 22050 or samples.dtype != np.float32 or samples.ndim != 2
            or samples.shape[1] not in {1, 2} or not 0 < len(samples) <= rate * MAX_AUDIO_SECONDS
            or not np.all(np.isfinite(samples)) or float(np.max(np.abs(samples))) > 16):
        raise MusicDspError('dsp_invalid_source')
    hop = round(rate * .02)
    fft_size = 2 ** math.ceil(math.log2(rate * .05))
    frequencies = np.fft.rfftfreq(fft_size, 1 / rate)
    mel_edges = np.linspace(2595 * math.log10(1 + 30 / 700), 2595 * math.log10(1 + min(9000, rate / 2) / 700), 22)
    edges = 700 * (10 ** (mel_edges / 2595) - 1)
    bank = np.zeros((20, len(frequencies)), dtype=np.float64)
    for index in range(20):
        bank[index] = np.maximum(0, np.minimum((frequencies - edges[index]) / (edges[index + 1] - edges[index]),
            (edges[index + 2] - frequencies) / (edges[index + 2] - edges[index + 1])))
    dct = np.cos(np.pi / 20 * np.arange(1, 14)[:, None] * (np.arange(20) + .5)[None, :])
    band = np.flatnonzero((frequencies >= 55) & (frequencies <= min(7040, rate / 2)))
    pitch = np.rint(69 + 12 * np.log2(frequencies[band] / 440)).astype(np.int64) % 12
    count = math.ceil(len(samples) / hop)
    values = np.zeros((count, 27), dtype=np.float32)
    energy = np.zeros(count, dtype=np.float32)
    previous = np.zeros(len(frequencies), dtype=np.float64)
    taper = np.hanning(fft_size)[:, None]
    for index, start in enumerate(range(0, len(samples), hop)):
        frame = np.zeros((fft_size, samples.shape[1]), dtype=np.float64)
        block = samples[start:start + fft_size]
        frame[:len(block)] = block
        power = np.mean(np.abs(np.fft.rfft(frame * taper, axis=0)) ** 2, axis=1)
        rms = math.sqrt(float(np.mean(np.asarray(samples[start:start + hop], dtype=np.float64) ** 2)))
        energy[index] = max(-200.0, 20 * math.log10(max(1e-10, rms)))
        chroma = np.bincount(pitch, weights=power[band], minlength=12)
        chroma /= max(1e-15, float(np.linalg.norm(chroma)))
        mfcc = dct @ np.log(np.maximum(1e-15, bank @ power))
        mfcc /= max(1e-15, float(np.linalg.norm(mfcc)))
        flux = float(np.sum(np.maximum(0, power - previous))) / max(1e-15, float(np.sum(power)))
        values[index, :12] = .5 * chroma
        values[index, 12:25] = .3 * mfcc
        values[index, 25] = .2 * min(1.0, flux)
        values[index, 26] = .1 * min(1.0, max(0.0, (float(energy[index]) + 80) / 80))
        previous = power
    return AlignmentFeatures(values, energy, hop / rate, len(samples) / rate, rate)


def _phrases(features: FeatureSequence) -> list[_Phrase]:
    active = np.flatnonzero(features.energy_dbfs >= -50)
    if len(active) == 0:
        return []
    groups: list[_Phrase] = []
    start, previous, count = int(active[0]), int(active[0]), 1
    for value in active[1:]:
        index = int(value)
        if (index - previous - 1) * features.hop_sec >= .18:
            if count * features.hop_sec >= .12:
                groups.append(_Phrase(start, previous + 1))
            start, count = index, 0
        previous = index
        count += 1
    if count * features.hop_sec >= .12:
        groups.append(_Phrase(start, previous + 1))
    if len(groups) > 1024:
        raise MusicDspError('alignment_budget_exceeded')
    return groups


def bounded_dtw(reference: NDArray[np.float32], prepared: NDArray[np.float32]) -> tuple[list[tuple[int, int]], float]:
    n, m = len(reference), len(prepared)
    if (not 1 <= n <= MAX_PHRASE_FRAMES or not 1 <= m <= MAX_PHRASE_FRAMES or n * m > MAX_DTW_CELLS):
        raise MusicDspError('alignment_budget_exceeded')
    if (reference.ndim != 2 or prepared.ndim != 2 or reference.shape[1] != 27 or prepared.shape[1] != 27
            or not np.all(np.isfinite(reference)) or not np.all(np.isfinite(prepared))):
        raise MusicDspError('dsp_invalid_source')
    cost = np.empty((n, m), dtype=np.float32)
    for index in range(n):
        difference = prepared - reference[index]
        cost[index] = np.sqrt(np.sum(difference * difference, axis=1))
    accumulated = np.full((n + 1, m + 1), np.inf, dtype=np.float64)
    steps = np.zeros((n, m), dtype=np.uint8)
    accumulated[0, 0] = 0
    radius = max(2, math.ceil(.15 * max(n, m)))
    for i in range(1, n + 1):
        center = i * m / n
        for j in range(max(1, math.floor(center - radius)), min(m, math.ceil(center + radius)) + 1):
            choices = (float(accumulated[i - 1, j - 1]), float(accumulated[i - 1, j]) + .05,
                float(accumulated[i, j - 1]) + .05)
            step = min(range(3), key=choices.__getitem__)
            accumulated[i, j] = choices[step] + float(cost[i - 1, j - 1])
            steps[i - 1, j - 1] = step
    if not math.isfinite(float(accumulated[n, m])):
        raise MusicDspError('dsp_invalid_anchors')
    path: list[tuple[int, int]] = []
    i, j = n, m
    while i and j:
        path.append((i - 1, j - 1))
        step = int(steps[i - 1, j - 1])
        if step in {0, 1}:
            i -= 1
        if step in {0, 2}:
            j -= 1
    path.reverse()
    return path, sum(float(cost[i, j]) for i, j in path) / len(path)


def _descriptor(features: FeatureSequence, phrase: _Phrase) -> NDArray[np.float64]:
    total = np.zeros(25, dtype=np.float64)
    for start in range(phrase.start, phrase.end, MAX_PHRASE_FRAMES):
        block = features.read(start, min(phrase.end, start + MAX_PHRASE_FRAMES))
        total += np.sum(block[:, :25], axis=0, dtype=np.float64)
    return total / (phrase.end - phrase.start)


def _match(reference: FeatureSequence, prepared: FeatureSequence, left: list[_Phrase], right: list[_Phrase]) -> tuple[list[tuple[int | None, int | None]], bool]:
    n, m = len(left), len(right)
    if n + m > 1024:
        raise MusicDspError('alignment_budget_exceeded')
    descriptors_left = [_descriptor(reference, phrase) for phrase in left]
    descriptors_right = [_descriptor(prepared, phrase) for phrase in right]
    ambiguous = any(float(np.linalg.norm(values[i] - values[j])) < .025
        for values in (descriptors_left, descriptors_right) for i in range(len(values)) for j in range(i))
    accumulated = np.full((n + 1, m + 1), np.inf, dtype=np.float64)
    steps = np.zeros((n + 1, m + 1), dtype=np.uint8)
    accumulated[:, 0] = np.arange(n + 1) * .35
    accumulated[0, :] = np.arange(m + 1) * .35
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            a, b = left[i - 1], right[j - 1]
            ratio = (a.end - a.start) * reference.hop_sec / ((b.end - b.start) * prepared.hop_sec)
            distance = float(np.linalg.norm(descriptors_left[i - 1] - descriptors_right[j - 1]))
            match = distance * 3 + abs(math.log(ratio)) if .75 <= ratio <= 1.5 else 2.0
            choices = (float(accumulated[i - 1, j - 1]) + match,
                float(accumulated[i - 1, j]) + .35, float(accumulated[i, j - 1]) + .35)
            step = min(range(3), key=choices.__getitem__)
            accumulated[i, j] = choices[step]
            steps[i, j] = step
    pairs: list[tuple[int | None, int | None]] = []
    i, j = n, m
    while i or j:
        step = 2 if not i else 1 if not j else int(steps[i, j])
        pairs.append((i - 1 if step != 2 else None, j - 1 if step != 1 else None))
        if step in {0, 1}:
            i -= 1
        if step in {0, 2}:
            j -= 1
    pairs.reverse()
    return pairs, ambiguous


def _identity(request: AlignmentRequest, reference: _Phrase | None, prepared: _Phrase | None) -> str:
    return hashlib.sha256(json.dumps([request.reference_sha256, request.prepared_sha256,
        None if reference is None else [reference.start, reference.end],
        None if prepared is None else [prepared.start, prepared.end], MATCHER_METHOD], separators=(',', ':')).encode()).hexdigest()[:32]


def _manual(request: AlignmentRequest, reference_duration: float, prepared_duration: float,
            reference_rate: int, prepared_rate: int) -> AlignmentResult:
    pairs = [(round(anchor.source_sec * prepared_rate), round(anchor.target_sec * reference_rate))
        for anchor in request.manual_anchors]
    if (pairs[-1][0] > round(prepared_duration * prepared_rate) or pairs[-1][1] > round(reference_duration * reference_rate)
            or any(right[0] <= left[0] or right[1] <= left[1] for left, right in zip(pairs, pairs[1:]))):
        raise MusicDspError('dsp_invalid_anchors')
    anchors = [WarpAnchor(source_sec=source / prepared_rate, target_sec=target / reference_rate) for source, target in pairs]
    try:
        validate_anchors(anchors, request.min_duration_ratio, request.max_duration_ratio)
    except ValueError as error:
        raise MusicDspError('dsp_invalid_anchors') from error
    configuration = hashlib.sha256(json.dumps({'request': request.model_dump(), 'rates': [reference_rate, prepared_rate],
        'method': 'manual-frame-anchors-v1'}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    phrase = AlignmentPhraseDiagnostic(phrase_id=configuration[:32], reference_start_sec=anchors[0].target_sec,
        reference_end_sec=anchors[-1].target_sec, prepared_start_sec=anchors[0].source_sec,
        prepared_end_sec=anchors[-1].source_sec, reason='manual')
    return AlignmentResult(id=configuration[:32], request=request, status='needs_review', anchors=anchors,
        phrases=[phrase], feature_method='manual-frame-anchors-v1', matcher_method='manual-frame-anchors-v1',
        configuration_sha256=configuration, warnings=['alignment_unverified'], created_at=datetime.now(timezone.utc).isoformat())


def align_features(request: AlignmentRequest, reference: FeatureSequence, prepared: FeatureSequence) -> AlignmentResult:
    if request.manual_anchors:
        return _manual(request, reference.duration_sec, prepared.duration_sec, reference.sample_rate_hz, prepared.sample_rate_hz)
    left, right = _phrases(reference), _phrases(prepared)
    pairs, ambiguous = _match(reference, prepared, left, right)
    warnings: set[DspWarning] = {'alignment_unverified'}
    anchors: list[WarpAnchor] = []
    diagnostics: list[AlignmentPhraseDiagnostic] = []
    if ambiguous:
        warnings.add('phrase_ambiguous')
    if reference.boundaries_sec or prepared.boundaries_sec:
        warnings.add('anchor_boundary_uncertain')
    for i, j in pairs:
        a, b = left[i] if i is not None else None, right[j] if j is not None else None
        fields: dict[str, object] = {'phrase_id': _identity(request, a, b),
            'reference_start_sec': a.start * reference.hop_sec if a else None,
            'reference_end_sec': min(reference.duration_sec, a.end * reference.hop_sec) if a else None,
            'prepared_start_sec': b.start * prepared.hop_sec if b else None,
            'prepared_end_sec': min(prepared.duration_sec, b.end * prepared.hop_sec) if b else None,
            'reason': 'unmatched_reference' if b is None else 'unmatched_prepared' if a is None else 'aligned'}
        if a is None or b is None:
            warnings.add('unmatched_phrases')
        else:
            try:
                reference_block = reference.read(a.start, a.end)
                prepared_block = prepared.read(b.start, b.end)
                path, cost = bounded_dtw(reference_block, prepared_block)
                fields['mean_cost'] = cost
                if float(np.max(np.std(reference_block[:, :25], axis=0))) < .01:
                    warnings.add('phrase_ambiguous')
                if (any(a.start * reference.hop_sec < boundary < a.end * reference.hop_sec for boundary in reference.boundaries_sec)
                        or any(b.start * prepared.hop_sec < boundary < b.end * prepared.hop_sec for boundary in prepared.boundaries_sec)):
                    fields['reason'] = 'ambiguous'
                selected = [(0, 0)]
                for target_index, source_index in path:
                    previous_target, previous_source = selected[-1]
                    if ((source_index - previous_source) * prepared.hop_sec >= .5
                            and (target_index - previous_target) * reference.hop_sec >= .5
                            and source_index < b.end - b.start - 1 and target_index < a.end - a.start - 1):
                        selected.append((target_index, source_index))
                selected.append((a.end - a.start, b.end - b.start))
                phrase_anchors = [WarpAnchor(source_sec=min(prepared.duration_sec, (b.start + source_index) * prepared.hop_sec),
                    target_sec=min(reference.duration_sec, (a.start + target_index) * reference.hop_sec))
                    for target_index, source_index in selected]
                validate_anchors(phrase_anchors, request.min_duration_ratio, request.max_duration_ratio)
                if len(anchors) + len(phrase_anchors) > MAX_ANCHORS:
                    raise MusicDspError('alignment_budget_exceeded')
                anchors.extend(phrase_anchors)
            except MusicDspError as error:
                if error.code != 'alignment_budget_exceeded':
                    raise
                fields['reason'] = 'budget_exceeded'
                warnings.add('alignment_budget_exceeded')
            except ValueError:
                fields['reason'] = 'unsafe_slope'
                warnings.add('anchor_boundary_uncertain')
        diagnostics.append(AlignmentPhraseDiagnostic.model_validate(fields))
    try:
        if anchors:
            validate_anchors(anchors, request.min_duration_ratio, request.max_duration_ratio)
    except ValueError:
        anchors = []
        warnings.add('anchor_boundary_uncertain')
    configuration = hashlib.sha256(json.dumps({'request': request.model_dump(), 'features': FEATURE_METHOD,
        'matcher': MATCHER_METHOD, 'hop': [reference.hop_sec, prepared.hop_sec], 'threshold_dbfs': -50,
        'min_activity_sec': .12, 'min_silence_sec': .18, 'band_fraction': .15, 'skip_penalty': .35,
        'source_code': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return AlignmentResult(id=configuration[:32], request=request, status='needs_review' if diagnostics else 'unavailable',
        anchors=anchors, phrases=diagnostics, feature_method=FEATURE_METHOD, matcher_method=MATCHER_METHOD,
        configuration_sha256=configuration, warnings=sorted(warnings), created_at=datetime.now(timezone.utc).isoformat())


def _append_features(path: Path, values: NDArray[np.float32]) -> None:
    with path.open('ab') as stream:
        stream.write(values.tobytes())


async def _stream_features(source: ResolvedDspSource, root: Path, run: RunCommand) -> StoredAlignmentFeatures:
    from scripts.music_analysis_worker import ANALYSIS_RATE, decode_window
    duration = source.duration_frames / source.sample_rate_hz
    hop = 441 / ANALYSIS_RATE
    count = math.ceil(duration / hop)
    if count > 360001:
        raise MusicDspError('alignment_budget_exceeded')
    energy = np.empty(count, dtype=np.float32)
    path = root / (uuid.uuid4().hex + '.features.f32')
    await run_owned_thread(path.touch, exist_ok=False)
    boundaries: list[float] = []
    start, written = 0.0, 0
    while start < duration:
        seconds = min(float(MAX_AUDIO_SECONDS), duration - start)
        samples = await decode_window(source.path, root, start, seconds, source.channels, run)
        block = await run_owned_thread(extract_features, samples, ANALYSIS_RATE)
        del samples
        central_start = start if start == 0 else start + 5
        central_end = duration if start + seconds >= duration else start + 115
        low = round((central_start - start) / hop)
        high = min(len(block.values), math.ceil((central_end - start) / hop - 1e-9))
        absolute = round(start / hop) + low
        if absolute != written or not low < high or written + high - low > count:
            raise MusicDspError('dsp_output_invalid')
        await run_owned_thread(_append_features, path, block.values[low:high])
        energy[written:written + high - low] = block.energy_dbfs[low:high]
        written += high - low
        if start + seconds >= duration:
            break
        boundaries.append(central_end)
        start += 110.0
    if written != count or path.stat().st_size != count * 27 * 4:
        raise MusicDspError('dsp_output_invalid')
    return StoredAlignmentFeatures(path, energy, hop, duration, ANALYSIS_RATE, tuple(boundaries))


async def align_prepared_vocal(request: AlignmentRequest, reference: ResolvedDspSource, prepared: ResolvedDspSource,
                               staging_root: Path, run: RunCommand) -> AlignmentResult:
    run = command_callback(run)
    root = _root(staging_root)
    await _source(reference, request.reference, request.reference_sha256)
    await _source(prepared, request.prepared, request.prepared_sha256)
    durations = (reference.duration_frames / reference.sample_rate_hz, prepared.duration_frames / prepared.sample_rate_hz)
    if request.manual_anchors:
        return _manual(request, durations[0], durations[1], reference.sample_rate_hz, prepared.sample_rate_hz)
    # Full-rate measurement is sequential. Two disk feature streams contain at
    # most 2*360000*27*4 bytes; decoded PCM remains one <=120 s window at a time.
    _disk(root, max(_pcm_bytes(value.duration_frames, value.channels) for value in (reference, prepared))
        + sum(math.ceil(duration / .02) * 27 * 4 for duration in durations) + 32 * 1024**2)
    feature_root = root / uuid.uuid4().hex
    await run_owned_thread(feature_root.mkdir)
    blocks: list[StoredAlignmentFeatures] = []
    for index, source in enumerate((reference, prepared)):
        await _metrics(source, root, run, f'alignment-input-{index}')
        blocks.append(await _stream_features(source, feature_root, run))
    result = await run_owned_thread(align_features, request, blocks[0], blocks[1])
    await _source(reference, request.reference, request.reference_sha256)
    await _source(prepared, request.prepared, request.prepared_sha256)
    return result
