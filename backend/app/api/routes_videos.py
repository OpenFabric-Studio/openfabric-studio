"""Short videos for songs in the library. One generation at a time."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from typing import Annotated, NoReturn
from fastapi.responses import FileResponse
from ..video_media import VideoMediaError

from ..video_jobs import (
    VideoJobError,
    analyze_song,
    cancel_video,
    delete_video,
    get_video,
    list_videos,
    output_file,
    start_video,
)
from ..work_busy import other_work_busy

from ..client_contracts import (
    VideoActivityResponse,
    VideoJobResponse,
    VideoPlanResponse,
    VideosResponse,
)

from ..client_contracts import CreateVideoRequest, PlanRequest

router = APIRouter(prefix="/api/videos", tags=["videos"])


def _raise(exc: VideoJobError) -> None:
    if exc.code == "busy":
        status = 409
    elif exc.code == "not_found":
        status = 404
    else:
        status = 400
    raise HTTPException(status_code=status, detail=exc.code) from exc


@router.get("", response_model=VideosResponse)
def get_videos():
    return {"videos": list_videos()}


@router.get("/activity", response_model=VideoActivityResponse)
async def video_activity():
    return {"busy": await other_work_busy()}


@router.post("/plan", response_model=VideoPlanResponse)
async def plan_video(body: PlanRequest):
    try:
        return await analyze_song(body.track_id)
    except VideoJobError as exc:
        _raise(exc)


@router.post("", response_model=VideoJobResponse)
async def create_video(body: CreateVideoRequest):
    shots = None
    if body.shots:
        shots = [
            {
                "start_sec": shot.start_sec,
                "seconds": shot.seconds,
                "prompt": shot.prompt,
            }
            for shot in body.shots
        ]
    try:
        return await start_video(
            body.track_id,
            body.prompt,
            body.seconds,
            body.start_sec,
            shots,
            body.stage1_steps,
            body.stage2_steps,
            body.cfg_scale,
            body.width,
            body.height,
        )
    except VideoJobError as exc:
        _raise(exc)


from .. import video_projects as projects, video_render as renders
from ..video_contracts import (
    VideoProject,
    VideoProjectsResponse,
    CreateVideoProjectRequest,
    UpdateVideoProjectRequest,
    VideoRevisionRequest,
    VideoRenderRequest,
    ApproveVideoVariantRequest,
    VideoExportRequest,
    VideoReadinessResponse,
)


def _project_error(exc: projects.VideoProjectError) -> NoReturn:
    code = exc.code
    status = (
        409
        if code
        in {
            "busy",
            "revision_conflict",
            "source_changed",
            "stale_variant",
            "worker_identity_unverified",
        }
        else 404
        if code
        in {
            "not_found",
            "no_track",
            "shot_not_found",
            "variant_not_found",
            "reference_not_found",
        }
        else 400
    )
    raise HTTPException(status_code=status, detail=code) from exc


@router.get("/readiness", response_model=VideoReadinessResponse)
def video_readiness() -> VideoReadinessResponse:
    return renders.readiness()


@router.get("/projects", response_model=VideoProjectsResponse)
def list_projects() -> VideoProjectsResponse:
    return VideoProjectsResponse(projects=projects.list_projects())


@router.post("/projects", response_model=VideoProject)
async def create_project(body: CreateVideoProjectRequest) -> VideoProject:
    try:
        return await projects.create(body)
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.get("/projects/{project_id}", response_model=VideoProject)
def get_project(project_id: str) -> VideoProject:
    try:
        return projects.get(project_id)
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.patch("/projects/{project_id}", response_model=VideoProject)
def update_project(project_id: str, body: UpdateVideoProjectRequest) -> VideoProject:
    try:
        return projects.update(project_id, body)
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str) -> dict[str, str]:
    try:
        await renders.delete(project_id)
        return {"deleted": project_id}
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.post("/projects/{project_id}/duplicate", response_model=VideoProject)
def duplicate_project(project_id: str, body: VideoRevisionRequest) -> VideoProject:
    try:
        return projects.duplicate(project_id, body)
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.post("/projects/{project_id}/references", response_model=VideoProject)
async def upload_reference(
    project_id: str,
    revision: Annotated[int, Form(ge=1)],
    file: Annotated[UploadFile, File()],
) -> VideoProject:
    try:
        return await projects.upload_reference(project_id, revision, file)
    except projects.VideoProjectError as exc:
        _project_error(exc)
    except VideoMediaError as exc:
        code = "ffmpeg_missing" if exc.code == "ffmpeg_missing" else "invalid_reference"
        _project_error(projects.VideoProjectError(code))


@router.get("/projects/{project_id}/references/{reference_id}")
def reference_file(project_id: str, reference_id: str) -> FileResponse:
    try:
        return FileResponse(
            projects.reference_file(project_id, reference_id), media_type="image/png"
        )
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.post("/projects/{project_id}/speech", response_model=VideoProject)
async def upload_speech(
    project_id: str,
    revision: Annotated[int, Form(ge=1)],
    file: Annotated[UploadFile, File()],
) -> VideoProject:
    try:
        return await projects.upload_speech(project_id, revision, file)
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.post("/projects/{project_id}/speech/clear", response_model=VideoProject)
def clear_speech(project_id: str, body: VideoRevisionRequest) -> VideoProject:
    try:
        return projects.clear_speech(project_id, body)
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.get("/projects/{project_id}/speech")
def speech_file(project_id: str) -> FileResponse:
    try:
        return FileResponse(projects.speech_file(project_id), media_type="audio/wav")
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.post("/projects/{project_id}/analyze", response_model=VideoProject)
async def analyze_project(project_id: str, body: VideoRevisionRequest) -> VideoProject:
    try:
        return await renders.analyze(project_id, body)
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.post("/projects/{project_id}/preview", response_model=VideoProject)
async def preview_project(project_id: str, body: VideoRenderRequest) -> VideoProject:
    try:
        return await renders.start(project_id, body, operation="preview")
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.post("/projects/{project_id}/render", response_model=VideoProject)
async def render_project(project_id: str, body: VideoRenderRequest) -> VideoProject:
    try:
        return await renders.start(project_id, body, operation="render")
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.post("/projects/{project_id}/resume", response_model=VideoProject)
async def resume_project(project_id: str, body: VideoRevisionRequest) -> VideoProject:
    try:
        return await renders.resume(project_id, body)
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.post("/projects/{project_id}/cancel", response_model=VideoProject)
async def cancel_project(project_id: str) -> VideoProject:
    try:
        return await renders.cancel(project_id)
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.post(
    "/projects/{project_id}/shots/{shot_id}/approve", response_model=VideoProject
)
async def approve_variant(
    project_id: str, shot_id: str, body: ApproveVideoVariantRequest
) -> VideoProject:
    try:
        return await renders.approve(project_id, shot_id, body)
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.post("/projects/{project_id}/export", response_model=VideoProject)
async def export_project(project_id: str, body: VideoExportRequest) -> VideoProject:
    try:
        return await renders.export(project_id, body)
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.get("/projects/{project_id}/file")
def project_file(project_id: str) -> FileResponse:
    try:
        return FileResponse(
            renders.output_file(project_id),
            media_type="video/mp4",
            filename="video.mp4",
        )
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.get("/projects/{project_id}/poster")
def project_poster(project_id: str) -> FileResponse:
    try:
        return FileResponse(
            renders.output_file(project_id, poster=True), media_type="image/png"
        )
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.get("/projects/{project_id}/shots/{shot_id}/variants/{variant_id}/file")
def variant_file(project_id: str, shot_id: str, variant_id: str) -> FileResponse:
    try:
        return FileResponse(
            renders.variant_file(project_id, shot_id, variant_id),
            media_type="video/mp4",
        )
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.get("/projects/{project_id}/shots/{shot_id}/variants/{variant_id}/poster")
def variant_poster(project_id: str, shot_id: str, variant_id: str) -> FileResponse:
    try:
        return FileResponse(
            renders.variant_file(project_id, shot_id, variant_id, poster=True),
            media_type="image/png",
        )
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.get("/{video_id}/file")
def video_file(video_id: str):
    try:
        path = output_file(video_id)
    except VideoJobError as exc:
        _raise(exc)
    return FileResponse(path, media_type="video/mp4")


@router.post("/{video_id}/cancel", response_model=VideoJobResponse)
async def cancel(video_id: str):
    try:
        return await cancel_video(video_id)
    except VideoJobError as exc:
        _raise(exc)


@router.get("/{video_id}", response_model=VideoJobResponse)
def one_video(video_id: str):
    try:
        return get_video(video_id)
    except VideoJobError as exc:
        _raise(exc)


@router.delete("/{video_id}")
async def remove_video(video_id: str):
    try:
        await delete_video(video_id)
    except VideoJobError as exc:
        _raise(exc)
    return {"deleted": video_id}
