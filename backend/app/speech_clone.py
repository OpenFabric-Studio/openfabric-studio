"""Talking / audiobook speech-clone worker (GPT-SoVITS-oriented).

Detects an optional GPT-SoVITS checkout, supports mock/dry-run for tests, and
does not download model weights. Real HTTP/CLI synthesis is wired in a later
pass once the user installs the engine and pretrained models themselves.
"""
from __future__ import annotations

import os
import struct
import uuid
import wave
from pathlib import Path
from typing import Literal

from . import voice_profiles
from .config import DATA_DIR, GPT_SOVITS_DIR
from .voice_profile_contracts import (
    SpeechCloneEngineStatus,
    SpeechCloneTrialRequest,
    SpeechCloneTrialResponse,
)

GPT_SOVITS_URL = "https://github.com/RVC-Boss/GPT-SoVITS"
INSTALL_HINTS = [
    "Install GPT-SoVITS separately (MIT): " + GPT_SOVITS_URL,
    "Set OPENFABRIC_GPT_SOVITS_DIR (or GPT_SOVITS_DIR) to that checkout.",
    "Download pretrained weights using the upstream README — OpenFabric never auto-fetches them.",
    "Singing voice clone remains Seed-VC; this path is talking / audiobook only.",
    "For CI/tests set OPENFABRIC_SPEECH_CLONE_MOCK=1 to exercise the dry-run path.",
]

# Overridable in tests.
ENGINE_DIR = GPT_SOVITS_DIR
TRIALS_ROOT = DATA_DIR / "speech-clone-trials"


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


def _looks_like_gpt_sovits(root: Path) -> bool:
    if not root.is_dir():
        return False
    markers = (
        root / "GPT_SoVITS",
        root / "api.py",
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


def engine_status() -> SpeechCloneEngineStatus:
    root = resolve_engine_root()
    return SpeechCloneEngineStatus(
        installed=root is not None,
        mock=mock_enabled(),
        root=str(root) if root is not None else None,
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

    # Detection succeeded; live API/CLI invoke + weights remain a follow-up.
    return SpeechCloneTrialResponse(
        status="engine_ready",
        detail=(
            f"GPT-SoVITS checkout detected at {root}. "
            "Synthesis invoke is not wired in this build — install upstream pretrained "
            "weights per their README, then the next OpenFabric pass will call the local API."
        ),
        engine=body.engine,
        profile_id=body.profile_id,
        install_hints=list(INSTALL_HINTS),
        trial_id=None,
        output_path=None,
    )


def http_status_for(response: SpeechCloneTrialResponse) -> int:
    if response.status == "engine_not_installed":
        return 501
    return 200
