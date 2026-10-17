"""Small, uncalibrated shared-audio screening; no identity or quality inference."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import hashlib
import json
import time
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from pydantic import Field
from .regional_acoustic_contracts import (AcousticContract, RegionalAcousticExcerpt, RegionalAcousticCandidate,
    RegionalAcousticReport, AcousticErrorCode)
from .music_analysis_contracts import MusicAnalysisId

METHOD_REVISION = 'regional-landmarks-v1'
RATE = 8000
WINDOW_SECONDS = 64
MAX_WINDOW_FRAMES = RATE * 120
ANCHOR_HOP = 2048
FFT_SIZE = 1024
MAX_HASHES = 2000000
MAX_PAIR_VOTES = 100000
MAX_POSTINGS = 256
MAX_COMPARISON_SECONDS = 900
AUDITION_BYTES = 32 * 1024**2
_BANDS = np.geomspace(80, 3500, 17)


class AcousticBudgetError(ValueError):
    """The result is unavailable, never a clean negative, when a bound fails."""


def configuration_sha256() -> str:
    # Code changes invalidate both reports and reviewed candidate identifiers.
    configuration = {'revision': METHOD_REVISION, 'source': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'worker': hashlib.sha256((Path(__file__).resolve().parents[1] / 'scripts/regional_acoustic_worker.py').read_bytes()).hexdigest(),
        'numpy_version': np.__version__,
        'rate': RATE, 'fft': FFT_SIZE, 'hop': ANCHOR_HOP, 'bands': _BANDS.tolist(),
        'max_hashes': MAX_HASHES, 'max_votes': MAX_PAIR_VOTES, 'max_postings': MAX_POSTINGS}
    return hashlib.sha256(json.dumps(configuration, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class AcousticWorkerSource(AcousticContract):
    # Private, server-resolved paths never appear in client model registries.
    source_path: str = Field(min_length=1, max_length=4096)
    source_frames: int = Field(ge=1, le=1382400000)
    excerpt: RegionalAcousticExcerpt


class AcousticWorkerRequest(AcousticContract):
    selection_sha256: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    configuration_sha256: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    sources: list[AcousticWorkerSource] = Field(min_length=1, max_length=100)


class AcousticProcessIdentity(AcousticContract):
    pid: int = Field(ge=1)
    token: MusicAnalysisId


class AcousticWorkerOutcome(AcousticContract):
    report: RegionalAcousticReport | None = None
    error_code: AcousticErrorCode | None = None


@dataclass(frozen=True)
class AcousticFingerprint:
    codes: NDArray[np.uint32]
    times: NDArray[np.int32]
    channels: NDArray[np.int8]
    bands: NDArray[np.float32]
    informative: bool


@dataclass(frozen=True)
class AcousticMatch:
    left_channel: int
    right_channel: int
    offset_frames: int
    left_start_frame: int
    right_start_frame: int
    aligned_seconds: float
    matched_landmarks: int
    distinct_anchors: int
    spectral_agreement: float


def fingerprint_window(samples: NDArray[np.float32]) -> AcousticFingerprint:
    """Finite <=120 s, 8 kHz float32 PCM; retain separate channel evidence.

    Four fixed-lag peak pairs per 256 ms anchor bound the index to <20 hashes/s
    per channel. This deliberately modest heuristic is not pitch/time invariant.
    """
    if (not isinstance(samples, np.ndarray) or samples.dtype != np.float32 or samples.ndim != 2
            or samples.shape[1] not in (1, 2) or not 0 < len(samples) <= MAX_WINDOW_FRAMES):
        raise AcousticBudgetError('regional_dataset_limit')
    for start in range(0, len(samples), 65536):
        block = samples[start:start + 65536]
        if not np.all(np.isfinite(block)) or np.max(np.abs(block)) > 16:
            raise AcousticBudgetError('regional_dataset_invalid')
    count = max(0, (len(samples) - FFT_SIZE) // ANCHOR_HOP + 1)
    channels = samples.shape[1]
    spectra = np.zeros((count, channels, 16), dtype=np.float32)
    peaks = np.zeros((count, channels), dtype=np.uint32)
    frequencies = np.fft.rfftfreq(FFT_SIZE, 1 / RATE)
    taper = np.hanning(FFT_SIZE)[:, None]
    bins = np.flatnonzero((frequencies >= 80) & (frequencies <= 3500))
    band_masks = [(frequencies >= low) & (frequencies < high) for low, high in zip(_BANDS, _BANDS[1:])]
    for index in range(count):
        frame = np.asarray(samples[index * ANCHOR_HOP:index * ANCHOR_HOP + FFT_SIZE], dtype=np.float64)
        power = np.abs(np.fft.rfft(frame * taper, axis=0)) ** 2
        peaks[index] = np.rint(bins[np.argmax(power[bins], axis=0)] / 2).astype(np.uint32)
        energies = np.stack([np.sum(power[mask], axis=0) for mask in band_masks], axis=1)
        total = np.sum(energies, axis=1, keepdims=True)
        logarithm = np.log10(energies / np.maximum(total, 1e-30) + 1e-8)
        logarithm -= np.mean(logarithm, axis=1, keepdims=True)
        logarithm /= np.maximum(np.linalg.norm(logarithm, axis=1, keepdims=True), 1e-20)
        spectra[index] = logarithm
    codes: list[NDArray[np.uint32]] = []
    times: list[NDArray[np.int32]] = []
    channel_ids: list[NDArray[np.int8]] = []
    for channel in range(channels):
        for lag in range(1, 5):
            size = max(0, count - lag)
            codes.append(np.asarray(peaks[:size, channel] | (peaks[lag:lag + size, channel] << 8) | np.uint32(lag << 16), dtype=np.uint32))
            times.append(np.arange(size, dtype=np.int32))
            channel_ids.append(np.full(size, channel, dtype=np.int8))
    informative = len(samples) >= 3 * RATE and any(len(np.unique(peaks[:, channel])) >= 4
        and float(np.mean(np.std(spectra[:, channel], axis=0))) >= .025 for channel in range(channels))
    return AcousticFingerprint(np.concatenate(codes), np.concatenate(times), np.concatenate(channel_ids), spectra, informative)


def combine_fingerprints(windows: Sequence[AcousticFingerprint]) -> AcousticFingerprint:
    """Join contiguous 64 s windows; omitted edge anchors remain explicitly limited."""
    if not windows:
        raise AcousticBudgetError('regional_dataset_invalid')
    if sum(len(item.codes) for item in windows) > MAX_HASHES:
        raise AcousticBudgetError('regional_dataset_limit')
    times = [item.times + index * (WINDOW_SECONDS * RATE // ANCHOR_HOP) for index, item in enumerate(windows)]
    return AcousticFingerprint(np.concatenate([item.codes for item in windows]), np.concatenate(times),
        np.concatenate([item.channels for item in windows]), np.concatenate([item.bands for item in windows]),
        any(item.informative for item in windows))


def compare_fingerprints(left: AcousticFingerprint, right: AcousticFingerprint, *,
                         clock: Callable[[], float] = time.monotonic, deadline: float | None = None) -> AcousticMatch | None:
    """Return one strongest candidate, never a probability or equality judgment."""
    if not left.informative or not right.informative:
        return None
    if len(left.codes) + len(right.codes) > MAX_HASHES:
        raise AcousticBudgetError('regional_dataset_limit')
    end = clock() + MAX_COMPARISON_SECONDS if deadline is None else deadline
    best: AcousticMatch | None = None
    votes = 0
    for lc in range(left.bands.shape[1]):
        for rc in range(right.bands.shape[1]):
            lmask, rmask = left.channels == lc, right.channels == rc
            lcodes, rcodes, ltimes, rtimes = left.codes[lmask], right.codes[rmask], left.times[lmask], right.times[rmask]
            lorder, rorder = np.argsort(lcodes), np.argsort(rcodes)
            lcodes, rcodes, ltimes, rtimes = lcodes[lorder], rcodes[rorder], ltimes[lorder], rtimes[rorder]
            offsets: dict[int, list[int]] = {}
            for code in np.intersect1d(lcodes, rcodes):
                if clock() > end:
                    raise AcousticBudgetError('regional_dataset_limit')
                la, lb = np.searchsorted(lcodes, code, side='left'), np.searchsorted(lcodes, code, side='right')
                ra, rb = np.searchsorted(rcodes, code, side='left'), np.searchsorted(rcodes, code, side='right')
                if max(lb - la, rb - ra) > MAX_POSTINGS:
                    raise AcousticBudgetError('regional_dataset_limit')
                votes += int((lb - la) * (rb - ra))
                if votes > MAX_PAIR_VOTES:
                    raise AcousticBudgetError('regional_dataset_limit')
                for lt in ltimes[la:lb]:
                    for rt in rtimes[ra:rb]:
                        offsets.setdefault(int(lt - rt), []).append(int(lt))
            for offset, anchors in sorted(offsets.items(), key=lambda item: (-len(item[1]), item[0]))[:8]:
                distinct = np.unique(anchors)
                if len(anchors) < 12 or len(distinct) < 6 or (distinct[-1] - distinct[0]) * ANCHOR_HOP / RATE < 3:
                    continue
                start = max(0, offset, int(distinct[0]))
                right_start = start - offset
                count = min(len(left.bands) - start, len(right.bands) - right_start, int(distinct[-1]) - start + 1)
                if count * ANCHOR_HOP / RATE < 3:
                    continue
                # Median limits seek/grid boundary sensitivity; this is an
                # uncalibrated screen, not a calibrated matching probability.
                agreement = float(np.median(np.sum(left.bands[start:start + count, lc]
                    * right.bands[right_start:right_start + count, rc], axis=1)))
                if agreement < .9:
                    continue
                match = AcousticMatch(lc, rc, offset * ANCHOR_HOP, start * ANCHOR_HOP,
                    right_start * ANCHOR_HOP, count * ANCHOR_HOP / RATE, len(anchors), len(distinct), min(1., max(-1., agreement)))
                if best is None or (match.spectral_agreement, match.matched_landmarks) > (best.spectral_agreement, best.matched_landmarks):
                    best = match
    return best


def candidate_sha256(candidate: RegionalAcousticCandidate, selection: str, configuration: str) -> str:
    body = {'selection': selection, 'configuration': configuration, 'candidate': candidate.model_dump(mode='json',
        exclude={'id', 'left_audition', 'right_audition', 'audition_unavailable_reason'})}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_report(report: RegionalAcousticReport) -> None:
    if report.configuration_sha256 != configuration_sha256() or any(item.id != candidate_sha256(item,
            report.selection_sha256, report.configuration_sha256) for item in report.candidates):
        raise AcousticBudgetError('regional_dataset_source_changed')


def build_report(bindings: Sequence[RegionalAcousticExcerpt], fingerprints: Sequence[AcousticFingerprint],
                 selection: str, configuration: str, deadline: float) -> RegionalAcousticReport:
    if len(bindings) != len(fingerprints) or not 0 < len(bindings) <= 100 or sum(len(item.codes) for item in fingerprints) > MAX_HASHES:
        raise AcousticBudgetError('regional_dataset_limit')
    excerpts = [binding.model_copy(update={'coverage': 'screened' if fingerprint.informative else 'insufficient_information',
        'landmark_count': len(fingerprint.codes)}) for binding, fingerprint in zip(bindings, fingerprints)]
    candidates: list[RegionalAcousticCandidate] = []
    for index, left in enumerate(excerpts):
        for ri in range(index + 1, len(excerpts)):
            if time.monotonic() > deadline:
                raise AcousticBudgetError('regional_dataset_limit')
            right = excerpts[ri]
            if (left.recording_id == right.recording_id or left.source_sha256 == right.source_sha256
                    or left.decoded_pcm_sha256 == right.decoded_pcm_sha256):
                continue
            match = compare_fingerprints(fingerprints[index], fingerprints[ri], deadline=deadline)
            if match is None:
                continue
            ls = left.start_frame + round(match.left_start_frame * left.sample_rate_hz / RATE)
            rs = right.start_frame + round(match.right_start_frame * right.sample_rate_hz / RATE)
            length = min(match.aligned_seconds, (left.end_frame - ls) / left.sample_rate_hz,
                (right.end_frame - rs) / right.sample_rate_hz)
            candidate = RegionalAcousticCandidate(id='0' * 64, left_excerpt_id=left.excerpt_id,
                right_excerpt_id=right.excerpt_id, left_channel=match.left_channel, right_channel=match.right_channel,
                left_start_frame=ls, right_start_frame=rs,
                left_end_frame=ls + round(length * left.sample_rate_hz), right_end_frame=rs + round(length * right.sample_rate_hz),
                aligned_seconds=length, matched_landmarks=match.matched_landmarks, distinct_anchors=match.distinct_anchors,
                spectral_agreement=match.spectral_agreement)
            candidates.append(candidate.model_copy(update={'id': candidate_sha256(candidate, selection, configuration)}))
    report = RegionalAcousticReport(configuration_sha256=configuration, selection_sha256=selection,
        excerpts=excerpts, candidates=candidates)
    if any(item.coverage != 'screened' for item in excerpts):
        report = report.model_copy(update={'limitations': [*report.limitations, 'insufficient_information']})
    return report
