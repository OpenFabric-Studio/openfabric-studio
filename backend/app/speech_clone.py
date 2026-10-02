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


def _mock_synthesize(*, profile_id: str, text: str) -> tuple[str, Path]:
    trial_id = uuid.uuid4().hex
    _ = (profile_id, text)
    out = trials_root() / f"{trial_id}.wav"
    _write_silent_wav(out)
    return trial_id, out


def _resolve_prompt_text(body: SpeechCloneTrialRequest, notes: str) -> str:
    if body.prompt_text and body.prompt_text.strip():
        return body.prompt_text.strip()
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
) -> Path:
    trial_id = uuid.uuid4().hex
    out = trials_root() / f"{trial_id}.wav"
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


def start_trial(body: SpeechCloneTrialRequest) -> SpeechCloneTrialResponse:
    profile = voice_profiles.get_profile(body.profile_id)
    if not profile.consent_confirmed:
        raise voice_profiles.VoiceProfileError("consent_required", 403)
    text = body.text.strip()
    if not text:
        raise voice_profiles.VoiceProfileError("text_required")

    if mock_enabled():
        trial_id, out = _mock_synthesize(profile_id=body.profile_id, text=text)
        return SpeechCloneTrialResponse(
            status="mock_completed",
            detail="Dry-run speech clone wrote a silent placeholder WAV (OPENFABRIC_SPEECH_CLONE_MOCK).",
            engine=body.engine,
            profile_id=body.profile_id,
            install_hints=[],
            trial_id=trial_id,
            output_path=str(out),
        )

    root = resolve_engine_root()
    if root is None:
        return SpeechCloneTrialResponse(
            status="engine_not_installed",
            detail=(
                "GPT-SoVITS speech engine is not installed or OPENFABRIC_GPT_SOVITS_DIR "
                "does not point at a valid checkout."
            ),
            engine=body.engine,
            profile_id=body.profile_id,
            install_hints=list(INSTALL_HINTS),
            trial_id=None,
            output_path=None,
        )

    if not api_reachable():
        return SpeechCloneTrialResponse(
            status="api_unavailable",
            detail=(
                f"GPT-SoVITS checkout detected at {root}, but the local API at "
                f"{api_base_url()} is not reachable. Start api.py (see install hints) "
                "after pretrained weights are in place."
            ),
            engine=body.engine,
            profile_id=body.profile_id,
            install_hints=list(INSTALL_HINTS),
            trial_id=None,
            output_path=None,
        )

    prompt_text = _resolve_prompt_text(body, profile.notes)
    prompt_language = (body.prompt_language or "en").strip() or "en"
    text_language = (body.text_language or "en").strip() or "en"
    refer = str(Path(profile.reference_audio_path).resolve())
    try:
        out = _synthesize_via_api(
            refer_wav_path=refer,
            prompt_text=prompt_text,
            prompt_language=prompt_language,
            text=text,
            text_language=text_language,
        )
    except Exception as exc:  # noqa: BLE001 — surface engine errors as structured failed status
        return SpeechCloneTrialResponse(
            status="failed",
            detail=f"GPT-SoVITS synthesis failed: {exc}",
            engine=body.engine,
            profile_id=body.profile_id,
            install_hints=list(INSTALL_HINTS),
            trial_id=None,
            output_path=None,
        )

    return SpeechCloneTrialResponse(
        status="completed",
        detail=f"Synthesized via GPT-SoVITS api.py at {api_base_url()}.",
        engine=body.engine,
        profile_id=body.profile_id,
        install_hints=[],
        trial_id=out.stem,
        output_path=str(out),
    )


def http_status_for(response: SpeechCloneTrialResponse) -> int:
    if response.status == "engine_not_installed":
        return 501
    return 200
