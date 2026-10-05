"""Atomic local video drafts, revisions and constrained reference artifacts."""

from __future__ import annotations
import asyncio, hashlib, logging, math, os, re, shutil, tempfile, threading, uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from fastapi import UploadFile
from pydantic import Field, ValidationError
from . import db
from .config import DATA_DIR
from .video_contracts import (
    VideoContract,
    VideoProject,
    VideoProjectShot,
    VideoReference,
    VideoSpeechClip,
    VideoProjectJob,
    CreateVideoProjectRequest,
    UpdateVideoProjectRequest,
    VideoRevisionRequest,
    VideoRenderRequest,
    VideoExportSettings,
    VideoSeconds,
    VideoSpeechLineRequest,
    ApplyVideoCharacterRequest,
    ApplyVideoCharacterAdapterRequest,
)
from .video_media import tool, probe_media
from .job_lifecycle import await_cleanup, kill_process_tree, spawn_process, communicate_process
from .video_process import WorkerIdentity

logger = logging.getLogger(__name__)
_lock = threading.RLock()
_deleting: set[str] = set()
_reference_tasks: dict[str, dict[asyncio.Task[VideoProject], _ReferenceUploadState]] = {}
_reference_cancelling: dict[str, int] = {}
_ID = re.compile(r"^[0-9a-f]{32}$")


class VideoProjectError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class _ReferenceUploadState:
    started: bool = False
    cancel_requested: bool = False


class SourceIdentity(VideoContract):
    path: str
    sha256: str
    size: int
    device: int
    inode: int
    mtime_ns: int
    ctime_ns: int


class PendingExport(VideoContract):
    path: str
    duration_sec: float
    width: int
    height: int
    fingerprint: str
    output_sha256: str = ""
    project_revision: int = Field(default=0, ge=0)


class StoredVideoProject(VideoContract):
    project: VideoProject
    source: SourceIdentity | None = None
    reference_paths: dict[str, str] = Field(default_factory=dict)
    speech_path: str = ""
    worker: WorkerIdentity | None = None
    published_file: str = ""
    pending_export: PendingExport | None = None
    render_request: VideoRenderRequest | None = None
    requested_export_settings: VideoExportSettings | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def projects_root() -> Path:
    return DATA_DIR / "video_projects"


def project_dir(project_id: str) -> Path:
    if not _ID.fullmatch(project_id):
        raise VideoProjectError("not_found")
    root = projects_root().resolve()
    path = root / project_id
    if not path.resolve().is_relative_to(root):
        raise VideoProjectError("not_found")
    return path


def artifact(project_id: str, relative: str) -> Path:
    name = Path(relative)
    root = project_dir(project_id).resolve()
    path = (root / name).resolve()
    if name.is_absolute() or ".." in name.parts or not path.is_relative_to(root):
        raise VideoProjectError("not_found")
    return path


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix="." + path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save(document: StoredVideoProject) -> None:
    with _lock:
        valid = StoredVideoProject.model_validate(document.model_dump())
        atomic_text(
            project_dir(valid.project.id) / "project.json", valid.model_dump_json()
        )


def ensure_open(project_id: str) -> None:
    with _lock:
        if project_id in _deleting:
            raise VideoProjectError("busy")


def begin_delete(project_id: str) -> None:
    with _lock:
        ensure_open(project_id)
        load(project_id)
        _deleting.add(project_id)


def finish_delete(project_id: str) -> None:
    with _lock:
        _deleting.discard(project_id)


def load(project_id: str) -> StoredVideoProject:
    path = project_dir(project_id) / "project.json"
    try:
        document = StoredVideoProject.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise VideoProjectError("not_found") from exc
    if document.project.id != project_id:
        raise VideoProjectError("not_found")
    return document


def source_path(track_id: int) -> Path:
    track = db.get_track(track_id)
    if track is None:
        raise VideoProjectError("no_track")
    path = Path(track["audio_path"]).resolve()
    if not path.is_file():
        raise VideoProjectError("track_missing")
    return path


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: Path) -> SourceIdentity:
    before = path.stat()
    digest = file_hash(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
        after.st_ino,
    ):
        raise VideoProjectError("source_changed")
    return SourceIdentity(
        path=str(path),
        sha256=digest,
        size=after.st_size,
        device=after.st_dev,
        inode=after.st_ino,
        mtime_ns=after.st_mtime_ns,
        ctime_ns=after.st_ctime_ns,
    )


def source_changed(document: StoredVideoProject) -> bool:
    if document.project.track_id is None:
        return False
    source = document.source
    if source is None:
        return True
    try:
        path = source_path(document.project.track_id)
        stat = path.stat()
    except (OSError, VideoProjectError):
        return True
    return str(path) != source.path or (
        stat.st_size,
        stat.st_dev,
        stat.st_ino,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    ) != (source.size, source.device, source.inode, source.mtime_ns, source.ctime_ns)


CHARACTER_LOCK_STRENGTH = 0.95


def effective_still(project: VideoProject, shot: VideoProjectShot) -> tuple[str | None, float]:
    """Resolve the still sent to the model. Character lock forces one image."""
    still = shot.reference_id
    if still is None and project.mode != "generated" and project.references:
        still = project.references[0].id
    if still is None and project.track_id is None and len(project.references) == 1:
        still = project.references[0].id
    if (
        project.character_lock
        and project.track_id is None
        and len(project.references) == 1
    ):
        still = project.references[0].id
    strength = (
        CHARACTER_LOCK_STRENGTH
        if project.character_lock and project.track_id is None
        else shot.reference_strength
    )
    return still, strength


def character_lock_warnings(project: VideoProject) -> list[str]:
    """Picture projects only. A missing or mixed still is image conditioning, not a trainer."""
    if not project.character_lock or project.track_id is not None or not project.shots:
        return []
    stills = [effective_still(project, shot)[0] for shot in project.shots]
    warnings: list[str] = []
    if any(item is None for item in stills):
        warnings.append("character_still_missing")
    present = {item for item in stills if item is not None}
    if len(present) > 1:
        warnings.append("character_still_mismatch")
    return warnings


def ensure_character_lock(project: VideoProject) -> None:
    warnings = character_lock_warnings(project)
    if "character_still_missing" in warnings:
        raise VideoProjectError("character_still_missing")
    if "character_still_mismatch" in warnings:
        raise VideoProjectError("character_still_mismatch")


def view(document: StoredVideoProject) -> VideoProject:
    project = document.project.model_copy(deep=True)
    project.source_changed = source_changed(document)
    extra = character_lock_warnings(project)
    from .video_character_training import adapter_warning

    trained = adapter_warning(project.character_adapter_id, project.track_id is None)
    if trained:
        extra = [*extra, trained]
    if extra:
        kept = [item for item in project.warnings if item not in {"character_adapter_mock", "character_adapter_missing", "character_still_missing", "character_still_mismatch"}]
        project.warnings = list(dict.fromkeys([*kept, *extra]))
    return project


def get(project_id: str) -> VideoProject:
    with _lock:
        return view(load(project_id))


def list_projects() -> list[VideoProject]:
    root = projects_root()
    if not root.is_dir():
        return []
    found: list[VideoProject] = []
    for path in root.iterdir():
        if not _ID.fullmatch(path.name):
            continue
        try:
            found.append(get(path.name))
        except VideoProjectError:
            logger.warning("Skipping invalid video project %s", path.name)
    return sorted(found, key=lambda project: project.updated_at, reverse=True)


_REEL_PLANS: dict[int, tuple[VideoSeconds, ...]] = {
    8: (4, 4),
    10: (4, 6),
    12: (4, 4, 4),
    14: (4, 4, 6),
}
_REEL_LABELS = (
    "Opening shot.",
    "The same subject continues.",
    "A closer view.",
    "The closing shot.",
)


def _enforce_reel(project: VideoProject) -> None:
    """Keep a reel vertical, short, and free of a library song."""
    if project.preset != "reel":
        if project.track_id is None and project.mode != "generated":
            raise VideoProjectError("song_required")
        return
    if project.track_id is not None:
        raise VideoProjectError("reel_has_song")
    if project.mode != "generated":
        raise VideoProjectError("reel_mode")
    if (project.settings.width, project.settings.height) != (704, 1280):
        raise VideoProjectError("reel_size")
    shots = sorted(project.shots, key=lambda shot: shot.start_sec)
    if not 2 <= len(shots) <= 4:
        raise VideoProjectError("reel_shots")
    if any(shot.seconds not in (2, 4, 6) for shot in shots):
        raise VideoProjectError("reel_length")
    end = shots[-1].start_sec + shots[-1].seconds
    if end < 8 - 1e-6 or end > 15 + 1 / 24:
        raise VideoProjectError("reel_duration")
    project.duration_sec = end


def _picture_project(body: CreateVideoProjectRequest) -> VideoProject:
    from .video_contracts import VideoExportSettings, VideoProjectSettings

    if body.preset == "reel" and body.track_id is not None:
        raise VideoProjectError("reel_has_song")
    if body.mode != "generated":
        raise VideoProjectError("song_required")
    if body.preset == "reel":
        chosen = 12 if body.duration_sec is None else body.duration_sec
        if chosen != int(chosen) or int(chosen) not in _REEL_PLANS:
            raise VideoProjectError("reel_duration")
        lengths = _REEL_PLANS[int(chosen)]
        duration = float(sum(lengths))
        settings = VideoProjectSettings(width=704, height=1280)
        export_settings = VideoExportSettings(aspect="portrait")
        base = (body.direction.strip() or "A vertical scene")[:1800]
        cursor = 0.0
        shots: list[VideoProjectShot] = []
        for index, seconds in enumerate(lengths):
            shots.append(
                VideoProjectShot(
                    id=uuid.uuid4().hex,
                    start_sec=cursor,
                    seconds=seconds,
                    prompt=f"{base} {_REEL_LABELS[index]}",
                    seed=(body.seed + index) % 2147483648,
                )
            )
            cursor += seconds
    else:
        duration = 12 if body.duration_sec is None else body.duration_sec
        if duration < 2 or duration > 60:
            raise VideoProjectError("bad_length")
        settings = VideoProjectSettings()
        export_settings = VideoExportSettings()
        shots = []
    stamp = now()
    project = VideoProject(
        id=uuid.uuid4().hex,
        revision=1,
        track_id=None,
        track_title="",
        name=body.name,
        preset=body.preset,
        mode="generated",
        direction=body.direction,
        seed=body.seed,
        duration_sec=duration,
        source_fingerprint="",
        created_at=stamp,
        updated_at=stamp,
        settings=settings,
        export_settings=export_settings,
        shots=shots,
    )
    _enforce_reel(project)
    return project


async def create(body: CreateVideoProjectRequest) -> VideoProject:
    from .video_jobs import _probe_duration, VideoJobError

    if body.track_id is None:
        project = _picture_project(body)
        document = StoredVideoProject(project=project, source=None)
        try:
            save(document)
        except OSError as exc:
            raise VideoProjectError("storage_failed") from exc
        return view(document)
    if body.preset == "reel":
        raise VideoProjectError("reel_has_song")
    path = source_path(body.track_id)
    before = path.stat()
    try:
        duration = await _probe_duration(path)
    except VideoJobError as exc:
        raise VideoProjectError(exc.code) from exc
    except FileNotFoundError as exc:
        raise VideoProjectError("ffmpeg_missing") from exc
    except (OSError, TimeoutError) as exc:
        raise VideoProjectError("processing_failed") from exc
    if not math.isfinite(duration) or duration < 2 or duration > 21600:
        raise VideoProjectError("bad_length")
    from .video_io import hash_file

    digest = await hash_file(path)
    after = path.stat()
    if (
        before.st_size,
        before.st_dev,
        before.st_ino,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_size,
        after.st_dev,
        after.st_ino,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise VideoProjectError("source_changed")
    source = SourceIdentity(
        path=str(path),
        sha256=digest,
        size=after.st_size,
        device=after.st_dev,
        inode=after.st_ino,
        mtime_ns=after.st_mtime_ns,
        ctime_ns=after.st_ctime_ns,
    )
    track = db.get_track(body.track_id)
    if track is None:
        raise VideoProjectError("no_track")
    project = VideoProject(
        id=uuid.uuid4().hex,
        revision=1,
        track_id=body.track_id,
        track_title=str(track["title"] or f"Track {body.track_id}"),
        name=body.name,
        mode=body.mode,
        direction=body.direction,
        seed=body.seed,
        duration_sec=duration,
        source_fingerprint=source.sha256,
        created_at=now(),
        updated_at=now(),
    )
    document = StoredVideoProject(project=project, source=source)
    try:
        save(document)
    except OSError as exc:
        raise VideoProjectError("storage_failed") from exc
    return view(document)


def mutate(
    project_id: str,
    change: Callable[[StoredVideoProject], None],
    *,
    revision: int | None = None,
    busy_ok: bool = False,
    bump: bool = True,
) -> VideoProject:
    with _lock:
        if not busy_ok:
            ensure_open(project_id)
        document = load(project_id)
        if revision is not None and revision != document.project.revision:
            raise VideoProjectError("revision_conflict")
        if (
            not busy_ok
            and document.project.job is not None
            and document.project.job.status in {"queued", "running"}
        ):
            raise VideoProjectError("busy")
        change(document)
        if bump:
            document.project.revision += 1
        document.project.updated_at = now()
        try:
            save(document)
        except OSError as exc:
            raise VideoProjectError("storage_failed") from exc
        return view(document)


def update(project_id: str, body: UpdateVideoProjectRequest) -> VideoProject:
    def change(document: StoredVideoProject) -> None:
        project = document.project
        before = project.model_copy(deep=True)
        if body.name is not None:
            project.name = body.name
        if body.mode is not None:
            project.mode = body.mode
        if body.direction is not None:
            project.direction = body.direction
        if body.character_lock is not None:
            if body.character_lock and project.track_id is not None:
                raise VideoProjectError("character_lock_picture_only")
            project.character_lock = body.character_lock
        if body.seed is not None:
            project.seed = body.seed
        if body.settings is not None:
            project.settings = body.settings
        if body.export_settings is not None:
            if project.track_id is not None and body.export_settings.attach_speech:
                raise VideoProjectError("speech_picture_only")
            project.export_settings = body.export_settings
        if body.overlays is not None:
            project.overlays = body.overlays
        if body.markers is not None:
            project.markers = body.markers
        if body.shots is not None:
            old = {shot.id: shot for shot in project.shots}
            shots: list[VideoProjectShot] = []
            for draft in sorted(body.shots, key=lambda shot: shot.start_sec):
                limit = 15 if project.preset == "reel" else project.duration_sec
                if draft.start_sec + draft.seconds > limit + 1 / 24:
                    raise VideoProjectError("past_end")
                if (
                    draft.reference_id is not None
                    and draft.reference_id not in document.reference_paths
                ):
                    raise VideoProjectError("reference_not_found")
                shot = VideoProjectShot.model_validate(draft.model_dump())
                previous = old.get(shot.id)
                if previous is not None:
                    shot.variants = previous.variants
                    if draft.model_dump(exclude={"locked"}) == previous.model_dump(
                        exclude={"variants", "approved_variant_id", "locked"}
                    ):
                        shot.approved_variant_id = previous.approved_variant_id
                shots.append(shot)
            project.shots = shots
        _enforce_reel(project)
        if any(
            overlay.end_sec > project.duration_sec + 1 / 24
            for overlay in project.overlays
        ):
            raise VideoProjectError("past_end")
        if any(marker.time_sec > project.duration_sec for marker in project.markers):
            raise VideoProjectError("past_end")
        global_changed = (
            project.settings,
            project.mode,
            project.direction,
            project.character_lock,
        ) != (before.settings, before.mode, before.direction, before.character_lock)
        if global_changed:
            for shot in project.shots:
                shot.approved_variant_id = None
        old_shots = [
            shot.model_dump(exclude={"variants", "approved_variant_id", "locked"})
            for shot in before.shots
        ]
        new_shots = [
            shot.model_dump(exclude={"variants", "approved_variant_id", "locked"})
            for shot in project.shots
        ]
        if (
            global_changed
            or old_shots != new_shots
            or project.overlays != before.overlays
            or project.export_settings != before.export_settings
        ):
            project.file_url = ""
            project.poster_url = ""

    return mutate(project_id, change, revision=body.revision)


def duplicate(project_id: str, body: VideoRevisionRequest) -> VideoProject:
    with _lock:
        ensure_open(project_id)
        document = load(project_id)
        if document.project.revision != body.revision:
            raise VideoProjectError("revision_conflict")
        copied = document.model_copy(deep=True)
        copied.project.id = uuid.uuid4().hex
        copied.project.revision = 1
        copied.project.name = copied.project.name[:110] + " (copy)"
        copied.project.created_at = now()
        copied.project.updated_at = now()
        copied.project.job = None
        copied.project.file_url = ""
        copied.project.poster_url = ""
        copied.worker = None
        copied.published_file = ""
        copied.pending_export = None
        for shot in copied.project.shots:
            shot.variants = []
            shot.approved_variant_id = None
        target = project_dir(copied.project.id)
        try:
            for relative in copied.reference_paths.values():
                original = artifact(project_id, relative)
                dest = artifact(copied.project.id, relative)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, dest)
            if copied.speech_path:
                original = artifact(project_id, copied.speech_path)
                dest = artifact(copied.project.id, copied.speech_path)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, dest)
            for reference in copied.project.references:
                reference.url = f"/api/videos/projects/{copied.project.id}/references/{reference.id}"
            save(copied)
        except OSError as exc:
            shutil.rmtree(target, ignore_errors=True)
            raise VideoProjectError("storage_failed") from exc
        return view(copied)


async def upload_reference(
    project_id: str, revision: int, upload: UploadFile
) -> VideoProject:
    task: asyncio.Task[VideoProject] | None = None
    state = _ReferenceUploadState()
    try:
        # Admission and registration share the deletion gate's lock. The owned
        # task includes process and file cleanup even if its HTTP caller leaves.
        with _lock:
            ensure_open(project_id)
            load(project_id)
            if project_id in _reference_cancelling:
                raise VideoProjectError("busy")
            if any(_reference_cleanup_failed(item) for item in _reference_tasks.get(project_id, ())):
                raise VideoProjectError("cleanup_failed")
            task = asyncio.create_task(_upload_reference(project_id, revision, upload, state))
            _reference_tasks.setdefault(project_id, {})[task] = state
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if task is not None and not task.done():
            _cancel_reference(task, state)
            await await_cleanup(asyncio.gather(task, return_exceptions=True))
        raise
    finally:
        if task is None:
            await await_cleanup(upload.close())
        elif task.done() and not _reference_cleanup_failed(task):
            with _lock:
                owned = _reference_tasks.get(project_id)
                if owned is not None:
                    owned.pop(task, None)
                    if not owned:
                        _reference_tasks.pop(project_id, None)


def _reference_cleanup_failed(task: asyncio.Task[VideoProject]) -> bool:
    if not task.done() or task.cancelled():
        return False
    failure = task.exception()
    return isinstance(failure, VideoProjectError) and failure.code == "cleanup_failed"


def _cancel_reference(task: asyncio.Task[VideoProject], state: _ReferenceUploadState) -> None:
    if task.done():
        return
    # Cancelling an unstarted Task prevents its finally from ever running.
    # Let it enter the cleanup scope and observe the request before doing work.
    if not state.started:
        state.cancel_requested = True
    elif not task.cancelling():
        task.cancel()


def reference_project_ids() -> set[str]:
    with _lock:
        return set(_reference_tasks)


def request_reference_shutdown() -> None:
    with _lock:
        for owned in _reference_tasks.values():
            for task, state in owned.items():
                _cancel_reference(task, state)


def request_reference_cancel(project_id: str) -> tuple[asyncio.Task[VideoProject], ...]:
    with _lock:
        _reference_cancelling[project_id] = _reference_cancelling.get(project_id, 0) + 1
        owned = _reference_tasks.get(project_id, {})
        pending = tuple(owned)
        for task, state in owned.items():
            _cancel_reference(task, state)
        return pending


async def drain_reference_uploads(
    project_id: str, pending: tuple[asyncio.Task[VideoProject], ...]
) -> None:
    try:
        results = await await_cleanup(asyncio.gather(*pending, return_exceptions=True))
        if any(isinstance(result, VideoProjectError) and result.code == "cleanup_failed" for result in results):
            raise VideoProjectError("cleanup_failed")
    finally:
        with _lock:
            remaining = _reference_cancelling[project_id] - 1
            if remaining:
                _reference_cancelling[project_id] = remaining
            else:
                _reference_cancelling.pop(project_id)


async def _cleanup_reference(
    proc: asyncio.subprocess.Process | None,
    temporary: Path | None,
    output: Path | None,
    published: bool,
    upload: UploadFile,
) -> None:
    try:
        try:
            await kill_process_tree(proc)
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if not published and output is not None:
                output.unlink(missing_ok=True)
        finally:
            await upload.close()
    except Exception as exc:
        logger.exception("Reference upload cleanup failed")
        raise VideoProjectError("cleanup_failed") from exc


async def _upload_reference(
    project_id: str, revision: int, upload: UploadFile, state: _ReferenceUploadState
) -> VideoProject:
    name = upload.filename or "reference"
    reference_id = uuid.uuid4().hex
    temporary: Path | None = None
    output: Path | None = None
    count = 0
    published = False
    proc: asyncio.subprocess.Process | None = None
    try:
        state.started = True
        if state.cancel_requested:
            raise asyncio.CancelledError
        if len(name) > 160 or "/" in name or "\\" in name or name in {".", ".."}:
            raise VideoProjectError("invalid_reference")
        root = project_dir(project_id)
        temporary = root / f".{reference_id}.upload"
        output = artifact(project_id, f"references/{reference_id}.png")
        with temporary.open("wb") as handle:
            while chunk := await upload.read(65536):
                count += len(chunk)
                if count > 20 * 1024 * 1024:
                    raise VideoProjectError("reference_too_large")
                handle.write(chunk)
        with temporary.open("rb") as reader:
            header = reader.read(16)
        if not (
            header.startswith(b"\x89PNG\r\n\x1a\n")
            or header.startswith(b"\xff\xd8\xff")
            or header.startswith(b"RIFF")
            and header[8:12] == b"WEBP"
        ):
            raise VideoProjectError("invalid_reference")
        info = await probe_media(temporary)
        if (
            not 1 <= info.width <= 8192
            or not 1 <= info.height <= 8192
            or info.width * info.height > 16777216
        ):
            raise VideoProjectError("reference_too_large")
        output.parent.mkdir(parents=True, exist_ok=True)
        proc = await spawn_process(
            tool("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-i",
            str(temporary),
            "-frames:v",
            "1",
            str(output),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        await communicate_process(proc, 30)
        if proc.returncode != 0:
            raise VideoProjectError("invalid_reference")

        def change(document: StoredVideoProject) -> None:
            if len(document.project.references) >= 6:
                raise VideoProjectError("too_many_references")
            document.reference_paths[reference_id] = str(output.relative_to(root))
            document.project.references.append(
                VideoReference(
                    id=reference_id,
                    name=name,
                    bytes=count,
                    width=info.width,
                    height=info.height,
                    url=f"/api/videos/projects/{project_id}/references/{reference_id}",
                )
            )

        result = mutate(project_id, change, revision=revision)
        published = True
        return result
    finally:
        await await_cleanup(_cleanup_reference(proc, temporary, output, published, upload))


def reference_file(project_id: str, reference_id: str) -> Path:
    document = load(project_id)
    relative = document.reference_paths.get(reference_id)
    if relative is None:
        raise VideoProjectError("not_found")
    path = artifact(project_id, relative)
    if not path.is_file():
        raise VideoProjectError("not_found")
    return path


def speech_file(project_id: str) -> Path:
    document = load(project_id)
    if not document.speech_path or document.project.speech_clip is None:
        raise VideoProjectError("not_found")
    path = artifact(project_id, document.speech_path)
    if not path.is_file():
        raise VideoProjectError("not_found")
    return path


def _looks_like_speech(header: bytes) -> bool:
    if header.startswith(b"RIFF") and header[8:12] == b"WAVE":
        return True
    if header.startswith((b"ID3", b"fLaC", b"OggS")):
        return True
    if len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0:
        return True
    return len(header) >= 8 and header[4:8] == b"ftyp"


async def upload_speech(project_id: str, revision: int, upload: UploadFile) -> VideoProject:
    task: asyncio.Task[VideoProject] | None = None
    state = _ReferenceUploadState()
    try:
        with _lock:
            ensure_open(project_id)
            document = load(project_id)
            if document.project.track_id is not None:
                raise VideoProjectError("speech_picture_only")
            if project_id in _reference_cancelling:
                raise VideoProjectError("busy")
            if any(_reference_cleanup_failed(item) for item in _reference_tasks.get(project_id, ())):
                raise VideoProjectError("cleanup_failed")
            task = asyncio.create_task(_upload_speech(project_id, revision, upload, state))
            _reference_tasks.setdefault(project_id, {})[task] = state
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if task is not None and not task.done():
            _cancel_reference(task, state)
            await await_cleanup(asyncio.gather(task, return_exceptions=True))
        raise
    finally:
        if task is None:
            await await_cleanup(upload.close())
        elif task.done() and not _reference_cleanup_failed(task):
            with _lock:
                owned = _reference_tasks.get(project_id)
                if owned is not None:
                    owned.pop(task, None)
                    if not owned:
                        _reference_tasks.pop(project_id, None)


async def _upload_speech(
    project_id: str, revision: int, upload: UploadFile, state: _ReferenceUploadState
) -> VideoProject:
    import wave

    name = upload.filename or "speech"
    speech_id = uuid.uuid4().hex
    temporary: Path | None = None
    output: Path | None = None
    count = 0
    published = False
    proc: asyncio.subprocess.Process | None = None
    removed: list[str] = []
    try:
        state.started = True
        if state.cancel_requested:
            raise asyncio.CancelledError
        if len(name) > 160 or "/" in name or "\\" in name or name in {".", ".."}:
            raise VideoProjectError("invalid_speech")
        root = project_dir(project_id)
        temporary = root / f".{speech_id}.speech"
        output = artifact(project_id, f"speech/{speech_id}.wav")
        with temporary.open("wb") as handle:
            while chunk := await upload.read(65536):
                count += len(chunk)
                if count > 80 * 1024 * 1024:
                    raise VideoProjectError("speech_too_large")
                handle.write(chunk)
        if count < 1:
            raise VideoProjectError("invalid_speech")
        with temporary.open("rb") as reader:
            header = reader.read(16)
        if not _looks_like_speech(header):
            raise VideoProjectError("invalid_speech")
        output.parent.mkdir(parents=True, exist_ok=True)
        proc = await spawn_process(
            tool("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-i",
            str(temporary),
            "-vn",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-c:a",
            "pcm_s16le",
            str(output),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        await communicate_process(proc, 120)
        if proc.returncode != 0 or not output.is_file():
            raise VideoProjectError("invalid_speech")
        with wave.open(str(output), "rb") as decoded:
            duration = decoded.getnframes() / decoded.getframerate()
        size = output.stat().st_size
        if size > 80 * 1024 * 1024:
            raise VideoProjectError("speech_too_large")
        if not 0.2 <= duration <= 600:
            raise VideoProjectError("invalid_speech")
        digest = file_hash(output)
        relative = str(output.relative_to(root))

        def change(document: StoredVideoProject) -> None:
            if document.project.track_id is not None:
                raise VideoProjectError("speech_picture_only")
            if document.speech_path and document.speech_path != relative:
                removed.append(document.speech_path)
            document.speech_path = relative
            document.project.speech_clip = VideoSpeechClip(
                id=speech_id,
                name=name,
                bytes=size,
                duration_sec=duration,
                sha256=digest,
                kind="upload",
            )
            document.project.export_settings.attach_speech = True
            document.project.warnings = [
                item for item in document.project.warnings if item != "speech_mock"
            ]
            document.project.file_url = ""
            document.project.poster_url = ""

        result = mutate(project_id, change, revision=revision)
        published = True
        for old in removed:
            artifact(project_id, old).unlink(missing_ok=True)
        return result
    finally:
        await await_cleanup(_cleanup_reference(proc, temporary, output, published, upload))


def clear_speech(project_id: str, body: VideoRevisionRequest) -> VideoProject:
    removed: list[str] = []

    def change(document: StoredVideoProject) -> None:
        if document.project.track_id is not None:
            raise VideoProjectError("speech_picture_only")
        if document.speech_path:
            removed.append(document.speech_path)
        document.speech_path = ""
        document.project.speech_clip = None
        document.project.export_settings.attach_speech = False
        document.project.warnings = [
            item for item in document.project.warnings if item != "speech_mock"
        ]
        document.project.file_url = ""
        document.project.poster_url = ""

    result = mutate(project_id, change, revision=body.revision)
    for old in removed:
        artifact(project_id, old).unlink(missing_ok=True)
    return result


def _drop_speech_warning(project: VideoProject, code: str) -> None:
    project.warnings = [item for item in project.warnings if item != code]


async def speak_line(project_id: str, body: VideoSpeechLineRequest) -> VideoProject:
    """Synthesize one talking line into the export track. Not a model input."""
    from . import speech_clone, video_characters, voice_profiles

    text = body.text.strip()
    if not text:
        raise VideoProjectError("text_required")
    profile = video_characters.require_voice(body.profile_id)
    with _lock:
        ensure_open(project_id)
        document = load(project_id)
        if document.project.track_id is not None:
            raise VideoProjectError("speech_picture_only")
        if body.revision != document.project.revision:
            raise VideoProjectError("revision_conflict")
        if document.project.job is not None and document.project.job.status in {"queued", "running"}:
            raise VideoProjectError("busy")
    speech_id = uuid.uuid4().hex
    root = project_dir(project_id)
    temporary = root / f".{speech_id}.line.wav"
    output = artifact(project_id, f"speech/{speech_id}.wav")
    proc: asyncio.subprocess.Process | None = None
    published = False
    removed: list[str] = []
    try:
        try:
            outcome = await asyncio.to_thread(
                speech_clone.synthesize_to_path,
                profile_id=profile.id,
                text=text,
                output_path=temporary,
                require_consent=True,
            )
        except voice_profiles.VoiceProfileError as exc:
            code = "voice_missing" if exc.code == "profile_not_found" else exc.code
            raise VideoProjectError(code) from exc
        if outcome.status not in {"completed", "mock_completed"} or outcome.output_path is None:
            code = (
                "speech_engine_missing"
                if outcome.status in {"engine_not_installed", "api_unavailable"}
                else "speech_failed"
            )
            raise VideoProjectError(code)
        output.parent.mkdir(parents=True, exist_ok=True)
        proc = await spawn_process(
            tool("ffmpeg"),
            "-v", "error", "-y", "-i", str(temporary),
            "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(output),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        await communicate_process(proc, 120)
        if proc.returncode != 0 or not output.is_file():
            raise VideoProjectError("speech_failed")
        import wave

        with wave.open(str(output), "rb") as decoded:
            duration = decoded.getnframes() / decoded.getframerate()
        size = output.stat().st_size
        if size > 80 * 1024 * 1024 or not 0.2 <= duration <= 600:
            raise VideoProjectError("invalid_speech")
        digest = file_hash(output)
        relative = str(output.relative_to(root))
        mock = outcome.status == "mock_completed"

        def change(stored: StoredVideoProject) -> None:
            if stored.project.track_id is not None:
                raise VideoProjectError("speech_picture_only")
            if stored.speech_path and stored.speech_path != relative:
                removed.append(stored.speech_path)
            stored.speech_path = relative
            stored.project.speech_clip = VideoSpeechClip(
                id=speech_id,
                name=f"{profile.name}.wav"[:160],
                bytes=size,
                duration_sec=duration,
                sha256=digest,
                kind="voice",
                voice_profile_id=profile.id,
                line=text[:500],
            )
            stored.project.export_settings.attach_speech = True
            _drop_speech_warning(stored.project, "speech_mock")
            if mock:
                stored.project.warnings = list(dict.fromkeys([*stored.project.warnings, "speech_mock"]))
            stored.project.file_url = ""
            stored.project.poster_url = ""

        result = mutate(project_id, change, revision=body.revision)
        published = True
        for old in removed:
            artifact(project_id, old).unlink(missing_ok=True)
        return result
    finally:
        temporary.unlink(missing_ok=True)
        if proc is not None:
            await await_cleanup(kill_process_tree(proc))
        if not published:
            output.unlink(missing_ok=True)


def apply_character(project_id: str, body: ApplyVideoCharacterRequest) -> VideoProject:
    """Copy the character still onto every shot and turn on Keep one character."""
    from . import video_characters

    character = video_characters.get_character(body.character_id)
    video_characters.require_voice(character.voice_profile_id)
    if not character.consent_confirmed:
        raise VideoProjectError("consent_required")
    source = video_characters.still_file(character.id)
    reference_id = uuid.uuid4().hex
    relative = f"references/{reference_id}.png"
    output = artifact(project_id, relative)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, output)
    published = False
    try:
        size = output.stat().st_size
        if size < 1 or size > 20 * 1024 * 1024:
            raise VideoProjectError("reference_too_large")

        def change(document: StoredVideoProject) -> None:
            if document.project.track_id is not None:
                raise VideoProjectError("character_picture_only")
            if len(document.project.references) >= 6:
                raise VideoProjectError("too_many_references")
            document.reference_paths[reference_id] = relative
            document.project.references.append(
                VideoReference(
                    id=reference_id,
                    name=character.still_name,
                    bytes=size,
                    width=character.still_width,
                    height=character.still_height,
                    url=f"/api/videos/projects/{project_id}/references/{reference_id}",
                )
            )
            for shot in document.project.shots:
                shot.reference_id = reference_id
                shot.reference_strength = CHARACTER_LOCK_STRENGTH
                shot.approved_variant_id = None
            document.project.character_lock = True
            document.project.character_id = character.id
            document.project.file_url = ""
            document.project.poster_url = ""

        result = mutate(project_id, change, revision=body.revision)
        published = True
        return result
    finally:
        if not published:
            output.unlink(missing_ok=True)


def apply_character_adapter(project_id: str, body: ApplyVideoCharacterAdapterRequest) -> VideoProject:
    """Use a trained adapter when one exists. Otherwise keep the still lock, and say so."""
    from . import video_character_training as training

    job = training.get_job(body.training_id)
    if not job.consent_confirmed:
        raise VideoProjectError("consent_required")
    source = training.still_file(job.id)
    reference_id = uuid.uuid4().hex
    relative = f"references/{reference_id}.png"
    output = artifact(project_id, relative)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, output)
    published = False
    try:
        size = output.stat().st_size
        header = output.read_bytes()[:24]
        if not header.startswith(b"\x89PNG\r\n\x1a\n") and not header.startswith(bytes.fromhex("89504e470d0a1a0a")):
            raise VideoProjectError("invalid_reference")
        width = int.from_bytes(header[16:20], "big")
        height = int.from_bytes(header[20:24], "big")
        if size < 1 or size > 20 * 1024 * 1024 or not 1 <= width <= 8192 or not 1 <= height <= 8192:
            raise VideoProjectError("reference_too_large")

        def change(document: StoredVideoProject) -> None:
            if document.project.track_id is not None:
                raise VideoProjectError("character_adapter_picture_only")
            if len(document.project.references) >= 6:
                raise VideoProjectError("too_many_references")
            document.reference_paths[reference_id] = relative
            document.project.references.append(
                VideoReference(
                    id=reference_id,
                    name=f"{job.name}.png"[:160],
                    bytes=size,
                    width=width,
                    height=height,
                    url=f"/api/videos/projects/{project_id}/references/{reference_id}",
                )
            )
            for shot in document.project.shots:
                shot.reference_id = reference_id
                shot.reference_strength = CHARACTER_LOCK_STRENGTH
                shot.approved_variant_id = None
            document.project.character_lock = True
            document.project.character_adapter_id = job.id
            document.project.file_url = ""
            document.project.poster_url = ""

        result = mutate(project_id, change, revision=body.revision)
        published = True
        return result
    finally:
        if not published:
            output.unlink(missing_ok=True)
