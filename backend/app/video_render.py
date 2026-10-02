"""Backend-owned previews, immutable variants and atomic video exports."""

from __future__ import annotations
import asyncio, hashlib, json, logging, math, os, platform, shutil, sys, uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal
from pydantic import Field, ValidationError
from . import video_projects as store
from .config import DATA_DIR, LOG_DIR, LTX_DIR
from .video_contracts import (
    VideoContract,
    VideoProject,
    VideoProjectShot,
    VideoVariant,
    VideoPhaseTiming,
    VideoProjectJob,
    VideoRenderRequest,
    VideoRevisionRequest,
    ApproveVideoVariantRequest,
    VideoExportRequest,
    VideoExportSettings,
    VideoSongAnalysis,
    VideoReadinessResponse,
    VideoEngineOption,
)
from .video_media import tool, validate_media, VideoMediaError
from .video_analysis import AnalysisFailure, analysis_available, planner_energy
from .video_io import copy_verified, hash_file
from .video_process import (
    spawn_owned,
    terminate_verified,
    read_owned_output,
    WorkerIdentity,
    WorkerOutputError,
)
from .job_lifecycle import (
    await_cleanup,
    cancel_and_wait,
    kill_process_tree,
    request_cancel,
)
from .resource_admission import admission_lock, native_work_inflight
from .stems import gpu_lock
from .gpu_lease import gpu_lease
from .video_engine import (
    VideoEngineError,
    ImageReference,
    RenderSettings,
    inspect_readiness,
    render_argv,
)

logger = logging.getLogger(__name__)
_tasks: dict[str, asyncio.Task[None]] = {}
_gpu_projects: set[str] = set()
_unverified: set[str] = set()
_analysis_tasks: dict[str, asyncio.Task[VideoSongAnalysis]] = {}
_cancelling: dict[str, int] = {}
_ACTIVE = {"queued", "running"}


class VariantReceipt(VideoContract):
    variant_id: str
    shot_id: str
    fingerprint: str
    output_sha256: str
    seconds: float
    width: int
    height: int


def work_busy() -> bool:
    return bool(_gpu_projects or _unverified)


def _elapsed(start: str) -> float:
    try:
        return max(
            0,
            (
                datetime.fromisoformat(store.now()) - datetime.fromisoformat(start)
            ).total_seconds(),
        )
    except ValueError:
        return 0


def _job_phase(
    project_id: str,
    phase: str,
    variant_id: str | None = None,
    current: int = 0,
    total: int = 0,
) -> None:
    def change(document: store.StoredVideoProject) -> None:
        job = document.project.job
        if job is None:
            return
        targets: list[VideoProjectJob | VideoVariant] = [job]
        if variant_id is not None:
            for shot in document.project.shots:
                targets.extend(item for item in shot.variants if item.id == variant_id)
        for target in targets:
            if target.phase != phase:
                if target.timings and not target.timings[-1].finished_at:
                    timing = target.timings[-1]
                    timing.finished_at = store.now()
                    timing.duration_sec = _elapsed(timing.started_at)
                target.timings.append(
                    VideoPhaseTiming(phase=phase, started_at=store.now())
                )
            target.phase = phase
            target.progress_current = current
            target.progress_total = total

    store.mutate(project_id, change, busy_ok=True, bump=False)


def _worker(project_id: str, identity: WorkerIdentity | None) -> None:
    def change(document: store.StoredVideoProject) -> None:
        if identity is None:
            document.worker = None
        else:
            path = Path(identity.receipt).resolve()
            root = store.project_dir(project_id).resolve()
            if not path.is_relative_to(root):
                raise store.VideoProjectError("invalid_worker_identity")
            document.worker = identity.model_copy(
                update={"receipt": str(path.relative_to(root))}
            )

    store.mutate(project_id, change, busy_ok=True, bump=False)


async def _command(
    project_id: str,
    argv: list[str],
    phase: str,
    *,
    variant_id: str | None = None,
    timeout: float = 300,
) -> None:
    from .video_jobs import parse_video_phase

    document = store.load(project_id)
    job = document.project.job
    if job is None:
        raise store.VideoProjectError("job_not_found")
    run = store.artifact(project_id, f"runs/{job.id}")
    run.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"video_project_{job.id}_{variant_id or phase}.log"
    _job_phase(project_id, phase, variant_id)
    stop = asyncio.Event()

    async def monitor() -> None:
        while not stop.is_set():
            try:
                with log_path.open("rb") as handle:
                    handle.seek(0, 2)
                    handle.seek(max(0, handle.tell() - 8000))
                    text = handle.read(8000).decode(errors="replace")
            except OSError:
                text = ""
            parsed = parse_video_phase(text)
            if parsed:
                _job_phase(project_id, parsed[0], variant_id, parsed[1], parsed[2])
            try:
                await asyncio.wait_for(stop.wait(), 2)
            except TimeoutError:
                pass

    proc: asyncio.subprocess.Process | None = None
    watcher: asyncio.Task[None] | None = None
    waiter: asyncio.Task[int] | None = None
    env = os.environ.copy()
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["PYTHONUNBUFFERED"] = "1"
    try:
        with log_path.open("w") as log:
            proc = await spawn_owned(
                argv,
                receipt_path=run / "worker.json",
                env=env,
                stdout=log.fileno(),
                on_identity=lambda value: _worker(project_id, value),
            )
            watcher = asyncio.create_task(monitor())
            waiter = asyncio.create_task(proc.wait())
            async with asyncio.timeout(timeout):
                done, _ = await asyncio.wait(
                    {watcher, waiter}, return_when=asyncio.FIRST_COMPLETED
                )
                if watcher in done:
                    await watcher
                code = await waiter
            if code != 0:
                raise store.VideoProjectError(
                    "generate_failed" if phase == "starting" else "processing_failed"
                )
    finally:
        stop.set()
        if watcher is not None:
            await await_cleanup(asyncio.gather(watcher, return_exceptions=True))
        if proc is not None:
            await await_cleanup(kill_process_tree(proc))
            if proc.stdin is not None:
                proc.stdin.close()
        if waiter is not None:
            await await_cleanup(asyncio.gather(waiter, return_exceptions=True))
        _worker(project_id, None)


def readiness() -> VideoReadinessResponse:
    options: list[VideoEngineOption] = []
    engine_ready = False
    for profile in ("ltx23", "ltx25"):
        pack: Literal["ltx23", "ltx25"] = "ltx23" if profile == "ltx23" else "ltx25"
        ready = inspect_readiness(LTX_DIR, DATA_DIR / "models" / "ltx", pack)
        engine_ready = engine_ready or ready.engine_ready
        supported = sys.platform == "darwin" and platform.machine().lower() in {
            "arm64",
            "aarch64",
        }
        options.append(
            VideoEngineOption(
                id=pack,
                name="LTX 2.3 q8" if pack == "ltx23" else "LTX 2.5 q8",
                available=ready.ready and supported,
                reason="unsupported_platform"
                if not supported
                else ""
                if ready.ready
                else "engine_incompatible"
                if not ready.engine_ready
                else "model_not_installed",
                total_bytes=ready.expected_bytes,
                uncached_bytes=ready.uncached_bytes,
                free_bytes=ready.free_bytes,
                model_revision=ready.model_revision,
                text_revision=ready.text_revision,
                warnings=list(ready.warnings),
            )
        )
    try:
        tool("ffmpeg")
        tool("ffprobe")
        ffmpeg = True
    except VideoMediaError:
        ffmpeg = False
    try:
        import PIL

        overlay = True
    except ImportError:
        overlay = False
    analysis = ffmpeg and analysis_available()
    warnings = ["visual_quality_unverified", "lyrics_require_explicit_timing"]
    if not analysis:
        warnings.append("analysis_unavailable")
    return VideoReadinessResponse(
        engine_ready=engine_ready,
        ffmpeg_ready=ffmpeg,
        analysis_ready=analysis,
        overlay_ready=overlay,
        options=options,
        modes=["generated", "cover", "visualizer"],
        warnings=warnings,
    )


async def _engine_fingerprint(project: VideoProject, *, verify: bool = False) -> str:
    if project.mode != "generated":
        return hashlib.sha256(
            (store.file_hash(Path(__file__)) + "cpu-cover").encode()
        ).hexdigest()
    from .video_engine import verified_cached_fingerprint

    cache = DATA_DIR / "models" / "ltx"
    ready = inspect_readiness(LTX_DIR, cache, project.settings.engine_pack)
    if not ready.engine_ready:
        raise store.VideoProjectError("engine_incompatible")
    if not ready.ready:
        raise store.VideoProjectError("model_not_installed")
    fingerprint = verified_cached_fingerprint(cache, project.settings.engine_pack)
    if fingerprint is None:
        if not verify:
            raise store.VideoProjectError("model_verification_required")
        code = (
            "from pathlib import Path;from app.video_engine import verify_and_record_artifacts;p=Path("
            + repr(str(cache))
            + ");k="
            + repr(project.settings.engine_pack)
            + ";verify_and_record_artifacts(p,k)"
        )
        await _command(
            project.id, [sys.executable, "-c", code], "verifying", timeout=3600
        )
        fingerprint = verified_cached_fingerprint(cache, project.settings.engine_pack)
    if fingerprint is None:
        raise store.VideoProjectError("model_integrity_failed")
    return hashlib.sha256(
        (
            fingerprint + ready.model_fingerprint + store.file_hash(Path(__file__))
        ).encode()
    ).hexdigest()


def _prompt(project: VideoProject, shot: VideoProjectShot) -> str:
    return " ".join((project.direction + " " + shot.prompt).split())


def fingerprint(
    document: store.StoredVideoProject, shot: VideoProjectShot, seed: int, engine: str
) -> str:
    project = document.project
    reference = shot.reference_id or (
        project.references[0].id
        if project.mode != "generated" and project.references
        else None
    )
    reference_hash = (
        store.file_hash(store.reference_file(project.id, reference))
        if reference is not None
        else ""
    )
    value = {
        "source": document.source.sha256,
        "engine": engine,
        "mode": project.mode,
        "settings": project.settings.model_dump(),
        "shot": shot.model_dump(exclude={"variants", "approved_variant_id", "locked"}),
        "effective_prompt": _prompt(project, shot),
        "seed": seed,
        "reference_sha256": reference_hash,
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def variant_path(project_id: str, shot_id: str, variant_id: str) -> Path:
    if not all(
        len(value) == 32 and all(char in "0123456789abcdef" for char in value)
        for value in (shot_id, variant_id)
    ):
        raise store.VideoProjectError("not_found")
    return store.artifact(project_id, f"shots/{shot_id}/{variant_id}.mp4")


def _receipt_path(path: Path) -> Path:
    return path.with_suffix(".json")


async def _valid_variant(
    document: store.StoredVideoProject,
    shot: VideoProjectShot,
    variant: VideoVariant,
    expected: str,
) -> bool:
    path = variant_path(document.project.id, shot.id, variant.id)
    if variant.fingerprint != expected:
        return False
    try:
        receipt = VariantReceipt.model_validate_json(_receipt_path(path).read_bytes())
        if (
            receipt.variant_id != variant.id
            or receipt.shot_id != shot.id
            or receipt.fingerprint != expected
            or receipt.output_sha256 != await hash_file(path)
        ):
            return False
        await validate_media(
            path,
            shot.seconds,
            (document.project.settings.width, document.project.settings.height),
        )
        return True
    except (ValidationError, OSError, VideoMediaError):
        return False


def _set_variant(
    project_id: str,
    shot_id: str,
    variant_id: str,
    change: Callable[[VideoVariant], None],
) -> None:
    def mutate(document: store.StoredVideoProject) -> None:
        shot = next(
            (item for item in document.project.shots if item.id == shot_id), None
        )
        if shot is None:
            raise store.VideoProjectError("shot_not_found")
        variant = next((item for item in shot.variants if item.id == variant_id), None)
        if variant is None:
            raise store.VideoProjectError("variant_not_found")
        change(variant)

    store.mutate(project_id, mutate, busy_ok=True, bump=False)


async def _poster(project_id: str, clip: Path, dest: Path) -> None:
    await _command(
        project_id,
        [
            tool("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-i",
            str(clip),
            "-frames:v",
            "1",
            "-update",
            "1",
            str(dest),
        ],
        "poster",
        timeout=30,
    )


async def _cpu_shot(
    project: VideoProject,
    shot: VideoProjectShot,
    seed: int,
    source: Path,
    dest: Path,
    variant_id: str,
) -> None:
    reference = shot.reference_id or (
        project.references[0].id if project.references else None
    )
    if reference is None:
        raise store.VideoProjectError("reference_required")
    image = store.reference_file(project.id, reference)
    width = project.settings.width
    height = project.settings.height
    frames = shot.seconds * 24
    direction = 1 if seed % 2 else -1
    x = f"(iw-iw/zoom)*({'0.2+0.6' if direction > 0 else '0.8-0.6'}*on/{frames})"
    motion = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},zoompan=z='min(1+on*0.0008,1.08)':x='{x}':y='(ih-ih/zoom)/2':d=1:s={width}x{height}:fps=24,format=yuv420p"
    argv = [
        tool("ffmpeg"),
        "-v",
        "error",
        "-y",
        "-loop",
        "1",
        "-framerate",
        "24",
        "-i",
        str(image),
        "-ss",
        str(shot.start_sec),
        "-i",
        str(source),
    ]
    if project.mode == "visualizer":
        filters = f"[0:v]{motion}[cover];[1:a]showwaves=s={width}x{height // 4}:mode=cline:colors=white:r=24,format=rgba[wave];[cover][wave]overlay=0:H-h[v]"
        argv += ["-filter_complex", filters, "-map", "[v]"]
    else:
        argv += ["-vf", motion]
    argv += [
        "-t",
        str(shot.seconds),
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-f",
        "mp4",
        str(dest),
    ]
    await _command(project.id, argv, "motion", variant_id=variant_id, timeout=300)


async def _generate(
    document: store.StoredVideoProject,
    shot: VideoProjectShot,
    seed: int,
    engine: str,
    source: Path,
) -> VideoVariant:
    project = document.project
    variant_id = uuid.uuid4().hex
    expected = fingerprint(document, shot, seed, engine)
    variant = VideoVariant(
        id=variant_id,
        seed=seed,
        fingerprint=expected,
        created_at=store.now(),
        prompt=_prompt(project, shot),
        settings=project.settings.model_copy(),
        mode=project.mode,
        reference_id=shot.reference_id,
        reference_strength=shot.reference_strength,
        source_fingerprint=document.source.sha256,
        engine_fingerprint=engine,
    )

    def add(saved: store.StoredVideoProject) -> None:
        target = next(item for item in saved.project.shots if item.id == shot.id)
        if len(target.variants) >= 20:
            raise store.VideoProjectError("variant_limit")
        target.variants.append(variant)

    store.mutate(project.id, add, busy_ok=True, bump=False)
    path = variant_path(project.id, shot.id, variant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.stem + ".partial.mp4")

    def running(item: VideoVariant) -> None:
        item.status = "running"
        item.started_at = store.now()

    _set_variant(project.id, shot.id, variant_id, running)
    try:
        if project.mode == "generated":
            excerpt = path.with_suffix(".wav")
            await _command(
                project.id,
                [
                    tool("ffmpeg"),
                    "-v",
                    "error",
                    "-y",
                    "-ss",
                    str(shot.start_sec),
                    "-i",
                    str(source),
                    "-t",
                    str(shot.seconds),
                    "-vn",
                    "-ac",
                    "2",
                    "-ar",
                    "44100",
                    str(excerpt),
                ],
                "excerpt",
                variant_id=variant_id,
            )
            references = (
                (
                    ImageReference(
                        store.reference_file(project.id, shot.reference_id),
                        0,
                        shot.reference_strength,
                    ),
                )
                if shot.reference_id
                else ()
            )
            frames = shot.seconds * 24 + 1
            settings = RenderSettings(
                output=partial,
                prompt=_prompt(project, shot),
                frames=frames,
                source_audio=excerpt,
                references=references,
                profile_id=project.settings.engine_pack,
                width=project.settings.width,
                height=project.settings.height,
                seed=seed,
                stage1_steps=project.settings.stage1_steps,
                stage2_steps=project.settings.stage2_steps,
                cfg_scale=project.settings.cfg_scale,
                negative_prompt=project.settings.negative_prompt or None,
                temporal_tiles=2 if frames > 145 else 1,
                spatial_tiles=2 if project.settings.width >= 1280 else 1,
            )
            await _command(
                project.id,
                render_argv(LTX_DIR, DATA_DIR / "models" / "ltx", settings),
                "starting",
                variant_id=variant_id,
                timeout=7200,
            )
        else:
            await _cpu_shot(project, shot, seed, source, partial, variant_id)
        await validate_media(
            partial, shot.seconds, (project.settings.width, project.settings.height)
        )
        receipt = VariantReceipt(
            variant_id=variant_id,
            shot_id=shot.id,
            fingerprint=expected,
            output_sha256=await hash_file(partial),
            seconds=shot.seconds,
            width=project.settings.width,
            height=project.settings.height,
        )
        store.atomic_text(_receipt_path(path), receipt.model_dump_json())
        partial.replace(path)
        poster = path.with_suffix(".png")
        await _poster(project.id, path, poster)

        def ready(item: VideoVariant) -> None:
            item.status = "ready"
            item.finished_at = store.now()
            item.duration_sec = shot.seconds
            item.file_url = f"/api/videos/projects/{project.id}/shots/{shot.id}/variants/{variant_id}/file"
            item.poster_url = f"/api/videos/projects/{project.id}/shots/{shot.id}/variants/{variant_id}/poster"
            if item.timings and not item.timings[-1].finished_at:
                timing = item.timings[-1]
                timing.finished_at = store.now()
                timing.duration_sec = _elapsed(timing.started_at)

        _set_variant(project.id, shot.id, variant_id, ready)
        return next(
            item for item in store.get(project.id).shots if item.id == shot.id
        ).variants[-1]
    except BaseException as exc:
        code = (
            "cancelled"
            if isinstance(exc, asyncio.CancelledError)
            else exc.code
            if isinstance(
                exc, (store.VideoProjectError, VideoEngineError, VideoMediaError)
            )
            else "processing_failed"
        )

        def failed(item: VideoVariant) -> None:
            item.status = "cancelled" if code == "cancelled" else "failed"
            item.error_code = code
            item.finished_at = store.now()

        _set_variant(project.id, shot.id, variant_id, failed)
        raise
    finally:
        partial.unlink(missing_ok=True)


def aspect_size(
    settings: VideoExportSettings, width: int, height: int
) -> tuple[int, int]:
    if settings.aspect == "portrait":
        return (int(height * 9 / 16) // 2 * 2, height)
    if settings.aspect == "square":
        return (min(width, height), min(width, height))
    return (width, int(width * 9 / 16) // 2 * 2)


def _timeline(
    shots: list[VideoProjectShot], duration: float
) -> list[tuple[VideoProjectShot | None, float]]:
    events: list[tuple[VideoProjectShot | None, float]] = []
    cursor = 0.0
    for shot in sorted(shots, key=lambda item: item.start_sec):
        gap = round((shot.start_sec - cursor) * 24) / 24
        if gap < 0:
            raise store.VideoProjectError("overlap")
        if gap > 0:
            events.append((None, gap))
        events.append((shot, float(shot.seconds)))
        cursor = shot.start_sec + shot.seconds
    remaining = round((duration - cursor) * 24) / 24
    if remaining > 0:
        events.append((None, remaining))
    return events


async def _assemble(
    document: store.StoredVideoProject,
    source: Path,
    engine: str,
    settings: VideoExportSettings,
) -> None:
    from .video_jobs import timeline_duration, _concat_list

    project = document.project
    job = project.job
    if job is None or not project.shots:
        raise store.VideoProjectError("no_shots")
    duration = (
        math.ceil(
            timeline_duration(
                [shot.model_dump() for shot in project.shots], project.duration_sec
            )
            * 24
            - 1e-6
        )
        / 24
    )
    width, height = aspect_size(
        settings, project.settings.width, project.settings.height
    )
    run = store.artifact(project.id, f"runs/{job.id}")
    run.mkdir(parents=True, exist_ok=True)
    pieces: list[Path] = []
    previous: VideoProjectShot | None = None
    for index, (shot, seconds) in enumerate(_timeline(project.shots, duration)):
        dest = run / f"piece_{index:02d}.mp4"
        if shot is None:
            held = previous or project.shots[0]
            selected = next(
                (
                    item
                    for item in held.variants
                    if item.id == held.approved_variant_id and item.status == "ready"
                ),
                None,
            )
            if selected is None:
                raise store.VideoProjectError("approval_required")
            if not await _valid_variant(
                document,
                held,
                selected,
                fingerprint(document, held, selected.seed, engine),
            ):
                raise store.VideoProjectError("stale_variant")
            frame = run / f"hold_{index:02d}.png"
            seek = max(0, held.seconds - 1 / 24) if previous else 0
            await _command(
                project.id,
                [
                    tool("ffmpeg"),
                    "-v",
                    "error",
                    "-y",
                    "-ss",
                    str(seek),
                    "-i",
                    str(variant_path(project.id, held.id, selected.id)),
                    "-frames:v",
                    "1",
                    "-update",
                    "1",
                    str(frame),
                ],
                "hold",
            )
            argv = [
                tool("ffmpeg"),
                "-v",
                "error",
                "-y",
                "-loop",
                "1",
                "-framerate",
                "24",
                "-i",
                str(frame),
                "-t",
                str(seconds),
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps=24",
            ]
        else:
            variant = next(
                (
                    item
                    for item in shot.variants
                    if item.id == shot.approved_variant_id and item.status == "ready"
                ),
                None,
            )
            if variant is None:
                raise store.VideoProjectError("approval_required")
            if not await _valid_variant(
                document,
                shot,
                variant,
                fingerprint(document, shot, variant.seed, engine),
            ):
                raise store.VideoProjectError("stale_variant")
            argv = [
                tool("ffmpeg"),
                "-v",
                "error",
                "-y",
                "-i",
                str(variant_path(project.id, shot.id, variant.id)),
                "-t",
                str(seconds),
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps=24",
            ]
        argv += [
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            str({"fast": 28, "standard": 20, "high": 16}[settings.quality]),
            str(dest),
        ]
        await _command(project.id, argv, "assemble")
        pieces.append(dest)
        if shot is not None:
            previous = shot
    listing = run / "concat.txt"
    listing.write_text(_concat_list(pieces))
    joined = run / "joined.mp4"
    await _command(
        project.id,
        [
            tool("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c",
            "copy",
            str(joined),
        ],
        "assemble",
    )
    output = store.artifact(project.id, f"exports/{job.id}.mp4")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.stem + ".partial.mp4")
    export_fingerprint = hashlib.sha256(
        (
            project.model_dump_json(
                exclude={"job", "updated_at", "file_url", "poster_url"}
            )
            + settings.model_dump_json()
            + engine
        ).encode()
    ).hexdigest()

    def pending(saved: store.StoredVideoProject) -> None:
        saved.pending_export = store.PendingExport(
            path=str(output.relative_to(store.project_dir(project.id))),
            duration_sec=duration,
            width=width,
            height=height,
            fingerprint=export_fingerprint,
            project_revision=project.revision,
        )

    store.mutate(project.id, pending, busy_ok=True, bump=False)
    try:
        argv = [
            tool("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-i",
            str(joined),
            "-i",
            str(source),
        ]
        if settings.include_overlays and project.overlays:
            from .video_text import render_text

            chain = "[0:v]null[v0]"
            last = "v0"
            input_index = 2
            for index, overlay in enumerate(project.overlays):
                image = run / f"text_{index}.png"
                render_text(overlay, width, height, image)
                argv += ["-i", str(image)]
                chain += f";[{last}][{input_index}:v]overlay=0:0:enable='between(t,{overlay.start_sec},{overlay.end_sec})'[v{index + 1}]"
                last = f"v{index + 1}"
                input_index += 1
            argv += [
                "-filter_complex",
                chain,
                "-map",
                f"[{last}]",
                "-c:v",
                "libx264",
                "-crf",
                str({"fast": 28, "standard": 20, "high": 16}[settings.quality]),
                "-pix_fmt",
                "yuv420p",
            ]
        else:
            argv += ["-map", "0:v:0", "-c:v", "copy"]
        argv += [
            "-map",
            "1:a:0",
            "-c:a",
            "aac",
            "-t",
            str(duration),
            "-f",
            "mp4",
            str(partial),
        ]
        await _command(project.id, argv, "export")
        await validate_media(partial, duration, (width, height), require_audio=True)
        output_hash = await hash_file(partial)

        def verified(saved: store.StoredVideoProject) -> None:
            if saved.pending_export is not None:
                saved.pending_export.output_sha256 = output_hash

        store.mutate(project.id, verified, busy_ok=True, bump=False)
        partial.replace(output)
        poster = output.with_suffix(".png")
        await _poster(project.id, output, poster)

        def publish(saved: store.StoredVideoProject) -> None:
            saved.published_file = str(
                output.relative_to(store.project_dir(project.id))
            )
            saved.pending_export = None
            saved.project.file_url = f"/api/videos/projects/{project.id}/file"
            saved.project.poster_url = f"/api/videos/projects/{project.id}/poster"
            saved.project.export_settings = settings

        store.mutate(project.id, publish, busy_ok=True, bump=False)
    finally:
        partial.unlink(missing_ok=True)


async def _run(
    project_id: str,
    request: VideoRenderRequest,
    operation: Literal["preview", "render", "export"],
    settings: VideoExportSettings | None,
) -> None:
    try:
        document = store.load(project_id)
        project = document.project
        job = project.job
        if job is None:
            raise store.VideoProjectError("job_not_found")

        def running(saved: store.StoredVideoProject) -> None:
            if saved.project.job is not None:
                saved.project.job.status = "running"
                saved.project.job.started_at = store.now()

        store.mutate(project_id, running, busy_ok=True, bump=False)
        _job_phase(project_id, "preparing")
        run = store.artifact(project_id, f"runs/{job.id}")
        run.mkdir(parents=True, exist_ok=True)
        source = run / ("source" + Path(document.source.path).suffix)
        store.atomic_text(run / "review.json", document.model_dump_json())
        await copy_verified(
            store.source_path(project.track_id), source, document.source.sha256
        )

        async def render() -> None:
            engine = await _engine_fingerprint(project, verify=True)
            selected = (
                set(request.shot_ids)
                if request.shot_ids
                else {shot.id for shot in project.shots}
            )
            if operation != "export":
                for index, shot in enumerate(project.shots):
                    if shot.id not in selected:
                        continue

                    def progress(saved: store.StoredVideoProject) -> None:
                        if saved.project.job is not None:
                            saved.project.job.shot_index = index + 1

                    store.mutate(project_id, progress, busy_ok=True, bump=False)
                    approved = next(
                        (
                            item
                            for item in shot.variants
                            if item.id == shot.approved_variant_id
                            and item.status == "ready"
                        ),
                        None,
                    )
                    if (
                        operation == "render"
                        and approved is not None
                        and await _valid_variant(
                            document,
                            shot,
                            approved,
                            fingerprint(document, shot, approved.seed, engine),
                        )
                    ):
                        continue
                    for variant_index in range(request.variants_per_shot):
                        seed = (shot.seed + variant_index) % 2147483648
                        expected = fingerprint(document, shot, seed, engine)
                        reusable = None
                        if request.reuse_completed:
                            for item in reversed(shot.variants):
                                if (
                                    item.status == "ready"
                                    and item.seed == seed
                                    and await _valid_variant(
                                        document, shot, item, expected
                                    )
                                ):
                                    reusable = item
                                    break
                        candidate = reusable or await _generate(
                            document, shot, seed, engine, source
                        )
                        if operation == "render" and variant_index == 0:

                            def approve(saved: store.StoredVideoProject) -> None:
                                next(
                                    item
                                    for item in saved.project.shots
                                    if item.id == shot.id
                                ).approved_variant_id = candidate.id

                            store.mutate(project_id, approve, busy_ok=True, bump=False)
                    document_updated = store.load(project_id)
                    shot.variants = next(
                        item
                        for item in document_updated.project.shots
                        if item.id == shot.id
                    ).variants
            if operation in {"render", "export"}:
                await _assemble(
                    store.load(project_id),
                    source,
                    engine,
                    settings or project.export_settings,
                )

        if project.mode == "generated" and operation != "export":
            _job_phase(project_id, "waiting")
            async with gpu_lease(gpu_lock, 'video_generation', 'Video project'):
                await render()
        else:
            await render()
        _finish(project_id, "ready", "")
    except asyncio.CancelledError:
        _finish(project_id, "cancelled", "cancelled")
        raise
    except (store.VideoProjectError, VideoMediaError, VideoEngineError) as exc:
        logger.warning(
            "Video project %s failed: %s", project_id, exc.code, exc_info=True
        )
        _finish(project_id, "failed", exc.code)
    except Exception:
        logger.exception("Video project %s failed", project_id)
        _finish(project_id, "failed", "processing_failed")
    finally:
        _gpu_projects.discard(project_id)
        if _tasks.get(project_id) is asyncio.current_task():
            _tasks.pop(project_id, None)


def _finish(
    project_id: str, status: Literal["ready", "failed", "cancelled"], code: str
) -> None:
    def change(document: store.StoredVideoProject) -> None:
        job = document.project.job
        if job is not None:
            job.status = status
            job.error_code = code
            job.finished_at = store.now()
            job.phase = ""
            if job.timings and not job.timings[-1].finished_at:
                timing = job.timings[-1]
                timing.finished_at = store.now()
                timing.duration_sec = _elapsed(timing.started_at)

    store.mutate(project_id, change, busy_ok=True, bump=False)


async def start(
    project_id: str,
    body: VideoRenderRequest,
    *,
    operation: Literal["preview", "render", "export"] = "render",
    export_settings: VideoExportSettings | None = None,
) -> VideoProject:
    from .video_jobs import work_busy as video_busy, workers_unverified
    from .work_busy import other_work_busy

    async with admission_lock:
        store.ensure_open(project_id)
        document = store.load(project_id)
        project = document.project
        if document.worker is not None:
            raise store.VideoProjectError("worker_identity_unverified")
        if body.revision != project.revision:
            raise store.VideoProjectError("revision_conflict")
        needs_gpu = project.mode == "generated" and operation != "export"
        slot_busy = video_busy() if needs_gpu else any(
            active_id not in _gpu_projects for active_id in _tasks
        )
        if (
            project_id in _tasks or _cancelling or project_id in _analysis_tasks
            or _unverified or workers_unverified() or slot_busy
        ):
            raise store.VideoProjectError("busy")
        if store.source_changed(document):
            raise store.VideoProjectError("source_changed")
        if not project.shots:
            raise store.VideoProjectError("no_shots")
        if any(
            value not in {shot.id for shot in project.shots} for value in body.shot_ids
        ):
            raise store.VideoProjectError("shot_not_found")
        try:
            tool("ffmpeg")
            tool("ffprobe")
        except VideoMediaError as exc:
            raise store.VideoProjectError("ffmpeg_missing") from exc
        effective_export = export_settings or project.export_settings
        if operation != "preview" and effective_export.include_overlays and project.overlays:
            try:
                import PIL
                from .video_text import validate_text
            except ImportError as exc:
                raise store.VideoProjectError("image_tools_unavailable") from exc
            width, height = aspect_size(effective_export, project.settings.width, project.settings.height)
            for overlay in project.overlays:
                validate_text(overlay, width, height)
        if project.mode == "generated":
            from .ace_jobs import work_busy as ace_busy

            if needs_gpu and (native_work_inflight() or ace_busy() or await other_work_busy()):
                raise store.VideoProjectError("busy")
            if sys.platform != "darwin" or platform.machine().lower() not in {
                "arm64",
                "aarch64",
            }:
                raise store.VideoProjectError("unsupported_platform")
            ready = inspect_readiness(
                LTX_DIR, DATA_DIR / "models" / "ltx", project.settings.engine_pack
            )
            if not ready.engine_ready:
                raise store.VideoProjectError("engine_incompatible")
            if not ready.ready:
                raise store.VideoProjectError("model_not_installed")
        elif not project.references:
            raise store.VideoProjectError("reference_required")
        if operation == "export" and any(
            shot.approved_variant_id is None for shot in project.shots
        ):
            raise store.VideoProjectError("approval_required")

        def reserve(saved: store.StoredVideoProject) -> None:
            saved.project.job = VideoProjectJob(
                id=uuid.uuid4().hex,
                operation=operation,
                status="queued",
                shot_ids=body.shot_ids or [shot.id for shot in project.shots],
                shot_count=len(body.shot_ids) or len(project.shots),
            )
            saved.render_request = body
            saved.requested_export_settings = export_settings
            saved.pending_export = None

        result = store.mutate(project_id, reserve, revision=body.revision)
        if needs_gpu:
            _gpu_projects.add(project_id)
        _tasks[project_id] = asyncio.create_task(
            _run(project_id, body, operation, export_settings)
        )
        return result


async def export(project_id: str, body: VideoExportRequest) -> VideoProject:
    return await start(
        project_id,
        VideoRenderRequest(revision=body.revision),
        operation="export",
        export_settings=body.settings,
    )


async def resume(project_id: str, body: VideoRevisionRequest) -> VideoProject:
    document = store.load(project_id)
    job = document.project.job
    if job is None or job.status not in {"failed", "cancelled"}:
        raise store.VideoProjectError("nothing_to_resume")
    request = (
        document.render_request or VideoRenderRequest(revision=body.revision)
    ).model_copy(update={"revision": body.revision, "reuse_completed": True})
    return await start(
        project_id,
        request,
        operation=job.operation,
        export_settings=document.requested_export_settings
        or document.project.export_settings,
    )


async def approve(
    project_id: str, shot_id: str, body: ApproveVideoVariantRequest
) -> VideoProject:
    document = store.load(project_id)
    if project_id in _unverified or document.worker is not None:
        raise store.VideoProjectError("worker_identity_unverified")
    if document.project.revision != body.revision:
        raise store.VideoProjectError("revision_conflict")
    if store.source_changed(document):
        raise store.VideoProjectError("source_changed")
    shot = next((item for item in document.project.shots if item.id == shot_id), None)
    if shot is None:
        raise store.VideoProjectError("shot_not_found")
    variant = next(
        (
            item
            for item in shot.variants
            if item.id == body.variant_id and item.status == "ready"
        ),
        None,
    )
    if variant is None:
        raise store.VideoProjectError("variant_not_found")
    engine = await _engine_fingerprint(document.project)
    if not await _valid_variant(
        document, shot, variant, fingerprint(document, shot, variant.seed, engine)
    ):
        raise store.VideoProjectError("stale_variant")

    def change(saved: store.StoredVideoProject) -> None:
        target = next(item for item in saved.project.shots if item.id == shot_id)
        if target.approved_variant_id != variant.id:
            saved.project.file_url = ""
            saved.project.poster_url = ""
        target.approved_variant_id = variant.id

    return store.mutate(project_id, change, revision=body.revision)


async def cancel(project_id: str) -> VideoProject:
    async with admission_lock:
        task = _tasks.get(project_id)
        measurement = _analysis_tasks.get(project_id)
        job = store.get(project_id).job
        _cancelling[project_id] = _cancelling.get(project_id, 0) + 1
        references = store.request_reference_cancel(project_id)
        request_cancel(task)
        if (
            measurement is not None
            and not measurement.done()
            and not measurement.cancelling()
        ):
            measurement.cancel()
    try:
        pending: list[asyncio.Task[None] | asyncio.Task[VideoSongAnalysis]] = []
        pending.append(asyncio.create_task(store.drain_reference_uploads(project_id, references)))
        if task is not None:
            pending.append(task)
        if measurement is not None:
            pending.append(measurement)
        results = await await_cleanup(asyncio.gather(*pending, return_exceptions=True))
        for result in results:
            if isinstance(result, BaseException) and not isinstance(
                result, asyncio.CancelledError
            ):
                logger.error("Video cleanup failed", exc_info=result)
                raise store.VideoProjectError("cleanup_failed")
    finally:
        if _tasks.get(project_id) is task:
            _tasks.pop(project_id, None)
        if _analysis_tasks.get(project_id) is measurement:
            _analysis_tasks.pop(project_id, None)
        remaining = _cancelling[project_id] - 1
        if remaining:
            _cancelling[project_id] = remaining
        else:
            _cancelling.pop(project_id)
            _gpu_projects.discard(project_id)
        project = store.get(project_id)
        if (
            job is not None
            and project.job is not None
            and project.job.id == job.id
            and project.job.status in _ACTIVE
        ):
            _finish(project_id, "cancelled", "cancelled")
    return store.get(project_id)


async def delete(project_id: str) -> None:
    async with admission_lock:
        store.begin_delete(project_id)
    try:
        await cancel(project_id)
        if project_id in _unverified or store.load(project_id).worker is not None:
            raise store.VideoProjectError("worker_identity_unverified")
        shutil.rmtree(store.project_dir(project_id))
    except OSError as exc:
        raise store.VideoProjectError("storage_failed") from exc
    finally:
        store.finish_delete(project_id)


def request_shutdown() -> None:
    store.request_reference_shutdown()
    for task in _tasks.values():
        request_cancel(task)
    for measurement in _analysis_tasks.values():
        if not measurement.done() and not measurement.cancelling():
            measurement.cancel()


async def shutdown() -> None:
    request_shutdown()
    results = await await_cleanup(
        asyncio.gather(
            *(cancel(project_id) for project_id in set(_tasks) | set(_analysis_tasks) | store.reference_project_ids()),
            return_exceptions=True,
        )
    )
    for result in results:
        if isinstance(result, BaseException) and not isinstance(
            result, asyncio.CancelledError
        ):
            logger.error("Video cleanup failed", exc_info=result)


def output_file(project_id: str, *, poster: bool = False) -> Path:
    document = store.load(project_id)
    if not document.published_file:
        raise store.VideoProjectError("not_found")
    path = store.artifact(project_id, document.published_file)
    if poster:
        path = store.artifact(
            project_id, str(Path(document.published_file).with_suffix(".png"))
        )
    if not path.is_file():
        raise store.VideoProjectError("not_found")
    return path


def variant_file(
    project_id: str, shot_id: str, variant_id: str, *, poster: bool = False
) -> Path:
    document = store.load(project_id)
    shot = next((item for item in document.project.shots if item.id == shot_id), None)
    if shot is None or not any(
        item.id == variant_id and item.status == "ready" for item in shot.variants
    ):
        raise store.VideoProjectError("not_found")
    path = variant_path(project_id, shot_id, variant_id)
    if poster:
        path = store.artifact(project_id, f"shots/{shot_id}/{variant_id}.png")
    if not path.is_file():
        raise store.VideoProjectError("not_found")
    return path


async def recover() -> None:
    for project in store.list_projects():
        if project.id in _tasks:
            continue
        document = store.load(project.id)
        job = document.project.job
        if document.worker is None and (
            job is None or job.status not in _ACTIVE and document.pending_export is None
        ):
            continue
        if document.worker is not None:
            identity = document.worker.model_copy(
                update={
                    "receipt": str(store.artifact(project.id, document.worker.receipt))
                }
            )
            if not await terminate_verified(identity):
                _unverified.add(project.id)
                _finish(project.id, "failed", "worker_identity_unverified")
                continue
            _unverified.discard(project.id)
        if job is None:
            _worker(project.id, None)
            continue
        for shot in project.shots:
            for variant in shot.variants:
                if variant.status not in _ACTIVE | {"failed", "cancelled"}:
                    continue
                if await _valid_variant(document, shot, variant, variant.fingerprint):

                    def ready(item: VideoVariant) -> None:
                        item.status = "ready"
                        item.error_code = ""
                        item.duration_sec = shot.seconds
                        item.finished_at = store.now()
                        item.file_url = f"/api/videos/projects/{project.id}/shots/{shot.id}/variants/{item.id}/file"
                        item.poster_url = (
                            f"/api/videos/projects/{project.id}/shots/{shot.id}/variants/{item.id}/poster"
                            if variant_path(project.id, shot.id, item.id)
                            .with_suffix(".png")
                            .is_file()
                            else ""
                        )

                    _set_variant(project.id, shot.id, variant.id, ready)
                elif variant.status in _ACTIVE:

                    def interrupted(item: VideoVariant) -> None:
                        item.status = "failed"
                        item.error_code = "interrupted"

                    _set_variant(project.id, shot.id, variant.id, interrupted)
        pending = document.pending_export
        adopted = False
        if pending is not None:
            try:
                path = store.artifact(project.id, pending.path)
                if (
                    pending.project_revision != project.revision
                    or not pending.output_sha256
                    or await hash_file(path) != pending.output_sha256
                ):
                    raise VideoMediaError()
                await validate_media(
                    path,
                    pending.duration_sec,
                    (pending.width, pending.height),
                    require_audio=True,
                )

                def publish(saved: store.StoredVideoProject) -> None:
                    saved.published_file = pending.path
                    saved.pending_export = None
                    saved.worker = None
                    saved.project.file_url = f"/api/videos/projects/{project.id}/file"
                    if saved.requested_export_settings is not None:
                        saved.project.export_settings = saved.requested_export_settings
                    saved.project.poster_url = (
                        f"/api/videos/projects/{project.id}/poster"
                        if path.with_suffix(".png").is_file()
                        else ""
                    )

                store.mutate(project.id, publish, busy_ok=True, bump=False)
                adopted = True
            except (VideoMediaError, OSError):
                pass
        _finish(
            project.id,
            "ready" if adopted else "failed",
            "" if adopted else "interrupted",
        )
        _worker(project.id, None)
        for partial in store.project_dir(project.id).rglob("*.partial.mp4"):
            if partial.resolve().is_relative_to(
                store.project_dir(project.id).resolve()
            ):
                partial.unlink(missing_ok=True)


async def analyze(project_id: str, body: VideoRevisionRequest) -> VideoProject:
    store.ensure_open(project_id)
    document = store.load(project_id)
    if project_id in _unverified or document.worker is not None:
        raise store.VideoProjectError("worker_identity_unverified")
    if document.project.revision != body.revision:
        raise store.VideoProjectError("revision_conflict")
    if (
        project_id in _analysis_tasks
        or project_id in _tasks
        or project_id in _cancelling
    ):
        raise store.VideoProjectError("busy")
    if store.source_changed(document):
        raise store.VideoProjectError("source_changed")

    async def measure() -> VideoSongAnalysis:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        receipt = store.project_dir(project_id) / "analysis.worker.json"
        try:
            ffmpeg = tool("ffmpeg")
        except VideoMediaError as exc:
            raise store.VideoProjectError("analysis_unavailable") from exc
        proc = await spawn_owned(
            [
                sys.executable,
                "-m",
                "app.video_analysis",
                "--input",
                str(store.source_path(document.project.track_id)),
                "--ffmpeg",
                ffmpeg,
            ],
            receipt_path=receipt,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            on_identity=lambda value: _worker(project_id, value),
        )
        try:
            try:
                out = await read_owned_output(
                    proc, max_bytes=4 * 1024 * 1024, timeout=200
                )
            except TimeoutError as exc:
                raise store.VideoProjectError("analysis_timeout") from exc
            except WorkerOutputError as exc:
                raise store.VideoProjectError("analysis_failed") from exc
            if proc.returncode != 0:
                try:
                    failure = AnalysisFailure.model_validate_json(out)
                except ValidationError:
                    logger.warning("Audio analysis worker failed for %s (exit %s)", project_id, proc.returncode)
                    raise store.VideoProjectError("analysis_failed") from None
                raise store.VideoProjectError(failure.error_code)
            try:
                return VideoSongAnalysis.model_validate_json(out)
            except ValidationError as exc:
                raise store.VideoProjectError("analysis_failed") from exc
        finally:
            await await_cleanup(kill_process_tree(proc))
            if proc.stdin is not None:
                proc.stdin.close()
            _worker(project_id, None)

    task = asyncio.create_task(measure())
    _analysis_tasks[project_id] = task
    try:
        result = await task
        if store.source_changed(document):
            raise store.VideoProjectError("source_changed")

        def change(saved: store.StoredVideoProject) -> None:
            from .video_jobs import propose_plan

            project = saved.project
            project.analysis = result
            manual = [marker for marker in project.markers if marker.kind == "manual"]
            remaining = 4000 - len(manual)
            measured = (
                result.markers
                if len(result.markers) <= remaining
                else [
                    result.markers[index * len(result.markers) // remaining]
                    for index in range(remaining)
                ]
                if remaining
                else []
            )
            project.markers = manual + measured
            if len(measured) < len(result.markers):
                project.warnings = list(
                    dict.fromkeys([*project.warnings, "markers_decimated"])
                )
            plan = propose_plan(
                project.track_title,
                "",
                result.duration_sec,
                planner_energy(result),
                project.direction,
            )
            planned = [
                VideoProjectShot(
                    id=uuid.uuid4().hex,
                    start_sec=float(shot["start_sec"]),
                    seconds=shot["seconds"],
                    prompt=shot["prompt"],
                    seed=(project.seed + index) % 2147483648,
                    reference_id=project.references[0].id
                    if project.mode != "generated" and project.references
                    else None,
                )
                for index, shot in enumerate(plan["shots"])
            ]
            locked = [shot for shot in project.shots if shot.locked]
            if not project.shots:
                project.shots = planned
            else:
                for shot in project.shots:
                    if shot.locked:
                        continue
                    proposal = next(
                        (
                            item
                            for item in planned
                            if item.start_sec
                            <= shot.start_sec
                            < item.start_sec + item.seconds
                        ),
                        None,
                    )
                    if proposal is not None and proposal.prompt != shot.prompt:
                        shot.prompt = proposal.prompt
                        shot.approved_variant_id = None
                        project.file_url = ""
                        project.poster_url = ""
                if locked:
                    project.warnings = list(
                        dict.fromkeys([*project.warnings, "locked_shots_preserved"])
                    )
            project.warnings = list(
                dict.fromkeys([*project.warnings, *result.warnings])
            )

        return store.mutate(project_id, change, revision=body.revision)
    finally:
        _analysis_tasks.pop(project_id, None)
