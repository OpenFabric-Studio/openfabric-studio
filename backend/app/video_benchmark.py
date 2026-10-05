"""Offline benchmark inventory and explicit exclusive-session inference harness."""
from __future__ import annotations
import asyncio
import os
import platform
import sys
import socket
import subprocess
import time
from pathlib import Path
from .video_contracts import VideoContract
from .video_engine import ENGINE_COMMIT, ENGINE_VERSION, EngineReadiness, ProfileId, RenderSettings, ImageReference, inspect_readiness, render_argv
from .video_process import spawn_owned
from .video_media import VideoMediaError, validate_media
from .job_lifecycle import await_cleanup, kill_process_tree


class VideoBenchmarkReport(VideoContract):
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


def peak_child_rss() -> int | None:
    # resource is unavailable on Windows; importing it at module load would
    # break even read-only inventory there. This is POSIX process RSS, not MLX
    # unified-memory allocation, and includes children from this CLI session.
    if sys.platform == 'win32':
        return None
    import resource
    rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return int(rss if platform.system() == 'Darwin' else rss * 1024)


def inventory(engine: Path, cache: Path, profile: ProfileId) -> VideoBenchmarkReport:
    machine, memory = platform.machine(), None
    if platform.system() == 'Darwin':
        try:
            result=subprocess.run(['sysctl','-n','hw.model','hw.memsize'],capture_output=True,text=True,timeout=2,check=True)
            values=result.stdout.splitlines()
            if len(values)==2 and values[1].isdigit():
                machine, memory = values[0], int(values[1])
        except (OSError,subprocess.SubprocessError):
            pass
    return VideoBenchmarkReport(system=platform.system(),machine=machine,physical_memory_bytes=memory,
        readiness=inspect_readiness(engine,cache,profile))


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
    *, exclusive: bool, app_port: int, width: int, height: int, reference: Path | None = None) -> VideoBenchmarkReport:
    require_exclusive(exclusive,app_port)
    if not report.readiness.ready:
        return report.model_copy(update={'inference_skip_reason':'model_not_installed'})
    if output.resolve().is_relative_to(engine.resolve()) or output.resolve().is_relative_to(cache.resolve()):
        raise ValueError('benchmark_output_must_be_separate')
    output.mkdir(parents=True,exist_ok=True)
    clip=output/'baseline.mp4'
    if clip.exists():
        raise ValueError('benchmark_output_exists')
    settings=RenderSettings(output=clip,prompt='A simple geometric character turns slowly in daylight',frames=49,
        references=(ImageReference(reference),) if reference is not None else (),
        mode='i2v' if reference is not None else 't2v',profile_id=report.readiness.profile_id,
        width=width,height=height,stage1_steps=10,stage2_steps=3,seed=42)
    argv=render_argv(engine,cache,settings)
    env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','PYTHONDONTWRITEBYTECODE':'1',
        'HF_HOME':str(output/'hf'),'XDG_CACHE_HOME':str(output/'cache')}
    proc=None
    start=time.monotonic()
    result=report.model_copy(update={'inference_executed':True,'inference_skip_reason':''})
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
    return result
