"""Private CPU worker. Its supervisor owns this process and all FFmpeg children."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import math
import os
from pathlib import Path
import sys
import tempfile
import uuid

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from numpy.typing import NDArray

from app.audio_quality import AudioQualityError, INPUT, RunCommand, measure
from app.music_analysis import (ANALYZER_REVISION, DISK_RESERVE, MAX_DOCUMENT_BYTES, MAX_SOURCE_BYTES,
    AnalysisWorkerOutcome, AnalysisWorkerReceipt, AnalysisWorkerRequest, AudioHeader, MusicAnalysisError,
    configuration_digest, decoded_byte_budget, parse_audio_header, probe_command, storage_budget)
from app.music_analysis_contracts import (AnalysisEnergyPoint, AnalysisEngine, AnalysisResult, AnalysisSection,
    KeyEstimate, MusicAnalysisFeatures, MusicAnalysisWarning)
from app.music_beats import LICENSE_EVIDENCE_IDS, BEATS_REVISION, TimedBeatWindow, stitch_windows
from app.music_beats_runtime import BeatRuntimeError, infer_window
from app.music_analysis_features import MusicAnalysisFeatureError, analyze_features
from app.video_io import hash_file
from app.video_media import tool

ANALYSIS_RATE = 22050
WINDOW_SECONDS = 120.0
OVERLAP_SECONDS = 10.0
MAX_COMMAND_OUTPUT = 1024 * 1024


async def run_command(argv: list[str], timeout: float) -> bytes:
    """FFmpeg inherits the outer supervisor's process group/Windows job.

    Do not start a second detached process group here: the durable owner must
    be able to drain every descendant before releasing the snapshot.
    """
    proc = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        if proc.stdout is None:
            raise MusicAnalysisError('analysis_failed')
        output = bytearray()
        async with asyncio.timeout(timeout):
            while chunk := await proc.stdout.read(65536):
                if len(output) + len(chunk) > MAX_COMMAND_OUTPUT:
                    raise MusicAnalysisError('worker_output_limit')
                output.extend(chunk)
            await proc.wait()
        if proc.returncode != 0:
            raise MusicAnalysisError('invalid_audio')
        return bytes(output)
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.communicate()


async def probe(source: Path, root: Path, run: RunCommand) -> AudioHeader:
    header = parse_audio_header(await run(probe_command(source), 60.0))
    import shutil
    if shutil.disk_usage(root).free < storage_budget(header) + DISK_RESERVE:
        raise MusicAnalysisError('insufficient_disk')
    return header


async def decode_window(source: Path, root: Path, start: float, seconds: float, channels: int, run: RunCommand) -> NDArray[np.float32]:
    if not 0 <= start < 7200 or not 0 < seconds <= WINDOW_SECONDS or channels not in {1, 2}:
        raise MusicAnalysisError('analysis_budget_exceeded')
    target = root / 'window.f32'
    if target.is_symlink():
        raise MusicAnalysisError('invalid_audio')
    packet = 1024 * channels * 4
    limit = (round(seconds * ANALYSIS_RATE) + math.ceil(.002 * ANALYSIS_RATE)) * channels * 4
    maximum = limit + packet
    try:
        await run([tool('ffmpeg'), '-v', 'error', '-nostdin', '-y', *INPUT, '-ss', f'{start:.9f}',
            '-i', str(source), '-map', '0:a:0', '-vn', '-t', f'{seconds:.9f}',
            '-ar', str(ANALYSIS_RATE), '-ac', str(channels), '-af', f'aresample={ANALYSIS_RATE},asetnsamples=n=1024:p=0',
            '-c:a', 'pcm_f32le', '-f', 'f32le', '-fs', str(limit), str(target)], 180.0)
        size = target.stat().st_size
        if target.is_symlink() or not 0 < size <= maximum or size % (4 * channels):
            raise MusicAnalysisError('invalid_audio')
        count = size // (4 * channels)
        # At most 2 ms resampler/seek rounding, never a truncated window.
        if abs(count - round(seconds * ANALYSIS_RATE)) > max(2, math.ceil(.002 * ANALYSIS_RATE)):
            raise MusicAnalysisError('invalid_audio')
        samples = np.fromfile(target, dtype='<f4')
        return samples.reshape((-1, channels))
    finally:
        # The callback has already drained the command before it returns/raises.
        target.unlink(missing_ok=True)


def _section(section: AnalysisSection, digest: str, offset: float, duration: float) -> AnalysisSection:
    body = section.model_dump()
    body['start_sec'] = section.start_sec + offset
    body['end_sec'] = min(duration, section.end_sec + offset)
    body['id'] = uuid.uuid5(uuid.NAMESPACE_URL,
        f'{ANALYZER_REVISION}:{digest}:{round((section.start_sec + offset) * ANALYSIS_RATE)}:{round((section.end_sec + offset) * ANALYSIS_RATE)}:{section.group}').hex
    return AnalysisSection.model_validate(body)


async def analyze_snapshot(request: AnalysisWorkerRequest, root: Path, run: RunCommand) -> AnalysisResult:
    source = root / request.snapshot_name
    if source.is_symlink() or source.resolve().parent != root.resolve() or not source.is_file():
        raise MusicAnalysisError('source_missing')
    if await hash_file(source, max_bytes=MAX_SOURCE_BYTES) != request.audio_sha256:
        raise MusicAnalysisError('source_changed')
    runtime = await run([tool('ffmpeg'), '-version'], 15.0)
    if (hashlib.sha256(runtime).hexdigest() != request.ffmpeg_sha256
            or configuration_digest(request.ffmpeg_version, request.ffmpeg_sha256,
                beats=request.beats.identity if request.beats else None) != request.configuration_sha256):
        raise MusicAnalysisError('analysis_runtime_unverified')
    header = await probe(source, root, run)
    try:
        measurement = await measure(source, root, run, name='loudness', max_decoded_bytes=decoded_byte_budget(header))
    except AudioQualityError as error:
        raise MusicAnalysisError('decode_budget_exceeded' if error.code == 'audio_decode_budget_exceeded' else 'invalid_audio') from error
    metrics = measurement.metrics
    if (metrics.source_sha256 != request.audio_sha256 or metrics.samples_analyzed % header.channels
            or metrics.samples_analyzed <= 0 or metrics.ffmpeg_version != request.ffmpeg_version):
        raise MusicAnalysisError('analysis_runtime_unverified')
    frames = metrics.samples_analyzed // header.channels
    duration = frames / header.rate
    if not 0 < duration <= 7200 or abs(duration - header.duration) > max(.1, 1 / header.rate):
        raise MusicAnalysisError('invalid_audio')
    energy: dict[int, AnalysisEnergyPoint] = {}
    warnings: set[MusicAnalysisWarning] = set()
    all_silent = True
    first: MusicAnalysisFeatures | None = None
    window_count = 0
    beat_windows: list[TimedBeatWindow] = []
    start = 0.0
    while start < duration:
        seconds = min(WINDOW_SECONDS, duration - start)
        samples = await decode_window(source, root, start, seconds, header.channels, run)
        features = analyze_features(samples, ANALYSIS_RATE)
        first = features if first is None else first
        window_count += 1
        warnings.update(features.warnings)
        all_silent = all_silent and 'silence' in features.warnings
        central_start = start if start == 0 else start + OVERLAP_SECONDS / 2
        central_end = duration if start + seconds >= duration else start + seconds - OVERLAP_SECONDS / 2
        if request.beats is not None:
            try:
                predicted = await infer_window(samples, root, request.beats, run)
            except BeatRuntimeError as error:
                raise MusicAnalysisError(error.code) from error
            beat_windows.append(TimedBeatWindow(start, central_start, central_end, predicted))
        del samples
        for point in features.energy:
            time = point.time_sec + start
            if central_start <= time < min(central_end, duration):
                slot = min(3999, math.floor(time / duration * 4000))
                if slot not in energy:
                    energy[slot] = AnalysisEnergyPoint(time_sec=time, rms_dbfs=point.rms_dbfs,
                        centroid_hz=point.centroid_hz, overlap_band_power=point.overlap_band_power)
        if start + seconds >= duration:
            break
        start += WINDOW_SECONDS - OVERLAP_SECONDS
    if first is None:
        raise MusicAnalysisError('invalid_audio')
    if not all_silent:
        warnings.discard('silence')
    if duration >= 2:
        warnings.discard('short_audio')
    body = first.model_dump()
    if window_count > 1:
        # Window-local groups/keys cannot establish whole-song recurrence or a
        # global modulation history. Retain measured energy and say so plainly.
        body.update(key=KeyEstimate(unknown_reason='silence' if all_silent else 'unstable').model_dump(), key_changes=[], sections=[])
        warnings.update({'analysis_partial', 'window_boundary_uncertain', 'key_unknown'})
    else:
        body['sections'] = [_section(section, request.audio_sha256, 0.0, duration).model_dump()
            for section in first.sections if section.start_sec < duration]
        body['key_changes'] = [{**change.model_dump(), 'end_sec': min(duration, change.end_sec)}
            for change in first.key_changes if change.start_sec < duration]
    body.update(duration_sec=duration, energy=[energy[index].model_dump() for index in sorted(energy)], warnings=sorted(warnings))
    if request.beats is not None and not all_silent:
        grid = stitch_windows(beat_windows, duration)
        body.update(beats_sec=grid.beats_sec, downbeats_sec=grid.downbeats_sec,
            tempo=grid.tempo.model_dump(), meter=grid.meter.model_dump(),
            warnings=sorted(warnings | set(grid.warnings)))
    if await hash_file(source, max_bytes=MAX_SOURCE_BYTES) != request.audio_sha256:
        raise MusicAnalysisError('source_changed')
    engines = [AnalysisEngine(task='features', name='numpy-summary', revision=ANALYZER_REVISION,
        configuration_sha256=request.configuration_sha256),
        AnalysisEngine(task='key', name='numpy-chroma', revision=first.key.method,
        configuration_sha256=request.configuration_sha256),
        AnalysisEngine(task='sections', name='numpy-energy-novelty', revision=ANALYZER_REVISION,
        configuration_sha256=request.configuration_sha256),
        AnalysisEngine(task='loudness', name='ffmpeg-loudnorm', revision='ffmpeg-loudnorm-oversampled-v1',
        configuration_sha256=request.configuration_sha256)]
    if request.beats is not None:
        engines.append(AnalysisEngine(task='beats', name='beat-this-small0', revision=BEATS_REVISION,
            configuration_sha256=request.beats.identity.configuration_sha256,
            weights_sha256=request.beats.identity.weights_sha256, precision='float32'))
    return AnalysisResult.model_validate({**body, 'id': request.result_id, 'source': request.source.model_dump(),
        'audio_sha256': request.audio_sha256, 'sample_rate_hz': header.rate, 'channels': header.channels,
        'duration_frames': frames, 'analyzer_revision': ANALYZER_REVISION,
        'configuration_sha256': request.configuration_sha256, 'loudness': metrics.model_dump(),
        'engines': [engine.model_dump() for engine in engines], 'created_at': request.created_at,
        'weights_sha256': request.beats.identity.weights_sha256 if request.beats else None,
        'license_evidence_ids': list(LICENSE_EVIDENCE_IDS) if request.beats else []})


def _atomic(path: Path, content: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def main(request_path: Path, result_path: Path) -> int:
    root = request_path.parent.resolve()
    if (request_path.name != 'request.json' or result_path.name != 'result.json' or result_path.parent.resolve() != root
            or request_path.is_symlink() or result_path.is_symlink() or request_path.stat().st_size > 131072):
        return 1
    request = AnalysisWorkerRequest.model_validate_json(request_path.read_bytes())
    if root.name != request.job_id:
        return 1
    try:
        result = await analyze_snapshot(request, root, run_command)
        outcome = AnalysisWorkerOutcome(result=result)
    except (MusicAnalysisError, MusicAnalysisFeatureError) as error:
        outcome = AnalysisWorkerOutcome(error_code=error.code)
    except TimeoutError:
        outcome = AnalysisWorkerOutcome(error_code='analysis_timeout')
    except AudioQualityError:
        outcome = AnalysisWorkerOutcome(error_code='invalid_audio')
    except Exception:
        outcome = AnalysisWorkerOutcome(error_code='analysis_failed')
    content = outcome.model_dump_json().encode()
    if len(content) > MAX_DOCUMENT_BYTES:
        content = AnalysisWorkerOutcome(error_code='worker_output_limit').model_dump_json().encode()
    _atomic(result_path, content)
    receipt = AnalysisWorkerReceipt(result_sha256=hashlib.sha256(content).hexdigest()).model_dump_json()
    _atomic(root / 'completion.json', receipt.encode())
    print(receipt, flush=True)
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--result', required=True, type=Path)
    arguments = parser.parse_args()
    raise SystemExit(asyncio.run(main(arguments.request, arguments.result)))
