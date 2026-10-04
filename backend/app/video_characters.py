"""Local character records: one consented speech voice and one locked still.

This is not a video model trainer. The still is image conditioning. The voice
is an existing speech profile, which already requires consent.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile
from pydantic import ValidationError

from . import voice_profiles
from .config import DATA_DIR
from .job_lifecycle import await_cleanup, kill_process_tree, spawn_process, communicate_process
from .video_contracts import VideoCharacter
from .video_media import probe_media, tool
from .video_projects import VideoProjectError, atomic_text

_ID = re.compile(r"^[0-9a-f]{32}$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def characters_root() -> Path:
    return DATA_DIR / "video_characters"


def character_dir(character_id: str) -> Path:
    if not _ID.fullmatch(character_id):
        raise VideoProjectError("not_found")
    root = characters_root().resolve()
    path = (root / character_id).resolve()
    if not path.is_relative_to(root):
        raise VideoProjectError("not_found")
    return path


def _load(character_id: str) -> VideoCharacter:
    path = character_dir(character_id) / "character.json"
    try:
        character = VideoCharacter.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise VideoProjectError("not_found") from exc
    if character.id != character_id:
        raise VideoProjectError("not_found")
    return character


def still_file(character_id: str) -> Path:
    path = character_dir(character_id) / "still.png"
    if not path.is_file():
        raise VideoProjectError("not_found")
    return path


def _with_voice(character: VideoCharacter) -> VideoCharacter:
    try:
        profile = voice_profiles.get_profile(character.voice_profile_id)
    except voice_profiles.VoiceProfileError:
        return character.model_copy(update={"voice_ready": False})
    return character.model_copy(
        update={
            "voice_name": profile.name,
            "voice_ready": bool(profile.consent_confirmed and character.consent_confirmed),
        }
    )


def list_characters() -> list[VideoCharacter]:
    root = characters_root()
    if not root.is_dir():
        return []
    rows: list[VideoCharacter] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or not _ID.fullmatch(path.name):
            continue
        try:
            rows.append(_with_voice(_load(path.name)))
        except VideoProjectError:
            continue
    rows.sort(key=lambda item: item.updated_at, reverse=True)
    return rows


def get_character(character_id: str) -> VideoCharacter:
    return _with_voice(_load(character_id))


def require_voice(profile_id: str) -> voice_profiles.SpeechVoiceProfile:
    try:
        profile = voice_profiles.get_profile(profile_id)
    except voice_profiles.VoiceProfileError as exc:
        if exc.code == "profile_not_found":
            raise VideoProjectError("voice_missing") from exc
        raise VideoProjectError(exc.code) from exc
    if not profile.consent_confirmed:
        raise VideoProjectError("consent_required")
    return profile


def _looks_like_image(header: bytes) -> bool:
    return (
        header.startswith(b"\x89PNG\r\n\x1a\n")
        or header.startswith(b"\xff\xd8\xff")
        or (header.startswith(b"RIFF") and header[8:12] == b"WEBP")
    )


async def create_character(
    *,
    name: str,
    voice_profile_id: str,
    consent_confirmed: bool,
    upload: UploadFile,
) -> VideoCharacter:
    cleaned = name.strip()
    if not cleaned or len(cleaned) > 80:
        raise VideoProjectError("invalid_draft")
    if not consent_confirmed:
        raise VideoProjectError("consent_required")
    profile = require_voice(voice_profile_id)
    filename = upload.filename or "still.png"
    if len(filename) > 160 or "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise VideoProjectError("invalid_reference")
    character_id = uuid.uuid4().hex
    root = character_dir(character_id)
    temporary = root / f".{character_id}.upload"
    output = root / "still.png"
    proc: asyncio.subprocess.Process | None = None
    published = False
    try:
        root.mkdir(parents=True, exist_ok=False)
        count = 0
        with temporary.open("wb") as handle:
            while chunk := await upload.read(65536):
                count += len(chunk)
                if count > 20 * 1024 * 1024:
                    raise VideoProjectError("reference_too_large")
                handle.write(chunk)
        if count < 1:
            raise VideoProjectError("invalid_reference")
        with temporary.open("rb") as reader:
            header = reader.read(16)
        if not _looks_like_image(header):
            raise VideoProjectError("invalid_reference")
        info = await probe_media(temporary)
        if (
            not 1 <= info.width <= 8192
            or not 1 <= info.height <= 8192
            or info.width * info.height > 16777216
        ):
            raise VideoProjectError("reference_too_large")
        proc = await spawn_process(
            tool("ffmpeg"),
            "-v", "error", "-y", "-i", str(temporary), "-frames:v", "1", str(output),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        await communicate_process(proc, 30)
        if proc.returncode != 0 or not output.is_file():
            raise VideoProjectError("invalid_reference")
        stamp = now()
        character = VideoCharacter(
            id=character_id,
            name=cleaned,
            voice_profile_id=profile.id,
            voice_name=profile.name,
            voice_ready=True,
            still_name=filename,
            still_width=info.width,
            still_height=info.height,
            still_url=f"/api/videos/characters/{character_id}/still",
            consent_confirmed=True,
            look="locked_still",
            created_at=stamp,
            updated_at=stamp,
        )
        atomic_text(root / "character.json", character.model_dump_json())
        published = True
        return character
    finally:
        await await_cleanup(_cleanup(proc, temporary, root, published, upload))


async def _cleanup(
    proc: asyncio.subprocess.Process | None,
    temporary: Path | None,
    root: Path,
    published: bool,
    upload: UploadFile,
) -> None:
    try:
        try:
            await kill_process_tree(proc)
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if not published:
                import shutil
                shutil.rmtree(root, ignore_errors=True)
        finally:
            await upload.close()
    except Exception as exc:
        raise VideoProjectError("cleanup_failed") from exc


def delete_character(character_id: str) -> None:
    import shutil

    path = character_dir(character_id)
    if not (path / "character.json").is_file():
        raise VideoProjectError("not_found")
    shutil.rmtree(path)
