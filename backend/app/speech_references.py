"""Immutable GPT-SoVITS reference inputs; consent is deliberately not a snapshot.

An API URL or checkout receipt cannot identify the API's loaded checkpoint.
Real external APIs therefore have no reusable cache identity until an actual
loaded-model identity protocol is available. The synthetic test engine does.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from . import voice_profiles


def normalize_language(value: str) -> str:
    """Supported api.py modes, verified against RVC-Boss/GPT-SoVITS api.py.

    BCP47 regions do not select a different GPT-SoVITS language mode. Unsupported
    languages are rejected instead of quietly producing English narration.
    """
    code = (value or "en").strip().lower()
    if code in {"all_zh", "all_ja", "all_ko", "all_yue", "auto", "auto_yue"}:
        return code
    primary = code.split("-", 1)[0]
    if primary not in {"en", "zh", "ja", "ko", "yue"}:
        raise voice_profiles.VoiceProfileError("speech_language_unsupported")
    return primary


class SpeechRenderSettings(BaseModel, frozen=True):
    """Explicit api.py sampling defaults, so later API defaults cannot change a job."""
    top_k: int = Field(default=20, ge=1, le=100)
    top_p: float = Field(default=0.6, gt=0, le=1)
    temperature: float = Field(default=0.6, gt=0, le=2)
    speed: float = Field(default=1.0, ge=0.25, le=4)


class SpeechRenderSnapshot(BaseModel, frozen=True):
    profile_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    reference_audio_path: str = Field(min_length=1, max_length=4096)
    reference_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_text: str = Field(min_length=1, max_length=2000)
    prompt_language: str = Field(min_length=2, max_length=35)
    text_language: str = Field(min_length=2, max_length=35)
    engine: str = "gpt-sovits"
    engine_identity: str | None = Field(default=None, max_length=200)
    settings: SpeechRenderSettings = Field(default_factory=SpeechRenderSettings)

    @property
    def identity(self) -> str:
        inputs = self.model_dump(exclude={"reference_audio_path"})
        return hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def capture(profile_id: str, text_language: str, directory: Path, engine_identity: str | None) -> SpeechRenderSnapshot:
    """Copy and hash the same bytes before any synthesis or await boundary."""
    with voice_profiles._LOCK:
        profile = voice_profiles.get_profile(profile_id)
        if not profile.consent_confirmed:
            raise voice_profiles.VoiceProfileError("consent_required", 403)
        if not profile.reference_transcript.strip():
            raise voice_profiles.VoiceProfileError("reference_transcript_required")
        source = Path(profile.reference_audio_path)
        owned = voice_profiles.profiles_root() / profile_id
        if owned.is_symlink() or source.is_symlink() or source.resolve().parent != owned.resolve() or not source.is_file():
            raise voice_profiles.VoiceProfileError("profile_storage_unavailable", 503)
        if directory.is_symlink():
            raise voice_profiles.VoiceProfileError("profile_storage_unavailable", 503)
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / f".{uuid.uuid4().hex}.reference{source.suffix}"
        digest = hashlib.sha256()
        try:
            with source.open("rb") as reader, temporary.open("xb") as writer:
                while chunk := reader.read(65536):
                    digest.update(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            fingerprint = digest.hexdigest()
            target = directory / f"{profile_id}-{fingerprint}{source.suffix}"
            if target.is_symlink():
                raise voice_profiles.VoiceProfileError("profile_storage_unavailable", 503)
            temporary.replace(target)
            return SpeechRenderSnapshot(profile_id=profile_id, reference_audio_path=str(target),
                reference_sha256=fingerprint, prompt_text=profile.reference_transcript.strip(),
                prompt_language=normalize_language(profile.reference_language), text_language=normalize_language(text_language), engine_identity=engine_identity)
        finally:
            temporary.unlink(missing_ok=True)
