"""Build a singing voice from uploaded songs, then apply it to a new track.

Extract pulls the vocal out of each file with Demucs. Merge joins those vocals
and slices them into the 1–30s clips Seed-VC will actually train on. Train
fine-tunes the singing model. Apply separates a finished song and converts its
vocal with the trained singing model, using this voice's clearest singing
as the reference, then mixes that vocal back with the instruments.

The converter keeps one 30 second context for the reference and the song
together. A long reference leaves only a few seconds of the song in each
pass, so a full track is stitched from many short pieces. Ten seconds of the
clearest singing leaves about twenty seconds of the song in each pass.
Pitch stays with the song. Shifting it onto the reference clip's average
moves the vocal out of tune with the instruments. Seed-VC leaves that shift
off for singing.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import logging
import os
import re
import shutil
import sqlite3
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol, TYPE_CHECKING

from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError

from . import db
from .atomic_files import JsonObject, document_lock, read_object, write_object
from .job_lifecycle import await_cleanup, cancel_and_wait, kill_process_tree, request_cancel
from .video_process import run_owned_command
from .config import DATA_DIR, FFMPEG_BIN_DIR, LOG_DIR as LOG_DIR, SEED_VC_DIR as SEED_VC_DIR
from .data_root import place_seed_models
from .orchestrator.process import tail_log
from .stems import gpu_lock, separate_file
from .gpu_lease import gpu_lease, gpu_owner
from .voice_apply_progress import ApplyLogReader, apply_event
from .voice_artifacts import ModelArtifact, StoredModel, load_registry, choices, publish, activate
from .voice_artifacts import resolve_model_artifact as resolve_registered_model
from .voice_preparation import PreparationDocument
from .voice_progress import VoiceProgressTracker, read_progress, finish_progress
from .artist_contracts import ArtistConsentVersion
from .voice_mix_contracts import VoiceMixReport, VoiceMixSettings
from .voice_mix import PinnedConversion
from . import voice_authorization
if TYPE_CHECKING:
    from .client_contracts import VoiceProfileResponse
    from .voice_contracts import VoiceTrialMetrics

logger = logging.getLogger(__name__)

ALLOWED_AUDIO_EXT = {"wav", "mp3", "flac", "ogg", "opus", "m4a"}
VOICES_DIR = DATA_DIR / "voices"
_ID = re.compile(r"^[0-9a-f]{32}$")
_ACTIVE = {"queued", "extracting", "cleaning", "preparing", "merging", "training"}
_APPLY_ACTIVE = {"queued", "running"}
_apply_cancelling: dict[int, int] = {}
_apply_deleting: set[int] = set()

# Seed-VC documents 100 steps as the minimum that improves a speaker.
# 200 stays in that few-shot range without a 1000-step run on Apple Silicon.
TRAIN_STEPS = 200
CLIP_SECONDS = 20
QUIET_DB = -42.0
PREP_VERSION = "1"
DIFFUSION_STEPS = 30
# The singing model hears the reference and the song in one 30s window.
# Ten seconds of reference leaves about twenty seconds of song per pass.
REFERENCE_SECONDS = 10.0
SINGING_CONFIG = "configs/presets/config_dit_mel_seed_uvit_whisper_base_f0_44k.yml"
_WHISPER_FP16 = "WhisperModel.from_pretrained(whisper_name, torch_dtype=torch.float16).to(device)"
_WHISPER_SAFE = (
    'WhisperModel.from_pretrained(whisper_name, torch_dtype=torch.float16 if device.type == "cuda" else torch.float32).to(device)'
)
_MEAN_VOLUME = re.compile(r"mean_volume:\s*(-inf|-?\d+(?:\.\d+)?)\s*dB")
_TRAIN_STEP = re.compile(r"step (\d+), loss: ([0-9eE.+\-]+)")
_BAR = re.compile(r"(\d+)\s*/\s*(\d+)")
_FRACTION = re.compile(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)")
_PRETRAINED_NAME = "DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema.pth"
TARGET_VERSION = "2"
_PUBLIC = (
    "id",
    "name",
    "created_at",
    "recordings",
    "status",
    "stage",
    "detail",
    "progress_current",
    "progress_total",
    "error",
    "error_code",
    "trained_steps",
    "built_from",
    "has_preview",
    "usable",
    "clean_vocals",
    "prepared",
    "prep_kept_sec",
    "prep_total_sec",
    "extract_report",
    "job_progress",
)


class BuildCancelled(Exception):
    pass


class VoiceBuildError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


class VoiceProcessSlot(Protocol):
    proc: asyncio.subprocess.Process | None

    def track(self, proc: asyncio.subprocess.Process | None) -> None: ...


@dataclass
class ProcSlot:
    proc: asyncio.subprocess.Process | None = None

    def track(self, proc: asyncio.subprocess.Process | None) -> None:
        self.proc = proc


@dataclass
class BuildJob:
    voice_id: str
    status: str = "queued"
    stage: str = "extracting"
    detail: str = ""
    progress_current: int = 0
    progress_total: int = 0
    error: str = ""
    error_code: str = ""
    cancel: bool = False
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    slot: VoiceProcessSlot = field(default_factory=ProcSlot)
    work_dir: Path | None = field(default=None, repr=False)
    built_from: list[str] = field(default_factory=list)
    checkpoint: str = ""
    config_path: str = ""
    reference: str = ""
    trained_steps: int = 0
    log_name: str = ""
    clean: bool = False
    prep_saved: bool = False
    prep_kept_sec: float = 0.0
    prep_total_sec: float = 0.0
    extract_report: dict | None = None
    file_percent: int = -1
    preparation: PreparationDocument | None = None
    training_steps: Literal[0, 200, 500, 1000] = 200
    resume: bool = False
    compare_checkpoints: bool = False
    models: list[StoredModel] = field(default_factory=list)
    timing: VoiceProgressTracker | None = None
    authorization: ArtistConsentVersion | None = None
    usage_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class ApplyJob:
    voice_id: str
    track_id: int
    status: str = "queued"
    error: str = ""
    error_code: str = ""
    audio_url: str = ""
    phase: str = ""
    started_at: float = 0.0
    duration_sec: float = 0.0
    cancel: bool = False
    cleanup_pending: bool = False
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    slot: VoiceProcessSlot = field(default_factory=ProcSlot)
    work_dir: Path | None = field(default=None, repr=False)
    partial_outputs: set[Path] = field(default_factory=set, repr=False)
    audio_version_id: str | None = None
    timing: VoiceProgressTracker = field(default_factory=lambda: VoiceProgressTracker.create('apply', ''))
    production_run_id: str | None = None
    production_vocal_asset_id: str | None = None
    authorization: ArtistConsentVersion | None = None
    model_artifact: ModelArtifact | None = None
    mix_settings: VoiceMixSettings | None = None
    mix_report: VoiceMixReport | None = None
    pinned_conversion: PinnedConversion | None = None
    usage_id: str = field(default_factory=lambda: uuid.uuid4().hex)


_builds: dict[str, BuildJob] = {}
_applies: dict[int, ApplyJob] = {}


def parse_mean_volume(text: str) -> float | None:
    found = _MEAN_VOLUME.findall(text)
    if not found:
        return None
    raw = found[-1]
    if raw == "-inf":
        return float("-inf")
    return float(raw)


def parse_train_progress(text: str) -> int | None:
    """Global training step, including batches that only appear inside the bar."""
    step: int | None = None
    batch_at_step: int | None = None
    total_at_step: int | None = None
    last_batch: int | None = None
    last_total: int | None = None
    wrapped = False
    for raw in text.replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        found = _TRAIN_STEP.search(line)
        bar = _BAR.search(line)
        if found:
            step = int(found.group(1))
            if bar:
                batch_at_step = int(bar.group(1))
                total_at_step = int(bar.group(2))
            else:
                batch_at_step = last_batch
                total_at_step = last_total
            last_batch = batch_at_step
            last_total = total_at_step
            wrapped = False
            continue
        if not bar:
            continue
        batch = int(bar.group(1))
        total = int(bar.group(2))
        if total <= 0:
            continue
        if last_batch is not None and batch < last_batch:
            wrapped = True
        last_batch = batch
        last_total = total
    if step is None:
        return None
    if last_batch is None or batch_at_step is None:
        return step
    if wrapped and total_at_step:
        extra = (total_at_step - batch_at_step) + last_batch
    else:
        extra = last_batch - batch_at_step
    if extra < 0:
        extra = 0
    return step + extra


def parse_fraction_percent(text: str) -> int | None:
    """Last current/total ratio in a tool log, as a percent from 0 to 100."""
    found = _FRACTION.findall(text.replace("\r", "\n"))
    if not found:
        return None
    current, total = found[-1]
    total_f = float(total)
    if total_f <= 0:
        return None
    return max(0, min(100, int(float(current) / total_f * 100)))


@dataclass(frozen=True)
class ClipStat:
    name: str
    duration: float
    mean_db: float


def rank_reference_clips(clips: list[ClipStat]) -> list[ClipStat]:
    """Clearest singing first. The loudest clip is often the most clipped."""
    usable = [clip for clip in clips if clip.duration >= 4 and -36 <= clip.mean_db <= -6]
    pool = usable or [clip for clip in clips if clip.duration >= 1 and clip.mean_db > -80]
    return sorted(pool, key=lambda clip: (
        0 if -26 <= clip.mean_db <= -12 else 1,
        abs(clip.mean_db + 18),
        -clip.duration,
        clip.name,
    ))


def reference_trim_seconds(duration: float, limit: float = REFERENCE_SECONDS) -> float | None:
    """How much of a built reference to keep. None when it is already short enough."""
    try:
        seconds = float(duration)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds <= 0 or seconds <= limit + 0.05:
        return None
    return float(limit)


def choose_reference_clips(clips: list[ClipStat], *, limit_sec: float = REFERENCE_SECONDS) -> list[str]:
    """Clearest singing first, stopping once the converter has enough to hear."""
    ranked = rank_reference_clips(clips)
    chosen: list[ClipStat] = []
    total = 0.0
    for clip in ranked:
        if total >= limit_sec:
            break
        chosen.append(clip)
        total += clip.duration
    return [clip.name for clip in chosen]


_EXCEPTION_LINE = re.compile(r"^(?:[A-Za-z_][\w.]*)?(?:Error|Exception|Interrupt)\b")


def failure_summary(text: str) -> str:
    """The exception line from a tool log, skipping the warnings above it."""
    lines: list[str] = []
    for raw in text.replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line or "warnings.warn" in line or line.startswith("FutureWarning"):
            continue
        lines.append(line)
    for line in reversed(lines):
        head = line.split(":", 1)[0]
        if "Warning" in head:
            continue
        if _EXCEPTION_LINE.match(line):
            return line[:500]
    for line in reversed(lines):
        if line.startswith(("File ", "Traceback", "During handling")):
            continue
        if "Warning" in line:
            continue
        return line[:500]
    return "training failed"


def dataset_was_cleaned(path: Path) -> bool:
    marker = path / "dataset" / "clean.txt"
    try:
        return marker.read_text(encoding="utf-8").strip() == "1"
    except OSError:
        return False


def dataset_prep_version(path: Path) -> str:
    marker = path / "dataset" / "prep.txt"
    try:
        return marker.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def judge_extract(pitch: float, level_db: float, peak: float) -> tuple[str, list[str]]:
    """A short quality label for the vocal that training will use."""
    notes: list[str] = []
    if level_db < -28.0:
        notes.append("quiet")
    elif level_db > -6.0:
        notes.append("hot")
    if peak >= 0.98:
        notes.append("clipped")
    if pitch >= 0.50 and -26.0 <= level_db <= -8.0:
        quality = "clear"
    elif pitch >= 0.34 and level_db >= -36.0:
        quality = "usable"
    else:
        quality = "weak"
    if "quiet" in notes and quality == "clear":
        quality = "usable"
    return quality, notes


def _level_db(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return -120.0
    if number != number or number in (float("inf"), float("-inf")):
        return -120.0
    return number


def _count(value: object) -> int:
    if not isinstance(value, (str, int, float)):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def build_extract_report(
    prep: dict,
    *,
    songs: int,
    skipped: int,
    cleaned: bool,
    extracted_sec: float,
    trimmed: int,
) -> dict:
    pitch = _nonneg_float(prep.get("pitch"))
    peak = _nonneg_float(prep.get("peak"))
    level = _level_db(prep.get("level_db"))
    quality, notes = judge_extract(pitch, level, peak)
    kept_sec = prep.get("kept_sec")
    if kept_sec is None:
        kept_sec = prep.get("output_seconds")
    kept_pieces = prep.get("kept_pieces")
    if kept_pieces is None:
        kept_pieces = prep.get("kept")
    return {
        "songs": _count(songs),
        "skipped": _count(skipped),
        "cleaned": bool(cleaned),
        "gaps_shortened": True,
        "extracted_sec": round(_nonneg_float(extracted_sec), 3),
        "kept_sec": round(_nonneg_float(kept_sec), 3),
        "kept_pieces": _count(kept_pieces),
        "dropped": _count(prep.get("dropped")),
        "trimmed": _count(trimmed),
        "max_gap_sec": round(_nonneg_float(prep.get("max_gap_sec")), 3),
        "level_db": round(level, 1),
        "pitch": round(pitch, 3),
        "peak": round(peak, 3),
        "quality": quality,
        "notes": notes,
    }


def public_extract_report(raw) -> dict | None:
    if not isinstance(raw, dict) or raw.get("quality") not in {"clear", "usable", "weak"}:
        return None
    report = build_extract_report(
        raw,
        songs=_count(raw.get("songs")),
        skipped=_count(raw.get("skipped")),
        cleaned=bool(raw.get("cleaned")),
        extracted_sec=_nonneg_float(raw.get("extracted_sec")),
        trimmed=_count(raw.get("trimmed")),
    )
    report["gaps_shortened"] = bool(raw.get("gaps_shortened", True))
    report["quality"] = raw["quality"]
    report["notes"] = [item for item in raw.get("notes") or [] if item in {"quiet", "hot", "clipped"}]
    return report


def parse_prep_report(text: str) -> dict:
    """The last JSON line printed by prep_vocal.py."""
    found: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("{") or not line.endswith("}"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "input_seconds" in data:
            found = data
    return found


def vocals_ready(path: Path, sources: list[Path], *, clean: bool = False) -> bool:
    """True when extract and merge already produced clips for these recordings.

    A missing clean.txt means the clips were built before cleanup existed,
    which is the same as cleanup left off. A missing prep.txt means the clips
    were built before gap tightening, so the next build prepares them again.
    """
    merged = path / "voice.wav"
    reference = path / "reference.wav"
    clips = list((path / "dataset").glob("clip_*.wav"))
    if not merged.is_file() or not reference.is_file() or not clips or not sources:
        return False
    if dataset_was_cleaned(path) != clean:
        return False
    if dataset_prep_version(path) != PREP_VERSION:
        return False
    newest = max(src.stat().st_mtime for src in sources)
    return merged.stat().st_mtime >= newest and reference.stat().st_mtime >= newest


def last_log_line(text: str) -> str:
    lines = [line.strip() for line in text.replace("\r", "\n").splitlines() if line.strip()]
    if not lines:
        return ""
    return lines[-1][:300]


def concat_filter(count: int) -> str:
    parts = []
    labels = []
    for index in range(count):
        parts.append(f"[{index}:a]aformat=sample_fmts=fltp:channel_layouts=stereo,aresample=44100[a{index}]")
        labels.append(f"[a{index}]")
    parts.append(f"{''.join(labels)}concat=n={count}:v=0:a=1[out]")
    return ";".join(parts)


def remix_filter(count: int = 4) -> str:
    parts = []
    labels = []
    for index in range(count):
        parts.append(f"[{index}:a]aformat=sample_fmts=fltp:channel_layouts=stereo,aresample=44100[a{index}]")
        labels.append(f"[a{index}]")
    joined = "".join(labels)
    parts.append(f"{joined}amix=inputs={count}:normalize=0:duration=longest:dropout_transition=0[mix]")
    parts.append('[mix]alimiter=limit=0.99:level=false:latency=true[out]')
    return ";".join(parts)


def vocal_gain(source_level_db: float, converted_level_db: float) -> float:
    if not math.isfinite(source_level_db) or not math.isfinite(converted_level_db):
        raise VoiceBuildError('invalid_audio')
    difference = max(-12.0412, min(12.0412, source_level_db - converted_level_db))
    return max(.25, min(4.0, 10 ** (difference / 20)))


async def _measure_conversion(audio: Path, expected_seconds: float, job: ApplyJob, label: str) -> VoiceTrialMetrics:
    from .voice_contracts import VoiceTrialMetrics
    script = Path(__file__).with_name('voice_measure.py')
    log_name = f'voice_measure_apply_{job.track_id}_{label}'
    code = await _spawn([str(_engine_python()), str(script), str(audio), str(expected_seconds)], cwd=script.parent, log_name=log_name, slot=job.slot)
    if code != 0:
        raise VoiceBuildError('invalid_audio')
    for line in reversed(tail_log(log_name, lines=10).splitlines()):
        if line.strip().startswith('{'):
            return VoiceTrialMetrics.model_validate_json(line)
    raise VoiceBuildError('invalid_audio')


def reconcile_interrupted(meta: dict) -> dict | None:
    if meta.get("status") not in _ACTIVE:
        return None
    updated = dict(meta)
    updated["status"] = "failed"
    updated["error_code"] = "interrupted"
    updated["error"] = ""
    progress = read_progress(meta.get('job_progress'))
    finish_progress(progress, 'failed', now=progress.observed_at if progress else None)
    updated['job_progress'] = progress.model_dump(mode='json') if progress else None
    return updated


def public_view(meta: dict, *, has_preview: bool, usable: bool) -> dict:
    view = {key: meta.get(key) for key in _PUBLIC}
    view["status"] = meta.get("status") or "idle"
    view["stage"] = meta.get("stage") or ""
    view["detail"] = meta.get("detail") or ""
    view["progress_current"] = int(meta.get("progress_current") or 0)
    view["progress_total"] = int(meta.get("progress_total") or 0)
    view["error"] = meta.get("error") or ""
    view["error_code"] = meta.get("error_code") or ""
    view["trained_steps"] = int(meta.get("trained_steps") or 0)
    view["built_from"] = list(meta.get("built_from") or [])
    view["recordings"] = list(meta.get("recordings") or [])
    view["has_preview"] = has_preview
    view["usable"] = usable
    view["clean_vocals"] = bool(meta.get("clean_vocals"))
    view["prepared"] = str(meta.get("prep_version") or "") == PREP_VERSION
    view["prep_kept_sec"] = _nonneg_float(meta.get("prep_kept_sec"))
    view["prep_total_sec"] = _nonneg_float(meta.get("prep_total_sec"))
    view["extract_report"] = public_extract_report(meta.get("extract_report"))
    progress = read_progress(meta.get('job_progress'))
    view['job_progress'] = progress.model_dump(mode='json') if progress else None
    raw_percent = meta.get("file_percent")
    try:
        view["file_percent"] = None if raw_percent is None else max(0, min(100, int(raw_percent)))
    except (TypeError, ValueError):
        view["file_percent"] = None
    return view


def _nonneg_float(value) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if number < 0 or number != number or number == float("inf"):
        return 0.0
    return number


def voices_root() -> Path:
    if VOICES_DIR.is_symlink():
        raise HTTPException(status_code=409, detail='invalid_voice_storage')
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    return VOICES_DIR


def voice_dir(voice_id: str) -> Path:
    if not _ID.fullmatch(voice_id or ""):
        raise HTTPException(status_code=404, detail="Voice not found")
    path = voices_root() / voice_id
    if not path.resolve().is_relative_to(voices_root().resolve()):
        raise HTTPException(status_code=404, detail="Voice not found")
    if not path.is_dir() or not (path / "voice.json").is_file():
        raise HTTPException(status_code=404, detail="Voice not found")
    return path


def read_meta(path: Path) -> JsonObject:
    meta_path = path / "voice.json"
    if not meta_path.is_file():
        raise HTTPException(status_code=404, detail="Voice not found")
    with document_lock(meta_path):
        return read_object(meta_path)


def write_meta(path: Path, meta: JsonObject) -> None:
    write_object(path / "voice.json", meta)


def new_voice_meta(voice_id: str, name: str) -> dict:
    return {
        "id": voice_id,
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "recordings": [],
        "status": "idle",
        "stage": "",
        "detail": "",
        "progress_current": 0,
        "progress_total": 0,
        "error": "",
        "error_code": "",
        "trained_steps": 0,
        "built_from": [],
    }


def _contained(root: Path, raw: str | None, fallback: str) -> Path | None:
    candidate = Path(raw) if raw else root / fallback
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _artifact_flags(path: Path, meta: dict) -> tuple[bool, bool]:
    registry = load_registry(path)
    if registry.active_model_id:
        try:
            artifact = resolve_model_artifact(path, registry.active_model_id)
            row = next(item for item in registry.models if item.id == registry.active_model_id)
            from .voice_artifacts import contained_file
            return contained_file(path, row.preview).is_file(), artifact.reference.is_file()
        except (HTTPException, StopIteration):
            return False, False
    preview = (path / "voice.wav").is_file()
    checkpoint = _contained(path, meta.get("checkpoint"), "runs/model/ft_model.pth")
    reference = _contained(path, meta.get("reference"), "reference.wav")
    config = _contained(path, meta.get("config"), "config.yml")
    usable = bool(
        checkpoint and checkpoint.is_file()
        and reference and reference.is_file()
        and config and config.is_file()
    )
    return preview, usable


def resolve_model_artifact(voice_path: Path, model_id: str) -> ModelArtifact:
    if load_registry(voice_path).models:
        return resolve_registered_model(voice_path, model_id)
    if model_id not in {'trained', 'legacy'}:
        raise HTTPException(status_code=404, detail='invalid_model')
    meta = read_meta(voice_path)
    checkpoint_value, config_value, reference_value = meta.get('checkpoint'), meta.get('config'), meta.get('reference')
    checkpoint = _contained(voice_path, checkpoint_value if isinstance(checkpoint_value, str) else None, 'runs/model/ft_model.pth')
    config = _contained(voice_path, config_value if isinstance(config_value, str) else None, 'config.yml')
    reference = _contained(voice_path, reference_value if isinstance(reference_value, str) else None, 'reference.wav')
    if not (checkpoint and checkpoint.is_file() and config and config.is_file() and reference and reference.is_file()):
        raise HTTPException(status_code=404, detail='invalid_model')
    return ModelArtifact(checkpoint=checkpoint, config=config, reference=reference, steps=_count(meta.get('trained_steps')), kind='trained')


def resolve_reference_artifact(voice_path: Path, reference_id: str) -> Path:
    if reference_id == 'published':
        registry = load_registry(voice_path)
        return resolve_model_artifact(voice_path, registry.active_model_id or 'trained').reference
    from .voice_preparation import reference_path
    return reference_path(voice_path, reference_id)


def select_model(voice_id: str, model_id: str) -> VoiceProfileResponse:
    from .client_contracts import VoiceProfileResponse
    path = voice_dir(voice_id)
    activate(path, model_id)
    return VoiceProfileResponse.model_validate(public_from_path(path))


def _overlay_job(meta: dict, job: BuildJob) -> dict:
    updated = dict(meta)
    updated["status"] = job.status
    updated["stage"] = job.stage
    updated["detail"] = job.detail
    updated["progress_current"] = job.progress_current
    updated["progress_total"] = job.progress_total
    updated["error"] = job.error
    updated["error_code"] = job.error_code
    updated["clean_vocals"] = bool(job.clean)
    updated["file_percent"] = job.file_percent if job.file_percent >= 0 else None
    updated['job_progress'] = job.timing.progress.model_dump(mode='json') if job.timing else None
    if job.prep_saved:
        updated["prep_version"] = PREP_VERSION
        updated["prep_kept_sec"] = job.prep_kept_sec
        updated["prep_total_sec"] = job.prep_total_sec
        if job.extract_report:
            updated["extract_report"] = job.extract_report
    return updated


def public_from_path(path: Path) -> dict:
    with document_lock(path / "voice.json"):
        meta = read_meta(path)
        voice_id = str(meta.get("id") or path.name)
        job = _builds.get(voice_id)
        if job is not None:
            meta = _overlay_job(meta, job)
        else:
            fixed = reconcile_interrupted(meta)
            if fixed is not None:
                meta = fixed
                write_meta(path, meta)
        preview, usable = _artifact_flags(path, meta)
        view = public_view(meta, has_preview=preview, usable=usable)
        models, active_id = choices(path)
        if not models and usable:
            from .voice_contracts import VoiceModelChoice
            models = [VoiceModelChoice(id='trained', steps=_count(meta.get('trained_steps')), kind='trained')]
            active_id = 'trained'
        view['models'] = [item.model_dump(mode='json') for item in models]
        view['active_model_id'] = active_id
        try:
            permission = voice_authorization.authorize(voice_id, 'convert')
            view['use_status'] = 'allowed' if permission is not None else 'legacy_unreviewed'
            view['use_error_code'] = ''
        except HTTPException as exc:
            view['usable'] = False
            view['use_status'] = 'blocked'
            view['use_error_code'] = str(exc.detail)
        return view


def list_public() -> list[dict]:
    root = voices_root()
    folders = [
        path for path in root.iterdir()
        if path.is_dir() and _ID.fullmatch(path.name) and (path / "voice.json").is_file()
    ]
    folders.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return [public_from_path(path) for path in folders]


def public_voice(voice_id: str) -> dict:
    return public_from_path(voice_dir(voice_id))


def _tool(name: str) -> str:
    candidate = FFMPEG_BIN_DIR / (f"{name}.exe" if sys.platform == "win32" else name)
    if candidate.is_file():
        return str(candidate)
    found = shutil.which(name)
    if not found:
        raise VoiceBuildError("engine_missing", f"{name} was not found")
    return found


def _session_kwargs() -> dict:
    if sys.platform == "win32":
        return {}
    return {"start_new_session": True}


async def kill_proc(proc: asyncio.subprocess.Process | None) -> None:
    await kill_process_tree(proc)


async def spawn_apply_worker(argv: list[str], cwd: Path, env: Mapping[str, str], stdout: int) -> asyncio.subprocess.Process:
    """A voice worker dies with its backend, including hard crashes."""
    from .video_process import spawn_owned
    root = voices_root().resolve()
    directory = root / '_apply' / 'workers'
    if not directory.resolve().is_relative_to(root):
        raise VoiceBuildError('invalid_source')
    directory.mkdir(parents=True, exist_ok=True)
    return await spawn_owned(argv, receipt_path=directory / f'{uuid.uuid4().hex}.json', cwd=cwd, env=env, stdout=stdout)


async def _communicate_apply(proc: asyncio.subprocess.Process, timeout: float) -> bytes:
    """Drain bounded logs without closing the supervisor's parent-liveness pipe."""
    try:
        async with asyncio.timeout(timeout):
            if proc.stdout is None:
                raise VoiceBuildError('invalid_audio')
            chunks: list[bytes] = []
            total = 0
            while chunk := await proc.stdout.read(65536):
                total += len(chunk)
                if total > 16 * 1024 * 1024:
                    raise VoiceBuildError('invalid_audio')
                chunks.append(chunk)
            await proc.wait()
            return b''.join(chunks)
    finally:
        try:
            await await_cleanup(kill_proc(proc))
        finally:
            if proc.stdin is not None:
                proc.stdin.close()


async def _ffmpeg(args: list[str], slot: VoiceProcessSlot, cancelled: Callable[[], bool], timeout: float = 1800) -> str:
    if cancelled():
        raise BuildCancelled()
    proc = await spawn_apply_worker([_tool('ffmpeg'), *args], Path.cwd(), os.environ.copy(), asyncio.subprocess.PIPE)
    slot.track(proc)
    drained = False
    try:
        err = await _communicate_apply(proc, timeout)
        drained = True
    except TimeoutError:
        await kill_proc(proc)
        drained = True
        raise VoiceBuildError("", "ffmpeg timed out") from None
    except BaseException:
        await await_cleanup(kill_proc(proc))
        drained = True
        raise
    finally:
        if drained:
            slot.track(None)
    if cancelled():
        raise BuildCancelled()
    text = err.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        detail = text.strip()[-1500:] or f"ffmpeg exited with code {proc.returncode}"
        raise RuntimeError(detail)
    return text


async def _mix_command(job: ApplyJob, argv: list[str], timeout: float) -> bytes:
    """Use the apply owner's supervisor for every shared DSP command."""
    _check(job)
    proc = await spawn_apply_worker(argv, Path.cwd(), os.environ.copy(), asyncio.subprocess.PIPE)
    job.slot.track(proc)
    drained = False
    try:
        output = await _communicate_apply(proc, timeout)
        drained = True
    except BaseException:
        await await_cleanup(kill_proc(proc))
        drained = True
        raise
    finally:
        if drained:
            job.slot.track(None)
    _check(job)
    if proc.returncode != 0:
        logger.warning('Voice mix command failed: %s', output.decode('utf-8', errors='replace')[-1500:])
        raise VoiceBuildError('voice_mix_failed')
    return output


async def _probe_duration(path: Path) -> float:
    result = await run_owned_command([
        _tool("ffprobe"),
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ], timeout=30)
    if result.returncode != 0:
        return 0.0
    try:
        return float(result.stdout.decode().strip())
    except ValueError:
        return 0.0


async def merge_vocals(paths: list[Path], dest: Path, slot: VoiceProcessSlot, cancelled: Callable[[], bool]) -> None:
    if not paths:
        raise VoiceBuildError("no_vocal")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if len(paths) == 1:
        await _ffmpeg(["-y", "-i", str(paths[0]), "-ar", "44100", "-ac", "2", str(dest)], slot, cancelled)
        return
    args = ["-y"]
    for path in paths:
        args.extend(["-i", str(path)])
    args.extend(["-filter_complex", concat_filter(len(paths)), "-map", "[out]", str(dest)])
    await _ffmpeg(args, slot, cancelled)


async def slice_dataset(merged: Path, dataset_dir: Path, reference: Path, slot: VoiceProcessSlot, cancelled: Callable[[], bool]) -> int:
    if dataset_dir.exists():
        shutil.rmtree(dataset_dir)
    dataset_dir.mkdir(parents=True)
    await _ffmpeg(
        [
            "-y", "-i", str(merged),
            "-f", "segment", "-segment_time", str(CLIP_SECONDS),
            "-reset_timestamps", "1",
            "-ar", "44100", "-ac", "2",
            str(dataset_dir / "clip_%03d.wav"),
        ],
        slot,
        cancelled,
    )
    kept_stats: list[ClipStat] = []
    for clip in sorted(dataset_dir.glob("clip_*.wav")):
        if cancelled():
            raise BuildCancelled()
        duration = await _probe_duration(clip)
        stats = await _ffmpeg(["-i", str(clip), "-af", "volumedetect", "-f", "null", "-"], slot, cancelled, timeout=120)
        mean = parse_mean_volume(stats)
        if duration < 1 or duration > 30 or (mean is not None and mean < QUIET_DB):
            clip.unlink(missing_ok=True)
            continue
        kept_stats.append(ClipStat(clip.name, duration, mean if mean is not None else -40.0))
    chosen = choose_reference_clips(kept_stats)
    if not kept_stats or not chosen:
        raise VoiceBuildError("too_quiet")
    shutil.copyfile(dataset_dir / chosen[0], reference)
    return len(kept_stats)


def _engine_python() -> Path:
    folder = "Scripts" if sys.platform == "win32" else "bin"
    name = "python.exe" if sys.platform == "win32" else "python"
    return SEED_VC_DIR / ".venv" / folder / name


def ensure_whisper_float32_off_cuda() -> None:
    """float16 Whisper produces garbage on Apple Silicon. The CUDA default stays."""
    path = SEED_VC_DIR / "inference.py"
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    if _WHISPER_FP16 not in text:
        return
    path.write_text(text.replace(_WHISPER_FP16, _WHISPER_SAFE, 1), encoding="utf-8")


def _write_singing_config(voice_path: Path) -> Path:
    preset = SEED_VC_DIR / SINGING_CONFIG
    if not preset.is_file():
        raise VoiceBuildError("engine_missing")
    text = preset.read_text(encoding="utf-8")
    needle = 'log_dir: "./runs"'
    if needle not in text:
        raise VoiceBuildError("engine_missing")
    runs = (voice_path / "runs").as_posix()
    dest = voice_path / "config.yml"
    dest.write_text(text.replace(needle, f'log_dir: "{runs}"', 1), encoding="utf-8")
    return dest


_CACHE_ASSIGN = "os.environ['HF_HUB_CACHE'] = './checkpoints/hf_cache'"
_CACHE_DEFAULT = "os.environ.setdefault('HF_HUB_CACHE', './checkpoints/hf_cache')"


def ensure_seed_cache_dir() -> None:
    """Download singing models into the library folder instead of the engine checkout."""
    for name in ("train.py", "inference.py"):
        path = SEED_VC_DIR / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if _CACHE_ASSIGN in text:
            path.write_text(text.replace(_CACHE_ASSIGN, _CACHE_DEFAULT, 1), encoding="utf-8")
    hf_utils = SEED_VC_DIR / "hf_utils.py"
    if not hf_utils.is_file():
        return
    text = hf_utils.read_text(encoding="utf-8")
    old = (
        'os.makedirs("./checkpoints", exist_ok=True)\n'
        '    model_path = hf_hub_download(repo_id=repo_id, filename=model_filename, cache_dir="./checkpoints")'
    )
    new = (
        'cache_dir = os.environ.get("HF_HUB_CACHE") or "./checkpoints"\n'
        "    os.makedirs(cache_dir, exist_ok=True)\n"
        "    model_path = hf_hub_download(repo_id=repo_id, filename=model_filename, cache_dir=cache_dir)"
    )
    updated = text
    if old in updated:
        updated = updated.replace(old, new, 1)
    old_config = 'config_path = hf_hub_download(repo_id=repo_id, filename=config_filename, cache_dir="./checkpoints")'
    new_config = "config_path = hf_hub_download(repo_id=repo_id, filename=config_filename, cache_dir=cache_dir)"
    if old_config in updated:
        updated = updated.replace(old_config, new_config, 1)
    if updated != text:
        hf_utils.write_text(updated, encoding="utf-8")


def _failure_detail(log_name: str) -> str:
    return failure_summary(tail_log(log_name, lines=200))


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{FFMPEG_BIN_DIR}{os.pathsep}{env.get('PATH', '')}"
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["DO_NOT_TRACK"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["HF_HUB_CACHE"] = str(place_seed_models(DATA_DIR, SEED_VC_DIR))
    ensure_seed_cache_dir()
    return env


async def _spawn(cmd: list[str], *, cwd: Path, log_name: str, slot: VoiceProcessSlot, offline: bool = False) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{log_name}.log"
    handle = open(log_path, "w", encoding="utf-8", errors="replace")
    proc: asyncio.subprocess.Process | None = None
    drained = False
    try:
        env = _child_env()
        if offline:
            env.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'})
        proc = await spawn_apply_worker(cmd, cwd, env, handle.fileno())
        slot.track(proc)
        try:
            code = await proc.wait()
            await await_cleanup(kill_proc(proc))
            drained = True
            return code
        except asyncio.CancelledError:
            await await_cleanup(kill_proc(proc))
            drained = True
            raise
    finally:
        if proc is not None and proc.stdin is not None:
            proc.stdin.close()
        if proc is None or drained:
            slot.track(None)
        handle.close()


async def phase_safe_mono(source: Path, destination: Path, slot: VoiceProcessSlot) -> None:
    script = Path(__file__).with_name('prep_vocal.py')
    code = await _spawn([str(_engine_python()), str(script), '--mono', str(source), str(destination)],
        cwd=script.parent, log_name=f'voice_mono_{uuid.uuid4().hex}', slot=slot)
    if code != 0 or not destination.is_file():
        raise VoiceBuildError('invalid_audio')


def configured_base_filename() -> str:
    preset = SEED_VC_DIR / SINGING_CONFIG
    if not preset.is_file():
        raise VoiceBuildError('engine_missing')
    match = re.search(r'^pretrained_model:\s*[\"\']?([A-Za-z0-9_.-]+\.pth)[\"\']?\s*(?:#.*)?$', preset.read_text(encoding='utf-8'), flags=re.MULTILINE)
    if match is None:
        raise VoiceBuildError('engine_incompatible')
    return match.group(1)


async def _ensure_pretrained_base(directory: Path, job: BuildJob, filename: str) -> Path:
    destination = directory / 'base.pth'
    cached = pretrained_singing_checkpoint(filename)
    if cached is None and job.authorization is not None:
        raise VoiceBuildError('reviewed_baseline_required')
    if cached:
        shutil.copyfile(cached, destination)
    else:
        script = Path(__file__).with_name('voice_download.py')
        code = await _spawn([str(_engine_python()), str(script), filename, str(destination)], cwd=SEED_VC_DIR,
            log_name=f'voice_base_{job.voice_id}_{directory.name}', slot=job.slot)
        if code != 0:
            raise VoiceBuildError('base_unavailable')
    if not destination.is_file() or destination.stat().st_size == 0:
        raise VoiceBuildError('base_unavailable')
    return destination


def _check(job: BuildJob | ApplyJob) -> None:
    if job.cancel:
        raise BuildCancelled()


def _sync_build(job: BuildJob, *, finished_ok: bool = False) -> None:
    if job.timing is not None and job.status not in _ACTIVE:
        job.timing.finish('done' if job.status == 'ready' else 'cancelled' if job.status == 'cancelled' else 'failed')
    path = voices_root() / job.voice_id
    meta_path = path / "voice.json"
    if not meta_path.is_file():
        return
    with document_lock(meta_path):
        try:
            meta = read_object(meta_path)
            meta.update(_overlay_job(meta, job))
            if finished_ok:
                meta["checkpoint"] = job.checkpoint
                meta["config"] = job.config_path
                meta["reference"] = job.reference
                meta["built_from"] = job.built_from
                meta["trained_steps"] = job.trained_steps
            write_meta(path, meta)
        except (OSError, ValueError):
            return


def _pull_train_log(job: BuildJob) -> None:
    if job.status != "training":
        return
    log_path = LOG_DIR / f"{job.log_name}.log"
    if not log_path.is_file():
        return
    try:
        text = log_path.read_bytes()[-16000:].decode("utf-8", errors="replace")
    except OSError:
        return
    step = parse_train_progress(text)
    changed = False
    if step is not None and step != job.progress_current:
        job.progress_current = min(step, job.progress_total or step)
        if job.timing is not None:
            job.timing.advance(job.progress_current)
        changed = True
    if job.detail:
        job.detail = ""
        changed = True
    if changed:
        _sync_build(job)


async def _watch_fraction(job: BuildJob, log_name: str, stop: asyncio.Event) -> None:
    """Copy a tool's own percent into the build, without storing the bar text."""
    while not stop.is_set():
        path = LOG_DIR / f"{log_name}.log"
        percent = None
        if path.is_file():
            try:
                text = path.read_bytes()[-12000:].decode("utf-8", errors="replace")
            except OSError:
                text = ""
            percent = parse_fraction_percent(text)
        if percent is not None and percent != job.file_percent:
            job.file_percent = percent
            _sync_build(job)
        try:
            await asyncio.wait_for(stop.wait(), 2)
        except TimeoutError:
            pass


async def _watch_train(job: BuildJob, stop: asyncio.Event) -> None:
    while not stop.is_set():
        _pull_train_log(job)
        try:
            await asyncio.wait_for(stop.wait(), 2)
        except TimeoutError:
            pass
    _pull_train_log(job)


async def _build_inner(job: BuildJob) -> None:
    voice_authorization.recheck(job.voice_id, job.authorization, 'train')
    from . import voice_preparation as prep
    from .seed_vc_compat import ensure_compatibility, EngineCompatibilityError
    path = voice_dir(job.voice_id)
    if job.preparation is None:
        raise VoiceBuildError("preparation_required")
    # Never read the mutable current review after an awaited operation.
    # Selection/source changes during assembly are rejected before publication.
    document = job.preparation
    response = document.response
    identifier = uuid.uuid4().hex
    directory = path / "builds" / identifier
    directory.mkdir(parents=True)
    job.work_dir = directory
    # Keep the complete reviewed source/time selection with immutable models,
    # even when a later preparation replaces the current review document.
    prep.save(directory, document)
    job.status, job.stage = "merging", "merging"
    if job.timing is not None:
        job.timing.start()
        job.timing.phase('assembling', total=len(response.selected_segment_ids), unit='samples')
    _sync_build(job)
    def assembled(completed: int) -> None:
        if job.timing is not None:
            job.timing.advance(completed)
        _sync_build(job)
    def assembling_phase(phase: Literal['references', 'merging']) -> None:
        if job.timing is not None:
            job.timing.phase(phase, total=1, unit='tasks')
        _sync_build(job)
    reference, preview = await prep.assemble(path, document, directory, job.slot, lambda: job.cancel, assembled, assembling_phase)
    _check(job)
    prep.validated(path, response.revision)
    fingerprint = prep.selection_fingerprint(document)
    def relative(file: Path) -> str:
        return file.relative_to(path).as_posix()
    python = _engine_python()
    if not python.is_file():
        raise VoiceBuildError("engine_missing")
    try:
        ensure_compatibility(SEED_VC_DIR)
    except EngineCompatibilityError as exc:
        raise VoiceBuildError(exc.code) from exc
    ensure_whisper_float32_off_cuda()
    config_path = _write_singing_config(directory)
    base_filename = configured_base_filename()
    if job.timing is not None:
        job.timing.phase('base_model', total=1, unit='tasks')
    _sync_build(job)
    base = await _ensure_pretrained_base(directory, job, base_filename)
    with base.open('rb') as baseline:
        base_sha256 = hashlib.file_digest(baseline, 'sha256').hexdigest()
    signature = hashlib.sha256((SEED_VC_DIR / SINGING_CONFIG).read_bytes() + b'\0' + base_filename.encode() + b'\0' + base_sha256.encode()).hexdigest()
    job.checkpoint, job.config_path = str(base), str(config_path)
    models: list[StoredModel] = [StoredModel(id=f"{identifier}_base", steps=0, kind="base",
        checkpoint=relative(base), config=relative(config_path), reference=relative(reference), preview=relative(preview),
        preparation_revision=response.revision, selection_fingerprint=fingerprint, model_signature=signature, base_filename=base_filename, base_sha256=base_sha256)]
    if job.training_steps:
        resumed_steps = 0
        run = directory / "runs" / "model"
        command = [str(python), "train.py", "--config", str(config_path), "--dataset-dir", str(directory / "dataset"),
            "--run-name", "model", "--batch-size", "1", "--max-steps", str(job.training_steps),
            "--max-epochs", "1000", "--save-every", "100", "--num-workers", "0"]
        if job.resume:
            registry = load_registry(path)
            active = next((item for item in registry.models if item.id == registry.active_model_id), None)
            if active is None or not active.resume_path:
                raise VoiceBuildError("resume_unavailable")
            if active.preparation_revision != response.revision or active.selection_fingerprint != fingerprint or active.model_signature != signature or active.steps >= job.training_steps:
                raise VoiceBuildError("resume_mismatch")
            artifact = resolve_model_artifact(path, active.id)
            if artifact.resume_path is None:
                raise VoiceBuildError("resume_unavailable")
            run.mkdir(parents=True)
            shutil.copyfile(artifact.resume_path, run / "resume.pth")
            command.append("--resume")
            resumed_steps = active.steps
        else:
            command.extend(["--pretrained-ckpt", str(base)])
        job.status, job.stage = "training", "training"
        job.progress_current, job.progress_total = resumed_steps, job.training_steps
        if job.timing is not None:
            job.timing.phase('waiting_gpu', total=0, unit='tasks')
        job.log_name = f"voice_train_{job.voice_id}_{identifier}"
        _sync_build(job)
        stop = asyncio.Event()
        watcher = asyncio.create_task(_watch_train(job, stop))
        try:
            async with gpu_lease(gpu_lock, 'voice_training', _voice_name(job.voice_id)):
                _check(job)
                if job.timing is not None:
                    job.timing.phase('training', current=resumed_steps, total=job.training_steps, unit='steps')
                _sync_build(job)
                code = await _spawn(command, cwd=SEED_VC_DIR, log_name=job.log_name, slot=job.slot)
        finally:
            stop.set()
            await watcher
        _check(job)
        if code != 0:
            raise VoiceBuildError("train_failed")
        final = run / "ft_model.pth"
        resume = run / "resume.pth"
        if not final.is_file() or not resume.is_file():
            raise VoiceBuildError("no_checkpoint")
        steps = [200, 500, 1000] if job.compare_checkpoints else [job.training_steps]
        for step in steps:
            checkpoint = run / f"step_{step}.pth" if job.compare_checkpoints else final
            if not checkpoint.is_file():
                raise VoiceBuildError("no_checkpoint")
            full_state = checkpoint if job.compare_checkpoints else resume
            models.append(StoredModel(id=f"{identifier}_{step}", steps=step, kind="trained", checkpoint=relative(checkpoint),
                config=relative(config_path), reference=relative(reference), preview=relative(preview), resume_path=relative(full_state),
                preparation_revision=response.revision, selection_fingerprint=fingerprint, model_signature=signature, base_filename=base_filename, base_sha256=base_sha256))
        job.checkpoint, job.config_path = str(final), str(config_path)
    # The only externally visible publication happens after every artifact exists.
    # The prior registry and its immutable files survive every earlier failure.
    if job.timing is not None:
        job.timing.phase('publishing', total=1, unit='tasks')
    _sync_build(job)
    prep.validated(path, response.revision)
    _check(job)
    if not load_registry(path).models:
        try:
            previous = resolve_model_artifact(path, 'trained')
            if previous.checkpoint and previous.config:
                old_preview = path / 'voice.wav'
                models.insert(0, StoredModel(id='trained', steps=previous.steps, kind='trained',
                    checkpoint=relative(previous.checkpoint), config=relative(previous.config), reference=relative(previous.reference),
                    preview=relative(old_preview if old_preview.is_file() else previous.reference)))
        except HTTPException:
            pass
    voice_authorization.recheck(job.voice_id, job.authorization, 'train')
    publish(path, models, models[-1].id)
    if job.timing is not None:
        job.timing.advance(1)
    job.models = models
    job.reference = str(reference)
    job.built_from = [item.filename for item in response.options.sources if item.enabled]
    job.trained_steps = job.training_steps
    job.prep_saved, job.prep_kept_sec = True, response.accepted_seconds
    job.prep_total_sec = sum(item.duration_sec for item in response.sources)
    job.status, job.stage, job.error, job.error_code = "ready", "training" if job.training_steps else "merging", "", ""
    job.progress_current = job.training_steps
    job.work_dir = None
    _sync_build(job, finished_ok=True)


async def _run_build(job: BuildJob) -> None:
    try:
        await _build_inner(job)
    except asyncio.CancelledError:
        await kill_proc(job.slot.proc)
        job.status = "cancelled"
        job.error_code = "cancelled"
        job.error = ""
        _sync_build(job)
        raise
    except BuildCancelled:
        job.status = "cancelled"
        job.error_code = "cancelled"
        job.error = ""
        _sync_build(job)
    except HTTPException as exc:
        job.status = 'failed'
        codes = {'preparation_required', 'stale_preparation', 'source_changed', 'singer_unconfirmed', 'insufficient_usable_audio', 'invalid_reference', 'artifact_missing'}
        job.error_code = exc.detail if isinstance(exc.detail, str) and exc.detail in codes else 'unknown'
        job.error = ''
        _sync_build(job)
    except VoiceBuildError as exc:
        logger.warning("Voice job failed (%s): %s", exc.code, exc.detail, exc_info=True)
        if job.cancel:
            job.status = "cancelled"
            job.error_code = "cancelled"
            job.error = ""
        else:
            job.status = "failed"
            job.error_code = exc.code
            job.error = ""
        _sync_build(job)
    except Exception:
        logger.exception("Voice job failed")
        if job.cancel:
            job.status = "cancelled"
            job.error_code = "cancelled"
            job.error = ""
        else:
            job.status = "failed"
            job.error_code = "unknown"
            job.error = "Voice processing failed. Check the backend log."
        _sync_build(job)
    finally:
        if job.work_dir is not None:
            shutil.rmtree(job.work_dir, ignore_errors=True)
        if job.status in _ACTIVE:
            job.status = "failed"
            job.error_code = job.error_code or "interrupted"
            _sync_build(job)
        if _builds.get(job.voice_id) is job:
            _builds.pop(job.voice_id, None)
        voice_authorization.record(job.authorization, 'train', 'done' if job.status == 'ready' else 'cancelled' if job.status == 'cancelled' else 'failed',
            job.usage_id, voice_id=job.voice_id, error_code=job.error_code)


def start_build(voice_id: str, *, clean: bool = False, preparation_revision: str | None = None,
    training_steps: Literal[0, 200, 500, 1000] = 200, resume: bool = False, compare_checkpoints: bool = False) -> dict:
    authorization = voice_authorization.authorize(voice_id, 'train')
    from .replacement_jobs import require_voice_idle
    require_voice_idle(voice_id)
    from .resource_admission import require_setup_idle
    require_setup_idle()
    path = voice_dir(voice_id)
    current = _builds.get(voice_id)
    if current is not None and current.status in _ACTIVE:
        return public_from_path(path)
    meta = read_meta(path)
    if not meta.get("recordings"):
        raise HTTPException(status_code=400, detail="no_recordings")
    from .voice_preparation import validated, selection_fingerprint
    preparation = validated(path, preparation_revision)
    if resume and compare_checkpoints:
        raise HTTPException(status_code=422, detail='incompatible_build_options')
    if resume and training_steps == 0:
        raise HTTPException(status_code=409, detail='resume_unavailable')
    if training_steps not in (0, 200, 500, 1000):
        raise HTTPException(status_code=400, detail='invalid_training_steps')
    if resume:
        registry = load_registry(path)
        active = next((item for item in registry.models if item.id == registry.active_model_id), None)
        if active is None or not active.resume_path:
            raise HTTPException(status_code=409, detail='resume_unavailable')
        artifact = resolve_model_artifact(path, active.id)
        if artifact.resume_path is None:
            raise HTTPException(status_code=409, detail='resume_unavailable')
        if active.steps >= training_steps or active.preparation_revision != preparation.response.revision or active.selection_fingerprint != selection_fingerprint(preparation):
            raise HTTPException(status_code=409, detail='resume_mismatch')
    job = BuildJob(voice_id=voice_id, clean=preparation.response.options.clean,
        preparation=preparation, training_steps=1000 if compare_checkpoints else training_steps,
        resume=resume, compare_checkpoints=compare_checkpoints,
        timing=VoiceProgressTracker.create('build', preparation.response.revision))
    job.authorization = authorization
    voice_authorization.record(authorization, 'train', 'requested', job.usage_id, voice_id=voice_id)
    _builds[voice_id] = job
    _sync_build(job)
    job.task = asyncio.create_task(_run_build(job))
    return public_from_path(path)


async def _cancel_build_job(job: BuildJob) -> None:
    job.cancel = True
    try:
        await cancel_and_wait(job.task)
    finally:
        await kill_proc(job.slot.proc)
        job.slot.track(None)
        if job.status in _ACTIVE:
            job.status = "cancelled"
            job.error_code = "cancelled"
            job.error = ""
            _sync_build(job)
        if _builds.get(job.voice_id) is job:
            _builds.pop(job.voice_id, None)


async def cancel_build(voice_id: str) -> dict:
    path = voice_dir(voice_id)
    job = _builds.get(voice_id)
    if job is not None and job.status in _ACTIVE:
        await _cancel_build_job(job)
    return public_from_path(path)


def request_apply_cancellation(track_id: int) -> None:
    """Prevent an enqueued conversion starting before an awaited rollback."""
    job = _applies.get(track_id)
    if job is not None:
        job.cancel = True
        request_cancel(job.task)


async def cancel_apply(track_id: int) -> None:
    """Drain this track's conversion before its files may be removed."""
    job = _applies.get(track_id)
    if job is None:
        return
    recovering_cleanup = job.cleanup_pending and job.task is not None and job.task.done()
    request_apply_cancellation(track_id)
    _apply_cancelling[track_id] = _apply_cancelling.get(track_id, 0) + 1
    async def drain() -> None:
        failed = False
        try:
            if job.task is not None:
                outcomes = await asyncio.gather(job.task, return_exceptions=True)
                for outcome in outcomes:
                    if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
                        if not recovering_cleanup:
                            failed = True
                            logger.error('Voice application cleanup failed for track %s', track_id,
                                exc_info=(type(outcome), outcome, outcome.__traceback__))
        finally:
            try:
                await kill_proc(job.slot.proc)
                job.slot.track(None)
                had_pending_cleanup = job.cleanup_pending
                job.cleanup_pending = False
                _cleanup_apply_files(job)
                if had_pending_cleanup or job.status in _APPLY_ACTIVE:
                    job.status = "cancelled"
                    job.error_code = "cancelled"
                    job.error = ""
                    _write_apply(job)
            except Exception:
                failed = True
                job.cleanup_pending = job.slot.proc is not None
                logger.exception('Voice application cleanup failed for track %s', track_id)
            finally:
                if not job.cleanup_pending and _applies.get(track_id) is job:
                    _applies.pop(track_id, None)
        if failed:
            raise HTTPException(status_code=409, detail='apply_cleanup_failed')
    try:
        await await_cleanup(drain())
    finally:
        remaining = _apply_cancelling[track_id] - 1
        if remaining:
            _apply_cancelling[track_id] = remaining
        else:
            _apply_cancelling.pop(track_id, None)


@asynccontextmanager
async def protect_track_removal(track_id: int) -> AsyncIterator[None]:
    """Block new applications until the drained track deletion completes."""
    if track_id in _apply_deleting:
        raise HTTPException(status_code=409, detail='apply_busy')
    _apply_deleting.add(track_id)
    try:
        await cancel_apply(track_id)
        yield
    finally:
        _apply_deleting.discard(track_id)


def require_ready_voice(voice_id: str) -> None:
    """Replacement uploads require a ready voice with contained usable artifacts."""
    try:
        path = voice_dir(voice_id)
    except HTTPException as exc:
        raise HTTPException(status_code=404, detail='voice_missing') from exc
    meta = read_meta(path)
    _preview, usable = _artifact_flags(path, meta)
    build = _builds.get(voice_id)
    if not usable or meta.get('status') != 'ready' or build is not None and build.status in _ACTIVE:
        raise HTTPException(status_code=409, detail='not_ready')


async def release_voice(voice_id: str) -> None:
    from .replacement_jobs import cancel_for_voice
    await cancel_for_voice(voice_id)
    from .voice_preparation import cancel, request_cancellation
    request_cancellation(voice_dir(voice_id))
    job = _builds.get(voice_id)
    applies = [job for job in _applies.values() if job.voice_id == voice_id]
    if job is not None:
        job.cancel = True
        request_cancel(job.task)
    for apply in applies:
        apply.cancel = True
        request_cancel(apply.task)
    outcomes = await await_cleanup(asyncio.gather(
        cancel(voice_dir(voice_id)),
        *([_cancel_build_job(job)] if job is not None else []),
        *(cancel_apply(job.track_id) for job in applies),
        return_exceptions=True,
    ))
    failed = False
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            failed = True
            logger.error('Voice cleanup failed for %s', voice_id, exc_info=(type(outcome), outcome, outcome.__traceback__))
    if failed:
        raise HTTPException(status_code=409, detail='voice_cleanup_failed')


async def shutdown() -> None:
    """Cancel and await all voice build/conversion tasks and their children."""
    from .voice_preparation import request_cancellation, shutdown as shutdown_preparation
    request_cancellation()
    builds = list(_builds.values())
    applies = list(_applies.values())
    for job in [*builds, *applies]:
        job.cancel = True
        request_cancel(job.task)
    outcomes = await await_cleanup(asyncio.gather(
        shutdown_preparation(),
        *(_cancel_build_job(job) for job in builds),
        *(cancel_apply(job.track_id) for job in applies),
        return_exceptions=True,
    ))
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            logger.error('Voice shutdown cleanup failed', exc_info=(type(outcome), outcome, outcome.__traceback__))


def preview_path(voice_id: str) -> Path | None:
    path = voice_dir(voice_id)
    registry = load_registry(path)
    active = next((item for item in registry.models if item.id == registry.active_model_id), None)
    if active:
        from .voice_artifacts import contained_file
        return contained_file(path, active.preview)
    wav = path / "voice.wav"
    return wav if wav.is_file() else None


def _apply_file(track_id: int) -> Path:
    directory = voices_root() / "_apply"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{int(track_id)}.json"


def _apply_payload(job: ApplyJob) -> dict:
    if job.status in _APPLY_ACTIVE and job.phase == 'waiting':
        owner = gpu_owner(gpu_lock)
        job.timing.progress.queue_reason, job.timing.progress.queue_label = owner.reason, owner.label
    return {
        "status": job.status,
        "error": job.error,
        "error_code": job.error_code,
        "audio_url": job.audio_url,
        "voice_id": job.voice_id,
        "phase": job.phase,
        "started_at": job.started_at,
        "duration_sec": job.duration_sec,
        "audio_version_id": job.audio_version_id,
        "job_progress": job.timing.progress.model_dump(mode='json'),
        "mix_settings": job.mix_settings.model_dump(mode='json') if job.mix_settings is not None else None,
        "mix": job.mix_report.model_dump(mode='json') if job.mix_report is not None else None,
    }


def _write_apply(job: ApplyJob) -> None:
    if job.status in {'done', 'failed', 'cancelled'}:
        job.timing.finish('done' if job.status == 'done' else 'cancelled' if job.status == 'cancelled' else 'failed')
    payload = _apply_payload(job)
    write_object(_apply_file(job.track_id), payload)
    from .audio_versions import sync_application
    sync_application(job.audio_version_id, job.track_id, job.status, job.error_code)
    if job.status in {'done', 'failed', 'cancelled'}:
        if job.production_run_id is not None:
            from .production_render import record_terminal
            record_terminal(job)
        else:
            voice_authorization.record(job.authorization, 'convert', job.status, job.usage_id,
                voice_id=job.voice_id, track_id=job.track_id, error_code=job.error_code)


def record_apply_failure(voice_id: str, track_id: int, code: str) -> None:
    """Persist a structured conversion-start failure for subsequent polling."""
    _write_apply(ApplyJob(voice_id=voice_id, track_id=track_id, status="failed", error_code=code))


_APPLY_PHASES = {"waiting", "separating", "preparing", "loading", "analyzing", "converting", "mixing"}


def _apply_phase(job: ApplyJob, phase: Literal['waiting', 'separating', 'preparing', 'loading', 'mixing']) -> None:
    job.phase = phase
    if phase == 'waiting':
        job.status = 'queued'
        job.timing.progress.status = 'queued'
        job.timing.phase('waiting_gpu', total=0, unit='tasks')
    else:
        job.status = 'running'
        job.timing.start()
        job.timing.phase(phase, total=0, unit='tasks')
    _write_apply(job)


async def _watch_apply(job: ApplyJob, log_name: str, stop: asyncio.Event) -> None:
    reader = ApplyLogReader(LOG_DIR / f'{log_name}.log')
    while True:
        changed = False
        for event in reader.read():
            if apply_event(job.timing, event):
                job.phase = event.phase
                changed = True
        if changed:
            _write_apply(job)
        if stop.is_set():
            # Drain any final events beyond this bounded read without dropping
            # them or waiting for another polling interval.
            if reader.at_end():
                return
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=.5)
        except TimeoutError:
            pass


def _voice_name(voice_id: object) -> str:
    if not isinstance(voice_id, str) or not _ID.fullmatch(voice_id):
        return ""
    path = voices_root() / voice_id / "voice.json"
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    name = meta.get("name") if isinstance(meta, dict) else ""
    return str(name).strip()[:80] if isinstance(name, str) else ""


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return 0.0
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) and number >= 0 else 0.0


def apply_view(payload: dict) -> dict:
    phase = payload.get("phase") or ""
    voice_id = payload.get("voice_id") or ""
    progress = read_progress(payload.get('job_progress'))
    try:
        settings = VoiceMixSettings.model_validate(payload['mix_settings']) if payload.get('mix_settings') is not None else None
        report = VoiceMixReport.model_validate(payload['mix']) if payload.get('mix') is not None else None
    except ValidationError:
        logger.exception('Invalid retained voice mix report')
        settings, report = None, None
    return {
        "status": payload.get("status") or "idle",
        "error": payload.get("error") or "",
        "error_code": payload.get("error_code") or "",
        "audio_url": payload.get("audio_url") or "",
        "phase": phase if phase in _APPLY_PHASES else "",
        "voice_id": voice_id if isinstance(voice_id, str) else "",
        "voice_name": _voice_name(voice_id),
        "started_at": _number(payload.get("started_at")),
        "duration_sec": _number(payload.get("duration_sec")),
        "job_progress": progress.model_dump(mode='json') if progress is not None else None,
        "mix_settings": settings.model_dump(mode='json') if settings is not None else None,
        "mix": report.model_dump(mode='json') if report is not None else None,
    }


def work_busy() -> bool:
    from .voice_preparation import busy
    if busy():
        return True
    if any(job.status in _ACTIVE for job in _builds.values()):
        return True
    return any(job.cleanup_pending or job.status in _APPLY_ACTIVE for job in _applies.values())


def application_active(track_id: int) -> bool:
    job = _applies.get(track_id)
    return job is not None and (job.cleanup_pending or job.status in _APPLY_ACTIVE)


def apply_status(track_id: int) -> dict:
    job = _applies.get(track_id)
    if job is not None:
        return apply_view(_apply_payload(job))
    path = voices_root() / "_apply" / f"{int(track_id)}.json"
    if not path.is_file():
        return apply_view({})
    with document_lock(path):
        try:
            payload = read_object(path)
        except (OSError, ValueError):
            return apply_view({})
        if payload.get("status") in _APPLY_ACTIVE:
            payload["status"] = "failed"
            payload["error_code"] = "interrupted"
            payload["error"] = ""
            progress = read_progress(payload.get('job_progress'))
            # The crash time is unknown. Freeze at the last persisted observation
            # rather than counting the entire offline interval as processing.
            if progress is not None:
                finish_progress(progress, 'failed', now=progress.observed_at)
            payload['job_progress'] = progress.model_dump(mode='json') if progress is not None else None
            try:
                write_object(path, payload)
            except OSError:
                logger.exception("Could not reconcile voice application status for track %s", track_id)
        return apply_view(payload)


def pretrained_singing_checkpoint(filename: str = _PRETRAINED_NAME) -> Path | None:
    """The downloaded base singing model, not this voice's fine-tune."""
    cache = place_seed_models(DATA_DIR, SEED_VC_DIR)
    if not cache.is_dir():
        return None
    matches = [
        path for path in cache.rglob(filename)
        if path.is_file() and ".locks" not in path.parts
    ]
    return matches[0] if matches else None


async def ensure_target_reference(voice_path: Path, fallback: Path, slot: VoiceProcessSlot, cancelled: Callable[[], bool]) -> Path:
    """A clear sample of this voice for the converter. Reused until the clips change."""
    dest = voice_path / "target.wav"
    marker = voice_path / "target.txt"
    dataset = voice_path / "dataset"
    clips = sorted(dataset.glob("clip_*.wav")) if dataset.is_dir() else []
    if dest.is_file() and marker.is_file() and marker.read_text(encoding="utf-8").strip() == TARGET_VERSION and clips:
        newest = max(clip.stat().st_mtime for clip in clips)
        if dest.stat().st_mtime >= newest:
            return dest
    if not clips:
        return fallback
    stats: list[ClipStat] = []
    for clip in clips:
        duration = await _probe_duration(clip)
        text = await _ffmpeg(
            ["-i", str(clip), "-af", "volumedetect", "-f", "null", "-"],
            slot,
            cancelled,
            timeout=120,
        )
        mean = parse_mean_volume(text)
        stats.append(ClipStat(clip.name, duration, mean if mean is not None else -120.0))
    names = choose_reference_clips(stats)
    chosen = [dataset / name for name in names if (dataset / name).is_file()]
    if not chosen:
        return fallback
    if len(chosen) == 1:
        shutil.copyfile(chosen[0], dest)
    else:
        await merge_vocals(chosen, dest, slot, cancelled)
    keep = reference_trim_seconds(await _probe_duration(dest))
    if keep is not None:
        partial = dest.with_name(f"{dest.stem}.partial.wav")
        try:
            await _ffmpeg(
                ["-y", "-i", str(dest), "-t", str(keep), "-ar", "44100", "-ac", "2", str(partial)],
                slot,
                cancelled,
                timeout=120,
            )
            partial.replace(dest)
        finally:
            partial.unlink(missing_ok=True)
    marker.write_text(f"{TARGET_VERSION}\n", encoding="utf-8")
    return dest


def _voiced_dest(audio_path: Path) -> Path:
    if audio_path.name.endswith(".voiced.wav"):
        return audio_path
    return audio_path.with_name(f"{audio_path.stem}.voiced.wav")


def partial_wav(dest: Path) -> Path:
    """Sibling wav used while the mix is written. A trailing .partial hides the format from ffmpeg."""
    return dest.with_name(f"{dest.stem}.partial.wav")


def _apply_paths(row: sqlite3.Row) -> tuple[Path, Path]:
    """Retries of replacements always use their independent original upload."""
    raw_path, raw_params = row['audio_path'], row['params_json']
    if not isinstance(raw_path, str) or not isinstance(raw_params, str):
        raise VoiceBuildError('track_missing')
    result = Path(raw_path)
    try:
        params = TypeAdapter(JsonObject).validate_json(raw_params)
    except ValidationError as exc:
        raise VoiceBuildError('replacement_source_missing') from exc
    if params.get('source') != 'voice_replacement':
        return result, _voiced_dest(result)
    source_id = params.get('source_track_id')
    if not isinstance(source_id, int) or isinstance(source_id, bool) or source_id <= 0 or row['model'] != 'upload':
        raise VoiceBuildError('replacement_source_missing')
    source_row = db.get_track(source_id)
    if source_row is None or source_row['model'] != 'upload':
        raise VoiceBuildError('replacement_source_missing')
    source_path, source_params = source_row['audio_path'], source_row['params_json']
    if not isinstance(source_path, str) or not isinstance(source_params, str):
        raise VoiceBuildError('replacement_source_missing')
    try:
        provenance = TypeAdapter(JsonObject).validate_json(source_params)
    except ValidationError as exc:
        raise VoiceBuildError('replacement_source_missing') from exc
    original, destination, root = Path(source_path).resolve(), _voiced_dest(result.resolve()), db.FILES_DIR.resolve()
    if provenance.get('source') != 'voice_replacement_source' or not original.is_file() or original == destination \
            or not original.is_relative_to(root) or not destination.resolve().is_relative_to(root):
        raise VoiceBuildError('replacement_source_missing')
    return original, destination


def _publish_apply(job: ApplyJob, destination: Path, partial: Path, converted: Path) -> None:
    _check(job)
    voice_authorization.recheck(job.voice_id, job.authorization, 'convert')
    if job.production_run_id is not None:
        from .production_render import converted_master
        converted_master(job, converted, partial)
    partial.replace(destination)
    if job.production_run_id is not None:
        from .production_render import complete_run
        complete_run(job, destination)
    if not db.update_track_audio(job.track_id, destination):
        raise VoiceBuildError('track_missing')
    db.delete_track_stems(job.track_id)
    db.delete_track_midi(job.track_id)
    job.audio_url = f'/api/tracks/{job.track_id}/audio?v={int(destination.stat().st_mtime)}'
    job.status = 'done'
    job.phase = ''
    job.timing.phase('complete', total=1, current=1, unit='tasks')
    job.error = ''
    job.error_code = ''
    _write_apply(job)
    if job.work_dir is not None:
        shutil.rmtree(job.work_dir, ignore_errors=True)


async def _apply_inner(job: ApplyJob) -> None:
    voice_authorization.recheck(job.voice_id, job.authorization, 'convert')
    _apply_phase(job, 'preparing')
    voice_path = voice_dir(job.voice_id)
    registry = load_registry(voice_path)
    try:
        artifact = job.model_artifact or resolve_model_artifact(voice_path, registry.active_model_id or 'trained')
    except HTTPException:
        raise VoiceBuildError("not_ready")
    checkpoint, config, reference = artifact.checkpoint, artifact.config, artifact.reference
    production = None
    if job.production_run_id is not None:
        from . import artist_production, production_render
        production = artist_production.get_run(job.production_run_id)
        artifact = production_render.require_run(production)
        checkpoint, config, reference = artifact.checkpoint, artifact.config, artifact.reference
    row = db.get_track(job.track_id)
    if not row:
        raise VoiceBuildError("track_missing")
    if job.audio_version_id is not None:
        from .audio_versions import application_paths
        try:
            audio, destination = application_paths(job.track_id, job.audio_version_id)
        except HTTPException as exc:
            raise VoiceBuildError(str(exc.detail)) from exc
    else:
        audio, destination = _apply_paths(row)
    if not audio.is_file():
        raise VoiceBuildError('track_missing')
    python = _engine_python()
    if not python.is_file():
        raise VoiceBuildError("engine_missing")
    from .seed_vc_compat import ensure_compatibility, EngineCompatibilityError
    try:
        ensure_compatibility(SEED_VC_DIR)
    except EngineCompatibilityError as exc:
        raise VoiceBuildError(exc.code) from exc
    ensure_whisper_float32_off_cuda()
    conversion_snapshot = None
    if job.mix_settings is not None:
        from .voice_mix import configure_conversion, observe_conversion
        if job.pinned_conversion is None:
            raise VoiceBuildError('dsp_runtime_unverified')
        conversion_snapshot = await observe_conversion(job.pinned_conversion)
        if job.audio_version_id is None:
            raise VoiceBuildError('dsp_invalid_source')
        configure_conversion(destination, job.audio_version_id, conversion_snapshot)
        if job.audio_version_id is not None:
            from .voice_mix import artifact_path, read_report
            from .video_io import copy_verified, hash_file
            from .music_dsp import MAX_SOURCE_BYTES
            report = read_report(destination, job.audio_version_id)
            if report is not None and report.conversion == conversion_snapshot and report.settings == job.mix_settings:
                from .audio_version_store import get as get_version
                version = get_version(db.get_db(), job.audio_version_id)
                if (version is None or report.source.track_id != version.public.track_id
                        or report.source.version_id != version.public.source_version_id
                        or await hash_file(audio, max_bytes=MAX_SOURCE_BYTES) != report.source_sha256):
                    raise VoiceBuildError('dsp_source_changed')
                _apply_phase(job, 'mixing')
                master = await artifact_path(destination, job.audio_version_id, report.mix.id)
                partial = partial_wav(destination)
                job.partial_outputs.add(partial)
                await copy_verified(master, partial, report.mix.audio_sha256, max_bytes=master.stat().st_size)
                if await observe_conversion(job.pinned_conversion) != conversion_snapshot:
                    raise VoiceBuildError('dsp_source_changed')
                job.mix_report = report
                _publish_apply(job, destination, partial, master)
                return

    _apply_phase(job, 'waiting')
    work = voices_root() / "_apply" / f"work_{job.track_id}"
    job.work_dir = work
    if work.exists():
        shutil.rmtree(work)

    def _track_separate(proc: asyncio.subprocess.Process | None) -> None:
        job.slot.track(proc)
        if proc is not None:
            _apply_phase(job, 'separating')

    if production is not None:
        from .artist_production import asset_path
        stems = {'vocals': asset_path(production.request.guide_asset_id),
            'accompaniment': asset_path(production.request.accompaniment_asset_id)}
    else:
        stems = await separate_file(
            audio,
            work / "stems",
            log_name=f"voice_apply_{job.track_id}",
            on_proc=_track_separate,
            new_session=True,
            spawn=spawn_apply_worker,
        )
    _check(job)
    missing = [name for name in (("vocals", "accompaniment") if production else ("vocals", "drums", "bass", "other")) if name not in stems]
    if missing:
        raise VoiceBuildError("no_stems")
    converted = work / "converted"
    converted.mkdir(parents=True)
    _apply_phase(job, 'preparing')
    if production is not None:
        from .production_render import assess_guide
        await assess_guide(job, production)
    # A published reference is already a reviewed contiguous <=10s window.
    # Legacy voices keep their prior reference-selection behavior.
    target_ref = reference if registry.models else await ensure_target_reference(voice_path, reference, job.slot, lambda: job.cancel)
    mono_source, mono_reference = work / 'source_mono.wav', work / 'reference_mono.wav'
    await phase_safe_mono(stems['vocals'], mono_source, job.slot)
    await phase_safe_mono(target_ref, mono_reference, job.slot)
    _check(job)
    log_name = f"voice_convert_{job.track_id}"
    from .voice_conversion import seed_command
    command = seed_command(python, mono_source, mono_reference, converted,
        diffusion_steps=DIFFUSION_STEPS, checkpoint=checkpoint, configuration=config)
    _apply_phase(job, 'waiting')
    async with gpu_lease(gpu_lock, 'voice_conversion', _voice_name(job.voice_id)):
        _check(job)
        _apply_phase(job, 'loading')
        # Retry logs are replaced before the watcher reads, so an earlier
        # attempt cannot supply this attempt's progress.
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        (LOG_DIR / f'{log_name}.log').write_text('', encoding='utf-8')
        stop = asyncio.Event()
        watcher = asyncio.create_task(_watch_apply(job, log_name, stop))
        try:
            voice_authorization.recheck(job.voice_id, job.authorization, 'convert')
            if job.pinned_conversion is not None:
                from .voice_mix import observe_conversion
                if await observe_conversion(job.pinned_conversion) != conversion_snapshot:
                    raise VoiceBuildError('dsp_source_changed')
            if production is not None:
                from .production_render import require_run
                require_run(production)
                code = await _spawn(command, cwd=SEED_VC_DIR, log_name=log_name, slot=job.slot, offline=True)
            else:
                code = await _spawn(command, cwd=SEED_VC_DIR, log_name=log_name, slot=job.slot)
        finally:
            stop.set()
            await await_cleanup(watcher)
    _check(job)
    if code != 0:
        raise VoiceBuildError("convert_failed", _failure_detail(log_name))
    wavs = sorted(converted.glob("vc_*.wav"))
    if len(wavs) != 1:
        raise VoiceBuildError("convert_failed", _failure_detail(log_name))
    _apply_phase(job, 'mixing')
    expected_seconds = await _probe_duration(stems['vocals'])
    if not math.isfinite(expected_seconds) or expected_seconds <= 0:
        raise VoiceBuildError('invalid_audio')
    source_metrics = await _measure_conversion(stems['vocals'], expected_seconds, job, 'source')
    converted_metrics = await _measure_conversion(wavs[0], expected_seconds, job, 'converted')
    if abs(converted_metrics.duration_delta_sec) > max(.25, .01 * expected_seconds):
        raise VoiceBuildError('output_duration_mismatch')
    dest = destination
    partial = partial_wav(dest)
    job.partial_outputs.add(partial)
    accompaniment = [stems['accompaniment']] if production else [stems['drums'], stems['bass'], stems['other']]
    if job.mix_settings is not None:
        from .audio_version_store import get as get_version
        from .music_analysis_contracts import VersionMusicSource
        from .voice_mix import artifact_path, observe_conversion, render_converted_mix
        from .video_io import copy_verified
        if job.audio_version_id is None or conversion_snapshot is None or job.pinned_conversion is None:
            raise VoiceBuildError('dsp_invalid_source')
        version = get_version(db.get_db(), job.audio_version_id)
        if version is None or version.public.source_version_id is None:
            raise VoiceBuildError('dsp_invalid_source')
        async def command(argv: list[str], timeout: float) -> bytes:
            return await _mix_command(job, argv, timeout)
        job.mix_report = await render_converted_mix(job.mix_settings,
            VersionMusicSource(track_id=version.public.track_id, version_id=version.public.source_version_id), audio,
            stems['vocals'], wavs[0], accompaniment, job.audio_version_id, dest, conversion_snapshot, command)
        _check(job)
        if await observe_conversion(job.pinned_conversion) != conversion_snapshot:
            raise VoiceBuildError('dsp_source_changed')
        master = await artifact_path(dest, job.audio_version_id, job.mix_report.mix.id)
        await copy_verified(master, partial, job.mix_report.mix.audio_sha256, max_bytes=master.stat().st_size)
        from .video_io import hash_file
        from .music_dsp import MAX_SOURCE_BYTES
        if await hash_file(audio, max_bytes=MAX_SOURCE_BYTES) != job.mix_report.source_sha256:
            raise VoiceBuildError('dsp_source_changed')
        matched = master  # Strict production cannot enter this opt-in branch.
    else:
        gain = vocal_gain(source_metrics.level_db, converted_metrics.level_db)
        matched = work / 'matched_vocal.wav'
        await _ffmpeg(['-y', '-i', str(wavs[0]), '-af', f'volume={gain:.8f},alimiter=limit=0.99:level=false:latency=true',
            '-c:a', 'pcm_f32le', str(matched)], job.slot, lambda: job.cancel)
        logger.info('Voice apply %s measured duration_delta=%.3f s, source_level=%.1f dBFS, converted_level=%.1f dBFS, gain=%.3f',
            job.track_id, converted_metrics.duration_delta_sec, source_metrics.level_db, converted_metrics.level_db, gain)
        mix_inputs = [argument for path in accompaniment for argument in ('-i', str(path))]
        await _ffmpeg(['-y', '-i', str(matched), *mix_inputs, '-filter_complex', remix_filter(1 + len(accompaniment)),
            '-map', '[out]', *(['-c:a', 'pcm_f32le'] if production is not None else []), '-f', 'wav', str(partial)],
            job.slot, lambda: job.cancel)
    _publish_apply(job, dest, partial, matched)


def _cleanup_apply_files(job: ApplyJob) -> None:
    for partial in job.partial_outputs:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            logger.exception("Could not remove a temporary voice output")
    if job.work_dir is not None:
        shutil.rmtree(job.work_dir, ignore_errors=True)


async def _run_apply(job: ApplyJob) -> None:
    from .artist_production import ProductionError
    from .artists import ArtistError
    from .music_dsp import MusicDspError
    try:
        await _apply_inner(job)
    except asyncio.CancelledError:
        await kill_proc(job.slot.proc)
        job.slot.track(None)
        job.status = "cancelled"
        job.error_code = "cancelled"
        job.error = ""
        _write_apply(job)
        raise
    except BuildCancelled:
        job.status = "cancelled"
        job.error_code = "cancelled"
        job.error = ""
        _write_apply(job)
    except (VoiceBuildError, MusicDspError) as exc:
        logger.warning("Voice job failed (%s): %s", exc.code, exc.detail if isinstance(exc, VoiceBuildError) else exc.code, exc_info=True)
        if job.cancel:
            job.status = "cancelled"
            job.error_code = "cancelled"
            job.error = ""
        else:
            job.status = "failed"
            job.error_code = exc.code
            job.error = ""
        _write_apply(job)
    except (ProductionError, ArtistError, HTTPException) as exc:
        job.status = 'cancelled' if job.cancel else 'failed'
        job.error_code = exc.code if isinstance(exc, (ProductionError, ArtistError)) else str(exc.detail)
        job.error = ''
        _write_apply(job)
    except Exception:
        logger.exception("Voice job failed")
        if job.cancel:
            job.status = "cancelled"
            job.error_code = "cancelled"
            job.error = ""
        else:
            job.status = "failed"
            job.error_code = "unknown"
            job.error = "Voice processing failed. Check the backend log."
        _write_apply(job)
    finally:
        if job.slot.proc is not None:
            # A failed kill must keep both ownership and files until a later drain.
            job.cleanup_pending = True
            job.status = "failed"
            job.error_code = "apply_cleanup_failed"
            job.error = ""
            _write_apply(job)
        else:
            try:
                _cleanup_apply_files(job)
                if job.status in _APPLY_ACTIVE:
                    job.status = "failed"
                    job.error_code = job.error_code or "interrupted"
                    _write_apply(job)
            finally:
                if _applies.get(job.track_id) is job:
                    _applies.pop(job.track_id, None)


def start_apply(voice_id: str, track_id: int, *, audio_version_id: str | None = None,
                production_run_id: str | None = None, mix: VoiceMixSettings | None = None) -> dict:
    if production_run_id is not None and mix is not None:
        raise HTTPException(status_code=409, detail='voice_mix_strict_production_unsupported')
    authorization = voice_authorization.authorize(voice_id, 'convert')
    from .resource_admission import require_setup_idle
    require_setup_idle()
    if track_id in _apply_cancelling or track_id in _apply_deleting or any(job.cleanup_pending for job in _applies.values()):
        raise HTTPException(status_code=409, detail='apply_busy')
    if not _ID.fullmatch(voice_id or ""):
        raise HTTPException(status_code=404, detail="Voice not found")
    voice_path = voice_dir(voice_id)
    meta = read_meta(voice_path)
    _preview, usable = _artifact_flags(voice_path, meta)
    if not usable:
        raise HTTPException(status_code=409, detail="not_ready")
    registry = load_registry(voice_path)
    pinned_artifact = resolve_model_artifact(voice_path, registry.active_model_id or 'trained')
    production_run = None
    if production_run_id is not None:
        from .artist_production import get_run
        from .production_render import require_run
        production_run = get_run(production_run_id)
        pinned_artifact = require_run(production_run)
    row = db.get_track(track_id)
    if not row:
        raise HTTPException(status_code=404, detail="track_missing")
    current = _applies.get(track_id)
    if current is not None and current.status in _APPLY_ACTIVE:
        if (audio_version_id is not None and current.audio_version_id != audio_version_id
                or mix is not None and mix != current.mix_settings):
            raise HTTPException(status_code=409, detail='apply_busy')
        return apply_status(track_id)
    from .audio_versions import application_paths, prepare_application, version_for_worker
    created_version = False
    if audio_version_id is None:
        audio_version_id = version_for_worker(track_id)
    if audio_version_id is None:
        audio_version_id = prepare_application(track_id, voice_id).id
        created_version = True
    _source_path, mix_destination = application_paths(track_id, audio_version_id)
    from . import voice_mix
    from .music_dsp import MusicDspError
    try:
        stored_settings = voice_mix.read_settings(mix_destination, audio_version_id)
        settings = mix if mix is not None else stored_settings
        if settings is not None and production_run_id is not None:
            raise HTTPException(status_code=409, detail='voice_mix_strict_production_unsupported')
        recorded_model = voice_mix.read_conversion(mix_destination, audio_version_id) if settings is not None else None
        model_id = registry.active_model_id or 'trained'
        if recorded_model is not None:
            if recorded_model.voice_id != voice_id:
                raise MusicDspError('dsp_source_changed')
            model_id = recorded_model.model_revision_id
            pinned_artifact = resolve_model_artifact(voice_path, model_id)
        pinned = voice_mix.pin_conversion(pinned_artifact, voice_id, model_id,
            SEED_VC_DIR / 'inference.py', DIFFUSION_STEPS) if settings is not None else None
        if settings is not None:
            voice_mix.configure(mix_destination, audio_version_id, settings)
    except (MusicDspError, OSError) as error:
        if created_version:
            from .audio_version_store import remove
            remove(db.get_db(), audio_version_id)
        raise HTTPException(status_code=409, detail=error.code if isinstance(error, MusicDspError) else 'dsp_runtime_unverified') from error
    job = ApplyJob(
        voice_id=voice_id,
        track_id=track_id,
        duration_sec=float(row["duration_ms"] or 0) / 1000,
        audio_version_id=audio_version_id,
        authorization=authorization,
        production_run_id=production_run_id,
        mix_settings=settings,
        pinned_conversion=pinned,
    )
    job.model_artifact = pinned_artifact
    job.started_at = job.timing.progress.queued_at
    _applies[track_id] = job
    try:
        if production_run is not None:
            from .artist_production import save_run
            production_run.audio_version_id = audio_version_id
            production_run.status = 'running'
            save_run(production_run)
        else:
            voice_authorization.record(authorization, 'convert', 'requested', job.usage_id, voice_id=voice_id, track_id=track_id)
        _write_apply(job)
        job.task = asyncio.create_task(_run_apply(job))
    except BaseException:
        if _applies.get(track_id) is job:
            _applies.pop(track_id, None)
        if created_version:
            from .audio_version_store import remove
            remove(db.get_db(), audio_version_id)
        raise
    return apply_status(track_id)
