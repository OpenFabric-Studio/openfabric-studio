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
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import UploadFile

from .config import DATA_DIR, LTX_DIR
from .gpu_lease import gpu_lease
from .job_lifecycle import await_cleanup, cancel_and_wait, communicate_process, kill_process_tree, spawn_process
from .image_normalization import normalization_argv
from .video_process import WorkerIdentity, spawn_owned, terminate_verified
from .resource_admission import admission_lock, native_work_inflight, require_setup_idle
from .stems import gpu_lock
from .video_contracts import (
    CharacterDatasetReview,
    ReviewCharacterAdapterRequest,
    VideoCharacterTrainerStatus,
    VideoCharacterTrainingJob,
)
from .video_media import probe_media, tool
from .video_projects import VideoProjectError, atomic_text

_ID = re.compile(r"^[0-9a-f]{32}$")
_TASKS: dict[str, asyncio.Task[None]] = {}
_UPLOADS: set[asyncio.Task[VideoCharacterTrainingJob]] = set()
_UNVERIFIED: set[str] = set()
_STOPPING = False
_LOG = logging.getLogger(__name__)
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
    path = DATA_DIR / "video_character_training"
    if path.is_symlink() or not path.resolve().is_relative_to(DATA_DIR.resolve()):
        raise VideoProjectError("storage_failed")
    return path


def _settings_path() -> Path:
    return DATA_DIR / "video_character_trainer.json"


def job_dir(job_id: str) -> Path:
    if not _ID.fullmatch(job_id):
        raise VideoProjectError("not_found")
    root = jobs_root().resolve()
    path = root / job_id
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise VideoProjectError("not_found")
    return path


def _job_path(job_id: str) -> Path:
    return _artifact(job_id, "job.json")


def _artifact(job_id: str, name: str) -> Path:
    root = job_dir(job_id).resolve()
    relative = Path(name)
    path = root / relative
    if relative.is_absolute() or ".." in relative.parts or path.is_symlink() or not path.resolve().is_relative_to(root):
        raise VideoProjectError("storage_failed")
    return path


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
    return work_busy()


def work_busy() -> bool:
    return bool(_UNVERIFIED or _UPLOADS) or any(not task.done() for task in _TASKS.values())


def list_jobs() -> list[VideoCharacterTrainingJob]:
    root = jobs_root()
    if not root.is_dir():
        return []
    rows: list[VideoCharacterTrainingJob] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or not _ID.fullmatch(path.name):
            continue
        try:
            rows.append(_load(path.name))
        except VideoProjectError:
            continue
    rows.sort(key=lambda item: item.updated_at, reverse=True)
    return rows


def get_job(job_id: str) -> VideoCharacterTrainingJob:
    return _load(job_id)


def _command_name(path: Path | None) -> str:
    return path.name if path is not None else ""


def resolve_trainer() -> tuple[Path | None, Literal["env", "settings", "none"], bool]:
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
    from .video_training_review import dependency_status
    dependencies_ready, reason = dependency_status(path) if path is not None else (False, "trainer_missing")
    return VideoCharacterTrainerStatus(
        configured=path is not None,
        source=source,
        command_name=_command_name(path),
        missing=missing,
        dependencies_ready=dependencies_ready,
        reason=reason,
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
    manifest: Path | None = None,
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
        if manifest is not None:
            flags += ["--dataset-manifest", str(manifest)]
        return [str(command), str(script), *flags]
    if manifest is not None:
        flags += ["--dataset-manifest", str(manifest)]
    return [str(command), *flags]


def ready_adapter_file(job_id: str | None, profile_id: Literal["ltx23", "ltx25"] = "ltx23") -> Path | None:
    """A real LoRA file only. A dry-run job and a still pin are not adapters."""
    if not job_id:
        return None
    try:
        job = _load(job_id)
    except VideoProjectError:
        return None
    if job.mock or job.status != "completed" or not job.adapter_ready:
        return None
    # The shipped trainer targets LTX-2.3. Similar model names do not prove
    # adapter compatibility with another base model or revision.
    if profile_id != "ltx23":
        raise VideoProjectError("character_adapter_incompatible")
    if job.provenance is not None:
        from .video_engine import model_packs
        if job.provenance.settings.base_profile != profile_id or job.provenance.base_revision != model_packs()[profile_id].revision:
            raise VideoProjectError("character_adapter_incompatible")
    try:
        path = _artifact(job_id, "adapter/adapter.safetensors")
        if path.is_file() and _MIN_ADAPTER <= path.stat().st_size <= _MAX_ADAPTER:
            return path
    except (OSError, VideoProjectError):
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
    path = _artifact(job_id, "still.png")
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
    with dest.open("rb") as reader:
        header = reader.read(16)
    return header


def _publish_adapter(output: Path) -> bool:
    if output.is_symlink():
        return False
    preferred = output / "adapter.safetensors"
    found: list[Path] = []
    if output.is_dir():
        found.extend(path for path in output.rglob("*.safetensors") if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(output.resolve()))
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
    if preferred.is_symlink() or chosen.resolve() != preferred.resolve():
        import shutil
        temporary = preferred.with_name(f".adapter-{uuid.uuid4().hex}.safetensors")
        try:
            shutil.copyfile(chosen, temporary)
            temporary.replace(preferred)
        finally:
            temporary.unlink(missing_ok=True)
    return preferred.is_file() and preferred.stat().st_size >= _MIN_ADAPTER


async def _run(job_id: str, argv: list[str], output: Path) -> None:
    proc: asyncio.subprocess.Process | None = None
    drained = False
    try:
        current = _load(job_id)
        current = current.model_copy(update={"status": "running", "updated_at": now()})
        _save(current)
        async with gpu_lease(gpu_lock, "video_generation", "Character training"):
            try:
                log_path = _artifact(job_id, "trainer.log")
                with log_path.open("wb") as log:
                    proc = await spawn_owned(argv, receipt_path=_artifact(job_id, "worker.json"),
                        stdout=log.fileno(), on_identity=lambda identity: atomic_text(_artifact(job_id, "identity.json"), identity.model_dump_json()),
                        env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"})
                    async with asyncio.timeout(_TRAIN_TIMEOUT):
                        await proc.wait()
            finally:
                if proc is not None:
                    await await_cleanup(kill_process_tree(proc))
                    drained = True
        with log_path.open("rb") as log_reader:
            log_reader.seek(0, 2)
            log_reader.seek(max(0, log_reader.tell() - 4000))
            detail = log_reader.read(4000).decode("utf-8", errors="replace")
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
            _LOG.warning("Character trainer failed: %s", detail)
            code = "model_not_installed" if "model_not_installed" in detail else "trainer_failed"
            updated = _load(job_id).model_copy(update={
                "status": "failed",
                "adapter_ready": False,
                "error_code": code,
                "detail": "The local trainer did not write an adapter.",
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
    except Exception:
        _LOG.exception("Character training failed")
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
        try:
            if proc is not None and not drained:
                await await_cleanup(kill_process_tree(proc))
            _artifact(job_id, "identity.json").unlink(missing_ok=True)
        except (OSError, VideoProjectError):
            _UNVERIFIED.add(job_id)
            _LOG.exception("Character worker cleanup failed")
        finally:
            _TASKS.pop(job_id, None)


async def create_job(
    *,
    name: str,
    consent_confirmed: bool,
    uploads: list[UploadFile],
    review: CharacterDatasetReview | None = None,
) -> VideoCharacterTrainingJob:
    async with admission_lock:
        require_setup_idle()
        if _STOPPING or work_busy() or native_work_inflight():
            raise VideoProjectError("busy")
        from .video_jobs import work_busy as video_busy
        from .work_busy import other_work_busy
        if video_busy() or await other_work_busy():
            raise VideoProjectError("busy")
        task = asyncio.create_task(_create_job(name=name, consent_confirmed=consent_confirmed, uploads=uploads, review=review))
        _UPLOADS.add(task)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        task.cancel()
        await await_cleanup(asyncio.gather(task, return_exceptions=True))
        raise
    finally:
        _UPLOADS.discard(task)
        for upload in uploads:
            await await_cleanup(upload.close())


async def _create_job(*, name: str, consent_confirmed: bool, uploads: list[UploadFile], review: CharacterDatasetReview | None = None) -> VideoCharacterTrainingJob:
    cleaned = " ".join(name.split())
    if not cleaned or len(cleaned) > 80:
        raise VideoProjectError("invalid_draft")
    if not consent_confirmed:
        raise VideoProjectError("consent_required")
    if not uploads:
        raise VideoProjectError("too_few_photos")
    if len(uploads) > 18:
        raise VideoProjectError("too_many_photos")
    if review is not None and {item.upload_index for item in review.items} != set(range(len(uploads))):
        raise VideoProjectError("dataset_item_mismatch")
    reviewed_items = {item.upload_index: item for item in review.items} if review is not None else {}
    job_id = uuid.uuid4().hex
    root = job_dir(job_id)
    photos: list[Path] = []
    clips: list[Path] = []
    dataset_paths: list[Path] = []
    dataset_kinds: list[Literal["photo", "clip"]] = []
    published = False
    try:
        root.mkdir(parents=True, exist_ok=False)
        for index, upload in enumerate(uploads):
            held_out = index in reviewed_items and reviewed_items[index].role == "held_out"
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
                target = root / ("held_out" if held_out else "photos") / f"{index:02d}.png"
                target.parent.mkdir(parents=True, exist_ok=True)
                proc = await spawn_process(
                    *normalization_argv(temporary, target),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    await communicate_process(proc, 30)
                finally:
                    await await_cleanup(kill_process_tree(proc))
                if proc.returncode != 0 or not target.is_file():
                    raise VideoProjectError("invalid_reference")
                original = root / "originals" / f"{index:02d}.bin"
                original.parent.mkdir(parents=True, exist_ok=True)
                temporary.replace(original)
                if not held_out:
                    photos.append(target)
                dataset_paths.append(target)
                dataset_kinds.append("photo")
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
                target = root / ("held_out" if held_out else "clips") / f"{index:02d}.mp4"
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary.replace(target)
                if not held_out:
                    clips.append(target)
                dataset_paths.append(target)
                dataset_kinds.append("clip")
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
        provenance = None
        manifest = None
        if review is not None:
            from .video_training_review import build_provenance
            try:
                provenance = build_provenance(root, dataset_paths, dataset_kinds, review, builtin=command is not None and _uses_engine_python(command))
            except ValueError as exc:
                raise VideoProjectError(str(exc)) from exc
            manifest = root / "dataset.json"
            atomic_text(manifest, provenance.model_dump_json())
        if command is not None and _uses_engine_python(command):
            if review is None:
                raise VideoProjectError("dataset_review_required")
            from .video_training_review import dependency_status
            ready, reason = dependency_status(command)
            if not ready:
                raise VideoProjectError(reason)
        stamp = now()
        output = root / "adapter"
        output.mkdir(parents=True, exist_ok=True)
        if source == "none":
            job = VideoCharacterTrainingJob(
                id=job_id,
                name=cleaned,
                status="mock_completed",
                consent_confirmed=True,
                provenance=provenance,
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
                provenance=provenance,
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
            provenance=provenance,
            photo_count=len(photos),
            clip_count=len(clips),
            adapter_ready=False,
            mock=False,
            created_at=stamp,
            updated_at=stamp,
        )
        _save(job)
        argv = training_argv(command, output, cleaned, photos, clips, manifest)
        _TASKS[job_id] = asyncio.create_task(_run(job_id, argv, output))
        published = True
        return job
    except BaseException:
        if not published:
            import shutil
            shutil.rmtree(root, ignore_errors=True)
        raise
    finally:
        for upload in uploads:
            await await_cleanup(upload.close())


async def cancel_job(job_id: str) -> VideoCharacterTrainingJob:
    _load(job_id)
    if job_id in _UNVERIFIED:
        raise VideoProjectError("worker_identity_unverified")
    task = _TASKS.get(job_id)
    if task is None or task.done():
        job = _load(job_id)
        return job
    await cancel_and_wait(task)
    if job_id in _UNVERIFIED:
        raise VideoProjectError("cleanup_failed")
    _TASKS.pop(job_id, None)
    job = _load(job_id)
    if job.status in {"queued", "running"}:
        _save(job.model_copy(update={"status": "cancelled", "error_code": "cancelled", "detail": "", "adapter_ready": False, "updated_at": now()}))
    return get_job(job_id)


async def recover() -> None:
    global _STOPPING
    _STOPPING = True
    for job in list_jobs():
        if job.id in _TASKS:
            continue
        identity_path = _artifact(job.id, "identity.json")
        if identity_path.exists():
            try:
                identity = WorkerIdentity.model_validate_json(identity_path.read_bytes())
                if Path(identity.receipt) != _artifact(job.id, "worker.json") or not await terminate_verified(identity):
                    raise VideoProjectError("worker_identity_unverified")
                identity_path.unlink()
                _UNVERIFIED.discard(job.id)
            except (OSError, ValueError, VideoProjectError):
                _UNVERIFIED.add(job.id)
                _save(job.model_copy(update={"status": "failed", "error_code": "worker_identity_unverified", "detail": "", "adapter_ready": False, "updated_at": now()}))
                continue
        if job.status in {"queued", "running"}:
            _save(job.model_copy(update={"status": "failed", "error_code": "interrupted", "detail": "", "adapter_ready": False, "updated_at": now()}))
    _STOPPING = False


async def shutdown() -> None:
    global _STOPPING
    _STOPPING = True
    for task in tuple(_UPLOADS):
        task.cancel()
    await await_cleanup(asyncio.gather(*tuple(_UPLOADS), return_exceptions=True))
    await await_cleanup(asyncio.gather(*(cancel_job(identifier) for identifier in tuple(_TASKS)), return_exceptions=True))
