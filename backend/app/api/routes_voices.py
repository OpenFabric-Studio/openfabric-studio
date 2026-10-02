"""Local singing voices: recordings stay under the Remiqora data directory."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from ..atomic_files import document_lock

from ..voice_build import (
    ALLOWED_AUDIO_EXT,
    new_voice_meta,
    preview_path,
    public_voice,
    read_meta,
    release_voice,
    start_apply,
    start_build,
    voice_dir,
    voices_root,
    write_meta,
    apply_status,
    cancel_build,
    list_public,
    resolve_reference_artifact,
    select_model,
    cancel_apply,
)
from .. import voice_preparation
from ..voice_contracts import AnalyzeVoiceCoverageRequest, PrepareVoiceRequest, VoicePreparationResponse, SelectVoiceSamplesRequest, SelectVoiceModelRequest

from ..client_contracts import ApplyStatusResponse, VoiceProfileResponse, VoicesResponse, VoiceUploadResponse, VoiceReplacementResponse

from ..client_contracts import ApplyVoiceRequest, BuildVoiceRequest, CreateVoiceRequest
from ..voice_replacement import ReplacementRoute

router = APIRouter(prefix="/api/voices", tags=["voices"], route_class=ReplacementRoute)


def _unique_dest(target_dir: Path, filename: str) -> Path:
    stem, _, ext = filename.rpartition(".")
    dest = target_dir / filename
    n = 1
    while dest.exists() or dest.is_symlink():
        dest = target_dir / f"{stem}_{n}.{ext}"
        n += 1
    return dest


@router.get("", response_model=VoicesResponse)
def list_voices():
    return {"voices": list_public()}


@router.post("", response_model=VoiceProfileResponse)
def create_voice(body: CreateVoiceRequest):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Voice name is required")
    voice_id = uuid.uuid4().hex
    path = voices_root() / voice_id
    (path / "recordings").mkdir(parents=True)
    write_meta(path, new_voice_meta(voice_id, name))
    return public_voice(voice_id)


@router.post("/apply", response_model=ApplyStatusResponse)
async def apply_voice(body: ApplyVoiceRequest):
    return start_apply(body.voice_id, body.track_id)


@router.get("/apply/{track_id}", response_model=ApplyStatusResponse)
def get_apply(track_id: int):
    return apply_status(track_id)


@router.post('/apply/{track_id}/cancel', response_model=ApplyStatusResponse)
async def cancel_voice_application(track_id: int) -> ApplyStatusResponse:
    await cancel_apply(track_id)
    return ApplyStatusResponse.model_validate(apply_status(track_id))


@router.post('/replace', response_model=VoiceReplacementResponse)
async def replace_voice(audio: Annotated[UploadFile, File()],
                        voice_id: Annotated[str, Form(pattern=r'^[0-9a-f]{32}$')]) -> VoiceReplacementResponse:
    from ..voice_replacement import replace_voice as accept
    return await accept(audio, voice_id)


@router.post("/{voice_id}/recordings", response_model=VoiceUploadResponse)
async def upload_recordings(voice_id: str, files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    path = voice_dir(voice_id)
    target = path / "recordings"
    target.mkdir(exist_ok=True)
    if not target.resolve().is_relative_to(path.resolve()):
        raise HTTPException(status_code=400, detail='invalid_source')
    saved: list[str] = []
    skipped: list[str] = []
    for upload in files:
        filename = Path((upload.filename or "").replace('\\', '/')).name
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if not filename or ext not in ALLOWED_AUDIO_EXT:
            skipped.append(upload.filename or "(unnamed)")
            continue
        dest = _unique_dest(target, filename)
        with open(dest, "xb") as out:
            shutil.copyfileobj(upload.file, out)
        saved.append(dest.name)
    if not saved:
        raise HTTPException(
            status_code=400,
            detail=f"No supported audio files in upload. Allowed: {', '.join(sorted(ALLOWED_AUDIO_EXT))}",
        )
    with document_lock(path / "voice.json"):
        meta = read_meta(path)
        recordings = meta.get("recordings")
        if not isinstance(recordings, list):
            raise HTTPException(status_code=409, detail="invalid_voice_metadata")
        recordings.extend({"filename": name, "bytes": (target / name).stat().st_size} for name in saved)
        write_meta(path, meta)
    return {"voice": public_voice(voice_id), "saved": saved, "skipped": skipped}


@router.post("/{voice_id}/build", response_model=VoiceProfileResponse)
async def build_voice(voice_id: str, body: BuildVoiceRequest):
    return start_build(voice_id, clean=body.clean, preparation_revision=body.preparation_revision,
        training_steps=body.training_steps, resume=body.resume, compare_checkpoints=body.compare_checkpoints)


@router.post('/{voice_id}/prepare', response_model=VoicePreparationResponse)
async def prepare_voice(voice_id: str, body: PrepareVoiceRequest) -> VoicePreparationResponse:
    view = public_voice(voice_id)
    if view['status'] in {'queued', 'extracting', 'cleaning', 'preparing', 'merging', 'training'}:
        raise HTTPException(status_code=409, detail='build_active')
    return voice_preparation.start(voice_dir(voice_id), body)


@router.get('/{voice_id}/preparation', response_model=VoicePreparationResponse)
async def get_preparation(voice_id: str) -> VoicePreparationResponse:
    return voice_preparation.get(voice_dir(voice_id))


@router.post('/{voice_id}/coverage', response_model=VoicePreparationResponse)
async def analyze_coverage(voice_id: str, body: AnalyzeVoiceCoverageRequest) -> VoicePreparationResponse:
    view = public_voice(voice_id)
    if view['status'] in {'queued', 'extracting', 'cleaning', 'preparing', 'merging', 'training'}:
        raise HTTPException(status_code=409, detail='build_active')
    return voice_preparation.start_coverage(voice_dir(voice_id), body)


@router.post('/{voice_id}/prepare/cancel', response_model=VoicePreparationResponse)
async def cancel_preparation(voice_id: str) -> VoicePreparationResponse:
    return await voice_preparation.cancel(voice_dir(voice_id))


@router.patch('/{voice_id}/selection', response_model=VoicePreparationResponse)
async def select_samples(voice_id: str, body: SelectVoiceSamplesRequest) -> VoicePreparationResponse:
    return voice_preparation.select(voice_dir(voice_id), body)


@router.get('/{voice_id}/samples/{segment_id}')
async def get_sample(voice_id: str, segment_id: str, variant: Literal['original', 'cleaned'] = 'original') -> FileResponse:
    return FileResponse(voice_preparation.sample_path(voice_dir(voice_id), segment_id, variant), media_type='audio/wav')


@router.get('/{voice_id}/reference/{reference_id}')
async def get_reference(voice_id: str, reference_id: str) -> FileResponse:
    return FileResponse(resolve_reference_artifact(voice_dir(voice_id), reference_id), media_type='audio/wav')


@router.patch('/{voice_id}/model', response_model=VoiceProfileResponse)
async def select_voice_model(voice_id: str, body: SelectVoiceModelRequest) -> VoiceProfileResponse:
    return VoiceProfileResponse.model_validate(select_model(voice_id, body.model_id))


@router.post("/{voice_id}/cancel", response_model=VoiceProfileResponse)
async def cancel_voice_build(voice_id: str):
    return await cancel_build(voice_id)


@router.get("/{voice_id}/preview")
def voice_preview(voice_id: str):
    path = preview_path(voice_id)
    if path is None:
        raise HTTPException(status_code=404, detail="No extracted vocal yet")
    return FileResponse(path)


@router.delete("/{voice_id}")
async def delete_voice(voice_id: str):
    path = voice_dir(voice_id)
    from ..voice_comparisons import release_voice as release_comparisons
    await release_comparisons(voice_id)
    await release_voice(voice_id)
    shutil.rmtree(path, ignore_errors=True)
    return {"deleted": voice_id}
