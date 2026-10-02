"""Library folder: one place for songs, voice clones, and singing models."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import ACE_STEP_DIR, DATA_DIR, IS_MACOS, SEED_VC_DIR
from ..data_root import (
    DataDirError,
    _is_inside,
    library_status,
    parse_chosen_folder,
    place_seed_models,
    save_data_dir,
    validate_data_dir,
)

from ..client_contracts import LibraryPickResponse, LibraryStatusResponse

from ..client_contracts import LibraryRequest
from ..artist_settings import ArtistSettings, ArtistSettingsError, get_settings, save_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])
_REPO_ROOT = Path(__file__).resolve().parents[3]


@router.get("", response_model=ArtistSettings)
def get_artist_settings() -> ArtistSettings:
    try:
        return get_settings()
    except ArtistSettingsError as exc:
        raise HTTPException(503, detail=exc.code) from exc


@router.put("", response_model=ArtistSettings)
def put_artist_settings(body: ArtistSettings) -> ArtistSettings:
    try:
        return save_settings(body)
    except ArtistSettingsError as exc:
        raise HTTPException(503, detail=exc.code) from exc


def _blocked() -> list[Path]:
    return [ACE_STEP_DIR, SEED_VC_DIR, _REPO_ROOT / "external"]


def _status() -> dict:
    status = library_status(DATA_DIR)
    status["can_pick"] = IS_MACOS
    return status


_PICK_SCRIPT = 'POSIX path of (choose folder with prompt "Choose the Remiqora library folder")'


@router.get("/library", response_model=LibraryStatusResponse)
def get_library():
    place_seed_models(DATA_DIR, SEED_VC_DIR)
    return _status()


@router.post("/library/pick", response_model=LibraryPickResponse)
def pick_library():
    """Open the Mac folder dialog. The browser cannot return a full folder path."""
    if sys.platform != "darwin":
        raise HTTPException(status_code=400, detail="picker")
    try:
        proc = subprocess.run(
            ["osascript", "-e", _PICK_SCRIPT],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=400, detail="picker") from exc
    if proc.returncode != 0:
        err = (proc.stderr or "").lower()
        if "cancel" in err:
            raise HTTPException(status_code=400, detail="cancelled")
        raise HTTPException(status_code=400, detail="picker")
    chosen = parse_chosen_folder(proc.stdout)
    if not chosen:
        raise HTTPException(status_code=400, detail="cancelled")
    return {"path": chosen}


@router.put("/library", response_model=LibraryStatusResponse)
def put_library(body: LibraryRequest):
    current = DATA_DIR.resolve()
    candidate = Path((body.data_dir or "").strip()).expanduser()
    try:
        if candidate.is_absolute() and candidate.resolve() != current and (
            _is_inside(candidate, current) or _is_inside(current, candidate)
        ):
            raise DataDirError("nested")
        path = validate_data_dir(body.data_dir, _blocked())
    except DataDirError as exc:
        raise HTTPException(status_code=400, detail=exc.code) from exc
    save_data_dir(path, DATA_DIR)
    return _status()
