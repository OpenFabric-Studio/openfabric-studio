"""Bounded observed pitch, using existing CPU coverage measurements only.

Imports and the common sampler do not load DSP packages. The owning replacement
attempt runs analyze_pitch in its supervised child, and owns cancellation.
"""
from __future__ import annotations

from bisect import bisect_right
import hashlib
from importlib import metadata
import json
import math
from pathlib import Path
import platform
import os
import sys
from typing import Literal
import uuid

from .audio_quality import RunCommand
from .music_dsp_contracts import WarpAnchor
from .replacement_pitch_contracts import (DiagnosticArtifactIdentity, DiagnosticWindow,
    PitchArtifactReport, PitchComparison, PitchRecommendation, PitchRuntimeVersions,
    PitchErrorCode, PitchUnknown, PitchWarning, PitchWindowObservation, PitchWorkerArtifact, PitchWorkerInput,
    ReplacementPitchReport, ReplacementPitchWorkerOutcome)
from .voice_contracts import VoiceCoverageMeasurement

MAX_WINDOWS = 12
MAX_WINDOW_SEC = 5
MAX_ARTIFACT_BYTES = 12 * 1024**3
MAX_MANIFEST_BYTES = 128 * 1024
MAX_OUTCOME_BYTES = 128 * 1024


class PitchDiagnosticError(ValueError):
    def __init__(self, code: PitchErrorCode) -> None:
        self.code = code
        super().__init__(code)


def sample_windows(duration_frames: int, sample_rate_hz: int) -> list[DiagnosticWindow]:
    """Cover <=60 seconds fully; otherwise distribute twelve disjoint 5s spans.

    All intervals are native-frame half-open bounds. No PCM allocation occurs.
    """
    if (type(duration_frames) is not int or type(sample_rate_hz) is not int
            or not 8000 <= sample_rate_hz <= 192000 or not 0 < duration_frames <= sample_rate_hz * 7200):
        raise PitchDiagnosticError('pitch_input_invalid')
    width = MAX_WINDOW_SEC * sample_rate_hz
    if duration_frames <= MAX_WINDOWS * width:
        return [DiagnosticWindow(start_frame=start, end_frame=min(start + width, duration_frames))
            for start in range(0, duration_frames, width)]
    return [DiagnosticWindow(start_frame=(duration_frames - width) * index // (MAX_WINDOWS - 1),
        end_frame=(duration_frames - width) * index // (MAX_WINDOWS - 1) + width)
        for index in range(MAX_WINDOWS)]


def _map_time(value: float, anchors: list[WarpAnchor], *, inverse: bool) -> float:
    xs = [item.target_sec if inverse else item.source_sec for item in anchors]
    ys = [item.source_sec if inverse else item.target_sec for item in anchors]
    if value < xs[0] - 1e-8 or value > xs[-1] + 1e-8:
        raise PitchDiagnosticError('pitch_input_invalid')
    index = min(len(xs) - 2, max(0, bisect_right(xs, value) - 1))
    return ys[index] + (value - xs[index]) * (ys[index + 1] - ys[index]) / (xs[index + 1] - xs[index])


def _interval(start: float, end: float, identity: DiagnosticArtifactIdentity) -> DiagnosticWindow | None:
    first = max(0, math.ceil(start * identity.sample_rate_hz - 1e-7))
    last = min(identity.duration_frames, math.floor(end * identity.sample_rate_hz + 1e-7))
    if last <= first:
        return None
    return DiagnosticWindow(start_frame=first, end_frame=last)


def _schedule(request: PitchWorkerInput) -> dict[str, list[DiagnosticWindow]]:
    reference = next(item for item in request.artifacts if item.role == 'reference')
    candidate = next((item for item in request.artifacts if item.role == 'candidate'), None)
    windows = sample_windows(reference.identity.duration_frames, reference.identity.sample_rate_hz)
    result: dict[str, list[DiagnosticWindow]] = {}
    if candidate is not None and candidate.timeline == 'prepared' and request.alignment_reviewed:
        anchors = request.alignment_anchors
        start = math.ceil(anchors[0].target_sec * reference.identity.sample_rate_hz)
        end = math.floor(anchors[-1].target_sec * reference.identity.sample_rate_hz)
        if end <= start:
            raise PitchDiagnosticError('pitch_input_invalid')
        windows, prepared = [], []
        for relative in sample_windows(end - start, reference.identity.sample_rate_hz):
            left, right = ((relative.start_frame + start) / reference.identity.sample_rate_hz,
                (relative.end_frame + start) / reference.identity.sample_rate_hz)
            a, b = _map_time(left, anchors, inverse=True), _map_time(right, anchors, inverse=True)
            if b - a > MAX_WINDOW_SEC:
                middle = (a + b) / 2
                a, b = middle - MAX_WINDOW_SEC / 2, middle + MAX_WINDOW_SEC / 2
                left, right = _map_time(a, anchors, inverse=False), _map_time(b, anchors, inverse=False)
            target, source = _interval(left, right, reference.identity), _interval(a, b, candidate.identity)
            if target is not None and source is not None:
                windows.append(target); prepared.append(source)
        result[candidate.identity.artifact_id] = prepared
    result[reference.identity.artifact_id] = windows
    for item in request.artifacts:
        identifier = item.identity.artifact_id
        if identifier in result:
            continue
        if item.timeline == 'prepared':
            result[identifier] = sample_windows(item.identity.duration_frames, item.identity.sample_rate_hz)
        else:
            mapped = [_interval(window.start_frame / reference.identity.sample_rate_hz,
                window.end_frame / reference.identity.sample_rate_hz, item.identity) for window in windows]
            result[identifier] = [window for window in mapped if window is not None]
    return result


def _hash(path: Path, maximum: int) -> tuple[str, tuple[int, int, int, int]]:
    try:
        before = path.stat()
        if path.is_symlink() or path.resolve() != path or not path.is_file() or not 0 < before.st_size <= maximum:
            raise PitchDiagnosticError('pitch_artifact_changed')
        checksum, total = hashlib.sha256(), 0
        with path.open('rb') as source:
            while block := source.read(1024**2):
                total += len(block)
                if total > maximum:
                    raise PitchDiagnosticError('pitch_budget_exceeded')
                checksum.update(block)
        after = path.stat()
        signature = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if signature != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or total != before.st_size:
            raise PitchDiagnosticError('pitch_artifact_changed')
        return checksum.hexdigest(), signature
    except OSError as error:
        raise PitchDiagnosticError('pitch_artifact_changed') from error


def _file(root: Path, item: PitchWorkerArtifact) -> Path:
    path = root / (item.identity.artifact_id + '.wav')
    if root.is_symlink() or root.resolve() != root or not root.is_dir() or path.is_symlink() or path.resolve() != path:
        raise PitchDiagnosticError('pitch_artifact_changed')
    return path


def _measure(path: Path, item: PitchWorkerArtifact, windows: list[DiagnosticWindow]) -> PitchArtifactReport:
    import numpy as np
    import soundfile as sf
    from .voice_coverage import CoverageError, analyze_coverage, summarize_coverage
    observations: list[PitchWindowObservation] = []
    measurements: list[VoiceCoverageMeasurement] = []
    failure: PitchUnknown | None = None
    try:
        with sf.SoundFile(path) as audio:
            for window in windows:
                count = window.end_frame - window.start_frame
                audio.seek(window.start_frame)
                samples = audio.read(count, dtype='float32', always_2d=True)
                if (samples.shape != (count, item.identity.channels) or not np.all(np.isfinite(samples))
                        or float(np.max(np.abs(samples))) > 16):
                    raise PitchDiagnosticError('pitch_input_invalid')
                try:
                    measurement = analyze_coverage(samples, item.identity.sample_rate_hz)
                except CoverageError as error:
                    failure = 'analysis_unavailable' if error.code == 'engine_incompatible' else 'analysis_failed'
                    measurement = None
                except ImportError:
                    failure, measurement = 'analysis_unavailable', None
                except (RuntimeError, ValueError):
                    failure, measurement = 'analysis_failed', None
                if measurement is None:
                    observations.append(PitchWindowObservation(window=window, analyzed_sec=0.0,
                        voiced_sec=0.0, reliable_voiced_sec=0.0))
                    break
                if (measurement.source_sample_rate != item.identity.sample_rate_hz
                        or not math.isclose(measurement.duration_sec, count / item.identity.sample_rate_hz,
                            rel_tol=0, abs_tol=1 / item.identity.sample_rate_hz)):
                    raise PitchDiagnosticError('pitch_input_invalid')
                measurements.append(measurement)
                observations.append(PitchWindowObservation(window=window, analyzed_sec=measurement.analyzed_sec,
                    voiced_sec=measurement.voiced_sec, reliable_voiced_sec=measurement.reliable_voiced_sec,
                    pitch_p05_hz=measurement.pitch_p05_hz, pitch_median_hz=measurement.pitch_median_hz,
                    pitch_p95_hz=measurement.pitch_p95_hz,
                    median_voicing_probability=measurement.median_voicing_probability))
    except (OSError, RuntimeError) as error:
        raise PitchDiagnosticError('pitch_input_invalid') from error
    summary = summarize_coverage(measurements)
    if failure is None:
        failure = ('no_reliable_pitch' if summary.reliable_voiced_sec == 0 else
            'insufficient_voiced_support' if summary.reliable_voiced_sec < 1 or summary.reliable_voiced_sec < .25 * summary.analyzed_sec else
            'pitch_near_analysis_boundary' if 'pitch_near_analysis_boundary' in summary.warnings else None)
    return PitchArtifactReport(identity=item.identity, role=item.role, applied_pitch_semitones=item.applied_pitch_semitones,
        status='unknown' if failure else 'observed', unknown_reason=failure, windows=observations,
        sampled_sec=sum((part.window.end_frame - part.window.start_frame) / item.identity.sample_rate_hz for part in observations),
        analyzed_sec=summary.analyzed_sec, voiced_sec=summary.voiced_sec, reliable_voiced_sec=summary.reliable_voiced_sec,
        pitch_p05_hz=summary.pitch_p05_hz if failure is None else None,
        pitch_median_hz=summary.pitch_median_hz if failure is None else None,
        pitch_p95_hz=summary.pitch_p95_hz if failure is None else None)


def _midi(frequency: float) -> float:
    return 69 + 12 * math.log2(frequency / 440)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return (ordered[middle] + ordered[(len(ordered) - 1) // 2]) / 2


def _compare(reference: PitchArtifactReport, candidate: PitchArtifactReport, *,
             kind: Literal['prepared_fit', 'conversion_drift'], reviewed: bool,
             current: float) -> PitchComparison:
    paired = min(len(reference.windows), len(candidate.windows))
    base = {'kind': kind, 'reference_artifact_id': reference.identity.artifact_id,
        'candidate_artifact_id': candidate.identity.artifact_id, 'paired_windows': paired}
    if not reviewed:
        return PitchComparison.model_validate({**base, 'status': 'unknown', 'reason': 'alignment_unreviewed', 'supported_windows': 0})
    if reference.status == 'unknown' or candidate.status == 'unknown':
        return PitchComparison.model_validate({**base, 'status': 'unknown', 'reason': reference.unknown_reason or candidate.unknown_reason,
            'supported_windows': 0})
    differences: list[float] = []
    quantiles: list[float] = []
    support = 0.0
    for a, b in zip(reference.windows, candidate.windows):
        if (a.pitch_median_hz is None or b.pitch_median_hz is None or a.pitch_p05_hz is None or b.pitch_p05_hz is None
                or a.pitch_p95_hz is None or b.pitch_p95_hz is None or min(a.reliable_voiced_sec, b.reliable_voiced_sec) < 1
                or a.reliable_voiced_sec < .5 * a.analyzed_sec or b.reliable_voiced_sec < .5 * b.analyzed_sec):
            continue
        differences.append(_midi(a.pitch_median_hz) - _midi(b.pitch_median_hz))
        quantiles.extend((_midi(a.pitch_p05_hz) - _midi(b.pitch_p05_hz), _midi(a.pitch_p95_hz) - _midi(b.pitch_p95_hz)))
        support += min(a.reliable_voiced_sec, b.reliable_voiced_sec)
    base['supported_windows'] = len(differences)
    if len(differences) < 3 or support < 5:
        return PitchComparison.model_validate({**base, 'status': 'unknown', 'reason': 'insufficient_paired_windows'})
    difference = _median(differences)
    spread = max(differences) - min(differences)
    values = {**base, 'median_delta_semitones': difference, 'spread_semitones': spread}
    if any(abs(value - difference) > .35 for value in differences) or any(abs(value - difference) > .5 for value in quantiles):
        return PitchComparison.model_validate({**values, 'status': 'unknown', 'reason': 'inconsistent_pitch_difference'})
    if kind == 'conversion_drift':
        return PitchComparison.model_validate({**values, 'status': 'no_change' if abs(difference) <= .1 else 'observed',
            'reason': 'already_matches' if abs(difference) <= .1 else None})
    total = candidate.applied_pitch_semitones + difference
    if abs(total) > 2.000001:
        return PitchComparison.model_validate({**values, 'status': 'unknown', 'reason': 'shift_outside_advisory_bound'})
    # Existing pYIN resolution is 0.2 semitones; do not invent finer precision.
    proposed = round(total / .2) * .2
    if abs(proposed - current) <= .100000001:
        return PitchComparison.model_validate({**values, 'status': 'no_change', 'reason': 'already_matches'})
    coarse = round(proposed)
    recommendation = PitchRecommendation(pitch_shift_semitones=coarse, pitch_nudge_semitones=round(proposed - coarse, 6))
    return PitchComparison.model_validate({**values, 'status': 'observed', 'recommendation': recommendation})


def _version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _configuration_sha256(request: PitchWorkerInput, runtime: PitchRuntimeVersions) -> str:
    configuration = request.model_dump(exclude={'artifact_root'})
    implementation = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ('replacement_pitch.py', 'replacement_pitch_contracts.py', 'voice_coverage.py')}
    worker = Path(__file__).resolve().parents[1] / 'scripts/replacement_pitch_worker.py'
    implementation['replacement_pitch_worker.py'] = hashlib.sha256(worker.read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps({'request': configuration, 'runtime': runtime.model_dump(),
        'implementation': implementation}, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def analyze_pitch(request: PitchWorkerInput) -> ReplacementPitchReport:
    """Child-only CPU function; caller supplies retained, authoritative identities.

    No setting is applied, no registry is changed and no original is written.
    Missing estimator support returns advisory unknown; stale media raises.
    """
    import soundfile as sf
    request = PitchWorkerInput.model_validate(request.model_dump())
    root = Path(request.artifact_root)
    schedule = _schedule(request)
    signatures: dict[str, tuple[int, int, int, int]] = {}
    for item in request.artifacts:
        path = _file(root, item)
        maximum = min(MAX_ARTIFACT_BYTES, item.identity.duration_frames * item.identity.channels * 8 + 1024**2)
        checksum, signature = _hash(path, maximum)
        try:
            info = sf.info(path)
        except (OSError, RuntimeError) as error:
            raise PitchDiagnosticError('pitch_input_invalid') from error
        if (checksum != item.identity.sha256 or (info.frames, info.samplerate, info.channels) != (
                item.identity.duration_frames, item.identity.sample_rate_hz, item.identity.channels)):
            raise PitchDiagnosticError('pitch_artifact_changed')
        signatures[item.identity.artifact_id] = signature
    artifacts = [_measure(_file(root, item), item, schedule[item.identity.artifact_id]) for item in request.artifacts]
    for item in request.artifacts:
        maximum = min(MAX_ARTIFACT_BYTES, item.identity.duration_frames * item.identity.channels * 8 + 1024**2)
        checksum, signature = _hash(_file(root, item), maximum)
        if checksum != item.identity.sha256 or signature != signatures[item.identity.artifact_id]:
            raise PitchDiagnosticError('pitch_artifact_changed')
    reference = next(item for item in artifacts if item.role == 'reference')
    candidate = next((item for item in artifacts if item.role == 'candidate'), None)
    converted = next((item for item in artifacts if item.role == 'converted'), None)
    comparisons: list[PitchComparison] = []
    if candidate is not None and request.prepared_recommendation:
        comparisons.append(_compare(reference, candidate, kind='prepared_fit', reviewed=request.alignment_reviewed,
            current=request.current_pitch_semitones))
    if converted is not None:
        source = candidate or reference
        descriptor = next(item for item in request.artifacts if item.role == source.role)
        comparisons.append(_compare(source, converted, kind='conversion_drift', reviewed=descriptor.timeline == 'reference', current=0))
    runtime = PitchRuntimeVersions(python=platform.python_version(), librosa=_version('librosa'), numpy=_version('numpy'), scipy=_version('scipy'))
    checksum = _configuration_sha256(request, runtime)
    warnings: list[PitchWarning] = ['sampled_passages_only', 'observed_pitch_not_full_range',
        'voicing_probability_not_pitch_confidence', 'monophonic_estimator', 'melody_correspondence_unverified',
        'user_review_required', 'key_not_inferred']
    if any(item.unknown_reason == 'analysis_unavailable' for item in artifacts):
        warnings.append('analysis_unavailable')
    return ReplacementPitchReport(configuration_sha256=checksum, runtime=runtime, artifacts=artifacts,
        comparisons=comparisons, warnings=warnings)


async def analyze_owned_pitch(request: PitchWorkerInput, work: Path, run: RunCommand, *,
                              python: Path | None = None) -> ReplacementPitchReport:
    """Delegate to the caller's receipt-owning spawn/drain callback.

    Workspaces survive errors/cancellation for the existing attempt owner to
    inspect. Callback cleanup failures propagate; this helper cannot declare an
    unverified worker idle. Runtime selection never installs or imports models.
    """
    if work.is_symlink() or work.resolve() != work or not work.is_dir():
        raise PitchDiagnosticError('pitch_input_invalid')
    request = PitchWorkerInput.model_validate(request.model_dump())
    body = request.model_dump_json().encode()
    if len(body) > MAX_MANIFEST_BYTES:
        raise PitchDiagnosticError('pitch_budget_exceeded')
    folder = work / ('pitch-' + uuid.uuid4().hex)
    folder.mkdir(exist_ok=False)
    source, result = folder / 'input.json', folder / 'output.json'
    with source.open('xb') as stream:
        stream.write(body); stream.flush(); os.fsync(stream.fileno())
    script = Path(__file__).resolve().parents[1] / 'scripts/replacement_pitch_worker.py'
    await run([str(python or Path(sys.executable)), '-I', str(script), '--request', str(source), '--result', str(result)], 600.0)
    try:
        if result.is_symlink() or result.resolve() != result or not result.is_file() or not 0 < result.stat().st_size <= MAX_OUTCOME_BYTES:
            raise PitchDiagnosticError('pitch_worker_output_invalid')
        with result.open('rb') as stream:
            raw = stream.read(MAX_OUTCOME_BYTES + 1)
        if len(raw) > MAX_OUTCOME_BYTES:
            raise PitchDiagnosticError('pitch_worker_output_invalid')
        outcome = ReplacementPitchWorkerOutcome.model_validate_json(raw)
        if outcome.input_sha256 != hashlib.sha256(body).hexdigest():
            raise PitchDiagnosticError('pitch_worker_output_invalid')
        if outcome.error_code is not None:
            raise PitchDiagnosticError(outcome.error_code)
        report = outcome.result
        if report is None or len(report.artifacts) != len(request.artifacts):
            raise PitchDiagnosticError('pitch_worker_output_invalid')
        if report.configuration_sha256 != _configuration_sha256(request, report.runtime):
            raise PitchDiagnosticError('pitch_worker_output_invalid')
        for observed, expected in zip(report.artifacts, request.artifacts):
            if (observed.identity != expected.identity or observed.role != expected.role
                    or observed.applied_pitch_semitones != expected.applied_pitch_semitones):
                raise PitchDiagnosticError('pitch_worker_output_invalid')
        return report
    except PitchDiagnosticError:
        raise
    except (OSError, ValueError) as error:
        raise PitchDiagnosticError('pitch_worker_output_invalid') from error
