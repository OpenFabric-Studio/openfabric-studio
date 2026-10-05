"""Unified track storage endpoints used by both models' frontends.

YuE2 and editor outputs are uploaded here. ACE batches persist directly on
the backend through ace_jobs. All origins share DATA_DIR/files/<model>/ and
one SQLite catalog.
"""
from __future__ import annotations

import re
import logging
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Body, File, Form, Depends, HTTPException, Request, Path as ApiPath, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import TypeAdapter, ValidationError

from .. import db
from ..config import MODELS
from ..contracts import JsonObject, SavedTrack, SetTrackFavoriteRequest, TracksResponse
from ..track_view import track_response as _row_to_dict

from ..client_contracts import MixSettingsResponse
from ..tagging import TaggedDownloadOptions, TaggedFileResponse
from ..track_activity import TrackActivityResponse, get_activity
from .tagged_download_response import download_options, tagged_download_response

router = APIRouter(prefix="/api/tracks", tags=["tracks"])

ALLOWED_AUDIO_EXT = {"wav", "mp3", "flac"}
ALLOWED_TRACK_MODELS = set(MODELS.keys()) | {"editor", "upload", "openrouter"}


@router.get("/activity", response_model=TrackActivityResponse)
async def track_activity() -> TrackActivityResponse:
    try:
        return get_activity()
    except (OSError, sqlite3.Error, ValueError) as exc:
        logging.getLogger(__name__).exception("Unable to read track activity")
        raise HTTPException(503, detail="track_activity_unavailable") from exc


def _sanitize(text: str) -> str:
    text = re.sub(r"\W+", "_", (text or "")[:40], flags=re.UNICODE)
    return text.strip("_") or "track"


def _create_unique(target_dir: Path, base: str, ext: str, with_abc: bool):
    """Create the audio file (and reserve the .abc name) under a name no
    other track uses. Filenames only carry second resolution, so two saves in
    the same second with the same title used to land on one file, and
    deleting either track then removed the other's audio."""
    n = 0
    while True:
        stem = base if n == 0 else f"{base}_{n}"
        n += 1
        abc_path = target_dir / f"{stem}.abc" if with_abc else None
        if abc_path is not None and abc_path.exists():
            continue
        audio_path = target_dir / f"{stem}.{ext}"
        try:
            f = open(audio_path, "xb")
        except FileExistsError:
            continue
        return audio_path, abc_path, f


@router.post("", response_model=SavedTrack)
async def save_track(
    model: str = Form(...),
    title: str = Form(""),
    lyrics: str = Form(""),
    seed: Optional[int] = Form(None, ge=-(2 ** 63), le=2 ** 63 - 1),
    duration_ms: Optional[float] = Form(None, ge=0, allow_inf_nan=False),
    wall_ms: Optional[float] = Form(None, ge=0, allow_inf_nan=False),
    params: str = Form("{}"),
    abc: Optional[str] = Form(None),
    audio: UploadFile = File(...),
):
    if model not in ALLOWED_TRACK_MODELS:
        raise HTTPException(status_code=400, detail=f"unknown track model/origin '{model}'")
    try:
        params_dict = TypeAdapter(JsonObject).validate_json(params or "{}")
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="invalid_track_params") from exc

    ext = (audio.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_AUDIO_EXT:
        ext = "wav"
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    base = f"{ts}_{_sanitize(title)}"
    target_dir = db.model_dir(model)
    audio_path, abc_path, f = _create_unique(target_dir, base, ext, bool(abc and abc.strip()))
    abc_written = False

    try:
        with f:
            shutil.copyfileobj(audio.file, f)

        if abc_path is not None:
            with open(abc_path, "x", encoding="utf-8") as af:
                abc_written = True
                af.write(abc)

        track_id = db.insert_track(
            model=model,
            title=title,
            lyrics=lyrics,
            seed=seed,
            duration_ms=duration_ms,
            wall_ms=wall_ms,
            params=params_dict,
            audio_path=audio_path,
            abc_path=abc_path,
        )
    except Exception:
        audio_path.unlink(missing_ok=True)
        if abc_written:
            abc_path.unlink(missing_ok=True)
        raise

    row = db.get_track(track_id)
    return _row_to_dict(row)


@router.post("/upload", response_model=SavedTrack)
async def upload_track(
    audio: UploadFile = File(...),
    title: Optional[str] = Form(None),
):
    ext = (audio.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_AUDIO_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format '{ext}'. Allowed: {', '.join(ALLOWED_AUDIO_EXT)}",
        )

    track_title = title.strip() if title and title.strip() else (audio.filename or "uploaded_track").rsplit(".", 1)[0]
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    base = f"{ts}_{_sanitize(track_title)}"
    target_dir = db.model_dir("upload")
    audio_path, _, f = _create_unique(target_dir, base, ext, False)

    try:
        with f:
            shutil.copyfileobj(audio.file, f)

        track_id = db.insert_track(
            model="upload",
            title=track_title,
            lyrics="",
            seed=None,
            duration_ms=None,
            wall_ms=None,
            params={"source": "user_upload"},
            audio_path=audio_path,
            abc_path=None,
        )
    except Exception:
        audio_path.unlink(missing_ok=True)
        raise

    row = db.get_track(track_id)
    return _row_to_dict(row)


@router.get("", response_model=TracksResponse)
async def list_tracks(model: Optional[str] = None):
    if model is not None and model not in ALLOWED_TRACK_MODELS:
        raise HTTPException(status_code=400, detail=f"unknown track model/origin '{model}'")
    return {"data": [_row_to_dict(r) for r in db.list_tracks(model)]}


@router.get("/{track_id}/audio")
async def track_audio(track_id: int):
    row = db.get_track(track_id)
    path = _track_file(row["audio_path"]) if row is not None else None
    if path is None:
        return JSONResponse({"error": "audio not found"}, status_code=404)
    return FileResponse(path)


def _track_file(value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        path = Path(value).resolve()
        return path if path.is_relative_to(db.FILES_DIR.resolve()) and path.is_file() else None
    except (OSError, RuntimeError):
        return None


@router.get("/{track_id}/download")
async def track_download(track_id: Annotated[int, ApiPath(gt=0, le=9_007_199_254_740_991)],
                         options: Annotated[TaggedDownloadOptions, Depends(download_options)], request: Request) -> TaggedFileResponse:
    return await tagged_download_response(track_id, None, None, options, request)


@router.get("/{track_id}/abc")
async def track_abc(track_id: int):
    row = db.get_track(track_id)
    path = _track_file(row["abc_path"]) if row is not None else None
    if path is None:
        return JSONResponse({"error": "track has no ABC plan"}, status_code=404)
    return PlainTextResponse(path.read_text(encoding="utf-8"))


@router.put("/{track_id}", response_model=SavedTrack)
async def rename_track(track_id: int, title: str = Body(..., embed=True)):
    if not db.update_track_title(track_id, title):
        raise HTTPException(status_code=404, detail="track not found")
    return _row_to_dict(db.get_track(track_id))


@router.put("/{track_id}/favorite", response_model=SavedTrack)
async def set_track_favorite(
    track_id: Annotated[int, ApiPath(ge=1, le=9_007_199_254_740_991)],
    request: SetTrackFavoriteRequest,
) -> SavedTrack:
    if not db.set_track_favorite(track_id, request.is_favorite):
        raise HTTPException(status_code=404, detail="track not found")
    row = db.get_track(track_id)
    if row is None:
        raise HTTPException(status_code=404, detail="track not found")
    return _row_to_dict(row)


@router.get("/{track_id}/mix", response_model=MixSettingsResponse)
async def get_mix_settings(track_id: int):
    row = db.get_track(track_id)
    if not row:
        raise HTTPException(status_code=404, detail="track not found")
    return {"settings": db.get_mix_settings(track_id)}


@router.put("/{track_id}/mix", response_model=MixSettingsResponse)
async def put_mix_settings(track_id: int, settings: JsonObject = Body(...)):
    row = db.get_track(track_id)
    if not row:
        raise HTTPException(status_code=404, detail="track not found")
    db.update_track_mix_settings(track_id, settings)
    return {"settings": settings}


@router.delete("/{track_id}")
async def delete_track(track_id: int):
    from ..yue_jobs import ensure_track_removable
    from ..reference_imports import ReferenceImportError, protect_track_removal as protect_track_references
    from ..voice_build import protect_track_removal
    from ..audio_versions import protect_track_versions_removal
    from ..audio_exports import protect_track_exports_removal, delete_track_exports
    try:
        async with protect_track_references(track_id), protect_track_versions_removal(track_id), protect_track_removal(track_id), protect_track_exports_removal(track_id):
            ensure_track_removable(track_id)
            delete_track_exports(track_id)
            if not db.delete_track(track_id):
                return JSONResponse({"error": "not found"}, status_code=404)
    except ReferenceImportError as exc:
        raise HTTPException(status_code=409, detail=exc.code) from exc
    return {"deleted": True}
