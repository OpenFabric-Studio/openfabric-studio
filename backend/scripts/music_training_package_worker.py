"""Owned CPU archive preparation; SIGTERM unwinds private staging and partial ZIPs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import signal
import sys
from types import FrameType
from typing import NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.music_training_contracts import MusicLoraTrainingRequest
from app.music_training_package import prepare_package
from scripts.stable_lora_worker import atomic_json, object_value, text


def run(path: Path) -> None:
    if (not path.is_absolute() or path.is_symlink() or path.resolve() != path or not path.is_file()
            or path.stat().st_size > 16 * 1024 * 1024 or re.fullmatch(r'[0-9a-f]{32}', path.parent.name) is None):
        raise ValueError('music_training_package_request_invalid')
    payload = object_value(json.loads(path.read_bytes()))
    if set(payload) != {'request', 'sources', 'request_sha256'}:
        raise ValueError('music_training_package_request_invalid')
    request = MusicLoraTrainingRequest.model_validate(payload['request'])
    fingerprint = hashlib.sha256(request.model_dump_json(exclude_none=True).encode()).hexdigest()
    if payload.get('request_sha256') != fingerprint:
        raise ValueError('music_training_package_request_changed')
    values = payload.get('sources')
    if not isinstance(values, list) or len(values) != len(request.samples):
        raise ValueError('music_training_package_dataset_invalid')
    sources = [Path(text(value, 10000)) for value in values]
    completion = path.parent / 'package-completion.json'
    if completion.exists() or completion.is_symlink():
        raise ValueError('music_training_package_existing_completion')
    archive = prepare_package(request, sources, path.parent / 'package.zip')
    with archive.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    atomic_json(completion, {'job_id': path.parent.name, 'engine': request.engine, 'request_sha256': fingerprint,
        'archive': 'package.zip', 'sha256': checksum, 'bytes': archive.stat().st_size})


def cancel(signum: int, frame: FrameType | None) -> NoReturn:
    raise SystemExit(128 + signum)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True, type=Path)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, cancel)
    run(args.request)


if __name__ == '__main__':
    main()
