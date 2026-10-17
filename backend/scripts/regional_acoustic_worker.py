"""Private bounded CPU scan. The regional owner supervises this process tree."""
from __future__ import annotations

import argparse
import asyncio
import math
from pathlib import Path
import sys
import time
from collections.abc import Awaitable, Callable

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import soundfile as sf
from app.atomic_files import write_object
from app.audio_quality import INPUT
from app.regional_acoustic_helper import (RATE, WINDOW_SECONDS, MAX_HASHES, AUDITION_BYTES, AcousticBudgetError,
    AcousticFingerprint, AcousticWorkerRequest, AcousticWorkerOutcome, configuration_sha256, fingerprint_window, combine_fingerprints, build_report)
from app.regional_acoustic_contracts import RegionalAcousticReport, RegionalAcousticAudition, RegionalAcousticCandidate
from app.video_io import hash_file
from app.video_media import tool

Run = Callable[[list[str], float, int], Awaitable[bytes]]


async def run_command(argv: list[str], timeout: float, maximum: int) -> bytes:
    # Inherit the outer POSIX session / Windows job, rather than detaching.
    process = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        if process.stdout is None:
            raise AcousticBudgetError('regional_dataset_invalid')
        content = bytearray()
        async with asyncio.timeout(timeout):
            while chunk := await process.stdout.read(min(65536, maximum - len(content) + 1)):
                if len(content) + len(chunk) > maximum:
                    raise AcousticBudgetError('regional_dataset_limit')
                content.extend(chunk)
            await process.wait()
        if process.returncode != 0:
            raise AcousticBudgetError('regional_dataset_invalid')
        return bytes(content)
    finally:
        if process.returncode is None:
            process.kill()
        await process.communicate()


def signature(path: Path) -> tuple[int, int, int, int, int]:
    status = path.stat()
    return status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns


async def decode(path: str, start: float, seconds: float, channels: int, run: Run, deadline: float) -> np.ndarray[tuple[int, ...], np.dtype[np.float32]]:
    expected = round(seconds * RATE)
    maximum = (expected + 1024) * channels * 4
    raw = await run([tool('ffmpeg'), '-v', 'error', '-nostdin', '-xerror', *INPUT,
        '-ss', f'{start:.9f}', '-i', path, '-map', '0:a:0', '-vn', '-t', f'{seconds:.9f}',
        '-ar', str(RATE), '-ac', str(channels), '-af', f'aresample={RATE},asetnsamples=n=1024:p=0',
        '-c:a', 'pcm_f32le', '-f', 'f32le', '-fs', str(maximum - channels * 4), 'pipe:1'],
        min(120., max(.001, deadline - time.monotonic())), maximum)
    if len(raw) % (channels * 4) or abs(len(raw) // (channels * 4) - expected) > max(2, math.ceil(.002 * RATE)):
        raise AcousticBudgetError('regional_dataset_invalid')
    samples = np.frombuffer(raw, dtype='<f4').reshape((-1, channels))
    if not len(samples) or not np.all(np.isfinite(samples)) or np.max(np.abs(samples)) > 16:
        raise AcousticBudgetError('regional_dataset_invalid')
    return samples[:expected]


async def scan(request: AcousticWorkerRequest, run: Run) -> RegionalAcousticReport:
    if request.configuration_sha256 != configuration_sha256():
        raise AcousticBudgetError('regional_dataset_source_changed')
    deadline = time.monotonic() + 900
    signatures: dict[Path, tuple[int, int, int, int, int]] = {}
    checksums: dict[Path, str] = {}
    fingerprints: list[AcousticFingerprint] = []
    total_bytes = hashes = 0
    # Validate ALL sources before spending time decoding any excerpt.
    for source in request.sources:
        path, binding = Path(source.source_path), source.excerpt
        if (not path.is_absolute() or path.is_symlink() or path.resolve() != path or path.suffix.lower() not in {'.wav', '.mp3', '.flac'}
                or not 0 < path.stat().st_size <= 512 * 1024**2 or binding.end_frame > source.source_frames):
            raise AcousticBudgetError('regional_dataset_source_changed')
        if path not in signatures:
            signatures[path] = signature(path); total_bytes += signatures[path][2]
            checksums[path] = await hash_file(path, max_bytes=512 * 1024**2)
            if total_bytes > 8 * 1024**3:
                raise AcousticBudgetError('regional_dataset_source_changed')
        if checksums[path] != binding.source_sha256:
            raise AcousticBudgetError('regional_dataset_source_changed')
        info = sf.info(path)
        if (info.frames, info.samplerate, info.channels) != (source.source_frames, binding.sample_rate_hz, binding.channels):
            raise AcousticBudgetError('regional_dataset_source_changed')
    for source in request.sources:
        if time.monotonic() > deadline:
            raise AcousticBudgetError('regional_dataset_limit')
        binding = source.excerpt
        duration = (binding.end_frame - binding.start_frame) / binding.sample_rate_hz
        windows: list[AcousticFingerprint] = []
        for offset in range(0, math.ceil(duration), WINDOW_SECONDS):
            seconds = min(float(WINDOW_SECONDS), duration - offset)
            samples = await decode(source.source_path, binding.start_frame / binding.sample_rate_hz + offset,
                seconds, binding.channels, run, deadline)
            fingerprint = fingerprint_window(samples)
            hashes += len(fingerprint.codes)
            if hashes > MAX_HASHES:
                raise AcousticBudgetError('regional_dataset_limit')
            windows.append(fingerprint)
        fingerprints.append(combine_fingerprints(windows))
    for source in request.sources:
        path = Path(source.source_path)
        if signature(path) != signatures[path] or await hash_file(path, max_bytes=512 * 1024**2) != source.excerpt.source_sha256:
            raise AcousticBudgetError('regional_dataset_source_changed')
    return build_report([source.excerpt for source in request.sources], fingerprints,
        request.selection_sha256, request.configuration_sha256, deadline)


async def retain_auditions(request: AcousticWorkerRequest, report: RegionalAcousticReport, root: Path, run: Run) -> RegionalAcousticReport:
    """Paired <=12 s clips, fixed total budget; publication still belongs to owner."""
    directory = root / 'auditions'
    if directory.exists() or directory.is_symlink() or directory.resolve() != directory:
        raise AcousticBudgetError('regional_dataset_source_changed')
    directory.mkdir()
    sources = {item.excerpt.excerpt_id: item for item in request.sources}
    used = 0
    deadline = time.monotonic() + 120
    candidates: list[RegionalAcousticCandidate] = []
    signatures: dict[Path, tuple[int, int, int, int, int]] = {}
    for candidate in report.candidates:
        left, right = sources[candidate.left_excerpt_id], sources[candidate.right_excerpt_id]
        seconds = min(12., candidate.aligned_seconds)
        estimates = [((round(seconds * RATE) + 16) * item.excerpt.channels * 4 + 4096) for item in (left, right)]
        if used + sum(estimates) > AUDITION_BYTES:
            candidates.append(candidate.model_copy(update={'audition_unavailable_reason': 'capacity'}))
            continue
        auditions: list[RegionalAcousticAudition] = []
        for side, source, start, end in (('left', left, candidate.left_start_frame, candidate.left_end_frame),
                ('right', right, candidate.right_start_frame, candidate.right_end_frame)):
            original = Path(source.source_path)
            if original.is_symlink() or original.resolve() != original:
                raise AcousticBudgetError('regional_dataset_source_changed')
            if original not in signatures:
                signatures[original] = signature(original)
                if await hash_file(original, max_bytes=512 * 1024**2) != source.excerpt.source_sha256:
                    raise AcousticBudgetError('regional_dataset_source_changed')
            samples = await decode(source.source_path, start / source.excerpt.sample_rate_hz, seconds,
                source.excerpt.channels, run, deadline)
            path = directory / (candidate.id + '-' + side + '.wav')
            with path.open('xb') as output:
                with sf.SoundFile(output.fileno(), mode='w', samplerate=RATE, channels=source.excerpt.channels,
                        format='WAV', subtype='FLOAT', closefd=False) as audio:
                    audio.write(samples)
                output.flush()
                import os
                os.fsync(output.fileno())
            used += path.stat().st_size
            if used > AUDITION_BYTES:
                raise AcousticBudgetError('regional_dataset_limit')
            auditions.append(RegionalAcousticAudition(sha256=await hash_file(path, max_bytes=AUDITION_BYTES),
                bytes=path.stat().st_size, channels=source.excerpt.channels, frames=len(samples), source_start_frame=start,
                source_end_frame=min(end, start + round(len(samples) * source.excerpt.sample_rate_hz / RATE))))
        candidates.append(candidate.model_copy(update={'left_audition': auditions[0], 'right_audition': auditions[1],
            'audition_unavailable_reason': None}))
    for source in request.sources:
        original = Path(source.source_path)
        if original in signatures and (signature(original) != signatures[original]
                or await hash_file(original, max_bytes=512 * 1024**2) != source.excerpt.source_sha256):
            raise AcousticBudgetError('regional_dataset_source_changed')
    return RegionalAcousticReport.model_validate({**report.model_dump(), 'candidates': [item.model_dump() for item in candidates]})


async def main(request_path: Path, result_path: Path) -> int:
    if (request_path.name != 'acoustic-request.json' or result_path.name != 'acoustic-result.json'
            or request_path.parent != result_path.parent or request_path.is_symlink() or result_path.exists()
            or result_path.is_symlink() or request_path.resolve() != request_path or request_path.stat().st_size > 2 * 1024**2):
        return 2
    try:
        request = AcousticWorkerRequest.model_validate_json(request_path.read_bytes())
        report = await scan(request, run_command)
        result = AcousticWorkerOutcome(report=await retain_auditions(request, report, result_path.parent, run_command))
    except AcousticBudgetError as error:
        code = str(error)
        result = AcousticWorkerOutcome(error_code='regional_dataset_limit' if code == 'regional_dataset_limit'
            else 'regional_dataset_source_changed' if code == 'regional_dataset_source_changed' else 'regional_dataset_invalid')
    except (ValueError, OSError, TimeoutError):
        result = AcousticWorkerOutcome(error_code='regional_dataset_invalid')
    write_object(result_path, result.model_dump(mode='json'))
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--result', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.request, args.result)))
