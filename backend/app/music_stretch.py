"""Verified Rubber Band 4.0 CLI inside a caller-owned, drained CPU workflow."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import uuid

import numpy as np
import soundfile as sf

from .audio_quality import RunCommand
from .job_lifecycle import run_owned_thread
from .music_dsp import MusicDspError
from .music_dsp_contracts import DspEngineIdentity
from .video_io import hash_file
from .video_media import tool

REVISION = 'rubberband-4.0.0-headroom-v1'
BLOCK_FRAMES = 65536
MAX_FRAMES = 192000 * 7200


def _validate_pcm(path: Path, rate: int, channels: int, frames: int) -> float:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > frames * channels * 4 + 65536:
        raise MusicDspError('dsp_output_invalid')
    try:
        info = sf.info(path)
        if (info.samplerate, info.channels, info.frames, info.subtype) != (rate, channels, frames, 'FLOAT'):
            raise MusicDspError('dsp_output_invalid')
        peak = 0.0
        count = 0
        with sf.SoundFile(path) as stream:
            while len(block := stream.read(BLOCK_FRAMES, dtype='float32', always_2d=True)):
                if not np.all(np.isfinite(block)):
                    raise MusicDspError('dsp_output_invalid')
                count += len(block)
                if count > frames:
                    raise MusicDspError('dsp_output_invalid')
                peak = max(peak, float(np.max(np.abs(block))))
        if count != frames:
            raise MusicDspError('dsp_output_invalid')
        return peak
    except (OSError, RuntimeError, ValueError) as error:
        raise MusicDspError('dsp_output_invalid') from error


def _map(path: Path, anchors: tuple[tuple[int, int], ...]) -> None:
    with path.open('x', encoding='ascii', newline='\n') as stream:
        stream.writelines(f'{source} {target}\n' for source, target in anchors)


async def _scale(source: Path, output: Path, power: int, rate: int, channels: int, frames: int, run: RunCommand) -> None:
    if output.exists() or output.is_symlink():
        raise MusicDspError('dsp_output_invalid')
    await run([tool('ffmpeg'), '-v', 'error', '-nostdin', '-i', str(source), '-map', '0:a:0', '-vn',
        '-af', f'volume={math.ldexp(1.0, power):.17g}:precision=double,asetnsamples=n=1024:p=0',
        '-c:a', 'pcm_f32le', '-fs', str(frames * channels * 4 + 4096), str(output)], 3600.0)
    await run_owned_thread(_validate_pcm, output, rate, channels, frames)


@dataclass(frozen=True)
class RubberBandStretch:
    executable: Path
    executable_sha256: str

    async def __call__(self, source_wav: Path, output_wav: Path, *, sample_rate_hz: int, channels: int,
                       input_frames: int, output_frames: int, anchors: tuple[tuple[int, int], ...],
                       pitch_shift_semitones: float, run: RunCommand) -> DspEngineIdentity:
        if (type(sample_rate_hz) is not int or not 8000 <= sample_rate_hz <= 192000
                or type(channels) is not int or channels not in {1, 2}
                or type(input_frames) is not int or type(output_frames) is not int
                or not 0 < input_frames <= min(MAX_FRAMES, 7200 * sample_rate_hz)
                or not 0 < output_frames <= min(MAX_FRAMES, 7200 * sample_rate_hz)
                or not math.isfinite(pitch_shift_semitones) or not -12 <= pitch_shift_semitones <= 12
                or not 2 <= len(anchors) <= 4096 or anchors[0] != (0, 0)
                or anchors[-1] != (input_frames, output_frames)):
            raise MusicDspError('dsp_invalid_anchors')
        for left, right in zip(anchors, anchors[1:]):
            if (any(type(frame) is not int for frame in (*left, *right))
                    or right[0] <= left[0] or right[1] <= left[1]
                    or not .75 - 1e-9 <= (right[1] - left[1]) / (right[0] - left[0]) <= 1.5 + 1e-9):
                raise MusicDspError('dsp_invalid_anchors')
        root = source_wav.parent
        if (source_wav.is_symlink() or source_wav.resolve() != source_wav or root.resolve() != root
                or output_wav.parent != root or output_wav.exists() or output_wav.is_symlink()):
            raise MusicDspError('dsp_invalid_source')
        executable = self.executable
        if (executable.is_symlink() or executable.resolve() != executable or not executable.is_file()
                or await hash_file(executable, max_bytes=64 * 1024**2) != self.executable_sha256):
            raise MusicDspError('dsp_runtime_unverified')
        peak = await run_owned_thread(_validate_pcm, source_wav, sample_rate_hz, channels, input_frames)
        power = min(0, math.floor(math.log2(.25 / peak))) if peak else 0
        if power < -32:
            raise MusicDspError('dsp_output_invalid')
        if (await run([str(executable), '--version'], 15.0)).strip() != b'4.0.0':
            raise MusicDspError('dsp_runtime_unverified')
        identifier = uuid.uuid4().hex
        scaled = root / (identifier + '-headroom.wav')
        native = root / (identifier + '-native.wav')
        map_path = root / (identifier + '-map.txt')
        # R3 can return short output for a non-uniform map despite successful exit.
        uniform = all(abs(target - source * output_frames / input_frames) <= .5 for source, target in anchors)
        engine = 'r3' if uniform else 'r2'
        recipe = f'{engine}-linked-power-two-headroom-v1'
        await _scale(source_wav, scaled, power, sample_rate_hz, channels, input_frames, run)
        argv = [str(executable), '--quiet', '-3' if uniform else '-2', '--centre-focus', '--ignore-clipping',
            '--time', f'{output_frames / input_frames:.17g}', '--pitch', f'{pitch_shift_semitones:.17g}']
        if not uniform:
            await run_owned_thread(_map, map_path, anchors)
            argv.extend(['--timemap', str(map_path)])
        argv.extend([str(scaled), str(native)])
        await run(argv, 3600.0)
        native_peak = await run_owned_thread(_validate_pcm, native, sample_rate_hz, channels, output_frames)
        # --ignore-clipping disables hidden auto-gain but clamps: never publish that outcome.
        if native_peak >= 1.0:
            raise MusicDspError('dsp_output_invalid')
        if await hash_file(executable, max_bytes=64 * 1024**2) != self.executable_sha256:
            raise MusicDspError('dsp_runtime_unverified')
        await _scale(native, output_wav, -power, sample_rate_hz, channels, output_frames, run)
        configuration = {'revision': REVISION, 'executable_sha256': self.executable_sha256, 'recipe': recipe,
            'rate': sample_rate_hz, 'channels': channels, 'input_frames': input_frames, 'output_frames': output_frames,
            'anchors': anchors, 'pitch_shift_semitones': pitch_shift_semitones, 'input_gain_power_of_two': power}
        return DspEngineIdentity(name='rubberband', revision=REVISION, executable_sha256=self.executable_sha256,
            recipe=recipe, input_gain_power_of_two=power, output_gain_power_of_two=-power,
            configuration_sha256=hashlib.sha256(json.dumps(configuration, sort_keys=True, separators=(',', ':'),
                allow_nan=False).encode()).hexdigest(), license_evidence_ids=['rubberband-4.0.0-gpl2-or-later'])
