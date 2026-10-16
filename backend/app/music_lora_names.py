"""Serialize names across training history and the managed adapter registry."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
import json
import sqlite3
import sys
import unicodedata

from pydantic import ValidationError

from . import db
from .atomic_files import document_lock
from .music_lora_contracts import AdapterEngine, MusicLoraError
from .music_training_contracts import MusicLoraTrainingJob
from .stable_lora_contracts import StableLoraTrainingJob


def normalized_name(value: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', value).split()).casefold()


@contextmanager
def name_transaction() -> Iterator[None]:
    """Keep the check and creation together, including across backend processes."""
    from .music_lora import _root
    root = _root(create=True).resolve()
    lease = root / 'names.lock'
    with document_lock(root / 'registry.lock'):
        if lease.is_symlink() or lease.resolve() != lease:
            raise MusicLoraError('music_lora_name_unavailable', 503)
        try:
            flags = os.O_CREAT | os.O_RDWR
            if sys.platform != 'win32':
                flags |= os.O_NOFOLLOW
            stream = os.fdopen(os.open(lease, flags, 0o600), 'r+b')
        except OSError as error:
            raise MusicLoraError('music_lora_name_unavailable', 503) from error
        with stream:
            try:
                if sys.platform == 'win32':
                    import msvcrt
                    if os.fstat(stream.fileno()).st_size == 0:
                        stream.write(b'0'); stream.flush()
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise MusicLoraError('music_lora_name_unavailable', 503) from error
            yield


def _jobs(engine: AdapterEngine) -> list[StableLoraTrainingJob | MusicLoraTrainingJob]:
    path = db.DB_PATH
    if path.is_symlink() or path.parent.resolve() != db.DATA_DIR.resolve():
        raise MusicLoraError('music_lora_name_unavailable', 503)
    if not path.exists():
        return []
    path = path.resolve()
    table = 'stable_lora_jobs' if engine == 'stable_audio3' else 'music_training_jobs'
    jobs: list[StableLoraTrainingJob | MusicLoraTrainingJob] = []
    try:
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            if connection.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?', ('table', table)).fetchone() is None:
                return []
            for identifier, encoded in connection.execute('SELECT id,payload_json FROM ' + table):
                if not isinstance(encoded, str) or len(encoded) > 16 * 1024**2:
                    raise ValueError('Invalid name history')
                payload: object = json.loads(encoded)
                request = payload.get('request') if isinstance(payload, dict) else None
                if isinstance(request, dict) and request.get('engine') == 'heartmula':
                    continue
                job = StableLoraTrainingJob.model_validate_json(encoded) if engine == 'stable_audio3' else MusicLoraTrainingJob.model_validate_json(encoded)
                if job.id != identifier:
                    raise ValueError('Invalid history identity')
                if isinstance(job, StableLoraTrainingJob) or job.request.engine == engine:
                    jobs.append(job)
        finally:
            connection.close()
    except (OSError, ValueError, ValidationError, sqlite3.Error) as error:
        raise MusicLoraError('music_lora_name_unavailable', 503) from error
    return jobs


def require_available(engine: AdapterEngine, name: str, *, family_job_id: str | None = None) -> None:
    """Call under name_transaction until the new job or adapter is persisted."""
    from .music_lora import list_adapters
    key = normalized_name(name)
    if not key or len(name) > 120 or family_job_id is not None and engine != 'yue2':
        raise MusicLoraError('music_lora_request_invalid', 422)
    jobs = _jobs(engine)
    family = {family_job_id} if family_job_id is not None else set()
    if family_job_id is not None:
        family.update(job.id for job in jobs if isinstance(job, MusicLoraTrainingJob)
            and job.request.yue_ar_source_job_id == family_job_id)
    if any(normalized_name(job.request.name) == key and job.id not in family for job in jobs):
        raise MusicLoraError('music_lora_name_exists', 409)
    registry = list_adapters()
    if registry.history_incomplete:
        raise MusicLoraError('music_lora_name_unavailable', 503)
    if any(adapter.engine == engine and normalized_name(adapter.name) == key
            and (family_job_id is None or adapter.source_job_id not in family) for adapter in registry.adapters):
        raise MusicLoraError('music_lora_name_exists', 409)
