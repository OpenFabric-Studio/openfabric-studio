"""Short videos for songs in the library. One generation at a time."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends, Request
from typing import Annotated, NoReturn
from fastapi.responses import FileResponse
from pydantic import ValidationError
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


def _raise(exc: VideoJobError) -> NoReturn:
    if exc.code == "busy":
        status = 409
    elif exc.code == "not_found":
        status = 404
    else:
        status = 400
    raise HTTPException(status_code=status, detail=exc.code) from exc


@router.get("", response_model=VideosResponse)
def get_videos() -> VideosResponse:
    return VideosResponse.model_validate({"videos": list_videos()})


@router.get("/activity", response_model=VideoActivityResponse)
async def video_activity() -> VideoActivityResponse:
    return VideoActivityResponse(busy=await other_work_busy())


@router.post("/plan", response_model=VideoPlanResponse)
async def plan_video(body: PlanRequest) -> VideoPlanResponse:
    try:
        return VideoPlanResponse.model_validate(await analyze_song(body.track_id))
    except VideoJobError as exc:
        _raise(exc)


@router.post("", response_model=VideoJobResponse)
async def create_video(body: CreateVideoRequest) -> VideoJobResponse:
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
        return VideoJobResponse.model_validate(await start_video(
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
        ))
    except VideoJobError as exc:
        _raise(exc)


from .. import video_projects as projects, video_render as renders
from ..video_contracts import (
    VideoProject,
    VideoProjectsResponse,
    CreateVideoProjectRequest,
    CreateDialogueReelRequest,
    RefreshDialogueCueRequest,
    UpdateVideoProjectRequest,
    VideoRevisionRequest,
    VideoRenderRequest,
    ApproveVideoVariantRequest,
    VideoExportRequest,
    VideoSpeechLineRequest,
    ApplyVideoCharacterRequest,
    ApplyVideoCharacterAdapterRequest,
    VideoCharacter,
    VideoCharactersResponse,
    VideoCharacterTrainingJob,
    VideoCharacterTrainingResponse,
    VideoCharacterTrainerStatus,
    VideoCharacterTrainerSettingsRequest,
    CharacterDatasetReview,
    ReviewCharacterAdapterRequest,
    VideoReadinessResponse,
)
from .. import video_characters
from .. import video_character_training as character_training


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
            "voice_missing",
        }
        else 403
        if code == "consent_required"
        else 400
    )
    raise HTTPException(status_code=status, detail=code) from exc


@router.get("/readiness", response_model=VideoReadinessResponse)
def video_readiness() -> VideoReadinessResponse:
    return renders.readiness()


@router.post("/dialogue-reels", response_model=VideoProject)
async def create_dialogue_reel(body: CreateDialogueReelRequest) -> VideoProject:
    from .. import video_dialogue
    try:
        return await video_dialogue.create(body)
    except projects.VideoProjectError as exc:
        _project_error(exc)


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


@router.post("/projects/{project_id}/speech/line", response_model=VideoProject)
async def speak_project_line(project_id: str, body: VideoSpeechLineRequest) -> VideoProject:
    try:
        return await projects.speak_line(project_id, body)
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.get("/characters", response_model=VideoCharactersResponse)
def get_characters() -> VideoCharactersResponse:
    return VideoCharactersResponse(characters=video_characters.list_characters())


@router.post("/characters", response_model=VideoCharacter)
async def create_character(
    name: Annotated[str, Form(min_length=1, max_length=80)],
    voice_profile_id: Annotated[str, Form(min_length=32, max_length=32)],
    consent_confirmed: Annotated[bool, Form()],
    file: Annotated[UploadFile, File()],
) -> VideoCharacter:
    try:
        return await video_characters.create_character(
            name=name,
            voice_profile_id=voice_profile_id,
            consent_confirmed=consent_confirmed,
            upload=file,
        )
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.get("/characters/{character_id}/still")
def character_still(character_id: str) -> FileResponse:
    try:
        return FileResponse(video_characters.still_file(character_id), media_type="image/png")
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.delete("/characters/{character_id}")
def remove_character(character_id: str) -> dict[str, str]:
    try:
        video_characters.delete_character(character_id)
        return {"deleted": character_id}
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.get("/character-training/status", response_model=VideoCharacterTrainerStatus)
def character_trainer_status() -> VideoCharacterTrainerStatus:
    return character_training.trainer_status()


@router.put("/character-training/trainer", response_model=VideoCharacterTrainerStatus)
def put_character_trainer(body: VideoCharacterTrainerSettingsRequest) -> VideoCharacterTrainerStatus:
    try:
        return character_training.save_trainer_command(body.command)
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.get("/character-training", response_model=VideoCharacterTrainingResponse)
def character_training_jobs() -> VideoCharacterTrainingResponse:
    return VideoCharacterTrainingResponse(jobs=character_training.list_jobs())


@router.post("/character-training", response_model=VideoCharacterTrainingJob)
async def start_character_training(
    name: Annotated[str, Form(min_length=1, max_length=80)],
    consent_confirmed: Annotated[bool, Form()],
    files: list[UploadFile] = File(...),
    dataset_review: Annotated[str | None, Form(max_length=15000)] = None,
) -> VideoCharacterTrainingJob:
    try:
        return await character_training.create_job(
            name=name,
            consent_confirmed=consent_confirmed,
            uploads=files,
            review=CharacterDatasetReview.model_validate_json(dataset_review) if dataset_review is not None else None,
        )
    except ValidationError as exc:
        raise HTTPException(422, detail="dataset_review_required") from exc
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.post("/character-training/{job_id}/comparison", response_model=VideoCharacterTrainingJob)
def character_comparison(job_id: str) -> VideoCharacterTrainingJob:
    from .. import video_character_comparison
    try:
        return video_character_comparison.create(job_id)
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.post("/character-training/{job_id}/review", response_model=VideoCharacterTrainingJob)
async def review_character(job_id: str, body: ReviewCharacterAdapterRequest) -> VideoCharacterTrainingJob:
    from .. import video_character_comparison
    try:
        return await video_character_comparison.review(job_id, body)
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.post("/character-training/{job_id}/cancel", response_model=VideoCharacterTrainingJob)
async def cancel_character_training(job_id: str) -> VideoCharacterTrainingJob:
    try:
        return await character_training.cancel_job(job_id)
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.post("/projects/{project_id}/character-adapter", response_model=VideoProject)
def apply_project_character_adapter(project_id: str, body: ApplyVideoCharacterAdapterRequest) -> VideoProject:
    try:
        return projects.apply_character_adapter(project_id, body)
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.post("/projects/{project_id}/character", response_model=VideoProject)
def apply_project_character(project_id: str, body: ApplyVideoCharacterRequest) -> VideoProject:
    try:
        return projects.apply_character(project_id, body)
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.get("/projects/{project_id}/speech")
def speech_file(project_id: str) -> FileResponse:
    try:
        return FileResponse(projects.speech_file(project_id), media_type="audio/wav")
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.post("/projects/{project_id}/dialogue-cues/{shot_id}", response_model=VideoProject)
async def refresh_dialogue_cue(project_id: str, shot_id: str, body: RefreshDialogueCueRequest) -> VideoProject:
    from .. import video_dialogue
    try:
        return await video_dialogue.refresh(project_id, shot_id, body)
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.post("/projects/{project_id}/undo", response_model=VideoProject)
def undo_project(project_id: str, body: VideoRevisionRequest) -> VideoProject:
    try:
        return projects.undo(project_id, body)
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.post("/projects/{project_id}/redo", response_model=VideoProject)
def redo_project(project_id: str, body: VideoRevisionRequest) -> VideoProject:
    try:
        return projects.redo(project_id, body)
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
async def cancel_project(project_id: str, request: Request) -> VideoProject:
    try:
        if projects.get(project_id).provider_config.provider == "openrouter":
            require_openrouter_origin(request)
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


@router.get("/projects/{project_id}/shots/{shot_id}/variants/{variant_id}/filmstrip")
def variant_filmstrip(project_id: str, shot_id: str, variant_id: str) -> FileResponse:
    try:
        return FileResponse(renders.filmstrip_file(project_id, shot_id, variant_id), media_type="image/png")
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
def video_file(video_id: str) -> FileResponse:
    try:
        path = output_file(video_id)
    except VideoJobError as exc:
        _raise(exc)
    return FileResponse(path, media_type="video/mp4")


@router.post("/{video_id}/cancel", response_model=VideoJobResponse)
async def cancel(video_id: str) -> VideoJobResponse:
    try:
        return VideoJobResponse.model_validate(await cancel_video(video_id))
    except VideoJobError as exc:
        _raise(exc)


@router.get("/{video_id}", response_model=VideoJobResponse)
def one_video(video_id: str) -> VideoJobResponse:
    try:
        return VideoJobResponse.model_validate(get_video(video_id))
    except VideoJobError as exc:
        _raise(exc)


@router.delete("/{video_id}")
async def remove_video(video_id: str) -> dict[str, str]:
    try:
        await delete_video(video_id)
    except VideoJobError as exc:
        _raise(exc)
    return {"deleted": video_id}


# Paid cloud operations are same-origin only; no browser receives credentials.
from ..module_security import require_local_origin as require_openrouter_origin
from .. import video_cloud
from ..openrouter_errors import OpenRouterError
from ..video_contracts import VideoCloudQuoteRequest, VideoCloudQuoteResponse, VideoCloudSubmitRequest, VideoCloudResumeRequest

@router.post("/projects/{project_id}/cloud/quote", response_model=VideoCloudQuoteResponse, dependencies=[Depends(require_openrouter_origin)])
def quote_cloud_video(project_id: str, body: VideoCloudQuoteRequest) -> VideoCloudQuoteResponse:
    try:
        return video_cloud.quote(project_id, body)
    except OpenRouterError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc
    except projects.VideoProjectError as exc:
        _project_error(exc)

@router.post("/projects/{project_id}/cloud/submit", response_model=VideoProject, dependencies=[Depends(require_openrouter_origin)])
async def submit_cloud_video(project_id: str, body: VideoCloudSubmitRequest) -> VideoProject:
    try:
        return await video_cloud.submit(project_id, body)
    except OpenRouterError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc
    except (projects.VideoProjectError, VideoMediaError) as exc:
        _project_error(projects.VideoProjectError(exc.code))

@router.post("/projects/{project_id}/cloud/resume", response_model=VideoProject, dependencies=[Depends(require_openrouter_origin)])
async def resume_cloud_video(project_id: str, body: VideoCloudResumeRequest) -> VideoProject:
    try:
        return await video_cloud.resume(project_id, body)
    except OpenRouterError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc
    except projects.VideoProjectError as exc:
        _project_error(exc)


@router.post("/projects/{project_id}/character-adapter/clear", response_model=VideoProject)
def clear_project_character_adapter(project_id: str, body: VideoRevisionRequest) -> VideoProject:
    try:
        return projects.clear_character_adapter(project_id, body)
    except projects.VideoProjectError as exc:
        _project_error(exc)
