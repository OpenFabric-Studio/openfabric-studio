"""Standalone CUDA preparation and fresh training for an exported local package."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import uuid
from urllib.parse import urlsplit

_ROOT = Path(__file__).resolve().parent
_BACKEND = _ROOT / 'backend' if (_ROOT / 'backend').is_dir() else _ROOT.parent
sys.path.insert(0, str(_BACKEND))


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
    if manifest.get('schema_version') != 1 or manifest.get('engine') not in ('heartmula', 'yue2'):
        raise ValueError('package_manifest_invalid')
    request_json = text(manifest.get('request_json'), 10 * 1024 * 1024)
    if (hashlib.sha256(request_json.encode()).hexdigest() != manifest.get('request_sha256')
            or json.dumps(json.loads(request_json), sort_keys=True, allow_nan=False)
            != json.dumps(object_value(manifest.get('request')), sort_keys=True, allow_nan=False)):
        raise ValueError('package_request_integrity')
    for entry in rows(manifest.get('files')):
        source = child(root, entry.get('path'))
        if not source.is_file() or source.stat().st_size != entry.get('bytes') or digest(source) != entry.get('sha256'):
            raise ValueError('package_file_integrity')
    return manifest


def command(argv: list[str], *, environment: dict[str, str] | None = None) -> None:
    subprocess.run(argv, check=True, env=environment)


def checkout(path: Path, repository: str, revision: str) -> None:
    if path.exists() and not (path / '.git').is_dir():
        raise ValueError('package_source_destination_exists')
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        command(['git', 'init', '-q', str(path)])
        command(['git', '-C', str(path), 'remote', 'add', 'origin', repository])
    actual = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], capture_output=True, text=True)
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
    output = subprocess.check_output([str(interpreter), '-c', code, repository, filename, revision, str(cache)], text=True, env=environment)
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


def run(root: Path, manifest: dict[str, object]) -> None:
    # main verifies every packaged file before importing any packaged module.
    from scripts.stable_lora_worker import atomic_json
    uv = shutil.which('uv')
    if uv is None or shutil.which('git') is None:
        raise ValueError('Install uv and Git before running this package.')
    engine = manifest['engine']
    ffmpeg = shutil.which('ffmpeg')
    if engine == 'yue2' and ffmpeg is None:
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
    primary = interpreters['trainer-venv' if engine == 'heartmula' else 'venv']
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
    if engine == 'heartmula':
        options = {'simpletuner_root': str(training / 'simpletuner'), 'heartlib_root': str(training / 'heartlib'),
            'codec_python': str(interpreters['codec-venv']), 'base_path': str(training / 'models/ckpt/HeartMuLa-oss-3B'),
            'decoder_path': str(training / 'models/ckpt/HeartCodec-oss-20260123'), 'encoder_path': str(training / 'encoder'),
            'tokenizer_path': str(training / 'models/ckpt/tokenizer.json'), 'device': 'cuda'}
    else:
        if ffmpeg is None:
            raise ValueError('FFmpeg is required.')
        options = {'python': str(primary), 'trainer_root': str(training / 'trainer'), 'comfy_root': str(training / 'comfy'),
            'base_checkpoint': str(runtime / 'training-assets/yue2/2f76ca75e6ee094169de899cc7fc99d6887e2196/yue2_3b_bf16.safetensors'),
            'ffmpeg': ffmpeg, 'device': 'cuda'}
    plan = {'request': request, 'request_sha256': text(manifest.get('request_sha256'), 64),
        'sources': [str(child(root, sample.get('path'))) for sample in rows(manifest.get('sources'))], 'runtime': options}
    atomic_json(run_dir / 'request.json', plan)
    environment.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'})
    worker = root / 'backend/scripts' / ('heart_lora_worker.py' if engine == 'heartmula' else 'yue_lora_worker.py')
    command([str(primary), str(worker), '--request', str(run_dir / 'request.json')], environment=environment)
    completion = object_value(json.loads((run_dir / 'completion.json').read_bytes()))
    artifact = child(run_dir, completion.get('checkpoint'))
    if not artifact.is_file() or digest(artifact) != completion.get('sha256') or completion.get('request_sha256') != manifest.get('request_sha256'):
        raise ValueError('package_result_integrity')
    print(f'Training completed. Import {artifact} through OpenFabric Studio to validate and preview it.', flush=True)
    print('Optimizer state is not resumed; every invocation starts a fresh training run.', flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='Verify package contents without network, installation or GPU work.')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest = verify(root)
    print('Package files verified.', flush=True)
    if not args.check:
        run(root, manifest)


if __name__ == '__main__':
    main()
