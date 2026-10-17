"""Shared Seed-VC command boundary; lifecycle and admission remain caller-owned."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf


def seed_command(python: Path, source: Path, reference: Path, output: Path, *, diffusion_steps: int,
                 checkpoint: Path | None, configuration: Path | None) -> list[str]:
    if (checkpoint is None) != (configuration is None) or not 1 <= diffusion_steps <= 100:
        raise ValueError('invalid_conversion_configuration')
    command = [str(python), 'inference.py', '--source', str(source), '--target', str(reference),
        '--output', str(output), '--diffusion-steps', str(diffusion_steps), '--inference-cfg-rate', '0.7',
        '--f0-condition', 'True', '--auto-f0-adjust', 'False', '--fp16', 'False']
    if checkpoint is not None and configuration is not None:
        command.extend(['--checkpoint', str(checkpoint), '--config', str(configuration)])
    return command


def phase_safe_mono_file(source: Path, output: Path) -> None:
    """Two bounded passes choose the same global energy fallback as safe_mono.

    No amplitude normalization or clipping. Caller owns/drains this thread and
    may remove a failed private output only after that drain completes.
    """
    if source.resolve() == output.resolve() or output.exists() or output.is_symlink():
        raise ValueError('invalid_conversion_source')
    info = sf.info(source)
    if info.channels not in (1, 2) or not 8000 <= info.samplerate <= 192000 or not 0 < info.frames <= 7200 * info.samplerate:
        raise ValueError('invalid_conversion_source')
    with sf.SoundFile(source) as audio:
        energies = np.zeros(info.channels, dtype=np.float64)
        mixed_energy = 0.0
        while (block := audio.read(65536, dtype='float32', always_2d=True)).shape[0]:
            if not np.isfinite(block).all():
                raise ValueError('invalid_conversion_source')
            double = block.astype(np.float64)
            energies += np.sum(double * double, axis=0)
            mixed = double.mean(axis=1)
            mixed_energy += float(np.sum(mixed * mixed))
        channel = int(np.argmax(energies)) if mixed_energy < .25 * float(np.max(energies)) else None
    with sf.SoundFile(source) as audio:
        with sf.SoundFile(output, mode='w', samplerate=info.samplerate, channels=1, subtype='FLOAT') as destination:
            while (block := audio.read(65536, dtype='float32', always_2d=True)).shape[0]:
                destination.write(block[:, channel] if channel is not None else block.mean(axis=1))
