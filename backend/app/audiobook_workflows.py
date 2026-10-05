"""Owned cast previews and reviewed passage replacement.

Accepted PCM is immutable. Replacement stages every representation before one
SQLite transaction changes its pointers; candidates never erase accepted work.
The blocking external speech call drains at the next passage boundary.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
import threading
import uuid
import wave
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator, Literal

from pydantic import BaseModel, Field, ValidationError

from . import audiobook_narration, audiobooks, speech_clone, voice_profiles
from .audiobook_contracts import (AcceptAudiobookRepairRequest, AudiobookAudition,
    AudiobookAuditionClip, AudiobookAuditionOptions, AudiobookPassage, AudiobookPassagesResponse,
    AudiobookRepair, CreateAudiobookAuditionRequest, CreateAudiobookRepairRequest)
from .job_lifecycle import await_cleanup
from .voice_profile_contracts import CloudSpeechQuote, CloudSpeechProvenance
from .speech_references import SpeechRenderSnapshot, capture, file_digest, normalize_language

_LOG = logging.getLogger(__name__)
_TASKS: dict[str, asyncio.Task[None]] = {}
_CANCEL: dict[str, threading.Event] = {}
_STOPPING = False
_PREPARING = 0
_PREPARATION_LOCK = threading.Lock()


@contextmanager
def _prepare_admission() -> Iterator[None]:
    """Exclude setup throughout capture and publish the owned worker before release."""
    global _PREPARING
    from .module_jobs import ModuleSetupError, speech_admission
    from .resource_admission import native_work_inflight
    try:
        with speech_admission():
            from .audiobook_publish import cleanup_pending
            if cleanup_pending():
                raise audiobooks.AudiobookError("audiobook_export_cleanup_failed", 503)
            if native_work_inflight():
                raise audiobooks.AudiobookError("native_model_busy", 409)
            with _PREPARATION_LOCK:
                _PREPARING += 1
            try:
                yield
            finally:
                with _PREPARATION_LOCK:
                    _PREPARING -= 1
    except ModuleSetupError as exc:
        raise audiobooks.AudiobookError(exc.code, 409) from exc


class _AuditionState(BaseModel):
    public: AudiobookAudition
    snapshots: list[SpeechRenderSnapshot]


class _RepairState(BaseModel):
    public: AudiobookRepair
    snapshot: SpeechRenderSnapshot
    source_identity: str | None
    source_text: str
    section_index: int
    pending_accept_token: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


def _cleanup_stages(state: _RepairState) -> None:
    """Replay only this journal's generated paths; never remove a selected output."""
    token = state.pending_accept_token
    if token is None:
        return
    public = state.public
    root = audiobooks.book_dir(public.book_id)
    chapters = audiobooks.chapters_dir(public.book_id)
    paths = [chapters / "sections" / f"{public.passage_id}-repair-{token}.wav",
        chapters / f"{public.chapter_index:04d}-repair-{token}.wav",
        *(root / f"export-{token}.{suffix}" for suffix in ("wav", "mp3", "m4b"))]
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        rows = connection.execute("""SELECT export_path FROM audiobook_books WHERE id=?
            UNION SELECT mp3_export_path FROM audiobook_books WHERE id=?
            UNION SELECT m4b_export_path FROM audiobook_books WHERE id=?
            UNION SELECT output_path FROM audiobook_jobs WHERE book_id=?
            UNION SELECT s.output_path FROM audiobook_sections s JOIN audiobook_jobs j ON j.id=s.job_id WHERE j.book_id=?""",
            (public.book_id,) * 5).fetchall()
    selected = {str(row[0]) for row in rows if row[0]}
    for path in paths:
        if str(path) not in selected and not path.is_symlink():
            audiobook_narration._contained(path, root)
            path.unlink(missing_ok=True)
            if path.suffix==".wav":
                provenance = path.with_suffix(".cloud.json")
                if not provenance.is_symlink():
                    provenance.unlink(missing_ok=True)
    state.pending_accept_token = None
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        # Cancellation may have updated the public state while encoding ran.
        # Clear only the private journal; do not replay an older public payload.
        connection.execute("UPDATE audiobook_workflows SET payload=json_set(payload,'$.pending_accept_token',NULL) WHERE id=?", (public.id,))
        connection.commit()


def _workspace(identifier: str, *, create: bool = True) -> Path:
    if audiobooks._ID.fullmatch(identifier) is None:
        raise audiobooks.AudiobookError("workflow_not_found", 404)
    root = audiobooks.books_root()
    parent = audiobook_narration._contained(root / "_workflows", root)
    if create:
        parent.mkdir(exist_ok=True)
    directory = audiobook_narration._contained(parent / identifier, parent)
    if create:
        directory.mkdir(exist_ok=True)
    elif not directory.is_dir():
        raise audiobooks.AudiobookError("audio_missing", 404)
    return directory


def _save(identifier: str, kind: str, state: _AuditionState | _RepairState) -> None:
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        audiobooks._ensure_schema(connection)
        public = state.public
        connection.execute("""INSERT INTO audiobook_workflows(id,kind,payload,created_at,book_id,chapter_index,passage_id,revision) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET payload=excluded.payload""",
            (identifier, kind, state.model_dump_json(), public.created_at, public.book_id, public.chapter_index,
             public.passage_id if isinstance(public, AudiobookRepair) else None, public.revision))
        connection.commit()


def _load(identifier: str, kind: Literal["audition", "repair"]) -> str:
    if audiobooks._ID.fullmatch(identifier) is None:
        raise audiobooks.AudiobookError("workflow_not_found", 404)
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        audiobooks._ensure_schema(connection)
        row = connection.execute("SELECT payload FROM audiobook_workflows WHERE id=? AND kind=?", (identifier, kind)).fetchone()
    if row is None:
        raise audiobooks.AudiobookError("workflow_not_found", 404)
    return str(row[0])


def get_audition(identifier: str) -> AudiobookAudition:
    state = _AuditionState.model_validate_json(_load(identifier, "audition"))
    public = state.public
    all_allowed = True
    for clip, snapshot in zip(public.clips,state.snapshots,strict=True):
        if not _preview_allowed(clip.profile_id,snapshot):
            clip.audio_url = None
            all_allowed = False
    if not all_allowed:
        public.scene_audio_url, public.detail = None, "consent_required"
    return public


def get_repair(identifier: str) -> AudiobookRepair:
    state = _RepairState.model_validate_json(_load(identifier, "repair"))
    if not _preview_allowed(state.snapshot.profile_id,state.snapshot):
        state.public.audio_url, state.public.detail = None, "consent_required"
    return state.public


def _preview_allowed(profile_id: str, snapshot: SpeechRenderSnapshot | None = None) -> bool:
    try:
        profile = voice_profiles.get_profile(profile_id)
        return profile.consent_confirmed and (snapshot is None or snapshot.cloud is None or not snapshot.cloud.clone_reference or profile.cloud is not None and profile.cloud.reference_transfer_confirmed)
    except voice_profiles.VoiceProfileError:
        return False


def get_passages(book_id: str, chapter_index: int) -> AudiobookPassagesResponse:
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        audiobooks.get_book(book_id)
        job = connection.execute("SELECT * FROM audiobook_jobs WHERE book_id=? AND chapter_index=?", (book_id, chapter_index)).fetchone()
        if job is None:
            raise audiobooks.AudiobookError("chapter_not_found", 404)
        rows = connection.execute("SELECT * FROM audiobook_sections WHERE job_id=? ORDER BY section_index", (job["id"],)).fetchall()
        revision = int(job["revision"])
        passages: list[AudiobookPassage] = []
        for row in rows:
            snapshot = SpeechRenderSnapshot.model_validate_json(str(row["snapshot_json"])) if row["snapshot_json"] else None
            complete = row["status"] == "done" and row["output_path"] is not None
            passages.append(AudiobookPassage(id=str(row["passage_id"]), section_index=int(row["section_index"]),
                text=str(row["section_text"]), profile_id=str(row["profile_id"] or audiobooks.get_book(book_id).profile_id),
                speaker=str(row["speaker_name"] or "Narrator"), start_ms=int(row["start_ms"]), end_ms=int(row["end_ms"]),
                status=row["status"], audio_url=f"/api/audiobooks/{book_id}/passages/{row['passage_id']}/audio?revision={revision}" if complete else None,
                renderer="openrouter" if snapshot and snapshot.cloud else "local",
                cloud_provenance=_provenance(Path(str(row["output_path"]))) if complete else None,
                render_identity=str(row["render_identity"]) if row["render_identity"] else None, language=snapshot.text_language if snapshot else str(job["render_language"] or "")))
        return AudiobookPassagesResponse(book_id=book_id, chapter_index=chapter_index, revision=revision, passages=passages)


def passage_audio_path(book_id: str, passage_id: str, revision: int | None = None) -> Path:
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        audiobooks.get_book(book_id)
        row = connection.execute("""SELECT s.output_path,j.revision FROM audiobook_sections s
            JOIN audiobook_jobs j ON j.id=s.job_id WHERE j.book_id=? AND s.passage_id=? AND s.status='done'""",
            (book_id, passage_id)).fetchone()
        if row is None or not row["output_path"]:
            raise audiobooks.AudiobookError("passage_not_ready", 404)
        if revision is not None and int(row["revision"]) != revision:
            raise audiobooks.AudiobookError("passage_changed", 409)
        path = Path(str(row["output_path"]))
        audiobook_narration._contained(path, audiobooks.book_dir(book_id))
        if not path.is_file():
            raise audiobooks.AudiobookError("passage_not_ready", 404)
        return path


def _excerpt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text.strip()
    boundary = text.rfind(" ", 0, limit)
    return text[:boundary if boundary > limit // 2 else limit].strip()


def start_audition(body: CreateAudiobookAuditionRequest, *, book_id: str | None = None, revision: int | None = None) -> AudiobookAudition:
    with _prepare_admission():
        return _start_audition(body, book_id=book_id, revision=revision)


def audition_inputs(body: CreateAudiobookAuditionRequest, chapter_languages: list[str] | None = None) -> list[tuple[str, str, str, str]]:
    planned: list[tuple[str, str, str, str]] = []
    chapters = list(enumerate(body.chapters)) if body.mode == "cast" else [(body.chapter_index, body.chapters[body.chapter_index])]
    for index, chapter in chapters:
        language = (chapter_languages[index] if chapter_languages else body.language) or "en"
        planned.extend((text, profile, speaker, language) for text, profile, speaker in audiobook_narration.plan_text(chapter.text, body.profile_id, body.cast, body.pronunciations))
    chosen: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    remaining = body.max_chars
    for text, profile_id, speaker, language in planned:
        if body.mode == "cast":
            if (profile_id, speaker) in seen:
                continue
            seen.add((profile_id, speaker))
            chosen.append((_excerpt(text, body.max_chars), profile_id, speaker, language))
        elif remaining > 0 and len(chosen) < 17:
            spoken = _excerpt(text, remaining)
            if spoken:
                chosen.append((spoken, profile_id, speaker, language))
                remaining -= len(spoken)
    if not chosen:
        raise audiobooks.AudiobookError("chapter_text_required")
    return chosen


def quote_audition(body: CreateAudiobookAuditionRequest) -> CloudSpeechQuote:
    from .cloud_speech import quote_inputs
    return quote_inputs([(text, profile, language) for text, profile, _, language in audition_inputs(body)])


def _start_audition(body: CreateAudiobookAuditionRequest, *, book_id: str | None = None, revision: int | None = None, chapter_languages: list[str] | None = None) -> AudiobookAudition:
    from .audiobook_cast import require_cast
    if _STOPPING:
        raise audiobooks.AudiobookError("audiobook_backend_stopping", 503)
    require_cast(body.cast)
    if body.chapter_index >= len(body.chapters):
        raise audiobooks.AudiobookError("chapter_not_found", 404)
    identifier, stamp = uuid.uuid4().hex, audiobooks._now()
    chosen = audition_inputs(body, chapter_languages)
    snapshots_by_profile = {(profile, language): capture(profile, language, _workspace(identifier), speech_clone.known_engine_identity()) for _, profile, _, language in chosen}
    clips = [AudiobookAuditionClip(index=index, speaker=speaker, profile_id=profile, text=text, language=language,renderer="openrouter" if snapshots_by_profile[(profile,language)].cloud else "local")
             for index, (text, profile, speaker, language) in enumerate(chosen)]
    public = AudiobookAudition(id=identifier, mode=body.mode, status="queued", clips=clips,
        created_at=stamp, updated_at=stamp, book_id=book_id, chapter_index=body.chapter_index, revision=revision)
    if body.mode == "cast":
        used = {(profile, speaker) for _, profile, speaker, _ in chosen}
        public.skipped_speakers = [member.name for member in body.cast if (member.profile_id, member.name) not in used]
    from .cloud_speech import approve_snapshots
    prepared = [snapshots_by_profile[(clip.profile_id, clip.language)] for clip in clips]
    authorization = approve_snapshots([(clip.text, snapshot) for clip, snapshot in zip(clips, prepared, strict=True)], body.cloud_approval)
    _save(identifier, "audition", _AuditionState(public=public, snapshots=[snapshot.model_copy(update={"cloud_authorization_id": authorization}) for snapshot in prepared]))
    _schedule(identifier, "audition")
    return get_audition(identifier)


def start_book_audition(book_id: str, options: AudiobookAuditionOptions) -> AudiobookAudition:
    from .audiobook_contracts import AudiobookChapterInput
    with _prepare_admission(), audiobooks.publication_lock(book_id), audiobooks._LOCK:
        book, jobs = audiobooks.get_book(book_id), audiobooks.list_jobs(book_id=book_id)
        if options.chapter_index >= len(jobs):
            raise audiobooks.AudiobookError("chapter_not_found", 404)
        request = CreateAudiobookAuditionRequest(title=book.title, profile_id=book.profile_id,
            chapters=[AudiobookChapterInput(title=job.chapter_title, text=job.chapter_text) for job in jobs],
            pronunciations=book.pronunciations, cast=book.cast, language=jobs[options.chapter_index].language,
            **options.model_dump())
        return _start_audition(request, book_id=book_id, revision=jobs[options.chapter_index].revision, chapter_languages=[job.language for job in jobs])


def _synthesize(snapshot: SpeechRenderSnapshot, text: str, target: Path) -> bool:
    outcome = speech_clone.synthesize_to_path(profile_id=snapshot.profile_id, text=text, output_path=target,
        prompt_text=snapshot.prompt_text, prompt_language=snapshot.prompt_language,
        text_language=snapshot.text_language, snapshot=snapshot, require_consent=True)
    if outcome.status not in {"completed", "mock_completed"} or outcome.output_path is None:
        code = outcome.status if outcome.status in {"engine_not_installed", "api_unavailable"} else outcome.detail if outcome.detail.startswith("cloud_") or outcome.detail.startswith("openrouter_") or outcome.detail == "setup_busy" else "chapter_synthesis_failed"
        raise audiobooks.AudiobookError(code)
    _validate_audio(target)
    return outcome.status == "mock_completed"


def _validate_audio(path: Path) -> None:
    if path.is_symlink():
        raise audiobooks.AudiobookError("invalid_speech_audio")
    with wave.open(str(path), "rb") as source:
        expected = source.getnframes() * source.getnchannels() * source.getsampwidth()
        if expected <= 0 or source.getsampwidth() != 2 or source.getcomptype() != "NONE":
            raise audiobooks.AudiobookError("invalid_speech_audio")
        actual = 0
        while chunk := source.readframes(65536):
            actual += len(chunk)
        if actual != expected:
            raise audiobooks.AudiobookError("invalid_speech_audio")


def _consent(profiles: list[str]) -> None:
    for profile in set(profiles):
        try:
            consent = voice_profiles.get_profile(profile).consent_confirmed
        except voice_profiles.VoiceProfileError as exc:
            raise audiobooks.AudiobookError(exc.code, exc.status) from exc
        if not consent:
            raise audiobooks.AudiobookError("consent_required", 403)


def _join(paths: list[Path], target: Path, *, cloud_workflow: bool = False) -> None:
    from .cloud_speech import compatible_pcm
    with compatible_pcm(paths,target.parent,cloud_workflow=cloud_workflow) as prepared:
        _join_pcm(prepared,target)


def _join_pcm(paths: list[Path], target: Path) -> None:
    parameters: tuple[int, int, int] | None = None
    with wave.open(str(target), "wb") as destination:
        for path in paths:
            _validate_audio(path)
            with wave.open(str(path), "rb") as source:
                actual = (source.getnchannels(), source.getsampwidth(), source.getframerate())
                if parameters is None:
                    parameters = actual
                    destination.setparams((*actual, 0, "NONE", "not compressed"))
                elif parameters != actual:
                    raise audiobooks.AudiobookError("audio_format_mismatch")
                while frames := source.readframes(65536):
                    destination.writeframesraw(frames)


def _run_audition(identifier: str, cancelled: threading.Event) -> None:
    state = _AuditionState.model_validate_json(_load(identifier, "audition"))
    public = state.public
    public.status = "running"
    _save(identifier, "audition", state)
    paths: list[Path] = []
    published = False
    try:
        for clip, snapshot in zip(public.clips, state.snapshots, strict=True):
            if cancelled.is_set():
                public.status = "cancelled"
                break
            clip.status = "running"
            _save(identifier, "audition", state)
            target = _workspace(identifier) / f"clip-{clip.index}.wav"
            clip.mock = _synthesize(snapshot, clip.text, target)
            clip.cloud_provenance = _provenance(target)
            with audiobooks._LOCK, voice_profiles._LOCK:
                _consent([clip.profile_id])
                clip.audio_url = f"/api/audiobooks/auditions/{identifier}/clips/{clip.index}/audio"
                clip.status = "done"
                _save(identifier, "audition", state)
            paths.append(target)
        if cancelled.is_set():
            public.status = "cancelled"
        elif public.status != "cancelled":
            if public.mode == "scene":
                _join(paths, _workspace(identifier) / "scene.wav",cloud_workflow=any(snapshot.cloud is not None for snapshot in state.snapshots))
            with audiobooks._LOCK, voice_profiles._LOCK:
                _consent([clip.profile_id for clip in public.clips])
                if public.mode == "scene":
                    public.scene_audio_url = f"/api/audiobooks/auditions/{identifier}/audio"
                public.status = "done"
                public.updated_at = audiobooks._now()
                _save(identifier, "audition", state)
                published = True
    except (audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as exc:
        public.status, public.detail = "failed", exc.code
    except (OSError, EOFError, wave.Error):
        _LOG.exception("Audition publication failed")
        public.status, public.detail = "failed", "invalid_speech_audio"
    finally:
        if not published:
            public.updated_at = audiobooks._now()
            _save(identifier, "audition", state)


def start_repair(book_id: str, chapter_index: int, passage_id: str, body: CreateAudiobookRepairRequest) -> AudiobookRepair:
    with _prepare_admission():
        return _start_repair(book_id, chapter_index, passage_id, body)


def _start_repair(book_id: str, chapter_index: int, passage_id: str, body: CreateAudiobookRepairRequest) -> AudiobookRepair:
    if _STOPPING:
        raise audiobooks.AudiobookError("audiobook_backend_stopping", 503)
    with audiobooks.publication_lock(book_id), audiobooks._LOCK:
        version = get_passages(book_id, chapter_index)
        if version.revision != body.revision:
            raise audiobooks.AudiobookError("passage_changed", 409)
        passage = next((item for item in version.passages if item.id == passage_id), None)
        if passage is None or passage.status != "done":
            raise audiobooks.AudiobookError("passage_not_ready", 409)
        if audiobooks.get_book(book_id).status != "done":
            raise audiobooks.AudiobookError("audiobook_busy", 409)
        _consent([passage.profile_id])
        identifier, stamp = uuid.uuid4().hex, audiobooks._now()
        with closing(audiobooks._connect()) as connection:
            row = connection.execute("SELECT snapshot_json FROM audiobook_sections WHERE passage_id=?", (passage_id,)).fetchone()
        snapshot = SpeechRenderSnapshot.model_validate_json(str(row[0])) if row is not None and row[0] else capture(passage.profile_id, passage.language or "en", _workspace(identifier), speech_clone.known_engine_identity())
        text = (body.text if body.text is not None else passage.text).strip()
        if not text:
            raise audiobooks.AudiobookError("chapter_text_required")
        from .cloud_speech import approve_snapshots
        authorization = approve_snapshots([(text,snapshot)], body.cloud_approval)
        snapshot = snapshot.model_copy(update={"cloud_authorization_id":authorization})
        public = AudiobookRepair(id=identifier, book_id=book_id, chapter_index=chapter_index, passage_id=passage_id,
            revision=version.revision, status="queued", text=text, created_at=stamp, updated_at=stamp,renderer="openrouter" if snapshot.cloud else "local")
        _save(identifier, "repair", _RepairState(public=public, snapshot=snapshot, source_identity=passage.render_identity,
            source_text=passage.text, section_index=passage.section_index))
    _schedule(identifier, "repair")
    return get_repair(identifier)


def _run_repair(identifier: str, cancelled: threading.Event) -> None:
    state = _RepairState.model_validate_json(_load(identifier, "repair"))
    public = state.public
    public.status = "running"
    _save(identifier, "repair", state)
    published = False
    try:
        if cancelled.is_set():
            public.status = "cancelled"
        else:
            public.mock = _synthesize(state.snapshot, public.text, _workspace(identifier) / "candidate.wav")
            public.cloud_provenance = _provenance(_workspace(identifier) / "candidate.wav")
            with audiobooks._LOCK, voice_profiles._LOCK:
                _consent([state.snapshot.profile_id])
                public.status = "cancelled" if cancelled.is_set() else "ready"
                if public.status == "ready":
                    public.audio_url = f"/api/audiobooks/repairs/{identifier}/audio"
                public.updated_at = audiobooks._now()
                _save(identifier, "repair", state)
                published = True
    except (audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as exc:
        public.status, public.detail = "failed", exc.code
    except (OSError, EOFError, wave.Error):
        _LOG.exception("Repair generation failed")
        public.status, public.detail = "failed", "invalid_speech_audio"
    finally:
        if not published:
            public.updated_at = audiobooks._now()
            _save(identifier, "repair", state)


def _check_current(state: _RepairState, requested: int) -> AudiobookPassagesResponse:
    public = state.public
    if get_repair(public.id).status != "ready":
        raise audiobooks.AudiobookError("repair_not_ready", 409)
    version = get_passages(public.book_id, public.chapter_index)
    passage = next((item for item in version.passages if item.id == public.passage_id), None)
    if requested != public.revision or version.revision != public.revision or passage is None or passage.text != state.source_text or passage.render_identity != state.source_identity:
        raise audiobooks.AudiobookError("passage_changed", 409)
    if audiobooks.get_book(public.book_id).status != "done":
        raise audiobooks.AudiobookError("audiobook_busy", 409)
    return version


def accept_repair(identifier: str, body: AcceptAudiobookRepairRequest) -> AudiobookPassagesResponse:
    with _prepare_admission():
        return _accept_repair(identifier, body)


def _accept_repair(identifier: str, body: AcceptAudiobookRepairRequest) -> AudiobookPassagesResponse:
    state = _RepairState.model_validate_json(_load(identifier, "repair"))
    public = state.public
    book_id = public.book_id
    with audiobooks.publication_lock(book_id):
        if public.status != "ready":
            raise audiobooks.AudiobookError("repair_not_ready", 409)
        if state.pending_accept_token is not None:
            _cleanup_stages(state)
        version = _check_current(state, body.revision)
        audiobook_narration._require_current_consent(book_id)
        candidate = _workspace(identifier) / "candidate.wav"
        _validate_audio(candidate)
        token = uuid.uuid4().hex
        chapter_target = audiobooks.chapters_dir(book_id) / f"{public.chapter_index:04d}-repair-{token}.wav"
        export_target = audiobooks.book_dir(book_id) / f"export-{token}.wav"
        sections_root = audiobook_narration._contained(audiobooks.chapters_dir(book_id) / "sections", audiobooks.chapters_dir(book_id))
        sections_root.mkdir(exist_ok=True)
        accepted_pcm = sections_root / f"{public.passage_id}-repair-{token}.wav"
        staged = [accepted_pcm, chapter_target, export_target, export_target.with_suffix(".mp3"), export_target.with_suffix(".m4b")]
        state.pending_accept_token = token
        _save(identifier, "repair", state)
        committed = False
        try:
            paths = [candidate if item.id == public.passage_id else passage_audio_path(book_id, item.id, version.revision) for item in version.passages]
            # Copy candidate into the book-owned immutable sections directory.
            from shutil import copyfile
            audiobook_narration._contained(accepted_pcm, sections_root)
            copyfile(candidate, accepted_pcm)
            if candidate.with_suffix(".cloud.json").is_file():
                copyfile(candidate.with_suffix(".cloud.json"),accepted_pcm.with_suffix(".cloud.json"))
            paths = [accepted_pcm if item.id == public.passage_id else path for item, path in zip(version.passages, paths, strict=True)]
            audiobook_narration.concat_wavs(book_id, paths, chapter_target, controlled=False)
            jobs = audiobooks.list_jobs(book_id=book_id)
            chapter_paths = [chapter_target if job.chapter_index == public.chapter_index else audiobooks.chapter_audio_path(book_id, job.chapter_index) for job in jobs]
            audiobook_narration.concat_wavs(book_id, chapter_paths, export_target, controlled=False)
            from .audiobook_publish import publish_formats
            book = audiobooks.get_book(book_id)
            mp3, m4b, note = publish_formats(book_id, title=book.title, author=book.author,
                chapters=[(job.chapter_title, path) for job, path in zip(jobs, chapter_paths, strict=True)], cover=audiobooks.cover_path_for(book_id),
                canonical_wav=export_target, destination_stem=f"export-{token}")
            if ":export_failed" in note or (book.mp3_ready and mp3 is None) or (book.m4b_ready and m4b is None):
                raise audiobooks.AudiobookError("export_failed", 503)
            cursor = 0
            offsets: list[tuple[int, int, str]] = []
            spans: list[dict[str, object]] = []
            for item, path in zip(version.passages, paths, strict=True):
                end = cursor + audiobook_narration._wav_ms(path)
                offsets.append((cursor, end, item.id))
                spans.append({"speaker": item.speaker, "start_ms": cursor, "end_ms": end})
                cursor = end
            from .narration_pauses import chunk_wav
            pauses = chunk_wav(chapter_target)
            import json
            with audiobooks._LOCK, voice_profiles._LOCK, closing(audiobooks._connect()) as connection:
                _check_current(state, body.revision)
                audiobook_narration._require_current_consent(book_id)
                _consent([state.snapshot.profile_id])
                connection.execute("BEGIN IMMEDIATE")
                for start, end, passage_id in offsets:
                    connection.execute("UPDATE audiobook_sections SET start_ms=?,end_ms=? WHERE passage_id=?", (start, end, passage_id))
                identity = hashlib.sha256(f"{state.snapshot.identity}\n{public.text}\n{file_digest(accepted_pcm)}\n{identifier}".encode()).hexdigest()
                connection.execute("UPDATE audiobook_sections SET section_text=?,text_sha256=?,output_path=?,snapshot_json=?,render_identity=? WHERE passage_id=?",
                    (public.text, hashlib.sha256(public.text.encode()).hexdigest(), str(accepted_pcm), state.snapshot.model_dump_json(), identity, public.passage_id))
                job_id = str(connection.execute("SELECT job_id FROM audiobook_sections WHERE passage_id=?", (public.passage_id,)).fetchone()[0])
                original = connection.execute("SELECT source_sha256 FROM audiobook_reviewed_text WHERE job_id=? AND section_index=?", (job_id, state.section_index)).fetchone()
                source_hash = str(original[0]) if original else hashlib.sha256(state.source_text.encode()).hexdigest()
                speaker = next(item.speaker for item in version.passages if item.id == public.passage_id)
                connection.execute("""INSERT INTO audiobook_reviewed_text(job_id,section_index,source_sha256,profile_id,speaker_name,text)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(job_id,section_index) DO UPDATE SET text=excluded.text""",
                    (job_id, state.section_index, source_hash, state.snapshot.profile_id, speaker, public.text))
                connection.execute("UPDATE audiobook_jobs SET output_path=?,revision=revision+1,pause_json=?,cast_spans_json=?,updated_at=? WHERE book_id=? AND chapter_index=?",
                    (str(chapter_target), json.dumps(pauses), json.dumps(audiobook_narration._collapse_spans(spans)), audiobooks._now(), book_id, public.chapter_index))
                connection.execute("UPDATE audiobook_books SET export_path=?,mp3_export_path=?,m4b_export_path=?,export_note=?,updated_at=? WHERE id=?",
                    (str(export_target), str(mp3) if mp3 else None, str(m4b) if m4b else None, note, audiobooks._now(), book_id))
                public.status, public.updated_at = "accepted", audiobooks._now()
                state.pending_accept_token = None
                connection.execute("UPDATE audiobook_workflows SET payload=? WHERE id=?", (state.model_dump_json(), identifier))
                connection.commit()
                committed = True
        except (OSError, EOFError, wave.Error, sqlite3.Error):
            _LOG.exception("Repair acceptance publication failed")
            raise audiobooks.AudiobookError("audiobook_storage_unavailable", 503) from None
        finally:
            if not committed:
                state.pending_accept_token = token
                from .audiobook_publish import cleanup_pending
                # Do not unlink bytes still potentially held by unverified descendants.
                if not cleanup_pending():
                    try:
                        _cleanup_stages(state)
                    except (OSError, sqlite3.Error):
                        _LOG.exception("Repair staging cleanup deferred to recovery")
                else:
                    from .audiobook_publish import defer_cleanup
                    defer_cleanup(staged)
        return get_passages(book_id, public.chapter_index)


async def _run(identifier: str, kind: Literal["audition", "repair"], cancelled: threading.Event) -> None:
    try:
        worker = _run_audition if kind == "audition" else _run_repair
        await await_cleanup(asyncio.to_thread(worker, identifier, cancelled))
    finally:
        _TASKS.pop(identifier, None)
        _CANCEL.pop(identifier, None)


def _schedule(identifier: str, kind: Literal["audition", "repair"]) -> None:
    cancelled = threading.Event()
    _CANCEL[identifier] = cancelled
    if audiobooks._sync_worker():
        try:
            (_run_audition if kind == "audition" else _run_repair)(identifier, cancelled)
        finally:
            _CANCEL.pop(identifier, None)
    else:
        _TASKS[identifier] = asyncio.create_task(_run(identifier, kind, cancelled), name=f"audiobook-{kind}:{identifier}")


def cancel(identifier: str, kind: Literal["audition", "repair"]) -> AudiobookAudition | AudiobookRepair:
    if kind == "audition":
        state = _AuditionState.model_validate_json(_load(identifier, kind))
    else:
        repair = _RepairState.model_validate_json(_load(identifier, kind))
        if repair.public.status in {"ready", "queued", "running"}:
            repair.public.status = "cancelled"
            _save(identifier, kind, repair)
        if identifier in _CANCEL:
            _CANCEL[identifier].set()
        return get_repair(identifier)
    if identifier in _CANCEL:
        _CANCEL[identifier].set()
    if state.public.status in {"queued", "running"}:
        state.public.status = "cancelled"
        _save(identifier, kind, state)
    return get_audition(identifier)


def audition_audio_path(identifier: str, index: int | None = None) -> Path:
    state = _AuditionState.model_validate_json(_load(identifier, "audition"))
    public = state.public
    selected = state.snapshots if index is None else [state.snapshots[index]] if 0 <= index < len(state.snapshots) else []
    if any(not _preview_allowed(snapshot.profile_id,snapshot) for snapshot in selected):
        raise audiobooks.AudiobookError("consent_required",403)
    if index is None:
        if public.scene_audio_url is None:
            raise audiobooks.AudiobookError("audio_missing", 404)
        _consent([clip.profile_id for clip in public.clips])
        path = _workspace(identifier, create=False) / "scene.wav"
    else:
        clip = next((item for item in public.clips if item.index == index), None)
        if clip is None or clip.audio_url is None:
            raise audiobooks.AudiobookError("audio_missing", 404)
        _consent([clip.profile_id])
        path = _workspace(identifier, create=False) / f"clip-{index}.wav"
    try:
        _validate_audio(path)
    except (OSError, EOFError, wave.Error):
        raise audiobooks.AudiobookError("audio_missing", 404) from None
    return path


def repair_audio_path(identifier: str) -> Path:
    state = _RepairState.model_validate_json(_load(identifier, "repair"))
    _consent([state.snapshot.profile_id])
    if not _preview_allowed(state.snapshot.profile_id,state.snapshot):
        raise audiobooks.AudiobookError("consent_required",403)
    if state.public.audio_url is None:
        raise audiobooks.AudiobookError("audio_missing", 404)
    path = _workspace(identifier, create=False) / "candidate.wav"
    try:
        _validate_audio(path)
    except (OSError, EOFError, wave.Error):
        raise audiobooks.AudiobookError("audio_missing", 404) from None
    return path


def list_auditions(book_id: str, chapter_index: int | None = None) -> list[AudiobookAudition]:
    audiobooks.get_book(book_id)
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        rows = connection.execute("SELECT payload FROM audiobook_workflows WHERE kind='audition' AND book_id=? AND (? IS NULL OR chapter_index=?) ORDER BY created_at DESC,id DESC LIMIT 100", (book_id,chapter_index,chapter_index)).fetchall()
    items = [_AuditionState.model_validate_json(str(row[0])).public for row in rows]
    return [get_audition(item.id) for item in items]


def list_repairs(book_id: str, chapter_index: int, passage_id: str, revision: int | None = None) -> list[AudiobookRepair]:
    get_passages(book_id, chapter_index)
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        rows = connection.execute("SELECT payload FROM audiobook_workflows WHERE kind='repair' AND book_id=? AND chapter_index=? AND passage_id=? AND (? IS NULL OR revision=?) ORDER BY created_at DESC,id DESC LIMIT 100", (book_id,chapter_index,passage_id,revision,revision)).fetchall()
    items = [_RepairState.model_validate_json(str(row[0])).public for row in rows]
    return [get_repair(item.id) for item in items]


def work_busy() -> bool:
    with _PREPARATION_LOCK:
        preparing = _PREPARING > 0
    from .audiobook_publish import cleanup_pending
    return preparing or bool(_CANCEL) or cleanup_pending() or any(not task.done() for task in tuple(_TASKS.values()))


async def start() -> None:
    global _STOPPING
    _STOPPING = False
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        audiobooks._ensure_schema(connection)
        rows = connection.execute("SELECT id,kind,payload FROM audiobook_workflows").fetchall()
    for row in rows:
        try:
            if row["kind"] == "audition":
                state = _AuditionState.model_validate_json(str(row["payload"]))
                if state.public.status in {"queued", "running"}:
                    state.public.status, state.public.detail = "cancelled", "audition_interrupted"
                    _save(str(row["id"]), "audition", state)
            else:
                repair = _RepairState.model_validate_json(str(row["payload"]))
                from .audiobook_publish import cleanup_pending
                if repair.pending_accept_token is not None and not cleanup_pending():
                    _cleanup_stages(repair)
                if repair.public.status in {"queued", "running"}:
                    repair.public.status, repair.public.detail = "cancelled", "repair_interrupted"
                    _save(str(row["id"]), "repair", repair)
        except ValidationError:
            _LOG.warning("Invalid stored audiobook workflow %s", row["id"])


async def shutdown() -> None:
    global _STOPPING
    _STOPPING = True
    for cancelled in tuple(_CANCEL.values()):
        cancelled.set()
    if _TASKS:
        await await_cleanup(asyncio.gather(*tuple(_TASKS.values()), return_exceptions=True))
    from .audiobook_publish import shutdown as drain_encoders
    await await_cleanup(drain_encoders())


async def wait_for(identifier: str) -> None:
    task = _TASKS.get(identifier)
    if task is not None:
        await await_cleanup(asyncio.shield(task))


def quote_repair(book_id: str, chapter_index: int, passage_id: str, body: CreateAudiobookRepairRequest) -> CloudSpeechQuote:
    from .cloud_speech import quote_snapshots
    with audiobooks.publication_lock(book_id), audiobooks._LOCK:
        version = get_passages(book_id, chapter_index)
        if version.revision != body.revision:
            raise audiobooks.AudiobookError("passage_changed",409)
        passage = next((item for item in version.passages if item.id == passage_id and item.status == "done"),None)
        if passage is None:
            raise audiobooks.AudiobookError("passage_not_ready",409)
        with closing(audiobooks._connect()) as connection:
            row = connection.execute("SELECT snapshot_json FROM audiobook_sections WHERE passage_id=?",(passage_id,)).fetchone()
        snapshot = SpeechRenderSnapshot.model_validate_json(str(row[0])) if row is not None and row[0] else capture(passage.profile_id,passage.language or "en",_workspace(uuid.uuid4().hex),speech_clone.known_engine_identity())
        return quote_snapshots([(body.text if body.text is not None else passage.text,snapshot)])


def quote_book_audition(book_id: str, options: AudiobookAuditionOptions) -> CloudSpeechQuote:
    from .audiobook_contracts import AudiobookChapterInput
    from .cloud_speech import quote_inputs
    with audiobooks.publication_lock(book_id), audiobooks._LOCK:
        book,jobs = audiobooks.get_book(book_id),audiobooks.list_jobs(book_id=book_id)
        if options.chapter_index >= len(jobs):
            raise audiobooks.AudiobookError("chapter_not_found",404)
        body = CreateAudiobookAuditionRequest(title=book.title,profile_id=book.profile_id,chapters=[AudiobookChapterInput(title=job.chapter_title,text=job.chapter_text) for job in jobs],cast=book.cast,pronunciations=book.pronunciations,**options.model_dump())
        return quote_inputs([(text,profile,language) for text,profile,_,language in audition_inputs(body,[job.language for job in jobs])])


def _provenance(path: Path) -> CloudSpeechProvenance | None:
    from .cloud_speech import read_provenance
    return read_provenance(path)
