"""Atomic project publication and pinned exact audio use a temporary library."""
from __future__ import annotations
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app import beat_jobs, beat_projects, beat_render, beat_store, db
from app.beat_contracts import BeatCreateRequest, BeatError, BeatJobRequest, canonical_json
from app.beat_project_contracts import MusicProjectProvenance, RenderedProjectLane


class MusicProjectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, value in (('_db', None), ('DATA_DIR', self.root), ('FILES_DIR', self.root / 'files'),
                ('DB_PATH', self.root / 'catalog.db')):
            self.enterContext(patch.object(db, name, value))
        for name, value in (('_tasks', {}), ('_processes', {}), ('_unverified', {}),
                ('_closing', False), ('_lock', asyncio.Lock()), ('_semaphore', asyncio.Semaphore(1))):
            self.enterContext(patch.object(beat_jobs, name, value))
        self.connection = db.get_db()
        beat_projects.migrate(self.connection)

    async def asyncTearDown(self) -> None:
        await beat_jobs.shutdown()
        self.connection.close()

    def built(self, title: str = 'Project'):
        provenance = MusicProjectProvenance(workflow='mashup', workflow_id='a' * 32,
            workflow_revision=0, source_manifest_sha256='b' * 64)
        lanes = [RenderedProjectLane(lane_id='drums', label='Drums', artifact_id='c' * 32,
            sha256='d' * 64, source_url='/api/beats/renders/' + 'a' * 32 + '/artifacts/' + 'c' * 32,
            trim_end_sec=2)]
        return beat_projects.build_audio_project(title, 120, lanes, provenance)

    async def test_generic_builder_is_deterministic_and_publication_is_atomic_idempotent(self) -> None:
        built = self.built()
        self.assertEqual(built, self.built())
        self.connection.execute("CREATE TRIGGER fail_publication BEFORE INSERT ON music_project_publications BEGIN SELECT RAISE(ABORT,'simulated interruption'); END")
        with self.assertRaises(sqlite3.Error):
            beat_projects.publish_audio_project(built, 'mashup:' + 'a' * 32)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM projects').fetchone()[0], 0)
        self.connection.execute('DROP TRIGGER fail_publication')
        identifier = beat_projects.publish_audio_project(built, 'mashup:' + 'a' * 32)
        self.assertEqual(beat_projects.publish_audio_project(self.built(), 'mashup:' + 'a' * 32), identifier)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM projects').fetchone()[0], 1)
        with self.assertRaises(BeatError) as conflict:
            beat_projects.publish_audio_project(self.built('Different'), 'mashup:' + 'a' * 32)
        self.assertEqual(conflict.exception.code, 'beat_submission_conflict')

    async def test_migration_leaves_legacy_json_byte_for_byte_and_refuses_future_versions(self) -> None:
        raw = '{"version":1,"legacy_custom":{"unknown":true}}'
        self.connection.execute("INSERT INTO projects(created_at,updated_at,name,data_json) VALUES('now','now','Legacy',?)", (raw,))
        self.connection.commit()
        beat_projects.migrate(self.connection)
        self.assertEqual(self.connection.execute('SELECT data_json FROM projects').fetchone()[0], raw)
        self.connection.execute('UPDATE music_project_schema_version SET version=99')
        with self.assertRaises(BeatError):
            beat_projects.migrate(self.connection)
        self.assertEqual(self.connection.execute('SELECT version FROM music_project_schema_version').fetchone()[0], 99)

    async def test_beat_handoff_uses_verified_stems_retained_version_and_pinned_revision(self) -> None:
        document = beat_store.create_document(BeatCreateRequest(title='Pinned beat'))
        request = BeatJobRequest(beat_id=document.id, expected_revision=0, mode='full', submission_key='a' * 32)
        job = await beat_jobs.start(request)
        task = beat_jobs._tasks.get(job.id)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), 20)
        publication = await beat_projects.publish_beat_render(job.id)
        repeated = await beat_projects.publish_beat_render(job.id)
        self.assertEqual(publication, repeated)
        row = db.get_project(publication.project_id)
        self.assertIsNotNone(row)
        if row is None:
            self.fail('Expected project')
        data = json.loads(row['data_json'])
        self.assertEqual(data['bpm'], 120)
        self.assertEqual(len(data['lanes']), 8)
        manifest = beat_render.validate_output(job.id)
        self.assertEqual(publication.provenance.source_manifest_sha256, hashlib.sha256(canonical_json(manifest)).hexdigest())
        self.assertEqual(len(publication.provenance.sources), 1)
        for lane in data['lanes']:
            clip = lane['clips'][0]
            self.assertEqual((clip['timelineStart'], clip['trimStart'], clip['trimEnd']), (0, 0, manifest.frames / 48000))
            self.assertEqual(clip['beatLink']['beat_revision'], 0)
            self.assertIsNone(clip['beatLink']['occurrence_index'])
            artifact = next(item for item in manifest.artifacts if item.id == clip['sourceArtifact']['artifact_id'])
            self.assertEqual(clip['sourceArtifact']['sha256'], artifact.sha256)
            self.assertEqual(lane['settings']['volume'], 1)
        original = row['data_json']
        beat_store.update(document.id, 0, document.model_copy(update={'title': 'New beat revision'}))
        await beat_projects.publish_beat_render(job.id)
        self.assertEqual(db.get_project(publication.project_id)['data_json'], original)


if __name__ == '__main__':
    unittest.main()
