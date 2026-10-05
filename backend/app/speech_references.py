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
from .voice_profile_contracts import CloudSpeechConfiguration


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


class CloudSpeechSnapshot(CloudSpeechConfiguration):
    model_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class SpeechRenderSnapshot(BaseModel, frozen=True):
    profile_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    reference_audio_path: str = Field(default="", max_length=4096)
    reference_sha256: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")
    prompt_text: str = Field(default="", max_length=2000)
    prompt_language: str = Field(min_length=2, max_length=35)
    text_language: str = Field(min_length=2, max_length=35)
    engine: str = "gpt-sovits"
    engine_identity: str | None = Field(default=None, max_length=200)
    settings: SpeechRenderSettings = Field(default_factory=SpeechRenderSettings)
    cloud: CloudSpeechSnapshot | None = None
    cloud_authorization_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")

    @property
    def identity(self) -> str:
        inputs = self.model_dump(exclude={"reference_audio_path", "cloud_authorization_id"})
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
        cloud: CloudSpeechSnapshot | None = None
        if profile.renderer == "openrouter":
            from .cloud_speech import validate_configuration, model_fingerprint, normalize_cloud_language
            if profile.cloud is None:
                raise voice_profiles.VoiceProfileError("cloud_speech_configuration_required")
            validate_configuration(profile.cloud)
            language = normalize_cloud_language(profile.cloud, text_language)
            cloud = CloudSpeechSnapshot(**profile.cloud.model_dump(), model_fingerprint=model_fingerprint(profile.cloud.model))
            if not cloud.clone_reference:
                return SpeechRenderSnapshot(profile_id=profile_id, prompt_language=profile.reference_language,
                    text_language=language, engine="openrouter", cloud=cloud)
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
                prompt_language=profile.reference_language if cloud else normalize_language(profile.reference_language),
                text_language=language if cloud else normalize_language(text_language), engine_identity=None if cloud else engine_identity,
                engine="openrouter" if cloud else "gpt-sovits", cloud=cloud)
        finally:
            temporary.unlink(missing_ok=True)


def normalize_for_profile(profile_id: str, language: str) -> str:
    profile = voice_profiles.get_profile(profile_id)
    if profile.renderer == "openrouter" and profile.cloud is not None:
        from .cloud_speech import normalize_cloud_language
        return normalize_cloud_language(profile.cloud, language)
    return normalize_language(language)
