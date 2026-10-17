"""Pinned optional CPU dependencies and local-only checkpoint readiness.

Status only reads bounded files. Setup is invoked by the existing module owner;
analysis never invokes setup or acquires a model.
"""
from __future__ import annotations

from email.parser import BytesParser
import hashlib
import os
from pathlib import Path
import shutil
import sys
from typing import TYPE_CHECKING, Literal

from pydantic import Field, ValidationError

from .atomic_files import write_object
from .job_lifecycle import run_owned_thread
from .music_analysis_contracts import MusicAnalysisErrorCode, MusicAnalysisContract
from .music_beats import (BEATS_REVISION, BeatAnalysisRecipe, BeatRuntimeIdentity, BeatWindowResult,
    BeatWorkerOutcome, BeatWorkerRequest, digest)

if TYPE_CHECKING:
    from .module_catalog import ModuleEnvironment
    from .module_jobs import InstallContext, InstallOutcome
    from .module_runtime import RuntimeEvidence
    from .audio_quality import RunCommand
    import numpy as np
    from numpy.typing import NDArray

REVISION: Literal['beat-this-small0-cpu-v1'] = BEATS_REVISION
RECEIPT_NAME = '.openfabric-beats-runtime.json'
WEIGHTS_URL = 'https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/small0.ckpt'
WEIGHTS_BYTES = 8451101
PUBLISHER_SHA1 = '77a7ef5c21f628578f2b259ac29d2d680412efcc'
# Observed separately after a capped publisher download and SHA-1 verification.
WEIGHTS_SHA256 = '6074be2c4d490c5f6101fcc374a1ec72ae93456e23bb6019783b849f5dc7d47b'
SOURCE_SHA256 = '900e9a9bcc1ceb40a249df5a4b8bfaec48e2fa38553388952719842a8be51a34'
SOURCE_NAMES = ('__init__.py', 'cli.py', 'dataset/__init__.py', 'dataset/augment.py', 'dataset/dataset.py',
    'dataset/mmnpz.py', 'inference.py', 'model/__init__.py', 'model/beat_tracker.py', 'model/loss.py',
    'model/pl_module.py', 'model/postprocessor.py', 'model/roformer.py', 'preprocessing.py', 'utils.py')
PACKAGES = {'beat-this': '1.1.0', 'torch': '2.6.0', 'torchaudio': '2.6.0', 'numpy': '2.2.6',
    'einops': '0.8.1', 'rotary-embedding-torch': '0.8.9', 'soxr': '0.5.0.post1', 'pydantic': '2.13.5'}


class BeatRuntimeError(ValueError):
    def __init__(self, code: MusicAnalysisErrorCode = 'analysis_runtime_unverified') -> None:
        self.code = code
        super().__init__(code)


class SetupMarker(MusicAnalysisContract):
    schema_version: Literal[1] = 1
    dependency_setup: Literal['beat-this-small0-cpu-v1'] = REVISION


def _file(path: Path, maximum: int) -> bytes:
    if path.is_symlink() or path.resolve() != path or not path.is_file() or path.stat().st_size > maximum:
        raise BeatRuntimeError()
    with path.open('rb') as stream:
        content = stream.read(maximum + 1)
    if len(content) > maximum:
        raise BeatRuntimeError()
    return content


def _hash(path: Path, maximum: int) -> str:
    return hashlib.sha256(_file(path, maximum)).hexdigest()


def python(root: Path) -> Path:
    return root / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')


def site_packages(root: Path) -> Path:
    return root / '.venv' / ('Lib/site-packages' if sys.platform == 'win32' else 'lib/python3.12/site-packages')


def dependency_identity(root: Path, python_version: str) -> BeatRuntimeIdentity:
    if root.is_symlink() or root.resolve() != root or not python_version.startswith('3.12.'):
        raise BeatRuntimeError()
    site = site_packages(root)
    source = site / 'beat_this'
    files = {name: _hash(source / name, 1024**2) for name in SOURCE_NAMES}
    actual_names: set[str] = set()
    for path in source.rglob('*.py'):
        actual_names.add(path.relative_to(source).as_posix())
        if len(actual_names) > len(SOURCE_NAMES):
            raise BeatRuntimeError()
    if set(SOURCE_NAMES) != actual_names or digest(files) != SOURCE_SHA256:
        raise BeatRuntimeError()
    packages: dict[str, str] = {}
    for count, path in enumerate(site.glob('*.dist-info/METADATA')):
        if count >= 1000:
            raise BeatRuntimeError()
        directory_name = path.parent.name.removesuffix('.dist-info').rsplit('-', 1)[0].lower().replace('_', '-')
        if directory_name not in PACKAGES:
            continue
        # Only selected metadata participates; no package or model is imported.
        message = BytesParser().parsebytes(_file(path, 1024**2))
        name, version = message.get('Name'), message.get('Version')
        if name is not None and version is not None:
            name = name.lower().replace('_', '-')
            if name in PACKAGES:
                if version.split('+')[0] != PACKAGES[name] or name in packages:
                    raise BeatRuntimeError()
                packages[name] = version
    if set(packages) != set(PACKAGES):
        raise BeatRuntimeError()
    return BeatRuntimeIdentity(source_sha256=SOURCE_SHA256, weights_sha256=WEIGHTS_SHA256,
        python_sha256=_hash(python(root).resolve(strict=True), 128 * 1024**2),
        python_version=python_version, packages=packages)


def identity(root: Path, *, require_weights: bool = True) -> BeatRuntimeIdentity:
    try:
        receipt = BeatRuntimeIdentity.model_validate_json(_file(root / RECEIPT_NAME, 16384))
        expected = dependency_identity(root, receipt.python_version)
        if expected != receipt:
            raise BeatRuntimeError()
        if require_weights:
            weight = root / 'weights/small0.ckpt'
            if not weight.is_file():
                raise BeatRuntimeError('analysis_weights_missing')
            if weight.stat().st_size != WEIGHTS_BYTES or _hash(weight, WEIGHTS_BYTES) != WEIGHTS_SHA256:
                raise BeatRuntimeError()
        return expected
    except BeatRuntimeError:
        raise
    except (OSError, ValueError, ValidationError) as error:
        raise BeatRuntimeError() from error


def configured_root() -> Path:
    from .module_catalog import configured_environment
    return configured_environment().paths['music_beats']


def recipe() -> tuple[Path, BeatRuntimeIdentity]:
    root = configured_root()
    return root, identity(root)


async def infer_window(samples: NDArray[np.float32], root: Path, beats: BeatAnalysisRecipe, run: RunCommand) -> BeatWindowResult:
    """The analysis owner/supervisor retains checkpoint and owns every child."""
    import numpy as np
    if (samples.dtype != np.float32 or samples.ndim != 2 or samples.shape[1] not in {1, 2}
            or not 0 < len(samples) <= 2646000 or not np.all(np.isfinite(samples))):
        raise BeatRuntimeError('invalid_audio')
    paths = [root / name for name in ('beats-input.f32', 'beats-request.json', 'beats-result.json')]
    if root.is_symlink() or root.resolve() != root or any(path.is_symlink() for path in paths):
        raise BeatRuntimeError()
    # Prior use of these names is quiescent: run must drain on every exit.
    for path in paths:
        path.unlink(missing_ok=True)
    data = np.ascontiguousarray(samples, dtype='<f4')
    content = memoryview(data).cast('B')
    input_sha256 = hashlib.sha256(content).hexdigest()
    with paths[0].open('xb') as output:
        output.write(content)
    request = BeatWorkerRequest(runtime_root=beats.runtime_root, input_sha256=input_sha256,
        frames=len(samples), channels=samples.shape[1], identity=beats.identity)
    paths[1].write_text(request.model_dump_json())
    complete = False
    try:
        await run([str(python(Path(beats.runtime_root))), '-I', str(Path(__file__).resolve().parents[1] / 'scripts/music_beats_worker.py'),
            '--request', str(paths[1]), '--result', str(paths[2])], 180.0)
        outcome = BeatWorkerOutcome.model_validate_json(_file(paths[2], 256 * 1024))
        if (outcome.input_sha256 != input_sha256 or outcome.configuration_sha256 != beats.identity.configuration_sha256
                or abs(outcome.result.duration_sec - len(samples) / 22050) > 1e-9):
            raise BeatRuntimeError()
        complete = True
        return outcome.result
    finally:
        # Unknown drain failures must retain their private inputs. A later
        # owner-verified retry may replace them after every child has exited.
        if complete:
            for path in paths:
                path.unlink(missing_ok=True)


def status(environment: ModuleEnvironment) -> RuntimeEvidence:
    from .module_contracts import ModuleEvidence
    from .module_runtime import RuntimeEvidence
    root = environment.paths['music_beats']
    dependencies = ready = False
    try:
        identity(root, require_weights=False)
        dependencies = True
        identity(root)
        ready = True
    except BeatRuntimeError:
        pass
    return RuntimeEvidence(ready=ready, installed=dependencies,
        capabilities=('offline_cpu_beats',) if ready else (), evidence=(
            ModuleEvidence(code='beat_this_dependencies', detail='Pinned CPU dependency receipt and source match.' if dependencies
                else 'The isolated beat-analysis dependency environment is missing or changed.', verified=dependencies),
            ModuleEvidence(code='beat_this_weights', detail='Exact local small0 checkpoint verified; no quality claim.' if ready
                else 'Select the reviewed small0 checkpoint in setup; analysis never downloads weights.', verified=ready)))


def initialize_marker(context: InstallContext, target: Path) -> None:
    from .module_jobs import ModuleSetupError
    context.confined(target)
    marker = target / RECEIPT_NAME
    if target.exists():
        try:
            content = _file(marker, 16384)
            try:
                SetupMarker.model_validate_json(content)
            except ValidationError:
                identity(target, require_weights=False)
        except (OSError, ValueError) as error:
            raise ModuleSetupError('installation_conflict') from error
    else:
        target.mkdir(parents=True)
        write_object(marker, SetupMarker().model_dump(mode='json'))


async def setup(context: InstallContext, target: Path, *, download_models: bool = False) -> InstallOutcome:
    from .module_install import DownloadArtifact, download_artifact, _record_owned
    from .module_jobs import InstallOutcome, ModuleSetupError
    initialize_marker(context, target)
    child_env = {**os.environ, 'PIP_NO_CACHE_DIR': '1', 'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1',
        'OPENBLAS_NUM_THREADS': '1', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}
    try:
        await run_owned_thread(identity, target, require_weights=False)
        complete = True
    except BeatRuntimeError:
        complete = False
    if not complete:
        # Dependency bytes are unknown; the reviewed module plan additionally
        # reserves an 8 GiB allowance. Verification of existing files needs none.
        if shutil.disk_usage(target).free < 2 * 1024**3:
            raise ModuleSetupError('insufficient_disk')
        context.update_detail('Installing the isolated pinned CPU beat-analysis dependencies; no inference is started.')
        await context.run([str(context.environment.python), '-m', 'venv', str(target / '.venv')], env=child_env)
        interpreter = python(target)
        torch = [str(interpreter), '-m', 'pip', 'install', '--no-cache-dir', '--only-binary=:all:', 'torch==2.6.0', 'torchaudio==2.6.0']
        if context.environment.platform != 'darwin':
            torch.extend(('--index-url', 'https://download.pytorch.org/whl/cpu'))
        await context.run(torch, env=child_env)
        requirements = Path(__file__).resolve().parents[1] / 'requirements-beats.txt'
        await context.run([str(interpreter), '-m', 'pip', 'install', '--no-cache-dir', '--only-binary=:all:', '-r', str(requirements)], env=child_env)
        context.workspace.mkdir(parents=True, exist_ok=True)
        version_file = context.confined(context.workspace / 'beats-python-version.txt')
        version_probe = 'import sys, pathlib; pathlib.Path(sys.argv[1]).write_text(sys.version.split()[0])'
        await context.run([str(interpreter), '-I', '-S', '-c', version_probe, str(version_file)], env=child_env, timeout=15)
        verified = await run_owned_thread(dependency_identity, target, _file(version_file, 80).decode().strip())
        # Verify the pinned package sources before executing their import path.
        probe = 'import sys, pathlib, beat_this.inference, torch; torch.set_num_threads(1); pathlib.Path(sys.argv[1]).write_text(sys.version.split()[0])'
        await context.run([str(interpreter), '-I', '-c', probe, str(version_file)], env=child_env, timeout=60)
        if await run_owned_thread(dependency_identity, target, _file(version_file, 80).decode().strip()) != verified:
            raise BeatRuntimeError()
        write_object(target / RECEIPT_NAME, verified.model_dump(mode='json'))
        # Replacing the dependency marker changes the installer's ownership hash.
        # Refresh it synchronously before a cancellable model acquisition starts.
        _record_owned(context, 'music_beats', target, REVISION)
    if download_models:
        weights = target / 'weights/small0.ckpt'
        if weights.exists() or weights.is_symlink():
            if weights.is_symlink() or weights.stat().st_size != WEIGHTS_BYTES or await run_owned_thread(_hash, weights, WEIGHTS_BYTES) != WEIGHTS_SHA256:
                raise ModuleSetupError('installation_conflict')
        else:
            # Match the catalog's known download plus 50% temporary reserve.
            # The shared downloader validates bytes/hash but has no disk guard.
            if shutil.disk_usage(target).free < WEIGHTS_BYTES + WEIGHTS_BYTES // 2:
                raise ModuleSetupError('insufficient_disk')
            weights.parent.mkdir(exist_ok=True)
            await download_artifact(DownloadArtifact(url=WEIGHTS_URL, bytes=WEIGHTS_BYTES, sha256=WEIGHTS_SHA256), weights, context)
        await run_owned_thread(identity, target)
        return InstallOutcome('verified', 'Pinned local CPU beat runtime and small0 checkpoint verified. Accuracy still requires annotated review.')
    return InstallOutcome('manual', 'CPU dependencies verified. No checkpoint was downloaded; select small0 weights in setup before beat analysis.')
