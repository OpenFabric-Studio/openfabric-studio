"""Replacement-specific fitting and level choices over the shared DSP core."""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from pathlib import Path
from typing import Literal
import uuid
import hashlib
import json

import numpy as np
import soundfile as sf
from pydantic import Field

from .audio_quality import RunCommand
from .audio_quality_contracts import LoudnessSettings
from .music_dsp import (NativeStretch, ResolvedDspSource, _metrics, _engine, _disk, _source, _validate_pcm, level_match_gain,
    render_mix, render_transform)
from .music_dsp_contracts import (DspArtifactRef, DspMixInput, DspMixRequest, DspMixResult,
    DspTransformRequest, DspTransformResult, FadeSettings, WarpAnchor, validate_anchors)
from .music_dsp_contracts import DspEngineIdentity
from .music_analysis_contracts import MusicAnalysisContract, MusicAnalysisId, MusicAnalysisSha256
from .audio_quality_contracts import AudioMetrics
from .audio_quality import INPUT
from .job_lifecycle import run_owned_thread
from .video_io import hash_file
from .video_media import tool
from .replacement_contracts import ReplacementMixSettings, ReplacementWarning

CROSSFADE_SECONDS = .01
MAX_PHRASE_SECONDS = 29.9  # leaves room for explicit overlap under native 30 s map cap
CLEANUP_RECIPE = 'tail-attenuation-65536-frame-8192-context-v1'


def prepare_performance_file(source: Path, output: Path, *, cleanup: Literal['off', 'light'],
                             keep_breaths: bool) -> None:
    """Bounded audition processing, without cropping, normalization or breath detection."""
    from .clean_vocal import clean_vocal
    info = sf.info(source)
    if (info.channels not in (1, 2) or not 8000 <= info.samplerate <= 192000
            or not 0 < info.frames <= info.samplerate * 7200 or output.exists()):
        raise ValueError('replacement_output_invalid')
    peak = 0.0
    with sf.SoundFile(source) as audio:
        while (block := audio.read(65536, dtype='float32', always_2d=True)).shape[0]:
            if not np.isfinite(block).all():
                raise ValueError('replacement_output_invalid')
            peak = max(peak, float(np.max(np.abs(block))))
    with sf.SoundFile(source) as audio, sf.SoundFile(output, 'w', samplerate=info.samplerate,
            channels=info.channels, subtype='FLOAT') as destination:
        for start in range(0, info.frames, 65536):
            left, end = max(0, start - 8192), min(info.frames, start + 65536 + 8192)
            audio.seek(left)
            block = audio.read(end - left, dtype='float32', always_2d=True)
            if len(block) != end - left or not np.isfinite(block).all():
                raise ValueError('replacement_output_invalid')
            processed = clean_vocal(block, info.samplerate) if cleanup == 'light' else block
            if not keep_breaths and peak > 0:
                # Shared stereo gain reduces all quiet content by at most 3 dB.
                # Amplitude cannot identify a breath, singer or recording source.
                power = np.mean(block.astype(np.float64) ** 2, axis=1)
                width = max(1, round(info.samplerate * .02))
                summed = np.concatenate((np.zeros(1), np.cumsum(np.pad(power, (width // 2, width - width // 2)))))
                rms = np.sqrt((summed[width:width + len(block)] - summed[:len(block)]) / width)
                gain = 10 ** (-3 / 20) + (1 - 10 ** (-3 / 20)) * np.clip(rms / (peak * .02), 0, 1)
                processed = np.asarray(processed * gain[:, None], dtype=np.float32)
            offset, count = start - left, min(65536, info.frames - start)
            destination.write(processed[offset:offset + count])


def phrase_spans(anchors: list[WarpAnchor], rate: int) -> list[list[WarpAnchor]]:
    """Partition a monotone map, retaining its absolute timeline and overlaps.

    Each output overlaps its neighbour by exactly 10 ms (native-frame rounded).
    The caller applies complementary linear fades inside that overlap. This is
    an explicit stitch recipe, not a claim of whole-song native map accuracy.
    """
    validate_anchors(anchors)
    rounded = [(round(item.source_sec * rate), round(item.target_sec * rate)) for item in anchors]
    if any(b[0] <= a[0] or b[1] <= a[1] for a, b in zip(rounded, rounded[1:])):
        raise ValueError('replacement_alignment_invalid')
    points: list[tuple[float, float]] = [(anchors[0].source_sec, anchors[0].target_sec)]
    for left, right in zip(anchors, anchors[1:]):
        count = max(1, math.ceil((right.source_sec - left.source_sec) / MAX_PHRASE_SECONDS))
        for index in range(1, count + 1):
            fraction = index / count
            points.append((left.source_sec + fraction * (right.source_sec - left.source_sec),
                left.target_sec + fraction * (right.target_sec - left.target_sec)))
    if len(points) > 1001:
        raise ValueError('replacement_alignment_capacity')

    def source_at(target: float) -> float:
        for left, right in zip(anchors, anchors[1:]):
            if target <= right.target_sec + 1e-12:
                return left.source_sec + (target - left.target_sec) * (right.source_sec - left.source_sec) / (right.target_sec - left.target_sec)
        return anchors[-1].source_sec

    half = round(CROSSFADE_SECONDS * rate / 2) / rate
    spans: list[list[WarpAnchor]] = []
    for index, (left_point, right_point) in enumerate(zip(points, points[1:])):
        start = left_point[1] - (half if index else 0)
        end = right_point[1] + (half if index + 2 < len(points) else 0)
        start = max(anchors[0].target_sec, start)
        end = min(anchors[-1].target_sec, end)
        segment = [WarpAnchor(source_sec=source_at(start), target_sec=start)]
        segment.extend(anchor for anchor in anchors if start < anchor.target_sec < end)
        segment.append(WarpAnchor(source_sec=source_at(end), target_sec=end))
        if segment[-1].source_sec - segment[0].source_sec > 30:
            raise ValueError('replacement_alignment_capacity')
        spans.append(segment)
    return spans


@dataclass(frozen=True)
class FittedVocal:
    result: DspTransformResult | DspMixResult
    phrases: tuple[DspTransformResult, ...]
    recipe: str


async def fit_prepared(source: ResolvedDspSource, anchors: list[WarpAnchor], duration: float, revision: int,
                       pitch: float, root: Path, run: RunCommand, native: NativeStretch | None) -> FittedVocal:
    if source.source.kind == 'transform':
        raise ValueError('replacement_alignment_invalid')
    uniform = all(math.isclose((right.target_sec - left.target_sec) / (right.source_sec - left.source_sec),
        (anchors[-1].target_sec - anchors[0].target_sec) / (anchors[-1].source_sec - anchors[0].source_sec), abs_tol=1e-9)
        for left, right in zip(anchors, anchors[1:]))
    # A whole-song uniform transform uses the independently verified native cap.
    # Nonuniform maps use explicit <=30s overlapping transforms and a measured
    # sample-offset mix. No waveform is invented in uncovered timeline regions.
    spans = [anchors] if uniform else phrase_spans(anchors, 48000)
    results: list[DspTransformResult] = []
    for index, span in enumerate(spans):
        result = await render_transform(DspTransformRequest(source=source.source, source_sha256=source.sha256,
            revision=revision, anchors=span, output_duration_sec=span[-1].target_sec - span[0].target_sec,
            sample_rate_hz=48000, pitch_shift_semitones=pitch,
            fades=FadeSettings(fade_in_sec=CROSSFADE_SECONDS if index else 0.0,
                fade_out_sec=CROSSFADE_SECONDS if index + 1 < len(spans) else 0.0)), source, root, run, native=native)
        results.append(result)
    mixed = await stitch_phrases(results, duration, revision, 1 if source.channels == 1 else 2, root, run)
    return FittedVocal(mixed, tuple(results), 'uniform-fit-v1' if uniform else 'reviewed-phrase-linear-10ms-crossfade-v1')


async def stitch_phrases(results: list[DspTransformResult], duration: float, revision: int,
                         channels: Literal[1, 2], root: Path, run: RunCommand) -> DspMixResult:
    """Sample-offset sums in groups of at most 32; no normalization or time shift."""
    if not 0 < len(results) <= 1000:
        raise ValueError('replacement_alignment_capacity')
    inputs: list[DspMixInput] = []
    sources: dict[str, ResolvedDspSource] = {}
    for result in results:
        identifier = uuid.uuid4().hex
        ref = DspArtifactRef(id=result.id, revision=revision, sha256=result.audio_sha256)
        sources[identifier] = ResolvedDspSource(root / result.artifact_name, ref, result.audio_sha256,
            result.sample_rate_hz, result.channels, result.duration_frames, result.output_metrics)
        inputs.append(DspMixInput(id=identifier, source=ref, source_sha256=result.audio_sha256,
            start_sec=result.target_start_frame / result.sample_rate_hz,
            duration_sec=min(result.duration_frames / result.sample_rate_hz,
                (result.target_start_frame + result.duration_frames) / result.sample_rate_hz
                - result.target_start_frame / result.sample_rate_hz)))
    while len(inputs) > 32:
        grouped: list[DspMixInput] = []
        next_sources: dict[str, ResolvedDspSource] = {}
        for offset in range(0, len(inputs), 32):
            group = inputs[offset:offset + 32]
            start = min(item.start_sec for item in group)
            end = max(round(item.start_sec * 48000) + round(item.duration_sec * 48000) for item in group) / 48000
            batch = [item.model_copy(update={'start_sec': item.start_sec - start}) for item in group]
            mixed = await render_mix(DspMixRequest(revision=revision, duration_sec=end - start, inputs=batch,
                channels=channels, sample_rate_hz=48000), {item.id: sources[item.id] for item in group}, root, run)
            identifier = uuid.uuid4().hex
            ref = DspArtifactRef(id=mixed.id, revision=revision, sha256=mixed.audio_sha256)
            next_sources[identifier] = ResolvedDspSource(root / mixed.artifact_name, ref, mixed.audio_sha256,
                mixed.sample_rate_hz, mixed.channels, mixed.duration_frames, mixed.output_metrics)
            grouped.append(DspMixInput(id=identifier, source=ref, source_sha256=mixed.audio_sha256,
                start_sec=start, duration_sec=min(mixed.duration_frames / mixed.sample_rate_hz,
                    (round(start * 48000) + mixed.duration_frames) / 48000 - start)))
        inputs, sources = grouped, next_sources
    return await render_mix(DspMixRequest(revision=revision, duration_sec=duration, inputs=inputs,
        channels=channels, sample_rate_hz=48000), sources, root, run)


@dataclass(frozen=True)
class ReplacementMix:
    vocal: DspTransformResult
    master: DspMixResult
    gain_db: float
    residual_lu: float | None
    warnings: tuple[ReplacementWarning, ...]
    processed: ResolvedDspSource
    convolution: ReplacementIrResult | None = None


class ReplacementIrResult(MusicAnalysisContract):
    id: MusicAnalysisId
    audio_sha256: MusicAnalysisSha256
    ir_sha256: MusicAnalysisSha256
    ir_gain: float = Field(gt=0, le=1)
    wet: float = Field(gt=0, le=.25)
    recipe: Literal['l1-headroom-linear-wet-tail-trim-v1'] = 'l1-headroom-linear-wet-tail-trim-v1'
    engine: DspEngineIdentity
    metrics: AudioMetrics


def _ir_gain(path: Path) -> float:
    info = sf.info(path)
    if info.channels not in (1, 2) or not 8000 <= info.samplerate <= 192000 or not 0 < info.frames <= info.samplerate * 10:
        raise ValueError('replacement_invalid_request')
    total = np.zeros(info.channels, dtype=np.float64)
    with sf.SoundFile(path) as audio:
        while (block := audio.read(65536, dtype='float32', always_2d=True)).shape[0]:
            if not np.isfinite(block).all():
                raise ValueError('replacement_output_invalid')
            total += np.sum(np.abs(block.astype(np.float64)), axis=0)
    maximum = float(np.max(total))
    if maximum <= 0:
        raise ValueError('replacement_invalid_request')
    return min(1.0, 1 / maximum)


async def convolve_owned_ir(source: ResolvedDspSource, impulse: ResolvedDspSource, wet: float,
                            root: Path, run: RunCommand) -> tuple[ReplacementIrResult, ResolvedDspSource]:
    if not 0 < wet <= .25 or impulse.sample_rate_hz != source.sample_rate_hz:
        raise ValueError('replacement_invalid_request')
    await _source(source, source.source, source.sha256)
    await _source(impulse, impulse.source, impulse.sha256)
    gain = await run_owned_thread(_ir_gain, impulse.path)
    _disk(root, source.duration_frames * source.channels * 4 * 3 + 32 * 1024**2)
    identifier = uuid.uuid4().hex
    output = root / (identifier + '.wav')
    layout = 'mono' if impulse.channels == 1 else 'stereo'
    channels = max(source.channels, impulse.channels)
    upmix = 'pan=stereo|c0=c0|c1=c0,' if source.channels == 1 and channels == 2 else ''
    graph = (f'[0:a]{upmix}asplit=2[dry][send];[1:a]'
        f'aformat=sample_fmts=fltp:channel_layouts={layout},volume={gain:.17g}[ir];'
        f'[send][ir]afir=dry=1:wet=1:irnorm=-1:irgain=1:irfmt={"mono" if impulse.channels == 1 else "input"}:'
        f'maxir=10:minp=256:maxp=8192:precision=double[reverb];'
        f'[dry]volume={1-wet:.17g}[direct];[reverb]volume={wet:.17g}[tail];'
        f'[direct][tail]amix=inputs=2:duration=first:normalize=0,'
        f'apad=whole_len={source.duration_frames},atrim=end_sample={source.duration_frames}[out]')
    await run([tool('ffmpeg'), '-v', 'error', '-xerror', '-n', *INPUT, '-i', str(source.path),
        *INPUT, '-i', str(impulse.path), '-filter_complex', graph, '-map', '[out]', '-c:a', 'pcm_f32le',
        '-fs', str(source.duration_frames * channels * 4 + 8192), str(output)], 600)
    await run_owned_thread(_validate_pcm, output, source.sample_rate_hz, channels, source.duration_frames)
    checksum = await hash_file(output, max_bytes=source.duration_frames * channels * 4 + 8192)
    audio = ResolvedDspSource(output, DspArtifactRef(id=identifier, revision=0, sha256=checksum), checksum,
        source.sample_rate_hz, channels, source.duration_frames)
    metrics = await _metrics(audio, root, run, identifier)
    configuration = hashlib.sha256(json.dumps({'recipe': 'l1-headroom-linear-wet-tail-trim-v1',
        'source': source.sha256, 'ir': impulse.sha256, 'gain': gain, 'wet': wet,
        'source_code': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, sort_keys=True).encode()).hexdigest()
    engine = await _engine(run, configuration)
    await _source(source, source.source, source.sha256)
    await _source(impulse, impulse.source, impulse.sha256)
    return ReplacementIrResult(id=identifier, audio_sha256=checksum, ir_sha256=impulse.sha256,
        ir_gain=gain, wet=wet, engine=engine, metrics=metrics), replace(audio, metrics=metrics)


async def mix_replacement(settings: ReplacementMixSettings, revision: int, original: ResolvedDspSource,
                          reference: ResolvedDspSource, vocal: ResolvedDspSource, backing: ResolvedDspSource,
                          root: Path, run: RunCommand, *, impulse: ResolvedDspSource | None = None) -> ReplacementMix:
    if settings.space != 'off' and impulse is None:
        raise ValueError('replacement_engine_unavailable')
    reference = replace(reference, metrics=await _metrics(reference, root, run, 'reference-level'))
    vocal = replace(vocal, metrics=await _metrics(vocal, root, run, 'prepared-level'))
    if reference.metrics is None or vocal.metrics is None or vocal.source.kind == 'transform':
        raise ValueError('replacement_output_invalid')
    match = level_match_gain(reference.metrics, vocal.metrics)
    gain = match.gain_db if settings.vocal_level == 'match_original' else settings.manual_gain_db
    warnings: list[ReplacementWarning] = list(match.warnings) if settings.vocal_level == 'match_original' else []
    seconds = vocal.duration_frames / vocal.sample_rate_hz
    transformed = await render_transform(DspTransformRequest(source=vocal.source, source_sha256=vocal.sha256,
        revision=revision, anchors=[WarpAnchor(source_sec=0.0, target_sec=0.0),
            WarpAnchor(source_sec=seconds, target_sec=seconds)], output_duration_sec=seconds,
        sample_rate_hz=48000, gain_db=gain, eq_bands=settings.eq_bands, compression=settings.compression), vocal, root, run)
    processed = ResolvedDspSource(root / transformed.artifact_name,
        DspArtifactRef(id=transformed.id, revision=revision, sha256=transformed.audio_sha256), transformed.audio_sha256, transformed.sample_rate_hz,
        transformed.channels, transformed.duration_frames, transformed.output_metrics)
    convolution: ReplacementIrResult | None = None
    if settings.space == 'owned_ir' and impulse is not None:
        convolution, processed = await convolve_owned_ir(processed, impulse, settings.wet, root, run)
    duration = original.duration_frames / original.sample_rate_hz
    profile = settings.loudness
    if settings.master_level == 'match_original':
        original_metrics = await _metrics(original, root, run, 'original-level')
        level = original_metrics.integrated_lufs
        if duration < 3 or level is None or not -40 <= level <= -8:
            warnings.append('replacement_loudness_unavailable')
            profile = LoudnessSettings()
        else:
            profile = LoudnessSettings(profile='custom', integrated_lufs=level, true_peak_dbtp=-1.0)
    sources: dict[str, ResolvedDspSource] = {}
    inputs: list[DspMixInput] = []
    for item in (processed, backing):
        identifier = uuid.uuid4().hex
        sources[identifier] = item
        inputs.append(DspMixInput(id=identifier, source=item.source, source_sha256=item.sha256,
            duration_sec=min(duration, item.duration_frames / item.sample_rate_hz)))
    channels: Literal[1, 2] = 1 if original.channels == 1 else 2
    master = await render_mix(DspMixRequest(revision=revision, duration_sec=duration,
        sample_rate_hz=48000, channels=channels, inputs=inputs, loudness=profile), sources, root, run)
    warnings.extend(transformed.warnings)
    warnings.extend(master.warnings)
    return ReplacementMix(transformed, master, gain,
        match.residual_lu if settings.vocal_level == 'match_original' else None,
        tuple(sorted(set(warnings))), processed, convolution)
