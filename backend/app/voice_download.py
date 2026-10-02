"""Fetch the inspected Seed-VC training baseline without loading GPU models.

Run only in the installed engine environment, after an explicit voice build.
The call matches Seed-VC hf_utils.load_custom_model_from_hf: Plachta/Seed-VC,
the preset's pretrained_model filename, and HF_HUB_CACHE.
"""
from __future__ import annotations

import importlib
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class HubDownloader(Protocol):
    def __call__(self, *, repo_id: str, filename: str, cache_dir: str) -> str: ...


def download(filename: str, destination: Path) -> None:
    if not re.fullmatch(r'[A-Za-z0-9_.-]+\.pth', filename):
        raise ValueError('invalid_base_filename')
    module = importlib.import_module('huggingface_hub')
    function: object = getattr(module, 'hf_hub_download', None)
    if not isinstance(function, HubDownloader):
        raise ValueError('engine_incompatible')
    downloaded = function(repo_id='Plachta/Seed-VC', filename=filename, cache_dir=os.environ.get('HF_HUB_CACHE') or './checkpoints')
    if not isinstance(downloaded, str) or not Path(downloaded).is_file():
        raise ValueError('base_unavailable')
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix('.partial.pth')
    try:
        shutil.copyfile(downloaded, partial)
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('usage: voice_download.py FILENAME DESTINATION')
    download(sys.argv[1], Path(sys.argv[2]))
