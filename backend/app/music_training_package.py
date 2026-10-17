"""Local, immutable NVIDIA packages; preparing an archive never contacts a service."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import wave
import zipfile

import soundfile as sf

from .module_catalog import ModuleEnvironment
from .music_training_contracts import MusicLoraTrainingRequest
from .music_training_setup import recipes, training_assets, training_license_declarations

_BACKEND = Path(__file__).resolve().parents[1]
_REPOSITORY = _BACKEND.parent
_LICENSE_FILES = ('LICENSE', 'LICENSE-MIT', 'NOTICE')
_COMMON = ('scripts/stable_lora_worker.py', 'app/__init__.py', 'app/orchestrator/__init__.py',
           'app/orchestrator/windows_job.py', 'app/orchestrator/windows_supervisor.py')
_YUE = ('scripts/yue_lora_worker.py', 'app/yue_lora_convert.py', 'app/yue_lora_runtime.py',
        'app/contracts.py', 'app/music_lora_contracts.py', 'app/music_training_contracts.py', 'app/music_training_source_contracts.py')


def _digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _copy(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError('music_training_package_source_unsafe')
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as reader, target.open('xb') as writer:
        shutil.copyfileobj(reader, writer, 1024 * 1024)


def _license_directory() -> Path:
    for directory in (_BACKEND.parent / 'licenses', _REPOSITORY):
        if all((directory / name).is_file() and not (directory / name).is_symlink() for name in _LICENSE_FILES):
            return directory
    raise ValueError('music_training_package_licenses_missing')


def _command(argv: list[str], *, timeout: float) -> None:
    from .video_process import run_owned_command
    environment = {**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1'}
    result = asyncio.run(run_owned_command(argv, timeout=timeout, max_bytes=2 * 1024**2,
        env=environment, stderr=subprocess.STDOUT))
    if result.returncode != 0:
        raise ValueError('music_training_package_preparation_failed')


def _prepare_pair_sample(staging: Path, source: Path, index: int, seconds: int) -> dict[str, object]:
    from .yue_lora_runtime import default_environment, score_argv, score_ready, validate_score
    environment = default_environment()
    if not score_ready(environment):
        raise ValueError('yue_lora_score_runtime_unprepared')
    formats = {'.wav': 'wav', '.mp3': 'mp3', '.flac': 'flac'}
    if source.suffix.lower() not in formats:
        raise ValueError('music_training_package_audio_invalid')
    excerpt = staging / f'excerpts/{index:03d}.wav'
    excerpt.parent.mkdir(exist_ok=True)
    _command([str(environment.ffmpeg), '-nostdin', '-v', 'error', '-xerror', '-protocol_whitelist', 'file,pipe',
        '-f', formats[source.suffix.lower()], '-i', str(source), '-t', str(seconds), '-map', '0:a:0',
        '-ac', '2', '-ar', '48000', '-c:a', 'pcm_s16le', '-f', 'wav', str(excerpt)], timeout=120)
    with wave.open(str(excerpt)) as audio:
        if (audio.getnchannels() != 2 or audio.getsampwidth() != 2 or audio.getframerate() != 48000
                or not .1 <= audio.getnframes() / 48000 <= seconds + .01):
            raise ValueError('music_training_package_audio_invalid')
    score = staging / f'scores/{index:03d}.abc'
    score.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.score-', dir=staging) as temporary:
        output = Path(temporary)
        _command(score_argv(excerpt, output, environment), timeout=1800)
        score.write_text(validate_score(output / 'score.abc') + '\n', encoding='utf-8')
    return {'path': excerpt.relative_to(staging).as_posix(), 'sha256': _digest(excerpt), 'bytes': excerpt.stat().st_size,
        'score_path': score.relative_to(staging).as_posix(), 'score_sha256': _digest(score)}


def prepare_package(request: MusicLoraTrainingRequest, sources: list[Path], newdestination: Path) -> Path:
    """Copy reviewed sources/settings/original audio and atomically publish a new ZIP."""
    if (not newdestination.is_absolute() or newdestination.is_symlink() or newdestination.exists()
            or not newdestination.parent.is_dir() or newdestination.parent.resolve() != newdestination.parent
            or newdestination.suffix.lower() != '.zip'):
        raise ValueError('music_training_package_destination_invalid')
    if len(sources) != len(request.samples):
        raise ValueError('music_training_package_dataset_invalid')
    for source in sources:
        if not source.is_absolute() or source.is_symlink() or source.resolve() != source or not source.is_file():
            raise ValueError('music_training_package_source_unsafe')
    excerpt_bytes = len(sources) * request.segment_seconds * 48000 * 2 * 2 if request.yue_pair is not None else 0
    if shutil.disk_usage(newdestination.parent).free < (sum(source.stat().st_size for source in sources) + excerpt_bytes) * 2 + 32 * 1024**2:
        raise ValueError('music_training_package_disk_space')
    with tempfile.TemporaryDirectory(prefix='.music-training-package-', dir=newdestination.parent) as temporary:
        staging = Path(temporary)
        samples: list[dict[str, object]] = []
        for index, (source, sample) in enumerate(zip(sources, request.samples, strict=True)):
            before = _digest(source)
            info = sf.info(source)
            if (not math.isfinite(info.duration) or info.duration <= .1 or not 8000 <= info.samplerate <= 192000
                    or info.channels not in (1, 2)):
                raise ValueError('music_training_package_audio_invalid')
            if request.yue_pair is not None and sample.lyrics.strip() and info.duration > request.segment_seconds + 1 / info.samplerate:
                raise ValueError('music_training_package_lyrics_require_matching_trimmed_clip')
            extension = source.suffix.lower()
            if not 2 <= len(extension) <= 6 or not extension[1:].isalnum():
                raise ValueError('music_training_package_audio_invalid')
            relative = f'audio/{index:03d}{extension}'
            copied = staging / relative
            _copy(source, copied)
            if _digest(source) != before or _digest(copied) != before:
                raise ValueError('music_training_package_source_changed')
            entry: dict[str, object] = {'path': relative, 'sha256': before, 'bytes': copied.stat().st_size,
                'original_duration_seconds': info.duration, 'offset_seconds': 0, 'segment_seconds': request.segment_seconds,
                'sample_index': index}
            if request.yue_pair is not None:
                entry.update({'original_path': relative, 'original_sha256': before})
                entry.update(_prepare_pair_sample(staging, copied, index, request.segment_seconds))
            samples.append(entry)
        for name in _COMMON + _YUE:
            _copy(_BACKEND / name, staging / 'backend' / name)
        _copy(_BACKEND / 'scripts/music_training_package_bootstrap.py', staging / 'bootstrap.py')
        license_directory = _license_directory()
        for name in _LICENSE_FILES:
            _copy(license_directory / name, staging / name)
        recipe = recipes()[request.engine]
        environments: list[dict[str, object]] = []
        for descriptor in recipe.environments:
            relative = 'requirements/' + descriptor.folder + '.txt'
            _copy(Path(__file__).parent / 'assets' / descriptor.requirements, staging / relative)
            environments.append({'folder': descriptor.folder, 'requirements': relative})
        virtual = ModuleEnvironment.for_root(staging / 'runtime', platform='linux', architecture='x86_64')
        assets = [dict(item.artifact.model_dump(mode='json'), path=item.target.relative_to(virtual.root).as_posix())
                  for item in training_assets(virtual, request.engine)]
        license_rows = [entry.model_dump(mode='json', exclude_none=True) for entry in training_license_declarations(request.engine)]
        (staging / 'README.txt').write_text(
            'OpenFabric Studio portable NVIDIA training package\n\n'
            'CUDA execution is UNVERIFIED. The same pinned model boundary was tested on Apple Silicon; CUDA needs its own train/load/preview evidence.\n'
            'This archive contains the selected original recordings, captions and reviewed settings. Paired YuE also includes the exact bounded WAV excerpts and checked ABC scores prepared locally with the installed native GGUF CLI. No upload occurs automatically.\n'
            'Install Python 3.12, uv and Git; YuE2 also needs FFmpeg. Use a CUDA GPU with BF16 support.\n'
            'Run: python bootstrap.py --check\n'
            'Then: python bootstrap.py\n'
            'The second command installs pinned environments, prints model download sizes and terms, downloads verified weights, and starts a fresh run.\n'
            'Weights are not included. Code/model/recording rights are separate; see manifest.json license declarations and the bundled notices.\n'
            'Paired YuE lyrics must match the entire selected short clip; longer vocal recordings are rejected. Empty-lyrics clips use the first selected segment.\n'
            'Keep completed checkpoints. Rerunning creates a new run and does not resume optimizer state.\n'
            'A paired YuE run trains AR then NAR and requires both runs/<id>/adapter-ar.safetensors and adapter-nar.safetensors. Its checked scores need no native CLI or extra transcription weights on NVIDIA.\n'
            'Legacy single-adapter runs produce runs/<id>/adapter.safetensors. Import the named outputs through OpenFabric Studio; its registry validates format, finite values and base identity before use.\n', encoding='utf-8')
        files = [{'path': path.relative_to(staging).as_posix(), 'sha256': _digest(path), 'bytes': path.stat().st_size}
                 for path in sorted(staging.rglob('*')) if path.is_file()]
        manifest = {'schema_version': 1, 'engine': request.engine, 'nvidia_validation': 'unverified',
            'request': request.model_dump(mode='json', exclude_none=True),
            'request_json': request.model_dump_json(exclude_none=True),
            'request_sha256': hashlib.sha256(request.model_dump_json(exclude_none=True).encode()).hexdigest(),
            'sources': samples, 'files': files, 'assets': assets, 'licenses': license_rows, 'environments': environments,
            'sources_pins': [{'folder': source.folder, 'repository': source.pin.repository, 'revision': source.pin.commit}
                            for source in recipe.sources]}
        (staging / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
        archive_descriptor, partial_name = tempfile.mkstemp(prefix='.' + newdestination.name + '.', suffix='.part', dir=newdestination.parent)
        partial = Path(partial_name)
        try:
            with os.fdopen(archive_descriptor, 'w+b') as stream:
                with zipfile.ZipFile(stream, mode='w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
                    for path in sorted(staging.rglob('*')):
                        if path.is_file():
                            archive.write(path, path.relative_to(staging).as_posix())
                stream.flush();os.fsync(stream.fileno())
            os.link(partial, newdestination)
        finally:
            partial.unlink(missing_ok=True)
    return newdestination
