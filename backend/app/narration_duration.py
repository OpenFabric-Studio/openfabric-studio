"""Bounded speech pace observations, keyed to frozen voice/model/language/settings.

Character counts work across supported writing systems without pretending a
word count means the same thing in English and Chinese. No media/text is stored
here. Unknown external model identities and silent mocks never calibrate pace.
"""
from __future__ import annotations

import hashlib
import logging
import math
import sqlite3
import statistics
import wave
from contextlib import closing
from pathlib import Path

from . import audiobooks, speech_clone, voice_profiles
from .audiobook_contracts import NarrationDurationGuidance, NarrationDurationRequest
from .speech_references import CloudSpeechSnapshot, SpeechRenderSnapshot, file_digest, normalize_for_profile

_LOG = logging.getLogger(__name__)


def _characters(text: str) -> int:
    return sum(character.isalnum() for character in text)


def _verified(snapshot: SpeechRenderSnapshot) -> bool:
    return snapshot.cloud is not None or snapshot.engine_identity is not None and not snapshot.engine_identity.startswith("openfabric-mock")


def _has_signal(path: Path) -> tuple[int, bool]:
    """Stream bounded integer PCM; reject empty/constant/silent recordings."""
    if path.is_symlink() or path.stat().st_size > 128 * 1024 * 1024:
        return 0, False
    with wave.open(str(path), "rb") as source:
        rate, frames, width = source.getframerate(), source.getnframes(), source.getsampwidth()
        if rate <= 0 or width not in {1, 2, 3, 4}:
            return 0, False
        milliseconds = round(frames * 1000 / rate)
        if milliseconds < 500 or milliseconds > 180000:
            return milliseconds, False
        low, high = 2**31, -(2**31)
        while chunk := source.readframes(65536):
            values = [int.from_bytes(chunk[index:index + width], "little", signed=width > 1) for index in range(0, len(chunk), width)]
            if values:
                low, high = min(low, min(values)), max(high, max(values))
            if high - low > max(4, 2 ** (width * 8 - 12)):
                return milliseconds, True
        return milliseconds, False


def record_measurement(snapshot: SpeechRenderSnapshot, text: str, path: Path, *, mock: bool = False) -> None:
    if mock or not _verified(snapshot):
        return
    characters = _characters(text)
    if characters < 10:
        return
    try:
        milliseconds, signal = _has_signal(path)
        pace = characters * 1000 / milliseconds if milliseconds else 0
        if not signal or not 0.5 <= pace <= 100:
            return
        source_hash = hashlib.sha256(f"{text}\n{file_digest(path)}".encode()).hexdigest()
        with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
            audiobooks._ensure_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""INSERT OR IGNORE INTO narration_measurements
                (render_key,source_hash,characters,duration_ms,created_at) VALUES(?,?,?,?,?)""",
                (snapshot.identity, source_hash, characters, milliseconds, audiobooks._now()))
            connection.execute("""DELETE FROM narration_measurements WHERE render_key=? AND source_hash NOT IN
                (SELECT source_hash FROM narration_measurements WHERE render_key=? ORDER BY created_at DESC,source_hash LIMIT 100)""",
                (snapshot.identity, snapshot.identity))
            connection.commit()
    except (OSError, EOFError, wave.Error, sqlite3.Error, audiobooks.AudiobookError):
        # Guidance is ancillary: a completed recording must remain publishable.
        _LOG.warning("Narration pace observation unavailable", exc_info=True)


def _current_snapshot(profile_id: str, language: str) -> SpeechRenderSnapshot:
    """Inspect the same identity inputs without copying references or loading models."""
    with voice_profiles._LOCK:
        profile = voice_profiles.get_profile(profile_id)
        if not profile.consent_confirmed:
            raise voice_profiles.VoiceProfileError("consent_required", 403)
        normalized = normalize_for_profile(profile_id, language)
        cloud: CloudSpeechSnapshot | None = None
        if profile.renderer == "openrouter":
            from .cloud_speech import model_fingerprint
            if profile.cloud is None:
                raise voice_profiles.VoiceProfileError("cloud_speech_configuration_required")
            cloud = CloudSpeechSnapshot(**profile.cloud.model_dump(), model_fingerprint=model_fingerprint(profile.cloud.model))
            if not cloud.clone_reference:
                return SpeechRenderSnapshot(profile_id=profile_id, prompt_language=profile.reference_language,
                    text_language=normalized, engine="openrouter", cloud=cloud)
        source = Path(profile.reference_audio_path)
        root = voice_profiles.profiles_root() / profile_id
        if root.is_symlink() or source.is_symlink() or source.resolve().parent != root.resolve() or not source.is_file():
            raise voice_profiles.VoiceProfileError("profile_storage_unavailable", 503)
        from .speech_references import normalize_language
        return SpeechRenderSnapshot(profile_id=profile_id, reference_sha256=file_digest(source),
            prompt_text=profile.reference_transcript.strip(), prompt_language=profile.reference_language if cloud else normalize_language(profile.reference_language),
            text_language=normalized, engine_identity=None if cloud else speech_clone.known_engine_identity(),
            engine="openrouter" if cloud else "gpt-sovits", cloud=cloud)


def guidance(body: NarrationDurationRequest) -> NarrationDurationGuidance:
    try:
        if body.passage_source is not None:
            from .audiobook_workflows import passage_render_snapshot
            source = body.passage_source
            snapshot = passage_render_snapshot(source.book_id, source.chapter_index, source.passage_id, source.revision)
            if snapshot.profile_id != body.profile_id:
                raise audiobooks.AudiobookError("duration_source_mismatch", 409)
        else:
            snapshot = _current_snapshot(body.profile_id, body.language)
    except (OSError, EOFError, sqlite3.Error):
        _LOG.warning("Narration duration inputs unavailable", exc_info=True)
        raise audiobooks.AudiobookError("duration_guidance_unavailable", 503) from None
    if not _verified(snapshot):
        return NarrationDurationGuidance(state="unavailable", reason="model_unverified", target_seconds=body.target_seconds)
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        audiobooks._ensure_schema(connection)
        rows = connection.execute("SELECT characters,duration_ms FROM narration_measurements WHERE render_key=? ORDER BY created_at DESC LIMIT 100", (snapshot.identity,)).fetchall()
    if not rows:
        return NarrationDurationGuidance(state="unavailable", reason="no_matching_takes", target_seconds=body.target_seconds, render_key=snapshot.identity)
    paces = [int(row["characters"]) * 1000 / int(row["duration_ms"]) for row in rows if int(row["duration_ms"]) > 0 and int(row["characters"]) > 0]
    if not paces or sum(int(row["duration_ms"]) for row in rows) < 1500:
        return NarrationDurationGuidance(state="unavailable", reason="insufficient_speech", target_seconds=body.target_seconds, render_key=snapshot.identity)
    pace = statistics.median(paces)
    # The envelope is empirical and conservative, never an accuracy guarantee.
    slow, fast = min(min(paces), pace * 0.75), max(max(paces), pace * 1.25)
    characters = _characters(body.text)
    return NarrationDurationGuidance(state="approximate", reason="measured_takes", target_seconds=body.target_seconds,
        measurement_count=len(rows), measured_audio_ms=sum(int(row["duration_ms"]) for row in rows),
        characters_per_second=pace, suggested_characters=min(20000, math.floor(body.target_seconds * pace)),
        estimated_min_ms=math.floor(characters * 1000 / fast), estimated_max_ms=math.ceil(characters * 1000 / slow), render_key=snapshot.identity)
