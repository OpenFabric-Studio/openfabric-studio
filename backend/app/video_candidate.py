"""Exact-source, research-only LTX candidate. No production pin override."""
from __future__ import annotations
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from .video_engine import ImageReference, RenderSettings, VideoEngineError, local_snapshot, validate_settings

CANDIDATE_COMMIT = 'bfa5755371a973651ea218ac3b56dcd34aa92c45'
CANDIDATE_VERSION = '0.16.0+193'
CANDIDATE_SHA256 = 'ab4006d332cffb58c3a6fdf580df2ace7d31638509a902ff3ca483e0d4f8af8c'


def candidate_digest(root: Path) -> str:
    """Hash reviewed source and dependency inputs, not local weights/venv state."""
    files = [path for path in (root / 'pyproject.toml', root / 'uv.lock') if path.is_file()]
    for name in ('ltx-core-mlx', 'ltx-pipelines-mlx'):
        base = root / 'packages' / name
        if (base / 'pyproject.toml').is_file():
            files.append(base / 'pyproject.toml')
        files.extend((base / 'src').rglob('*.py'))
    digest = hashlib.sha256()
    for path in sorted(files):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise VideoEngineError('candidate_source_invalid')
        digest.update(path.relative_to(root).as_posix().encode() + b'\0' + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def verify_candidate(root: Path) -> None:
    if candidate_digest(root) != CANDIDATE_SHA256:
        raise VideoEngineError('candidate_source_invalid')


@dataclass(frozen=True)
class CandidateSettings:
    memory_mode: Literal['low_ram', 'resident'] = 'low_ram'
    lora_mode: Literal['fused', 'unfused'] = 'fused'
    width: int = 704
    height: int = 448
    reference: Path | None = None
    adapter: Path | None = None
    adapter_strength: float = 1.0


def candidate_argv(engine: Path, cache: Path, output: Path, settings: CandidateSettings) -> list[str]:
    if settings.memory_mode not in ('low_ram', 'resident') or settings.lora_mode not in ('fused', 'unfused'):
        raise VideoEngineError('bad_settings')
    if settings.memory_mode == 'low_ram' and settings.lora_mode == 'unfused':
        raise VideoEngineError('candidate_unfused_requires_resident')
    verify_candidate(engine)
    rendered = RenderSettings(output=output / 'baseline.mp4', frames=49,
        prompt='A simple geometric character turns slowly in daylight', seed=42,
        width=settings.width, height=settings.height, stage1_steps=10, stage2_steps=3,
        references=(ImageReference(settings.reference),) if settings.reference is not None else (),
        mode='i2v' if settings.reference is not None else 't2v', adapter=settings.adapter,
        adapter_strength=settings.adapter_strength)
    validate_settings(rendered)
    if settings.reference is not None and not settings.reference.is_file():
        raise VideoEngineError('reference_missing')
    runner = Path(__file__).resolve().parents[1] / 'scripts/run_video.py'
    argv = [str(engine / '.venv/bin/python'), str(runner), '--engine-dir', str(engine.resolve()),
        '--candidate', '--telemetry', str(output / 'telemetry.json'), '--', 'generate',
        '--two-stage', '--no-audio', '--prompt', rendered.prompt, '--seed', '42',
        '--model', str(local_snapshot(cache, 'ltx23').resolve()),
        '--gemma', str(local_snapshot(cache, 'gemma3').resolve()),
        '--output', str(rendered.output), '--frames', '49', '--frame-rate', '24',
        '--width', str(settings.width), '--height', str(settings.height),
        '--stage1-steps', '10', '--stage2-steps', '3', '--cfg-scale', '3',
        '--tile-frames', '1', '--tile-spatial', '1']
    if settings.memory_mode == 'low_ram':
        argv.append('--low-ram')
    if settings.reference is not None:
        argv.extend(['--image', str(settings.reference.resolve()), '0', '1'])
    if settings.adapter is not None:
        if (not settings.adapter.is_file() or settings.adapter.suffix != '.safetensors'
                or settings.adapter.stat().st_size < 1024):
            raise VideoEngineError('adapter_missing')
        argv.extend(['--lora', str(settings.adapter.resolve()), str(settings.adapter_strength)])
    return argv
