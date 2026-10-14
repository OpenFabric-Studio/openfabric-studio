"""Durable, reviewed deletion of voice-owned files; other copies remain explicit."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from pydantic import TypeAdapter

from . import db
from .config import DATA_DIR
from .contracts import JsonObject
from .voice_retention_contracts import ConfirmVoiceDeletionRequest, RetentionId, VoiceDeletionItem, VoiceDeletionPlan, VoiceDeletionReceipt

_LOCK = threading.RLock()
_EXECUTING: set[str] = set()
_LOG = logging.getLogger(__name__)
_JSON = TypeAdapter(JsonObject)
_VOICE_ID = TypeAdapter(RetentionId)
MAX_RECEIPTS = 4096


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE IF NOT EXISTS voice_deletion_plans (
        id TEXT PRIMARY KEY CHECK(length(id)=32 AND id NOT GLOB '*[^0-9a-f]*'),
        voice_id TEXT NOT NULL UNIQUE CHECK(length(voice_id)=32 AND voice_id NOT GLOB '*[^0-9a-f]*'),
        status TEXT NOT NULL CHECK(status IN ('pending','executing','deleted')),
        plan_json TEXT NOT NULL)''')
    connection.execute('''CREATE TABLE IF NOT EXISTS voice_deletion_receipts (
        plan_id TEXT NOT NULL REFERENCES voice_deletion_plans(id),
        sequence INTEGER NOT NULL CHECK(sequence>0), receipt_json TEXT NOT NULL,
        PRIMARY KEY(plan_id,sequence))''')
    connection.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(payload: JsonObject) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def _exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def _folder(voice_id: str, *, required: bool = False) -> Path:
    from . import voice_build
    # Validate identity before constructing even an internal filesystem path.
    try:
        _VOICE_ID.validate_python(voice_id)
    except ValueError as exc:
        raise HTTPException(404, detail='voice_retention_voice_missing') from exc
    root = voice_build.VOICES_DIR
    path = root / voice_id
    if DATA_DIR.is_symlink() or root.is_symlink() or path.is_symlink() or root.resolve().parent != DATA_DIR.resolve():
        raise HTTPException(409, detail='voice_retention_unsafe_path')
    if required and (not _exists(path) or not path.is_dir()):
        raise HTTPException(404, detail='voice_retention_voice_missing')
    return path


def _owned_path(voice_id: str, relative: str) -> Path:
    folder = _folder(voice_id)
    root = folder.parent
    path = root / relative
    if not path.resolve().is_relative_to(folder.resolve()):
        raise HTTPException(409, detail='voice_retention_unsafe_path')
    for current in (path, *path.parents):
        if current == root:
            break
        if current.is_symlink():
            raise HTTPException(409, detail='voice_retention_unsafe_path')
    return path


def _hash_file(path: Path) -> tuple[int, str]:
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise HTTPException(409, detail='voice_retention_unsafe_path')
    digest = hashlib.sha256()
    with path.open('rb') as reader:
        while chunk := reader.read(1024 * 1024):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise HTTPException(409, detail='voice_retention_plan_changed')
    return after.st_size, digest.hexdigest()


def _inventory_assets(voice_id: str) -> list[VoiceDeletionItem]:
    folder = _folder(voice_id, required=True)
    items: list[VoiceDeletionItem] = []
    referenced: set[Path] = set()
    for query, columns in (
        ('SELECT audio_path,abc_path FROM tracks', ('audio_path', 'abc_path')),
        ('SELECT audio_path,captured_source_path FROM audio_versions', ('audio_path', 'captured_source_path')),
    ):
        for row in db.get_db().execute(query):
            for column in columns:
                value: object = row[column]
                if isinstance(value, str) and value:
                    referenced.add(Path(value).resolve())
    def visit(path: Path) -> None:
        if len(items) >= 100_000:
            raise HTTPException(409, detail='voice_retention_inventory_limit')
        relative = path.relative_to(folder.parent).as_posix()
        info = path.lstat()
        kind: str = 'recording' if '/recordings/' in relative else 'sample' if '/preparations/' in relative else 'trial' if '/trials/' in relative else 'voice_model' if '/models/' in relative or '/runs/' in relative or path.suffix in {'.pth', '.pt', '.ckpt', '.safetensors'} else 'metadata'
        if stat.S_ISDIR(info.st_mode):
            items.append(VoiceDeletionItem(id=relative, kind='directory', root_alias='library_voices', relative_path=relative, action='delete', status='planned'))
            for child in sorted(path.iterdir()):
                visit(child)
        elif stat.S_ISREG(info.st_mode):
            size, digest = _hash_file(_owned_path(voice_id, relative))
            # Pydantic narrows the deliberately enumerated file classification.
            items.append(VoiceDeletionItem.model_validate({'id': relative, 'kind': kind, 'root_alias': 'library_voices',
                'relative_path': relative, 'bytes': size, 'sha256': digest,
                'action': 'retain' if path.resolve() in referenced else 'delete',
                'status': 'retained' if path.resolve() in referenced else 'planned'}))
        else:
            items.append(VoiceDeletionItem(id=relative, kind='external_copy', root_alias='library_voices', relative_path=relative,
                action='unknown', status='pending', error_code='voice_retention_unsafe_path'))
    visit(folder)
    return items


def _inventory_retained(voice_id: str) -> list[VoiceDeletionItem]:
    connection = db.get_db()
    items = [VoiceDeletionItem(id='shared-seed-models', kind='shared_model', root_alias='library_models', relative_path='seed-vc', action='retain', status='retained'),
        VoiceDeletionItem(id='logs-review', kind='log', root_alias='logs', action='unknown', status='pending'),
        VoiceDeletionItem(id='backups-review', kind='backup', root_alias='external', action='unknown', status='external'),
        VoiceDeletionItem(id='downloads-and-unmanaged-copies', kind='external_copy', root_alias='external', action='unknown', status='external')]
    track_ids: set[int] = set()
    for row in connection.execute('SELECT id,params_json FROM tracks'):
        raw: object = row['params_json']
        identifier: object = row['id']
        if not isinstance(raw, str) or not isinstance(identifier, int):
            raise HTTPException(409, detail='voice_retention_catalog_invalid')
        try:
            params = _JSON.validate_json(raw)
        except ValueError:
            items.append(VoiceDeletionItem(id=f'catalog-review:{identifier}', kind='derived_track', root_alias='catalog', track_id=identifier, action='unknown', status='pending', error_code='voice_retention_catalog_invalid'))
            continue
        if params.get('voice_id') == voice_id:
            track_ids.add(identifier)
            items.append(VoiceDeletionItem(id=f'track:{identifier}', kind='derived_track', root_alias='catalog', track_id=identifier, action='retain', status='retained'))
    for row in connection.execute('SELECT id,track_id FROM audio_versions WHERE voice_id=?', (voice_id,)):
        version: object = row['id']
        track: object = row['track_id']
        if not isinstance(version, str) or not isinstance(track, int):
            raise HTTPException(409, detail='voice_retention_catalog_invalid')
        track_ids.add(track)
        items.append(VoiceDeletionItem(id=f'version:{version}', kind='audio_version', root_alias='catalog', track_id=track, version_id=version, action='retain', status='retained'))
    for track in sorted(track_ids):
        export = db.FILES_DIR / '_exports' / str(track)
        if _exists(export):
            items.append(VoiceDeletionItem(id=f'exports:{track}', kind='audio_export', root_alias='library_files', relative_path=f'_exports/{track}', track_id=track, action='retain', status='retained'))
        work_root = _folder(voice_id).parent
        for candidate in (work_root / '_apply' / f'work_{track}', work_root / '_apply' / f'{track}.json'):
            _inventory_work_copy(candidate, work_root, track, items)
    _inventory_production_sources(voice_id, items)
    # Legacy metadata may name files outside this voice. Never expose or remove them.
    metadata = _folder(voice_id) / 'voice.json'
    if metadata.is_file() and not metadata.is_symlink():
        try:
            raw_meta = _JSON.validate_json(metadata.read_bytes())
        except ValueError:
            raw_meta = {}
        for key in ('checkpoint', 'config', 'reference'):
            value = raw_meta.get(key)
            if isinstance(value, str) and Path(value).is_absolute() and not Path(value).resolve().is_relative_to(metadata.parent.resolve()):
                items.append(VoiceDeletionItem(id=f'external-legacy:{key}', kind='voice_model', root_alias='external', action='unknown', status='external'))
    return items


def _inventory_work_copy(path: Path, root: Path, track_id: int, items: list[VoiceDeletionItem]) -> None:
    if not _exists(path):
        return
    if len(items) >= 100_000:
        raise HTTPException(409, detail='voice_retention_inventory_limit')
    relative = path.relative_to(root).as_posix()
    item = VoiceDeletionItem(id='work:' + relative, kind='working_copy', root_alias='library_voice_work',
        relative_path=relative, track_id=track_id, action='unknown', status='pending')
    items.append(item)
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        item.error_code = 'voice_retention_unsafe_path'
    elif path.is_dir():
        for child in sorted(path.iterdir()):
            _inventory_work_copy(child, root, track_id, items)
    else:
        try:
            item.bytes, item.sha256 = _hash_file(path)
        except (HTTPException, OSError):
            item.error_code = 'voice_retention_copy_review_required'


def _inventory_production_sources(voice_id: str, items: list[VoiceDeletionItem]) -> None:
    from .artist_production import ProductionError, asset_path, graph, list_runs
    connection = db.get_db()
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='production_runs'").fetchone() is None:
        return
    identifiers: set[str] = set()
    for run in list_runs():
        if run.request.voice_id != voice_id:
            continue
        roots = [run.output_asset_id] if run.output_asset_id else [run.request.guide_asset_id, run.request.accompaniment_asset_id]
        for root in roots:
            if root is None:
                continue
            try:
                assets = graph(root)
            except ProductionError:
                items.append(VoiceDeletionItem(id='source-review:' + root, kind='source_asset', root_alias='catalog',
                    action='unknown', status='pending', error_code='voice_retention_source_review_required'))
                continue
            for asset in assets:
                if asset.id in identifiers:
                    continue
                identifiers.add(asset.id)
                item = VoiceDeletionItem(id='source:' + asset.id, kind='source_asset', root_alias='library_files',
                    sha256=asset.sha256, action='retain', status='retained', track_id=run.track_id)
                try:
                    path = asset_path(asset.id)
                    item.relative_path = path.relative_to(db.FILES_DIR.resolve()).as_posix()
                    size, observed = _hash_file(path)
                    item.bytes = size
                    if observed != asset.sha256:
                        raise ValueError('source changed')
                except (ProductionError, HTTPException, OSError, ValueError):
                    item.status, item.error_code = 'pending', 'voice_retention_source_review_required'
                items.append(item)


def _save_plan(plan: VoiceDeletionPlan) -> None:
    connection = db.get_db()
    connection.execute('INSERT INTO voice_deletion_plans(id,voice_id,status,plan_json) VALUES(?,?,?,?) ON CONFLICT(voice_id) DO UPDATE SET status=excluded.status,plan_json=excluded.plan_json',
        (plan.id, plan.voice_id, plan.status, plan.model_dump_json()))
    connection.commit()


def get_plan(plan_id: str) -> VoiceDeletionPlan:
    with _LOCK:
        row = db.get_db().execute('SELECT plan_json,status,voice_id FROM voice_deletion_plans WHERE id=?', (plan_id,)).fetchone()
        if row is None:
            raise HTTPException(404, detail='voice_retention_plan_missing')
        payload: object = row['plan_json']
        if not isinstance(payload, str):
            raise HTTPException(409, detail='voice_retention_catalog_invalid')
        try:
            plan = VoiceDeletionPlan.model_validate_json(payload)
        except ValueError as exc:
            raise HTTPException(409, detail='voice_retention_catalog_invalid') from exc
        if plan.id != plan_id or plan.voice_id != row['voice_id'] or plan.status != row['status']:
            raise HTTPException(409, detail='voice_retention_catalog_invalid')
        calculated = _digest(_JSON.validate_python(plan.model_dump(mode='json', exclude={'plan_sha256', 'status'})))
        if plan.plan_sha256 != '0' * 64 and calculated != plan.plan_sha256:
            raise HTTPException(409, detail='voice_retention_plan_invalid')
        return plan


def require_not_frozen(voice_id: str) -> None:
    with _LOCK:
        if db.get_db().execute('SELECT 1 FROM voice_deletion_plans WHERE voice_id=?', (voice_id,)).fetchone() is not None:
            raise HTTPException(409, detail='voice_retention_frozen')


def plan_deletion(voice_id: str) -> VoiceDeletionPlan:
    with _LOCK:
        connection = db.get_db()
        prior = connection.execute('SELECT id FROM voice_deletion_plans WHERE voice_id=?', (voice_id,)).fetchone()
        if prior is not None:
            identifier: object = prior['id']
            if not isinstance(identifier, str):
                raise HTTPException(409, detail='voice_retention_catalog_invalid')
            plan = get_plan(identifier)
            if plan.status != 'pending' or identifier in _EXECUTING:
                return plan
        else:
            _folder(voice_id, required=True)
            plan = VoiceDeletionPlan(id=uuid4().hex, voice_id=voice_id, status='pending', created_at=_now(), plan_sha256='0' * 64, items=[])
            # Admission sees the freeze before any inventory read or awaited drain.
            _save_plan(plan)
        plan.items = [*_inventory_assets(voice_id), *_inventory_retained(voice_id)]
        plan.plan_sha256 = _digest(_JSON.validate_python(plan.model_dump(mode='json', exclude={'plan_sha256', 'status'})))
        _save_plan(plan)
        return plan


def verify_receipt(receipt: VoiceDeletionReceipt) -> bool:
    payload = _JSON.validate_python(receipt.model_dump(mode='json', exclude={'receipt_sha256'}))
    return not receipt.full_erasure_verified and _digest(payload) == receipt.receipt_sha256


def get_receipt(plan_id: str) -> VoiceDeletionReceipt:
    with _LOCK:
        # Stream a bounded chain instead of trusting only the latest two receipts.
        cursor = db.get_db().execute('SELECT plan_id,sequence,receipt_json FROM voice_deletion_receipts WHERE plan_id=? ORDER BY sequence LIMIT ?', (plan_id, MAX_RECEIPTS + 1))
        row = cursor.fetchone()
        if row is None:
            raise HTTPException(404, detail='voice_retention_receipt_missing')
        plan = get_plan(plan_id)
        expected_sequence = 1
        previous_digest: str | None = None
        latest: VoiceDeletionReceipt | None = None
        while row is not None:
            raw: object = row['receipt_json']
            if expected_sequence > MAX_RECEIPTS or not isinstance(raw, str) or row['plan_id'] != plan_id or row['sequence'] != expected_sequence:
                raise HTTPException(409, detail='voice_retention_receipt_invalid')
            try:
                receipt = VoiceDeletionReceipt.model_validate_json(raw)
                valid = verify_receipt(receipt)
            except ValueError as exc:
                raise HTTPException(409, detail='voice_retention_receipt_invalid') from exc
            if not valid or receipt.plan_id != plan.id or receipt.voice_id != plan.voice_id or receipt.sequence != expected_sequence or receipt.previous_receipt_sha256 != previous_digest:
                raise HTTPException(409, detail='voice_retention_receipt_invalid')
            previous_digest = receipt.receipt_sha256
            latest = receipt
            expected_sequence += 1
            row = cursor.fetchone()
        if latest is None:
            raise HTTPException(404, detail='voice_retention_receipt_missing')
        return latest


def _record(plan: VoiceDeletionPlan, items: list[VoiceDeletionItem], *, reserve_terminal: bool = False) -> VoiceDeletionReceipt:
    with _LOCK:
        try:
            previous = get_receipt(plan.id)
        except HTTPException as exc:
            if exc.detail != 'voice_retention_receipt_missing':
                raise
            previous = None
        if previous is not None and previous.sequence >= MAX_RECEIPTS - int(reserve_terminal):
            raise HTTPException(409, detail='voice_retention_receipt_capacity_exceeded')
        receipt = VoiceDeletionReceipt(plan_id=plan.id, voice_id=plan.voice_id, plan_sha256=plan.plan_sha256, status=plan.status,
            created_at=_now(), sequence=previous.sequence + 1 if previous else 1, items=items, voice_assets_deleted=plan.status == 'deleted',
            previous_receipt_sha256=previous.receipt_sha256 if previous else None, receipt_sha256='0' * 64)
        receipt.receipt_sha256 = _digest(_JSON.validate_python(receipt.model_dump(mode='json', exclude={'receipt_sha256'})))
        connection = db.get_db()
        connection.execute('INSERT INTO voice_deletion_receipts(plan_id,sequence,receipt_json) VALUES(?,?,?)', (plan.id, receipt.sequence, receipt.model_dump_json()))
        connection.execute('UPDATE voice_deletion_plans SET status=?,plan_json=? WHERE id=?', (plan.status, plan.model_dump_json(), plan.id))
        connection.commit()
        return receipt


def _working_items(plan: VoiceDeletionPlan) -> list[VoiceDeletionItem]:
    try:
        prior = get_receipt(plan.id)
    except HTTPException as exc:
        if exc.detail != 'voice_retention_receipt_missing':
            raise
        return [item.model_copy(deep=True) for item in plan.items]
    if prior.plan_sha256 != plan.plan_sha256:
        return [item.model_copy(deep=True) for item in plan.items]
    previous = {item.id: item for item in prior.items}
    return [previous.get(item.id, item).model_copy(deep=True) for item in plan.items]


def _validate_remaining(plan: VoiceDeletionPlan, items: list[VoiceDeletionItem]) -> None:
    folder = _folder(plan.voice_id)
    current = _inventory_assets(plan.voice_id) if _exists(folder) else []
    reviewed = {item.id: item for item in items if item.root_alias == 'library_voices'}
    for item in current:
        expected = reviewed.get(item.id)
        if expected is None or expected.kind != item.kind or expected.sha256 != item.sha256 or expected.action != item.action or expected.status == 'completed':
            raise HTTPException(409, detail='voice_retention_plan_changed')


async def execute_deletion(plan_id: str, request: ConfirmVoiceDeletionRequest) -> VoiceDeletionReceipt:
    from .voice_build import release_voice
    from .voice_comparisons import release_voice as release_comparisons
    with _LOCK:
        plan = get_plan(plan_id)
        if not request.delete_voice_assets or request.confirm_voice_id != plan.voice_id or request.plan_sha256 != plan.plan_sha256 or plan.plan_sha256 == '0' * 64:
            raise HTTPException(409, detail='voice_retention_confirmation_required')
        if plan.status == 'deleted':
            return get_receipt(plan_id)
        if plan_id in _EXECUTING:
            raise HTTPException(409, detail='voice_retention_busy')
        items = _working_items(plan)
        plan.status = 'executing'
        _record(plan, items, reserve_terminal=True)
        _EXECUTING.add(plan_id)
    try:
        try:
            await release_comparisons(plan.voice_id)
            await release_voice(plan.voice_id)
        except Exception:
            _LOG.exception('Could not drain voice before reviewed deletion')
            for item in items:
                if item.id == plan.voice_id:
                    item.status, item.error_code = 'pending', 'voice_retention_cleanup_failed'
            return _record(plan, items)
        try:
            _validate_remaining(plan, items)
        except HTTPException:
            # Nothing was removed in this attempt; changed files need a fresh review.
            plan.status = 'pending'
            _record(plan, items)
            raise
        metadata_id = f'{plan.voice_id}/voice.json'
        ordered = sorted((item for item in items if item.action == 'delete'), key=lambda item: (item.kind == 'directory', item.id == metadata_id, -(item.relative_path or '').count('/'), item.id))
        for item in ordered:
            if item.relative_path is None:
                raise HTTPException(409, detail='voice_retention_catalog_invalid')
            # Keep a partial deletion discoverable in the voice library after reload.
            if item.id == metadata_id and any(other.id != metadata_id and other.root_alias == 'library_voices' and other.kind != 'directory' and other.status != 'completed' for other in items):
                item.status, item.error_code = 'pending', 'voice_retention_recovery_metadata_retained'
                continue
            try:
                path = _owned_path(plan.voice_id, item.relative_path)
                if _exists(path):
                    if item.kind == 'directory':
                        path.rmdir()
                    else:
                        size, digest = _hash_file(path)
                        if size != item.bytes or digest != item.sha256:
                            raise HTTPException(409, detail='voice_retention_plan_changed')
                        _owned_path(plan.voice_id, item.relative_path).unlink()
                item.status, item.error_code = 'completed', ''
            except HTTPException as exc:
                item.status, item.error_code = 'pending', str(exc.detail)
            except OSError:
                _LOG.exception('Could not remove reviewed voice asset')
                item.status, item.error_code = 'pending', 'voice_retention_delete_failed'
            await asyncio.sleep(0)
        try:
            if not _exists(_folder(plan.voice_id)):
                plan.status = 'deleted'
        except OSError:
            _LOG.exception('Could not verify reviewed voice absence')
        return _record(plan, items)
    except asyncio.CancelledError:
        _record(plan, items)
        raise
    finally:
        with _LOCK:
            _EXECUTING.discard(plan_id)


def reconcile_deletions() -> None:
    """Observe accessible absence on restart; never resume destructive work silently."""
    with _LOCK:
        identifiers = db.get_db().execute("SELECT id FROM voice_deletion_plans WHERE status='executing'").fetchall()
        for row in identifiers:
            identifier: object = row['id']
            if not isinstance(identifier, str) or identifier in _EXECUTING:
                continue
            try:
                plan = get_plan(identifier)
                items = _working_items(plan)
                for item in items:
                    if item.action == 'delete' and item.relative_path is not None and not _exists(_owned_path(plan.voice_id, item.relative_path)):
                        item.status, item.error_code = 'completed', ''
                if not _exists(_folder(plan.voice_id)):
                    plan.status = 'deleted'
                _record(plan, items)
            except (HTTPException, OSError, ValueError):
                _LOG.exception('Could not reconcile reviewed voice deletion')
