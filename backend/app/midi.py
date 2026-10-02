"""Audio -> MIDI transcription (MuScriptor) for a saved track or one of its stems.

Unlike Demucs, MuScriptor isn't a CLI of its own: it is loaded into YuE2's
already-running audiocpp_server and driven over that server's REST API. Two
consequences shape this module:

* YuE2 must be the *active* model, otherwise there is no server to talk to
  (the route checks this and refuses up front rather than queueing a job that
  cannot run).
* Cancellation retains exclusive server admission while an owned stop/reset
  drains inference. Transcription cannot overlap a generator model switch.

One job per (track, source) pair: the full mix and each Demucs stem are
transcribed independently, each constrained to the instrument groups that
source can plausibly contain, and each written to its own .mid.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import httpx

from . import db, native_yue
from .job_lifecycle import await_cleanup, cancel_and_wait, communicate_process, request_cancel, spawn_process

logger = logging.getLogger(__name__)
from .config import (
    FFMPEG_BIN_DIR,
    MODELS,
    MUSCRIPTOR_FAMILY,
    MUSCRIPTOR_MODEL_ID,
    MUSCRIPTOR_MODEL_PATH,
    MUSCRIPTOR_TASK,
)

IS_WINDOWS = sys.platform == "win32"

FULL_SOURCE = "full"
SOURCES = (FULL_SOURCE, "vocals", "drums", "bass", "other")

# Instrument groups MuScriptor may emit, by source. Names come from its
# tokenizer's instrument table; an empty string means "no constraint", which
# is what the full mix and Demucs' catch-all "other" stem need.
SOURCE_INSTRUMENTS: dict[str, str] = {
    FULL_SOURCE: "",
    "vocals": "voice",
    "drums": "drums",
    "bass": "electric_bass,acoustic_bass",
    "other": "",
}

JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]

# The transcription itself is a single long request; a read timeout would
# abandon a job that is still progressing fine.
_client = httpx.AsyncClient(timeout=None)

# Serializes transcriptions against each other so a "transcribe every stem"
# burst doesn't put four decodes on the GPU at once. It deliberately does not
# replace server admission: owned_session also excludes YuE generation and
# reference melody extraction through the same native process.
_gpu_lock = asyncio.Lock()


@dataclass
class MidiJob:
    status: JobStatus
    error: Optional[str] = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)


_jobs: dict[tuple[int, str], MidiJob] = {}


def _server_path(path: Path) -> str:
    # The native server's JSON parser mishandles backslash escapes in paths it
    # is handed back; forward slashes round-trip fine (same fix as the
    # frontend's upload helper).
    return str(path).replace("\\", "/")


def source_audio_path(track_id: int, source: str) -> Optional[Path]:
    row = db.get_track(track_id)
    if not row:
        return None
    if source == FULL_SOURCE:
        return Path(row["audio_path"])
    stems = json.loads(row["stems_json"]) if row["stems_json"] else {}
    path = stems.get(source)
    return Path(path) if path else None


async def _to_mono_wav(src: Path) -> Path:
    """MuScriptor reads a WAV container only, and the server's own upload path
    normalizes to mono/44.1k before handing audio to a model - do the same here
    so a track saved as mp3/flac (or a stereo stem) is accepted identically."""
    fd, out_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    env = os.environ.copy()
    env["PATH"] = f"{FFMPEG_BIN_DIR}{os.pathsep}{env.get('PATH', '')}"
    try:
        proc = await spawn_process(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src), "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", out_path,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        _, stderr = await communicate_process(proc)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed to convert audio to WAV: {stderr.decode('utf-8', 'replace').strip()[:500]}")
        return Path(out_path)
    except (Exception, asyncio.CancelledError):
        Path(out_path).unlink(missing_ok=True)
        raise


async def _ensure_model_loaded(base_url: str) -> None:
    if not MUSCRIPTOR_MODEL_PATH.exists():
        raise RuntimeError(
            f"MuScriptor weights not found at {MUSCRIPTOR_MODEL_PATH}. "
            "Install them with: python tools/model_manager_v2.py install muscriptor_small_f32"
        )
    listed = await _client.get(f"{base_url}/v1/models")
    listed.raise_for_status()
    for entry in listed.json().get("data") or []:
        if entry.get("id") == MUSCRIPTOR_MODEL_ID and entry.get("loaded"):
            return
    resp = await _client.post(
        f"{base_url}/v1/models/load",
        json={
            "id": MUSCRIPTOR_MODEL_ID,
            "path": _server_path(MUSCRIPTOR_MODEL_PATH),
            "family": MUSCRIPTOR_FAMILY,
            "task": MUSCRIPTOR_TASK,
            "mode": "offline",
            "load_options": {},
            "session_options": {},
        },
    )
    resp.raise_for_status()


def _midi_bytes_from_result(result: dict) -> Optional[bytes]:
    for artifact in result.get("artifacts") or []:
        meta = artifact.get("meta") or {}
        if artifact.get("kind") == "midi" or meta.get("format") == "midi":
            payload = artifact.get("payload")
            if isinstance(payload, str):
                return base64.b64decode(payload)
    return None


def status(track_id: int, source: str) -> dict:
    # A live job always wins over the DB: while a re-run is in flight the old
    # .mid is still on disk, and reporting it as "done" would hide the rerun.
    job = _jobs.get((track_id, source))
    if job:
        return {"status": job.status, "error": job.error}
    if source in db.get_track_midi(track_id):
        return {"status": "done", "error": None}
    return {"status": "idle", "error": None}


def status_all(track_id: int) -> dict:
    return {s: status(track_id, s) for s in SOURCES}


def is_active(track_id: int, source: str) -> bool:
    job = _jobs.get((track_id, source))
    return bool(job and job.status in ("queued", "running"))


def any_active(track_id: int) -> bool:
    return any(is_active(track_id, s) for s in SOURCES)


def work_busy() -> bool:
    return any(job.status in ("queued", "running") for job in _jobs.values())


def forget(track_id: int) -> None:
    for s in SOURCES:
        _jobs.pop((track_id, s), None)


async def start(track_id: int, source: str, *, force: bool = False) -> MidiJob:
    key = (track_id, source)
    job = _jobs.get(key)
    if job and job.status in ("queued", "running"):
        return job
    if job and job.status == "done" and not force:
        return job
    job = MidiJob(status="queued")
    _jobs[key] = job
    job.task = asyncio.create_task(_run(track_id, source))
    return job


async def cancel(track_id: int, source: str) -> dict:
    job = _jobs.get((track_id, source))
    if job and job.status in ("queued", "running") and job.task is not None:
        try:
            await cancel_and_wait(job.task)
        finally:
            if job.status in ("queued", "running"):
                job.status = "cancelled"
                job.error = None
    return status(track_id, source)


async def shutdown() -> None:
    """Drain local transcription/preprocessing tasks, then close HTTP transport."""
    for job in _jobs.values():
        request_cancel(job.task)
    try:
        await await_cleanup(asyncio.gather(*(cancel(track_id, source) for track_id, source in list(_jobs))))
    finally:
        await _client.aclose()


async def _run(track_id: int, source: str) -> None:
    job = _jobs[(track_id, source)]
    wav_path: Optional[Path] = None
    try:
        async with _gpu_lock:
            job.status = "running"
            row = db.get_track(track_id)
            if not row:
                job.status = "failed"
                job.error = "track not found"
                return
            audio_path = source_audio_path(track_id, source)
            if not audio_path or not audio_path.exists():
                job.status = "failed"
                job.error = f"audio for source '{source}' not found"
                return

            base_url = MODELS["yue2"].proxy_target
            wav_path = await _to_mono_wav(audio_path)
            async with native_yue.owned_session() as session:
                session.mutating = True
                await _ensure_model_loaded(base_url)

                resp = await _client.post(
                    f"{base_url}/v1/tasks/run",
                    json={
                        "model": MUSCRIPTOR_MODEL_ID,
                        "request": {
                            "audio": _server_path(wav_path),
                            "options": {
                                "instruments": SOURCE_INSTRUMENTS[source],
                                "output_format": "midi",
                                # MuScriptor transcribes in 5s chunks. With the
                                # server default (prelude_forcing=true) every chunk
                                # after the first is forced to open with the notes
                                # the previous one left sounding, and once a chunk
                                # comes out empty the next one is pushed straight to
                                # EOS - so a quiet intro silences everything after
                                # it (a psytrance track here stayed empty until
                                # 1:50, and its 0-60s slice transcribed fine on its
                                # own). Letting the model predict its own tie
                                # section costs a few notes clipped at chunk seams
                                # and roughly doubles what gets transcribed.
                                "prelude_forcing": "false",
                            },
                        },
                    },
                )
                resp.raise_for_status()
                session.mutating = False
            midi_bytes = _midi_bytes_from_result(resp.json())
            if not midi_bytes:
                job.status = "failed"
                job.error = "MuScriptor returned no MIDI artifact"
                return

            out_dir = db.midi_dir(row["model"], track_id)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{source}.mid"
            out_path.write_bytes(midi_bytes)
            db.set_track_midi_entry(track_id, source, out_path)
            job.status = "done"
            job.error = None
    except asyncio.CancelledError:
        job.status = "cancelled"
        raise
    except httpx.HTTPStatusError as exc:
        logger.exception("MIDI transcription server failed for track %s source %s", track_id, source)
        job.status = "failed"
        job.error = f"MIDI transcription server returned HTTP {exc.response.status_code}. Check the backend log."
    except Exception:
        logger.exception("MIDI transcription failed for track %s source %s", track_id, source)
        job.status = "failed"
        job.error = "MIDI transcription failed. Check the backend log."
    finally:
        if wav_path is not None:
            wav_path.unlink(missing_ok=True)
