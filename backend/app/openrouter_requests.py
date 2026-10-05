"""Durable safe receipts: an ambiguous submit is never a retryable intent."""
from __future__ import annotations
from datetime import datetime, timezone
import heapq
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Literal
from pydantic import TypeAdapter, ValidationError
from .atomic_files import document_lock, write_object
from .config import DATA_DIR
from .contracts import JsonObject
from .openrouter_contracts import OpenRouterQuote, OpenRouterReceipt
from .openrouter_errors import OpenRouterError
from . import openrouter_catalog as catalog

REQUESTS_ROOT = DATA_DIR/'providers'/'openrouter-requests'
_json: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_HISTORY_UNAVAILABLE = False
_LOG = logging.getLogger(__name__)

def now() -> str: return datetime.now(timezone.utc).isoformat()

def _path(identifier: str) -> Path:
    import re
    if re.fullmatch('[0-9a-f]{32}',identifier) is None: raise OpenRouterError('request_not_found',404)
    path=REQUESTS_ROOT/f'{identifier}.json'
    if REQUESTS_ROOT.is_symlink() or REQUESTS_ROOT.parent.is_symlink() or path.is_symlink(): raise OpenRouterError('provider_storage_unavailable')
    return path

def _save(record: OpenRouterReceipt) -> None:
    path=_path(record.id)
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        write_object(path,_json.validate_json(record.model_dump_json()))
    except (OSError,ValidationError,ValueError) as error: raise OpenRouterError('provider_storage_unavailable') from error

def get(identifier: str) -> OpenRouterReceipt:
    path=_path(identifier)
    with document_lock(path):
        try:
            if path.stat().st_size>16384: raise ValueError()
            record=OpenRouterReceipt.model_validate_json(path.read_bytes())
            if record.id!=identifier: raise ValueError()
            return record
        except FileNotFoundError as error: raise OpenRouterError('request_not_found',404) from error
        except (OSError,ValueError,ValidationError) as error: raise OpenRouterError('provider_storage_unavailable') from error

def prepare(owner_id: str, quote: OpenRouterQuote) -> OpenRouterReceipt:
    if catalog.saved_quote(quote.id)!=quote: raise OpenRouterError('quote_mismatch',409)
    record=OpenRouterReceipt(id=quote.id,owner_id=owner_id,kind=quote.kind,model_id=quote.model_id,
        model_fingerprint=quote.model_fingerprint,request_fingerprint=quote.request_fingerprint,quote_id=quote.id,
        estimated_usd=quote.estimated_usd,state='intent',created_at=now(),updated_at=now())
    with document_lock(_path(record.id)):
        if _path(record.id).exists():
            existing=get(record.id)
            if existing.owner_id==record.owner_id and existing.state=='intent': return existing
            raise OpenRouterError('request_already_prepared',409)
        _save(record)
    return record

def begin_submit(identifier: str, quote_id: str, request_fingerprint: str) -> OpenRouterReceipt:
    with document_lock(_path(identifier)):
        record=get(identifier)
        if record.state!='intent': raise OpenRouterError('request_already_submitted',409)
        if record.quote_id!=quote_id or record.request_fingerprint!=request_fingerprint: raise OpenRouterError('quote_mismatch',409)
        changed=record.model_copy(update={'state':'submitting','updated_at':now()})
        _save(changed)
        return changed

def update(identifier: str, *, state: Literal['submission_unknown','submitted','completed','failed','canceled_tracking'], remote_id: str | None=None, actual_cost_usd: float | None=None, error_code: str | None=None) -> OpenRouterReceipt:
    with document_lock(_path(identifier)):
        record=get(identifier)
        if record.state=='intent' or record.state in {'failed','completed'} and state!=record.state:
            raise OpenRouterError('request_state_conflict',409)
        if remote_id is not None and record.remote_id is not None and remote_id!=record.remote_id:
            raise OpenRouterError('request_remote_identity_conflict',409)
        changed=OpenRouterReceipt.model_validate({**record.model_dump(),'state':state,'remote_id':remote_id if remote_id is not None else record.remote_id,
            'actual_cost_usd':actual_cost_usd if actual_cost_usd is not None else record.actual_cost_usd,'error_code':error_code,'updated_at':now()})
        _save(changed)
        return changed

def _records() -> Iterator[OpenRouterReceipt]:
    global _HISTORY_UNAVAILABLE
    if REQUESTS_ROOT.is_symlink() or REQUESTS_ROOT.parent.is_symlink(): raise OpenRouterError('provider_storage_unavailable')
    if not REQUESTS_ROOT.exists(): return
    for path in REQUESTS_ROOT.glob('*.json'):
        try: yield get(path.stem)
        except OpenRouterError:
            _HISTORY_UNAVAILABLE=True
            _LOG.warning('An unreadable OpenRouter receipt was retained; no retry is authorized')

def list_receipts() -> list[OpenRouterReceipt]:
    # Public history remains bounded; accumulated valid receipts never disable
    # startup or lose the ability to inspect older domain-owned request IDs.
    return heapq.nlargest(1000,_records(),key=lambda row:(row.created_at,row.id))

def history_unavailable() -> bool: return _HISTORY_UNAVAILABLE

def recover() -> None:
    global _HISTORY_UNAVAILABLE
    _HISTORY_UNAVAILABLE=False
    try:
        for record in _records():
            if record.state=='submitting':
                try: update(record.id,state='submission_unknown',error_code='submission_unknown')
                except OpenRouterError:
                    _HISTORY_UNAVAILABLE=True
                    _LOG.warning('An OpenRouter receipt could not be reconciled; no retry is authorized')
    except OpenRouterError:
        _HISTORY_UNAVAILABLE=True
        _LOG.warning('OpenRouter receipt storage is unavailable; local workflows remain available')

def mark_tracking_canceled(identifier: str) -> OpenRouterReceipt:
    return update(identifier,state='canceled_tracking')
