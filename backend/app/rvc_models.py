"""Opaque, explicitly trusted local RVC registry. No torch import/deserialization.

The inspected upstream loader calls torch.load without an explicit safe mode.
Supervision is process ownership, not a security sandbox. Registration records
operator trust/evidence; neither compatibility nor rights are certified here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import shutil
from typing import IO
import uuid

from pydantic import TypeAdapter, ValidationError

from . import db
from .atomic_files import document_lock, write_object
from .contracts import JsonObject
from .replacement_contracts import (ReplacementErrorCode, RvcModelImport, RvcModelsResponse,
    RvcRegisteredModel, RvcRuntimeFile, RvcRuntimeIdentity)

MAX_MODEL_BYTES = 256 * 1024**2
MAX_AUX_BYTES = 2 * 1024**3
MAX_METADATA_BYTES = 65536
_json = TypeAdapter(JsonObject)
# Observed raw bytes at the reviewed upstream pin, not published checksums.
SOURCE_HASHES = {
    'infer/cli.py': '8e321cd606bfa1f4756685db897e90df2278e77d3e58869bc83d4735ff7b7387',
    'infer/vc/modules.py': '7b2e12f56c2b2462d7944d8eda470796ffd4aec50f784c3f6f5169133eaae89b',
    'infer/hubert.py': 'bbfba54d1d83eae3fb634b2909f8f6bf41e6ffe436200ec246867c5c93ebba26',
    'infer/vc/pipeline.py': 'df8ccd98ca7f81e38934a0af93cc63b4f79764dc47d932f05914c566e876522a',
    'configs/config.py': 'f304463184b55898aa256fadeafd50d640ed0f880dbae6f89ad54db358240043',
}


class RvcModelError(Exception):
    def __init__(self, code: ReplacementErrorCode, status: int = 409) -> None:
        self.code, self.status = code, status
        super().__init__(code)


def _root() -> Path:
    path = db.FILES_DIR / 'replacement_models' / 'rvc'
    if db.FILES_DIR.is_symlink() or path.is_symlink() or path.resolve() != path:
        raise RvcModelError('replacement_storage_failed', 503)
    return path


def model_root(identifier: str) -> Path:
    if re.fullmatch(r'[0-9a-f]{32}', identifier) is None:
        raise RvcModelError('replacement_model_changed')
    root = _root() / identifier
    if root.is_symlink() or root.resolve() != root:
        raise RvcModelError('replacement_storage_failed', 503)
    return root


def _hash(path: Path, *, maximum: int) -> str:
    if path.is_symlink() or path.resolve() != path or not path.is_file() or not 0 < path.stat().st_size <= maximum:
        raise RvcModelError('replacement_model_changed')
    before = path.stat()
    checksum, count = hashlib.sha256(), 0
    with path.open('rb') as stream:
        while block := stream.read(65536):
            count += len(block)
            if count > maximum:
                raise RvcModelError('replacement_model_changed')
            checksum.update(block)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise RvcModelError('replacement_model_changed')
    return checksum.hexdigest()


def observe_runtime() -> RvcRuntimeIdentity:
    from .optional_engines import OptionalEngineError, RVC_RUNTIME, _engine_python, require_installed
    try:
        root = require_installed('rvc')
        if root.is_symlink() or root.resolve() != root or not _engine_python('rvc').is_file():
            raise RvcModelError('replacement_engine_unavailable')
        environment_sha = _verified_environment(root)
        files: list[RvcRuntimeFile] = []
        for name, checksum in SOURCE_HASHES.items():
            actual = _hash(root / name, maximum=1024**2)
            if actual != checksum:
                raise RvcModelError('replacement_engine_incompatible')
            files.append(RvcRuntimeFile(role='engine_source', filename=name, sha256=actual))
        for name in ('config.json', 'preprocessor_config.json'):
            relative = 'assets/hubert_base/' + name
            files.append(RvcRuntimeFile(role='hubert_configuration', filename=relative,
                sha256=_hash(root / relative, maximum=1024**2)))
        candidates = [name for name in ('model.safetensors', 'pytorch_model.bin') if (root / 'assets/hubert_base' / name).is_file()]
        if len(candidates) != 1:
            raise RvcModelError('replacement_engine_unavailable')
        relative = 'assets/hubert_base/' + candidates[0]
        files.append(RvcRuntimeFile(role='hubert_weights', filename=relative,
            sha256=_hash(root / relative, maximum=MAX_AUX_BYTES)))
        relative = 'assets/rmvpe/rmvpe.pt'
        files.append(RvcRuntimeFile(role='rmvpe_weights', filename=relative,
            sha256=_hash(root / relative, maximum=MAX_AUX_BYTES)))
        configuration = hashlib.sha256(''.join(item.model_dump_json() for item in files).encode()).hexdigest()
        return RvcRuntimeIdentity(files=files, configuration_sha256=configuration,
            environment_sha256=environment_sha, runtime=RVC_RUNTIME)
    except OptionalEngineError as error:
        raise RvcModelError('replacement_engine_unavailable') from error
    except OSError as error:
        raise RvcModelError('replacement_engine_unavailable') from error


def _verified_environment(root: Path) -> str:
    from .module_catalog import configured_environment
    from .module_evidence import environment_fingerprint, environment_verified
    environment = configured_environment()
    fingerprint = environment_fingerprint(environment, 'rvc')
    if (environment.paths['rvc'].resolve() != root.resolve() or fingerprint is None
            or not environment_verified(environment, 'rvc')):
        raise RvcModelError('replacement_engine_unavailable')
    return fingerprint


def _entry(identifier: str, *, include_deleted: bool = False) -> RvcRegisteredModel:
    root = model_root(identifier)
    path = root / 'entry.json'
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_METADATA_BYTES or not include_deleted and (root / 'deleted.json').exists():
            raise RvcModelError('replacement_model_changed')
        model = RvcRegisteredModel.model_validate_json(path.read_bytes())
        if model.id != identifier:
            raise RvcModelError('replacement_model_changed')
        return model
    except (OSError, ValidationError) as error:
        raise RvcModelError('replacement_model_changed') from error


def copy_upload(stream: IO[bytes], destination: Path, budget: int) -> tuple[str, int]:
    digest, total = hashlib.sha256(), 0
    with destination.open('xb') as output:
        while block := stream.read(min(65536, budget - total + 1)):
            total += len(block)
            if total > budget:
                raise RvcModelError('upload_too_large', 413)
            digest.update(block)
            output.write(block)
        if not total:
            raise RvcModelError('replacement_model_untrusted', 422)
        output.flush()
        import os
        os.fsync(output.fileno())
    return digest.hexdigest(), total


def hash_upload(stream: IO[bytes], budget: int) -> tuple[str, int]:
    digest, total = hashlib.sha256(), 0
    stream.seek(0)
    try:
        while block := stream.read(min(65536, budget - total + 1)):
            total += len(block)
            if total > budget:
                raise RvcModelError('upload_too_large', 413)
            digest.update(block)
        if not total:
            raise RvcModelError('replacement_model_untrusted', 422)
        return digest.hexdigest(), total
    finally:
        stream.seek(0)


def import_model(request: RvcModelImport, checkpoint: IO[bytes], *, checkpoint_name: str,
                 index: IO[bytes] | None = None, index_name: str | None = None) -> RvcRegisteredModel:
    from .voice_build import voice_dir
    from .voice_authorization import authorize
    if Path(checkpoint_name).suffix.lower() != '.pth' or index is not None and (index_name is None or Path(index_name).suffix.lower() != '.index'):
        raise RvcModelError('replacement_model_untrusted', 422)
    if request.index_rate > 0 and index is None:
        raise RvcModelError('replacement_invalid_request', 422)
    checkpoint_sha, used = hash_upload(checkpoint, MAX_MODEL_BYTES)
    index_sha, index_bytes = hash_upload(index, MAX_MODEL_BYTES - used) if index is not None else (None, 0)
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    with document_lock(root / 'registry'):
        folders = [path for path in root.iterdir() if path.is_dir()]
        if len(folders) > 100:
            raise RvcModelError('replacement_storage_failed', 503)
        for path in folders:
            existing = _entry(path.name, include_deleted=True)
            metadata = RvcModelImport.model_validate(existing.model_dump(include=set(RvcModelImport.model_fields)))
            same = metadata == request and existing.checkpoint_sha256 == checkpoint_sha and existing.index_sha256 == index_sha
            if request.request_id is not None and existing.request_id == request.request_id or request.request_id is None and same:
                if not same:
                    raise RvcModelError('replacement_request_conflict')
                if (path / 'deleted.json').exists():
                    raise RvcModelError('replacement_model_changed')
                return readiness(existing)
        voice_dir(request.voice_id)  # a real profile; Seed-VC readiness is irrelevant
        authorize(request.voice_id, 'convert')
        if len(folders) >= 100:
            raise RvcModelError('replacement_storage_failed', 503)
        if shutil.disk_usage(root).free < used + index_bytes + 1024**3:
            raise RvcModelError('replacement_disk_full')
        identifier = uuid.uuid4().hex
        folder = model_root(identifier)
        folder.mkdir()
        try:
            copied_checkpoint = copy_upload(checkpoint, folder / 'model.pth', MAX_MODEL_BYTES)[0]
            copied_index = copy_upload(index, folder / 'added.index', MAX_MODEL_BYTES - used)[0] if index is not None else None
            if copied_checkpoint != checkpoint_sha or copied_index != index_sha:
                raise RvcModelError('replacement_model_changed')
            if any(item.sha256 is not None and item.sha256 not in {checkpoint_sha, index_sha}
                    for item in request.weights_license.identities):
                raise RvcModelError('replacement_evidence_required', 422)
            try:
                runtime = observe_runtime()
            except RvcModelError:
                runtime = None
            model = RvcRegisteredModel.model_validate({**request.model_dump(), 'id': identifier,
                'checkpoint_sha256': checkpoint_sha, 'index_sha256': index_sha, 'observed_runtime': runtime,
                'created_at': datetime.now(timezone.utc).isoformat()})
            if len(model.model_dump_json().encode()) > MAX_METADATA_BYTES:
                raise RvcModelError('replacement_invalid_request', 422)
            write_object(folder / 'entry.json', _json.validate_json(model.model_dump_json()))
            return readiness(model)
        except BaseException:
            # Only this unpublished, never-executed import directory is removed.
            shutil.rmtree(folder)
            raise


def readiness(model: RvcRegisteredModel) -> RvcRegisteredModel:
    reasons: list[ReplacementErrorCode] = []
    if model.observed_runtime is None:
        reasons.append('replacement_engine_unavailable')
    else:
        from .optional_engines import OptionalEngineError, require_installed
        try:
            engine = require_installed('rvc')
            if _verified_environment(engine) != model.observed_runtime.environment_sha256:
                raise RvcModelError('replacement_engine_incompatible')
            for item in model.observed_runtime.files:
                path = engine / item.filename
                if path.is_symlink() or path.resolve() != path or not path.is_file() or path.stat().st_size == 0:
                    raise RvcModelError('replacement_engine_unavailable')
                if item.role == 'engine_source' and _hash(path, maximum=1024**2) != item.sha256:
                    raise RvcModelError('replacement_engine_incompatible')
            root = model_root(model.id)
            for path in [root / 'model.pth', *([root / 'added.index'] if model.index_sha256 is not None else [])]:
                if path.is_symlink() or path.resolve() != path or not path.is_file() or not 0 < path.stat().st_size <= MAX_MODEL_BYTES:
                    raise RvcModelError('replacement_model_changed')
        except (RvcModelError, OptionalEngineError, OSError):
            reasons.append('replacement_engine_unavailable')
    runtime_ready = not reasons
    try:
        from .voice_build import voice_dir
        from .voice_authorization import authorize
        voice_dir(model.voice_id)
        authorize(model.voice_id, 'convert')
    except Exception:
        reasons.append('replacement_model_untrusted')
    # List is a cheap metadata view, not a full checkpoint/auxiliary hash scan.
    return model.model_copy(update={'ready': runtime_ready, 'blocked_reasons': reasons})


def list_models() -> RvcModelsResponse:
    root = _root()
    if not root.is_dir():
        return RvcModelsResponse()
    folders = sorted(path for path in root.iterdir() if path.is_dir())
    if len(folders) > 100:
        raise RvcModelError('replacement_storage_failed', 503)
    models: list[RvcRegisteredModel] = []
    for path in folders:
        if (path / 'deleted.json').exists():
            continue
        models.append(readiness(_entry(path.name)))
    return RvcModelsResponse(models=models)


@dataclass(frozen=True)
class ResolvedRvcModel:
    public: RvcRegisteredModel
    checkpoint: Path
    index: Path | None


def resolve(identifier: str) -> ResolvedRvcModel:
    model = _entry(identifier)
    folder = model_root(identifier)
    checkpoint, index = folder / 'model.pth', folder / 'added.index' if model.index_sha256 is not None else None
    if (_hash(checkpoint, maximum=MAX_MODEL_BYTES) != model.checkpoint_sha256
            or index is not None and _hash(index, maximum=MAX_MODEL_BYTES) != model.index_sha256):
        raise RvcModelError('replacement_model_changed')
    if model.observed_runtime is None:
        raise RvcModelError('replacement_engine_unavailable')
    if observe_runtime() != model.observed_runtime:
        raise RvcModelError('replacement_model_changed')
    current = readiness(model)
    if not current.ready or current.blocked_reasons:
        raise RvcModelError(current.blocked_reasons[0])
    return ResolvedRvcModel(current, checkpoint, index)


def get(identifier: str) -> RvcRegisteredModel:
    return readiness(_entry(identifier))


def delete(identifier: str) -> None:
    model = _entry(identifier)
    row = db.get_db().execute("SELECT 1 FROM replacement_plans WHERE deleted_at IS NULL AND json_extract(request_json,'$.target.registered_model_id')=? LIMIT 1", (identifier,)).fetchone()
    if row is not None:
        raise RvcModelError('replacement_model_in_use')
    # Keep immutable imported bytes/evidence for retained render manifests.
    write_object(model_root(model.id) / 'deleted.json', {'deleted_at': datetime.now(timezone.utc).isoformat()})
