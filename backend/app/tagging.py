"""Version-aware metadata downloads using supervised FFmpeg stream copy.

Temporary copies belong to the backend until response completion or shutdown.
The selected library file is never written, replaced, or re-encoded.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.responses import FileResponse
from starlette.types import Receive, Scope, Send

from . import artist_settings, audio_exports, audio_version_store, audio_versions, db, export_provenance
from .audio_version_contracts import AudioVersion
from .config import MODELS
from .contracts import Contract, SavedTrack
from .job_lifecycle import await_cleanup, kill_process_tree
from .track_view import track_response
from .export_provenance_contracts import ContentOrigin
from .tagging_genres import guess_genre
from .video_io import copy_verified, hash_file
from .video_media import VideoMediaError, tool
from .video_process import WorkerOutputError, read_owned_output, spawn_owned
from .video_projects import VideoProjectError

logger = logging.getLogger(__name__)
_TEMP_ROOT = Path(tempfile.gettempdir())
_TIMEOUT = 120.0
_MAX_BYTES = 8 * 1024**3
_MAX_METADATA_BYTES = 1024 * 1024
_capacity = asyncio.Semaphore(2)
_tasks: dict[str, asyncio.Task[TaggedAudioDownload]] = {}
_processes: dict[str, asyncio.subprocess.Process] = {}
_pending: dict[str, TaggedAudioDownload] = {}
_INPUT = ["-protocol_whitelist", "file,pipe", "-format_whitelist", "wav,mp3,flac"]


class TaggedDownloadOptions(Contract):
    model_config = ConfigDict(extra="forbid")
    album: str | None = Field(default=None, max_length=120, strict=True, pattern=r"^[^\x00-\x1f\x7f]*$")
    track_no: int | None = Field(default=None, ge=1, le=999, strict=True)


class TaggingError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class TaggedAudioDownload:
    identifier: str
    directory: Path
    path: Path
    filename: str

    def cleanup(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)
        _pending.pop(self.identifier, None)


class TaggedFileResponse(FileResponse):
    """ASGI cancellation and send failures also release the backend-owned copy."""
    def __init__(self, download: TaggedAudioDownload) -> None:
        super().__init__(download.path, filename=download.filename, headers={"X-Tags": "written"})
        self.download = download

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Temporary files cannot be handed to a server as a deferred pathname:
        # send() may return before its file reader opens the path. Stream the
        # body here so cleanup follows the final chunk on every ASGI server.
        stream_scope: Scope = dict(scope)
        stream_scope["extensions"] = {name: value for name, value in scope.get("extensions", {}).items()
                                      if name != "http.response.pathsend"}
        try:
            await super().__call__(stream_scope, receive, send)
        finally:
            self.download.cleanup()


class _ProbeFormat(BaseModel):
    tags: dict[str, str] = Field(default_factory=dict)


class _Probe(BaseModel):
    format: _ProbeFormat = Field(default_factory=_ProbeFormat)


def _bounded_text(value: str, max_bytes: int = 4096) -> str:
    # Bound generated descriptive fields; lyrics use the complete metadata
    # document and are rejected explicitly if its overall limit is exceeded.
    text = "".join(char for char in value if char in "\n\t" or ord(char) >= 32 and ord(char) != 127)
    return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def build_tags(track: SavedTrack, version: AudioVersion, artist: str,
               options: TaggedDownloadOptions, previous_comment: str = "",
               album_default: str = "", content_origin: ContentOrigin | None = None) -> dict[str, str]:
    title = _bounded_text(" ".join(track.title.split()), 2048) or "Untitled"
    album = options.album if options.album is not None and options.album.strip() else album_default
    tags = {"title": title, "artist": artist, "album": (album or title).strip(), "encoded_by": "OpenFabric"}
    try:
        tags["date"] = datetime.fromisoformat(track.created_at.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    if options.track_no is not None:
        tags["track"] = str(options.track_no)
    if track.lyrics.strip():
        tags["lyrics"] = track.lyrics
    genre = guess_genre(track.params, track.title)
    if genre:
        tags["genre"] = _bounded_text(genre, 160)
    bpm = track.params.get("bpm")
    if isinstance(bpm, (int, float)) and not isinstance(bpm, bool) and math.isfinite(bpm) and 0 < bpm <= 1000:
        tags["TBPM"] = str(round(bpm))
    key = track.params.get("key_scale", track.params.get("keyscale"))
    if isinstance(key, str) and key.strip():
        tags["TKEY"] = _bounded_text(key.strip(), 160)
    origin = content_origin or export_provenance.track_component(track.id,version.id,'0'*64,is_voice=version.kind=='voice').content_origin
    tags['OPENFABRIC_CONTENT_ORIGIN'] = origin
    notes = [previous_comment] if previous_comment else []
    notes.append(f'content_origin={origin}')
    if origin in {'generated','mixed'}:
        tags['AI_GENERATED'] = 'true'
    if track.model in {"ace_step", "yue2"}:
        label = MODELS[track.model].label
        tags.update({"AI_GENERATED": "true", "GENERATOR": label})
        notes.append(f"Generated with OpenFabric ({label})")
        if track.model == "yue2":
            notes.append("Check the terms of the exact model weights used")
    elif track.model == "openrouter":
        tags["GENERATOR"] = "OpenRouter"
        notes.append("Generated with OpenFabric (OpenRouter)")
    elif track.model == "editor":
        notes.append("Mixed in the OpenFabric editor")
    if track.seed is not None:
        tags["SEED"] = str(track.seed)
        notes.append(f"seed={track.seed}")
    notes.append(f"audio_version={version.id}")
    if track.created_at:
        tags["CREATED_AT"] = _bounded_text(track.created_at, 128)
        notes.append(f"created_at={tags['CREATED_AT']}")
    if version.kind == "voice":
        tags["VOICE_ID"] = version.voice_id or ""
        tags["VOICE_NAME"] = _bounded_text(version.voice_name or "", 800)
        notes.append(f"voice={tags['VOICE_NAME'] or version.voice_id or version.id}")
    # WAV's RIFF INFO supports a limited vocabulary; retain all provenance in
    # the standard comment as well as custom MP3/FLAC metadata.
    tags["comment"] = "; ".join(notes)
    # Empty artist is an intentional override, including on pre-tagged uploads.
    return {key: value for key, value in tags.items() if value or key == "artist"}


def _metadata_document(previous: dict[str, str], tags: dict[str, str]) -> str:
    """Merge source tags without duplicate casing and encode FFmetadata v1.

    Syntax: https://ffmpeg.org/ffmpeg-formats.html#Metadata-1
    Metadata never enters argv; even multiline lyrics leave a short command.
    """
    overridden = {key.casefold() for key in tags}
    merged = {key: value for key, value in previous.items() if key.casefold() not in overridden}
    merged.update(tags)
    if sum(len(key) + len(value) for key, value in merged.items()) > _MAX_METADATA_BYTES:
        raise TaggingError("metadata_too_large")
    def escaped(value: str) -> str:
        if any(ord(char) < 32 and char not in "\n\r\t" or ord(char) == 127 for char in value):
            raise TaggingError("metadata_invalid")
        return "".join("\\" + char if char in "\\=;#[]\n\r" else char for char in value)
    document = ";FFMETADATA1\n" + "\n".join(f"{escaped(key)}={escaped(value)}" for key, value in merged.items()) + "\n"
    if len(document.encode("utf-8")) > _MAX_METADATA_BYTES:
        raise TaggingError("metadata_too_large")
    return document


def download_name(tags: dict[str, str], extension: str) -> str:
    name = f"{tags.get('artist', '')} - {tags['title']}".strip(" -")
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "_", name)[:120].strip(" .") or "track"
    return f"{name}.{extension}"


def _selected(track_id: int, version_id: str | None, export_id: str | None) -> tuple[SavedTrack, AudioVersion, Path]:
    if isinstance(track_id, bool) or not 0 < track_id <= 9_007_199_254_740_991:
        raise TaggingError("track_not_found")
    if version_id is not None and re.fullmatch(r"[0-9a-f]{32}", version_id) is None:
        raise TaggingError("audio_version_not_found")
    if export_id is not None and (version_id is None or re.fullmatch(r"[0-9a-f]{32}", export_id) is None):
        raise TaggingError("export_not_found")
    try:
        row = db.get_track(track_id)
        if row is None:
            raise TaggingError("track_not_found")
        if version_id is None:
            original = next((version for version in audio_versions.list_versions(track_id).versions
                if version.kind == "original" and version.status == "done"), None)
            if original is None:
                raise TaggingError("original_audio_missing")
            version_id = original.id
        stored = audio_version_store.get(db.get_db(), version_id)
        if stored is None or stored.public.track_id != track_id:
            raise TaggingError("audio_version_not_found")
        source = (audio_exports.export_file(track_id, version_id, export_id) if export_id is not None
                  else audio_versions.resolve_source(track_id, version_id))
        root = db.FILES_DIR.resolve()
        if source.resolve() != source or not source.is_relative_to(root) or not source.is_file() or not 0 < source.stat().st_size <= _MAX_BYTES:
            raise TaggingError("audio_source_unavailable")
        if source.suffix.lower() not in {".wav", ".mp3", ".flac"}:
            raise TaggingError("audio_format_unsupported")
        return track_response(row), stored.public, source
    except HTTPException as exc:
        code = {"track_missing": "track_not_found", "audio_version_missing": "audio_version_not_found"}.get(
            str(exc.detail), "audio_source_unavailable")
        raise TaggingError(code) from exc
    except audio_exports.AudioExportError as exc:
        raise TaggingError(exc.code) from exc
    except (OSError, sqlite3.Error, ValueError, ValidationError) as exc:
        logger.warning("Unable to resolve tagged audio source", exc_info=True)
        raise TaggingError("audio_source_unavailable") from exc


async def _run_tool(identifier: str, argv: list[str], directory: Path, *, max_bytes: int = 65536) -> bytes:
    proc = await spawn_owned(argv, receipt_path=directory / "worker.json", stdout=asyncio.subprocess.PIPE)
    _processes[identifier] = proc
    try:
        output = await read_owned_output(proc, max_bytes=max_bytes, timeout=_TIMEOUT)
        if proc.returncode != 0:
            raise TaggingError("tagging_failed")
        return output
    except TimeoutError as exc:
        raise TaggingError("tagging_timeout") from exc
    except WorkerOutputError as exc:
        raise TaggingError("tagging_output_invalid") from exc
    finally:
        try:
            await await_cleanup(kill_process_tree(proc))
        finally:
            if proc.stdin is not None:
                proc.stdin.close()
            _processes.pop(identifier, None)


async def _prepare(identifier: str, track_id: int, version_id: str | None,
                   export_id: str | None, options: TaggedDownloadOptions) -> TaggedAudioDownload:
    directory: Path | None = None
    success = False
    try:
        async with _capacity:
            track, version, source = _selected(track_id, version_id, export_id)
            saved_tags = artist_settings.get_settings()
            artist = saved_tags.artist
            ffmpeg, ffprobe = tool("ffmpeg"), tool("ffprobe")
            directory = Path(tempfile.mkdtemp(prefix="openfabric-tagged-", dir=_TEMP_ROOT))
            # Snapshot with owned, cancellable IO before launching an external
            # decoder. Concurrent source edits cannot silently change selection.
            digest = await hash_file(source)
            captured = directory / f"source{source.suffix.lower()}"
            await copy_verified(source, captured, digest)
            probe = _Probe.model_validate_json(await _run_tool(identifier,
                [ffprobe, "-v", "error", *_INPUT, "-show_format", "-of", "json", str(captured)], directory,
                max_bytes=_MAX_METADATA_BYTES + 8192))
            previous = next((value for key, value in probe.format.tags.items() if key.lower() == "comment"), "")
            captured_origin: ContentOrigin | None=None
            if export_id is not None:
                retained=audio_exports.get_export(track_id,version.id,export_id).provenance
                if retained is not None and retained.artifact_sha256==digest:captured_origin=retained.content_origin
            tags = build_tags(track, version, artist, options, previous, album_default=saved_tags.album,content_origin=captured_origin)
            metadata = directory / "metadata.txt"
            metadata.write_text(_metadata_document(probe.format.tags, tags), encoding="utf-8", newline="")
            extension = source.suffix.lower().lstrip(".")
            target = directory / f"download.{extension}"
            command = [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-n", *_INPUT, "-i", str(captured),
                       "-protocol_whitelist", "file", "-format_whitelist", "ffmetadata", "-f", "ffmetadata", "-i", str(metadata),
                       "-map", "0:a", "-c:a", "copy", "-map_metadata", "1"]
            if extension == "mp3":
                command += ["-id3v2_version", "3"]
            command += ["-f", extension, str(target)]
            await _run_tool(identifier, command, directory)
            if not target.is_file() or not 0 < target.stat().st_size <= _MAX_BYTES + _MAX_METADATA_BYTES:
                raise TaggingError("tagging_failed")
            captured.unlink()
            metadata.unlink()
            (directory / "worker.json").unlink(missing_ok=True)
            result = TaggedAudioDownload(identifier, directory, target, download_name(tags, extension))
            _pending[identifier] = result
            success = True
            return result
    except VideoMediaError as exc:
        raise TaggingError("ffmpeg_missing") from exc
    except artist_settings.ArtistSettingsError as exc:
        raise TaggingError(exc.code) from exc
    except (OSError, ValueError, ValidationError, VideoProjectError) as exc:
        logger.warning("Unable to prepare tagged audio download", exc_info=True)
        raise TaggingError("tagging_failed") from exc
    finally:
        if directory is not None and not success:
            shutil.rmtree(directory, ignore_errors=True)


async def prepare_download(track_id: int, version_id: str | None = None,
                           export_id: str | None = None, *,
                           options: TaggedDownloadOptions | None = None) -> TaggedAudioDownload:
    identifier = uuid.uuid4().hex
    profile = TaggedDownloadOptions.model_validate_json((options or TaggedDownloadOptions()).model_dump_json())
    task = asyncio.create_task(_prepare(identifier, track_id, version_id, export_id, profile))
    _tasks[identifier] = task
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        task.cancel()
        try:
            await await_cleanup(asyncio.gather(task, return_exceptions=True))
        finally:
            completed = _pending.get(identifier)
            if completed is not None:
                completed.cleanup()
        raise
    finally:
        _tasks.pop(identifier, None)


async def shutdown() -> None:
    tasks = list(_tasks.values())
    for task in tasks:
        if not task.done() and not task.cancelling():
            task.cancel()
    try:
        await await_cleanup(asyncio.gather(*tasks, return_exceptions=True))
    finally:
        for result in list(_pending.values()):
            result.cleanup()
