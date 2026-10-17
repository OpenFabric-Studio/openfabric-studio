"""Voice-only separation presets; optional MSST is configured on the server."""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from .config import DEMUCS_DIR, LOG_DIR
from .managed_layout import engine_default
from .job_lifecycle import await_cleanup, kill_process_tree
from .video_process import spawn_owned
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


# Declared model/checksum prefixes from upstream demucs/remote/files.txt and
# htdemucs[_ft].yaml. These are integrity prefixes, not full published digests.
_DEMUCS_MODELS = {
    'fast': ('htdemucs', (('955717e8', '8726e21a'),)),
    'high': ('htdemucs_ft', (('f7e0c4bc', 'ba3fe64a'), ('d12395a8', 'e57c48e6'),
        ('92cfc3b6', 'ef3bcb9c'), ('04573f0d', 'f3cf25b2'))),
}


def _file_signature(path: Path) -> tuple[int, int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@dataclass(frozen=True)
class OfflineDemucs:
    python: Path
    engine: Path
    model: str
    files: tuple[tuple[Path, str, tuple[int, int, int, int, int]], ...]
    bag: bytes

    @property
    def model_bytes(self) -> int:
        return sum(signature[2] for _, _, signature in self.files)

    def command(self, audio: Path, output: Path) -> list[str]:
        """Copy a bounded local-only repository. Call in an owned thread."""
        repo = output / 'models'
        repo.mkdir(parents=True, exist_ok=False)
        for path, prefix, signature in self.files:
            if path.is_symlink() or _file_signature(path) != signature:
                raise VoiceSeparationError('separation_model_changed')
            target = repo / path.name
            digest = hashlib.sha256()
            total = 0
            with path.open('rb') as source, target.open('xb') as destination:
                while block := source.read(65536):
                    total += len(block)
                    if total > signature[2]:
                        raise VoiceSeparationError('separation_model_changed')
                    digest.update(block)
                    destination.write(block)
            if (total != signature[2] or not digest.hexdigest().startswith(prefix)
                    or _file_signature(path) != signature):
                raise VoiceSeparationError('separation_model_changed')
        (repo / (self.model + '.yaml')).write_bytes(self.bag)
        return [str(self.python), '-m', 'demucs', '--repo', str(repo), '-n', self.model,
            '--float32', '-o', str(output / 'separated'), str(audio)]


def offline_demucs(quality: Literal['fast', 'high'], *, engine: Path | None = None,
                   checkpoints: Path | None = None) -> OfflineDemucs:
    """Read-only local readiness; never starts Python, uv, or a model download.

    Managed setup stores torch checkpoints under the managed cache. Explicit
    internal paths support isolated fixtures; public requests cannot set them.
    Actual checkpoint integrity is checked while making the private repo.
    """
    if engine is None:
        engine = DEMUCS_DIR
    if checkpoints is None:
        from .module_catalog import configured_environment
        checkpoints = configured_environment().root / 'cache/torch/hub/checkpoints'
    python = engine / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    model, declared = _DEMUCS_MODELS[quality]
    try:
        # The interpreter may be a normal venv symlink. Source/cache paths may
        # not escape their configured roots through symbolic components.
        if (engine.is_symlink() or engine.resolve() != engine or not python.is_file()
                or checkpoints.is_symlink() or checkpoints.resolve() != checkpoints):
            raise VoiceSeparationError('separation_unavailable')
        for name in ('pretrained.py', 'repo.py', 'separate.py'):
            source = engine / 'demucs' / name
            if source.is_symlink() or not source.is_file() or source.stat().st_size > 1024**2:
                raise VoiceSeparationError('separation_unavailable')
        bag_path = engine / 'demucs/remote' / (model + '.yaml')
        if bag_path.is_symlink() or bag_path.resolve() != bag_path or bag_path.stat().st_size > 4096:
            raise VoiceSeparationError('separation_unavailable')
        files: list[tuple[Path, str, tuple[int, int, int, int, int]]] = []
        for signature, prefix in declared:
            path = checkpoints / f'{signature}-{prefix[:8]}.th'
            if path.is_symlink() or path.resolve() != path or not path.is_file() or not 0 < path.stat().st_size <= 2 * 1024**3:
                raise VoiceSeparationError('separation_unavailable')
            files.append((path, prefix, _file_signature(path)))
        return OfflineDemucs(python, engine, model, tuple(files), bag_path.read_bytes())
    except OSError as error:
        raise VoiceSeparationError('separation_unavailable') from error


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
                    proc = await spawn_owned(command, receipt_path=out_dir / 'worker.json', cwd=ROFORMER_DIR,
                                             env=os.environ.copy(), stdout=log.fileno())
                else:
                    proc = await spawn(command, ROFORMER_DIR, os.environ.copy(), log.fileno())
                if on_proc:
                    on_proc(proc)
                code = await proc.wait()
                if spawn is None:
                    await await_cleanup(kill_process_tree(proc))
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
