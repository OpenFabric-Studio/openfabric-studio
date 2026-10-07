"""Offline benchmark inventory and explicit exclusive-session inference harness."""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import platform
import shutil
import sys
import socket
import subprocess
import time
from pathlib import Path
from dataclasses import replace
from typing import Literal
from .video_contracts import VideoContract
from .video_engine import ENGINE_COMMIT, ENGINE_VERSION, EngineReadiness, ProfileId, RenderSettings, ImageReference, inspect_readiness, render_argv
from .video_process import spawn_owned
from .video_media import VideoMediaError, validate_media
from .job_lifecycle import await_cleanup, kill_process_tree
from .video_candidate import CANDIDATE_COMMIT, CANDIDATE_VERSION, CANDIDATE_SHA256, CandidateSettings, candidate_argv, verify_candidate


class VideoBenchmarkReport(VideoContract):
    audio_sha256: str | None = None
    engine_commit: str = ENGINE_COMMIT
    engine_version: str = ENGINE_VERSION
    system: str
    machine: str
    physical_memory_bytes: int | None = None
    readiness: EngineReadiness
    inference_executed: bool = False
    inference_skip_reason: str = 'inventory_only'
    elapsed_sec: float | None = None
    peak_child_rss_bytes: int | None = None
    peak_mlx_memory_bytes: int | None = None
    output: str = ''
    output_validated: bool = False
    error_code: str = ''
    source_sha256: str | None = None
    memory_mode: Literal['low_ram', 'resident'] = 'low_ram'
    lora_mode: Literal['fused', 'unfused'] = 'fused'
    reference_sha256: str | None = None
    adapter_sha256: str | None = None
    output_sha256: str | None = None
    mlx_memory_scope: str = 'unavailable'


def peak_child_rss() -> int | None:
    # resource is unavailable on Windows; importing it at module load would
    # break even read-only inventory there. This is POSIX process RSS, not MLX
    # unified-memory allocation, and includes children from this CLI session.
    if sys.platform == 'win32':
        return None
    import resource
    rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return int(rss if platform.system() == 'Darwin' else rss * 1024)


def inventory(engine: Path, cache: Path, profile: ProfileId, *, candidate: bool = False) -> VideoBenchmarkReport:
    machine, memory = platform.machine(), None
    if platform.system() == 'Darwin':
        try:
            result=subprocess.run(['sysctl','-n','hw.model','hw.memsize'],capture_output=True,text=True,timeout=2,check=True)
            values=result.stdout.splitlines()
            if len(values)==2 and values[1].isdigit():
                machine, memory = values[0], int(values[1])
        except (OSError,subprocess.SubprocessError):
            pass
    ready = inspect_readiness(engine,cache,profile)
    report = VideoBenchmarkReport(system=platform.system(),machine=machine,physical_memory_bytes=memory, readiness=ready)
    if candidate:
        if profile != 'ltx23':
            raise ValueError('candidate_profile_not_reviewed')
        verify_candidate(engine)
        engine_ready = (engine / '.venv/bin/python').is_file()
        report.engine_commit, report.engine_version = CANDIDATE_COMMIT, CANDIDATE_VERSION
        report.source_sha256 = CANDIDATE_SHA256
        report.readiness = replace(ready, engine_ready=engine_ready, ready=engine_ready and not ready.missing_files,
            model_fingerprint=hashlib.sha256((CANDIDATE_COMMIT + ready.model_fingerprint).encode()).hexdigest(),
            warnings=tuple(item for item in ready.warnings if item != 'engine_incompatible') + ('experimental_candidate',))
    return report


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_input(source: Path, destination: Path) -> Path:
    before = file_sha256(source)
    # Exclusive creation prevents an old/symlinked staged input being overwritten.
    with source.open('rb') as incoming, destination.open('xb') as captured:
        shutil.copyfileobj(incoming, captured, 1024 * 1024)
    if file_sha256(destination) != before or file_sha256(source) != before:
        raise ValueError('benchmark_input_changed')
    return destination


def mlx_telemetry(path: Path) -> int | None:
    try:
        if path.is_symlink() or path.stat().st_size > 4096:
            return None
        payload: object = json.loads(path.read_text())
        if isinstance(payload, dict):
            value = payload.get('peak_mlx_memory_bytes')
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
    except (OSError, ValueError, UnicodeError):
        pass
    return None


def app_running(port: int) -> bool:
    if not 1 <= port <= 65535:
        raise ValueError('invalid_app_port')
    # A listening socket is enough to refuse; an unrelated service is not
    # permission to race the app's in-process admission lock.
    try:
        with socket.create_connection(('127.0.0.1',port),timeout=1):
            return True
    except OSError:
        return False


def require_exclusive(exclusive: bool, port: int) -> None:
    if not exclusive:
        raise ValueError('exclusive_session_required')
    if app_running(port):
        raise ValueError('running_app_present')


async def run_case(report: VideoBenchmarkReport, engine: Path, cache: Path, output: Path,
    *, exclusive: bool, app_port: int, width: int, height: int, reference: Path | None = None,
    candidate_settings: CandidateSettings | None = None) -> VideoBenchmarkReport:
    require_exclusive(exclusive,app_port)
    if not report.readiness.ready:
        return report.model_copy(update={'inference_skip_reason':'model_not_installed'})
    if output.is_symlink() or output.resolve().is_relative_to(engine.resolve()) or output.resolve().is_relative_to(cache.resolve()):
        raise ValueError('benchmark_output_must_be_separate')
    output.mkdir(parents=True,exist_ok=True)
    clip=output/'baseline.mp4'
    if any((output/name).exists() or (output/name).is_symlink() for name in ('baseline.mp4', 'worker.log', 'worker.json', 'telemetry.json')):
        raise ValueError('benchmark_output_exists')
    if candidate_settings is not None and (candidate_settings.width, candidate_settings.height, candidate_settings.reference) != (width, height, reference):
        raise ValueError('candidate_settings_mismatch')
    # Capture operator-selected inputs before spawning. The worker cannot read
    # a replacement take/reference while its report hashes the earlier file.
    if reference is not None:
        reference = snapshot_input(reference, output / ('reference' + reference.suffix))
    if candidate_settings is not None:
        candidate_settings = replace(candidate_settings, reference=reference)
        if candidate_settings.source_audio is not None:
            source_audio = candidate_settings.source_audio
            candidate_settings = replace(candidate_settings, source_audio=snapshot_input(source_audio, output / ('audio' + source_audio.suffix)))
        if candidate_settings.adapter is not None:
            adapter = candidate_settings.adapter
            staged_adapter = output / 'adapter.safetensors'
            candidate_settings = replace(candidate_settings, adapter=snapshot_input(adapter, staged_adapter))
    settings=RenderSettings(output=clip,prompt='A simple geometric character turns slowly in daylight',frames=49,
        references=(ImageReference(reference),) if reference is not None else (),
        mode='i2v' if reference is not None else 't2v',profile_id=report.readiness.profile_id,
        width=width,height=height,stage1_steps=10,stage2_steps=3,seed=42)
    if candidate_settings is not None:
        if report.engine_commit != CANDIDATE_COMMIT:
            raise ValueError('candidate_report_mismatch')
        argv=candidate_argv(engine,cache,output,candidate_settings)
    else:
        if report.engine_commit != ENGINE_COMMIT:
            raise ValueError('production_report_mismatch')
        argv=render_argv(engine,cache,settings)
        separator = argv.index('--')
        argv[separator:separator] = ['--telemetry', str(output/'telemetry.json')]
    env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','PYTHONDONTWRITEBYTECODE':'1',
        'HF_HUB_DISABLE_IMPLICIT_TOKEN':'1','HF_HOME':str(output/'hf'),'XDG_CACHE_HOME':str(output/'cache'),
        'LTX2_LORA_MODE':candidate_settings.lora_mode if candidate_settings is not None else 'fused'}
    proc=None
    start=time.monotonic()
    result=report.model_copy(update={'inference_executed':True,'inference_skip_reason':''})
    if reference is not None:
        result.reference_sha256 = file_sha256(reference)
    if candidate_settings is not None:
        result.memory_mode, result.lora_mode = candidate_settings.memory_mode, candidate_settings.lora_mode
        if candidate_settings.source_audio is not None:
            result.audio_sha256 = file_sha256(candidate_settings.source_audio)
        if candidate_settings.adapter is not None:
            result.adapter_sha256 = file_sha256(candidate_settings.adapter)
    try:
        with (output/'worker.log').open('wb') as log:
            # This standalone mode is only for an operator-declared exclusive
            # session. There is no cross-process GPU lease in the running app.
            proc=await spawn_owned(argv,receipt_path=output/'worker.json',stdout=log.fileno(),env=env)
            async with asyncio.timeout(1200):
                await proc.wait()
        if proc.returncode != 0:
            result.error_code='benchmark_worker_failed'
        else:
            await validate_media(clip,2,(width,height))
            result.output_validated=True
            result.output=str(clip)
            result.output_sha256=file_sha256(clip)
    except TimeoutError:
        result.error_code='benchmark_timeout'
    except VideoMediaError:
        result.error_code='benchmark_output_invalid'
    except OSError:
        result.error_code='benchmark_worker_unavailable'
    finally:
        if proc is not None:
            await await_cleanup(kill_process_tree(proc))
        result.elapsed_sec=time.monotonic()-start
        result.peak_child_rss_bytes=peak_child_rss()
        result.peak_mlx_memory_bytes=mlx_telemetry(output/'telemetry.json')
        if result.peak_mlx_memory_bytes is not None:
            result.mlx_memory_scope='allocator_peak_since_last_vendor_reset; not whole-process RSS'
    return result
