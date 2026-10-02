"""Stem separation endpoints (Demucs) for a saved track.

Separate from routes_tracks.py so the one-off-job semantics (start/cancel/
status/download-per-stem) stay in their own place, but reuses the same
tracks table (stems_json column) and file-layout conventions.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import db, audio_exports
from .. import stems

from ..client_contracts import StemsStatusResponse

router = APIRouter(prefix="/api/tracks", tags=["stems"])


def _stem_urls(track_id: int, row) -> dict[str, str] | None:
    if not row["stems_json"]:
        return None
    names = json.loads(row["stems_json"]).keys()
    return {n: f"/api/tracks/{track_id}/stems/{n}" for n in names}


@router.post("/{track_id}/stems", response_model=StemsStatusResponse)
async def start_stems(track_id: int, force: bool = False):
    row = db.get_track(track_id)
    if not row or not Path(row["audio_path"]).exists():
        raise HTTPException(status_code=404, detail="track or audio not found")
    job = await stems.start(track_id, force=force)
    return {"status": job.status, "error": job.error}


@router.post("/{track_id}/stems/cancel", response_model=StemsStatusResponse)
async def cancel_stems(track_id: int):
    row = db.get_track(track_id)
    if not row:
        raise HTTPException(status_code=404, detail="track not found")
    return await stems.cancel(track_id)


@router.get("/{track_id}/stems/status", response_model=StemsStatusResponse)
async def stems_status(track_id: int):
    row = db.get_track(track_id)
    if not row:
        raise HTTPException(status_code=404, detail="track not found")
    result = stems.status(track_id)
    # Re-fetch: stems.status() may have just observed a job finish, and the
    # row read above could predate that job's DB write.
    result["stems"] = _stem_urls(track_id, db.get_track(track_id)) if result["status"] == "done" else None
    return result


@router.delete("/{track_id}/stems")
async def delete_stems(track_id: int):
    row = db.get_track(track_id)
    if not row:
        raise HTTPException(status_code=404, detail="track not found")
    if stems.is_active(track_id):
        raise HTTPException(status_code=409, detail="separation is in progress")
    try:
        async with audio_exports.protect_stem_exports_removal(track_id):
            if not db.delete_track_stems(track_id):
                raise HTTPException(status_code=404, detail="no stems to delete")
            stems.forget(track_id)
    except audio_exports.AudioExportError as exc:
        raise HTTPException(409, detail=exc.code) from exc
    return {"deleted": True}


@router.get("/{track_id}/stems/{stem_name}")
async def stem_file(track_id: int, stem_name: str):
    if stem_name not in stems.STEM_NAMES:
        raise HTTPException(status_code=404, detail="unknown stem")
    try:
        return FileResponse(audio_exports.stem_source(track_id, stem_name))
    except audio_exports.AudioExportError as exc:
        raise HTTPException(404, detail=exc.code) from exc
