"""Bulk file upload for ACE-Step LoRA training datasets.

ACE-Step's own training API (see external/patches, /v1/dataset/*) only ever
scans a server-local folder path - it has no upload endpoint at all. This
lets the browser drop/pick files directly instead of the user having to copy
them onto the server's disk by hand first. Uploaded files land under
ACE_STEP_DIR/datasets/<name>/, which is exactly the location ACE-Step's own
path-safety check (safe_path()) already requires audio_dir to be inside, so
the resulting relative path can be handed straight to /v1/dataset/scan.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..config import ACE_STEP_DIR

from ..client_contracts import UploadDatasetFilesResponse

router = APIRouter(prefix="/api/lora-dataset", tags=["lora-dataset"])

ALLOWED_AUDIO_EXT = {"wav", "mp3", "flac", "ogg", "opus"}


def _sanitize_dataset_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "")[:60]).strip("_")
    return name or "my_lora_dataset"


def _unique_dest(target_dir: Path, filename: str) -> Path:
    stem, _, ext = filename.rpartition(".")
    dest = target_dir / filename
    n = 1
    while dest.exists():
        dest = target_dir / f"{stem}_{n}.{ext}"
        n += 1
    return dest


@router.post("/upload", response_model=UploadDatasetFilesResponse)
async def upload_dataset_files(
    dataset_name: str = Form(...),
    files: list[UploadFile] = File(...),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    safe_name = _sanitize_dataset_name(dataset_name)
    target_dir = ACE_STEP_DIR / "datasets" / safe_name
    target_dir.mkdir(parents=True, exist_ok=True)

    saved: list[str] = []
    skipped: list[str] = []
    for f in files:
        filename = Path(f.filename or "").name  # drop any path components
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if not filename or ext not in ALLOWED_AUDIO_EXT:
            skipped.append(f.filename or "(unnamed)")
            continue
        dest = _unique_dest(target_dir, filename)
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        saved.append(dest.name)

    if not saved:
        raise HTTPException(
            status_code=400,
            detail=f"No supported audio files in upload. Allowed: {', '.join(sorted(ALLOWED_AUDIO_EXT))}",
        )

    return {
        "audio_dir": f"datasets/{safe_name}",
        "saved": saved,
        "skipped": skipped,
    }
