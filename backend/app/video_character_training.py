"""Local character LoRA jobs for silent videos and reels.

A still pin is not training. This module stores a consented photo set and, when
a local trainer command is configured, runs that command. The reviewed video
engine's own Python is the intended command: it runs the pinned LTX train CLI
through backend/scripts/train_character_adapter.py. No weights are downloaded
here. Without a command, the job is saved as a dry run and is not an adapter.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile

from .config import DATA_DIR, LTX_DIR
from .gpu_lease import gpu_lease
from .job_lifecycle import communicate_process, kill_process_tree, spawn_process
from .stems import gpu_lock
from .video_contracts import (
    VideoCharacterTrainerStatus,
    VideoCharacterTrainingJob,
)
from .video_media import probe_media, tool
from .video_projects import VideoProjectError, atomic_text

_ID = re.compile(r"^[0-9a-f]{32}$")
_TASKS: dict[str, asyncio.Task[None]] = {}
_MIN_PHOTOS = 3
_MAX_PHOTOS = 12
_MAX_CLIPS = 6
_MAX_PHOTO = 20 * 1024 * 1024
_MAX_CLIP = 80 * 1024 * 1024
_MIN_ADAPTER = 1024
_MAX_ADAPTER = 8 * 1024**3
_TRAIN_TIMEOUT = 24 * 60 * 60


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def jobs_root() -> Path:
    return DATA_DIR / "video_character_training"


def _settings_path() -> Path:
    return DATA_DIR / "video_character_trainer.json"


def job_dir(job_id: str) -> Path:
    if not _ID.fullmatch(job_id):
        raise VideoProjectError("not_found")
    root = jobs_root().resolve()
    path = (root / job_id).resolve()
    if not path.is_relative_to(root):
        raise VideoProjectError("not_found")
    return path


def _job_path(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def _load(job_id: str) -> VideoCharacterTrainingJob:
    try:
        job = VideoCharacterTrainingJob.model_validate_json(_job_path(job_id).read_bytes())
    except (OSError, ValueError) as exc:
        raise VideoProjectError("not_found") from exc
    if job.id != job_id:
        raise VideoProjectError("not_found")
    return job


def _save(job: VideoCharacterTrainingJob) -> None:
    atomic_text(_job_path(job.id), job.model_dump_json())


def training_busy() -> bool:
    return any(not task.done() for task in _TASKS.values())


def _reconcile(job: VideoCharacterTrainingJob) -> VideoCharacterTrainingJob:
    task = _TASKS.get(job.id)
    # A finished in-process task updates its own record. Only a restart, with
    # no task left, means the job was interrupted.
    if job.status in {"queued", "running"} and task is None:
        job = job.model_copy(update={
            "status": "failed",
            "error_code": "interrupted",
            "detail": "Training stopped before it finished.",
            "adapter_ready": False,
            "updated_at": now(),
        })
        try:
            _save(job)
        except OSError as exc:
            raise VideoProjectError("storage_failed") from exc
    return job


def list_jobs() -> list[VideoCharacterTrainingJob]:
    root = jobs_root()
    if not root.is_dir():
        return []
    rows: list[VideoCharacterTrainingJob] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or not _ID.fullmatch(path.name):
            continue
        try:
            rows.append(_reconcile(_load(path.name)))
        except VideoProjectError:
            continue
    rows.sort(key=lambda item: item.updated_at, reverse=True)
    return rows


def get_job(job_id: str) -> VideoCharacterTrainingJob:
    return _reconcile(_load(job_id))


def _command_name(path: Path | None) -> str:
    return path.name if path is not None else ""


def resolve_trainer() -> tuple[Path | None, str, bool]:
    """Return the command, where it came from, and whether a configured path is missing."""
    env = os.getenv("OPENFABRIC_VIDEO_CHARACTER_TRAINER", "").strip()
    if env:
        path = Path(env).expanduser()
        if path.is_file():
            return path, "env", False
        return None, "env", True
    try:
        raw = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None, "none", False
    command = raw.get("command") if isinstance(raw, dict) else None
    if not isinstance(command, str) or not command.strip():
        return None, "none", False
    path = Path(command).expanduser()
    if path.is_file():
        return path, "settings", False
    return None, "settings", True


def trainer_status() -> VideoCharacterTrainerStatus:
    path, source, missing = resolve_trainer()
    return VideoCharacterTrainerStatus(
        configured=path is not None,
        source=source if source in {"env", "settings", "none"} else "none",
        command_name=_command_name(path),
        missing=missing,
    )


def save_trainer_command(command: str) -> VideoCharacterTrainerStatus:
    cleaned = command.strip()
    path = _settings_path()
    if not cleaned:
        path.unlink(missing_ok=True)
        return trainer_status()
    target = Path(cleaned).expanduser()
    if not target.is_absolute() or not target.is_file():
        raise VideoProjectError("trainer_missing")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_text(path, json.dumps({"command": str(target)}))
    return trainer_status()


def _uses_engine_python(command: Path) -> bool:
    return command.name.lower() in {"python", "python.exe", "python3", "python3.exe"}


def train_steps() -> int:
    raw = os.getenv("OPENFABRIC_VIDEO_CHARACTER_TRAIN_STEPS", "800").strip() or "800"
    try:
        steps = int(raw)
    except ValueError:
        steps = 800
    return min(3000, max(100, steps))


def train_rank() -> int:
    raw = os.getenv("OPENFABRIC_VIDEO_CHARACTER_TRAIN_RANK", "32").strip() or "32"
    try:
        rank = int(raw)
    except ValueError:
        rank = 32
    return min(64, max(8, rank))


def training_argv(
    command: Path,
    output: Path,
    name: str,
    images: list[Path],
    clips: list[Path],
) -> list[str]:
    flags = ["--output", str(output), "--name", name]
    for image in images:
        flags += ["--image", str(image)]
    for clip in clips:
        flags += ["--clip", str(clip)]
    if _uses_engine_python(command):
        script = Path(__file__).resolve().parents[1] / "scripts" / "train_character_adapter.py"
        flags = [
            "--steps", str(train_steps()),
            "--rank", str(train_rank()),
            "--engine-dir", str(LTX_DIR),
            "--model-cache", str(DATA_DIR / "models" / "ltx"),
            *flags,
        ]
        return [str(command), str(script), *flags]
    return [str(command), *flags]


def ready_adapter_file(job_id: str | None) -> Path | None:
    """A real LoRA file only. A dry-run job and a still pin are not adapters."""
    if not job_id:
        return None
    try:
        job = _load(job_id)
    except VideoProjectError:
        return None
    if job.mock or job.status != "completed" or not job.adapter_ready:
        return None
    path = job_dir(job_id) / "adapter" / "adapter.safetensors"
    try:
        if path.is_file() and _MIN_ADAPTER <= path.stat().st_size <= _MAX_ADAPTER:
            return path
    except OSError:
        return None
    return None


def adapter_warning(character_adapter_id: str | None, picture: bool) -> str | None:
    if not character_adapter_id or not picture:
        return None
    if ready_adapter_file(character_adapter_id) is not None:
        return None
    try:
        job = _load(character_adapter_id)
    except VideoProjectError:
        return "character_adapter_missing"
    if job.mock or job.status == "mock_completed":
        return "character_adapter_mock"
    return "character_adapter_missing"


def still_file(job_id: str) -> Path:
    path = job_dir(job_id) / "still.png"
    if not path.is_file():
        raise VideoProjectError("not_found")
    return path


def _looks_like_image(header: bytes) -> bool:
    return (
        header.startswith(b"\x89PNG\r\n\x1a\n")
        or header.startswith(b"\xff\xd8\xff")
        or (header.startswith(b"RIFF") and header[8:12] == b"WEBP")
    )


def _looks_like_video(header: bytes) -> bool:
    return header[4:8] == b"ftyp" or header.startswith(b"\x1aE\xdf\xa3")


async def _store_upload(upload: UploadFile, dest: Path, limit: int) -> bytes:
    count = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as handle:
        while chunk := await upload.read(65536):
            count += len(chunk)
            if count > limit:
                raise VideoProjectError("reference_too_large" if limit == _MAX_PHOTO else "clip_too_large")
            handle.write(chunk)
    if count < 1:
        raise VideoProjectError("invalid_reference")
    header = dest.read_bytes()[:16]
    return header


def _publish_adapter(output: Path) -> bool:
    preferred = output / "adapter.safetensors"
    found: list[Path] = []
    if output.is_dir():
        found.extend(path for path in output.rglob("*.safetensors") if path.is_file())
    loras = [path for path in found if "lora" in path.name.lower() or path.name == "adapter.safetensors"]
    chosen = preferred if preferred in found else None
    if chosen is None and loras:
        chosen = max(loras, key=lambda path: path.stat().st_mtime_ns)
    elif chosen is None and len(found) == 1:
        chosen = found[0]
    if chosen is None:
        return False
    try:
        size = chosen.stat().st_size
    except OSError:
        return False
    if not _MIN_ADAPTER <= size <= _MAX_ADAPTER:
        return False
    if chosen.resolve() != preferred.resolve():
        preferred.write_bytes(chosen.read_bytes())
    return preferred.is_file() and preferred.stat().st_size >= _MIN_ADAPTER


async def _run(job_id: str, argv: list[str], output: Path) -> None:
    proc: asyncio.subprocess.Process | None = None
    try:
        current = _load(job_id)
        current = current.model_copy(update={"status": "running", "updated_at": now()})
        _save(current)
        async with gpu_lease(gpu_lock, "video_generation", "Character training"):
            proc = await spawn_process(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"},
            )
            _out, err = await communicate_process(proc, _TRAIN_TIMEOUT)
        detail = err.decode("utf-8", errors="replace").strip()[-400:]
        ready = proc.returncode == 0 and _publish_adapter(output)
        if ready:
            updated = _load(job_id).model_copy(update={
                "status": "completed",
                "adapter_ready": True,
                "mock": False,
                "error_code": "",
                "detail": "",
                "updated_at": now(),
            })
        else:
            code = "model_not_installed" if "model_not_installed" in detail else "trainer_failed"
            updated = _load(job_id).model_copy(update={
                "status": "failed",
                "adapter_ready": False,
                "error_code": code,
                "detail": detail or "The local trainer did not write an adapter.",
                "updated_at": now(),
            })
        _save(updated)
    except TimeoutError:
        failed = _load(job_id).model_copy(update={
            "status": "failed",
            "adapter_ready": False,
            "error_code": "trainer_failed",
            "detail": "Training took too long and was stopped.",
            "updated_at": now(),
        })
        _save(failed)
    except asyncio.CancelledError:
        try:
            cancelled = _load(job_id).model_copy(update={
                "status": "cancelled",
                "adapter_ready": False,
                "error_code": "cancelled",
                "detail": "",
                "updated_at": now(),
            })
            _save(cancelled)
        except VideoProjectError:
            pass
        raise
    except OSError:
        try:
            failed = _load(job_id).model_copy(update={
                "status": "failed",
                "adapter_ready": False,
                "error_code": "storage_failed",
                "detail": "",
                "updated_at": now(),
            })
            _save(failed)
        except VideoProjectError:
            pass
    finally:
        if proc is not None:
            await kill_process_tree(proc)
        _TASKS.pop(job_id, None)


async def create_job(
    *,
    name: str,
    consent_confirmed: bool,
    uploads: list[UploadFile],
) -> VideoCharacterTrainingJob:
    cleaned = " ".join(name.split())
    if not cleaned or len(cleaned) > 80:
        raise VideoProjectError("invalid_draft")
    if not consent_confirmed:
        raise VideoProjectError("consent_required")
    if not uploads:
        raise VideoProjectError("too_few_photos")
    from .video_jobs import work_busy

    if work_busy() or training_busy():
        raise VideoProjectError("busy")
    job_id = uuid.uuid4().hex
    root = job_dir(job_id)
    photos: list[Path] = []
    clips: list[Path] = []
    published = False
    try:
        root.mkdir(parents=True, exist_ok=False)
        for index, upload in enumerate(uploads):
            filename = upload.filename or f"file-{index}"
            if len(filename) > 160 or "/" in filename or "\\" in filename or filename in {".", ".."}:
                raise VideoProjectError("invalid_reference")
            temporary = root / "incoming" / f"{index:02d}.bin"
            header = await _store_upload(upload, temporary, _MAX_CLIP)
            if _looks_like_image(header):
                if temporary.stat().st_size > _MAX_PHOTO:
                    raise VideoProjectError("reference_too_large")
                try:
                    info = await probe_media(temporary)
                except Exception as exc:
                    from .video_media import VideoMediaError
                    if not isinstance(exc, VideoMediaError):
                        raise
                    raise VideoProjectError("invalid_reference") from exc
                if not 1 <= info.width <= 8192 or not 1 <= info.height <= 8192:
                    raise VideoProjectError("reference_too_large")
                target = root / "photos" / f"{len(photos):02d}.png"
                target.parent.mkdir(parents=True, exist_ok=True)
                proc = await spawn_process(
                    tool("ffmpeg"), "-v", "error", "-y", "-i", str(temporary),
                    "-frames:v", "1", str(target),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                await communicate_process(proc, 30)
                if proc.returncode != 0 or not target.is_file():
                    raise VideoProjectError("invalid_reference")
                photos.append(target)
            elif _looks_like_video(header):
                try:
                    info = await probe_media(temporary)
                except Exception as exc:
                    from .video_media import VideoMediaError
                    if not isinstance(exc, VideoMediaError):
                        raise
                    raise VideoProjectError("invalid_clip") from exc
                if info.width < 1 or info.height < 1:
                    raise VideoProjectError("invalid_clip")
                if not 0.4 <= info.video_duration <= 8:
                    raise VideoProjectError("clip_too_long")
                target = root / "clips" / f"{len(clips):02d}.mp4"
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary.replace(target)
                clips.append(target)
            else:
                raise VideoProjectError("invalid_reference")
            temporary.unlink(missing_ok=True)
        if len(photos) < _MIN_PHOTOS:
            raise VideoProjectError("too_few_photos")
        if len(photos) > _MAX_PHOTOS:
            raise VideoProjectError("too_many_photos")
        if len(clips) > _MAX_CLIPS:
            raise VideoProjectError("too_many_clips")
        still = root / "still.png"
        if not still.exists():
            still.write_bytes(photos[0].read_bytes())
        command, source, missing = resolve_trainer()
        stamp = now()
        output = root / "adapter"
        output.mkdir(parents=True, exist_ok=True)
        if source == "none":
            job = VideoCharacterTrainingJob(
                id=job_id,
                name=cleaned,
                status="mock_completed",
                consent_confirmed=True,
                photo_count=len(photos),
                clip_count=len(clips),
                adapter_ready=False,
                mock=True,
                error_code="",
                detail="No local trainer is configured. The photos were saved. No adapter was trained.",
                created_at=stamp,
                updated_at=stamp,
            )
            _save(job)
            published = True
            return job
        if missing or command is None:
            job = VideoCharacterTrainingJob(
                id=job_id,
                name=cleaned,
                status="failed",
                consent_confirmed=True,
                photo_count=len(photos),
                clip_count=len(clips),
                adapter_ready=False,
                mock=False,
                error_code="trainer_missing",
                detail="The trainer command is set, but that file is not on this Mac.",
                created_at=stamp,
                updated_at=stamp,
            )
            _save(job)
            published = True
            return job
        job = VideoCharacterTrainingJob(
            id=job_id,
            name=cleaned,
            status="queued",
            consent_confirmed=True,
            photo_count=len(photos),
            clip_count=len(clips),
            adapter_ready=False,
            mock=False,
            created_at=stamp,
            updated_at=stamp,
        )
        _save(job)
        argv = training_argv(command, output, cleaned, photos, clips)
        _TASKS[job_id] = asyncio.create_task(_run(job_id, argv, output))
        published = True
        return job
    except Exception:
        if not published:
            import shutil
            shutil.rmtree(root, ignore_errors=True)
        raise
    finally:
        for upload in uploads:
            await upload.close()


async def cancel_job(job_id: str) -> VideoCharacterTrainingJob:
    _load(job_id)
    task = _TASKS.get(job_id)
    if task is None or task.done():
        job = _load(job_id)
        if job.status in {"queued", "running"}:
            return _reconcile(job)
        return job
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return get_job(job_id)
