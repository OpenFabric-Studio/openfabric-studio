"""Immutable confirmed ranges; observed occupancy can suggest, never set, bounds."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from pydantic import ValidationError

from .singer_range_contracts import (OutOfRangeNote, PlannedNote, RangeAnalysisStatus, RangeApproval, RangeAssessment, RangeDecision,
    SingerRangeProfile, SingerRangeRequest)
from .voice_contracts import VoiceCoverageMeasurement, VoiceCoverageSummary
from .voice_coverage import summarize_coverage

logger = logging.getLogger(__name__)


class SingerRangeError(Exception):
    def __init__(self, code: str, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE IF NOT EXISTS singer_range_profiles (
        id TEXT PRIMARY KEY CHECK(length(id)=32 AND id NOT GLOB '*[^0-9a-f]*'),
        artist_id TEXT NOT NULL REFERENCES artist_profiles(id),
        revision INTEGER NOT NULL CHECK(revision>=1),
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), UNIQUE(artist_id,revision))''')
    connection.execute('''CREATE TRIGGER IF NOT EXISTS singer_range_no_update
        BEFORE UPDATE ON singer_range_profiles BEGIN SELECT RAISE(ABORT,'singer_range_immutable'); END''')
    connection.execute('''CREATE TRIGGER IF NOT EXISTS singer_range_no_delete
        BEFORE DELETE ON singer_range_profiles BEGIN SELECT RAISE(ABORT,'singer_range_immutable'); END''')
    connection.commit()


def _parse(row: sqlite3.Row) -> SingerRangeProfile:
    raw: object = row['payload_json']
    try:
        if not isinstance(raw, str):
            raise ValueError('invalid_range_payload')
        result = SingerRangeProfile.model_validate_json(raw)
        if (result.id, result.artist_id, result.revision) != (row['id'], row['artist_id'], row['revision']):
            raise ValueError('invalid_range_identity')
        return result
    except (ValueError, ValidationError) as exc:
        raise SingerRangeError('singer_range_metadata_invalid') from exc


@contextmanager
def _transaction() -> Iterator[sqlite3.Connection]:
    from . import db
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
        logger.exception('Singer range write failed')
        raise SingerRangeError('singer_range_storage_failed', 503) from exc
    finally:
        if connection is not None:
            connection.close()


def get_profile(artist_id: str) -> SingerRangeProfile | None:
    from . import artists, db
    artists.get_artist(artist_id)
    try:
        row = db.get_db().execute('SELECT * FROM singer_range_profiles WHERE artist_id=? ORDER BY revision DESC LIMIT 1', (artist_id,)).fetchone()
        return _parse(row) if row is not None else None
    except sqlite3.Error as exc:
        logger.exception('Singer range read failed')
        raise SingerRangeError('singer_range_storage_failed', 503) from exc


def save_profile(artist_id: str, request: SingerRangeRequest) -> SingerRangeProfile:
    from . import artists
    artists.get_artist(artist_id)
    request = SingerRangeRequest.model_validate_json(request.model_dump_json())
    with _transaction() as connection:
        row = connection.execute('SELECT * FROM singer_range_profiles WHERE artist_id=? ORDER BY revision DESC LIMIT 1', (artist_id,)).fetchone()
        previous = _parse(row) if row is not None else None
        profile = SingerRangeProfile(**request.model_dump(), id=uuid.uuid4().hex, artist_id=artist_id,
            revision=previous.revision + 1 if previous is not None else 1, created_at=datetime.now(timezone.utc).isoformat())
        connection.execute('INSERT INTO singer_range_profiles(id,artist_id,revision,payload_json) VALUES(?,?,?,?)',
            (profile.id, artist_id, profile.revision, profile.model_dump_json()))
    return profile


def coverage_notes(measurement: VoiceCoverageMeasurement, start_sec: float) -> list[PlannedNote]:
    return [PlannedNote(start_sec=start_sec, end_sec=start_sec + measurement.duration_sec, midi_note=bin.midi_note,
        timing_basis='observed_window', observed_seconds=bin.seconds) for bin in measurement.pitch_bins if bin.seconds > 0]


def evaluate_range(artist_id: str, profile: SingerRangeProfile | None, notes: list[PlannedNote], *,
                   approval: RangeApproval | None = None, analysis_complete: bool = True,
                   coverage: VoiceCoverageSummary | None = None, source_sha256: str | None = None,
                   chosen_key: str | None = None, error_code: str = '') -> RangeAssessment:
    """Pure worker evaluation; intervals for observed bins are windows, not note onsets."""
    if len(notes) > 100000 or (profile is not None and profile.artist_id != artist_id):
        raise SingerRangeError('singer_range_invalid_notes')
    warnings: set[str] = set(coverage.warnings if coverage is not None else [])
    if profile is None:
        warnings.add('range_profile_missing')
    if not notes:
        warnings.add('no_reliable_pitch')
    if not analysis_complete:
        warnings.add('analysis_incomplete')
    observed = any(note.timing_basis == 'observed_window' for note in notes)
    if observed:
        warnings.update({'observed_pitch_is_fallible', 'observed_pitch_is_not_full_vocal_range'})
    status: RangeAnalysisStatus = 'observed' if profile is not None and notes and analysis_complete else 'unknown'
    policy = profile.policy if profile is not None else 'needs_approval'
    key = chosen_key if chosen_key is not None else profile.chosen_key if profile is not None else ''
    if key:
        warnings.add('chosen_key_does_not_transpose_audio')
    identity = {'artist_id': artist_id, 'profile_version_id': profile.id if profile is not None else None,
        'notes': [note.model_dump(mode='json') for note in notes], 'analysis_status': status,
        'source_sha256': source_sha256, 'chosen_key': key}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    outside: list[OutOfRangeNote] = []
    if profile is not None:
        for note in notes:
            distance = max(profile.comfortable_low_midi - note.midi_note, note.midi_note - profile.comfortable_high_midi, 0)
            if distance:
                extended = None if profile.extended_low_midi is None or profile.extended_high_midi is None else not profile.extended_low_midi <= note.midi_note <= profile.extended_high_midi
                outside.append(OutOfRangeNote(**note.model_dump(), distance_semitones=distance, outside_extended_range=extended))
    applied = False
    decision: RangeDecision = 'allow'
    if outside:
        warnings.add('out_of_comfortable_range')
        if any(note.outside_extended_range for note in outside):
            warnings.add('out_of_extended_range')
    if outside or status == 'unknown':
        decision = 'blocked' if policy == 'block' else 'needs_approval' if policy == 'needs_approval' else 'allow'
        if decision == 'needs_approval':
            warnings.add('approval_required')
            applied = bool(status == 'observed' and profile is not None and approval is not None
                and approval.profile_version_id == profile.id and approval.assessment_sha256 == digest)
            if applied:
                decision = 'allow'
                warnings.discard('approval_required')
            elif approval is not None:
                warnings.add('approval_does_not_match_assessment')
    occupied = [bin.midi_note for bin in coverage.pitch_bins if bin.seconds > 0] if coverage is not None else []
    return RangeAssessment(artist_id=artist_id, profile_version_id=profile.id if profile is not None else None,
        policy=policy, analysis_status=status, decision=decision, assessment_sha256=digest, source_sha256=source_sha256,
        chosen_key=key, notes_evaluated=len(notes), out_of_range_notes=outside,
        observed_low_midi=min(occupied) if occupied else None, observed_high_midi=max(occupied) if occupied else None,
        observed_reliable_voiced_sec=coverage.reliable_voiced_sec if coverage is not None else 0,
        approval_applied=applied, warnings=sorted(warnings), error_code=error_code)


def assess_notes(artist_id: str, notes: list[PlannedNote], approval: RangeApproval | None = None) -> RangeAssessment:
    return evaluate_range(artist_id, get_profile(artist_id), notes, approval=approval)


def assess_coverage(artist_id: str, measurement: VoiceCoverageMeasurement, *, start_sec: float = 0,
                    approval: RangeApproval | None = None) -> RangeAssessment:
    return evaluate_range(artist_id, get_profile(artist_id), coverage_notes(measurement, start_sec),
        approval=approval, coverage=summarize_coverage([measurement]))
