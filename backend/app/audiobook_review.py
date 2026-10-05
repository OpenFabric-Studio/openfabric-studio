"""Opt-in local ASR hints tied to immutable audiobook passage versions."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import logging
from pathlib import Path
import re
import shutil
import uuid
import wave
from typing import Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from . import audiobooks
from .audiobook_review_contracts import AsrCapability, AsrReview, AsrReviewFlag, AsrReviewRequest, AsrReviewsResponse
from .atomic_files import JsonObject, document_lock, write_object
from .job_lifecycle import await_cleanup, kill_process_tree
from .resource_admission import admission_lock, native_work_inflight, require_setup_idle
from .video_process import WorkerIdentity, WorkerOutputError, read_owned_output, spawn_owned, terminate_verified

_LOG = logging.getLogger(__name__)
_ID = re.compile(r"^[0-9a-f]{32}$")
_TASKS: dict[str, asyncio.Task[None]] = {}
_PROCESSES: dict[str, asyncio.subprocess.Process] = {}
_UNVERIFIED: set[str] = set()
_CLEANUP_PENDING: set[str] = set()
_STOPPING = False
ReviewState = Literal["queued", "running", "completed", "unavailable", "skipped", "failed", "canceled"]


class AudiobookReviewError(Exception):
    def __init__(self, code: str, status: int = 400) -> None:
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True)
class PassageSnapshot:
    book_id: str
    chapter_index: int
    passage_id: str
    revision: int
    render_identity: str
    audio_path: Path
    text: str
    language: str


class _StoredReview(BaseModel):
    review: AsrReview
    source_path: str = Field(min_length=1, max_length=4000)
    language: str = Field(default="", max_length=35)
    worker: WorkerIdentity | None = None


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _directory(identifier: str) -> Path:
    if _ID.fullmatch(identifier) is None:
        raise AudiobookReviewError("asr_review_not_found", 404)
    root = audiobooks.books_root().resolve()
    parent = root / ".asr-reviews"
    path = parent / identifier
    if parent.is_symlink() or path.is_symlink() or not path.resolve().is_relative_to(root):
        raise AudiobookReviewError("asr_storage_unavailable", 503)
    parent.mkdir(exist_ok=True)
    return path


def _path(identifier: str) -> Path:
    path = _directory(identifier) / "review.json"
    if path.is_symlink():
        raise AudiobookReviewError("asr_storage_unavailable", 503)
    return path


def workspace_path(identifier: str) -> Path:
    root = _directory(identifier)
    path = root / "work"
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise AudiobookReviewError("asr_storage_unavailable", 503)
    return path


def _read(identifier: str) -> _StoredReview:
    path = _path(identifier)
    try:
        with document_lock(path):
            if path.stat().st_size > 1024 * 1024:
                raise AudiobookReviewError("asr_storage_unavailable", 503)
            stored = _StoredReview.model_validate_json(path.read_text(encoding="utf-8"))
            if stored.review.id != identifier:
                raise AudiobookReviewError("asr_storage_unavailable", 503)
            return stored
    except FileNotFoundError as error:
        raise AudiobookReviewError("asr_review_not_found", 404) from error
    except (OSError, ValidationError) as error:
        raise AudiobookReviewError("asr_storage_unavailable", 503) from error


def _write(stored: _StoredReview) -> None:
    path = _path(stored.review.id)
    path.parent.mkdir(exist_ok=True)
    write_object(path, TypeAdapter(JsonObject).validate_json(stored.model_dump_json()))


def get(identifier: str) -> AsrReview:
    return _read(identifier).review


def list_reviews(book_id: str) -> AsrReviewsResponse:
    if _ID.fullmatch(book_id) is None:
        raise AudiobookReviewError("invalid_book_id", 404)
    root = _directory("0" * 32).parent
    reviews = [get(path.name) for path in root.iterdir() if _ID.fullmatch(path.name) and not path.is_symlink()]
    return AsrReviewsResponse(reviews=sorted((review for review in reviews if review.book_id == book_id), key=lambda review: review.created_at, reverse=True))


def _snapshot(book_id: str, chapter_index: int, passage_id: str) -> PassageSnapshot:
    from . import audiobook_workflows
    with audiobooks.publication_lock(book_id):
        response = audiobook_workflows.get_passages(book_id, chapter_index)
        passage = next((row for row in response.passages if row.id == passage_id), None)
        if passage is None or passage.status != "done" or passage.render_identity is None:
            raise AudiobookReviewError("asr_passage_unavailable", 409)
        path = audiobook_workflows.passage_audio_path(book_id, passage_id)
        return PassageSnapshot(book_id, chapter_index, passage_id, response.revision, passage.render_identity,
                               path, passage.text, passage.language)


def _audio_identity(path: Path) -> tuple[str, int]:
    root = audiobooks.books_root().resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
        raise AudiobookReviewError("asr_passage_unavailable", 409)
    try:
        digest = hashlib.sha256()
        with path.open("rb") as recording:
            while chunk := recording.read(65536):
                digest.update(chunk)
        with wave.open(str(path), "rb") as audio:
            if audio.getnframes() < 1 or audio.getframerate() < 1:
                raise AudiobookReviewError("asr_passage_unavailable", 409)
            duration = audio.getnframes() * 1000 // audio.getframerate()
    except (OSError, EOFError, wave.Error) as error:
        raise AudiobookReviewError("asr_passage_unavailable", 409) from error
    return digest.hexdigest(), duration


def capability() -> AsrCapability:
    from .reference_imports import _whisper
    from .yue_upload import get_ffmpeg_bin
    binary, model = _whisper()
    reason = "whisper_missing" if binary is None else "whisper_model_missing" if model is None else "ffmpeg_missing" if get_ffmpeg_bin() is None else ""
    return AsrCapability(available=not reason, reason=reason, setup_hint="" if not reason else
        "Install local whisper.cpp whisper-cli and configure REFERENCE_WHISPER_BIN / REFERENCE_WHISPER_MODEL. FFmpeg is required. No model download is performed.")


def compare_transcript(expected: str, transcript: str, duration_ms: int) -> list[AsrReviewFlag]:
    """Report uncertain lexical/duration differences, never a quality verdict."""
    tokens = re.compile(r"[\u3400-\u9fff\u3040-\u30ff]|[^\W_]+", re.UNICODE)
    original, recognized = tokens.findall(expected.casefold()), tokens.findall(transcript.casefold())
    missing, inserted = 0, 0
    for tag, start, end, other_start, other_end in SequenceMatcher(a=original, b=recognized, autojunk=False).get_opcodes():
        if tag in {"delete", "replace"}:
            missing += end - start
        if tag in {"insert", "replace"}:
            inserted += other_end - other_start
    flags: list[AsrReviewFlag] = []
    if missing:
        flags.append(AsrReviewFlag(code="omission", message=f"Possible omission or ASR mismatch: {missing} expected token(s) did not match. Listen before deciding on a repair."))
    if inserted:
        flags.append(AsrReviewFlag(code="repeat", message=f"Possible repetition, added speech or ASR mismatch: {inserted} extra token(s). Listen before deciding on a repair."))
    count = len(original)
    if count and (duration_ms < max(300, count * 150) or duration_ms > count * 1000 + 10000):
        flags.append(AsrReviewFlag(code="duration", message="Duration falls outside a broad narration estimate. Language, pacing and pauses affect it; review the recording manually."))
    return flags


def _state(identifier: str, state: ReviewState, reason: str = "", transcript: str = "", flags: list[AsrReviewFlag] | None = None) -> None:
    path = _path(identifier)
    with document_lock(path):
        stored = _read(identifier)
        stored.review.state, stored.review.reason = state, reason
        stored.review.updated_at = _stamp()
        stored.review.transcript = transcript
        stored.review.flags = flags or []
        _write(stored)


def _worker(identifier: str, identity: WorkerIdentity | None) -> None:
    with document_lock(_path(identifier)):
        stored = _read(identifier)
        stored.worker = identity
        _write(stored)


async def _drain(identifier: str, proc: asyncio.subprocess.Process) -> None:
    drain = asyncio.create_task(kill_process_tree(proc))
    try:
        await await_cleanup(drain)
    finally:
        if drain.done() and not drain.cancelled() and drain.exception() is None:
            _PROCESSES.pop(identifier, None)
            _UNVERIFIED.discard(identifier)
            _worker(identifier, None)
        else:
            _UNVERIFIED.add(identifier)


async def _run_tool(identifier: str, command: list[str], workspace: Path, timeout: float) -> None:
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await spawn_owned(command, receipt_path=_directory(identifier) / "worker.json", cwd=workspace,
                                 stdout=asyncio.subprocess.PIPE, on_identity=lambda value: _worker(identifier, value))
        _PROCESSES[identifier] = proc
        await read_owned_output(proc, max_bytes=65536, timeout=timeout)
        if proc.returncode != 0:
            raise AudiobookReviewError("asr_tool_failed", 503)
    except (OSError, TimeoutError, WorkerOutputError) as error:
        raise AudiobookReviewError("asr_tool_failed", 503) from error
    finally:
        if proc is not None:
            await _drain(identifier, proc)
        elif _read(identifier).worker is not None:
            if not await _recover_worker(identifier):
                _UNVERIFIED.add(identifier)


async def _transcribe(identifier: str, snapshot: PassageSnapshot, workspace: Path) -> str:
    from .reference_imports import _whisper
    from .yue_upload import get_ffmpeg_bin
    from .stems import gpu_lock
    binary, model = _whisper()
    ffmpeg = get_ffmpeg_bin()
    if binary is None or model is None or ffmpeg is None:
        raise AudiobookReviewError("asr_unavailable", 503)
    wav, output = workspace / "input.wav", workspace / "transcript"
    await _run_tool(identifier, [ffmpeg, "-nostdin", "-v", "error", "-y", "-protocol_whitelist", "file,pipe",
        "-format_whitelist", "wav", "-i", str(snapshot.audio_path), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], workspace, 120)
    async with gpu_lock:
        await _run_tool(identifier, [binary, "-m", str(model), "-f", str(wav), "-otxt", "-of", str(output), "-l", snapshot.language or "auto"], workspace, 900)
    transcript = output.with_suffix(".txt")
    if transcript.is_symlink() or not transcript.resolve().is_relative_to(workspace.resolve()) or not transcript.is_file() or transcript.stat().st_size > 80000:
        raise AudiobookReviewError("asr_output_invalid", 503)
    text = transcript.read_text(encoding="utf-8").strip()
    if len(text) > 20000:
        raise AudiobookReviewError("asr_output_invalid", 503)
    return text


async def _cleanup(identifier: str) -> None:
    path = workspace_path(identifier)
    cleanup = asyncio.create_task(asyncio.to_thread(shutil.rmtree, path)) if path.exists() else asyncio.create_task(asyncio.sleep(0))
    try:
        await await_cleanup(cleanup)
    finally:
        if cleanup.done() and not cleanup.cancelled() and cleanup.exception() is None:
            _CLEANUP_PENDING.discard(identifier)
        else:
            _CLEANUP_PENDING.add(identifier)


async def _execute(identifier: str, snapshot: PassageSnapshot) -> None:
    workspace = workspace_path(identifier)
    try:
        _state(identifier, "running")
        workspace.mkdir(exist_ok=True)
        transcript = await _transcribe(identifier, snapshot, workspace)
        with audiobooks.publication_lock(snapshot.book_id):
            current = _snapshot(snapshot.book_id, snapshot.chapter_index, snapshot.passage_id)
            digest, _ = _audio_identity(current.audio_path)
            if current.revision != snapshot.revision or current.render_identity != snapshot.render_identity or digest != get(identifier).audio_sha256:
                _state(identifier, "skipped", "passage_changed")
            else:
                _state(identifier, "completed", transcript=transcript, flags=compare_transcript(snapshot.text, transcript, get(identifier).duration_ms))
    except asyncio.CancelledError:
        _state(identifier, "canceled", "canceled_by_user_or_shutdown")
        raise
    except AudiobookReviewError as error:
        _state(identifier, "unavailable" if error.code == "asr_unavailable" else "skipped" if error.code == "asr_passage_unavailable" else "failed", error.code)
    except Exception:
        _LOG.exception("Passage ASR review failed")
        _state(identifier, "failed", "asr_review_failed")
    finally:
        if identifier not in _UNVERIFIED:
            try:
                await _cleanup(identifier)
            except OSError:
                _LOG.exception("ASR workspace cleanup retained for retry")
        else:
            _CLEANUP_PENDING.add(identifier)


async def create(book_id: str, chapter_index: int, passage_id: str, request: AsrReviewRequest) -> AsrReview:
    async with admission_lock:
        require_setup_idle()
        if _STOPPING or _UNVERIFIED or _CLEANUP_PENDING or len(_TASKS) >= 2:
            raise AudiobookReviewError("asr_review_busy", 409)
        if native_work_inflight():
            raise AudiobookReviewError("asr_native_work_busy", 409)
        snapshot = _snapshot(book_id, chapter_index, passage_id)
        if snapshot.revision != request.revision or snapshot.render_identity != request.render_identity:
            raise AudiobookReviewError("passage_changed", 409)
        digest, duration = _audio_identity(snapshot.audio_path)
        status = capability()
        stamp = _stamp()
        review = AsrReview(id=uuid.uuid4().hex, book_id=book_id, chapter_index=chapter_index, passage_id=passage_id,
            revision=snapshot.revision, render_identity=snapshot.render_identity, audio_sha256=digest,
            state="queued" if status.available else "unavailable", reason=status.reason, expected_text=snapshot.text,
            duration_ms=duration, created_at=stamp, updated_at=stamp)
        _write(_StoredReview(review=review, source_path=str(snapshot.audio_path), language=snapshot.language))
        if status.available:
            task = asyncio.create_task(_execute(review.id, snapshot))
            _TASKS[review.id] = task
            task.add_done_callback(lambda finished: _TASKS.pop(review.id, None))
        return review


async def cancel(identifier: str) -> AsrReview:
    task = _TASKS.get(identifier)
    if task is not None:
        task.cancel()
        await await_cleanup(asyncio.gather(task, return_exceptions=True))
    if get(identifier).state in {"queued", "running"}:
        _state(identifier, "canceled", "canceled_by_user_or_shutdown")
    return get(identifier)


def work_busy() -> bool:
    return bool(_UNVERIFIED) or bool(_CLEANUP_PENDING) or bool(_PROCESSES) or any(not task.done() for task in _TASKS.values())


async def _recover_worker(identifier: str) -> bool:
    stored = _read(identifier)
    if stored.worker is None:
        return True
    receipt = _directory(identifier) / "worker.json"
    if receipt.is_symlink() or Path(stored.worker.receipt).resolve() != receipt.resolve():
        return False
    if not await terminate_verified(stored.worker):
        return False
    _worker(identifier, None)
    _UNVERIFIED.discard(identifier)
    return True


async def start() -> None:
    global _STOPPING
    _STOPPING = True
    parent = _directory("0" * 32).parent
    for path in parent.iterdir():
        if _ID.fullmatch(path.name) is None or path.is_symlink():
            continue
        identifier = path.name
        if not await _recover_worker(identifier):
            _UNVERIFIED.add(identifier)
            _CLEANUP_PENDING.add(identifier)
            _state(identifier, "failed", "asr_worker_unverified")
            continue
        if get(identifier).state in {"queued", "running"}:
            _state(identifier, "canceled", "interrupted_by_restart")
        await _cleanup(identifier)
    _STOPPING = False


async def shutdown() -> None:
    global _STOPPING
    _STOPPING = True
    tasks = tuple(_TASKS.values())
    for task in tasks:
        if not task.done() and not task.cancelling():
            task.cancel()
    await await_cleanup(asyncio.gather(*tasks, return_exceptions=True))
    await await_cleanup(asyncio.gather(*(_drain(identifier, proc) for identifier, proc in tuple(_PROCESSES.items())), return_exceptions=True))
    for identifier in tuple(_UNVERIFIED):
        if identifier not in _PROCESSES and await _recover_worker(identifier):
            _UNVERIFIED.discard(identifier)
    await await_cleanup(asyncio.gather(*(_cleanup(identifier) for identifier in tuple(_CLEANUP_PENDING) if identifier not in _UNVERIFIED), return_exceptions=True))
