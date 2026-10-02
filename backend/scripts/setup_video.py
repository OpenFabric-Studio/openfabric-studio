#!/usr/bin/env python3
"""Pinned engine setup; weights require an explicit download flag and cache root.

Existing checkouts and environments are inspected, never reset or resynchronised.
The immutable vendor lock pins runtime dependencies; inference-only fresh installs
avoid trainer/development extras. A render/preflight never calls this installer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.video_engine import (ENGINE_COMMIT, LOCK_SHA256, ProfileId,
    VideoEngineError, ensure_compatibility, inspect_readiness, model_packs,
    verify_and_record_artifacts)

ENGINE_REPO = 'https://github.com/dgrauet/ltx-2-mlx.git'
logger = logging.getLogger(__name__)


def _git(engine_dir: Path, *arguments: str) -> str:
    result = subprocess.run(['git', '-C', str(engine_dir), *arguments],
        check=True, capture_output=True, text=True, timeout=120)
    return result.stdout.strip()


def _validate_checkout(engine_dir: Path) -> None:
    if _git(engine_dir, 'rev-parse', 'HEAD') != ENGINE_COMMIT:
        raise VideoEngineError('engine_checkout_revision')
    if _git(engine_dir, 'status', '--porcelain', '--untracked-files=normal'):
        raise VideoEngineError('engine_checkout_dirty')
    lock = engine_dir / 'uv.lock'
    if not lock.is_file() or hashlib.sha256(lock.read_bytes()).hexdigest() != LOCK_SHA256:
        raise VideoEngineError('engine_incompatible')
    ensure_compatibility(engine_dir)


def prepare_engine(engine_dir: Path) -> None:
    """Create only an absent checkout; validate existing work without mutations."""
    if engine_dir.exists() or engine_dir.is_symlink():
        if engine_dir.is_symlink() or not (engine_dir / '.git').exists() or not (engine_dir / 'pyproject.toml').is_file():
            raise VideoEngineError('engine_checkout_conflict')
        _validate_checkout(engine_dir)
        return
    engine_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.video-engine-', dir=engine_dir.parent) as scratch:
        staged = Path(scratch) / 'checkout'
        staged.mkdir()
        _git(staged, 'init')
        _git(staged, 'remote', 'add', 'origin', ENGINE_REPO)
        _git(staged, 'fetch', '--depth', '1', 'origin', ENGINE_COMMIT)
        _git(staged, 'checkout', '--detach', 'FETCH_HEAD')
        _validate_checkout(staged)
        # Fail if someone created the destination during clone; never overwrite it.
        if engine_dir.exists() or engine_dir.is_symlink():
            raise VideoEngineError('engine_checkout_conflict')
        staged.rename(engine_dir)


def sync_environment(engine_dir: Path) -> None:
    if (engine_dir / '.venv').exists():
        command = ['uv', 'sync', '--frozen', '--no-dev', '--inexact', '--offline', '--check']
    else:
        command = ['uv', 'sync', '--frozen', '--no-dev', '--no-python-downloads']
    try:
        subprocess.run(command, cwd=engine_dir, check=True, timeout=900)
    except subprocess.CalledProcessError as exc:
        # Updating an existing environment is a separate operator decision.
        raise VideoEngineError('engine_environment_unready') from exc


def download_argv(engine_dir: Path, cache_dir: Path, profile_id: ProfileId) -> list[list[str]]:
    packs = model_packs()
    selected = [packs[profile_id]] + ([packs['gemma3']] if profile_id == 'ltx23' else [])
    return [[str(engine_dir / '.venv/bin/hf'), 'download', pack.repo_id,
             *[item.name for item in pack.files], '--revision', pack.revision,
             '--cache-dir', str(cache_dir / 'hub')] for pack in selected]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine-dir', type=Path,
        default=Path(__file__).resolve().parents[2] / 'external/ltx-2-mlx')
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--pack', choices=['ltx23', 'ltx25'], default='ltx23')
    parser.add_argument('--download-models', action='store_true')
    parser.add_argument('--verify-models', action='store_true')
    parser.add_argument('--verify-only', action='store_true', help='Verify installed weights and record identities; no install/sync/download')
    parser.add_argument('--preflight', action='store_true', help='Read-only; does not create engine or environment')
    args = parser.parse_args()
    engine: object = args.engine_dir
    cache: object = args.cache_dir
    pack: object = args.pack
    download: object = args.download_models
    verify: object = args.verify_models
    verify_only: object = args.verify_only
    preflight: object = args.preflight
    if (not isinstance(engine, Path) or cache is not None and not isinstance(cache, Path)
            or pack not in ('ltx23', 'ltx25') or not isinstance(pack, str)
            or not isinstance(download, bool) or not isinstance(verify, bool) or not isinstance(preflight, bool)
            or not isinstance(verify_only, bool)):
        return 2
    profile: ProfileId = 'ltx23' if pack == 'ltx23' else 'ltx25'
    if (download or verify or preflight or verify_only) and cache is None:
        parser.error('--cache-dir is required for model operations/preflight')
    try:
        if verify_only and isinstance(cache, Path):
            ensure_compatibility(engine)
            print(json.dumps({'content_fingerprint': verify_and_record_artifacts(cache, profile)}))
            return 0
        if preflight:
            if isinstance(cache, Path):
                print(json.dumps(asdict(inspect_readiness(engine, cache, profile))))
            return 0
        prepare_engine(engine)
        sync_environment(engine)
        if isinstance(cache, Path):
            ready = inspect_readiness(engine, cache, profile)
            if download:
                if 'insufficient_disk_space' in ready.warnings:
                    raise VideoEngineError('insufficient_disk_space')
                for command in download_argv(engine, cache, profile):
                    subprocess.run(command, check=True)
            if download or verify:
                verify_and_record_artifacts(cache, profile)
            print(json.dumps(asdict(inspect_readiness(engine, cache, profile))))
        else:
            print(json.dumps({'engine_ready': True, 'engine_commit': ENGINE_COMMIT,
                'warnings': ['model_installation_not_checked'],
                'next_step': 'Rerun with --cache-dir PATH --download-models to explicitly install pinned weights.'}))
    except VideoEngineError as exc:
        logger.error('Video setup rejected: %s (%s)', exc.code, exc.detail)
        print(json.dumps({'error_code': exc.code}), file=sys.stderr)
        return 2
    except (OSError, subprocess.SubprocessError):
        logger.exception('Video setup failed')
        print(json.dumps({'error_code': 'engine_setup_failed'}), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
