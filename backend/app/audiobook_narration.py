"""Owned audiobook workers, durable short sections and atomic PCM WAV joins.

The upstream speech API is blocking and has no cancellation contract. Control
requests stop at a section boundary; shutdown waits for that call to finish.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid
import wave
from typing import Literal
from contextlib import closing
from pathlib import Path

from pydantic import BaseModel, Field

from . import audiobooks, speech_clone, voice_profiles
from .audiobook_contracts import AudiobookBook, CastMember, PronunciationEntry
from .job_lifecycle import await_cleanup
from .speech_references import SpeechRenderSnapshot, capture

SECTION_CHARS = 1200
_TASKS: dict[str, asyncio.Task[None]] = {}
_SHUTTING_DOWN = False
_LOG = logging.getLogger(__name__)


class _Chapter(BaseModel):
    id: str
    chapter_index: int
    chapter_text: str
    language: str = ""
    bypass_cache: bool = False
    revision: int = 1
    reassemble_only: bool = False


class _Section(BaseModel):
    section_index: int = Field(ge=0)
    section_text: str = Field(min_length=1, max_length=SECTION_CHARS)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["queued", "running", "done"]
    output_path: str | None = None
    profile_id: str = ""
    speaker_name: str = ""
    passage_id: str = ""
    snapshot_json: str | None = None
    start_ms: int = 0
    end_ms: int = 0
    display_text: str | None = None
    gap_after_ms: int | None = None


class _Stopped(Exception):
    pass


def split_sections(text: str) -> list[str]:
    """Keep pause punctuation with the left piece so a request is not cut mid-phrase."""
    result: list[str] = []
    remainder = text
    marks = ("\n\n", ". ", "? ", "! ", "; ", ": ", ", ", "—", "–", "\n")
    while len(remainder) > SECTION_CHARS:
        window = remainder[:SECTION_CHARS]
        boundary = max(window.rfind(mark) for mark in marks)
        if boundary < SECTION_CHARS // 3:
            boundary = window.rfind(" ")
        if boundary < SECTION_CHARS // 3:
            boundary = SECTION_CHARS
        else:
            boundary += 1
        piece = remainder[:boundary]
        if not piece.strip():
            boundary = min(SECTION_CHARS, len(remainder))
            piece = remainder[:boundary]
        result.append(piece)
        remainder = remainder[boundary:]
    if remainder:
        result.append(remainder)
    return result


def _active(identifier: str) -> bool:
    return audiobooks.get_book(identifier).status in {"queued", "running"}


def _require_current_consent(identifier: str) -> None:
    book = audiobooks.get_book(identifier)
    # Cast edits deliberately preserve completed chapters. Their original
    # section provenance remains authoritative when those chapters are reused
    # in a new publication, even after a speaker leaves the current cast.
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        rows = connection.execute(
            """SELECT DISTINCT s.profile_id FROM audiobook_sections s
               JOIN audiobook_jobs j ON j.id = s.job_id
               WHERE j.book_id = ? AND j.status = 'done' AND s.status = 'done'
                     AND s.profile_id != ''""",
            (identifier,),
        ).fetchall()
    used_profiles = {book.profile_id, *(str(row["profile_id"]) for row in rows)}
    for profile_id in used_profiles:
        if not voice_profiles.get_profile(profile_id).consent_confirmed:
            raise audiobooks.AudiobookError("consent_required", 403)
    from .audiobook_cast import require_cast
    require_cast(book.cast)


def _contained(path: Path, root: Path) -> Path:
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise audiobooks.AudiobookError("audiobook_storage_unavailable", 503)
    return path


def concat_wavs(identifier: str, paths: list[Path], target: Path, *, controlled: bool = True) -> Path:
    from .cloud_speech import compatible_pcm
    root = audiobooks.book_dir(identifier)
    for path in paths:
        _contained(path,root)
    with compatible_pcm(paths,target.parent,cloud_workflow=bool(audiobooks.get_book(identifier).cloud_models)) as prepared:
        return _concat_wavs(identifier,prepared,target,controlled=controlled)


def concat_timed_wavs(identifier: str, paths: list[Path], target: Path, gaps_after_ms: list[int], *, controlled: bool = True) -> list[tuple[int, int]]:
    """Join accepted dry takes and silence, deriving cue edges from output samples."""
    from .cloud_speech import compatible_pcm
    if len(paths) != len(gaps_after_ms) or any(gap < 0 or gap > 10000 for gap in gaps_after_ms):
        raise audiobooks.AudiobookError("invalid_pacing")
    root = audiobooks.book_dir(identifier)
    for path in paths:
        _contained(path, root)
    with compatible_pcm(paths, target.parent, cloud_workflow=bool(audiobooks.get_book(identifier).cloud_models)) as prepared:
        return _join_pcm(identifier, prepared, target, gaps_after_ms, controlled=controlled)


def _concat_wavs(identifier: str, paths: list[Path], target: Path, *, controlled: bool = True) -> Path:
    _join_pcm(identifier, paths, target, [0] * len(paths), controlled=controlled)
    return target


def _join_pcm(identifier: str, paths: list[Path], target: Path, gaps_after_ms: list[int], *, controlled: bool) -> list[tuple[int, int]]:
    """Stream matching PCM sections without an unmanaged ffmpeg subprocess."""
    if not paths:
        raise audiobooks.AudiobookError("audio_missing")
    root = audiobooks.book_dir(identifier)
    _contained(target, root)
    temporary = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.tmp.wav")
    cursor = 0
    offsets: list[tuple[int, int]] = []
    try:
        parameters: tuple[int, int, int] | None = None
        with wave.open(str(temporary), "wb") as destination:
            for path, gap_ms in zip(paths, gaps_after_ms, strict=True):
                _contained(path, root)
                with wave.open(str(path), "rb") as source:
                    actual = (source.getnchannels(), source.getsampwidth(), source.getframerate())
                    if parameters is None:
                        parameters = actual
                        destination.setnchannels(actual[0])
                        destination.setsampwidth(actual[1])
                        destination.setframerate(actual[2])
                    elif actual != parameters:
                        raise audiobooks.AudiobookError("audio_format_mismatch")
                    start_frame = cursor
                    while frames := source.readframes(65536):
                        if controlled and not _active(identifier):
                            raise _Stopped()
                        destination.writeframesraw(frames)
                        cursor += len(frames) // (actual[0] * actual[1])
                    offsets.append((round(start_frame * 1000 / actual[2]), round(cursor * 1000 / actual[2])))
                    gap_frames = round(gap_ms * actual[2] / 1000)
                    # PCM8 is unsigned, all other integer PCM widths use signed zero.
                    zero = bytes([128]) if actual[1] == 1 else bytes(actual[1])
                    while gap_frames:
                        if controlled and not _active(identifier):
                            raise _Stopped()
                        count = min(gap_frames, 65536)
                        destination.writeframesraw(zero * actual[0] * count)
                        cursor += count
                        gap_frames -= count
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        temporary.replace(target)
        return offsets
    finally:
        temporary.unlink(missing_ok=True)


def _planned(identifier: str, chapter_text: str, job_id: str | None = None) -> list[tuple[str, str, str]]:
    """Spoken pieces as (text, profile id, speaker label)."""
    book = audiobooks.get_book(identifier)
    planned = plan_text(chapter_text, book.profile_id, book.cast, book.pronunciations)
    if job_id is not None:
        with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
            rows = connection.execute("SELECT * FROM audiobook_reviewed_text WHERE job_id=?", (job_id,)).fetchall()
        replacements = {int(row["section_index"]): row for row in rows}
        for index, (text, profile, speaker) in enumerate(planned):
            replacement = replacements.get(index)
            if replacement is not None and replacement["source_sha256"] == hashlib.sha256(text.encode()).hexdigest() and replacement["profile_id"] == profile and replacement["speaker_name"] == speaker:
                planned[index] = (str(replacement["text"]), profile, speaker)
    return planned


def plan_text(chapter_text: str, narrator_id: str, cast: list[CastMember], pronunciations: list[PronunciationEntry]) -> list[tuple[str, str, str]]:
    """The same speaker routing, spoken replacements and sectioning for previews."""
    from .audiobook_cast import split_turns
    from .audiobook_pronounce import apply_pronunciations
    pieces: list[tuple[str, str, str]] = []
    for profile_id, speaker, spoken in split_turns(chapter_text, narrator_id, cast):
        pronounced = apply_pronunciations(spoken, pronunciations)
        if not pronounced.strip():
            continue
        for piece in split_sections(pronounced):
            pieces.append((piece, profile_id, speaker))
    return pieces


def plan_display_text(chapter_text: str, narrator_id: str, cast: list[CastMember], pronunciations: list[PronunciationEntry]) -> list[str | None]:
    """Retain original spelling only at boundaries that survive spoken replacements.

    A section split inside a replacement has no precise written equivalent and
    deliberately returns null. Never reconstruct old wording from current text.
    """
    from .audiobook_cast import split_turns
    from .audiobook_pronounce import apply_pronunciations_with_mapping
    result: list[str | None] = []
    for _, _, original in split_turns(chapter_text, narrator_id, cast):
        spoken, boundaries = apply_pronunciations_with_mapping(original, pronunciations)
        if not spoken.strip():
            continue
        offset = 0
        for piece in split_sections(spoken):
            end = offset + len(piece)
            start_source, end_source = boundaries[offset], boundaries[end]
            result.append(original[start_source:end_source] if start_source is not None and end_source is not None else None)
            offset = end
    return result



def passage_gaps(book: AudiobookBook, speakers: list[str], overrides: list[int | None]) -> list[int]:
    if len(speakers) != len(overrides):
        raise audiobooks.AudiobookError("invalid_pacing")
    return [override if override is not None else
        book.passage_gap_ms + (book.speaker_change_gap_ms if speaker != speakers[index + 1] else 0)
        if index + 1 < len(speakers) else 0
        for index, (speaker, override) in enumerate(zip(speakers, overrides, strict=True))]


def _stored_sections(chapter: _Chapter) -> list[_Section]:
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        rows = connection.execute("SELECT * FROM audiobook_sections WHERE job_id=? ORDER BY section_index", (chapter.id,)).fetchall()
    return [_Section.model_validate(dict(row)) for row in rows]


def _section_matches(section: _Section, index: int, text: str, profile_id: str, speaker: str, narrator_id: str) -> bool:
    stored_profile = section.profile_id or narrator_id
    if section.section_index != index or section.section_text != text or stored_profile != profile_id:
        return False
    if section.text_sha256 != hashlib.sha256(text.encode()).hexdigest():
        return False
    if section.speaker_name and section.speaker_name != speaker:
        return False
    return True


def _sections(chapter: _Chapter, planned: list[tuple[str, str, str]], narrator_id: str) -> list[_Section]:
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        audiobooks._ensure_schema(connection)
        rows = connection.execute(
            """SELECT section_index, section_text, text_sha256, status, output_path, profile_id, speaker_name, passage_id, snapshot_json, start_ms, end_ms, display_text, gap_after_ms
               FROM audiobook_sections WHERE job_id = ? ORDER BY section_index""",
            (chapter.id,),
        ).fetchall()
        if rows:
            sections = [_Section.model_validate(dict(row)) for row in rows]
            if len(sections) != len(planned) or any(
                not _section_matches(section, index, text, profile_id, speaker, narrator_id)
                for index, (section, (text, profile_id, speaker)) in enumerate(zip(sections, planned, strict=True))
            ):
                raise audiobooks.AudiobookError("narration_sections_changed")
            # Newly seeded cloud/redo rows have a frozen speech snapshot before
            # this worker runs. Only still-unrendered rows can acquire original
            # wording from the current source; never retrofit historical PCM.
            book_id = str(connection.execute("SELECT book_id FROM audiobook_jobs WHERE id=?", (chapter.id,)).fetchone()[0])
            book = audiobooks.get_book(book_id)
            originals = plan_text(chapter.chapter_text, narrator_id, book.cast, book.pronunciations)
            displays = plan_display_text(chapter.chapter_text, narrator_id, book.cast, book.pronunciations)
            for index, section in enumerate(sections):
                if section.status != "done" and section.display_text is None and index < len(originals) and originals[index] == (section.section_text, section.profile_id or narrator_id, section.speaker_name or "Narrator"):
                    section.display_text = displays[index]
                    connection.execute("UPDATE audiobook_sections SET display_text=? WHERE job_id=? AND section_index=?", (section.display_text, chapter.id, section.section_index))
            connection.commit()
            pending = [section for section in sections if section.snapshot_json is None and
                (section.status != "done" or section.output_path is None or not Path(section.output_path).is_file())]
            if pending:
                book_id = str(connection.execute("SELECT book_id FROM audiobook_jobs WHERE id=?", (chapter.id,)).fetchone()[0])
                from .speech_references import normalize_language
                language = (chapter.language or audiobooks.get_book(book_id).language or "en").lower().split("-", 1)[0]
                reference_root = _contained(audiobooks.book_dir(book_id) / "references", audiobooks.book_dir(book_id))
                snapshots = {voice: capture(voice, language, reference_root, speech_clone.known_engine_identity())
                    for voice in dict.fromkeys(section.profile_id or narrator_id for section in pending)}
                for section in pending:
                    section.snapshot_json = snapshots[section.profile_id or narrator_id].model_dump_json()
                    connection.execute("UPDATE audiobook_sections SET snapshot_json=? WHERE job_id=? AND section_index=?", (section.snapshot_json, chapter.id, section.section_index))
                # Historical PCM has no verified language/reference provenance.
                if all(section.snapshot_json is not None for section in sections):
                    connection.execute("UPDATE audiobook_jobs SET render_language=? WHERE id=?", (language, chapter.id))
                connection.commit()
            return sections
        book_id = str(connection.execute("SELECT book_id FROM audiobook_jobs WHERE id = ?", (chapter.id,)).fetchone()[0])
        from .speech_references import normalize_language
        language = (chapter.language or audiobooks.get_book(book_id).language or "en").lower().split("-", 1)[0]
        reference_root = _contained(audiobooks.book_dir(book_id) / "references", audiobooks.book_dir(book_id))
        snapshots = {voice_id: capture(voice_id, language, reference_root, speech_clone.known_engine_identity()) for voice_id in dict.fromkeys(voice for _, voice, _ in planned)}
        book = audiobooks.get_book(book_id)
        displays = plan_display_text(chapter.chapter_text, narrator_id, book.cast, book.pronunciations)
        originals = plan_text(chapter.chapter_text, narrator_id, book.cast, book.pronunciations)
        for index, (text, profile_id, speaker) in enumerate(planned):
            display = displays[index] if index < len(originals) and originals[index] == (text, profile_id, speaker) else None
            connection.execute("""INSERT INTO audiobook_sections
                (job_id, section_index, section_text, text_sha256, status, output_path, profile_id, speaker_name, passage_id, snapshot_json,display_text)
                VALUES (?, ?, ?, ?, 'queued', NULL, ?, ?, ?, ?, ?)""",
                (chapter.id, index, text, hashlib.sha256(text.encode()).hexdigest(), profile_id, speaker, uuid.uuid5(uuid.UUID(chapter.id), str(index)).hex, snapshots[profile_id].model_dump_json(), display))
        connection.execute("UPDATE audiobook_jobs SET render_language = ? WHERE id = ?", (language, chapter.id))
        connection.commit()
        rows = connection.execute(
            """SELECT section_index, section_text, text_sha256, status, output_path, profile_id, speaker_name, passage_id, snapshot_json, start_ms, end_ms, display_text, gap_after_ms
               FROM audiobook_sections WHERE job_id = ? ORDER BY section_index""",
            (chapter.id,),
        ).fetchall()
        return [_Section.model_validate(dict(row)) for row in rows]


def _section_status(job_id: str, index: int, status: str, path: Path | None = None) -> None:
    measured_snapshot: SpeechRenderSnapshot | None = None
    measured_text = ""
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        connection.execute("UPDATE audiobook_sections SET status = ?, output_path = ? WHERE job_id = ? AND section_index = ?", (status, str(path) if path else None, job_id, index))
        if status == "done" and path is not None:
            row = connection.execute("SELECT snapshot_json,section_text,passage_id FROM audiobook_sections WHERE job_id=? AND section_index=?", (job_id, index)).fetchone()
            if row is not None and row[0]:
                snapshot = SpeechRenderSnapshot.model_validate_json(str(row[0]))
                measured_snapshot, measured_text = snapshot, str(row[1])
                from .speech_references import file_digest
                identity = hashlib.sha256(f"{snapshot.identity}\n{row[1]}\n{file_digest(path)}\n{path.name}".encode()).hexdigest()
                connection.execute("UPDATE audiobook_sections SET render_identity=? WHERE job_id=? AND section_index=?", (identity, job_id, index))
        connection.commit()
    if measured_snapshot is not None and path is not None:
        from .narration_duration import record_measurement
        record_measurement(measured_snapshot, measured_text, path)


def _job_status(job_id: str, status: str, detail: str = "", path: Path | None = None) -> None:
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        row = connection.execute("SELECT b.status, j.status FROM audiobook_books b JOIN audiobook_jobs j ON j.book_id = b.id WHERE j.id = ?", (job_id,)).fetchone()
        if row is None:
            raise audiobooks.AudiobookError("book_not_found", 404)
        if row[0] in {"paused", "cancelled"}:
            if row[1] == "done":
                return
            status = "cancelled" if row[0] == "cancelled" else "queued"
            detail, path = "stopped_at_section_boundary", None
        audiobooks._update_job(connection, job_id, status=status, detail=detail, output_path=str(path) if path else None)
        connection.commit()


def _synthesize(identifier: str, profile_id: str, text: str, target: Path, snapshot: SpeechRenderSnapshot | None = None) -> None:
    # Check the control flag after acquiring the shared lock: a waiting book
    # may have been paused while another book or speech trial used the engine.
    with speech_clone.SYNTHESIS_LOCK:
        if not _active(identifier):
            raise _Stopped()
        outcome = speech_clone.synthesize_to_path(profile_id=profile_id, text=text, output_path=target, require_consent=True,
            text_language=snapshot.text_language if snapshot else "en", snapshot=snapshot)
        if outcome.status not in {"completed", "mock_completed"} or outcome.output_path is None:
            _LOG.warning("Audiobook section failed: %s", outcome.status)
            code = outcome.status if outcome.status in {"engine_not_installed", "api_unavailable"} else outcome.detail if outcome.detail.startswith("cloud_") or outcome.detail.startswith("openrouter_") or outcome.detail == "setup_busy" else "chapter_synthesis_failed"
            raise audiobooks.AudiobookError(code)


def _wav_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        if rate <= 0:
            return 0
        return int(round(handle.getnframes() * 1000 / rate))


def _collapse_spans(spans: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    for span in spans:
        if merged and merged[-1]["speaker"] == span["speaker"] and merged[-1]["end_ms"] == span["start_ms"]:
            merged[-1]["end_ms"] = span["end_ms"]
        else:
            merged.append(dict(span))
    return merged


def _process_chapter(identifier: str, profile_id: str, chapter: _Chapter) -> None:
    from .audiobook_pronounce import PronunciationError
    try:
        retained_sections = _stored_sections(chapter) if chapter.reassemble_only else None
        planned = [(section.section_text, section.profile_id or profile_id, section.speaker_name or "Narrator") for section in retained_sections] if retained_sections is not None else _planned(identifier, chapter.chapter_text, chapter.id)
    except PronunciationError as exc:
        _job_status(chapter.id, "failed", exc.code)
        return
    if not planned:
        _job_status(chapter.id, "failed", "chapter_text_required")
        return
    output_root = audiobooks.chapters_dir(identifier)
    sections_root = _contained(output_root / "sections", output_root)
    sections_root.mkdir(exist_ok=True)
    paths: list[Path] = []
    _job_status(chapter.id, "running", "narrating_sections")
    try:
        sections = retained_sections if retained_sections is not None else _sections(chapter, planned, profile_id)
        for section in sections:
            if not _active(identifier):
                raise _Stopped()
            voice_id = section.profile_id or profile_id
            # Reusing PCM is still publication of this person's voice. The
            # synthesis boundary only checks consent on a cache miss.
            profile = voice_profiles.get_profile(voice_id)
            if not profile.consent_confirmed:
                raise audiobooks.AudiobookError("consent_required", 403)
            target = _contained(sections_root / f"{section.passage_id or chapter.id}-{chapter.revision}-{section.section_index:04d}.wav", output_root)
            if section.status == "done" and section.output_path is not None:
                retained = _contained(Path(section.output_path), output_root)
                if retained.is_file():
                    paths.append(retained)
                    continue
            if chapter.reassemble_only:
                raise audiobooks.AudiobookError("accepted_audio_missing", 409)
            _section_status(chapter.id, section.section_index, "running")
            temporary = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.partial.wav")
            try:
                from .audiobook_cache import reuse, store
                snapshot = SpeechRenderSnapshot.model_validate_json(section.snapshot_json) if section.snapshot_json else None
                identity = snapshot.identity if snapshot is not None and snapshot.engine_identity is not None else None
                if chapter.bypass_cache or not reuse(voice_id, section.section_text, temporary, render_identity=identity):
                    _synthesize(identifier, voice_id, section.section_text, temporary, snapshot)
                # The upstream reply must be real PCM WAV before publication.
                with wave.open(str(temporary), "rb") as handle:
                    if handle.getnframes() <= 0:
                        raise audiobooks.AudiobookError("invalid_speech_audio")
                temporary.replace(target)
                cloud_note = temporary.with_suffix(".cloud.json")
                if cloud_note.is_file() and not cloud_note.is_symlink():
                    cloud_note.replace(target.with_suffix(".cloud.json"))
                store(voice_id, section.section_text, target, render_identity=identity)
            finally:
                temporary.unlink(missing_ok=True)
            _section_status(chapter.id, section.section_index, "done", target)
            paths.append(target)
        _require_current_consent(identifier)
        gaps = passage_gaps(audiobooks.get_book(identifier), [section.speaker_name or "Narrator" for section in sections], [section.gap_after_ms for section in sections])
        target = output_root / f"{chapter.chapter_index:04d}-{uuid.uuid4().hex}.wav"
        offsets = concat_timed_wavs(identifier, paths, target, gaps)
        try:
            from .narration_pauses import chunk_wav
            audiobooks.save_pause_spans(chapter.id, chunk_wav(target))
        except (OSError, wave.Error, ValueError):
            _LOG.warning("Pause spans were not stored for %s", chapter.id, exc_info=True)
        spans: list[dict[str, object]] = []
        for section, (start, end) in zip(sections, offsets, strict=True):
            spans.append({"speaker": section.speaker_name or "Narrator", "start_ms": start, "end_ms": end})
            with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
                connection.execute("UPDATE audiobook_sections SET start_ms = ?, end_ms = ? WHERE job_id = ? AND section_index = ?", (start, end, chapter.id, section.section_index))
                connection.commit()
        audiobooks.save_cast_spans(chapter.id, _collapse_spans(spans))
        # Publication and revocation use the same profile lock. Keep the
        # established book→profile lock order and hold neither during encoding.
        with audiobooks._LOCK, voice_profiles._LOCK:
            _require_current_consent(identifier)
            _job_status(chapter.id, "done", "narration_completed", target)
            with closing(audiobooks._connect()) as connection:
                connection.execute("UPDATE audiobook_jobs SET duration_ms=? WHERE id=?", (_wav_ms(target), chapter.id))
                connection.commit()
    except _Stopped:
        status = "cancelled" if audiobooks.get_book(identifier).status == "cancelled" else "queued"
        _job_status(chapter.id, status, "stopped_at_section_boundary")
        raise
    except (audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as exc:
        _job_status(chapter.id, "failed", exc.code)
    except (OSError, EOFError, wave.Error):
        _LOG.exception("Audiobook audio publication failed")
        _job_status(chapter.id, "failed", "invalid_speech_audio")


def _process_book(identifier: str) -> None:
    if not _active(identifier):
        return
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        transition = connection.execute("UPDATE audiobook_books SET status = 'running', export_path = NULL, mp3_export_path = NULL, m4b_export_path = NULL, export_note = '', updated_at = ? WHERE id = ? AND status IN ('queued', 'running')", (audiobooks._now(), identifier))
        if transition.rowcount == 0:
            return
        connection.commit()
        rows = connection.execute("SELECT id, chapter_index, chapter_text, language, revision, bypass_cache,reassemble_only FROM audiobook_jobs WHERE book_id = ? AND status != 'done' ORDER BY chapter_index", (identifier,)).fetchall()
        chapters = [_Chapter.model_validate(dict(row)) for row in rows]
    profile_id = audiobooks.get_book(identifier).profile_id
    # Freeze every chapter's reference inputs before the first upstream call.
    for chapter in chapters:
        if chapter.reassemble_only:
            continue
        try:
            _sections(chapter, _planned(identifier, chapter.chapter_text, chapter.id), profile_id)
        except (audiobooks.AudiobookError, voice_profiles.VoiceProfileError) as exc:
            _job_status(chapter.id, "failed", exc.code)
            continue
    for chapter in chapters:
        if not _active(identifier):
            return
        _process_chapter(identifier, profile_id, chapter)
    if not _active(identifier):
        return
    jobs = audiobooks.list_jobs(book_id=identifier)
    if any(job.status != "done" or job.output_path is None for job in jobs):
        with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
            if not _active(identifier):
                return
            audiobooks._update_book(connection, identifier, status="failed", clear_export=True)
            connection.commit()
        return
    with audiobooks.publication_lock(identifier):
        _require_current_consent(identifier)
        paths = [audiobooks.chapter_audio_path(identifier, job.chapter_index) for job in jobs]
        exported = concat_wavs(identifier, paths, audiobooks.book_dir(identifier) / "export.wav")
        if not _active(identifier):
            return
        from .audiobook_publish import publish_formats
        book = audiobooks.get_book(identifier)
        try:
            mp3, m4b, note = publish_formats(
                identifier,
                title=book.title,
                author=book.author,
                chapters=[(job.chapter_title, path) for job, path in zip(jobs, paths, strict=True)],
                cover=audiobooks.cover_path_for(identifier),
            )
        except audiobooks.AudiobookError as exc:
            mp3, m4b, note = None, None, exc.code
        with audiobooks._LOCK, voice_profiles._LOCK:
            _require_current_consent(identifier)
            audiobooks.finish_book(identifier, wav=exported, mp3=mp3, m4b=m4b, note=note)


def run_sync(identifier: str) -> None:
    try:
        _process_book(identifier)
    except _Stopped:
        pass
    except Exception:
        _LOG.exception("Audiobook narration failed")
        with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
            if _active(identifier):
                audiobooks._update_book(connection, identifier, status="failed", clear_export=True)
                connection.commit()


async def _run(identifier: str) -> None:
    try:
        # Cancellation cannot discard ownership of the blocking speech call.
        await await_cleanup(asyncio.to_thread(run_sync, identifier))
    finally:
        if _TASKS.get(identifier) is asyncio.current_task():
            _TASKS.pop(identifier, None)


def schedule(identifier: str) -> None:
    if _SHUTTING_DOWN:
        raise audiobooks.AudiobookError("audiobook_backend_stopping", 503)
    previous = _TASKS.get(identifier)
    if previous is None or previous.done():
        _TASKS[identifier] = asyncio.create_task(_run(identifier), name=f"audiobook:{identifier}")


def work_busy() -> bool:
    # A paused/cancelled worker can still own its current upstream section.
    if any(not task.done() for task in tuple(_TASKS.values())):
        return True
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        audiobooks._ensure_schema(connection)
        return connection.execute("SELECT 1 FROM audiobook_books WHERE status IN ('queued', 'running') LIMIT 1").fetchone() is not None


async def wait_for_book(identifier: str) -> None:
    task = _TASKS.get(identifier)
    if task is not None:
        await await_cleanup(asyncio.shield(task))


def pause_book(identifier: str) -> AudiobookBook:
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        book = audiobooks.get_book(identifier)
        if book.status not in {"queued", "running", "paused"}:
            raise audiobooks.AudiobookError("audiobook_not_active", 409)
        audiobooks._update_book(connection, identifier, status="paused")
        connection.commit()
    return audiobooks.get_book(identifier)


def cancel_book(identifier: str) -> AudiobookBook:
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        book = audiobooks.get_book(identifier)
        if book.status == "done":
            raise audiobooks.AudiobookError("audiobook_already_completed", 409)
        audiobooks._update_book(connection, identifier, status="cancelled", clear_export=True)
        connection.execute("UPDATE audiobook_jobs SET status = 'cancelled', detail = 'stopped_at_section_boundary' WHERE book_id = ? AND status != 'done'", (identifier,))
        connection.commit()
    return audiobooks.get_book(identifier)


async def resume_book(identifier: str) -> AudiobookBook:
    await wait_for_book(identifier)
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        book = audiobooks.get_book(identifier)
        if book.status not in {"paused", "cancelled", "failed"}:
            raise audiobooks.AudiobookError("audiobook_not_resumable", 409)
        profile = voice_profiles.get_profile(book.profile_id)
        if not profile.consent_confirmed:
            raise audiobooks.AudiobookError("consent_required", 403)
        from .audiobook_cast import require_cast
        require_cast(book.cast)
        audiobooks._update_book(connection, identifier, status="queued", clear_export=True)
        connection.execute("UPDATE audiobook_jobs SET status = 'queued', detail = 'resuming_sections' WHERE book_id = ? AND status != 'done'", (identifier,))
        connection.commit()
    schedule(identifier)
    return audiobooks.get_book(identifier)


async def start() -> None:
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = False
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        audiobooks._ensure_schema(connection)
        connection.execute("UPDATE audiobook_books SET status = 'paused', updated_at = ? WHERE status IN ('queued', 'running')", (audiobooks._now(),))
        connection.execute("UPDATE audiobook_jobs SET status = 'queued', detail = 'narration_interrupted' WHERE status = 'running'")
        connection.execute("UPDATE audiobook_sections SET status = 'queued', output_path = NULL WHERE status = 'running'")
        connection.commit()


async def shutdown() -> None:
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = True
    for identifier in tuple(_TASKS):
        if _active(identifier):
            pause_book(identifier)
    await await_cleanup(asyncio.gather(*tuple(_TASKS.values()), return_exceptions=True))
