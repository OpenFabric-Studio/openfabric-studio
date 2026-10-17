"""Launch the pinned ACE API with local-only base conditioning admission.

This child uses the engine's Python and standard library only. Ordinary engine
launches never import this adapter or change their download behavior.
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path, PurePosixPath
import runpy
import sys
from types import ModuleType
from typing import NoReturn

BASE_MODEL = 'acestep-v15-base'
_COMPONENTS = frozenset((BASE_MODEL, 'vae', 'Qwen3-Embedding-0.6B'))
MINIMUM_FILES = (
    'checkpoints/acestep-v15-base/model.safetensors', 'checkpoints/acestep-v15-base/config.json',
    'checkpoints/vae/diffusion_pytorch_model.safetensors', 'checkpoints/vae/config.json',
    'checkpoints/Qwen3-Embedding-0.6B/model.safetensors', 'checkpoints/Qwen3-Embedding-0.6B/config.json',
)


def _deny_download(*args: object, **kwargs: object) -> NoReturn:
    raise RuntimeError('ACE conditioning is offline; review missing model assets in app setup')


def _replace(module: ModuleType, name: str, replacement: object) -> None:
    existing: object = getattr(module, name, None)
    if not callable(existing):
        raise RuntimeError('ACE offline adapter source changed')
    setattr(module, name, replacement)


def install_offline_guards(root: Path, required: tuple[str, ...]) -> None:
    """Bind the inspected native init hooks before api_server captures imports."""
    root = root.resolve()
    if not 6 <= len(required) <= 100 or len(set(required)) != len(required) or not set(MINIMUM_FILES).issubset(required):
        raise RuntimeError('ACE offline artifact identity is invalid')
    for relative in required:
        parts = PurePosixPath(relative).parts
        if (len(relative) > 300 or len(parts) < 3 or parts[0] != 'checkpoints' or parts[1] not in _COMPONENTS
                or '\\' in relative or ':' in relative or any(part in ('.', '..') for part in parts)):
            raise RuntimeError('ACE offline artifact identity is invalid')

    def present() -> bool:
        try:
            return all(not (path := root / relative).is_symlink() and path.resolve().is_relative_to(root)
                and path.is_file() and path.stat().st_size > 0 for relative in required)
        except OSError:
            return False

    def require_model(model_name: str, checkpoint_dir: str) -> str:
        if model_name not in _COMPONENTS or Path(checkpoint_dir).resolve() != root / 'checkpoints' or not present():
            _deny_download()
        return str(root / 'checkpoints' / model_name)

    def ensure_present(self: object, *, checkpoint_path: Path, config_path: str,
                       prefer_source: str | None, vae_variant: str | None = None) -> tuple[str, bool] | None:
        if (checkpoint_path.resolve() != root / 'checkpoints' or config_path != BASE_MODEL
                or vae_variant not in (None, 'official') or not present()):
            return 'ERROR: ACE conditioning is offline; selected local assets are unavailable', False
        return None

    core = importlib.import_module('acestep.model_downloader')
    api = importlib.import_module('acestep.api.model_download')
    for name in ('_smart_download', '_download_from_huggingface_internal', '_download_from_modelscope_internal',
                 'ensure_main_model', 'ensure_dit_model', 'ensure_lm_model', 'ensure_vae_model'):
        _replace(core, name, _deny_download)
    _replace(api, 'ensure_model_downloaded', require_model)
    for name in ('can_access_google', 'download_from_huggingface', 'download_from_modelscope'):
        _replace(api, name, _deny_download)
    helpers = importlib.import_module('acestep.core.generation.handler.init_service_downloads')
    mixin: object = getattr(helpers, 'InitServiceDownloadsMixin', None)
    if not isinstance(mixin, type):
        raise RuntimeError('ACE offline adapter source changed')
    setattr(mixin, '_ensure_models_present', ensure_present)


def main() -> None:
    root = Path(os.environ['ACESTEP_PROJECT_ROOT']).resolve()
    if Path.cwd().resolve() != root:
        raise RuntimeError('ACE offline engine identity changed')
    encoded = os.environ['OPENFABRIC_ACE_LOCAL_ARTIFACTS']
    if len(encoded) > 32768:
        raise RuntimeError('ACE offline artifact identity is invalid')
    raw: object = json.loads(encoded)
    if not isinstance(raw, list) or any(not isinstance(value, str) for value in raw):
        raise RuntimeError('ACE offline artifact identity is invalid')
    required = tuple(value for value in raw if isinstance(value, str))
    sys.path.insert(0, str(root))
    install_offline_guards(root, required)
    runpy.run_module('acestep.api_server', run_name='__main__')


if __name__ == '__main__':
    main()
