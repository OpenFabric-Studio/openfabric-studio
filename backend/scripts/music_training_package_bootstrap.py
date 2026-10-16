"""Standalone CUDA preparation and fresh training for an exported local package."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
from types import FrameType
from typing import TYPE_CHECKING, NoReturn
import uuid
from urllib.parse import urlsplit

_ROOT = Path(__file__).resolve().parent
_BACKEND = _ROOT / 'backend' if (_ROOT / 'backend').is_dir() else _ROOT.parent
sys.path.insert(0, str(_BACKEND))

if TYPE_CHECKING:
    from app.orchestrator.windows_job import WindowsJob


def object_value(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError('package_manifest_invalid')
    return {key: item for key, item in value.items() if isinstance(key, str)}


def text(value: object, maximum: int = 4000) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or '\0' in value:
        raise ValueError('package_manifest_invalid')
    return value


def integer(value: object, lower: int, upper: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not lower <= value <= upper:
        raise ValueError('package_manifest_invalid')
    return value


def child(root: Path, value: object) -> Path:
    name = text(value, 10000)
    relative = PurePosixPath(name)
    if relative.is_absolute() or '\\' in name or ':' in name or any(part in ('.', '..') for part in relative.parts):
        raise ValueError('package_path_unsafe')
    path = root.joinpath(*relative.parts)
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('package_path_unsafe')
    return path


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def rows(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 1000:
        raise ValueError('package_manifest_invalid')
    return [object_value(item) for item in value]


def verify(root: Path) -> dict[str, object]:
    path = root / 'manifest.json'
    if path.is_symlink() or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('package_manifest_invalid')
    manifest = object_value(json.loads(path.read_bytes()))
    if manifest.get('schema_version') != 1 or manifest.get('engine') != 'yue2':
        raise ValueError('package_manifest_invalid')
    request_json = text(manifest.get('request_json'), 10 * 1024 * 1024)
    if (hashlib.sha256(request_json.encode()).hexdigest() != manifest.get('request_sha256')
            or json.dumps(json.loads(request_json), sort_keys=True, allow_nan=False)
            != json.dumps(object_value(manifest.get('request')), sort_keys=True, allow_nan=False)):
        raise ValueError('package_request_integrity')
    checked: dict[str, dict[str, object]] = {}
    for entry in rows(manifest.get('files')):
        source = child(root, entry.get('path'))
        name = text(entry.get('path'), 10000)
        if name in checked or not source.is_file() or source.stat().st_size != entry.get('bytes') or digest(source) != entry.get('sha256'):
            raise ValueError('package_file_integrity')
        checked[name] = entry
    request = object_value(manifest.get('request'))
    if request.get('engine') != 'yue2':
        raise ValueError('package_manifest_invalid')
    sources = rows(manifest.get('sources'))
    samples = request.get('samples')
    if not isinstance(samples, list) or len(samples) != len(sources):
        raise ValueError('package_manifest_invalid')
    for sample_source in sources:
        checked_source = checked.get(text(sample_source.get('path'), 10000))
        if checked_source is None or checked_source.get('sha256') != sample_source.get('sha256') or checked_source.get('bytes') != sample_source.get('bytes'):
            raise ValueError('package_file_integrity')
        if request.get('yue_pair') is not None:
            for path_key, hash_key in (('original_path', 'original_sha256'), ('score_path', 'score_sha256')):
                checked_source = checked.get(text(sample_source.get(path_key), 10000))
                if checked_source is None or checked_source.get('sha256') != sample_source.get(hash_key):
                    raise ValueError('package_file_integrity')
    return manifest


def stop_tree(process: subprocess.Popen[str], job: WindowsJob | None) -> None:
    try:
        if job is not None:
            job.terminate()
        elif sys.platform != 'win32':
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    finally:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        if job is not None:
            deadline = time.monotonic() + 10
            while job.active_processes():
                if time.monotonic() >= deadline:
                    raise OSError('package_windows_job_not_drained')
                time.sleep(.05)
        elif sys.platform != 'win32':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def execute(argv: list[str], *, environment: dict[str, str] | None = None,
            capture: bool = False, check: bool = True, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    job = None
    try:
        if sys.platform == 'win32':
            from app.orchestrator.windows_job import WindowsJob
            from app.orchestrator.windows_supervisor import supervisor_command
            job = WindowsJob.create()
            argv = supervisor_command(job.name, argv)
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(argv, env=environment, stdin=subprocess.PIPE if job is not None else subprocess.DEVNULL,
                stdout=output if capture else None, text=True, start_new_session=sys.platform != 'win32')
            try:
                returncode = process.wait(timeout=timeout)
                if capture and output.tell() > 1024**2:
                    raise ValueError('package_command_output_too_large')
                output.seek(0)
                result = subprocess.CompletedProcess(argv, returncode, output.read().decode('utf-8') if capture else '')
                if check:
                    result.check_returncode()
                return result
            finally:
                try:
                    stop_tree(process, job)
                finally:
                    if process.stdin is not None:
                        process.stdin.close()
    finally:
        if job is not None:
            job.close()


def command(argv: list[str], *, environment: dict[str, str] | None = None) -> None:
    execute(argv, environment=environment)


def checkout(path: Path, repository: str, revision: str) -> None:
    if path.exists() and not (path / '.git').is_dir():
        raise ValueError('package_source_destination_exists')
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        command(['git', 'init', '-q', str(path)])
        command(['git', '-C', str(path), 'remote', 'add', 'origin', repository])
    actual = execute(['git', '-C', str(path), 'rev-parse', 'HEAD'], capture=True, check=False, timeout=20)
    if actual.returncode == 0:
        if actual.stdout.strip() != revision:
            raise ValueError('package_source_revision_mismatch')
        command(['git', '-C', str(path), 'diff-index', '--quiet', 'HEAD', '--'])
        return
    command(['git', '-C', str(path), 'fetch', '--depth=1', 'origin', revision])
    command(['git', '-C', str(path), 'checkout', '--detach', 'FETCH_HEAD'])


def python_path(root: Path) -> Path:
    return root / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')


def fetch_asset(runtime: Path, item: dict[str, object], interpreter: Path, environment: dict[str, str]) -> None:
    target = child(runtime, item.get('path'))
    expected = text(item.get('sha256'), 64)
    size = integer(item.get('bytes'), 1, 100 * 1024**3)
    if target.exists():
        if target.stat().st_size != size or digest(target) != expected:
            raise ValueError('package_existing_model_integrity')
        return
    url = urlsplit(text(item.get('url'), 10000))
    parts = url.path.strip('/').split('/')
    if url.scheme != 'https' or url.netloc != 'huggingface.co' or len(parts) < 5 or parts[2] != 'resolve':
        raise ValueError('package_model_url_invalid')
    repository, revision, filename = '/'.join(parts[:2]), parts[3], '/'.join(parts[4:])
    cache = runtime / '.downloads' / expected
    code = ('import json,sys; from huggingface_hub import hf_hub_download; '
        'print(json.dumps(hf_hub_download(sys.argv[1],filename=sys.argv[2],revision=sys.argv[3],local_dir=sys.argv[4],token=False)))')
    output = execute([str(interpreter), '-c', code, repository, filename, revision, str(cache)],
        capture=True, environment=environment, timeout=6 * 3600).stdout
    downloaded: object = json.loads(output)
    if not isinstance(downloaded, str):
        raise ValueError('package_download_invalid')
    source = Path(downloaded)
    if not source.is_file() or source.stat().st_size != size or digest(source) != expected:
        raise ValueError('package_model_integrity')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name('.' + target.name + '.' + uuid.uuid4().hex + '.part')
    try:
        with source.open('rb') as reader, temporary.open('xb') as writer:
            shutil.copyfileobj(reader, writer, 1024 * 1024)
            writer.flush();os.fsync(writer.fileno())
        if digest(temporary) != expected:
            raise ValueError('package_model_integrity')
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def artifact_identity(path: Path) -> dict[str, object]:
    with path.open('rb') as source:
        prefix = source.read(8)
        if len(prefix) != 8:
            raise ValueError('package_result_integrity')
        length = struct.unpack('<Q', prefix)[0]
        if not 2 <= length <= 4 * 1024**2 or length + 8 >= path.stat().st_size:
            raise ValueError('package_result_integrity')
        header = object_value(json.loads(source.read(length)))
    metadata = object_value(header.get('__metadata__'))
    return object_value(json.loads(text(metadata.get('openfabric_yue2_conversion'), 65536)))


def verify_result(run_dir: Path, manifest: dict[str, object]) -> list[Path]:
    completion_path = child(run_dir, 'completion.json')
    if not completion_path.is_file() or completion_path.stat().st_size > 65536:
        raise ValueError('package_result_integrity')
    completion = object_value(json.loads(completion_path.read_bytes()))
    request = object_value(manifest.get('request'))
    paired = request.get('yue_pair')
    if paired is None:
        artifact = child(run_dir, completion.get('checkpoint'))
        if (not artifact.is_file() or digest(artifact) != completion.get('sha256')
                or completion.get('request_sha256') != manifest.get('request_sha256')):
            raise ValueError('package_result_integrity')
        return [artifact]
    settings = object_value(paired)
    dataset_path = child(run_dir, 'dataset.json')
    bases = [item.get('sha256') for item in rows(manifest.get('assets'))
             if text(item.get('path'), 10000).startswith('training-assets/yue2/')]
    if (manifest.get('engine') != 'yue2' or completion.get('engine') != 'yue2'
            or completion.get('job_id') != run_dir.name
            or completion.get('request_sha256') != manifest.get('request_sha256')
            or integer(completion.get('max_steps'), 1, 100000) != request.get('max_steps')
            or integer(completion.get('planner_steps'), 1, 100000) != settings.get('planner_steps')
            or len(bases) != 1 or completion.get('base_sha256') != bases[0]
            or not dataset_path.is_file() or dataset_path.stat().st_size > 2 * 1024**2
            or digest(dataset_path) != completion.get('dataset_sha256')):
        raise ValueError('package_result_integrity')
    dataset = object_value(json.loads(dataset_path.read_bytes()))
    if dataset.get('request_sha256') != manifest.get('request_sha256'):
        raise ValueError('package_result_integrity')
    samples = rows(dataset.get('samples'))
    sources = rows(manifest.get('sources'))
    descriptions = rows(request.get('samples'))
    if len(samples) != len(sources) or len(samples) != len(descriptions):
        raise ValueError('package_result_integrity')
    for sample, source, description in zip(samples, sources, descriptions, strict=True):
        if (sample.get('source_sha256') != source.get('sha256') or sample.get('score_sha256') != source.get('score_sha256')
                or any(sample.get(key) != description.get(key) for key in ('caption', 'lyrics', 'instrumental'))):
            raise ValueError('package_result_integrity')
    artifacts = rows(completion.get('artifacts'))
    if len(artifacts) != 2 or {text(entry.get('target')) for entry in artifacts} != {'ar', 'nar'}:
        raise ValueError('package_result_integrity')
    outputs: list[Path] = []
    for entry in sorted(artifacts, key=lambda item: text(item.get('target'))):
        artifact = child(run_dir, entry.get('checkpoint'))
        if (artifact in outputs or artifact.suffix != '.safetensors' or not artifact.is_file()
                or not 8 < artifact.stat().st_size <= 512 * 1024**2 or digest(artifact) != entry.get('sha256')):
            raise ValueError('package_result_integrity')
        identity = artifact_identity(artifact)
        if identity.get('stage') != entry.get('target') or identity.get('base_sha256') != bases[0]:
            raise ValueError('package_result_integrity')
        outputs.append(artifact)
    return outputs


def run(root: Path, manifest: dict[str, object]) -> None:
    # main verifies every packaged file before importing any packaged module.
    from scripts.stable_lora_worker import atomic_json
    uv = shutil.which('uv')
    if uv is None or shutil.which('git') is None:
        raise ValueError('Install uv and Git before running this package.')
    engine = manifest['engine']
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg is None:
        raise ValueError('Install FFmpeg before running the YuE2 package.')
    assets = rows(manifest.get('assets'))
    total = sum(integer(item.get('bytes'), 1, 100 * 1024**3) for item in assets)
    print(f'CUDA execution is unverified. Reviewed model downloads: {total:,} bytes before cache reuse.', flush=True)
    for license_entry in rows(manifest.get('licenses')):
        print(text(license_entry.get('name')) + ': ' + text(license_entry.get('declared_license')) + ' — ' + text(license_entry.get('source_url')), flush=True)
    runtime = root / 'runtime'
    runtime.mkdir(exist_ok=True)
    if shutil.disk_usage(runtime).free < 2 * total + 2 * 1024**3:
        raise ValueError('Insufficient free space for verified downloads and model copies.')
    training = child(runtime, 'training/' + text(engine))
    training.mkdir(parents=True, exist_ok=True)
    for source in rows(manifest.get('sources_pins')):
        checkout(child(training, source.get('folder')), text(source.get('repository'), 10000), text(source.get('revision'), 40))
    interpreters: dict[str, Path] = {}
    for descriptor in rows(manifest.get('environments')):
        folder = text(descriptor.get('folder'), 100)
        venv = child(training, folder)
        interpreter = python_path(venv)
        if not interpreter.is_file():
            command([uv, 'venv', '--python', '3.12', str(venv)])
        command([uv, 'pip', 'install', '--python', str(interpreter), '-r', str(child(root, descriptor.get('requirements')))])
        command([uv, 'pip', 'check', '--python', str(interpreter)])
        interpreters[folder] = interpreter
    primary = interpreters['venv']
    environment = {**os.environ, 'OPENFABRIC_CONFIG': str(root / 'isolated-config.json'),
        'OPENFABRIC_DATA_DIR': str(root / 'isolated-data'), 'SEED_VC_DIR': str(root / 'unused-seed-vc'),
        'OPENFABRIC_MODULE_ROOT': str(runtime), 'HF_HUB_DISABLE_IMPLICIT_TOKEN': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
        'DO_NOT_TRACK': '1', 'PYTHONUTF8': '1'}
    cuda_check = ('import torch; '
        '\nif not torch.cuda.is_available(): raise RuntimeError("A working NVIDIA CUDA device is required.")'
        '\nif not torch.cuda.is_bf16_supported(): raise RuntimeError("This package requires CUDA BF16 support.")')
    command([str(primary), '-c', cuda_check], environment=environment)
    for item in assets:
        fetch_asset(runtime, item, primary, environment)
    run_dir = root / 'runs' / uuid.uuid4().hex
    run_dir.mkdir(parents=True)
    request = object_value(manifest.get('request'))
    options = {'python': str(primary), 'trainer_root': str(training / 'trainer'), 'comfy_root': str(training / 'comfy'),
        'base_checkpoint': str(runtime / 'training-assets/yue2/2f76ca75e6ee094169de899cc7fc99d6887e2196/yue2_3b_bf16.safetensors'),
        'ffmpeg': ffmpeg, 'device': 'cuda'}
    plan: dict[str, object] = {'request': request, 'request_sha256': text(manifest.get('request_sha256'), 64),
        'sources': [str(child(root, sample.get('path'))) for sample in rows(manifest.get('sources'))], 'runtime': options}
    if request.get('yue_pair') is not None:
        plan['prepared_scores'] = [{'path': str(child(root, sample.get('score_path'))), 'sha256': text(sample.get('score_sha256'), 64)}
                                   for sample in rows(manifest.get('sources'))]
    atomic_json(run_dir / 'request.json', plan)
    environment.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'})
    worker = root / 'backend/scripts/yue_lora_worker.py'
    command([str(primary), str(worker), '--request', str(run_dir / 'request.json')], environment=environment)
    artifacts = verify_result(run_dir, manifest)
    for artifact in artifacts:
        print(f'Training completed. Import {artifact} through OpenFabric Studio to validate and preview it.', flush=True)
    print('Optimizer state is not resumed; every invocation starts a fresh training run.', flush=True)


def main() -> None:
    signal.signal(signal.SIGTERM, cancel)
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='Verify package contents without network, installation or GPU work.')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest = verify(root)
    print('Package files verified.', flush=True)
    if not args.check:
        run(root, manifest)


def cancel(signum: int, frame: FrameType | None) -> NoReturn:
    raise SystemExit(128 + signum)


if __name__ == '__main__':
    main()
