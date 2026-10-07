"""Pinned, offline video inference with non-destructive vendor compatibility.

The inspected MLX release parses A2V tiling flags but omits their implementation.
Our import loader applies reviewed source substitutions in memory; engine files,
environments and weights are never changed by a render or readiness request.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import math
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import CodeType, ModuleType
from typing import Literal, Sequence

ENGINE_COMMIT = '1724ca673d59f023a8a95efee06e5d36d61c2765'
ENGINE_VERSION = '0.15.12'
LOCK_SHA256 = 'd98390eceface14a375b93ea27b6b77ae1cf35ac41e153a5eb873809cbc2725e'
SOURCE_HASHES = {
    'packages/ltx-pipelines-mlx/src/ltx_pipelines_mlx/cli.py': '05befd15f983b131a0163140787e163a25665278c8e249a9a56f34f0132e43ee',
    'packages/ltx-pipelines-mlx/src/ltx_pipelines_mlx/a2vid_two_stage.py': 'e99a99b4837b50cb7fdb4f17a5a9467e6f21f1e0800267ba73622c0eb46b9135',
    'packages/ltx-pipelines-mlx/src/ltx_pipelines_mlx/ti2vid_two_stages.py': 'b859d884de9115d05518568d494a60cc49265da9c7614fd0aa4007598c41c9ae',
    'packages/ltx-pipelines-mlx/src/ltx_pipelines_mlx/scheduler.py': '151e527aa222f7c2abcdd2533e39e822dfd1f33b9daf47b432c114c6cd7efa00',
    'packages/ltx-pipelines-mlx/src/ltx_pipelines_mlx/utils/_orchestration.py': 'aef99dc321c8abacc25d79db5dcb600a31f05cc4d5e3880ebdb7738fd9067437',
    'packages/ltx-core-mlx/src/ltx_core_mlx/components/modality_tiling.py': '9956af69e497ce0065bcb4453aeb15c9f7bc62b3c1d5f50032e02140b8c9c4b0',
}


class VideoEngineError(Exception):
    def __init__(self, code: str, detail: str = '') -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail or code)


def _source(engine_dir: Path, relative: str) -> str:
    root = engine_dir.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise VideoEngineError('engine_incompatible', f'Missing or escaped source: {relative}')
    contents = path.read_bytes()
    if hashlib.sha256(contents).hexdigest() != SOURCE_HASHES[relative]:
        raise VideoEngineError('engine_incompatible', f'Unreviewed source: {relative}')
    return contents.decode('utf-8')


def _replace_once(source: str, before: str, after: str) -> str:
    if source.count(before) != 1:
        raise VideoEngineError('engine_incompatible', 'Unsupported A2V signature')
    return source.replace(before, after, 1)


def patched_sources(engine_dir: Path) -> dict[str, str]:
    """Validate all compatibility dependencies before creating patched source."""
    validated = {relative: _source(engine_dir, relative) for relative in SOURCE_HASHES}
    prefix = 'packages/ltx-pipelines-mlx/src/ltx_pipelines_mlx/'
    return patch_a2v_sources(validated[prefix + 'cli.py'], validated[prefix + 'a2vid_two_stage.py'])


def patch_a2v_sources(cli: str, a2v: str) -> dict[str, str]:
    """Overlay reviewed A2V signatures after the caller validates exact source."""
    start = cli.index('def _cmd_a2v(')
    end = cli.index('\ndef _cmd_retake(', start)
    handler = cli[start:end]
    handler = _replace_once(handler,
        '        low_ram_streaming=getattr(args, "low_ram", False),\n',
        '        low_ram_streaming=getattr(args, "low_ram", False),\n        tile_count=_build_tile_count_config(args),\n')
    cli = cli[:start] + handler + cli[end:]
    a2v = _replace_once(a2v, '        x0_model = X0Model(self.dit)\n', '''        stage1_dit = self.dit
        if self._tile_count is not None:
            from ltx_core_mlx.components.modality_tiling import TiledLTXModel, VideoModalityTiler
            stage1_dit = TiledLTXModel(self.dit, VideoModalityTiler(self._tile_count, latent_shape=(F, H_half, W_half)))
        x0_model = X0Model(stage1_dit)
''')
    a2v = _replace_once(a2v, '        output_2 = denoise_loop(\n            model=x0_model,', '''        stage2_model = X0Model(self.dit)
        if self._tile_count is not None:
            from ltx_core_mlx.components.modality_tiling import TiledLTXModel, VideoModalityTiler
            stage2_model = X0Model(TiledLTXModel(self.dit, VideoModalityTiler(self._tile_count, latent_shape=(F, H_full, W_full))))
        output_2 = denoise_loop(
            model=stage2_model,''')
    result = {'ltx_pipelines_mlx.cli': cli, 'ltx_pipelines_mlx.a2vid_two_stage': a2v}
    for name, source in result.items():
        ast.parse(source, filename=name)
    return result


def ensure_compatibility(engine_dir: Path) -> None:
    """Read-only compatibility check; unknown or changed dependencies fail closed."""
    patched_sources(engine_dir)


class _PatchedLoader(importlib.machinery.SourceFileLoader):
    def __init__(self, name: str, path: str, source: str) -> None:
        super().__init__(name, path)
        self.source = source

    def get_code(self, fullname: str) -> CodeType:
        return compile(self.source, self.path, 'exec')


class _CompatibilityFinder(importlib.abc.MetaPathFinder):
    def __init__(self, engine_dir: Path, sources: dict[str, str]) -> None:
        self.engine_dir = engine_dir.resolve()
        self.sources = sources

    def find_spec(self, fullname: str, path: Sequence[str] | None = None,
                  target: ModuleType | None = None) -> importlib.machinery.ModuleSpec | None:
        source = self.sources.get(fullname)
        if source is None:
            return None
        relative = 'packages/ltx-pipelines-mlx/src/' + fullname.replace('.', '/') + '.py'
        loader = _PatchedLoader(fullname, str(self.engine_dir / relative), source)
        return importlib.util.spec_from_loader(fullname, loader)


def install_compatibility(engine_dir: Path) -> None:
    """Install patches before vendor import, exclusively inside the child process."""
    install_source_overlay(engine_dir, patched_sources(engine_dir))


def install_source_overlay(engine_dir: Path, sources: dict[str, str]) -> None:
    """Child-only installation; callers must supply hash-validated sources."""
    if any(name in sys.modules for name in sources):
        raise VideoEngineError('engine_incompatible', 'Vendor already imported before compatibility installation')
    sys.meta_path.insert(0, _CompatibilityFinder(engine_dir, sources))


ProfileId = Literal['ltx23', 'ltx25']
RenderMode = Literal['a2v', 'i2v', 't2v']


@dataclass(frozen=True)
class ImageReference:
    path: Path
    frame_index: int = 0
    strength: float = 1.0


@dataclass(frozen=True)
class RenderSettings:
    output: Path
    prompt: str
    frames: int
    source_audio: Path | None = None
    references: tuple[ImageReference, ...] = ()
    profile_id: ProfileId = 'ltx23'
    mode: RenderMode = 'a2v'
    width: int = 704
    height: int = 448
    frame_rate: int = 24
    seed: int = 42
    stage1_steps: int = 30
    stage2_steps: int = 3
    cfg_scale: float = 3.0
    negative_prompt: str | None = None
    temporal_tiles: int = 1
    spatial_tiles: int = 1
    # Local character LoRA. Songs (a2v) never receive it.
    adapter: Path | None = None
    adapter_strength: float = 1.0


def validate_settings(settings: RenderSettings) -> None:
    """Fail before loading weights; higher refinement requests are not real steps."""
    integers = (settings.frames, settings.width, settings.height, settings.frame_rate,
                settings.seed, settings.stage1_steps, settings.stage2_steps,
                settings.temporal_tiles, settings.spatial_tiles)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in integers):
        raise VideoEngineError('bad_settings')
    if (settings.profile_id not in ('ltx23', 'ltx25') or settings.mode not in ('a2v', 'i2v', 't2v')
            or not 1 <= len(settings.prompt.strip()) <= 4001
            or not 9 <= settings.frames <= 289 or (settings.frames - 1) % 8 != 0
            or not 64 <= settings.width <= 1920 or settings.width % 64
            or not 64 <= settings.height <= 1920 or settings.height % 64
            or settings.frame_rate != 24 or not 0 <= settings.seed <= 2**31 - 1
            or not 10 <= settings.stage1_steps <= 50 or not 1 <= settings.stage2_steps <= 3
            or isinstance(settings.cfg_scale, bool) or not math.isfinite(settings.cfg_scale)
            or not 1 <= settings.cfg_scale <= 8
            or not 1 <= settings.temporal_tiles <= 4 or not 1 <= settings.spatial_tiles <= 4
            or len(settings.references) > 6
            or settings.negative_prompt is not None and len(settings.negative_prompt) > 2000
            or (settings.adapter is not None and settings.mode == 'a2v')
            or (settings.adapter is not None and (
                isinstance(settings.adapter_strength, bool)
                or not isinstance(settings.adapter_strength, (int, float))
                or not math.isfinite(float(settings.adapter_strength))
                or not 0 < float(settings.adapter_strength) <= 2))):
        raise VideoEngineError('bad_settings')
    for reference in settings.references:
        if (isinstance(reference.frame_index, bool) or not isinstance(reference.frame_index, int)
                or not 0 <= reference.frame_index < settings.frames
                or isinstance(reference.strength, bool) or not math.isfinite(reference.strength)
                or not 0 <= reference.strength <= 1):
            raise VideoEngineError('bad_settings')


@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int
    sha256: str | None
    git_blob_sha1: str | None


@dataclass(frozen=True)
class ModelPack:
    repo_id: str
    revision: str
    files: tuple[ModelFile, ...]


@dataclass(frozen=True)
class RequiredArtifact:
    path: Path
    repo_id: str
    revision: str
    relative_path: str
    size: int
    sha256: str | None
    git_blob_sha1: str | None


@dataclass(frozen=True)
class EngineReadiness:
    profile_id: ProfileId
    ready: bool
    engine_ready: bool
    model_fingerprint: str
    model_revision: str
    text_revision: str
    expected_bytes: int
    uncached_bytes: int
    free_bytes: int
    missing_files: tuple[str, ...]
    warnings: tuple[str, ...]


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise VideoEngineError('engine_incompatible', 'Invalid tracked manifest')
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise VideoEngineError('engine_incompatible', 'Invalid manifest key')
        result[key] = item
    return result


def _string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise VideoEngineError('engine_incompatible', 'Invalid tracked manifest string')
    return value


def _digest(value: object, length: int) -> str | None:
    if value is None:
        return None
    text = _string(value)
    if re.fullmatch(f'[0-9a-f]{{{length}}}', text) is None:
        raise VideoEngineError('engine_incompatible', 'Invalid tracked manifest digest')
    return text


def model_packs() -> dict[str, ModelPack]:
    """Only reviewed, fixed revisions/files; no remote metadata call at runtime."""
    try:
        data: object = json.loads(Path(__file__).with_name('video_engine_manifest.json').read_text())
    except (OSError, ValueError, UnicodeError) as exc:
        raise VideoEngineError('engine_incompatible', 'Unreadable tracked manifest') from exc
    packs: dict[str, ModelPack] = {}
    records = _object(data)
    if set(records) != {'ltx23', 'ltx25', 'gemma3'}:
        raise VideoEngineError('engine_incompatible', 'Unexpected tracked packs')
    for key, value in records.items():
        record = _object(value)
        repo = _string(record.get('repo_id'))
        revision = _digest(record.get('revision'), 40)
        raw_files = record.get('files')
        if (revision is None or not isinstance(raw_files, list) or not raw_files
                or re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) is None):
            raise VideoEngineError('engine_incompatible', 'Invalid tracked manifest files')
        files: list[ModelFile] = []
        for raw in raw_files:
            item = _object(raw)
            name = _string(item.get('name'))
            size = item.get('size')
            if Path(name).name != name or name in ('.', '..') or isinstance(size, bool) or not isinstance(size, int) or size < 1:
                raise VideoEngineError('engine_incompatible', 'Invalid tracked artifact')
            sha256, blob = _digest(item.get('sha256'), 64), _digest(item.get('git_blob_sha1'), 40)
            if (sha256 is None) == (blob is None):
                raise VideoEngineError('engine_incompatible', 'Missing or ambiguous tracked digest')
            files.append(ModelFile(name, size, sha256, blob))
        if len({item.name for item in files}) != len(files):
            raise VideoEngineError('engine_incompatible', 'Duplicate tracked artifact')
        packs[key] = ModelPack(repo, revision, tuple(files))
    return packs


def _pack_dir(cache_dir: Path, pack: ModelPack) -> Path:
    return cache_dir / 'hub' / ('models--' + pack.repo_id.replace('/', '--')) / 'snapshots' / pack.revision


def local_snapshot(cache_dir: Path, key: str) -> Path:
    """Pinned local snapshot directory. This does not download weights."""
    packs = model_packs()
    if key not in packs:
        raise VideoEngineError('bad_settings')
    return _pack_dir(cache_dir, packs[key])


def required_artifacts(cache_dir: Path, profile_id: ProfileId = 'ltx23') -> tuple[RequiredArtifact, ...]:
    packs = model_packs()
    if profile_id not in ('ltx23', 'ltx25'):
        raise VideoEngineError('bad_settings')
    selected = [packs[profile_id]] + ([packs['gemma3']] if profile_id == 'ltx23' else [])
    return tuple(RequiredArtifact(_pack_dir(cache_dir, pack) / item.name, pack.repo_id,
        pack.revision, item.name, item.size, item.sha256, item.git_blob_sha1)
        for pack in selected for item in pack.files)


def _file_signature(path: Path) -> tuple[int, int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


FileSignature = tuple[int, int, int, int, int]
VerifiedArtifact = tuple[RequiredArtifact, FileSignature]


def _content_fingerprint(artifacts: Sequence[RequiredArtifact]) -> str:
    fingerprint = hashlib.sha256(ENGINE_COMMIT.encode())
    for artifact in artifacts:
        fingerprint.update(repr((artifact.repo_id, artifact.revision, artifact.relative_path,
                                 artifact.sha256 or artifact.git_blob_sha1)).encode())
    return fingerprint.hexdigest()


def _receipt_path(cache_dir: Path, profile_id: ProfileId) -> Path:
    if profile_id not in ('ltx23', 'ltx25'):
        raise VideoEngineError('bad_settings')
    root = cache_dir.resolve()
    path = root / 'video-engine' / f'{profile_id}-provenance.json'
    if not path.resolve().is_relative_to(root):
        raise VideoEngineError('model_integrity_failed')
    return path


def _receipt_record(profile_id: ProfileId, fingerprint: str,
                    verified: Sequence[VerifiedArtifact]) -> dict[str, object]:
    return {'schema_version': 1, 'engine_commit': ENGINE_COMMIT, 'engine_version': ENGINE_VERSION,
        'lock_sha256': LOCK_SHA256, 'profile_id': profile_id, 'content_fingerprint': fingerprint,
        'artifacts': [{'repo_id': item.repo_id, 'revision': item.revision, 'path': item.relative_path,
            'size': item.size, 'sha256': item.sha256, 'git_blob_sha1': item.git_blob_sha1,
            'file_signature': list(signature)} for item, signature in verified]}


def _record_verification(cache_dir: Path, profile_id: ProfileId, fingerprint: str,
                         verified: Sequence[VerifiedArtifact]) -> None:
    root = cache_dir.resolve()
    destination = _receipt_path(cache_dir, profile_id)
    # A replacement during verification of a later artifact invalidates the whole receipt.
    for item, signature in verified:
        path = item.path.resolve()
        if not path.is_relative_to(root) or _file_signature(path) != signature:
            raise VideoEngineError('model_integrity_failed')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent, delete=False) as stream:
        staging = Path(stream.name)
        try:
            json.dump(_receipt_record(profile_id, fingerprint, verified), stream)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            staging.unlink(missing_ok=True)
            raise
    try:
        staging.replace(destination)
    finally:
        staging.unlink(missing_ok=True)


def _validate_receipt_types(record: dict[str, object]) -> None:
    version = record.get('schema_version')
    rows = record.get('artifacts')
    if isinstance(version, bool) or not isinstance(version, int) or not isinstance(rows, list):
        raise VideoEngineError('model_integrity_failed')
    for raw in rows:
        item = _object(raw)
        size, signature = item.get('size'), item.get('file_signature')
        if (isinstance(size, bool) or not isinstance(size, int) or not isinstance(signature, list)
                or len(signature) != 5 or any(isinstance(value, bool) or not isinstance(value, int)
                                               or value < 0 for value in signature)):
            raise VideoEngineError('model_integrity_failed')


def verified_cached_fingerprint(cache_dir: Path, profile_id: ProfileId = 'ltx23') -> str | None:
    """Read-only bounded receipt check; metadata changes require a fresh content check.

    This is a local performance receipt, not an authenticated statement. It detects
    ordinary replacements/corruption after verification using pinned artifacts and
    inode/size/time identities; it does not defend against a user forging the receipt.
    """
    try:
        receipt = _receipt_path(cache_dir, profile_id)
        if not receipt.is_file() or receipt.stat().st_size > 128 * 1024:
            return None
        with receipt.open('rb') as stream:
            contents = stream.read(128 * 1024 + 1)
        if len(contents) > 128 * 1024:
            return None
        raw: object = json.loads(contents)
        record = _object(raw)
        _validate_receipt_types(record)
        root = cache_dir.resolve()
        artifacts = required_artifacts(cache_dir, profile_id)
        verified: list[VerifiedArtifact] = []
        for item in artifacts:
            path = item.path.resolve()
            if not path.is_relative_to(root) or not path.is_file():
                return None
            signature = _file_signature(path)
            if signature[2] != item.size:
                return None
            verified.append((item, signature))
        fingerprint = _content_fingerprint(artifacts)
        return fingerprint if record == _receipt_record(profile_id, fingerprint, verified) else None
    except (OSError, ValueError, UnicodeError, VideoEngineError):
        return None


def _verify_artifacts(cache_dir: Path, profile_id: ProfileId, record: bool) -> str:
    """Explicit, streaming content check against pinned HF LFS/git digests.

    This reads tens of GB and belongs in setup/background admission, never an
    HTTP readiness check. The returned fingerprint identifies verified content.
    Only an explicit record request creates an atomic verification receipt.
    """
    root = cache_dir.resolve()
    artifacts = required_artifacts(cache_dir, profile_id)
    verified: list[VerifiedArtifact] = []
    for artifact in artifacts:
        try:
            path = artifact.path.resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise VideoEngineError('model_integrity_failed')
            before = _file_signature(path)
            if before[2] != artifact.size:
                raise VideoEngineError('model_integrity_failed')
            # HF uses SHA256 for LFS payloads and git's prefixed SHA1 for small files.
            digest = hashlib.sha256() if artifact.sha256 else hashlib.sha1()
            if artifact.git_blob_sha1:
                digest.update(f'blob {artifact.size}\0'.encode())
            with path.open('rb') as stream:
                while chunk := stream.read(4 * 1024**2):
                    digest.update(chunk)
            actual = digest.hexdigest()
            if actual != (artifact.sha256 or artifact.git_blob_sha1) or _file_signature(path) != before:
                raise VideoEngineError('model_integrity_failed')
            verified.append((artifact, before))
        except OSError as exc:
            raise VideoEngineError('model_integrity_failed') from exc
    fingerprint = _content_fingerprint(artifacts)
    if record:
        try:
            _record_verification(cache_dir, profile_id, fingerprint, verified)
        except OSError as exc:
            raise VideoEngineError('model_integrity_failed') from exc
    return fingerprint


def verify_artifacts(cache_dir: Path, profile_id: ProfileId = 'ltx23') -> str:
    """Stream pinned content verification without writing any cache metadata."""
    return _verify_artifacts(cache_dir, profile_id, record=False)


def verify_and_record_artifacts(cache_dir: Path, profile_id: ProfileId = 'ltx23') -> str:
    """Explicit setup/background verification with an atomic stat-identity receipt."""
    return _verify_artifacts(cache_dir, profile_id, record=True)


def _cached_blob(item: RequiredArtifact, cache_root: Path) -> bool:
    """An existing HF blob need not be downloaded again to recreate a snapshot."""
    digest = item.sha256 or item.git_blob_sha1
    blob = cache_root / 'hub' / ('models--' + item.repo_id.replace('/', '--')) / 'blobs' / str(digest)
    try:
        path = blob.resolve()
        return path.is_relative_to(cache_root) and path.is_file() and path.stat().st_size == item.size
    except OSError:
        return False


def inspect_readiness(engine_dir: Path, cache_dir: Path, profile_id: ProfileId = 'ltx23') -> EngineReadiness:
    """Bounded offline stat checks, not expensive content verification of weights."""
    warnings = ['visual_quality_unverified', 'model_fingerprint_uses_file_metadata']
    try:
        ensure_compatibility(engine_dir)
        engine_ready = (engine_dir / '.venv/bin/python').is_file()
    except (VideoEngineError, OSError, UnicodeError):
        engine_ready = False
        warnings.append('engine_incompatible')
    artifacts = required_artifacts(cache_dir, profile_id)
    missing: list[str] = []
    uncached = 0
    fingerprint = hashlib.sha256()
    fingerprint.update(ENGINE_COMMIT.encode())
    cache_root = cache_dir.resolve()
    for item in artifacts:
        signature: tuple[int, int, int, int, int] | None = None
        try:
            path = item.path.resolve()
            if path.is_relative_to(cache_root) and path.is_file():
                signature = _file_signature(path)
        except OSError:
            pass
        if signature is None or signature[2] != item.size:
            missing.append(f'{item.repo_id}/{item.relative_path}')
            if not _cached_blob(item, cache_root):
                uncached += item.size
        fingerprint.update(repr((item.repo_id, item.revision, item.relative_path,
                                 item.sha256, item.git_blob_sha1, signature)).encode())
    ancestor = cache_root
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    free = shutil.disk_usage(ancestor).free
    if uncached and free < uncached + 2 * 1024**3:
        warnings.append('insufficient_disk_space')
    if profile_id == 'ltx25':
        warnings.append('ltx25_comparison_opt_in')
    packs = model_packs()
    return EngineReadiness(profile_id, engine_ready and not missing, engine_ready,
        fingerprint.hexdigest(), packs[profile_id].revision,
        packs['gemma3'].revision if profile_id == 'ltx23' else packs[profile_id].revision,
        sum(item.size for item in artifacts), uncached, free, tuple(missing), tuple(warnings))


def render_argv(engine_dir: Path, cache_dir: Path, settings: RenderSettings) -> list[str]:
    validate_settings(settings)
    if settings.mode == 'a2v' and (settings.source_audio is None or not settings.source_audio.is_file()):
        raise VideoEngineError('source_missing')
    if settings.mode == 'i2v' and not settings.references:
        raise VideoEngineError('reference_missing')
    if any(not reference.path.is_file() for reference in settings.references):
        raise VideoEngineError('reference_missing')
    ready = inspect_readiness(engine_dir, cache_dir, settings.profile_id)
    if not ready.engine_ready:
        raise VideoEngineError('engine_incompatible')
    if not ready.ready:
        raise VideoEngineError('model_not_installed')
    packs = model_packs()
    model_dir = _pack_dir(cache_dir, packs[settings.profile_id]).resolve()
    text_dir = _pack_dir(cache_dir, packs['gemma3']).resolve() if settings.profile_id == 'ltx23' else model_dir
    runner = Path(__file__).resolve().parents[1] / 'scripts/run_video.py'
    argv = [str(engine_dir / '.venv/bin/python'), str(runner), '--engine-dir', str(engine_dir.resolve()), '--',
            'a2v' if settings.mode == 'a2v' else 'generate', '--prompt', settings.prompt,
            '--output', str(settings.output), '--model', str(model_dir), '--gemma', str(text_dir),
            '--frames', str(settings.frames), '--frame-rate', str(settings.frame_rate),
            '--width', str(settings.width), '--height', str(settings.height), '--seed', str(settings.seed),
            '--stage1-steps', str(settings.stage1_steps), '--stage2-steps', str(settings.stage2_steps),
            '--cfg-scale', str(settings.cfg_scale), '--low-ram', '--tile-frames', str(settings.temporal_tiles),
            '--tile-spatial', str(settings.spatial_tiles)]
    if settings.mode == 'a2v' and settings.source_audio is not None:
        argv.extend(['--audio', str(settings.source_audio), '--audio-start', '0'])
    else:
        argv.extend(['--two-stage', '--no-audio'])
    if settings.negative_prompt is not None:
        argv.extend(['--negative-prompt', settings.negative_prompt])
    for reference in settings.references:
        argv.extend(['--image', str(reference.path), str(reference.frame_index), str(reference.strength)])
    if settings.adapter is not None:
        if settings.mode == 'a2v':
            raise VideoEngineError('bad_settings')
        adapter = settings.adapter.resolve()
        if not adapter.is_file() or adapter.suffix != '.safetensors' or adapter.stat().st_size < 1024:
            raise VideoEngineError('adapter_missing')
        argv.extend(['--lora', str(adapter), str(float(settings.adapter_strength))])
    return argv
