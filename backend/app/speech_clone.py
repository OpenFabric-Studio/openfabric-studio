"""Talking / audiobook speech-clone worker (GPT-SoVITS-oriented).

Detects an optional GPT-SoVITS checkout, supports mock/dry-run for tests, and
calls the upstream local HTTP API (api.py on port 9880 by default) when the
engine server is running. OpenFabric never downloads pretrained weights.
"""
from __future__ import annotations

import os
import io
import asyncio
import logging
import struct
import threading
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin

import httpx

from . import voice_profiles
from .speech_references import SpeechRenderSettings, SpeechRenderSnapshot, file_digest, normalize_language
from .config import DATA_DIR, GPT_SOVITS_DIR
from .module_evidence import record_capability_success
from .job_lifecycle import await_cleanup
from .voice_profile_contracts import (
    SpeechCloneEngineStatus,
    SpeechCloneTrialRequest,
    SpeechCloneTrialResponse,
)

GPT_SOVITS_URL = "https://github.com/RVC-Boss/GPT-SoVITS"
# Pinned upstream entrypoint: RVC-Boss/GPT-SoVITS api.py (POST / on port 9880).
# Payload keys: refer_wav_path, prompt_text, prompt_language, text, text_language.
# Success → WAV body (HTTP 200). Failure → JSON (HTTP 400).
DEFAULT_API_BASE = "http://127.0.0.1:9880"
INSTALL_HINTS = [
    "Install GPT-SoVITS separately (MIT): " + GPT_SOVITS_URL,
    "Run ./setup_speech.sh from the OpenFabric Studio repo (clone + Python 3.11 venv; no weight download).",
    "Set OPENFABRIC_GPT_SOVITS_DIR (or GPT_SOVITS_DIR) to that checkout.",
    "Download pretrained weights using the upstream README — OpenFabric never auto-fetches them.",
    "Start the API from the checkout: .venv/bin/python api.py -a 127.0.0.1 -p 9880 -d cpu  (or -d mps if supported).",
    "Optional: OPENFABRIC_GPT_SOVITS_API_URL (default http://127.0.0.1:9880).",
    "Singing voice clone remains Seed-VC; this path is talking / audiobook only.",
    "For CI/tests set OPENFABRIC_SPEECH_CLONE_MOCK=1 to exercise the dry-run path.",
]

# Overridable in tests.
ENGINE_DIR = GPT_SOVITS_DIR
TRIALS_ROOT = DATA_DIR / "speech-clone-trials"
API_TIMEOUT_S = 120.0
MAX_SPEECH_AUDIO_BYTES = 128 * 1024 * 1024
SYNTHESIS_LOCK = threading.RLock()
_STOPPING = threading.Event()
_LOG = logging.getLogger(__name__)


def trials_root() -> Path:
    root = TRIALS_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def mock_enabled() -> bool:
    return os.getenv("OPENFABRIC_SPEECH_CLONE_MOCK", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def api_base_url() -> str:
    raw = os.getenv("OPENFABRIC_GPT_SOVITS_API_URL", "").strip() or DEFAULT_API_BASE
    return raw.rstrip("/")


def _looks_like_gpt_sovits(root: Path) -> bool:
    if not root.is_dir():
        return False
    markers = (
        root / "GPT_SoVITS",
        root / "api.py",
        root / "api_v2.py",
        root / "webui.py",
        root / "GPT_SoVITS" / "inference_webui.py",
    )
    return any(path.exists() for path in markers)


def resolve_engine_root() -> Path | None:
    root = Path(ENGINE_DIR)
    if _looks_like_gpt_sovits(root):
        return root.resolve()
    return None


def engine_installed(_engine: Literal["speech", "gpt-sovits"] = "gpt-sovits") -> bool:
    return resolve_engine_root() is not None


async def _probe_api(timeout_s: float) -> bool:
    async with asyncio.timeout(timeout_s):
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            # api.py has no dedicated health route; missing params return 400.
            # Inspect headers only: health probing never consumes engine output.
            async with client.stream("GET", api_base_url() + "/") as response:
                return response.status_code < 500


def api_reachable(timeout_s: float = 1.5) -> bool:
    """Bounded reachability probe; a response is not synthesis verification."""
    try:
        return asyncio.run(_probe_api(timeout_s))
    except (httpx.HTTPError, TimeoutError):
        return False


def engine_status() -> SpeechCloneEngineStatus:
    root = resolve_engine_root()
    reachable = api_reachable() if root is not None and not mock_enabled() else False
    return SpeechCloneEngineStatus(
        installed=root is not None,
        mock=mock_enabled(),
        root=str(root) if root is not None else None,
        api_base_url=api_base_url(),
        api_reachable=reachable,
        install_hints=list(INSTALL_HINTS),
    )


def _write_silent_wav(path: Path, *, duration_s: float = 0.25, rate: int = 16000) -> None:
    """Minimal PCM WAV so tests and UI can treat mock output as real audio."""
    frames = max(1, int(rate * duration_s))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack("<" + "h" * frames, *([0] * frames)))


def _resolve_prompt_text(prompt_text: str | None, notes: str) -> str:
    if prompt_text and prompt_text.strip():
        return prompt_text.strip()
    if notes.strip():
        return notes.strip()
    raise voice_profiles.VoiceProfileError("reference_transcript_required")


def _looks_like_wav(data: bytes | bytearray) -> bool:
    return len(data) > 44 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def _validate_pcm_wav(data: bytes | bytearray) -> None:
    if not _looks_like_wav(data):
        raise RuntimeError("invalid_speech_audio")
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            expected = source.getnframes() * source.getnchannels() * source.getsampwidth()
            if expected <= 0 or expected > MAX_SPEECH_AUDIO_BYTES or source.getcomptype() != "NONE":
                raise RuntimeError("invalid_speech_audio")
            actual = 0
            while chunk := source.readframes(65536):
                actual += len(chunk)
            if actual != expected:
                raise RuntimeError("invalid_speech_audio")
    except (wave.Error, EOFError) as exc:
        raise RuntimeError("invalid_speech_audio") from exc


async def _request_speech_audio(url: str, payload: dict[str, str | int | float]) -> bytearray:
    """An absolute request deadline includes headers and every streamed byte."""
    body = bytearray()
    async with asyncio.timeout(API_TIMEOUT_S):
        async with httpx.AsyncClient(timeout=API_TIMEOUT_S) as client:
            async with client.stream("POST", url, json=payload) as response:
                if response.status_code >= 400:
                    raise RuntimeError("speech_api_failed")
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    if len(body) + len(chunk) > MAX_SPEECH_AUDIO_BYTES:
                        raise RuntimeError("speech_audio_too_large")
                    body.extend(chunk)
    return body


def _synthesize_via_api(
    *,
    refer_wav_path: str,
    prompt_text: str,
    prompt_language: str,
    text: str,
    text_language: str,
    output_path: Path | None = None,
    settings: SpeechRenderSettings | None = None,
) -> Path:
    out = Path(output_path) if output_path is not None else trials_root() / f"{uuid.uuid4().hex}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, str | int | float] = {
        "refer_wav_path": refer_wav_path,
        "prompt_text": prompt_text,
        "prompt_language": prompt_language,
        "text": text,
        "text_language": text_language,
    }
    if settings is not None:
        payload.update(top_k=settings.top_k, top_p=settings.top_p, temperature=settings.temperature, speed=settings.speed)
    url = urljoin(api_base_url() + "/", "")
    # This synchronous boundary is called from a request/owned narration
    # worker thread. Async I/O supplies cancellable absolute timeout behavior.
    body = asyncio.run(_request_speech_audio(url, payload))
    _validate_pcm_wav(body)
    temporary = out.with_name(f".{out.stem}.{uuid.uuid4().hex}.tmp.wav")
    try:
        temporary.write_bytes(body)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        temporary.replace(out)
    finally:
        temporary.unlink(missing_ok=True)
    return out


@dataclass(frozen=True)
class SynthesisOutcome:
    """Shared result for speech trials and audiobook chapter jobs."""

    status: Literal[
        "engine_not_installed",
        "engine_ready",
        "api_unavailable",
        "mock_completed",
        "completed",
        "failed",
    ]
    detail: str
    output_path: Path | None = None
    install_hints: list[str] = field(default_factory=list)


def _synthesize_unlocked(
    *,
    profile_id: str,
    text: str,
    output_path: Path,
    prompt_text: str | None = None,
    prompt_language: str | None = None,
    text_language: str | None = None,
    require_consent: bool = True,
    snapshot: SpeechRenderSnapshot | None = None,
) -> SynthesisOutcome:
    """Synthesize talking speech into ``output_path`` (mock, API, or structured failure).

    Used by speech trials and audiobook chapter workers. Always re-reads the
    profile so consent cannot be skipped after enqueue.
    """
    profile = voice_profiles.get_profile(profile_id)
    if require_consent and not profile.consent_confirmed:
        raise voice_profiles.VoiceProfileError("consent_required", 403)
    cleaned = text.strip()
    if not cleaned:
        raise voice_profiles.VoiceProfileError("text_required")
    if (snapshot is not None and snapshot.cloud is not None) or (snapshot is None and profile.renderer == "openrouter"):
        from .cloud_speech import synthesize
        return synthesize(profile_id=profile_id, text=cleaned, output_path=output_path, snapshot=snapshot)
    resolved_prompt = snapshot.prompt_text if snapshot is not None else _resolve_prompt_text(prompt_text, profile.reference_transcript)
    target_language = normalize_language(text_language or "en")
    reference_language = normalize_language(prompt_language or profile.reference_language)
    if snapshot is not None:
        if snapshot.profile_id != profile_id or snapshot.engine_identity != known_engine_identity():
            raise voice_profiles.VoiceProfileError("speech_engine_changed", 409)
        reference = Path(snapshot.reference_audio_path)
        if reference.is_symlink() or not reference.is_file() or file_digest(reference) != snapshot.reference_sha256:
            raise voice_profiles.VoiceProfileError("speech_reference_changed", 409)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if mock_enabled():
        _write_silent_wav(output_path)
        return SynthesisOutcome(
            status="mock_completed",
            detail="Dry-run speech clone wrote a silent placeholder WAV (OPENFABRIC_SPEECH_CLONE_MOCK).",
            output_path=output_path,
        )

    root = resolve_engine_root()
    if root is None:
        return SynthesisOutcome(
            status="engine_not_installed",
            detail=(
                "GPT-SoVITS speech engine is not installed or OPENFABRIC_GPT_SOVITS_DIR "
                "does not point at a valid checkout."
            ),
            install_hints=list(INSTALL_HINTS),
        )

    if not api_reachable():
        return SynthesisOutcome(
            status="api_unavailable",
            detail=(
                f"GPT-SoVITS checkout detected at {root}, but the local API at "
                f"{api_base_url()} is not reachable. Start api.py (see install hints) "
                "after pretrained weights are in place."
            ),
            install_hints=list(INSTALL_HINTS),
        )

    prompt_language_value = snapshot.prompt_language if snapshot is not None else reference_language
    text_language_value = snapshot.text_language if snapshot is not None else target_language
    refer = snapshot.reference_audio_path if snapshot is not None else str(Path(profile.reference_audio_path).resolve())
    try:
        produced = _synthesize_via_api(
            refer_wav_path=refer,
            prompt_text=resolved_prompt,
            prompt_language=prompt_language_value,
            text=cleaned,
            text_language=text_language_value,
            output_path=output_path,
            **({"settings": snapshot.settings} if snapshot is not None else {}),
        )
        try:
            record_capability_success("speech")
        except Exception:
            _LOG.exception("Speech capability receipt could not be saved")
        return SynthesisOutcome(
            status="completed",
            detail=f"Synthesized via GPT-SoVITS api.py at {api_base_url()}.",
            output_path=produced,
        )
    except Exception:  # noqa: BLE001 — keep technical details on the backend
        _LOG.exception("Speech synthesis failed")
        return SynthesisOutcome(
            status="failed",
            detail="speech_synthesis_failed",
            install_hints=list(INSTALL_HINTS),
        )


def synthesize_to_path(
    *, profile_id: str, text: str, output_path: Path,
    prompt_text: str | None = None, prompt_language: str | None = None,
    text_language: str | None = None, require_consent: bool = True,
    snapshot: SpeechRenderSnapshot | None = None,
) -> SynthesisOutcome:
    """Serialize books and speech trials, rechecking consent inside the lock."""
    from .module_jobs import ModuleSetupError, speech_admission
    if _STOPPING.is_set():
        return SynthesisOutcome(status="failed", detail="speech_backend_stopping")
    try:
        with speech_admission(), SYNTHESIS_LOCK:
            if _STOPPING.is_set():
                return SynthesisOutcome(status="failed", detail="speech_backend_stopping")
            return _synthesize_unlocked(profile_id=profile_id, text=text, output_path=output_path,
                                        prompt_text=prompt_text, prompt_language=prompt_language,
                                        text_language=text_language, require_consent=require_consent,
                                        **({"snapshot": snapshot} if snapshot is not None else {}))
    except ModuleSetupError as exc:
        return SynthesisOutcome(status="failed", detail=exc.code)


def known_engine_identity() -> str | None:
    """Only identities of the actual active renderer may authorize cache reuse."""
    return "openfabric-mock-pcm-v1" if mock_enabled() else None


def start() -> None:
    _STOPPING.clear()


def begin_shutdown() -> None:
    """Close admission before async cleanup yields to queued request threads."""
    _STOPPING.set()


def _drain_synthesis() -> None:
    with SYNTHESIS_LOCK:
        pass


async def shutdown() -> None:
    begin_shutdown()
    await await_cleanup(asyncio.to_thread(_drain_synthesis))


def start_trial(body: SpeechCloneTrialRequest) -> SpeechCloneTrialResponse:
    profile = voice_profiles.get_profile(body.profile_id)
    if not profile.consent_confirmed:
        raise voice_profiles.VoiceProfileError("consent_required", 403)
    text = body.text.strip()
    if not text:
        raise voice_profiles.VoiceProfileError("text_required")

    trial_id = uuid.uuid4().hex
    out = trials_root() / f"{trial_id}.wav"
    snapshot: SpeechRenderSnapshot | None = None
    if profile.renderer == "openrouter":
        from .speech_references import capture
        from .cloud_speech import approve_snapshots
        snapshot = capture(profile.id,body.text_language or "en",trials_root()/"references",None)
        authorization = approve_snapshots([(text,snapshot)],body.cloud_approval)
        snapshot = snapshot.model_copy(update={"cloud_authorization_id":authorization})
    outcome = synthesize_to_path(
        profile_id=body.profile_id,
        text=text,
        output_path=out,
        prompt_text=body.prompt_text,
        prompt_language=body.prompt_language,
        text_language=body.text_language,
        require_consent=True,
        **({"snapshot": snapshot} if snapshot is not None else {}),
    )
    from .cloud_speech import read_provenance
    provenance = read_provenance(out) if outcome.output_path else None
    return SpeechCloneTrialResponse(
        status=outcome.status,
        detail=outcome.detail,
        engine="openrouter" if profile.renderer == "openrouter" else body.engine,
        profile_id=body.profile_id,
        install_hints=list(outcome.install_hints),
        trial_id=trial_id if outcome.output_path is not None else None,
        output_path=str(outcome.output_path) if outcome.output_path is not None else None,
        cloud_receipt_id=provenance.receipt_id if provenance else None,
    )


def http_status_for(response: SpeechCloneTrialResponse) -> int:
    if response.status == "engine_not_installed":
        return 501
    return 200
