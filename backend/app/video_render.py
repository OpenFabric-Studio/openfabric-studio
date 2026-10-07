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
_cloud_projects: set[str] = set()
_unverified: set[str] = set()
_analysis_tasks: dict[str, asyncio.Task[VideoSongAnalysis]] = {}
_cancelling: dict[str, int] = {}
_ACTIVE = {"queued", "running"}


class _SourceFormat(VideoContract):
    tags: dict[str,str]=Field(default_factory=dict)


class _SourceMetadata(VideoContract):
    format: _SourceFormat=Field(default_factory=_SourceFormat)


async def _retained_metadata(project_id: str,paths: list[Path]) -> dict[str,str]:
    merged: dict[str,tuple[str,list[str]]]={}
    total=0
    for path in dict.fromkeys(paths):
        raw=await _command(project_id,[tool('ffprobe'),'-v','error','-show_entries','format_tags','-of','json',str(path)],'retaining_metadata',timeout=60)
        try:parsed=_SourceMetadata.model_validate_json(raw)
        except ValidationError as exc:raise store.VideoProjectError('invalid_media') from exc
        for key,value in parsed.format.tags.items():
            total+=len(key.encode())+len(value.encode())
            if total>65536 or len(key)>200 or len(value.encode())>16384 or '=' in key or any(ord(char)<32 for char in key):
                raise store.VideoProjectError('metadata_too_large')
            folded=key.casefold()
            if folded not in merged:merged[folded]=(key,[value])
            elif value not in merged[folded][1]:merged[folded][1].append(value)
    return {original: '; '.join(values) if key=='comment' else values[0] if len(values)==1 else json.dumps(values,ensure_ascii=False)
            for key,(original,values) in merged.items()}


class VariantReceipt(VideoContract):
    variant_id: str
    shot_id: str
    fingerprint: str
    output_sha256: str
    seconds: float
    width: int
    height: int


def work_busy() -> bool:
    from .video_dialogue import work_busy as dialogue_busy
    return bool(_gpu_projects or _cloud_projects or _unverified or dialogue_busy())


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
) -> bytes:
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
    with log_path.open('rb') as output:
        output.seek(0,2)
        output.seek(max(0,output.tell()-1024*1024))
        return output.read(1024*1024)


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
    if project.provider_config.provider == "openrouter":
        return hashlib.sha256((project.provider_config.model_dump_json()+"cloud-silent-v1").encode()).hexdigest()
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



def _publish_checked(project_id: str, change: Callable[[store.StoredVideoProject], None]) -> None:
    from . import voice_profiles
    from .video_dialogue import require_consent
    with store._lock, voice_profiles._LOCK:
        document = store.load(project_id)
        require_consent(document.project)
        store.require_retained_consent(document)
        store.mutate(project_id, change, busy_ok=True, bump=False)


def _prompt(project: VideoProject, shot: VideoProjectShot) -> str:
    return " ".join((project.direction + " " + shot.prompt).split())


def fitted_speech_filter(duration: float) -> str:
    """Pad or trim an attached speech clip to the picture. It is not a model input."""
    if not math.isfinite(duration) or duration <= 0:
        raise store.VideoProjectError("invalid_speech")
    return f"apad=whole_dur={duration:.6f},atrim=0:{duration:.6f}"


def generation_audio(project: VideoProject, source: Path | None) -> Path | None:
    """Songs use the library track. A speech clip is never a model input."""
    if project.track_id is None:
        return None
    return source


def generation_mode(project: VideoProject, has_still: bool) -> Literal["a2v", "i2v", "t2v"]:
    """Songs stay audio-to-video. A picture project uses one still, or text alone."""
    if project.track_id is not None:
        return "a2v"
    return "i2v" if has_still else "t2v"


def _still_id(project: VideoProject, shot: VideoProjectShot) -> str | None:
    return store.effective_still(project, shot)[0]


def fingerprint(
    document: store.StoredVideoProject, shot: VideoProjectShot, seed: int, engine: str
) -> str:
    project = document.project
    reference = _still_id(project, shot)
    reference_hash = (
        store.file_hash(store.reference_file(project.id, reference))
        if reference is not None
        else ""
    )
    value = {
        "source": document.source.sha256 if document.source is not None else "",
        "engine": engine,
        "mode": project.mode,
        "settings": project.settings.model_dump(),
        "shot": shot.model_dump(exclude={"variants", "approved_variant_id", "locked"}),
        "effective_prompt": _prompt(project, shot),
        "seed": seed,
        "reference_sha256": reference_hash,
    }
    if project.provider_config.provider == "openrouter":
        value["provider_config"] = project.provider_config.model_dump()
    cue = next((item for item in project.dialogue_cues if item.shot_id == shot.id), None)
    if cue is not None:
        value["dialogue_identity"] = cue.model_dump(exclude={"waveform_peaks"})
    if project.character_lock and project.track_id is None:
        _still, strength = store.effective_still(project, shot)
        value["character_lock"] = True
        value["locked_reference"] = _still or ""
        value["locked_strength"] = strength
    if project.track_id is None and project.character_adapter_id:
        from .video_character_training import ready_adapter_file

        adapter = ready_adapter_file(project.character_adapter_id, project.settings.engine_pack)
        value["character_adapter_id"] = project.character_adapter_id
        value["character_adapter_sha256"] = store.file_hash(adapter) if adapter is not None else ""
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
    if document.project.provider_config.provider == "openrouter":
        cloud = variant.cloud
        if (
            cloud is None
            or variant.provider_config != document.project.provider_config
            or cloud.receipt.state != "completed"
            or cloud.receipt.remote_id is None
            or cloud.receipt.owner_id != f"video-{document.project.id}-{shot.id}-{variant.id}"
            or cloud.receipt.model_id != document.project.provider_config.model_id
            or cloud.source_duration_sec is None
            or cloud.received_sha256 is None
            or cloud.slot_duration_sec != shot.seconds
            or cloud.remote_duration_sec < cloud.slot_duration_sec
            or cloud.remote_duration_sec != cloud.slot_duration_sec and not cloud.trim_confirmed
        ):
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
            document.project.frame_size,
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



async def _filmstrip(project_id: str, clip: Path, dest: Path, seconds: float) -> None:
    from PIL import Image
    partial = dest.with_name(f".{dest.stem}-{uuid.uuid4().hex}.partial.png")
    try:
        await _command(project_id, [tool("ffmpeg"), "-v", "error", "-y", "-i", str(clip),
            "-vf", f"fps=5/{seconds},scale=160:90:force_original_aspect_ratio=decrease,pad=160:90:(ow-iw)/2:(oh-ih)/2,tile=5x1:nb_frames=5",
            "-frames:v", "1", str(partial)], "poster")
        with Image.open(partial) as image:
            image.load()
            if image.size != (800, 90):
                raise store.VideoProjectError("filmstrip_invalid")
        partial.replace(dest)
    except OSError as exc:
        raise store.VideoProjectError("filmstrip_invalid") from exc
    finally:
        partial.unlink(missing_ok=True)


def filmstrip_file(project_id: str, shot_id: str, variant_id: str) -> Path:
    clip = variant_file(project_id, shot_id, variant_id)
    path = clip.with_name(clip.stem + ".filmstrip.png")
    if path.is_symlink() or not path.is_file():
        raise store.VideoProjectError("not_found")
    return path


async def _cpu_shot(
    project: VideoProject,
    shot: VideoProjectShot,
    seed: int,
    source: Path | None,
    dest: Path,
    variant_id: str,
) -> None:
    if source is None:
        raise store.VideoProjectError("song_required")
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
    source: Path | None,
) -> VideoVariant:
    project = document.project
    source = generation_audio(project, source)
    still, strength = store.effective_still(project, shot)
    locked = project.character_lock and project.track_id is None
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
        reference_id=still if locked else shot.reference_id,
        reference_strength=strength if locked else shot.reference_strength,
        source_fingerprint=document.source.sha256 if document.source is not None else "",
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
            references = (
                (
                    ImageReference(
                        store.reference_file(project.id, still),
                        0,
                        strength,
                    ),
                )
                if still is not None
                else ()
            )
            mode = generation_mode(project, bool(references))
            excerpt: Path | None = None
            # A talking clip is never a model input. Songs stay on the a2v path.
            if mode == "a2v":
                if source is None:
                    raise store.VideoProjectError("source_missing")
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
            frames = shot.seconds * 24 + 1
            adapter = None
            if project.track_id is None and mode != "a2v":
                from .video_character_training import ready_adapter_file

                adapter = ready_adapter_file(project.character_adapter_id, project.settings.engine_pack)
            settings = RenderSettings(
                output=partial,
                prompt=_prompt(project, shot),
                frames=frames,
                source_audio=excerpt,
                references=references,
                profile_id=project.settings.engine_pack,
                mode=mode,
                width=project.settings.width,
                height=project.settings.height,
                seed=seed,
                stage1_steps=project.settings.stage1_steps,
                stage2_steps=project.settings.stage2_steps,
                cfg_scale=project.settings.cfg_scale,
                negative_prompt=project.settings.negative_prompt or None,
                temporal_tiles=2 if frames > 145 else 1,
                spatial_tiles=2 if max(project.settings.width, project.settings.height) >= 1280 else 1,
                adapter=adapter,
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
        strip = path.with_name(path.stem + ".filmstrip.png")
        try:
            await _filmstrip(project.id, path, strip, shot.seconds)
        except store.VideoProjectError:
            logger.warning("Optional shot filmstrip unavailable", exc_info=True)

        def ready(item: VideoVariant) -> None:
            item.status = "ready"
            item.finished_at = store.now()
            item.duration_sec = shot.seconds
            item.file_url = f"/api/videos/projects/{project.id}/shots/{shot.id}/variants/{variant_id}/file"
            item.poster_url = f"/api/videos/projects/{project.id}/shots/{shot.id}/variants/{variant_id}/poster"
            if strip.is_file():
                item.filmstrip_url = f"/api/videos/projects/{project.id}/shots/{shot.id}/variants/{variant_id}/filmstrip"
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
    # 704×1280 is already a vertical frame the engine accepts. Do not crop it.
    if settings.aspect == "portrait" and (width, height) == (704, 1280):
        return (704, 1280)
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
    source: Path | None,
    engine: str,
    settings: VideoExportSettings,
) -> None:
    from .video_jobs import timeline_duration, _concat_list

    project = document.project
    job = project.job
    if job is None or not project.shots:
        raise store.VideoProjectError("no_shots")
    if project.track_id is not None and source is None:
        raise store.VideoProjectError("source_missing")
    if project.track_id is None:
        source = None
    speech: Path | None = None
    if project.track_id is None and settings.attach_speech:
        if not document.speech_path:
            raise store.VideoProjectError("speech_missing")
        speech = store.artifact(project.id, document.speech_path)
        if not speech.is_file():
            raise store.VideoProjectError("speech_missing")
    soundtrack = source if source is not None else speech
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
        settings, *project.frame_size
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
    from .video_text import font_identity
    from . import export_provenance, audio_quality
    components=export_provenance.video_components(document,attach_speech=settings.attach_speech)
    sources=[variant_path(project.id,shot.id,variant.id) for shot in project.shots for variant in shot.variants
        if variant.id==shot.approved_variant_id and variant.status=='ready']
    if soundtrack is not None:sources.append(soundtrack)
    retained_tags=await _retained_metadata(project.id,sources)
    previous_comment=next((value for key,value in retained_tags.items() if key.casefold()=='comment'),'')
    export_overlays=list(project.overlays) if settings.include_overlays else []
    visible_label=False
    if settings.visible_ai_label and any(item.role!='conditioning_reference' and item.content_origin in {'generated','mixed'} for item in components):
        from .video_contracts import VideoOverlay
        label='AI-generated' if export_provenance.origin(components)=='generated' else 'Contains AI-generated content'
        export_overlays.append(VideoOverlay(id=uuid.uuid4().hex,kind='title',text=label,start_sec=0,end_sec=duration,
            font_size=max(14,min(28,round(width*.035))),position='top'))
        visible_label=True
    caption_font = font_identity() if export_overlays else ""
    async def quality_command(command: list[str],timeout: float) -> bytes:
        return await _command(project.id,command,'audio_quality',timeout=timeout)
    if soundtrack is not None and settings.loudness.target is not None:
        fitted=run/'programme.wav'
        await _command(project.id,[tool('ffmpeg'),'-v','error','-nostdin','-y','-i',str(soundtrack),'-vn','-af',
            fitted_speech_filter(duration) if speech is not None else f'atrim=duration={duration}',
            '-c:a','pcm_f32le',str(fitted)],'preparing_soundtrack')
        measured=await audio_quality.measure(fitted,run,quality_command)
        processing=audio_quality.normalization_filter(settings.loudness,measured)
        if processing is not None:
            normalized_audio=run/'programme.normalized.wav'
            await _command(project.id,[tool('ffmpeg'),'-v','info','-nostdin','-y','-i',str(fitted),'-af',processing,
                '-ar','48000','-c:a','pcm_f32le',str(normalized_audio)],'normalizing_soundtrack')
            soundtrack=normalized_audio
    export_fingerprint = hashlib.sha256(
        (
            project.model_dump_json(
                exclude={"job", "updated_at", "file_url", "poster_url"}
            )
            + settings.model_dump_json()
            + engine
            + caption_font
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
        ]
        if soundtrack is not None:
            argv += ["-i", str(soundtrack)]
        if export_overlays:
            from .video_text import render_text

            chain = "[0:v]null[v0]"
            last = "v0"
            input_index = 2 if soundtrack is not None else 1
            for index, overlay in enumerate(export_overlays):
                image = run / f"text_{index}.png"
                render_text(overlay, width, height, image)
                argv += ["-i", str(image)]
                chain += f";[{last}][{input_index}:v]overlay=0:0:enable='between(t,{overlay.start_sec},{overlay.end_sec})'[v{index + 1}]"
                last = f"v{index + 1}"
                input_index += 1
            if speech is not None:
                chain += f";[1:a]{fitted_speech_filter(duration)}[aout]"
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
            if soundtrack is not None:
                argv += ["-map", "[aout]" if speech is not None else "1:a:0", "-c:a", "aac"]
            else:
                argv += ["-an"]
        else:
            argv += ["-map", "0:v:0", "-c:v", "copy"]
            if soundtrack is not None:
                argv += ["-map", "1:a:0"]
                if speech is not None:
                    argv += ["-af", fitted_speech_filter(duration)]
                argv += ["-c:a", "aac"]
            else:
                argv += ["-an"]
        argv += [
            *(part for key,value in retained_tags.items() if key.casefold() not in {'comment','openfabric_content_origin','openfabric_export_id'} for part in ('-metadata',f'{key}={value}')),
            *export_provenance.metadata_args(job.id,export_provenance.origin(components),previous_comment),
            '-movflags','+use_metadata_tags',
            "-t",
            str(duration),
            "-f",
            "mp4",
            str(partial),
        ]
        await _command(project.id, argv, "export")
        await validate_media(
            partial, duration, (width, height), require_audio=soundtrack is not None
        )
        metrics=None
        if soundtrack is not None:
            try:metrics=(await audio_quality.measure(partial,run,quality_command,name='export')).metrics
            except audio_quality.AudioQualityError:
                logger.warning('Video export audio measurement unavailable',exc_info=True)
            except store.VideoProjectError as exc:
                if exc.code!='processing_failed':raise
                logger.warning('Video export audio measurement process failed',exc_info=True)
        target=audio_quality.target_result(settings.loudness,metrics)
        provenance=export_provenance.manifest(job.id,'video',partial,components,
            transformations=[f'assemble:{width}x{height}:24fps',f'loudness:{settings.loudness.profile}:result={target}'],
            measured_audio=metrics,visible_ai_label=visible_label,audio_target=settings.loudness,audio_target_result=target)
        output_hash = await hash_file(partial)

        def verified(saved: store.StoredVideoProject) -> None:
            if saved.pending_export is not None:
                saved.pending_export.output_sha256 = output_hash
                saved.pending_export.provenance=provenance

        store.mutate(project.id, verified, busy_ok=True, bump=False)
        partial.replace(output)
        export_provenance.write(output,provenance)
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
            saved.project.export_provenance=provenance
            saved.project.provenance_url=f'/api/videos/projects/{project.id}/provenance'
            saved.project.manifest_url=f'/api/videos/projects/{project.id}/manifest'
            saved.project.warnings=[code for code in saved.project.warnings if code not in {'loudness_target_warning','loudness_target_inconclusive'}]
            if target in {'warning','inconclusive'}:
                saved.project.warnings=list(dict.fromkeys([*saved.project.warnings,'loudness_target_'+target]))

        _publish_checked(project.id, publish)
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
        source: Path | None = None
        if project.track_id is not None:
            if document.source is None:
                raise store.VideoProjectError("source_missing")
            source = run / ("source" + Path(document.source.path).suffix)
            await copy_verified(
                store.source_path(project.track_id), source, document.source.sha256
            )
        store.atomic_text(run / "review.json", document.model_dump_json())

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
        from .resource_admission import require_setup_idle
        require_setup_idle()
        store.ensure_open(project_id)
        document = store.load(project_id)
        project = document.project
        from .video_dialogue import require_consent
        require_consent(project)
        store.require_retained_consent(document)
        if document.worker is not None:
            raise store.VideoProjectError("worker_identity_unverified")
        if body.revision != project.revision:
            raise store.VideoProjectError("revision_conflict")
        if project.provider_config.provider == "openrouter" and operation != "export":
            raise store.VideoProjectError("cloud_quote_required")
        needs_gpu = project.mode == "generated" and project.provider_config.provider == "local" and operation != "export"
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
        if operation != "export":
            store.ensure_character_lock(project)
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
        if project.track_id is not None and effective_export.attach_speech:
            raise store.VideoProjectError("speech_picture_only")
        if (
            project.track_id is None
            and effective_export.attach_speech
            and operation != "preview"
            and not document.speech_path
        ):
            raise store.VideoProjectError("speech_missing")
        if operation != "preview" and effective_export.include_overlays and project.overlays:
            try:
                import PIL
                from .video_text import validate_text
            except ImportError as exc:
                raise store.VideoProjectError("image_tools_unavailable") from exc
            width, height = aspect_size(effective_export, *project.frame_size)
            for overlay in project.overlays:
                validate_text(overlay, width, height)
        if project.mode == "generated" and project.provider_config.provider == "local":
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
        elif project.mode != "generated" and not project.references:
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
            saved.cloud_variant_id = None

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


async def cancel(project_id: str, *, stop_cloud_tracking: bool = True) -> VideoProject:
    tracking_failed = False
    async with admission_lock:
        task = _tasks.get(project_id)
        measurement = _analysis_tasks.get(project_id)
        job = store.get(project_id).job
        _cancelling[project_id] = _cancelling.get(project_id, 0) + 1
        references = store.request_reference_cancel(project_id)
        from .video_dialogue import request_cancel as cancel_dialogue
        dialogue_tasks = cancel_dialogue(project_id)
        if stop_cloud_tracking:
            from .video_cloud import stop_tracking
            from .openrouter_errors import OpenRouterError
            try:
                stop_tracking(project_id)
            except (OpenRouterError, store.VideoProjectError):
                # A failed ledger write cannot prevent owned network/process draining.
                tracking_failed = True
                logger.warning("Cloud tracking ledger could not be updated", exc_info=True)
        request_cancel(task)
        if (
            measurement is not None
            and not measurement.done()
            and not measurement.cancelling()
        ):
            measurement.cancel()
    try:
        pending: list[asyncio.Task[None] | asyncio.Task[VideoSongAnalysis] | asyncio.Task[VideoProject]] = list(dialogue_tasks)
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
        if tracking_failed:
            _finish(project_id, "cancelled", "cloud_tracking_storage_failed")
            raise store.VideoProjectError("cloud_tracking_storage_failed")
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
            _cloud_projects.discard(project_id)
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
    from .video_dialogue import request_shutdown as stop_dialogue
    stop_dialogue()
    store.request_reference_shutdown()
    for task in _tasks.values():
        request_cancel(task)
    for measurement in _analysis_tasks.values():
        if not measurement.done() and not measurement.cancelling():
            measurement.cancel()


async def shutdown() -> None:
    from .video_dialogue import shutdown as shutdown_dialogue
    request_shutdown()
    results = await await_cleanup(
        asyncio.gather(
            shutdown_dialogue(),
            *(cancel(project_id, stop_cloud_tracking=False) for project_id in set(_tasks) | set(_analysis_tasks) | store.reference_project_ids()),
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
    store.require_retained_consent(document)
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


def provenance_file(project_id: str,format: Literal['json','txt']) -> Path:
    from . import export_provenance
    document=store.load(project_id)
    media=output_file(project_id)
    value=document.project.export_provenance
    if value is None:raise store.VideoProjectError('manifest_unavailable')
    try:export_provenance.write(media,value)
    except (OSError,export_provenance.ProvenanceError) as exc:
        raise store.VideoProjectError('manifest_unavailable') from exc
    return export_provenance.path_for(media,format)


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
    from .video_dialogue import recover as recover_dialogue
    await recover_dialogue()
    for project in store.list_projects():
        if project.id in _tasks:
            continue
        document = store.load(project.id)
        job = document.project.job
        if document.worker is None:
            from .video_cloud import recover as recover_cloud
            if await recover_cloud(project.id):
                continue
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
            _worker(project.id, None)
            from .video_cloud import recover as recover_cloud
            if await recover_cloud(project.id):
                continue
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
        recovery_error = 'interrupted'
        if pending is not None:
            from . import export_provenance
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
                    require_audio=document.source is not None or bool(document.speech_path and (document.requested_export_settings or project.export_settings).attach_speech),
                )
                if pending.provenance is not None:
                    from . import export_provenance
                    export_provenance.write(path,pending.provenance)

                def publish(saved: store.StoredVideoProject) -> None:
                    saved.published_file = pending.path
                    saved.pending_export = None
                    saved.worker = None
                    saved.project.file_url = f"/api/videos/projects/{project.id}/file"
                    saved.project.export_provenance=pending.provenance
                    if pending.provenance is not None and pending.provenance.audio_target_result is not None:
                        saved.project.warnings=[code for code in saved.project.warnings if code not in {'loudness_target_warning','loudness_target_inconclusive'}]
                        if pending.provenance.audio_target_result in {'warning','inconclusive'}:
                            saved.project.warnings.append('loudness_target_'+pending.provenance.audio_target_result)
                    if pending.provenance is not None:
                        saved.project.provenance_url=f'/api/videos/projects/{project.id}/provenance'
                        saved.project.manifest_url=f'/api/videos/projects/{project.id}/manifest'
                    if saved.requested_export_settings is not None:
                        saved.project.export_settings = saved.requested_export_settings
                    saved.project.poster_url = (
                        f"/api/videos/projects/{project.id}/poster"
                        if path.with_suffix(".png").is_file()
                        else ""
                    )

                _publish_checked(project.id, publish)
                adopted = True
            except store.VideoProjectError as exc:
                recovery_error = exc.code
            except (VideoMediaError, OSError, export_provenance.ProvenanceError):
                pass
        _finish(
            project.id,
            "ready" if adopted else "failed",
            "" if adopted else recovery_error,
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
    track_id = document.project.track_id
    if track_id is None or document.source is None:
        raise store.VideoProjectError("no_song")
    if store.source_changed(document):
        raise store.VideoProjectError("source_changed")
    song = store.source_path(track_id)

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
                str(song),
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
