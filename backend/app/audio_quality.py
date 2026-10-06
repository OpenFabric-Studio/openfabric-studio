"""Bounded CPU measurements executed by the caller's existing owned worker."""
from __future__ import annotations
import asyncio
import hashlib
import math
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from pydantic import BaseModel, Field, ValidationError
from .audio_quality_contracts import AudioMetrics, LoudnessSettings, TargetResult
from .job_lifecycle import await_cleanup
from .video_media import tool

RunCommand=Callable[[list[str],float],Awaitable[bytes]]
MAX_DECODED_BYTES=8*1024**3
INPUT=['-protocol_whitelist','file,pipe','-format_whitelist','wav,mp3,flac,ogg,mov']


class AudioQualityError(Exception):
    def __init__(self,code: str) -> None:
        self.code=code;super().__init__(code)


class LoudnormReport(BaseModel):
    input_i: str=Field(alias='input_i')
    input_tp: str
    input_lra: str
    input_thresh: str
    target_offset: str
    normalization_type: str=''


class Stream(BaseModel):
    sample_rate: str
    channels: int=Field(ge=1,le=32)


class Probe(BaseModel):
    streams: list[Stream]=Field(min_length=1,max_length=1)


@dataclass(frozen=True)
class Measurement:
    metrics: AudioMetrics
    report: LoudnormReport


def number(value: str) -> float | None:
    try:parsed=float(value)
    except ValueError:raise AudioQualityError('audio_measurement_failed') from None
    return parsed if math.isfinite(parsed) else None


def report(raw: bytes) -> LoudnormReport:
    text=raw.decode('utf-8',errors='replace')
    start=text.rfind('{');end=text.rfind('}')
    try:
        return LoudnormReport.model_validate_json(text[start:end+1])
    except (ValidationError,ValueError):
        raise AudioQualityError('audio_measurement_failed') from None


def _samples(path: Path) -> tuple[int,float,int,int]:
    count=0;peak=0.;full=0;near=0
    if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<MAX_DECODED_BYTES or path.stat().st_size%4:
        raise AudioQualityError('audio_measurement_failed')
    with path.open('rb') as source:
        while chunk:=source.read(65536):
            for (sample,) in struct.iter_unpack('<f',chunk):
                if not math.isfinite(sample):raise AudioQualityError('audio_measurement_failed')
                absolute=abs(sample);peak=max(peak,absolute);count+=1
                full+=absolute>=1-1/32768;near+=absolute>=.999
    return count,peak,full,near


async def measure(source: Path,root: Path,run: RunCommand,*,name: str='source') -> Measurement:
    # Preserve the decoded channel layout. Counting only a downmix can conceal
    # clipping in an individual original channel.
    raw_probe=await run([tool('ffprobe'),'-v','error',*INPUT,'-select_streams','a:0','-show_entries','stream=sample_rate,channels','-of','json',str(source)],60)
    try:
        stream=Probe.model_validate_json(raw_probe).streams[0];rate=int(stream.sample_rate)
        if not 8000<=rate<=384000:raise ValueError('unsupported rate')
    except (ValidationError,ValueError,IndexError):raise AudioQualityError('audio_measurement_failed') from None
    output=await run([tool('ffmpeg'),'-nostats','-nostdin','-v','info',*INPUT,'-i',str(source),'-map','0:a:0','-vn',
        '-af','loudnorm=I=-23:TP=-1:LRA=50:print_format=json','-f','null','-'],3600)
    measured=report(output)
    version=next((line[:200] for line in output.decode('utf-8',errors='replace').splitlines() if line.startswith('ffmpeg version ')),'unavailable')
    raw=root/f'{name}.analysis.f32';raw.unlink(missing_ok=True)
    complete=False
    try:
        await run([tool('ffmpeg'),'-v','error','-nostdin','-y',*INPUT,'-i',str(source),'-map','0:a:0','-vn','-c:a','pcm_f32le',
            '-f','f32le','-fs',str(MAX_DECODED_BYTES),str(raw)],3600)
        count,peak,full,near=await await_cleanup(asyncio.to_thread(_samples,raw))
        complete=True
    finally:
        # The owner retains incomplete files if a process drain could not be
        # verified. Removing a pathname is not cancellation of its writer.
        if complete:raw.unlink(missing_ok=True)
    seconds=count/stream.channels/rate
    def source_hash() -> str:
        checksum=hashlib.sha256()
        with source.open('rb') as handle:
            while block:=handle.read(1024*1024):checksum.update(block)
        return checksum.hexdigest()
    metrics=AudioMetrics(duration_sec=seconds,integrated_lufs=number(measured.input_i),loudness_range_lu=number(measured.input_lra),
        true_peak_dbtp=number(measured.input_tp),sample_peak_dbfs=20*math.log10(peak) if peak>0 else None,
        full_scale_fraction=full/count,near_full_scale_fraction=near/count,ffmpeg_version=version,
        source_sha256=await await_cleanup(asyncio.to_thread(source_hash)),samples_analyzed=count)
    if peak==0:metrics.warnings.append('silence')
    if full:metrics.warnings.append('full_scale_samples')
    if metrics.true_peak_dbtp is not None and metrics.true_peak_dbtp>0:metrics.warnings.append('true_peak_over')
    if seconds<3:metrics.warnings.append('short_programme')
    return Measurement(metrics,measured)


def normalization_filter(settings: LoudnessSettings,measurement: Measurement) -> str | None:
    target=settings.target
    values=[number(value) for value in (measurement.report.input_i,measurement.report.input_tp,measurement.report.input_lra,measurement.report.input_thresh,measurement.report.target_offset)]
    if target is None or any(value is None for value in values):return None
    integrated,peak=target
    # An explicit high LRA target preserves linear gain when possible. FFmpeg
    # reports a dynamic fallback when true-peak limiting is necessary.
    return (f'loudnorm=I={integrated}:TP={peak}:LRA=50:linear=true:print_format=json:'
        f'measured_I={measurement.report.input_i}:measured_TP={measurement.report.input_tp}:'
        f'measured_LRA={measurement.report.input_lra}:measured_thresh={measurement.report.input_thresh}:offset={measurement.report.target_offset}')


def target_result(settings: LoudnessSettings,metrics: AudioMetrics | None) -> TargetResult:
    target=settings.target
    if target is None:return 'off'
    if metrics is None or metrics.integrated_lufs is None or metrics.true_peak_dbtp is None:return 'inconclusive'
    integrated,peak=target
    return 'met' if abs(metrics.integrated_lufs-integrated)<=.5 and metrics.true_peak_dbtp<=peak+.1 else 'warning'
