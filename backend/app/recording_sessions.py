"""Bounded recording QC and exact-byte training/held-out separation."""
from __future__ import annotations

import hashlib
import logging
import math
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import HTTPException
from pydantic import ValidationError

from . import db
from .recording_contracts import (RecordingAudioInfo, RecordingLabels, RecordingPurpose,
    RecordingQC, RecordingScript, RecordingSession, RecordingWarning)

logger = logging.getLogger(__name__)
MAX_AUDIO_BYTES = 512 * 1024 * 1024
MAX_SOURCE_FILES = 1000
_AUDIO_EXTENSIONS = {'wav', 'mp3', 'flac', 'ogg', 'opus', 'm4a', 'partial'}


class RecordingError(Exception):
    def __init__(self, code: str, status_code: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class _FileIdentity:
    sha256: str
    file_bytes: int
    stamp: tuple[int, int, int, int]


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE IF NOT EXISTS recording_sessions (
        id TEXT PRIMARY KEY CHECK(length(id)=32 AND id NOT GLOB '*[^0-9a-f]*'),
        voice_id TEXT NOT NULL CHECK(length(voice_id)=32 AND voice_id NOT GLOB '*[^0-9a-f]*'),
        purpose TEXT NOT NULL CHECK(purpose IN ('training','held_out')),
        original_sha256 TEXT NOT NULL CHECK(length(original_sha256)=64 AND original_sha256 NOT GLOB '*[^0-9a-f]*'),
        relative_path TEXT NOT NULL, payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        UNIQUE(voice_id,original_sha256))''')
    connection.execute('CREATE INDEX IF NOT EXISTS idx_recording_sessions_voice ON recording_sessions(voice_id,purpose)')
    connection.execute('''CREATE TRIGGER IF NOT EXISTS recording_session_no_update
        BEFORE UPDATE ON recording_sessions BEGIN SELECT RAISE(ABORT,'recording_session_immutable'); END''')
    connection.execute('''CREATE TRIGGER IF NOT EXISTS recording_session_no_delete
        BEFORE DELETE ON recording_sessions BEGIN SELECT RAISE(ABORT,'recording_session_immutable'); END''')
    connection.commit()


def _voice_root(voice_id: str) -> Path:
    from .voice_build import voice_dir
    try:
        return voice_dir(voice_id).resolve()
    except HTTPException as exc:
        raise RecordingError('recording_voice_missing', 404) from exc


def _path(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if '..' in path.parts or not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise RecordingError('recording_invalid_path')
    return resolved


def _purpose_root(voice: Path, purpose: RecordingPurpose) -> Path:
    root = voice / ('recordings' if purpose == 'training' else 'trials')
    if not root.resolve().is_relative_to(voice):
        raise RecordingError('recording_invalid_path')
    return root


def _identity(path: Path) -> _FileIdentity:
    try:
        digest, total = hashlib.sha256(), 0
        with path.open('rb') as handle:
            before = os.fstat(handle.fileno())
            if before.st_size <= 0:
                raise RecordingError('recording_invalid_audio')
            if before.st_size > MAX_AUDIO_BYTES:
                raise RecordingError('recording_too_large', 413)
            while chunk := handle.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_AUDIO_BYTES:
                    raise RecordingError('recording_too_large', 413)
                digest.update(chunk)
            after = os.fstat(handle.fileno())
        stamp = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if stamp != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or total != before.st_size:
            raise RecordingError('recording_source_changed', 409)
        return _FileIdentity(digest.hexdigest(), total, stamp)
    except OSError as exc:
        logger.warning('Recording could not be read', exc_info=True)
        raise RecordingError('recording_unavailable', 409) from exc


def _measure(path: Path) -> RecordingAudioInfo:
    """100 ms RMS windows: the quietest decile is an amplitude proxy, not isolated noise."""
    try:
        info = sf.info(path)
        if not 1 <= info.samplerate <= 192000 or not 1 <= info.channels <= 8 or not 0 < info.frames <= 3600 * info.samplerate:
            raise RecordingError('recording_decode_limit')
        peak, squared_sum, clipped, frames, near_silence = 0.0, 0.0, 0, 0, 0
        window_levels: list[float] = []
        with sf.SoundFile(path) as source:
            while True:
                samples = source.read(max(1, info.samplerate // 10), dtype='float32', always_2d=True)
                count = samples.shape[0]
                if samples.shape[1] != info.channels or not np.isfinite(samples).all():
                    raise RecordingError('recording_invalid_audio')
                if count == 0:
                    break
                frames += count
                if frames > info.frames:
                    raise RecordingError('recording_invalid_audio')
                absolute = np.abs(samples)
                block_peak = float(np.max(absolute))
                if block_peak > 8:
                    raise RecordingError('recording_invalid_audio')
                peak = max(peak, block_peak)
                block_squared = float(np.sum(np.square(samples, dtype=np.float64)))
                squared_sum += block_squared
                clipped += int(np.count_nonzero(absolute >= .999))
                level = math.sqrt(block_squared / samples.size)
                window_levels.append(level)
                if level < .001:
                    near_silence += count
        if frames != info.frames or frames == 0:
            raise RecordingError('recording_invalid_audio')
        sample_count = frames * info.channels
        rms = math.sqrt(squared_sum / sample_count)
        noise_proxy = float(np.percentile(window_levels, 10, method='lower'))
        warnings: list[RecordingWarning] = ['noise_estimate_is_amplitude_proxy', 'pitch_listening_required']
        if clipped:
            warnings.append('full_scale_samples')
        if near_silence / frames >= .5:
            warnings.append('mostly_near_silence')
        if (info.samplerate, info.channels, info.subtype) != (48000, 1, 'PCM_24'):
            warnings.append('capture_differs_from_guidance')
        return RecordingAudioInfo(format=info.format, sample_rate=info.samplerate, channels=info.channels,
            subtype=info.subtype, duration_sec=frames / info.samplerate,
            qc=RecordingQC(peak=peak, clipped_fraction=clipped / sample_count, rms_dbfs=20 * math.log10(max(rms, 1e-6)),
                near_silence_fraction=near_silence / frames, noise_floor_proxy_dbfs=20 * math.log10(max(noise_proxy, 1e-6)),
                samples_analyzed=sample_count, warnings=warnings))
    except RecordingError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        logger.debug('Recording decoding failed', exc_info=True)
        raise RecordingError('recording_invalid_audio') from exc


def _parse(row: sqlite3.Row) -> RecordingSession:
    raw: object = row['payload_json']
    try:
        if not isinstance(raw, str):
            raise ValueError('invalid_recording_payload')
        record = RecordingSession.model_validate_json(raw)
        for column in ('id', 'voice_id', 'purpose', 'original_sha256', 'relative_path'):
            if getattr(record, column) != row[column]:
                raise ValueError('invalid_recording_identity')
        return record
    except (ValidationError, ValueError) as exc:
        raise RecordingError('recording_metadata_invalid', 409) from exc


def _rows(query: str, parameters: tuple[str, ...]) -> list[sqlite3.Row]:
    try:
        result: list[sqlite3.Row] = db.get_db().execute(query, parameters).fetchall()
        return result
    except sqlite3.Error as exc:
        logger.exception('Recording metadata read failed')
        raise RecordingError('recording_storage_failed', 503) from exc


@contextmanager
def _transaction() -> Iterator[sqlite3.Connection]:
    connection: sqlite3.Connection | None = None
    try:
        db.get_db()
        connection = sqlite3.connect(db.DB_PATH, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            yield connection
    except sqlite3.Error as exc:
        logger.exception('Recording metadata write failed')
        raise RecordingError('recording_storage_failed', 503) from exc
    finally:
        if connection is not None:
            connection.close()


def list_sessions(voice_id: str) -> list[RecordingSession]:
    _voice_root(voice_id)
    return [_parse(row) for row in _rows('SELECT * FROM recording_sessions WHERE voice_id=? ORDER BY rowid', (voice_id,))]


def _opposite_hashes(voice: Path, purpose: RecordingPurpose) -> set[str]:
    directory = voice / ('trials/sources' if purpose == 'training' else 'recordings')
    if not directory.resolve().is_relative_to(voice):
        raise RecordingError('recording_invalid_path')
    result: set[str] = set()
    if not directory.is_dir():
        return result
    for index, candidate in enumerate(directory.iterdir()):
        if index >= MAX_SOURCE_FILES:
            raise RecordingError('recording_source_limit')
        if candidate.is_file() and candidate.suffix.lower().lstrip('.') in _AUDIO_EXTENSIONS:
            result.add(_identity(_path(candidate, directory)).sha256)
    return result


def _require_purpose(voice_id: str, sha256: str, purpose: RecordingPurpose, opposite_hashes: set[str]) -> None:
    rows = _rows('SELECT * FROM recording_sessions WHERE voice_id=? AND original_sha256=?', (voice_id, sha256))
    known = _parse(rows[0]) if rows else None
    if sha256 in opposite_hashes or (known is not None and known.purpose != purpose):
        raise RecordingError('recording_held_out_overlap' if purpose == 'training' else 'recording_training_overlap', 409)


def inspect_file(path: Path, voice_id: str, purpose: RecordingPurpose, labels: RecordingLabels, *,
                 original_filename: str | None = None) -> RecordingSession:
    if purpose not in {'training', 'held_out'}:
        raise RecordingError('recording_invalid_purpose')
    voice = _voice_root(voice_id)
    source = _path(path, _purpose_root(voice, purpose))
    identity = _identity(source)
    _require_purpose(voice_id, identity.sha256, purpose, _opposite_hashes(voice, purpose))
    measured = _measure(source)
    if identity != _identity(_path(path, _purpose_root(voice, purpose))):
        raise RecordingError('recording_source_changed', 409)
    record = RecordingSession(**measured.model_dump(), id=uuid.uuid4().hex, voice_id=voice_id, purpose=purpose,
        labels=labels, original_filename=original_filename or path.name, relative_path=source.relative_to(voice).as_posix(),
        original_sha256=identity.sha256, file_bytes=identity.file_bytes, created_at=datetime.now(timezone.utc).isoformat())
    with _transaction() as connection:
        row = connection.execute('SELECT * FROM recording_sessions WHERE voice_id=? AND original_sha256=?', (voice_id, identity.sha256)).fetchone()
        if row is not None:
            existing = _parse(row)
            if existing.purpose != purpose:
                raise RecordingError('recording_held_out_overlap' if purpose == 'training' else 'recording_training_overlap', 409)
            if existing.relative_path != record.relative_path:
                raise RecordingError('recording_duplicate', 409)
            return existing
        connection.execute('INSERT INTO recording_sessions(id,voice_id,purpose,original_sha256,relative_path,payload_json) VALUES(?,?,?,?,?,?)',
            (record.id, voice_id, purpose, identity.sha256, record.relative_path, record.model_dump_json()))
    return record


def require_training_sources(voice_id: str, source_filenames: list[str]) -> None:
    voice = _voice_root(voice_id)
    if not 1 <= len(source_filenames) <= MAX_SOURCE_FILES or len(set(source_filenames)) != len(source_filenames):
        raise RecordingError('recording_invalid_sources')
    opposite = _opposite_hashes(voice, 'training')
    known = list_sessions(voice_id)
    for filename in source_filenames:
        if Path(filename).name != filename or '\\' in filename:
            raise RecordingError('recording_invalid_path')
        source = _path(voice / 'recordings' / filename, _purpose_root(voice, 'training'))
        identity = _identity(source)
        previous = next((item for item in known if item.relative_path == f'recordings/{filename}'), None)
        if previous is not None and previous.original_sha256 != identity.sha256:
            raise RecordingError('recording_source_changed', 409)
        _require_purpose(voice_id, identity.sha256, 'training', opposite)


def require_held_out_file(voice_id: str, path: Path) -> None:
    voice = _voice_root(voice_id)
    source = _path(path, _purpose_root(voice, 'held_out'))
    _require_purpose(voice_id, _identity(source).sha256, 'held_out', _opposite_hashes(voice, 'held_out'))


def recording_script() -> RecordingScript:
    return RecordingScript()
