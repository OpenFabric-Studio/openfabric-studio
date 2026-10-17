"""Caller-owned CPU rendering. No queue, worker registry, model or installation.

The supplied command callback must contain and drain its complete process tree
before returning or raising. Failed/unverified workspaces remain caller-owned.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
from typing import Literal, Protocol
import uuid

import numpy as np
import soundfile as sf

from .audio_quality import (AudioQualityError, INPUT, MAX_DECODED_BYTES, Measurement, RunCommand,
    measure, normalization_filter, target_result)
from .audio_quality_contracts import AudioMetrics, LoudnessSettings
from .job_lifecycle import run_owned_thread
from .music_dsp_contracts import (DspEngineIdentity, DspErrorCode, DspInputSource, DspMeasuredInput,
    DspMixRequest, DspMixResult, DspTransformRequest, DspTransformResult, DspWarning)
from .video_io import hash_file
from .video_media import tool

MAX_SOURCE_BYTES = 8 * 1024**3
MAX_NATIVE_SECONDS = 600
MAX_MAPPED_NATIVE_SECONDS = 30
DISK_RESERVE = 1024**3
DSP_REVISION = 'ffmpeg-cpu-dsp-v1'


async def splice_inpaint(original: Path, generated: Path, output: Path, *, sample_rate_hz: int,
                         channels: int, source_frames: int, output_frames: int, start_frame: int,
                         end_frame: int, crossfade_frames: int, run: RunCommand) -> None:
    """Preserve original float32-representable WAV samples outside a reviewed mask.

    Crossfades are linear and entirely inside the region. The caller owns every
    input, intermediate and output and must drain its command process tree.
    """
    integers = (sample_rate_hz, channels, source_frames, output_frames, start_frame, end_frame, crossfade_frames)
    root = _root(output.parent)
    if (any(type(value) is not int for value in integers) or sample_rate_hz not in (44100, 48000)
            or channels not in (1, 2) or not 0 < source_frames == output_frames <= sample_rate_hz * 600
            or not 0 <= start_frame < end_frame <= source_frames
            or not 0 <= crossfade_frames <= sample_rate_hz // 20
            or crossfade_frames * 2 > end_frame - start_frame
            or any(path.is_symlink() or path.resolve() != path or path.parent != root for path in (original, generated, output))
            or len({original, generated, output}) != 3 or output.exists()):
        raise MusicDspError('dsp_output_invalid')
    try:
        info, native = await run_owned_thread(sf.info, original), await run_owned_thread(sf.info, generated)
        if ((info.samplerate, info.channels, info.frames) != (sample_rate_hz, channels, source_frames)
                or info.format not in ('WAV', 'WAVEX') or info.subtype not in ('PCM_16', 'PCM_24', 'FLOAT')
                or (native.samplerate, native.channels, native.frames) != (44100, 2, round(source_frames * 44100 / sample_rate_hz))
                or native.format not in ('WAV', 'WAVEX') or native.subtype not in ('PCM_16', 'FLOAT')
                or any(path.stat().st_size > _pcm_bytes(max(source_frames, native.frames), 2) for path in (original, generated))):
            raise MusicDspError('dsp_output_invalid')
    except (OSError, RuntimeError, ValueError) as error:
        raise MusicDspError('dsp_output_invalid') from error
    before_original = await hash_file(original, max_bytes=MAX_SOURCE_BYTES)
    before_generated = await hash_file(generated, max_bytes=MAX_SOURCE_BYTES)
    aligned = root / f'inpaint-aligned-{uuid.uuid4().hex}.wav'
    await _write(generated, aligned, [], sample_rate_hz, channels, source_frames, command_callback(run))
    await run_owned_thread(_validate_pcm, aligned, sample_rate_hz, channels, source_frames)

    def splice() -> None:
        with sf.SoundFile(original) as reference, sf.SoundFile(aligned) as replacement, output.open('xb') as handle:
            with sf.SoundFile(handle.fileno(), mode='w', samplerate=sample_rate_hz, channels=channels,
                              format='WAV', subtype='FLOAT', closefd=False) as target:
                position = 0
                while position < source_frames:
                    count = min(65536, source_frames - position)
                    old = reference.read(count, dtype='float32', always_2d=True)
                    new = replacement.read(count, dtype='float32', always_2d=True)
                    if len(old) != count or len(new) != count or not np.all(np.isfinite(old)) or not np.all(np.isfinite(new)):
                        raise MusicDspError('dsp_output_invalid')
                    indexes = np.arange(position, position + count)
                    selected = (indexes >= start_frame) & (indexes < end_frame)
                    weight = selected.astype(np.float32)
                    if crossfade_frames:
                        denominator = max(1, crossfade_frames - 1)
                        left = (indexes >= start_frame) & (indexes < start_frame + crossfade_frames)
                        right = (indexes >= end_frame - crossfade_frames) & (indexes < end_frame)
                        weight[left] = (indexes[left] - start_frame) / denominator
                        weight[right] = (end_frame - 1 - indexes[right]) / denominator
                    result = old.copy()
                    result[selected] = old[selected] * (1 - weight[selected, None]) + new[selected] * weight[selected, None]
                    target.write(result)
                    position += count
            os.fsync(handle.fileno())

    await run_owned_thread(splice)
    await run_owned_thread(_validate_pcm, output, sample_rate_hz, channels, output_frames)
    if (await hash_file(original, max_bytes=MAX_SOURCE_BYTES) != before_original
            or await hash_file(generated, max_bytes=MAX_SOURCE_BYTES) != before_generated):
        raise MusicDspError('dsp_source_changed')


class MusicDspError(Exception):
    def __init__(self, code: DspErrorCode) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ResolvedDspSource:
    path: Path
    source: DspInputSource
    sha256: str
    sample_rate_hz: int
    channels: int
    duration_frames: int
    metrics: AudioMetrics | None = None


@dataclass(frozen=True)
class LevelMatchGain:
    gain_db: float
    residual_lu: float | None
    warnings: tuple[DspWarning, ...]


def level_match_gain(reference: AudioMetrics, prepared: AudioMetrics) -> LevelMatchGain:
    values = (reference.integrated_lufs, prepared.integrated_lufs)
    if (min(reference.duration_sec, prepared.duration_sec) < 3 or any(value is None or not math.isfinite(value) for value in values)
            or reference.integrated_lufs is None or prepared.integrated_lufs is None):
        return LevelMatchGain(0.0, None, ('level_match_unavailable',))
    difference = reference.integrated_lufs - prepared.integrated_lufs
    gain = max(-12.0412, min(12.0412, difference))
    warnings: list[DspWarning] = []
    if gain != difference:
        warnings.append('gain_capped')
    if prepared.true_peak_dbtp is not None and prepared.true_peak_dbtp + gain > 0:
        warnings.append('hard_clip_risk')
    return LevelMatchGain(gain, difference - gain, tuple(warnings))


class NativeStretch(Protocol):
    async def __call__(self, source_wav: Path, output_wav: Path, *, sample_rate_hz: int,
                       channels: int, input_frames: int, output_frames: int,
                       anchors: tuple[tuple[int, int], ...], pitch_shift_semitones: float,
                       run: RunCommand) -> DspEngineIdentity: ...


def command_callback(run: RunCommand) -> RunCommand:
    async def command(argv: list[str], timeout: float) -> bytes:
        try:
            return await run(argv, timeout)
        except TimeoutError as error:
            raise MusicDspError('dsp_timeout') from error
    return command


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _root(path: Path) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise MusicDspError('dsp_invalid_source')
    return path.resolve()


def _pcm_bytes(frames: int, channels: int) -> int:
    size = (frames + 1024) * channels * 4 + 4096
    if not 0 < size < MAX_DECODED_BYTES:
        raise MusicDspError('dsp_budget_exceeded')
    return size


def _disk(root: Path, required: int) -> None:
    if shutil.disk_usage(root).free < required + DISK_RESERVE:
        raise MusicDspError('dsp_insufficient_disk')


def _source_metadata(value: ResolvedDspSource) -> None:
    if (type(value.sample_rate_hz) is not int or not 8000 <= value.sample_rate_hz <= 192000
            or type(value.channels) is not int or value.channels not in {1, 2}
            or type(value.duration_frames) is not int or not 0 < value.duration_frames <= value.sample_rate_hz * 7200):
        raise MusicDspError('dsp_invalid_source')


async def _source(value: ResolvedDspSource, expected: DspInputSource, digest: str) -> None:
    _source_metadata(value)
    if value.source != expected or value.sha256 != digest:
        raise MusicDspError('dsp_source_changed')
    if value.path.is_symlink() or value.path.resolve() != value.path or not value.path.is_file():
        raise MusicDspError('dsp_invalid_source')
    if await hash_file(value.path, max_bytes=MAX_SOURCE_BYTES) != digest:
        raise MusicDspError('dsp_source_changed')


async def _metrics(source: ResolvedDspSource, root: Path, run: RunCommand, name: str) -> AudioMetrics:
    metrics = source.metrics
    if metrics is None:
        budget = _pcm_bytes(source.duration_frames + math.ceil(.1 * source.sample_rate_hz), source.channels)
        try:
            metrics = (await measure(source.path, root, run, name=name, max_decoded_bytes=budget)).metrics
        except AudioQualityError as error:
            raise MusicDspError('dsp_budget_exceeded' if error.code == 'audio_decode_budget_exceeded' else 'dsp_invalid_source') from error
    if (metrics.source_sha256 != source.sha256 or metrics.samples_analyzed != source.duration_frames * source.channels
            or not math.isclose(metrics.duration_sec, source.duration_frames / source.sample_rate_hz, abs_tol=1e-9, rel_tol=0)):
        raise MusicDspError('dsp_invalid_source')
    return metrics


def frame_anchors(request: DspTransformRequest, source: ResolvedDspSource) -> tuple[tuple[int, int], ...]:
    _source_metadata(source)
    rate = request.sample_rate_hz or source.sample_rate_hz
    anchors = tuple((round(anchor.source_sec * source.sample_rate_hz), round(anchor.target_sec * rate))
        for anchor in request.anchors)
    if anchors[-1][0] > source.duration_frames:
        raise MusicDspError('dsp_invalid_anchors')
    for previous, current in zip(anchors, anchors[1:]):
        delta_source, delta_target = current[0] - previous[0], current[1] - previous[1]
        if delta_source <= 0 or delta_target <= 0:
            raise MusicDspError('dsp_invalid_anchors')
        ratio = delta_target / rate / (delta_source / source.sample_rate_hz)
        if not request.min_duration_ratio - 1e-9 <= ratio <= request.max_duration_ratio + 1e-9:
            raise MusicDspError('dsp_invalid_anchors')
    return anchors


def _channels(before: int, after: int) -> list[str]:
    if before == after:
        return []
    return ['pan=stereo|c0=c0|c1=c0'] if before == 1 else ['pan=mono|c0=0.5*c0+0.5*c1']


def _compressor(threshold: float, ratio: float, knee: float, attack: float, release: float, makeup: float = 0) -> str:
    return (f'threshold={10 ** (threshold / 20):.12g}:ratio={ratio:.12g}:knee={10 ** (knee / 20):.12g}'
        f':attack={attack:.12g}:release={release:.12g}:makeup={10 ** (makeup / 20):.12g}'
        ':link=maximum:detection=rms:mode=downward')


def _effects(request: DspTransformRequest, rate: int, channels: int, frames: int) -> list[str]:
    filters: list[str] = []
    if request.gain_db:
        filters.append(f'volume={request.gain_db:.12g}dB:precision=double')
    if request.highpass_hz is not None:
        filters.append(f'highpass=f={request.highpass_hz:.12g}:p=2:t=q:w=0.7071067811865476:r=f64')
    for band in request.eq_bands:
        if band.frequency_hz > min(20000, .45 * rate):
            raise MusicDspError('dsp_invalid_source')
        if band.gain_db:
            filters.append(f'equalizer=f={band.frequency_hz:.12g}:t=q:w={band.q:.12g}:g={band.gain_db:.12g}:r=f64')
    compression = request.compression
    if compression is not None:
        filters.append('acompressor=' + _compressor(compression.threshold_dbfs, compression.ratio,
            compression.knee_db, compression.attack_ms, compression.release_ms, compression.makeup_db))
    curve = 'qsin' if request.fades.curve == 'equal_power' else 'tri'
    for direction, seconds in (('in', request.fades.fade_in_sec), ('out', request.fades.fade_out_sec)):
        count = round(seconds * rate)
        if count:
            start = 0 if direction == 'in' else frames - count
            if direction == 'out' and count == 1:
                expressions = '|'.join(f'if(eq(n,{frames - 1}),0,val({channel}))' for channel in range(channels))
                filters.append(f'aeval={expressions}')
            else:
                filters.append(f'afade=t={direction}:ss={start}:ns={max(1, count - 1)}:curve={curve}')
    return filters


async def _write(source: Path, output: Path, filters: list[str], rate: int, channels: int, frames: int, run: RunCommand) -> None:
    if output.exists() or output.is_symlink():
        raise MusicDspError('dsp_invalid_source')
    packet = 1024 * channels * 4
    await run([tool('ffmpeg'), '-v', 'error', '-nostdin', *INPUT, '-i', str(source), '-map', '0:a:0', '-vn',
        '-af', ','.join([*filters, 'asetnsamples=n=1024:p=0']), '-ar', str(rate), '-ac', str(channels),
        '-c:a', 'pcm_f32le', '-fs', str(_pcm_bytes(frames, channels) - packet), str(output)], 3600.0)


def _validate_pcm(path: Path, rate: int, channels: int, frames: int) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _pcm_bytes(frames, channels):
        raise MusicDspError('dsp_output_invalid')
    try:
        info = sf.info(path)
    except (OSError, RuntimeError) as error:
        raise MusicDspError('dsp_output_invalid') from error
    if (info.samplerate, info.channels, info.frames, info.subtype) != (rate, channels, frames, 'FLOAT'):
        raise MusicDspError('dsp_output_invalid')


def _pcm_peak(path: Path) -> float:
    maximum = 0.0
    with sf.SoundFile(path) as source:
        while len(block := source.read(65536, dtype='float32', always_2d=True)):
            if not np.all(np.isfinite(block)):
                raise MusicDspError('dsp_output_invalid')
            maximum = max(maximum, float(np.max(np.abs(block))))
    return maximum


async def _engine(run: RunCommand, configuration: str) -> DspEngineIdentity:
    output = await run([tool('ffmpeg'), '-version'], 15.0)
    executable = await hash_file(Path(tool('ffmpeg')).resolve(), max_bytes=512 * 1024**2)
    return DspEngineIdentity(name='ffmpeg', revision=DSP_REVISION,
        executable_sha256=executable,
        configuration_sha256=_digest({'request': configuration, 'ffmpeg_recipe': hashlib.sha256(output).hexdigest(), 'executable': executable}))


async def _finish(path: Path, root: Path, identifier: str, rate: int, channels: int, frames: int,
                  loudness: LoudnessSettings, run: RunCommand) -> tuple[Path, Measurement, list[DspWarning]]:
    await run_owned_thread(_validate_pcm, path, rate, channels, frames)
    budget = _pcm_bytes(frames + math.ceil(.1 * rate), channels)
    try:
        measured = await measure(path, root, run, name=identifier + '-output', max_decoded_bytes=budget)
    except AudioQualityError as error:
        raise MusicDspError('dsp_budget_exceeded' if error.code == 'audio_decode_budget_exceeded' else 'dsp_output_invalid') from error
    warnings: list[DspWarning] = []
    if loudness.target is not None:
        normalize = normalization_filter(loudness, measured)
        if normalize is None:
            warnings.append('loudness_inconclusive')
        else:
            normalized = root / (identifier + '-normalized.wav')
            await _write(path, normalized, [normalize], rate, channels, frames, run)
            path = normalized
            await run_owned_thread(_validate_pcm, path, rate, channels, frames)
            try:
                measured = await measure(path, root, run, name=identifier + '-normalized', max_decoded_bytes=budget)
            except AudioQualityError as error:
                raise MusicDspError('dsp_budget_exceeded' if error.code == 'audio_decode_budget_exceeded' else 'dsp_output_invalid') from error
            if target_result(loudness, measured.metrics) != 'met':
                warnings.append('loudness_target_unmet')
    if measured.metrics.true_peak_dbtp is not None and measured.metrics.true_peak_dbtp > 0:
        warnings.append('true_peak_over')
    if measured.metrics.full_scale_fraction:
        warnings.append('hard_clip_risk')
    final = root / (identifier + '.wav')
    if final.exists() or final.is_symlink():
        raise MusicDspError('dsp_output_invalid')
    await run_owned_thread(path.replace, final)
    return final, measured, warnings


async def render_transform(request: DspTransformRequest, source: ResolvedDspSource, staging_root: Path,
                           run: RunCommand, *, native: NativeStretch | None = None) -> DspTransformResult:
    run = command_callback(run)
    anchors = frame_anchors(request, source)
    rate = request.sample_rate_hz or source.sample_rate_hz
    channels_value = request.channels or source.channels
    if channels_value == 1:
        channels: Literal[1, 2] = 1
    elif channels_value == 2:
        channels = 2
    else:
        raise MusicDspError('dsp_invalid_source')
    frames = anchors[-1][1] - anchors[0][1]
    effects = _effects(request, rate, channels, frames)
    needs_native = request.pitch_shift_semitones != 0 or any(
        right[1] - left[1] != round((right[0] - left[0]) * rate / source.sample_rate_hz)
        for left, right in zip(anchors, anchors[1:]))
    if needs_native and native is None:
        raise MusicDspError('dsp_stretch_unavailable')
    dry_frames = round((anchors[-1][0] - anchors[0][0]) * rate / source.sample_rate_hz)
    local = tuple((round((source_frame - anchors[0][0]) * rate / source.sample_rate_hz),
        target_frame - anchors[0][1]) for source_frame, target_frame in anchors)
    uniform = dry_frames > 0 and all(abs(target - source_frame * frames / dry_frames) <= .5
        for source_frame, target in local)
    native_limit = MAX_NATIVE_SECONDS if uniform else MAX_MAPPED_NATIVE_SECONDS
    if needs_native and (anchors[-1][0] - anchors[0][0]) / source.sample_rate_hz > native_limit:
        raise MusicDspError('dsp_budget_exceeded')
    root = _root(staging_root)
    await _source(source, request.source, request.source_sha256)
    _disk(root, 4 * _pcm_bytes(max(frames, source.duration_frames), max(channels, source.channels)))
    metrics = await _metrics(source, root, run, uuid.uuid4().hex + '-input')
    identifier = uuid.uuid4().hex
    dry = root / (identifier + '-dry.wav')
    filters = [f'atrim=start_sample={anchors[0][0]}:end_sample={anchors[-1][0]}', 'asetpts=PTS-STARTPTS']
    if rate != source.sample_rate_hz:
        filters.append(f'aresample={rate}')
    filters.extend(_channels(source.channels, channels))
    await _write(source.path, dry, filters, rate, channels, dry_frames, run)
    await run_owned_thread(_validate_pcm, dry, rate, channels, dry_frames)
    configuration = _digest({'request': request.model_dump(), 'recipe': DSP_REVISION,
        'source_code': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    engines = [await _engine(run, configuration)]
    rendered = dry
    if needs_native:
        if native is None:
            raise MusicDspError('dsp_stretch_unavailable')
        rendered = root / (identifier + '-stretched.wav')
        if any(right[0] <= left[0] or right[1] <= left[1] for left, right in zip(local, local[1:])):
            raise MusicDspError('dsp_invalid_anchors')
        engines.append(await native(dry, rendered, sample_rate_hz=rate, channels=channels,
            input_frames=dry_frames, output_frames=frames, anchors=local,
            pitch_shift_semitones=request.pitch_shift_semitones, run=run))
        await run_owned_thread(_validate_pcm, rendered, rate, channels, frames)
        dry_peak = await run_owned_thread(_pcm_peak, dry)
        output_peak = await run_owned_thread(_pcm_peak, rendered)
        if dry_peak > 0 and output_peak == 0:
            raise MusicDspError('dsp_output_invalid')
    if effects:
        processed = root / (identifier + '-processed.wav')
        await _write(rendered, processed, effects, rate, channels, frames, run)
        rendered = processed
    final, output, warnings = await _finish(rendered, root, identifier, rate, channels, frames, request.loudness, run)
    await _source(source, request.source, request.source_sha256)
    if abs(request.pitch_shift_semitones) > 2:
        warnings.append('pitch_artifact_risk')
    return DspTransformResult(id=identifier, revision=request.revision, artifact_name=final.name,
        audio_sha256=output.metrics.source_sha256, sample_rate_hz=rate, channels=channels, duration_frames=frames,
        output_metrics=output.metrics, target_result=target_result(request.loudness, output.metrics),
        engines=engines, configuration_sha256=configuration, warnings=warnings, created_at=datetime.now(timezone.utc).isoformat(),
        request=request, input_metrics=metrics, target_start_frame=anchors[0][1], applied_gain_db=request.gain_db)


async def render_mix(request: DspMixRequest, sources: Mapping[str, ResolvedDspSource], staging_root: Path,
                     run: RunCommand) -> DspMixResult:
    run = command_callback(run)
    root = _root(staging_root)
    rate, channels = request.sample_rate_hz, request.channels
    frames = round(request.duration_sec * rate)
    if frames < 1 or set(sources) != {item.id for item in request.inputs}:
        raise MusicDspError('dsp_invalid_source')
    _disk(root, 3 * _pcm_bytes(frames, channels) + sum(_pcm_bytes(source.duration_frames, source.channels) for source in sources.values()))
    measured: list[DspMeasuredInput] = []
    argv = [tool('ffmpeg'), '-v', 'error', '-nostdin']
    parts: list[str] = []
    indices = {item.id: index for index, item in enumerate(request.inputs)}
    for index, item in enumerate(request.inputs):
        source = sources[item.id]
        await _source(source, item.source, item.source_sha256)
        metrics = await _metrics(source, root, run, uuid.uuid4().hex + '-input')
        measured.append(DspMeasuredInput(id=item.id, source=item.source, source_sha256=item.source_sha256, metrics=metrics))
        start = round(item.source_start_sec * source.sample_rate_hz)
        end = round((item.source_start_sec + item.duration_sec) * source.sample_rate_hz)
        if end > source.duration_frames or end <= start:
            raise MusicDspError('dsp_invalid_source')
        argv.extend([*INPUT, '-i', str(source.path)])
        filters = [f'atrim=start_sample={start}:end_sample={end}', 'asetpts=PTS-STARTPTS', f'aresample={rate}',
            *_channels(source.channels, channels), f'adelay=delays={round(item.start_sec * rate)}S:all=1',
            f'apad=whole_len={frames}', f'atrim=end_sample={frames}']
        consumers = [candidate for candidate in request.inputs if candidate.duck is not None
            and candidate.duck.max_reduction_db > 0 and candidate.duck.sidechain_input_id == item.id]
        labels = [f'base{index}', *(f'sc{index}_{indices[candidate.id]}' for candidate in consumers)]
        parts.append(f'[{index}:a:0]' + ','.join(filters) + f',asplit={len(labels)}' + ''.join(f'[{label}]' for label in labels))
    for index, item in enumerate(request.inputs):
        label = f'base{index}'
        duck = item.duck
        if duck is not None and duck.max_reduction_db > 0:
            minimum = 10 ** (-duck.max_reduction_db / 20)
            parts.extend([f'[{label}]asplit=2[floor{index}][main{index}]',
                f'[main{index}][sc{indices[duck.sidechain_input_id]}_{index}]sidechaincompress=' + _compressor(
                    duck.threshold_dbfs, duck.ratio, duck.knee_db, duck.attack_ms, duck.release_ms) + f'[compressed{index}]',
                f'[floor{index}]volume={minimum:.12g}:precision=double[keep{index}]',
                f'[compressed{index}]volume={1 - minimum:.12g}:precision=double[reduce{index}]',
                f'[keep{index}][reduce{index}]amix=inputs=2:normalize=0:duration=first:dropout_transition=0[duck{index}]'])
            label = f'duck{index}'
        filters = ['volume=0:precision=double' if item.muted else f'volume={item.gain_db:.12g}dB:precision=double']
        if item.pan and channels == 2:
            filters.append(f'pan=stereo|c0={min(1.0, 1 - item.pan):.12g}*c0|c1={min(1.0, 1 + item.pan):.12g}*c1')
        parts.append(f'[{label}]' + ','.join(filters) + f'[mix{index}]')
    parts.append(''.join(f'[mix{index}]' for index in range(len(request.inputs))) +
        f'amix=inputs={len(request.inputs)}:normalize=0:duration=longest:dropout_transition=0,asetnsamples=n=1024:p=0[out]')
    identifier = uuid.uuid4().hex
    graph = ';'.join(parts)
    if len(graph.encode()) > 131072:
        raise MusicDspError('dsp_budget_exceeded')
    rendered = root / (identifier + '-mix.wav')
    argv.extend(['-filter_complex', graph, '-map', '[out]', '-c:a', 'pcm_f32le',
        '-fs', str(_pcm_bytes(frames, channels) - 1024 * channels * 4), str(rendered)])
    await run(argv, 3600.0)
    final, output, warnings = await _finish(rendered, root, identifier, rate, channels, frames, request.loudness, run)
    for item in request.inputs:
        await _source(sources[item.id], item.source, item.source_sha256)
    configuration = _digest({'request': request.model_dump(), 'recipe': DSP_REVISION,
        'source_code': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    return DspMixResult(id=identifier, revision=request.revision, artifact_name=final.name,
        audio_sha256=output.metrics.source_sha256, sample_rate_hz=rate, channels=channels, duration_frames=frames,
        output_metrics=output.metrics, target_result=target_result(request.loudness, output.metrics),
        engines=[await _engine(run, configuration)], configuration_sha256=configuration, warnings=warnings,
        created_at=datetime.now(timezone.utc).isoformat(), request=request, input_metrics=measured)
