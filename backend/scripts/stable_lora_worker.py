"""Supervised fresh dataset copy, native pre-encoding and native LoRA training."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import uuid
import wave

MAX_CHECKPOINT_BYTES = 512 * 1024 * 1024


def object_value(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError('invalid_training_request')
    return {key: item for key, item in value.items() if isinstance(key, str)}


def text(value: object, maximum: int = 4000) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or '\0' in value:
        raise ValueError('invalid_training_request')
    return value


def integer(value: object, lower: int, upper: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not lower <= value <= upper:
        raise ValueError('invalid_training_request')
    return value


def digest(path: Path, maximum: int = MAX_CHECKPOINT_BYTES) -> str:
    checksum = hashlib.sha256()
    count = 0
    with path.open('rb') as handle:
        while chunk := handle.read(65536):
            count += len(chunk)
            if count > maximum:
                raise ValueError('training_artifact_too_large')
            checksum.update(chunk)
    return checksum.hexdigest()


def checkpoint_digest(path: Path, *, max_steps: int, variant: str, rank: int) -> str:
    if path.is_symlink() or not 8 < path.stat().st_size <= MAX_CHECKPOINT_BYTES:
        raise ValueError('invalid_training_checkpoint')
    with path.open('rb') as handle:
        prefix = handle.read(8)
        if len(prefix) != 8:
            raise ValueError('invalid_training_checkpoint')
        length = struct.unpack('<Q', prefix)[0]
        if not 2 <= length <= 4 * 1024 * 1024 or 8 + length >= path.stat().st_size:
            raise ValueError('invalid_training_checkpoint')
        header = object_value(json.loads(handle.read(length)))
    metadata = object_value(header.get('__metadata__'))
    config = object_value(json.loads(text(metadata.get('lora_config'), 65536)))
    expected = 'sa3-medium' if variant == 'medium' else 'sa3-sm-music'
    if config.get('base_model') != expected or config.get('adapter_type') != 'lora' or integer(config.get('step'), 1, 100000) != max_steps or integer(config.get('rank'), 8, 32) != rank:
        raise ValueError('incomplete_training_checkpoint')
    return digest(path)


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class TrainingPlan:
    directory: Path
    sources: list[Path]
    captions: list[str]
    preencode_argv: list[str]
    train_argv: list[str]
    ffmpeg: str
    fingerprint: str
    variant: str
    max_steps: int
    rank: int


def argv(value: object) -> list[str]:
    if not isinstance(value, list) or not 2 <= len(value) <= 64:
        raise ValueError('invalid_training_request')
    return [text(item, 10000) for item in value]


def read_plan(path: Path) -> TrainingPlan:
    if path.is_symlink() or not path.is_absolute() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('invalid_training_request')
    directory = path.parent
    if directory.is_symlink() or directory.resolve() != directory or re.fullmatch(r'[0-9a-f]{32}', directory.name) is None:
        raise ValueError('invalid_training_directory')
    payload = object_value(json.loads(path.read_bytes()))
    request = object_value(payload.get('request'))
    samples = request.get('samples')
    sources = payload.get('sources')
    if not isinstance(samples, list) or not 1 <= len(samples) <= 100 or not isinstance(sources, list) or len(sources) != len(samples):
        raise ValueError('invalid_training_dataset')
    captions = [text(object_value(sample).get('caption')) for sample in samples]
    paths = [Path(text(source, 10000)) for source in sources]
    if any(not source.is_absolute() or source.is_symlink() or not source.is_file() for source in paths):
        raise ValueError('invalid_training_dataset')
    variant = text(request.get('variant'))
    if variant not in ('small-music', 'medium'):
        raise ValueError('invalid_training_variant')
    rank = integer(request.get('rank'), 8, 32)
    if rank not in (8, 16, 32):
        raise ValueError('invalid_training_rank')
    fingerprint = text(payload.get('request_sha256'))
    if re.fullmatch(r'[0-9a-f]{64}', fingerprint) is None:
        raise ValueError('invalid_training_request')
    return TrainingPlan(directory, paths, captions, argv(payload.get('preencode_argv')), argv(payload.get('train_argv')),
        text(payload.get('ffmpeg'), 10000), fingerprint, variant, integer(request.get('max_steps'), 1, 100000), rank)


def progress(directory: Path, stage: str) -> None:
    atomic_json(directory / 'stage.json', {'stage': stage})


def prepare_dataset(plan: TrainingPlan) -> None:
    directory = plan.directory / 'audio'
    directory.mkdir()
    manifest: list[dict[str, object]] = []
    formats = {'.wav': 'wav', '.mp3': 'mp3', '.flac': 'flac'}
    for index, (source, caption) in enumerate(zip(plan.sources, plan.captions, strict=True)):
        if source.suffix.lower() not in formats or source.is_symlink():
            raise ValueError('invalid_training_dataset')
        source_sha256 = digest(source)
        pending = directory / f'{index:03d}.partial.wav'
        output = directory / f'{index:03d}.wav'
        subprocess.run([plan.ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-protocol_whitelist', 'file,pipe',
            '-f', formats[source.suffix.lower()], '-i', str(source), '-t', '600', '-map', '0:a:0',
            '-ac', '2', '-ar', '44100', '-c:a', 'pcm_s16le', '-f', 'wav', str(pending)], check=True, timeout=120)
        with wave.open(str(pending)) as audio:
            if audio.getnchannels() != 2 or audio.getsampwidth() != 2 or audio.getframerate() != 44100 or not .1 <= audio.getnframes() / 44100 <= 600.01:
                raise ValueError('invalid_training_audio')
        if source_sha256 != digest(source):
            raise ValueError('training_source_changed')
        pending.replace(output)
        atomic_json(output.with_suffix('.json'), {'text': caption})
        manifest.append({'sample': output.name, 'source_sha256': source_sha256, 'audio_sha256': digest(output), 'caption': caption})
    atomic_json(plan.directory / 'dataset.json', {'request_sha256': plan.fingerprint, 'samples': manifest})


def validate_latents(plan: TrainingPlan) -> None:
    for index, caption in enumerate(plan.captions):
        latent = plan.directory / 'latents' / f'{index:03d}.npy'
        metadata = latent.with_suffix('.json')
        if latent.is_symlink() or metadata.is_symlink() or not latent.is_file() or latent.stat().st_size <= 128 or metadata.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('training_latent_missing')
        value = object_value(json.loads(metadata.read_bytes()))
        if value.get('text') != caption:
            raise ValueError('training_caption_changed')


def run(plan: TrainingPlan) -> None:
    os.environ.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1'})
    os.chdir(plan.directory)
    progress(plan.directory, 'preparing')
    prepare_dataset(plan)
    progress(plan.directory, 'encoding')
    subprocess.run(plan.preencode_argv, check=True)
    # The native encoder reports per-file errors without a failing exit code.
    validate_latents(plan)
    progress(plan.directory, 'training')
    subprocess.run(plan.train_argv, check=True)
    checkpoints = list((plan.directory / 'checkpoints').glob(f'training/*/checkpoints/training-step={plan.max_steps}-epoch=*.safetensors'))
    if len(checkpoints) != 1:
        raise ValueError('training_final_checkpoint_missing')
    checkpoint = checkpoints[0]
    if any(path.is_symlink() for path in (checkpoint, *checkpoint.parents)) or not checkpoint.resolve().is_relative_to(plan.directory):
        raise ValueError('invalid_training_checkpoint')
    checksum = checkpoint_digest(checkpoint, max_steps=plan.max_steps, variant=plan.variant, rank=plan.rank)
    progress(plan.directory, 'publishing')
    atomic_json(plan.directory / 'completion.json', {'job_id': plan.directory.name, 'request_sha256': plan.fingerprint,
        'checkpoint': str(checkpoint.relative_to(plan.directory)), 'sha256': checksum, 'dataset_sha256': digest(plan.directory / 'dataset.json'),
        'variant': plan.variant, 'max_steps': plan.max_steps})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', type=Path, required=True)
    arguments = parser.parse_args()
    run(read_plan(arguments.request))


if __name__ == '__main__':
    main()
