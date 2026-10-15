"""Recoverable dataset catalog; native operations never rebuild saved annotations."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import uuid
import zipfile

import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator

from .atomic_files import document_lock, read_object, write_object
from .config import ACE_STEP_DIR, DATA_DIR, MODELS
from .contracts import JsonObject, JsonValue
from .lora_dataset_contracts import (
    CreateLoraDatasetRequest, RenameLoraDatasetRequest, LoraDatasetRevision,
    LoraDatasetEntry, LoraDatasetIssue, LoraDatasetsResponse, LoraWorkspace,
    LoraDatasetSample, LoraDatasetOperationResponse, ScanLoraDatasetRequest,
    SaveLoraDatasetRequest, LoraPreprocessReceipt, LoraObservedTask, LoraDatasetMetadata,
    PreprocessLoraDatasetRequest, LoraDatasetTaskStarted, AutoLabelLoraDatasetRequest, UpdateLoraDatasetSampleRequest,
)
from .job_lifecycle import run_owned_thread
from .orchestrator.manager import manager
from .orchestrator.state import ModelStatus

logger = logging.getLogger(__name__)
native_lock = asyncio.Lock()
_MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
_MAX_DATASETS = 10000


class LoraDatasetError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        self.code, self.status = code, status
        super().__init__(code)


class _SourceSignature(BaseModel):
    path: str = Field(max_length=4096)
    size: int = Field(ge=0)
    mtime_ns: int


SourceSignatures = dict[str, _SourceSignature | None]


class _Catalog(BaseModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    datasets: list[LoraDatasetEntry] = Field(default_factory=list, max_length=_MAX_DATASETS)
    workspace: LoraWorkspace = Field(default_factory=LoraWorkspace)
    dataset_hashes: dict[str, str] = Field(default_factory=dict)
    source_signatures: dict[str, SourceSignatures] = Field(default_factory=dict)


class _Metadata(BaseModel):
    name: str = Field(default='Untitled dataset', min_length=1, max_length=80)


class _DatasetDocument(BaseModel):
    metadata: _Metadata = Field(default_factory=_Metadata)
    samples: list[LoraDatasetSample] = Field(max_length=100000)

    @model_validator(mode='after')
    def unique_sample_ids(self) -> _DatasetDocument:
        ids = [sample.id for sample in self.samples]
        if len(ids) != len(set(ids)):
            raise ValueError('dataset_sample_ids_duplicate')
        if any(not sample.audio_path for sample in self.samples):
            raise ValueError('dataset_audio_path_missing')
        return self


def _catalog_path() -> Path:
    root = DATA_DIR.resolve()
    folder = root / 'lora-datasets'
    if not folder.resolve().is_relative_to(root):
        raise LoraDatasetError('dataset_path_unsafe')
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'catalog.json'
    if path.is_symlink():
        raise LoraDatasetError('dataset_path_unsafe')
    return path


def _read_catalog(path: Path) -> _Catalog:
    if not path.exists():
        return _Catalog()
    if path.stat().st_size > _MAX_DOCUMENT_BYTES:
        raise LoraDatasetError('dataset_catalog_invalid', 503)
    return _Catalog.model_validate(read_object(path))


def _write_catalog(path: Path, catalog: _Catalog) -> None:
    if len(catalog.datasets) > _MAX_DATASETS:
        raise LoraDatasetError('dataset_catalog_full')
    validated = _Catalog.model_validate_json(catalog.model_dump_json())
    payload = _json_object(validated)
    if len(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')) > _MAX_DOCUMENT_BYTES:
        raise LoraDatasetError('dataset_catalog_full')
    write_object(path, payload)


def _json_object(model: BaseModel) -> JsonObject:
    # The atomic helper validates finite JSON, including the complete nested model.
    from pydantic import TypeAdapter
    from .contracts import JsonValue
    value: JsonValue = TypeAdapter(JsonValue).validate_json(model.model_dump_json())
    if not isinstance(value, dict):
        raise LoraDatasetError('dataset_invalid', 422)
    return value


def datasets_root() -> Path:
    engine = ACE_STEP_DIR.resolve()
    root = (engine / 'datasets').resolve()
    if not root.is_relative_to(engine):
        raise LoraDatasetError('dataset_path_unsafe')
    return root


def confined_path(relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or '\\' in relative or '\x00' in relative:
        raise LoraDatasetError('dataset_path_unsafe', 422)
    result = (ACE_STEP_DIR.resolve() / path).resolve()
    if not result.is_relative_to(datasets_root()):
        raise LoraDatasetError('dataset_path_unsafe', 422)
    return result


def _relative(path: Path) -> str:
    return path.relative_to(ACE_STEP_DIR.resolve()).as_posix()


def _read_dataset(path: Path) -> tuple[JsonObject, _DatasetDocument]:
    if path.stat().st_size > _MAX_DOCUMENT_BYTES:
        raise LoraDatasetError('dataset_invalid', 422)
    raw = read_object(path)
    return raw, _DatasetDocument.model_validate(raw)


def _discovery_paths(root: Path) -> Iterator[Path]:
    if not root.exists():
        return
    for directory, children, files in os.walk(root, followlinks=False):
        children[:] = [name for name in children if not name.startswith('.')]
        for name in files:
            if name.endswith('.json') and not name.startswith('.'):
                yield Path(directory) / name
        for name in children:
            child = Path(directory) / name
            if child.is_symlink() and not child.resolve().is_relative_to(root):
                yield child


def _content_hash(raw: JsonObject) -> str:
    return hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _refresh(catalog: _Catalog) -> list[LoraDatasetIssue]:
    root = datasets_root()
    known = {entry.dataset_path: entry for entry in catalog.datasets}
    issues: list[LoraDatasetIssue] = []
    for path in _discovery_paths(root):
        relative = _relative(path)
        if relative in known:
            continue
        try:
            safe = confined_path(relative)
            if not safe.is_file():
                continue
            raw, document = _read_dataset(safe)
            signatures = _source_signatures(raw)
        except LoraDatasetError as exc:
            issues.append(LoraDatasetIssue(path=relative, code='dataset_path_unsafe' if exc.code == 'dataset_path_unsafe' else 'dataset_invalid'))
            continue
        except (OSError, ValueError, ValidationError):
            issues.append(LoraDatasetIssue(path=relative, code='dataset_invalid'))
            continue
        if len(catalog.datasets) >= _MAX_DATASETS:
            issues.append(LoraDatasetIssue(path=relative, code='dataset_unavailable'))
            break
        entry = LoraDatasetEntry(id=hashlib.sha256(relative.encode()).hexdigest()[:32],
            name=document.metadata.name, dataset_path=relative, audio_dir=_relative(safe.parent),
            sample_count=len(document.samples), labeled_count=sum(sample.labeled for sample in document.samples), revision=0)
        catalog.datasets.append(entry)
        catalog.source_signatures[entry.id] = signatures
        known[relative] = entry
    for entry in catalog.datasets:
        try:
            raw, document = _read_dataset(confined_path(entry.dataset_path))
            fingerprint = _content_hash(raw)
            previous = catalog.dataset_hashes.get(entry.id)
            if previous is not None and fingerprint != previous:
                entry.revision += 1
            catalog.dataset_hashes[entry.id] = fingerprint
            entry.sample_count = len(document.samples)
            entry.labeled_count = sum(sample.labeled for sample in document.samples)
            entry.available, entry.error_code = True, ''
        except (OSError, ValueError, LoraDatasetError):
            entry.available, entry.error_code = False, 'dataset_unavailable'
    return issues[:10000]


def list_datasets() -> LoraDatasetsResponse:
    path = _catalog_path()
    with document_lock(path):
        catalog = _read_catalog(path)
        before = catalog.model_dump_json()
        issues = _refresh(catalog)
        if catalog.model_dump_json() != before:
            _write_catalog(path, catalog)
        return LoraDatasetsResponse(datasets=catalog.datasets, issues=issues)


def _entry(catalog: _Catalog, identifier: str, revision: int | None = None, *, allow_trash: bool = False) -> LoraDatasetEntry:
    if not re.fullmatch(r'[0-9a-f]{32}', identifier):
        raise LoraDatasetError('dataset_id_invalid', 422)
    entry = next((entry for entry in catalog.datasets if entry.id == identifier), None)
    if entry is None:
        raise LoraDatasetError('dataset_not_found', 404)
    if revision is not None and entry.revision != revision:
        raise LoraDatasetError('dataset_revision_conflict')
    if entry.trashed and not allow_trash:
        raise LoraDatasetError('dataset_trashed')
    confined_path(entry.dataset_path)
    confined_path(entry.audio_dir)
    return entry


def get_dataset(identifier: str, revision: int | None = None) -> LoraDatasetEntry:
    list_datasets()
    path = _catalog_path()
    with document_lock(path):
        return _entry(_read_catalog(path), identifier, revision)


def _unique_name(catalog: _Catalog, name: str, identifier: str = '') -> None:
    if any(entry.id != identifier and not entry.trashed and entry.name.casefold() == name.casefold() for entry in catalog.datasets):
        raise LoraDatasetError('dataset_name_conflict')


def create_dataset(body: CreateLoraDatasetRequest) -> LoraDatasetEntry:
    list_datasets()
    path = _catalog_path()
    with document_lock(path):
        catalog = _read_catalog(path)
        _unique_name(catalog, body.name)
        if len(catalog.datasets) >= _MAX_DATASETS:
            raise LoraDatasetError('dataset_catalog_full')
        identifier = uuid.uuid4().hex
        audio = confined_path(f'datasets/{identifier}')
        audio.mkdir(parents=True)
        document = audio / 'dataset.json'
        write_object(document, {'metadata': {'name': body.name}, 'samples': []})
        entry = LoraDatasetEntry(id=identifier, name=body.name, dataset_path=_relative(document),
            audio_dir=_relative(audio), sample_count=0, labeled_count=0, revision=0)
        catalog.datasets.append(entry)
        catalog.dataset_hashes[identifier] = _content_hash(read_object(document))
        catalog.source_signatures[identifier] = {}
        _write_catalog(path, catalog)
        return entry


def rename_dataset(identifier: str, body: RenameLoraDatasetRequest) -> LoraDatasetEntry:
    list_datasets()
    path = _catalog_path()
    with document_lock(path):
        catalog = _read_catalog(path)
        entry = _entry(catalog, identifier, body.revision)
        _unique_name(catalog, body.name, identifier)
        entry.name, entry.revision = body.name, entry.revision + 1
        _write_catalog(path, catalog)
        return entry


def _trash(identifier: str, body: LoraDatasetRevision, trashed: bool) -> LoraDatasetEntry:
    list_datasets()
    path = _catalog_path()
    with document_lock(path):
        catalog = _read_catalog(path)
        entry = _entry(catalog, identifier, body.revision, allow_trash=True)
        if not trashed:
            _unique_name(catalog, entry.name, identifier)
        entry.trashed, entry.revision = trashed, entry.revision + 1
        if trashed and catalog.workspace.selected_dataset_id == identifier:
            catalog.workspace.selected_dataset_id = None
            catalog.workspace.revision += 1
        _write_catalog(path, catalog)
        return entry


def trash_dataset(identifier: str, body: LoraDatasetRevision) -> LoraDatasetEntry:
    return _trash(identifier, body, True)


def restore_dataset(identifier: str, body: LoraDatasetRevision) -> LoraDatasetEntry:
    return _trash(identifier, body, False)


def get_workspace() -> LoraWorkspace:
    path = _catalog_path()
    with document_lock(path):
        return _read_catalog(path).workspace


def save_workspace(body: LoraWorkspace) -> LoraWorkspace:
    path = _catalog_path()
    with document_lock(path):
        catalog = _read_catalog(path)
        if body.revision != catalog.workspace.revision:
            raise LoraDatasetError('workspace_revision_conflict')
        if body.selected_dataset_id:
            _entry(catalog, body.selected_dataset_id)
        # Receipts attest to backend verification; browser state cannot mint one.
        body = body.model_copy(update={'revision': body.revision + 1, 'preprocess_receipt': catalog.workspace.preprocess_receipt})
        catalog.workspace = body
        _write_catalog(path, catalog)
        return body


def _samples(raw: JsonObject) -> list[JsonObject]:
    value = raw.get('samples', [])
    if not isinstance(value, list) or any(not isinstance(sample, dict) for sample in value):
        raise LoraDatasetError('dataset_invalid', 422)
    return [sample for sample in value if isinstance(sample, dict)]


def _source(sample: JsonObject) -> str:
    value = sample.get('audio_path')
    if not isinstance(value, str) or not value or '\x00' in value:
        raise LoraDatasetError('dataset_invalid', 422)
    path = Path(value)
    try:
        return str((path if path.is_absolute() else ACE_STEP_DIR / path).resolve())
    except (OSError, ValueError, RuntimeError) as exc:
        raise LoraDatasetError('dataset_invalid', 422) from exc


def _source_signatures(raw: JsonObject) -> SourceSignatures:
    signatures: SourceSignatures = {}
    for sample in _samples(raw):
        identifier = sample.get('id')
        if not isinstance(identifier, str):
            raise LoraDatasetError('dataset_invalid', 422)
        source = Path(_source(sample))
        try:
            stat = source.stat()
            signatures[identifier] = _SourceSignature(path=str(source), size=stat.st_size, mtime_ns=stat.st_mtime_ns)
        except OSError:
            signatures[identifier] = None
    return signatures


def _stored_source_signatures(identifier: str) -> SourceSignatures:
    path = _catalog_path()
    with document_lock(path):
        return dict(_read_catalog(path).source_signatures.get(identifier, {}))


def _changed_sources(previous: SourceSignatures, current: SourceSignatures) -> set[str]:
    return {identifier for identifier, signature in current.items() if identifier in previous and previous[identifier] != signature}


def _require_sources_unchanged(identifier: str, raw: JsonObject) -> None:
    if _changed_sources(_stored_source_signatures(identifier), _source_signatures(raw)):
        raise LoraDatasetError('dataset_sources_changed')


def merge_scan(original: JsonObject, scanned: JsonObject, changed_ids: set[str] | None = None) -> JsonObject:
    """Existing source identities retain IDs, edits, labels, lyrics and missing audio."""
    previous = [dict(sample) for sample in _samples(original)]
    fresh = {_source(sample): sample for sample in _samples(scanned)}
    for sample in previous:
        if changed_ids and sample.get('id') in changed_ids:
            sample['labeled'] = False
            replacement = fresh.get(_source(sample), {})
            for key in ('audio_path', 'filename', 'duration'):
                if key in replacement:
                    sample[key] = replacement[key]
    known = {_source(sample): sample for sample in previous}
    merged = list(previous)
    for sample in _samples(scanned):
        if _source(sample) not in known:
            merged.append(sample)
            known[_source(sample)] = sample
    result = dict(original)
    result['samples'] = list(merged)
    return result


def _publish(identifier: str, revision: int, raw: JsonObject, audio_dir: str | None = None, source_signatures: SourceSignatures | None = None) -> LoraDatasetEntry:
    document = _DatasetDocument.model_validate(raw)
    path = _catalog_path()
    with document_lock(path):
        catalog = _read_catalog(path)
        entry = _entry(catalog, identifier, revision)
        write_object(confined_path(entry.dataset_path), raw)
        entry.sample_count = len(document.samples)
        entry.labeled_count = sum(sample.labeled for sample in document.samples)
        entry.revision += 1
        catalog.dataset_hashes[identifier] = _content_hash(raw)
        if source_signatures is not None:
            catalog.source_signatures.setdefault(identifier, {}).update(source_signatures)
        if audio_dir is not None:
            entry.audio_dir = audio_dir
        _write_catalog(path, catalog)
        return entry


async def _native(method: str, endpoint: str, body: JsonObject | None = None) -> JsonObject:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(method, f'{MODELS["ace_step"].proxy_target}/{endpoint}', json=body)
        response.raise_for_status()
        from pydantic import TypeAdapter
        value: JsonValue = TypeAdapter(JsonValue).validate_json(response.content)
        if not isinstance(value, dict) or value.get('code', 200) != 200 or value.get('error'):
            raise LoraDatasetError('dataset_native_failed', 502)
        data = value.get('data', value)
        if not isinstance(data, dict):
            raise LoraDatasetError('dataset_native_invalid', 502)
        return data
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning('Dataset native request unavailable: %s', type(exc).__name__)
        raise LoraDatasetError('dataset_native_unavailable', 503) from exc


async def require_native_idle(*, require_running: bool = False) -> None:
    status = manager.state.models['ace_step'].status
    if status in {ModelStatus.STOPPED, ModelStatus.ERROR}:
        if require_running:
            raise LoraDatasetError('dataset_engine_inactive', 503)
        return
    if status != ModelStatus.RUNNING:
        raise LoraDatasetError('dataset_engine_unavailable', 503)
    for endpoint in ('v1/dataset/auto_label_status', 'v1/dataset/preprocess_status', 'v1/training/status'):
        data = await _native('GET', endpoint)
        if endpoint.endswith('training/status'):
            running = data.get('is_training')
            if not isinstance(running, bool):
                raise LoraDatasetError('dataset_native_invalid', 503)
            if running:
                raise LoraDatasetError('dataset_busy')
        else:
            task_status = data.get('status')
            if not isinstance(task_status, str) or task_status not in {'idle', 'running', 'completed', 'failed'}:
                raise LoraDatasetError('dataset_native_invalid', 503)
            if task_status == 'running':
                raise LoraDatasetError('dataset_busy')


async def _snapshot(entry: LoraDatasetEntry, settings: JsonObject | None = None) -> JsonObject:
    temporary = confined_path(entry.dataset_path).with_name(f'.snapshot-{uuid.uuid4().hex}.json')
    try:
        await _native('POST', 'v1/dataset/save', {'save_path': _relative(temporary), 'dataset_name': entry.name, **(settings or {})})
        raw, _ = _read_dataset(temporary)
        return raw
    finally:
        temporary.unlink(missing_ok=True)


def _operation(entry: LoraDatasetEntry, raw: JsonObject) -> LoraDatasetOperationResponse:
    document = _DatasetDocument.model_validate(raw)
    samples = [sample.model_copy(update={'index': index}) for index, sample in enumerate(document.samples)]
    metadata = LoraDatasetMetadata.model_validate(raw.get('metadata', {}))
    return LoraDatasetOperationResponse(dataset=entry, samples=samples, metadata=metadata)


async def load_dataset(identifier: str, body: LoraDatasetRevision) -> LoraDatasetOperationResponse:
    await require_native_idle(require_running=True)
    entry = get_dataset(identifier, body.revision)
    raw, document = _read_dataset(confined_path(entry.dataset_path))
    if document.samples:
        await _native('POST', 'v1/dataset/load', {'dataset_path': entry.dataset_path})
    return _operation(entry, raw)


async def scan_dataset(identifier: str, body: ScanLoraDatasetRequest) -> LoraDatasetOperationResponse:
    await require_native_idle(require_running=True)
    entry = get_dataset(identifier, body.revision)
    original, _ = _read_dataset(confined_path(entry.dataset_path))
    folder = confined_path(body.audio_dir or entry.audio_dir)
    if not folder.is_dir():
        raise LoraDatasetError('dataset_audio_unavailable', 422)
    for directory, children, files in os.walk(folder, followlinks=False):
        for name in children + files:
            if not (Path(directory) / name).resolve().is_relative_to(datasets_root()):
                raise LoraDatasetError('dataset_path_unsafe', 422)
    await _native('POST', 'v1/dataset/scan', {'audio_dir': _relative(folder), 'dataset_name': entry.name,
        'custom_tag': body.custom_tag, 'tag_position': body.tag_position, 'all_instrumental': body.all_instrumental})
    scanned = await _snapshot(entry)
    previous_signatures = _stored_source_signatures(identifier)
    current_signatures = _source_signatures(original)
    changed = _changed_sources(previous_signatures, current_signatures)
    history_unknown = bool(current_signatures.keys() - previous_signatures.keys())
    merged = merge_scan(original, scanned, changed)
    metadata, scanned_metadata = original.get('metadata', {}), scanned.get('metadata', {})
    if not isinstance(metadata, dict) or not isinstance(scanned_metadata, dict):
        raise LoraDatasetError('dataset_native_invalid', 502)
    merged['metadata'] = {**metadata, **scanned_metadata, 'num_samples': len(_samples(merged)),
        'genre_ratio': metadata.get('genre_ratio', 0)}
    entry = _publish(identifier, body.revision, merged, _relative(folder), _source_signatures(merged))
    await _native('POST', 'v1/dataset/load', {'dataset_path': entry.dataset_path})
    response = _operation(entry, merged)
    response.message = 'dataset_sources_changed' if changed else 'dataset_source_history_unavailable' if history_unknown else ''
    return response


async def save_dataset(identifier: str, body: SaveLoraDatasetRequest) -> LoraDatasetOperationResponse:
    await require_native_idle(require_running=True)
    entry = get_dataset(identifier, body.revision)
    original, _ = _read_dataset(confined_path(entry.dataset_path))
    await _native('POST', 'v1/dataset/load', {'dataset_path': entry.dataset_path})
    current = await _snapshot(entry, {'custom_tag': body.custom_tag, 'tag_position': body.tag_position,
        'all_instrumental': body.all_instrumental, 'genre_ratio': body.genre_ratio})
    if {_source(sample) for sample in _samples(original)} != {_source(sample) for sample in _samples(current)}:
        raise LoraDatasetError('dataset_builder_mismatch')
    entry = _publish(identifier, body.revision, current)
    return _operation(entry, current)


def _preprocess_hash(raw: JsonObject) -> str:
    # Display names and save timestamps do not change tensors; source changes do.
    value = dict(raw)
    metadata = value.get('metadata')
    if isinstance(metadata, dict):
        value['metadata'] = {key: item for key, item in metadata.items() if key not in {'name', 'created_at', 'num_samples'}}
    sources: list[JsonValue] = []
    for sample in _samples(raw):
        source = Path(_source(sample))
        try:
            stat = source.stat()
        except OSError as exc:
            raise LoraDatasetError('dataset_audio_unavailable', 422) from exc
        sources.append([str(source), stat.st_size, stat.st_mtime_ns])
    value['source_identity'] = sources
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


async def _model_identity() -> str:
    health = await _native('GET', 'health')
    name = health.get('loaded_model')
    if health.get('models_initialized') is not True or not isinstance(name, str) or not name or len(name) > 200 or Path(name).name != name:
        raise LoraDatasetError('preprocess_model_unverified')
    checkpoint = ACE_STEP_DIR.resolve() / 'checkpoints' / name
    identities: list[str] = []
    if checkpoint.is_dir():
        for file in sorted(checkpoint.rglob('*')):
            if file.is_file():
                stat = file.stat()
                identities.append(f'{file.relative_to(checkpoint)}:{stat.st_size}:{stat.st_mtime_ns}')
    digest = hashlib.sha256('\n'.join(identities).encode()).hexdigest()
    return f'{ACE_STEP_DIR.resolve()}:{name}:{digest}'


def _record_preprocess(receipt: LoraPreprocessReceipt, entry: LoraDatasetEntry) -> None:
    path = _catalog_path()
    with document_lock(path):
        catalog = _read_catalog(path)
        _entry(catalog, entry.id, entry.revision)
        catalog.workspace.selected_dataset_id = entry.id
        catalog.workspace.preprocess_receipt = receipt
        if receipt.task_id:
            catalog.workspace.preprocess_task = LoraObservedTask(task_id=receipt.task_id,
                dataset_id=entry.id, dataset_path=entry.dataset_path, output_dir=receipt.output_dir)
        catalog.workspace.revision += 1
        _write_catalog(path, catalog)


def _tensor_zip_complete(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if not names or len(names) != len(set(names)):
                return False
            prefix = names[0].split('/')[0] + '/'
            required = {prefix + 'data.pkl', prefix + 'version', prefix + 'byteorder'}
            if not required.issubset(names) or not any(name.startswith(prefix + 'data/') for name in names):
                return False
            if any(not name.startswith(prefix) or item.compress_type != zipfile.ZIP_STORED for name, item in zip(names, archive.infolist())):
                return False
            # shortcut: archive integrity does not prove tensor semantics, native loading remains authoritative.
            return archive.testzip() is None
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
        return False


def _quarantine_incomplete_tensors(output: Path, document: _DatasetDocument) -> None:
    if not output.exists():
        return
    for item in output.rglob('*'):
        if item.is_symlink():
            raise LoraDatasetError('dataset_path_unsafe', 422)
    for sample in document.samples:
        path = output / f'{sample.id}.pt'
        if sample.labeled and path.exists():
            if not path.is_file():
                raise LoraDatasetError('dataset_path_unsafe', 422)
            if not _tensor_zip_complete(path):
                path.rename(output / f'.openfabric-partial-{uuid.uuid4().hex}-{sample.id}.partial')


async def preprocess_dataset(identifier: str, body: PreprocessLoraDatasetRequest) -> LoraDatasetTaskStarted:
    await require_native_idle(require_running=True)
    entry = get_dataset(identifier, body.revision)
    raw, document = _read_dataset(confined_path(entry.dataset_path))
    if not any(sample.labeled for sample in document.samples):
        raise LoraDatasetError('dataset_no_labeled_samples', 422)
    output = confined_path(body.output_dir)
    if not output.is_relative_to(confined_path(entry.dataset_path).parent) or output == confined_path(entry.dataset_path).parent:
        raise LoraDatasetError('dataset_path_unsafe', 422)
    _require_sources_unchanged(identifier, raw)
    receipt = LoraPreprocessReceipt(dataset_id=entry.id, dataset_hash=_preprocess_hash(raw),
        model_identity=await _model_identity(), output_dir=_relative(output))
    previous = get_workspace().preprocess_receipt
    if body.skip_existing:
        if previous is None or previous.model_copy(update={'task_id': ''}) != receipt:
            raise LoraDatasetError('preprocess_reuse_mismatch')
        await run_owned_thread(_quarantine_incomplete_tensors, output, document)
    elif output.exists() and any(output.iterdir()):
        raise LoraDatasetError('preprocess_output_exists')
    # Loading this saved snapshot also prevents processing a different tab's builder.
    await _native('POST', 'v1/dataset/load', {'dataset_path': entry.dataset_path})
    _record_preprocess(receipt, entry)
    result = LoraDatasetTaskStarted.model_validate(await _native('POST', 'v1/dataset/preprocess_async',
        {'output_dir': receipt.output_dir, 'skip_existing': body.skip_existing}))
    if result.task_id:
        _record_preprocess(receipt.model_copy(update={'task_id': result.task_id}), entry)
    return result


def _record_auto_label(entry: LoraDatasetEntry, task_id: str = '') -> None:
    path = _catalog_path()
    with document_lock(path):
        catalog = _read_catalog(path)
        _entry(catalog, entry.id, entry.revision)
        catalog.workspace.selected_dataset_id = entry.id
        catalog.workspace.auto_label_task = LoraObservedTask(task_id=task_id, dataset_id=entry.id, dataset_path=entry.dataset_path)
        catalog.workspace.revision += 1
        _write_catalog(path, catalog)


async def auto_label_dataset(identifier: str, body: AutoLabelLoraDatasetRequest) -> LoraDatasetTaskStarted:
    entry = get_dataset(identifier, body.revision)
    await require_native_idle(require_running=True)
    raw, document = _read_dataset(confined_path(entry.dataset_path))
    _require_sources_unchanged(identifier, raw)
    if not document.samples:
        raise LoraDatasetError('dataset_no_samples', 422)
    await _native('POST', 'v1/dataset/load', {'dataset_path': entry.dataset_path})
    _record_auto_label(entry)
    result = LoraDatasetTaskStarted.model_validate(await _native('POST', 'v1/dataset/auto_label_async', {
        'save_path': entry.dataset_path, 'skip_metas': body.skip_metas, 'format_lyrics': body.format_lyrics,
        'transcribe_lyrics': body.transcribe_lyrics, 'only_unlabeled': body.only_unlabeled,
    }))
    if result.task_id:
        _record_auto_label(entry, result.task_id)
    return result


async def update_dataset_sample(identifier: str, index: int, body: UpdateLoraDatasetSampleRequest) -> LoraDatasetOperationResponse:
    entry = get_dataset(identifier, body.revision)
    await require_native_idle(require_running=True)
    original, document = _read_dataset(confined_path(entry.dataset_path))
    if index < 0 or index >= len(document.samples):
        raise LoraDatasetError('dataset_sample_not_found', 404)
    await _native('POST', 'v1/dataset/load', {'dataset_path': entry.dataset_path})
    current_sample = _json_object(document.samples[index])
    requested = _json_object(body)
    payload = {key: requested[key] if key in body.model_fields_set else current_sample[key]
        for key in UpdateLoraDatasetSampleRequest.model_fields if key != 'revision'}
    payload['sample_idx'] = index
    await _native('PUT', f'v1/dataset/sample/{index}', payload)
    updated = await _snapshot(entry)
    if {_source(sample) for sample in _samples(original)} != {_source(sample) for sample in _samples(updated)}:
        raise LoraDatasetError('dataset_builder_mismatch')
    signature = _source_signatures(updated)[document.samples[index].id]
    entry = _publish(identifier, body.revision, updated, source_signatures={document.samples[index].id: signature})
    return _operation(entry, updated)
