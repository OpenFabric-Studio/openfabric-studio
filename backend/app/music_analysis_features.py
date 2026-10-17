"""Bounded NumPy window summaries; these heuristics do not establish musical structure."""
from __future__ import annotations

import math
from typing import Literal
import uuid

import numpy as np
from numpy.typing import NDArray

from .music_analysis_contracts import (
    AnalysisEnergyPoint, AnalysisSection, KeyCandidate, KeyChange, KeyEstimate, MusicAnalysisFeatures, MusicAnalysisWarning,
)

FEATURES_REVISION = 'numpy-summary-v1'
MAX_WINDOW_SECONDS = 120
MAX_PCM_FRAMES = 22050 * MAX_WINDOW_SECONDS
MAX_PCM_AMPLITUDE = 16.0
_FFT_SIZE = 4096
_HOP = 1024
_KEY_METHOD = 'triad-weighted-diatonic-correlation-v1'


class MusicAnalysisFeatureError(Exception):
    def __init__(self, code: Literal['invalid_audio', 'analysis_budget_exceeded']) -> None:
        self.code = code
        super().__init__(code)


def _candidates(chroma: NDArray[np.float64]) -> list[KeyCandidate]:
    centered = chroma - np.mean(chroma)
    norm = float(np.linalg.norm(centered))
    if norm <= 1e-20:
        return []
    result: list[KeyCandidate] = []
    # Own uncalibrated profile: tonic=3, triad third/fifth=2, remaining scale degrees=1.
    scales: tuple[tuple[Literal['major', 'minor'], tuple[int, ...]], ...] = (
        ('major', (0, 2, 4, 5, 7, 9, 11)), ('minor', (0, 2, 3, 5, 7, 8, 10)),
    )
    for mode, scale in scales:
        profile = np.zeros(12, dtype=np.float64)
        profile[list(scale)] = 1
        profile[0], profile[scale[2]], profile[7] = 3, 2, 2
        profile -= np.mean(profile)
        denominator = norm * float(np.linalg.norm(profile))
        for tonic in range(12):
            score = float(centered @ np.roll(profile, tonic)) / denominator
            result.append(KeyCandidate(tonic_pc=tonic, mode=mode, score=min(1.0, max(-1.0, score))))
    return sorted(result, key=lambda candidate: (-candidate.score, candidate.tonic_pc, candidate.mode))


def _key(chroma: NDArray[np.float64], tonal_ratio: float, duration: float, active: bool,
         disagreement: bool) -> KeyEstimate:
    candidates = _candidates(chroma)
    reason: Literal['insufficient_evidence', 'short_audio', 'ambiguous', 'channel_disagreement'] = 'insufficient_evidence'
    if disagreement:
        reason = 'channel_disagreement'
    elif duration < 2:
        reason = 'short_audio'
    elif active and tonal_ratio >= .65 and np.count_nonzero(chroma >= .12 * np.max(chroma)) >= 3 and len(candidates) >= 2:
        best = candidates[0]
        if best.score >= .75 and best.score - candidates[1].score >= .12:
            return KeyEstimate(status='known', tonic_pc=best.tonic_pc, mode=best.mode, method=_KEY_METHOD,
                candidates=candidates, confidence=None, unknown_reason=None)
        reason = 'ambiguous'
    return KeyEstimate(method=_KEY_METHOD, candidates=candidates, unknown_reason=reason)


def _sections(points: list[AnalysisEnergyPoint], duration: float, sample_rate: int) -> list[AnalysisSection]:
    cells: list[tuple[float, float]] = []
    for second in range(math.ceil(duration)):
        block = [point for point in points if second <= point.time_sec < second + 1]
        if block:
            cells.append((sum(point.rms_dbfs for point in block) / len(block), sum(point.centroid_hz for point in block) / len(block)))
    boundaries = [0.0]
    for index in range(1, len(cells)):
        energy_change = abs(cells[index][0] - cells[index - 1][0])
        timbre_change = abs(cells[index][1] - cells[index - 1][1])
        if index - boundaries[-1] >= 2 and duration - index >= 1 and (energy_change >= 9 or timbre_change >= 1000):
            boundaries.append(float(index))
    boundaries.append(duration)
    groups: list[tuple[float, float]] = []
    sections: list[AnalysisSection] = []
    for start, end in zip(boundaries, boundaries[1:]):
        block = [point for point in points if start <= point.time_sec < end]
        energy = sum(point.rms_dbfs for point in block) / len(block)
        centroid = sum(point.centroid_hz for point in block) / len(block)
        group = next((index for index, (level, timbre) in enumerate(groups)
            if abs(energy - level) <= 3 and abs(centroid - timbre) <= max(200, .2 * max(centroid, timbre))), len(groups))
        if group == len(groups):
            groups.append((energy, centroid))
        label = chr(65 + group % 26) + (str(group // 26 + 1) if group >= 26 else '')
        identifier = uuid.uuid5(uuid.NAMESPACE_URL,
            f'{FEATURES_REVISION}:section:{round(start * sample_rate)}:{round(end * sample_rate)}:{label}').hex
        sections.append(AnalysisSection(id=identifier, start_sec=start, end_sec=end, group=label, label=label))
    return sections


def analyze_features(samples: NDArray[np.float32], sample_rate_hz: int) -> MusicAnalysisFeatures:
    """Summarize one <=120 s decoded window; all timestamps are relative to this window.

    PCM is finite float32 with shape (frames, channels), at 8–22.05 kHz and magnitude
    <=16, preserving float-audio overshoot. Power is combined after channel transforms.
    Overlap-band power is the 200–4,000 Hz fraction.
    The caller owns decoding, window stitching, source identity and AudioMetrics.
    """
    if (isinstance(sample_rate_hz, bool) or not isinstance(sample_rate_hz, int) or not 8000 <= sample_rate_hz <= 22050
            or not isinstance(samples, np.ndarray) or samples.dtype != np.float32 or samples.ndim != 2
            or samples.shape[1] not in (1, 2) or samples.shape[0] == 0):
        raise MusicAnalysisFeatureError('invalid_audio')
    frames, channels = samples.shape
    if frames > MAX_PCM_FRAMES or frames > sample_rate_hz * MAX_WINDOW_SECONDS:
        raise MusicAnalysisFeatureError('analysis_budget_exceeded')
    maximum = 0.0
    for start in range(0, frames, 65536):
        block = samples[start:start + 65536]
        if not np.all(np.isfinite(block)):
            raise MusicAnalysisFeatureError('invalid_audio')
        maximum = max(maximum, float(np.max(np.abs(block))))
    if maximum > MAX_PCM_AMPLITUDE:
        raise MusicAnalysisFeatureError('invalid_audio')
    duration = frames / sample_rate_hz
    taper = np.hanning(_FFT_SIZE)[:, None]
    frequencies = np.fft.rfftfreq(_FFT_SIZE, 1 / sample_rate_hz)
    band = np.flatnonzero((frequencies >= 55) & (frequencies <= 3520))
    midi = 69 + 12 * np.log2(frequencies[band] / 440)
    nearest = np.rint(midi)
    tuned = np.abs(midi - nearest) <= .25
    pitch_classes = nearest[tuned].astype(np.int64) % 12
    tuned_bins = band[tuned]
    overlap = (frequencies >= 200) & (frequencies <= 4000)
    chroma = np.zeros(12, dtype=np.float64)
    local_chroma = np.zeros((math.ceil(duration), 12), dtype=np.float64)
    local_band_power = np.zeros(math.ceil(duration), dtype=np.float64)
    channel_chroma = np.zeros((channels, 12), dtype=np.float64)
    band_power = 0.0
    points: list[AnalysisEnergyPoint] = []
    for start in range(0, frames, _HOP):
        source = np.asarray(samples[start:start + _FFT_SIZE], dtype=np.float64)
        rms = math.sqrt(float(np.mean(source * source)))
        frame = np.zeros((_FFT_SIZE, channels), dtype=np.float64)
        frame[:len(source)] = source
        power = np.abs(np.fft.rfft(frame * taper, axis=0)) ** 2
        combined = np.mean(power, axis=1)
        total = float(np.sum(combined))
        centroid = float(frequencies @ combined) / total if total > 0 else 0.0
        fraction = float(np.sum(combined[overlap])) / total if total > 0 else 0.0
        points.append(AnalysisEnergyPoint(time_sec=start / sample_rate_hz, rms_dbfs=max(-200.0, 20 * math.log10(max(rms, 1e-10))),
            centroid_hz=centroid, overlap_band_power=min(1.0, max(0.0, fraction))))
        current_chroma = np.bincount(pitch_classes, weights=combined[tuned_bins], minlength=12)
        chroma += current_chroma
        second = start // sample_rate_hz
        local_chroma[second] += current_chroma
        local_band_power[second] += float(np.sum(combined[band]))
        for channel in range(channels):
            channel_chroma[channel] += np.bincount(pitch_classes, weights=power[tuned_bins, channel], minlength=12)
        band_power += float(np.sum(combined[band]))
    disagreement = False
    if channels == 2:
        denominator = float(np.linalg.norm(channel_chroma[0]) * np.linalg.norm(channel_chroma[1]))
        disagreement = denominator > 1e-20 and float(channel_chroma[0] @ channel_chroma[1]) / denominator < .6
    warnings: list[MusicAnalysisWarning] = ['beat_grid_uncertain', 'meter_unknown', 'vocal_activity_unavailable', 'generic_sections']
    if duration < 2:
        warnings.append('short_audio')
    if disagreement:
        warnings.append('channel_disagreement')
    if maximum == 0:
        warnings.append('silence')
        key = KeyEstimate(method=_KEY_METHOD, unknown_reason='silence')
    else:
        key = _key(chroma, float(np.sum(chroma)) / band_power if band_power > 0 else 0.0, duration,
            max(point.rms_dbfs for point in points) > -70, disagreement)
    local_keys: list[KeyChange] = []
    for start in range(0, math.ceil(duration), 4):
        end = min(duration, start + 4)
        profile = np.sum(local_chroma[start:start + 4], axis=0)
        power_sum = float(np.sum(local_band_power[start:start + 4]))
        local_key = _key(profile, float(np.sum(profile)) / power_sum if power_sum > 0 else 0.0, end - start,
            any(point.rms_dbfs > -70 for point in points if start <= point.time_sec < end), disagreement)
        if local_key.status == 'known':
            local_keys.append(KeyChange(start_sec=float(start), end_sec=end, key=local_key))
    if len({(local.key.tonic_pc, local.key.mode) for local in local_keys}) > 1:
        warnings.append('key_changes')
        key = KeyEstimate(method=_KEY_METHOD, candidates=key.candidates, unknown_reason='unstable')
    else:
        local_keys = []
    if key.status == 'unknown':
        warnings.append('key_unknown')
    return MusicAnalysisFeatures(duration_sec=duration, analysis_sample_rate_hz=sample_rate_hz, hop_sec=_HOP / sample_rate_hz,
        key=key, key_changes=local_keys, sections=_sections(points, duration, sample_rate_hz), energy=points, warnings=warnings)
