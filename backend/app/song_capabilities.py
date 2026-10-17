"""Bounded proof for pinned ACE conditioning; no model import, launch or download."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Literal

from .job_lifecycle import run_owned_thread
from .module_catalog import ModuleEnvironment, configured_environment, engine_python, supported
from .module_music_models import ace_conditioning_artifacts
from .music_queue_contracts import MusicQueueError
from .music_conditioning_contracts import ConditioningErrorCode
from .song_from_beat_contracts import SongFromBeatCapabilities, SongFromBeatCapability
from .video_io import hash_file
from .video_projects import VideoProjectError

SOURCE_COMMIT = 'ca1e85fe9430179831e6bc6be790c332190a3866'
BASE_MODEL = 'acestep-v15-base'
BOOTSTRAP_SHA256 = '934524fd7242472b04ff40c89d0c2606cadf80deceec7a996042e3b6a16ce67c'
# Critical request/conditioning/model-loading source; pristine and this fork's cancellation patch.
SOURCE_FILES: dict[str, tuple[str, ...]] = {
    'acestep/constants.py': ('7b8d4ce49649c819d1b3be87a434be2d90768308768d4620639328f906209b22',),
    'acestep/api/http/release_task_request_parser.py': ('ff664073c4730a2690cb266029a206f1bc1793905cc5cb32d733aa0d3433b96e',),
    'acestep/api/http/release_task_request_builder.py': ('031ac48cde3c1afb83e0dd05c03bbba5d5c5339b8f2a5577ea373ab28b4f8255',),
    'acestep/api/job_generation_setup.py': ('69ee03d5d0091a9176a6001f430b84fb4b9d65ddae6b72cf2ccd367b7de9b113',),
    'acestep/inference.py': ('f43827def02a1122e2de483961b04f16544e4da9108eed9e42fd529175c15df5',),
    'acestep/core/generation/handler/generate_music_request.py': ('281e481be2a0a1ab0b5dbc3cf471aef8611096305fc46cee5bcc22eaeb297c8c',),
    'acestep/core/generation/handler/conditioning_masks.py': ('5d9c1fdb249931794af76db83166347be0e8a081f958338c9a7963131e87a365',),
    'acestep/core/generation/handler/conditioning_target.py': ('472b302837800f54972b5b5a3d6374b7855ee3eac9e05762757fcb41d5455068',),
    'acestep/core/generation/handler/padding_utils.py': ('16de06cdc360c23dfbec563e36da64a8f5711e409204f800fae86b78f129e0c4',),
    'acestep/core/generation/handler/service_generate_execute.py': ('508fd70b669600ca8626c703d4213eda2c6ef46fcaee4fd9d662cb4843d83bdc', 'bdb435d7bfbf4a362cc254075ce2e0a8b8c50fafe39f320e02aa841186b783c3'),
    'acestep/api/http/model_init_service.py': ('e5e1c18f29cc5ece306a64bfd4b5b4b9e586907f725740fa9379bd6b2d380932',),
    'acestep/api/startup_model_init.py': ('cfa0f91d81ceed382ac79c92972da32abb537720594df01a741537d0a46cffd6',),
    'acestep/models/base/apg_guidance.py': ('33985a589f3af4f3daff071b71cae68da1bf971070da7c27223acbea9f5f2ae7',),
    'acestep/models/base/configuration_acestep_v15.py': ('5f15aa79fe793bba3c00b6992dd193a269954950603d5031ecd7703b0fa69da1',),
    'acestep/models/base/modeling_acestep_v15_base.py': ('f3edaa0f86ce174c0c6703a8d80442673783ba96515a0fa84126a9b605b0919d',),
    'acestep/api_server.py': ('b580bbff8878362f051d8652a2c12c57473d49edae46dd35c47b8f1bd3df3adc', '2b5234c2b85bb55fa07f987bef14f3397a5bfcdc95acd61a05a04d1f90b040ab'),
    'acestep/core/generation/handler/init_service_orchestrator.py': ('df1e3ac43db9018793adae343760b43c56e7e81648cfcd057730162352c49d72',),
    'acestep/core/generation/handler/init_service_loader.py': ('22b41692bcb73fead4c831d16eaef3dda56cc995577f2353d763445dae7362e1',),
    'acestep/core/generation/handler/init_service_catalog.py': ('48d41952ec25da854bfcb2ccdf9fa0cbf285c1f5e50cb34b9e978d6e634620d2',),
    'acestep/core/generation/handler/init_service_downloads.py': ('02a95f2293dc0cd82ff5046816503668f8339157ba0b18715e061f3142999f8f', 'cb61a4facc4894bd11a1bef2ee12e9dc5be2142eb23d2093506b0996780ee695'),
    'acestep/api/model_download.py': ('38723f6de7ca4349f671719c650526ad7baa7babfe4814c129b3489eac9afdbb',),
    'acestep/model_downloader.py': ('34fcd1914620c11ad487070f47966803d48a290b7b05b42259cf9fd437c032d5',),
}


@dataclass(frozen=True)
class AceRuntimeProof:
    source_proof_sha256: str
    model_sha256: str
    artifacts_sha256: str


def proof_digest() -> str:
    return hashlib.sha256(json.dumps({'native': SOURCE_FILES, 'offline_adapter': BOOTSTRAP_SHA256},
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def bootstrap_path() -> Path:
    return Path(__file__).resolve().parents[1] / 'scripts/ace_conditioning_server.py'


def source_ready(environment: ModuleEnvironment) -> bool:
    root = environment.paths['ace_step'].resolve()
    try:
        adapter = bootstrap_path()
        if (adapter.is_symlink() or not adapter.is_file() or adapter.stat().st_size > 32768
                or hashlib.sha256(adapter.read_bytes()).hexdigest() != BOOTSTRAP_SHA256):
            return False
        for relative, expected in SOURCE_FILES.items():
            path = root / relative
            if (path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file()
                    or path.stat().st_size > 2 * 1024**2 or hashlib.sha256(path.read_bytes()).hexdigest() not in expected):
                return False
        return True
    except OSError:
        return False


def weights_present(environment: ModuleEnvironment) -> bool:
    from .module_jobs import ModuleSetupError
    root = environment.paths['ace_step'].resolve()
    try:
        return all(not (path := root / item.path).is_symlink() and path.resolve().is_relative_to(root)
            and path.is_file() and (path.stat().st_size == item.bytes or item.path.endswith('.py'))
            for item in ace_conditioning_artifacts())
    except (OSError, ModuleSetupError):
        return False


def capabilities(environment: ModuleEnvironment | None = None) -> SongFromBeatCapabilities:
    env = environment or configured_environment()
    installed = supported('ace_step', env) and engine_python(env.paths['ace_step'], env.platform).is_file() and source_ready(env)
    weights = weights_present(env)
    reason: ConditioningErrorCode | None = 'conditioning_runtime_unavailable' if not installed else 'conditioning_model_changed' if not weights else None
    modes: tuple[Literal['ace_complete', 'ace_lego'], ...] = ('ace_complete', 'ace_lego')
    return SongFromBeatCapabilities(modes=[SongFromBeatCapability(mode=mode, proof_status='source_verified',
        installed=installed, weights_ready=weights, ready=installed and weights, source_commit=SOURCE_COMMIT,
        proof_sha256=proof_digest(), reason=reason,
        notes='Pinned source passes retained ctx_audio into Complete/Lego conditioning. Only the reviewed base model is admitted. '
            'Source and exact weight hashes are checked before loading; model execution and audio quality remain unverified. '
            'The native pipeline decodes a new mixture and can pad or normalize it: no isolated addition, original PCM preservation, '
            'exact output duration, or harmonic fidelity is promised. Inputs must be 10–300 seconds; no automatic repetition or fallback.')
        for mode in modes])


async def verify_runtime(environment: ModuleEnvironment | None = None) -> AceRuntimeProof:
    """Hash local model inputs before any init request; never invoke native downloads."""
    env = environment or configured_environment()
    if not await run_owned_thread(source_ready, env):
        raise MusicQueueError('conditioning_runtime_changed')
    if not supported('ace_step', env) or not engine_python(env.paths['ace_step'], env.platform).is_file():
        raise MusicQueueError('conditioning_runtime_unavailable')
    root = env.paths['ace_step'].resolve()
    digests: list[tuple[str, str]] = []
    model_sha256 = ''
    for item in ace_conditioning_artifacts():
        path = root / item.path
        allowed: tuple[str, ...] = (item.sha256,)
        if item.path.endswith('.py'):
            allowed += SOURCE_FILES.get('acestep/models/base/' + path.name, ())
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
                raise MusicQueueError('conditioning_model_changed')
            digest = await hash_file(path, max_bytes=max(item.bytes, 2 * 1024**2) if path.suffix == '.py' else item.bytes)
            if digest not in allowed or path.suffix != '.py' and path.stat().st_size != item.bytes:
                raise MusicQueueError('conditioning_model_changed')
        except (OSError, VideoProjectError) as error:
            raise MusicQueueError('conditioning_model_changed') from error
        digests.append((item.path, digest))
        if item.path.endswith('/acestep-v15-base/model.safetensors'):
            model_sha256 = digest
    if not model_sha256:
        raise MusicQueueError('conditioning_model_changed')
    return AceRuntimeProof(proof_digest(), model_sha256,
        hashlib.sha256(json.dumps(digests, separators=(',', ':')).encode()).hexdigest())
