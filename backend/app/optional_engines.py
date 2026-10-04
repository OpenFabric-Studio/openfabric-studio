"""Optional local engines. Setup clones them. This module never downloads weights.

Kokoro, Chatterbox, Wan 2.2 TI2V-5B (mlx-video), and RVC can be installed from
Settings or the setup_*.sh wrappers. Generation is not callable. require_installed
only checks the pinned checkout and its virtualenv. refuse_generation always
stops, including when that checkout is present, so a missing product pipeline
cannot be mistaken for a finished voice or video path.

GPT-SoVITS, Seed-VC, and LTX stay the engines they already are.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .module_contracts import ModuleId

OPTIONAL_ENGINE_IDS: tuple[ModuleId, ...] = ('kokoro', 'chatterbox', 'wan22', 'rvc')

SETUP_SCRIPTS: dict[ModuleId, str] = {
    'kokoro': 'setup_kokoro.sh',
    'chatterbox': 'setup_chatterbox.sh',
    'wan22': 'setup_wan22.sh',
    'rvc': 'setup_rvc.sh',
}

# Distribution names checked with importlib.metadata after an explicit install.
PACKAGES: dict[ModuleId, tuple[str, ...]] = {
    'kokoro': ('kokoro', 'soundfile'),
    'chatterbox': ('chatterbox-tts',),
    'wan22': ('mlx-video',),
    'rvc': ('torch', 'soundfile', 'librosa', 'faiss-cpu'),
}

_RVC_PACKAGES = (
    'torch', 'torchaudio', 'soundfile', 'librosa', 'numpy', 'faiss-cpu',
    'pyyaml', 'scipy', 'av', 'einops', 'tqdm',
)


class OptionalEngineError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def engine_dir(identifier: ModuleId) -> Path:
    from . import config
    paths = {
        'kokoro': config.KOKORO_DIR,
        'chatterbox': config.CHATTERBOX_DIR,
        'wan22': config.WAN22_DIR,
        'rvc': config.RVC_DIR,
    }
    try:
        return paths[identifier]
    except KeyError as exc:
        raise OptionalEngineError('unknown_engine', 'That optional engine is not registered.') from exc


def install_command(uv: str, identifier: ModuleId, python: Path, target: Path) -> list[str]:
    """Package install only. No model repo, no from_pretrained, no Turbo, no 14B."""
    if identifier == 'rvc':
        return [uv, 'pip', 'install', '--python', str(python), *_RVC_PACKAGES]
    if identifier in ('kokoro', 'chatterbox', 'wan22'):
        command = [uv, 'pip', 'install', '--python', str(python), '-e', str(target)]
        if identifier == 'kokoro':
            command.append('soundfile')
        return command
    raise OptionalEngineError('unknown_engine', 'That optional engine is not registered.')


def setup_hint(identifier: ModuleId) -> str:
    script = SETUP_SCRIPTS.get(identifier)
    if script is None:
        raise OptionalEngineError('unknown_engine', 'That optional engine is not registered.')
    return f'Run ./{script} on the Mac. OpenFabric does not download weights for this engine.'


def installed(identifier: ModuleId) -> bool:
    from .module_catalog import DEFINITIONS, engine_python
    if identifier not in OPTIONAL_ENGINE_IDS:
        return False
    root = engine_dir(identifier)
    definition = DEFINITIONS[identifier]
    python = engine_python(root, sys.platform)
    return all((root / marker).exists() for marker in definition.source_markers) and python.is_file()


def require_installed(identifier: ModuleId) -> Path:
    if identifier not in OPTIONAL_ENGINE_IDS:
        raise OptionalEngineError('unknown_engine', 'That optional engine is not registered.')
    if not installed(identifier):
        raise OptionalEngineError('engine_not_installed', setup_hint(identifier))
    return engine_dir(identifier)


def refuse_generation(identifier: ModuleId) -> None:
    """Fail closed. A present checkout still cannot synthesize, clone, or render."""
    require_installed(identifier)
    details = {
        'kokoro': 'Kokoro narration is not callable yet. The checkout can be installed, but OpenFabric does not run KPipeline and does not download voices. It does not clone a person and does not replace GPT-SoVITS. On a Mac the upstream hint is PYTORCH_ENABLE_MPS_FALLBACK=1.',
        'chatterbox': 'Chatterbox generation is not callable yet. Original and multilingual are the only classes this app will accept later, on cuda, cpu, or mps. Turbo is refused. Weights are not downloaded.',
        'wan22': 'Wan 2.2 video is not callable yet. Only TI2V-5B is registered, and OpenFabric does not run mlx-video or download weights. 14B, S2V, and Animate are not wired.',
        'rvc': 'RVC conversion is not callable yet. On a Mac the install is CPU, not GPU. HuBERT, RMVPE, and trained voices are not downloaded. Seed-VC is unchanged.',
    }
    raise OptionalEngineError('generation_not_wired', details[identifier])


def prepare_chatterbox(model: str) -> None:
    if model == 'turbo':
        raise OptionalEngineError('chatterbox_turbo_refused', 'Chatterbox Turbo is not wired and is not claimed to work on a Mac.')
    if model not in ('original', 'multilingual'):
        raise OptionalEngineError('chatterbox_model_refused', 'Only the original and multilingual Chatterbox classes are registered.')
    refuse_generation('chatterbox')


def prepare_wan(variant: str) -> None:
    if variant != 'ti2v-5b':
        raise OptionalEngineError('wan_variant_refused', 'Only Wan 2.2 TI2V-5B is registered. 14B, S2V, and Animate are not wired.')
    refuse_generation('wan22')


def prepare_kokoro() -> None:
    refuse_generation('kokoro')


def prepare_rvc() -> None:
    refuse_generation('rvc')
