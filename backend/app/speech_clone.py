"""Talking / audiobook speech-clone worker (GPT-SoVITS-oriented).

Detects an optional GPT-SoVITS checkout, supports mock/dry-run for tests, and
calls the upstream local HTTP API (api.py on port 9880 by default) when the
engine server is running. OpenFabric never downloads pretrained weights.
"""
from __future__ import annotations

import os
import struct
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin

import httpx

from . import voice_profiles
from .config import DATA_DIR, GPT_SOVITS_DIR
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


def api_reachable(timeout_s: float = 1.5) -> bool:
    """Best-effort probe: any TCP/HTTP response from the pinned API base counts as up."""
    base = api_base_url()
    try:
        with httpx.Client(timeout=timeout_s) as client:
            # api.py has no dedicated health route; root GET without params returns 400 JSON when up.
            response = client.get(base + "/")
            return response.status_code < 500
    except httpx.HTTPError:
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
    return "Reference audio."


def _looks_like_wav(data: bytes) -> bool:
    return len(data) > 44 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def _synthesize_via_api(
    *,
    refer_wav_path: str,
    prompt_text: str,
    prompt_language: str,
    text: str,
    text_language: str,
    output_path: Path | None = None,
) -> Path:
    out = Path(output_path) if output_path is not None else trials_root() / f"{uuid.uuid4().hex}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "refer_wav_path": refer_wav_path,
        "prompt_text": prompt_text,
        "prompt_language": prompt_language,
        "text": text,
        "text_language": text_language,
    }
    url = urljoin(api_base_url() + "/", "")
    with httpx.Client(timeout=API_TIMEOUT_S) as client:
        response = client.post(url, json=payload)
    if response.status_code >= 400:
        detail = response.text[:500]
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                detail = str(parsed.get("message") or parsed.get("detail") or detail)
        except Exception:
            pass
        raise RuntimeError(f"GPT-SoVITS API HTTP {response.status_code}: {detail}")
    content_type = (response.headers.get("content-type") or "").lower()
    body = response.content
    if "json" in content_type and not _looks_like_wav(body):
        raise RuntimeError(f"GPT-SoVITS API returned JSON instead of audio: {body[:300]!r}")
    if not body:
        raise RuntimeError("GPT-SoVITS API returned an empty body")
    out.write_bytes(body)
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


def synthesize_to_path(
    *,
    profile_id: str,
    text: str,
    output_path: Path,
    prompt_text: str | None = None,
    prompt_language: str | None = None,
    text_language: str | None = None,
    require_consent: bool = True,
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

    resolved_prompt = _resolve_prompt_text(prompt_text, profile.notes)
    prompt_language_value = (prompt_language or "en").strip() or "en"
    text_language_value = (text_language or "en").strip() or "en"
    refer = str(Path(profile.reference_audio_path).resolve())
    try:
        produced = _synthesize_via_api(
            refer_wav_path=refer,
            prompt_text=resolved_prompt,
            prompt_language=prompt_language_value,
            text=cleaned,
            text_language=text_language_value,
            output_path=output_path,
        )
        return SynthesisOutcome(
            status="completed",
            detail=f"Synthesized via GPT-SoVITS api.py at {api_base_url()}.",
            output_path=produced,
        )
    except Exception as exc:  # noqa: BLE001 — surface engine errors as structured failed status
        return SynthesisOutcome(
            status="failed",
            detail=f"GPT-SoVITS synthesis failed: {exc}",
            install_hints=list(INSTALL_HINTS),
        )


def start_trial(body: SpeechCloneTrialRequest) -> SpeechCloneTrialResponse:
    profile = voice_profiles.get_profile(body.profile_id)
    if not profile.consent_confirmed:
        raise voice_profiles.VoiceProfileError("consent_required", 403)
    text = body.text.strip()
    if not text:
        raise voice_profiles.VoiceProfileError("text_required")

    trial_id = uuid.uuid4().hex
    out = trials_root() / f"{trial_id}.wav"
    outcome = synthesize_to_path(
        profile_id=body.profile_id,
        text=text,
        output_path=out,
        prompt_text=body.prompt_text,
        prompt_language=body.prompt_language,
        text_language=body.text_language,
        require_consent=True,
    )
    return SpeechCloneTrialResponse(
        status=outcome.status,
        detail=outcome.detail,
        engine=body.engine,
        profile_id=body.profile_id,
        install_hints=list(outcome.install_hints),
        trial_id=trial_id if outcome.output_path is not None else None,
        output_path=str(outcome.output_path) if outcome.output_path is not None else None,
    )


def http_status_for(response: SpeechCloneTrialResponse) -> int:
    if response.status == "engine_not_installed":
        return 501
    return 200
