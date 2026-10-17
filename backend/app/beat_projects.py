"""Typed audio project construction and atomic workflow publication.

Legacy projects remain opaque. Only newly constructed documents use this
bounded schema; repeated handoff never overwrites the user's editor changes.
"""
from __future__ import annotations
from collections.abc import Sequence
import hashlib
import re
import sqlite3

from pydantic import ValidationError
import soundfile as sf

from . import beat_jobs, beat_render, beat_store, db
from .beat_contracts import BeatError, canonical_json
from .beat_project_contracts import (BeatClipLink, BuiltMusicProject, EditorAudioClip,
    EditorLane, EditorProjectDocument, MusicProjectArtifact, MusicProjectProvenance,
    MusicProjectPublication, MusicProjectSource, RenderedProjectLane)
from .job_lifecycle import run_owned_thread


def migrate(connection: sqlite3.Connection) -> None:
    existing = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='music_project_schema_version'").fetchone()
    if existing is not None:
        row = connection.execute('SELECT version FROM music_project_schema_version WHERE id=1').fetchone()
        if row is not None and row[0] > 1:
            raise BeatError('beat_storage_unavailable')
    connection.execute('SAVEPOINT music_project_migration')
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS music_project_schema_version(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL CHECK(version>=1))')
        connection.execute('CREATE TABLE IF NOT EXISTS music_project_publications(idempotency_key TEXT PRIMARY KEY,request_sha256 TEXT NOT NULL,project_id INTEGER NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,provenance_json TEXT NOT NULL CHECK(json_valid(provenance_json)))')
        connection.execute('INSERT OR IGNORE INTO music_project_schema_version(id,version) VALUES(1,1)')
        connection.execute('RELEASE music_project_migration')
    except BaseException:
        connection.execute('ROLLBACK TO music_project_migration')
        connection.execute('RELEASE music_project_migration')
        raise


def build_audio_project(title: str, bpm: float, lanes: Sequence[RenderedProjectLane],
    provenance: MusicProjectProvenance) -> BuiltMusicProject:
    """Pure deterministic builder for backend-verified retained audio artifacts."""
    if len(lanes) > 4096:
        raise BeatError('beat_render_limit')
    groups: dict[str, EditorLane] = {}
    try:
        provenance = MusicProjectProvenance.model_validate(provenance.model_dump())
        for index, supplied in enumerate(lanes):
            lane = RenderedProjectLane.model_validate(supplied.model_dump())
            clip_id = hashlib.sha256(canonical_json(lane) + canonical_json(provenance)
                + str(index).encode('ascii')).hexdigest()[:32]
            clip = EditorAudioClip(id=clip_id, sourceUrl=lane.source_url, sourceLabel=lane.label,
                timelineStart=lane.start_sec, trimStart=lane.trim_start_sec, trimEnd=lane.trim_end_sec,
                fadeInDuration=lane.fade_in_sec, fadeOutDuration=lane.fade_out_sec, originalBpm=bpm,
                muted=lane.muted, beatLink=lane.beat_link,
                sourceArtifact=MusicProjectArtifact(artifact_id=lane.artifact_id, sha256=lane.sha256))
            if lane.lane_id not in groups:
                groups[lane.lane_id] = EditorLane(id=lane.lane_id, name=lane.label, clips=[], settings=lane.settings)
            elif groups[lane.lane_id].settings != lane.settings or groups[lane.lane_id].name != lane.label:
                raise BeatError('beat_pattern_invalid')
            groups[lane.lane_id].clips.append(clip)
        document = EditorProjectDocument(lanes=list(groups.values()), bpm=bpm, musicProvenance=provenance)
        return BuiltMusicProject(name=title, data=document, provenance=provenance)
    except ValidationError as exc:
        raise BeatError('beat_pattern_invalid') from exc


def publish_audio_project(built: BuiltMusicProject, idempotency_key: str) -> int:
    """Insert editor row and unique publication mapping in one transaction."""
    if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', idempotency_key) is None:
        raise BeatError('beat_pattern_invalid')
    try:
        built = BuiltMusicProject.model_validate_json(built.model_dump_json())
    except ValidationError as exc:
        raise BeatError('beat_pattern_invalid') from exc
    content = canonical_json(built)
    if len(content) > 8 * 1024 * 1024:
        raise BeatError('beat_render_limit')
    checksum = hashlib.sha256(content).hexdigest()
    connection = db.get_db()
    connection.execute('BEGIN IMMEDIATE')
    try:
        existing = connection.execute('SELECT request_sha256,project_id FROM music_project_publications WHERE idempotency_key=?',
            (idempotency_key,)).fetchone()
        if existing is not None:
            if existing['request_sha256'] != checksum:
                raise BeatError('beat_submission_conflict')
            raw_id: object = existing['project_id']
            if not isinstance(raw_id, int) or isinstance(raw_id, bool):
                raise BeatError('beat_storage_unavailable')
            connection.commit()
            return raw_id
        timestamp = beat_store.now()
        cursor = connection.execute('INSERT INTO projects(created_at,updated_at,name,data_json) VALUES(?,?,?,?)',
            (timestamp, timestamp, built.name, canonical_json(built.data).decode('ascii')))
        identifier = cursor.lastrowid
        if identifier is None:
            raise BeatError('beat_storage_unavailable')
        connection.execute('INSERT INTO music_project_publications(idempotency_key,request_sha256,project_id,provenance_json) VALUES(?,?,?,?)',
            (idempotency_key, checksum, identifier, canonical_json(built.provenance).decode('ascii')))
        connection.commit()
        return identifier
    except BaseException:
        connection.rollback()
        raise


async def publish_beat_render(render_id: str) -> MusicProjectPublication:
    job = beat_jobs.get(render_id)
    if job.status != 'done' or job.request.mode != 'full' or job.manifest is None:
        raise BeatError('beat_save_failed')
    manifest = await run_owned_thread(beat_render.validate_output, render_id)
    if canonical_json(manifest) != canonical_json(job.manifest):
        raise BeatError('beat_sample_changed')
    document = beat_store.get_revision(job.beat_id, job.beat_revision)
    if hashlib.sha256(canonical_json(document)).hexdigest() != manifest.document_sha256:
        raise BeatError('beat_sample_changed')
    mixes = [item for item in manifest.artifacts if item.kind == 'mix']
    if len(mixes) != 1:
        raise BeatError('beat_save_failed')
    source = beat_jobs.resolve_render_source(job.beat_id, job.beat_revision, render_id, mixes[0].sha256)
    provenance = MusicProjectProvenance(workflow='beat', workflow_id=job.beat_id,
        workflow_revision=job.beat_revision, source_manifest_sha256=hashlib.sha256(canonical_json(manifest)).hexdigest(),
        sources=[MusicProjectSource(track_id=source.track_id, version_id=source.version_id, sha256=source.sha256)])
    stems = {item.lane_id: item for item in manifest.artifacts if item.kind == 'stem'}
    if len(stems) != len(document.patterns[0].lanes):
        raise BeatError('beat_save_failed')
    lanes: list[RenderedProjectLane] = []
    for authored in document.patterns[0].lanes:
        artifact = stems.get(authored.lane_id)
        if artifact is None:
            raise BeatError('beat_save_failed')
        path = await run_owned_thread(beat_render.resolve_artifact, render_id, artifact)
        info = await run_owned_thread(sf.info, path)
        if (info.samplerate, info.channels, info.frames, info.subtype) != (48000, 2, manifest.frames, 'FLOAT'):
            raise BeatError('beat_sample_changed')
        lanes.append(RenderedProjectLane(lane_id=authored.lane_id, label=authored.label,
            artifact_id=artifact.id, sha256=artifact.sha256,
            source_url=f'/api/beats/renders/{render_id}/artifacts/{artifact.id}',
            trim_end_sec=manifest.frames / 48000, muted=authored.muted,
            beat_link=BeatClipLink(beat_id=job.beat_id, beat_revision=job.beat_revision,
                render_id=render_id, lane_id=authored.lane_id)))
    built = build_audio_project(document.title, document.bpm, lanes, provenance)
    identifier = publish_audio_project(built, 'beat-render:' + render_id)
    return MusicProjectPublication(project_id=identifier, provenance=provenance)
