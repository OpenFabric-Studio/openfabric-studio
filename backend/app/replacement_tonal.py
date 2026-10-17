"""Bounded advisory spectra and lagged accompaniment similarity on retained PCM.

These measurements describe sampled audio. Neither correlation nor a closer
spectral envelope identifies a singer, establishes quality, or clears rights.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sys
import uuid

import numpy as np
from numpy.typing import NDArray
import soundfile as sf
from pydantic import TypeAdapter, ValidationError

from .atomic_files import write_object
from .audio_quality import RunCommand
from .contracts import JsonValue
from .job_lifecycle import run_owned_thread
from .music_analysis import MusicAnalysisError
from .music_dsp_contracts import EqBand
from .replacement_pitch import sample_windows
from .replacement_pitch_contracts import DiagnosticArtifactIdentity, DiagnosticWindow
from .replacement_tonal_contracts import (BleedDiagnostic, BleedWindow, MatchingEqDiagnostic,
    ReplacementTonalReport, ReplacementTonalWorkerOutcome, ReplacementTonalWorkerRequest, TonalArtifactBasis, TonalBand,
    TonalErrorCode, TonalFrequency, TonalRole, TonalUnknown, TonalWarning)
from .video_io import hash_file
from .video_media import tool
from .video_projects import VideoProjectError

RATE = 22050
FREQUENCIES: tuple[TonalFrequency, ...] = (125, 250, 500, 1000, 2000, 4000)
FFT = 2048
HOP = 1024
MAX_LAG = math.floor(RATE * .25)
WARNINGS: tuple[TonalWarning, ...] = ('sampled_passages_only', 'amplitude_activity_not_voicing',
    'spectral_difference_not_quality', 'vowels_and_timbre_affect_spectra',
    'correlation_not_singer_detection', 'shared_instrumentation_can_correlate',
    'low_correlation_not_absence_of_bleed', 'correlation_thresholds_uncalibrated', 'user_review_required')
_json: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


class TonalError(Exception):
    def __init__(self, code: TonalErrorCode) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class _Audio:
    basis: TonalArtifactBasis
    samples: tuple[NDArray[np.float32], ...]
    masks: tuple[NDArray[np.bool_], ...]


def _header(root: Path, identity: DiagnosticArtifactIdentity) -> Path:
    path = root / (identity.artifact_id + '.wav')
    if root.is_symlink() or root.resolve() != root or not root.is_dir() or path.is_symlink() or not path.is_file():
        raise TonalError('replacement_tonal_source_changed')
    try:
        info = sf.info(path)
    except (RuntimeError, OSError) as error:
        raise TonalError('replacement_tonal_invalid_audio') from error
    if (info.samplerate != identity.sample_rate_hz or info.channels != identity.channels
            or info.frames != identity.duration_frames):
        raise TonalError('replacement_tonal_source_changed')
    return path


async def _verify(root: Path, identity: DiagnosticArtifactIdentity) -> Path:
    path = await run_owned_thread(_header, root, identity)
    try:
        checksum = await hash_file(path, max_bytes=identity.duration_frames * identity.channels * 8 + 65536)
    except (OSError, ValueError, VideoProjectError) as error:
        raise TonalError('replacement_tonal_source_changed') from error
    if checksum != identity.sha256:
        raise TonalError('replacement_tonal_source_changed')
    return path


def _activity(samples: tuple[NDArray[np.float32], ...]) -> tuple[NDArray[np.bool_], ...]:
    hop = round(.02 * RATE)
    energies: list[NDArray[np.float64]] = []
    for audio in samples:
        blocks = [math.sqrt(float(np.mean(np.asarray(audio[start:start + hop], dtype=np.float64) ** 2)))
            for start in range(0, len(audio), hop)]
        energies.append(np.asarray(blocks, dtype=np.float64))
    levels = np.concatenate(energies)
    threshold = max(.001, float(np.percentile(levels, 95)) * 10 ** (-35 / 20))
    return tuple(np.asarray(np.repeat(level >= threshold, hop)[:len(audio)], dtype=np.bool_)
        for level, audio in zip(energies, samples))


async def _collect(role: TonalRole, identity: DiagnosticArtifactIdentity, path: Path,
                   windows: list[DiagnosticWindow], root: Path, run: RunCommand) -> _Audio:
    # The existing decoder anti-aliases, bounds output bytes, and delegates every
    # child to the caller's owned command callback (or the owned outer worker).
    from scripts.music_analysis_worker import decode_window
    samples: list[NDArray[np.float32]] = []
    for window in windows:
        seconds = (window.end_frame - window.start_frame) / identity.sample_rate_hz
        try:
            decoded = await decode_window(path, root, window.start_frame / identity.sample_rate_hz,
                seconds, identity.channels, run)
        except MusicAnalysisError as error:
            raise TonalError('replacement_tonal_invalid_audio') from error
        expected = round(seconds * RATE)
        decoded = decoded[:expected]
        if not len(decoded) or not np.isfinite(decoded).all() or float(np.max(np.abs(decoded))) > 16:
            raise TonalError('replacement_tonal_invalid_audio')
        samples.append(decoded)
    audio = tuple(samples)
    masks = await run_owned_thread(_activity, audio)
    return _Audio(TonalArtifactBasis(role=role, identity=identity, windows=windows,
        active_samples=sum(int(np.count_nonzero(mask)) for mask in masks)), audio, masks)


def _spectrum(audio: _Audio, upper: float) -> tuple[float | None, ...]:
    frequencies = np.fft.rfftfreq(FFT, 1 / RATE)
    broadband = (frequencies >= 80) & (frequencies <= upper)
    taper = np.hanning(FFT)[:, None]
    measurements: list[list[float]] = [[] for _ in FREQUENCIES]
    if audio.basis.active_samples < RATE:
        return (None,) * len(FREQUENCIES)
    for samples, mask in zip(audio.samples, audio.masks):
        current: list[list[float]] = [[] for _ in FREQUENCIES]
        for start in range(0, len(samples) - FFT + 1, HOP):
            if np.mean(mask[start:start + FFT]) < .8:
                continue
            frame = np.asarray(samples[start:start + FFT], dtype=np.float64)
            # Average powers after channel transforms: inverted stereo must not cancel.
            power = np.mean(np.abs(np.fft.rfft((frame - np.mean(frame, axis=0)) * taper, axis=0)) ** 2, axis=1)
            total = float(np.sum(power[broadband]))
            if total <= 1e-20:
                continue
            for index, center in enumerate(FREQUENCIES):
                if center * math.sqrt(2) > upper:
                    continue
                selection = (frequencies >= center / math.sqrt(2)) & (frequencies < center * math.sqrt(2))
                fraction = float(np.sum(power[selection])) / total
                if fraction >= 1e-5:
                    current[index].append(min(0.0, max(-160.0, 10 * math.log10(fraction))))
        for index, values in enumerate(current):
            if len(values) >= 8:
                measurements[index].append(float(np.median(values)))
    return tuple(float(np.median(values)) if values else None for values in measurements)


def _distance(differences: list[float]) -> float:
    centered = np.asarray(differences, dtype=np.float64) - float(np.median(differences))
    return float(np.mean(np.abs(centered)))


def _matching(reference: _Audio, candidate: _Audio | None, matched: _Audio | None) -> MatchingEqDiagnostic:
    identities = [reference.basis.identity, *([candidate.basis.identity] if candidate is not None else []),
        *([matched.basis.identity] if matched is not None else [])]
    upper = min(10000.0, .45 * min(identity.sample_rate_hz for identity in identities))
    reference_values = _spectrum(reference, upper)
    candidate_values = _spectrum(candidate, upper) if candidate is not None else (None,) * 6
    matched_values = _spectrum(matched, upper) if matched is not None else (None,) * 6
    differences = {index: original - new for index, (original, new) in enumerate(zip(reference_values, candidate_values))
        if original is not None and new is not None}
    gains: dict[int, float] = {}
    if len(differences) >= 3:
        common = float(np.median(list(differences.values())))
        centered = {index: value - common for index, value in differences.items()}
        for index in differences:
            neighbors = [centered[neighbor] for neighbor in range(max(0, index - 1), min(6, index + 2)) if neighbor in centered]
            value = max(-6.0, min(6.0, .5 * float(np.median(neighbors))))
            gains[index] = value if abs(value) >= .25 else 0.0
        boost = sum(max(0.0, gain) for gain in gains.values())
        if boost > 6:
            factor = math.nextafter(6.0, 0.0) / boost
            gains = {index: gain * factor if gain > 0 else gain for index, gain in gains.items()}
    bands: list[TonalBand] = []
    for index, frequency in enumerate(FREQUENCIES):
        if index in differences:
            bands.append(TonalBand(frequency_hz=frequency, status='supported', reference_db=reference_values[index],
                new_db=candidate_values[index], matched_db=matched_values[index], difference_db=differences[index],
                suggested_gain_db=gains.get(index, 0.0)))
        else:
            reason: TonalUnknown = ('missing_new_vocal' if candidate is None else 'bandwidth_unavailable'
                if frequency * math.sqrt(2) > upper else 'insufficient_active_audio')
            bands.append(TonalBand(frequency_hz=frequency, status='unsupported', reason=reason))
    if len(differences) < 3:
        reason = ('missing_new_vocal' if candidate is None else 'insufficient_active_audio'
            if min(reference.basis.active_samples, candidate.basis.active_samples) < RATE else 'insufficient_bands')
        return MatchingEqDiagnostic(status='inconclusive', reason=reason, bands=bands)
    post = [original - new for index in differences
        if (original := reference_values[index]) is not None and (new := matched_values[index]) is not None]
    return MatchingEqDiagnostic(status='available', bands=bands,
        suggested_eq_bands=[EqBand(frequency_hz=float(FREQUENCIES[index]), q=1.0, gain_db=gain)
            for index, gain in gains.items() if gain], before_difference_db=_distance(list(differences.values())),
        after_difference_db=_distance(post) if len(post) == len(differences) else None)


def _lagged(x: NDArray[np.float64], y: NDArray[np.float64], mask: NDArray[np.bool_]
            ) -> tuple[float, int, int, bool] | None:
    count = min(len(x), len(y), len(mask))
    x, y = x[:count], y[:count]
    weight = np.asarray(mask[:count], dtype=np.float64)
    if np.sum(weight) < RATE:
        return None
    size = 1 << (2 * count - 1).bit_length()
    lags = np.arange(-min(MAX_LAG, count - 1), min(MAX_LAG, count - 1) + 1)
    low, high = np.maximum(0, lags), np.minimum(count, count + lags)
    def sums(values: NDArray[np.float64]) -> NDArray[np.float64]:
        prefix = np.concatenate((np.zeros(1), np.cumsum(values)))
        return np.asarray(prefix[high] - prefix[low], dtype=np.float64)
    support, sx, sx2 = sums(weight), sums(weight * x), sums(weight * x * x)
    positions = lags % size
    weighted = np.fft.rfft(weight, size)
    fy = np.conj(np.fft.rfft(y, size))
    sy = np.fft.irfft(weighted * fy, size)[positions]
    sy2 = np.fft.irfft(weighted * np.conj(np.fft.rfft(y * y, size)), size)[positions]
    sxy = np.fft.irfft(np.fft.rfft(weight * x, size) * fy, size)[positions]
    safe_support = np.maximum(1, support)
    vx, vy = np.maximum(0.0, sx2 - sx * sx / safe_support), np.maximum(0.0, sy2 - sy * sy / safe_support)
    valid = (support >= RATE) & (vx > safe_support * 1e-12) & (vy > safe_support * 1e-12)
    correlation = np.zeros(len(lags), dtype=np.float64)
    np.divide(sxy - sx * sy / safe_support, np.sqrt(vx * vy), out=correlation, where=valid)
    correlation = np.clip(correlation, -1, 1)
    if not np.any(valid):
        return None
    absolute = np.where(valid, np.abs(correlation), -1)
    maximum = float(np.max(absolute))
    tied = np.flatnonzero(absolute >= maximum - 1e-9)
    selected = min((int(index) for index in tied), key=lambda index: (abs(int(lags[index])), int(lags[index])))
    lag = int(lags[selected])
    ambiguous = bool(np.any((absolute >= maximum - .02) & (np.abs(lags - lag) > round(.002 * RATE))))
    return float(correlation[selected]), lag, int(support[selected]), ambiguous


def _bleed(vocal: _Audio, backing: _Audio, reference: _Audio, *, aligned: bool) -> BleedDiagnostic:
    vocal_id, backing_id = vocal.basis.identity.artifact_id, backing.basis.identity.artifact_id
    if not aligned:
        return BleedDiagnostic(vocal_artifact_id=vocal_id, backing_artifact_id=backing_id,
            status='inconclusive', reason='timeline_unmapped', correlation_strength='inconclusive')
    observations: list[BleedWindow] = []
    for index, (voice, accompaniment, vocal_mask, source_mask) in enumerate(zip(vocal.samples,
            backing.samples, vocal.masks, reference.masks)):
        count = min(len(voice), len(accompaniment), len(vocal_mask), len(source_mask))
        mask = vocal_mask[:count] & source_mask[:count]
        best: BleedWindow | None = None
        for vocal_channel in range(voice.shape[1]):
            for backing_channel in range(accompaniment.shape[1]):
                result = _lagged(np.asarray(voice[:count, vocal_channel], dtype=np.float64),
                    np.asarray(accompaniment[:count, backing_channel], dtype=np.float64), mask)
                if result is None:
                    continue
                signed, lag, support, ambiguous = result
                observation = BleedWindow(window_index=index, active_samples=support, vocal_channel=vocal_channel,
                    backing_channel=backing_channel, signed_correlation=signed, absolute_correlation=abs(signed),
                    lag_frames=lag, lag_ms=lag / RATE * 1000,
                    polarity='inverted' if signed < 0 else 'positive', lag_ambiguous=ambiguous)
                if best is None or observation.absolute_correlation > best.absolute_correlation + 1e-8:
                    best = observation
        if best is not None:
            observations.append(best)
    if not observations:
        reason: TonalUnknown = ('insufficient_active_audio' if vocal.basis.active_samples < RATE
            or reference.basis.active_samples < RATE else 'no_variable_signal')
        return BleedDiagnostic(vocal_artifact_id=vocal_id, backing_artifact_id=backing_id,
            status='inconclusive', reason=reason, correlation_strength='inconclusive')
    value = float(np.median([observation.absolute_correlation for observation in observations]))
    return BleedDiagnostic(vocal_artifact_id=vocal_id, backing_artifact_id=backing_id, status='measured',
        correlation_strength='high' if value >= .6 else 'medium' if value >= .3 else 'weak',
        median_absolute_correlation=value, windows=observations)


async def analyze_tonal(request: ReplacementTonalWorkerRequest, work_root: Path, run: RunCommand) -> ReplacementTonalReport:
    """Measure owned artifact copies; the callback must own/drain all subprocesses.

    At most four artifacts x60 seconds of retained, decoded windows are held.
    No filters are applied to the retained input files or selected automatically.
    """
    if work_root.is_symlink() or work_root.resolve() != work_root or not work_root.is_dir():
        raise TonalError('replacement_tonal_unavailable')
    artifact_root = Path(request.artifact_root)
    inputs: list[tuple[TonalRole, DiagnosticArtifactIdentity]] = [('source_vocal', request.source_vocal), ('backing', request.backing)]
    if request.new_vocal is not None:
        inputs.append(('new_vocal', request.new_vocal))
    if request.matched_vocal is not None:
        inputs.append(('matched_vocal', request.matched_vocal))
    paths = {role: await _verify(artifact_root, identity) for role, identity in inputs}
    runtime = await run([tool('ffmpeg'), '-version'], 15.0)
    try:
        version = runtime.decode('utf-8', errors='strict').splitlines()[0][:512]
    except (UnicodeError, IndexError) as error:
        raise TonalError('replacement_tonal_unavailable') from error
    aligned = [identity for role, identity in inputs if role != 'new_vocal' or request.new_vocal_aligned]
    duration = min(identity.duration_sec for identity in aligned)
    source_rate = request.source_vocal.sample_rate_hz
    common = sample_windows(min(request.source_vocal.duration_frames, math.floor(duration * source_rate)), source_rate)
    audio: dict[TonalRole, _Audio] = {}
    for role, identity in inputs:
        windows = (sample_windows(identity.duration_frames, identity.sample_rate_hz)
            if role == 'new_vocal' and not request.new_vocal_aligned else [DiagnosticWindow(
                start_frame=round(window.start_frame / source_rate * identity.sample_rate_hz),
                end_frame=min(identity.duration_frames, round(window.end_frame / source_rate * identity.sample_rate_hz))) for window in common])
        audio[role] = await _collect(role, identity, paths[role], windows, work_root, run)
    matching = await run_owned_thread(_matching, audio['source_vocal'], audio.get('new_vocal'), audio.get('matched_vocal'))
    bleed = [await run_owned_thread(_bleed, audio['source_vocal'], audio['backing'], audio['source_vocal'], aligned=True)]
    if request.new_vocal is not None:
        bleed.append(await run_owned_thread(_bleed, audio['new_vocal'], audio['backing'], audio['source_vocal'],
            aligned=request.new_vocal_aligned))
    for _, identity in inputs:
        await _verify(artifact_root, identity)
    warnings = list(WARNINGS)
    if any(window.lag_ambiguous for measurement in bleed for window in measurement.windows):
        warnings.append('lag_ambiguous')
    if any(abs(identity.duration_sec - duration) > 1 / identity.sample_rate_hz for identity in aligned):
        warnings.append('overlapping_timeline_only')
    configuration = hashlib.sha256(json.dumps({'request': request.model_dump(exclude={'artifact_root'}),
        'ffmpeg': hashlib.sha256(runtime).hexdigest(), 'numpy': np.__version__,
        'source': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, sort_keys=True).encode()).hexdigest()
    return ReplacementTonalReport(plan_id=request.plan_id, revision=request.revision,
        configuration_sha256=configuration, ffmpeg_version=version, numpy_version=np.__version__,
        artifacts=[item.basis for item in audio.values()], matching_eq=matching, bleed=bleed, warnings=warnings)


async def analyze_owned_tonal(request: ReplacementTonalWorkerRequest, work_root: Path,
                              run: RunCommand) -> ReplacementTonalReport:
    """Delegate the CPU worker to the existing durable replacement supervisor."""
    if work_root.is_symlink() or work_root.resolve() != work_root or not work_root.is_dir():
        raise TonalError('replacement_tonal_unavailable')
    root = work_root / ('tonal-' + uuid.uuid4().hex)
    await run_owned_thread(root.mkdir)
    input_path, output_path = root / 'input.json', root / 'output.json'
    payload = _json.validate_json(request.model_dump_json())
    if not isinstance(payload, dict):
        raise TonalError('replacement_tonal_invalid_audio')
    await run_owned_thread(write_object, input_path, payload)
    input_sha256 = hashlib.sha256(await run_owned_thread(input_path.read_bytes)).hexdigest()
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'replacement_tonal_worker.py'
    try:
        await run([sys.executable, str(script), str(input_path), str(output_path)], 240.0)
        if output_path.is_symlink() or not output_path.is_file() or output_path.stat().st_size > 131072:
            raise TonalError('replacement_tonal_unavailable')
        outcome = ReplacementTonalWorkerOutcome.model_validate_json(await run_owned_thread(output_path.read_bytes))
    except (OSError, ValueError, ValidationError) as error:
        raise TonalError('replacement_tonal_unavailable') from error
    if outcome.input_sha256 != input_sha256:
        raise TonalError('replacement_tonal_source_changed')
    if outcome.error_code is not None:
        raise TonalError(outcome.error_code)
    report = outcome.result
    if report is None:
        raise TonalError('replacement_tonal_unavailable')
    expected: dict[TonalRole, DiagnosticArtifactIdentity] = {'source_vocal': request.source_vocal, 'backing': request.backing}
    if request.new_vocal is not None:
        expected['new_vocal'] = request.new_vocal
    if request.matched_vocal is not None:
        expected['matched_vocal'] = request.matched_vocal
    if (report.plan_id != request.plan_id or report.revision != request.revision
            or {item.role: item.identity for item in report.artifacts} != expected):
        raise TonalError('replacement_tonal_source_changed')
    return report
