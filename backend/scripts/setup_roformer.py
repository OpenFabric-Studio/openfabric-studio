"""Install the upstream-listed Kimberley vocal model, with verified provenance.

The engine is installed separately by setup_roformer.sh. This helper uses only
the standard library and runs solely during an explicitly requested setup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from urllib.request import urlopen

ENGINE_COMMIT = '84b1eac0887756b4f1a9d7a1ff49105939749ed2'
MODEL_REVISION = 'ac9b0614ab3cd7f77219e18ba494dfd93956c348'
CHECKPOINT_SIZE = 913106900
CHECKPOINT_SHA256 = '87201f4d31afb5bc79993230fc49446918425574db48c01c405e44f365c7559e'
CONFIG_SHA256 = 'f63f38eb1e6e40a7db0dade714a5ae257555dd8748f4e774eae8679275a81926'
MODEL_URL = f'https://huggingface.co/KimberleyJSN/melbandroformer/resolve/{MODEL_REVISION}/MelBandRoformer.ckpt'
CONFIG_RELATIVE = 'configs/KimberleyJensen/config_vocals_mel_band_roformer_kj.yaml'
ALLOWED_ENV_KEYS = frozenset({'VOICE_ROFORMER_DIR', 'VOICE_ROFORMER_MODEL_TYPE', 'VOICE_ROFORMER_CONFIG', 'VOICE_ROFORMER_CHECKPOINT'})


def sha256_file(path: Path) -> str:
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def atomic_text(destination: Path, content: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(destination.stat().st_mode) if destination.exists() else 0o600
    descriptor, name = tempfile.mkstemp(prefix=f'.{destination.name}.', suffix='.partial', dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as output:
            if sys.platform == 'win32':
                temporary.chmod(mode)
            else:
                os.fchmod(output.fileno(), mode)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def download_checkpoint(destination: Path) -> None:
    if destination.is_file() and destination.stat().st_size == CHECKPOINT_SIZE and sha256_file(destination) == CHECKPOINT_SHA256:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f'.{destination.name}.', suffix='.partial', dir=destination.parent)
    temporary = Path(name)
    digest = hashlib.sha256()
    count = 0
    try:
        with os.fdopen(descriptor, 'wb') as output, urlopen(MODEL_URL, timeout=60) as response:
            while chunk := response.read(1024 * 1024):
                if not isinstance(chunk, bytes):
                    raise ValueError('invalid_download_response')
                output.write(chunk)
                digest.update(chunk)
                count += len(chunk)
                if count > CHECKPOINT_SIZE:
                    raise ValueError('checkpoint_integrity_failed')
            output.flush()
            os.fsync(output.fileno())
        if count != CHECKPOINT_SIZE or digest.hexdigest() != CHECKPOINT_SHA256:
            raise ValueError('checkpoint_integrity_failed')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def inference_config(original: str) -> str:
    """Preserve the model architecture; reduce only execution memory settings."""
    training, separator, inference = original.partition('\ninference:\n')
    if not separator:
        raise ValueError('config_incompatible')
    training, amp_count = re.subn(r'(?m)^(  use_amp:) true\b', r'\1 false', training)
    inference, batch_count = re.subn(r'(?m)^(  batch_size:) \d+\b', r'\1 1', inference)
    if amp_count != 1 or batch_count != 1:
        raise ValueError('config_incompatible')
    return training + separator + inference


def configure_env(path: Path, values: Mapping[str, str]) -> None:
    if not values.keys() <= ALLOWED_ENV_KEYS or any('\n' in value or '\r' in value for value in values.values()):
        raise ValueError('invalid_roformer_setting')
    original = path.read_bytes().decode('utf-8') if path.exists() else ''
    lines: list[str] = []
    updated: set[str] = set()
    for line in original.splitlines(keepends=True):
        match = re.match(r'^(?:export\s+)?(VOICE_ROFORMER_[A-Z_]+)\s*=', line)
        key = match.group(1) if match else None
        if key is not None and key in values:
            if key not in updated:
                ending = '\r\n' if line.endswith('\r\n') else '\n'
                lines.append(f'{key}={json.dumps(values[key])}{ending}')
                updated.add(key)
        else:
            lines.append(line)
    result = ''.join(lines)
    if result and not result.endswith(('\n', '\r')):
        result += '\n'
    for key, value in values.items():
        if key not in updated:
            result += f'{key}={json.dumps(value)}\n'
    atomic_text(path, result)


def setup(engine: Path, dotenv: Path) -> None:
    commit = subprocess.check_output(['git', '-C', str(engine), 'rev-parse', 'HEAD'], text=True).strip()
    if commit != ENGINE_COMMIT:
        raise ValueError('engine_version_mismatch')
    source = engine / CONFIG_RELATIVE
    if sha256_file(source) != CONFIG_SHA256:
        raise ValueError('config_integrity_failed')
    model_directory = engine / 'remiqora-models' / MODEL_REVISION
    checkpoint = model_directory / 'MelBandRoformer.ckpt'
    config = model_directory / 'config.yaml'
    print(f'Preparing verified vocal weights ({CHECKPOINT_SIZE / 1024**3:.2f} GiB).', flush=True)
    download_checkpoint(checkpoint)
    atomic_text(config, inference_config(source.read_text(encoding='utf-8')))
    atomic_text(model_directory / 'provenance.json', json.dumps({
        'engine_repository': 'https://github.com/ZFTurbo/Music-Source-Separation-Training',
        'engine_commit': commit, 'model_repository': 'https://huggingface.co/KimberleyJSN/melbandroformer',
        'model_revision': MODEL_REVISION, 'checkpoint_sha256': CHECKPOINT_SHA256,
        'checkpoint_bytes': CHECKPOINT_SIZE, 'original_config_sha256': CONFIG_SHA256,
        'inference_config_sha256': sha256_file(config),
        'execution_changes': {'inference.batch_size': 1, 'training.use_amp': False},
    }, indent=2) + '\n')
    configure_env(dotenv, {'VOICE_ROFORMER_DIR': str(engine.resolve()), 'VOICE_ROFORMER_MODEL_TYPE': 'mel_band_roformer',
        'VOICE_ROFORMER_CONFIG': str(config.resolve()), 'VOICE_ROFORMER_CHECKPOINT': str(checkpoint.resolve())})
    print('RoFormer artifacts verified and configured. Restart the backend to load these settings.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', required=True, type=Path)
    parser.add_argument('--dotenv', required=True, type=Path)
    arguments = parser.parse_args()
    setup(arguments.engine, arguments.dotenv)
