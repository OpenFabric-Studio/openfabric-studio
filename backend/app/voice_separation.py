"""Voice-only separation presets; optional MSST is configured on the server."""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path
from typing import Callable

from .config import DEMUCS_DIR, LOG_DIR
from .managed_layout import engine_default
from .job_lifecycle import await_cleanup, kill_process_tree, spawn_process
from .stems import SpawnProcess, gpu_lock, separate_file
from .gpu_lease import gpu_lease
from .voice_contracts import SeparationQuality, VoiceSeparationOption, VoiceSeparationOptionsResponse

ROFORMER_DIR = Path(os.getenv("VOICE_ROFORMER_DIR", engine_default('Music-Source-Separation-Training', str(Path(__file__).resolve().parents[2] / "external" / "Music-Source-Separation-Training"))))
ROFORMER_CONFIG = Path(os.getenv("VOICE_ROFORMER_CONFIG", ""))
ROFORMER_CHECKPOINT = Path(os.getenv("VOICE_ROFORMER_CHECKPOINT", ""))
ROFORMER_MODEL_TYPE = os.getenv("VOICE_ROFORMER_MODEL_TYPE", "mel_band_roformer")


class VoiceSeparationError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def roformer_python() -> Path:
    return ROFORMER_DIR / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def separation_options() -> VoiceSeparationOptionsResponse:
    demucs = (DEMUCS_DIR / "pyproject.toml").is_file()
    configured = all(path.is_file() for path in (ROFORMER_DIR / "inference.py", roformer_python(), ROFORMER_CONFIG, ROFORMER_CHECKPOINT)) and ROFORMER_MODEL_TYPE in ("bs_roformer", "mel_band_roformer")
    return VoiceSeparationOptionsResponse(options=[
        VoiceSeparationOption(id="fast", available=demucs, reason="" if demucs else "demucs_not_installed"),
        VoiceSeparationOption(id="high", available=demucs, reason="" if demucs else "demucs_not_installed"),
        VoiceSeparationOption(id="roformer", available=configured, reason="" if configured else "roformer_not_configured"),
    ])


def find_vocal_output(root: Path) -> Path:
    candidates = [path for path in root.rglob("*.wav") if path.stem == "vocals" or path.stem.endswith("_vocals")]
    if len(candidates) != 1:
        raise ValueError("ambiguous_vocal_output")
    vocal = candidates[0]
    if not vocal.resolve().is_relative_to(root.resolve()) or not vocal.is_file() or vocal.stat().st_size == 0:
        raise ValueError("invalid_vocal_output")
    return vocal


async def separate_vocal(
    audio: Path, out_dir: Path, *, quality: SeparationQuality, log_name: str,
    on_proc: Callable[[asyncio.subprocess.Process | None], None] | None = None,
    spawn: SpawnProcess | None = None,
) -> Path:
    if quality != "roformer":
        stems = await separate_file(audio, out_dir, quality=quality, log_name=log_name, on_proc=on_proc, new_session=True, spawn=spawn,
                                    gpu_reason='voice_preparation', gpu_label=audio.name)
        vocal = stems.get("vocals")
        if vocal is None:
            raise VoiceSeparationError("no_vocal")
        return vocal
    if not next(option.available for option in separation_options().options if option.id == "roformer"):
        raise VoiceSeparationError("roformer_not_configured")
    # Verified against upstream utils/settings.py, not arbitrary shell arguments.
    settings = ROFORMER_DIR / "utils" / "settings.py"
    required = ("--model_type", "--config_path", "--start_check_point", "--input_folder", "--store_dir", "--pcm_type", "--filename_template")
    if not settings.is_file() or not all(flag in settings.read_text(encoding="utf-8") for flag in required):
        raise VoiceSeparationError("roformer_incompatible")
    async with gpu_lease(gpu_lock, 'voice_preparation', audio.name):
        inputs = out_dir / "input"
        outputs = out_dir / "output"
        command = [str(roformer_python()), "inference.py", "--model_type", ROFORMER_MODEL_TYPE, "--config_path", str(ROFORMER_CONFIG.resolve()), "--start_check_point", str(ROFORMER_CHECKPOINT.resolve()), "--input_folder", str(inputs.resolve()), "--store_dir", str(outputs.resolve()), "--pcm_type", "FLOAT", "--filename_template", "{file_name}_{instr}"]
        proc: asyncio.subprocess.Process | None = None
        try:
            shutil.rmtree(out_dir, ignore_errors=True)
            inputs.mkdir(parents=True)
            outputs.mkdir()
            shutil.copyfile(audio, inputs / f"source{audio.suffix}")
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with (LOG_DIR / f"{log_name}.log").open("w", encoding="utf-8") as log:
                if spawn is None:
                    proc = await spawn_process(*command, cwd=str(ROFORMER_DIR), stdout=log, stderr=asyncio.subprocess.STDOUT)
                else:
                    proc = await spawn(command, ROFORMER_DIR, os.environ.copy(), log.fileno())
                if on_proc:
                    on_proc(proc)
                code = await proc.wait()
            if code != 0:
                raise VoiceSeparationError("separation_failed")
            try:
                return find_vocal_output(outputs)
            except ValueError as exc:
                raise VoiceSeparationError("no_vocal") from exc
        except BaseException as exc:
            try:
                await await_cleanup(kill_process_tree(proc))
            finally:
                shutil.rmtree(out_dir, ignore_errors=True)
            if isinstance(exc, OSError):
                raise VoiceSeparationError("separation_failed") from exc
            raise
        finally:
            if on_proc:
                on_proc(None)
