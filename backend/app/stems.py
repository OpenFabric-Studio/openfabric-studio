"""On-demand stem separation (Demucs) for a saved track.

Unlike ACE-Step/YuE2, Demucs isn't a persistent HTTP server tracked in
MODELS/OrchestratorState - it's a one-shot CLI job. This module keeps its own
tiny in-memory job registry and drives the subprocess directly.

Runs alongside whatever model (if any) is currently active, rather than
stopping it first: measured peak VRAM for a real separation is only ~1GB
above baseline (htdemucs is a small model), which comfortably coexists with
ACE-Step/YuE2 on this card - no need to evict the active model for this.
The shared GPU lock serializes separation, voice training/conversion and video
generation. It remains independent of orchestrator.manager's model-switching
lock; owner labels describe the actual holder without changing that ordering.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional
from collections.abc import Awaitable, Callable, Mapping

from . import db
from .config import DEMUCS_DIR, FFMPEG_BIN_DIR, LOG_DIR
from .orchestrator.process import tail_log
from .job_lifecycle import await_cleanup, cancel_and_wait, kill_process_tree, request_cancel, spawn_process
from .gpu_lease import gpu_lease
from .voice_contracts import VoiceQueueReason

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

STEM_NAMES = ("vocals", "drums", "bass", "other")

_gpu_lock = asyncio.Lock()
# Voice training and song conversion take this same lock. It is not re-entrant:
# a caller that already holds it must not call separate_file().
gpu_lock = _gpu_lock

JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]
SpawnProcess = Callable[
    [list[str], Path, Mapping[str, str], int], Awaitable[asyncio.subprocess.Process]
]


@dataclass
class StemJob:
    status: JobStatus
    error: Optional[str] = None
    proc: Optional[asyncio.subprocess.Process] = None
    cancel_requested: bool = False
    task: asyncio.Task[None] | None = field(default=None, repr=False)


_jobs: dict[int, StemJob] = {}


async def start(track_id: int, *, force: bool = False) -> StemJob:
    job = _jobs.get(track_id)
    if job and job.status in ("queued", "running"):
        return job
    if job and job.status == "done" and not force:
        return job
    job = StemJob(status="queued")
    _jobs[track_id] = job
    job.task = asyncio.create_task(_run(track_id))
    return job


async def cancel(track_id: int) -> dict:
    job = _jobs.get(track_id)
    if job and job.status in ("queued", "running"):
        job.cancel_requested = True
        try:
            await cancel_and_wait(job.task)
        finally:
            await _kill_tree(job.proc)
            job.proc = None
            if job.status in ("queued", "running"):
                job.status = "cancelled"
                job.error = None
    return status(track_id)


async def shutdown() -> None:
    """Drain every separation task before application shutdown completes."""
    for job in _jobs.values():
        job.cancel_requested = True
        request_cancel(job.task)
    await await_cleanup(asyncio.gather(*(cancel(track_id) for track_id in list(_jobs))))


def is_active(track_id: int) -> bool:
    job = _jobs.get(track_id)
    return bool(job and job.status in ("queued", "running"))


def work_busy() -> bool:
    return any(job.status in ("queued", "running") for job in _jobs.values())


def _demucs_cmd(audio: Path, out_dir: Path, *, quality: Literal["fast", "high"] = "fast") -> list[str]:
    if quality not in ("fast", "high"):
        raise ValueError("invalid_separation_quality")
    model = "htdemucs_ft" if quality == "high" else "htdemucs"
    return ["uv", "run", "demucs", "-n", model, "--float32", "-o", str(out_dir), str(audio)]


def _demucs_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{FFMPEG_BIN_DIR}{os.pathsep}{env.get('PATH', '')}"
    env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    env.setdefault("DO_NOT_TRACK", "1")
    return env


async def separate_file(
    audio: Path,
    out_dir: Path,
    *,
    log_name: str,
    on_proc: Callable[[asyncio.subprocess.Process | None], None] | None = None,
    new_session: bool = False,
    quality: Literal["fast", "high"] = "fast",
    spawn: SpawnProcess | None = None,
    gpu_reason: VoiceQueueReason = 'stem_separation',
    gpu_label: str = '',
) -> dict[str, Path]:
    """Split audio while holding gpu_lock; a caller may own worker creation."""
    async with gpu_lease(gpu_lock, gpu_reason, gpu_label or audio.name):
        shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f"{log_name}.log"
        with open(log_path, "w", encoding="utf-8", errors="replace") as log_file:
            argv = _demucs_cmd(audio, out_dir, quality=quality)
            env = _demucs_env()
            if spawn is None:
                proc = await spawn_process(
                    *argv,
                    cwd=str(DEMUCS_DIR),
                    env=env,
                    stdout=log_file,
                    stderr=asyncio.subprocess.STDOUT,
                )
            else:
                proc = await spawn(argv, DEMUCS_DIR, env, log_file.fileno())
            if on_proc is not None:
                on_proc(proc)
            drained = False
            try:
                returncode = await proc.wait()
                drained = True
            except asyncio.CancelledError:
                await _kill_tree(proc)
                drained = True
                shutil.rmtree(out_dir, ignore_errors=True)
                raise
            finally:
                if drained and on_proc is not None:
                    on_proc(None)
        if returncode != 0:
            raise RuntimeError(f"demucs exited with code {returncode}\n{tail_log(log_name)}")
        stems = {
            wav.stem: wav
            for wav in out_dir.rglob("*.wav")
            if wav.stem in STEM_NAMES
        }
        if not stems:
            raise RuntimeError(f"demucs finished but produced no stem files\n{tail_log(log_name)}")
        return stems


def forget(track_id: int) -> None:
    """Drop any in-memory job record so status() falls back to the DB
    (used after deleting stems out from under a finished job)."""
    _jobs.pop(track_id, None)


async def _kill_tree(proc: asyncio.subprocess.Process | None) -> None:
    # `uv run demucs ...` spawns demucs as a child process, so plain
    # terminate()/kill() on the "uv" process alone leaves the actual
    # GPU computation running. Mirrors ManagedProcess._force_kill().
    await kill_process_tree(proc)


def status(track_id: int) -> dict:
    # A known job always wins over the DB: while a (re)run is in flight the
    # old stems_json may still point at files _run() is about to delete, so
    # trusting the DB here would misreport "done" for a cancelled/failed
    # redo. The DB is only consulted as a fallback once there's no job left
    # in memory (e.g. after a backend restart).
    job = _jobs.get(track_id)
    if job:
        return {"status": job.status, "error": job.error}
    row = db.get_track(track_id)
    if row and row["stems_json"]:
        return {"status": "done", "error": None}
    return {"status": "idle", "error": None}


async def _run(track_id: int) -> None:
    job = _jobs[track_id]
    log_name = f"demucs_{track_id}"
    out_dir: Path | None = None
    try:
        async with gpu_lease(_gpu_lock, 'stem_separation', f'Track {track_id}'):
            if job.cancel_requested:
                job.status = "cancelled"
                return
            job.status = "running"

            row = db.get_track(track_id)
            if not row:
                job.status = "failed"
                job.error = "track not found"
                return
            audio_path = Path(row["audio_path"])
            out_dir = db.stems_dir(row["model"], track_id)
            shutil.rmtree(out_dir, ignore_errors=True)
            out_dir.mkdir(parents=True, exist_ok=True)
            # Invalidate any previous run's stems_json now, since its files
            # were just deleted - the DB must never point at files that no
            # longer exist, even if this run itself fails or is cancelled.
            db.update_track_stems(track_id, None)

            LOG_DIR.mkdir(parents=True, exist_ok=True)
            log_path = LOG_DIR / f"{log_name}.log"

            with open(log_path, "w", encoding="utf-8", errors="replace") as log_file:
                proc = await spawn_process(
                    *_demucs_cmd(audio_path, out_dir),
                    cwd=str(DEMUCS_DIR),
                    env=_demucs_env(),
                    stdout=log_file,
                    stderr=asyncio.subprocess.STDOUT,
                )
                job.proc = proc
                returncode = await proc.wait()

            if job.cancel_requested:
                job.status = "cancelled"
                return
            if returncode != 0:
                job.status = "failed"
                logger.error("Demucs %s exited with code %s: %s", track_id, returncode, tail_log(log_name))
                job.error = "Stem separation failed. Check the backend log."
                return

            stems = {
                wav.stem: str(wav)
                for wav in out_dir.rglob("*.wav")
                if wav.stem in STEM_NAMES
            }
            if not stems:
                job.status = "failed"
                logger.error("Demucs %s produced no stems: %s", track_id, tail_log(log_name))
                job.error = "Stem separation produced no audio. Check the backend log."
                return
            db.update_track_stems(track_id, stems)
            job.status = "done"
    except asyncio.CancelledError:
        await _kill_tree(job.proc)
        job.status = "cancelled"
        job.error = None
        raise
    except Exception:
        logger.exception("Stem separation failed for track %s", track_id)
        job.status = "failed"
        job.error = "Stem separation failed. Check the backend log."
    finally:
        job.proc = None
        if out_dir is not None and job.status != "done":
            shutil.rmtree(out_dir, ignore_errors=True)
