"""Cost and immutable input preparation for existing narration/preview workflows."""
from __future__ import annotations

import hashlib
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from . import audiobook_narration, audiobooks, cloud_speech, speech_clone, voice_profiles
from .audiobook_contracts import AudiobookCloudControlRequest, CreateAudiobookRequest
from .speech_references import SpeechRenderSnapshot, capture
from .voice_profile_contracts import CloudSpeechApproval, CloudSpeechQuote

PreparedSection = tuple[str, SpeechRenderSnapshot, str]
PreparedChapter = tuple[str, list[PreparedSection]]


def creation_inputs(body: CreateAudiobookRequest) -> list[tuple[str, str, str]]:
    return [(text, profile, body.language or "en") for chapter in body.chapters
        for text, profile, _ in audiobook_narration.plan_text(chapter.text.strip(), body.profile_id, body.cast, body.pronunciations)]


def quote_creation(body: CreateAudiobookRequest) -> CloudSpeechQuote:
    return cloud_speech.quote_inputs(creation_inputs(body))


def prepare_creation(body: CreateAudiobookRequest) -> list[PreparedChapter]:
    inputs = creation_inputs(body)
    if not any(voice_profiles.get_profile(profile).renderer == "openrouter" for _, profile, _ in inputs):
        return []
    captured: dict[str, SpeechRenderSnapshot] = {}
    for _, profile, language in inputs:
        if profile not in captured:
            captured[profile] = capture(profile, language, cloud_speech._root() / "references", speech_clone.known_engine_identity())
    authorization = cloud_speech.approve_snapshots([(text, captured[profile]) for text, profile, _ in inputs], body.cloud_approval)
    prepared: list[PreparedChapter] = []
    for chapter in body.chapters:
        pieces = audiobook_narration.plan_text(chapter.text.strip(), body.profile_id, body.cast, body.pronunciations)
        prepared.append((body.language or "en", [(text, captured[profile].model_copy(update={"cloud_authorization_id": authorization}), speaker) for text, profile, speaker in pieces]))
    return prepared


def seed_sections(connection: sqlite3.Connection, job_id: str, sections: list[PreparedSection]) -> None:
    for index, (text, snapshot, speaker) in enumerate(sections):
        connection.execute("""INSERT INTO audiobook_sections
            (job_id,section_index,section_text,text_sha256,status,profile_id,speaker_name,passage_id,snapshot_json)
            VALUES(?,?,?,?,'queued',?,?,?,?)""", (job_id,index,text,hashlib.sha256(text.encode()).hexdigest(),snapshot.profile_id,speaker,
            uuid.uuid5(uuid.UUID(job_id), str(index)).hex,snapshot.model_dump_json()))
    if sections:
        connection.execute("UPDATE audiobook_jobs SET render_language=? WHERE id=?", (sections[0][1].text_language,job_id))


def control_snapshots(book_id: str, body: AudiobookCloudControlRequest) -> list[tuple[str, SpeechRenderSnapshot]]:
    book = audiobooks.get_book(book_id)
    current = {book.profile_id, *(member.profile_id for member in book.cast)}
    if not book.cloud_models and not any(voice_profiles.get_profile(profile).renderer == "openrouter" for profile in current):
        return []
    jobs = audiobooks.list_jobs(book_id=book_id)
    if body.action == "regenerate":
        jobs = [job for job in jobs if job.chapter_index == body.chapter_index]
        if not jobs:
            raise audiobooks.AudiobookError("chapter_not_found", 404)
    else:
        jobs = [job for job in jobs if job.status != "done"]
    result: list[tuple[str, SpeechRenderSnapshot]] = []
    for job in jobs:
        planned = audiobook_narration._planned(book_id, job.chapter_text, job.id)
        with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
            rows = connection.execute("SELECT * FROM audiobook_sections WHERE job_id=? ORDER BY section_index", (job.id,)).fetchall()
        if rows and body.action != "regenerate":
            for row in rows:
                if row["status"] == "done" and row["output_path"] and Path(str(row["output_path"])).is_file():
                    continue
                snapshot = SpeechRenderSnapshot.model_validate_json(str(row["snapshot_json"])) if row["snapshot_json"] else capture(str(row["profile_id"] or book.profile_id), job.language or "en", cloud_speech._root() / "references", speech_clone.known_engine_identity())
                result.append((str(row["section_text"]), snapshot))
        else:
            for text, profile, _ in planned:
                result.append((text, capture(profile,job.language or "en",cloud_speech._root()/"references",speech_clone.known_engine_identity())))
    return result


def quote_control(book_id: str, body: AudiobookCloudControlRequest) -> CloudSpeechQuote:
    return cloud_speech.quote_snapshots(control_snapshots(book_id,body))


def approve_control(book_id: str, body: AudiobookCloudControlRequest) -> None:
    with audiobooks.publication_lock(book_id),audiobooks._LOCK:
        captured = control_snapshots(book_id,body)
        ensure_retry_safe(captured)
        authorization = cloud_speech.approve_snapshots(captured,body.cloud_approval)
        if authorization is None:
            return
        iterator = iter(captured)
        jobs = [job for job in audiobooks.list_jobs(book_id=book_id) if job.status != "done"]
        with closing(audiobooks._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for job in jobs:
                rows = connection.execute("SELECT * FROM audiobook_sections WHERE job_id=? ORDER BY section_index",(job.id,)).fetchall()
                if rows:
                    for row in rows:
                        if row["status"] == "done" and row["output_path"] and Path(str(row["output_path"])).is_file():
                            continue
                        text,snapshot = next(iterator)
                        if text != str(row["section_text"]):
                            raise audiobooks.AudiobookError("narration_sections_changed",409)
                        snapshot = snapshot.model_copy(update={"cloud_authorization_id":authorization})
                        connection.execute("UPDATE audiobook_sections SET snapshot_json=? WHERE job_id=? AND section_index=?",(snapshot.model_dump_json(),job.id,int(row["section_index"])))
                else:
                    planned = audiobook_narration._planned(book_id,job.chapter_text,job.id)
                    sections: list[PreparedSection] = []
                    for text,_,speaker in planned:
                        captured_text,snapshot = next(iterator)
                        if text != captured_text:
                            raise audiobooks.AudiobookError("narration_sections_changed",409)
                        sections.append((text,snapshot.model_copy(update={"cloud_authorization_id":authorization}),speaker))
                    seed_sections(connection,job.id,sections)
            record_models(connection,book_id,captured)
            connection.commit()


def ensure_retry_safe(captured: list[tuple[str,SpeechRenderSnapshot]]) -> None:
    from . import openrouter_requests
    for _,snapshot in captured:
        if snapshot.cloud is None or snapshot.cloud_authorization_id is None:
            continue
        with cloud_speech._LOCK,closing(cloud_speech._connect()) as connection:
            rows = connection.execute("SELECT receipt_id,normalized FROM renders WHERE approval_id=?",(snapshot.cloud_authorization_id,)).fetchall()
        for row in rows:
            if not row["normalized"]:
                state = openrouter_requests.get(str(row["receipt_id"])).state
                if state in {"submitting","submission_unknown","submitted","completed","canceled_tracking"}:
                    raise voice_profiles.VoiceProfileError("cloud_speech_submission_unknown",409)


def record_models(connection: sqlite3.Connection, book_id: str, captured: list[tuple[str,SpeechRenderSnapshot]]) -> None:
    row = connection.execute("SELECT cloud_models_json FROM audiobook_books WHERE id=?",(book_id,)).fetchone()
    if row is None:
        raise audiobooks.AudiobookError("book_not_found",404)
    models = set(audiobooks._MODEL_IDS.validate_json(str(row[0])))
    models.update(snapshot.cloud.model for _,snapshot in captured if snapshot.cloud is not None)
    import json
    connection.execute("UPDATE audiobook_books SET cloud_models_json=? WHERE id=?",(json.dumps(sorted(models)),book_id))
