"""Beat API controls authoring explicitly; reads never start rendering."""
from __future__ import annotations

import asyncio
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

from fastapi import FastAPI
import httpx

from app import beat_jobs, beat_projects, beat_render, beat_store, db
from app.api import routes_beats
from app.beat_contracts import canonical_json
from app.beat_project_contracts import MusicProjectProvenance, MusicProjectPublication


class BeatRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, value in (('_db', None), ('DATA_DIR', self.root), ('FILES_DIR', self.root / 'files'),
                ('DB_PATH', self.root / 'catalog.db')):
            self.enterContext(patch.object(db, name, value))
        self.enterContext(patch.object(beat_jobs, '_closing', False))
        self.connection = db.get_db()
        application = FastAPI()
        application.include_router(routes_beats.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
            base_url='http://127.0.0.1:9000')
        self.addAsyncCleanup(self.client.aclose)

    async def asyncTearDown(self) -> None:
        self.connection.close()

    async def test_create_edit_restore_cas_and_reads_never_render(self) -> None:
        with patch.object(beat_jobs, 'start', new=AsyncMock()) as start:
            created = await self.client.post('/api/beats', json={'title': 'A beat'})
            self.assertEqual(created.status_code, 201, created.text)
            document = created.json()
            identifier = document['id']
            self.assertEqual((await self.client.get('/api/beats')).json()['documents'][0]['id'], identifier)
            self.assertEqual((await self.client.get('/api/beats/' + identifier)).json(), document)
            updated = await self.client.patch('/api/beats/' + identifier,
                json={'expected_revision': 0, 'document': {**document, 'title': 'Edited'}})
            self.assertEqual(updated.status_code, 200, updated.text)
            stale = await self.client.patch('/api/beats/' + identifier,
                json={'expected_revision': 0, 'document': document})
            self.assertEqual(stale.json(), {'detail': 'beat_revision_conflict'})
            restored = await self.client.post('/api/beats/' + identifier + '/restore',
                json={'expected_revision': 1, 'revision': 0})
            self.assertEqual((restored.status_code, restored.json()['title'], restored.json()['revision']), (200, 'A beat', 2))
            self.assertEqual(len((await self.client.get('/api/beats/' + identifier + '/revisions')).json()['revisions']), 3)
            start.assert_not_awaited()

    async def test_candidate_save_rebases_identity_and_rejects_wrong_kit_hash(self) -> None:
        first = (await self.client.post('/api/beats', json={})).json()
        saved = await self.client.post('/api/beats', json={'document': {**first, 'revision': 99}})
        self.assertEqual(saved.status_code, 201, saved.text)
        self.assertNotEqual(saved.json()['id'], first['id'])
        self.assertEqual(saved.json()['revision'], 0)
        rejected = await self.client.post('/api/beats', json={'document': {**first, 'kit_manifest_sha256': 'f' * 64}})
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertEqual(len((await self.client.get('/api/beats')).json()['documents']), 2)

    async def test_boundary_rejects_external_origin_invalid_payloads_and_private_errors(self) -> None:
        external = await self.client.post('/api/beats', json={}, headers={'Origin': 'https://example.invalid'})
        self.assertEqual(external.status_code, 403)
        for body in ({'bpm': True}, {'title': 'x', 'path': '/private/secret'}, {'seed': 1.5}):
            response = await self.client.post('/api/beats', json=body)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(response.json(), {'detail': 'beat_pattern_invalid'})
        with patch.object(beat_store, 'get', side_effect=OSError('/private/secret')):
            response = await self.client.get('/api/beats/' + 'a' * 32)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'detail': 'beat_storage_unavailable'})
        huge = await self.client.post('/api/beats', content=b' ' * (8 * 1024 * 1024 + 1))
        self.assertEqual(huge.status_code, 413)

    async def test_kits_multipart_and_presets_use_static_routes(self) -> None:
        response = await self.client.get('/api/beats/kits')
        self.assertEqual(response.status_code, 200, response.text)
        kit = beat_render.load_kit('procedural-core')
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as zipped:
            for instrument in kit.instruments:
                for region in instrument.regions:
                    zipped.writestr(region.asset_id + '.wav', beat_render.resolve_sample(kit, region.asset_id).read_bytes())
        imported = await self.client.post('/api/beats/kits/import', files={
            'manifest': ('kit.json', canonical_json(kit), 'application/json'),
            'archive': ('kit.zip', archive.getvalue(), 'application/zip')})
        self.assertEqual(imported.status_code, 200, imported.text)
        presets = await self.client.get('/api/beats/presets')
        self.assertEqual(presets.status_code, 200, presets.text)
        self.assertIn('presets', presets.json())

    async def test_editor_handoff_calls_atomic_owner_and_accepts_no_untrusted_document(self) -> None:
        publication = MusicProjectPublication(project_id=12, provenance=MusicProjectProvenance(workflow='beat',
            workflow_id='a' * 32, workflow_revision=0, source_manifest_sha256='b' * 64))
        with patch.object(beat_projects, 'publish_beat_render', new=AsyncMock(return_value=publication)) as publish:
            response = await self.client.post('/api/beats/renders/' + 'c' * 32 + '/project')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['project_id'], 12)
            publish.assert_awaited_once_with('c' * 32)
            invalid = await self.client.post('/api/beats/renders/' + 'c' * 32 + '/project', json={'data': {'sourceUrl': '/private'}})
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(publish.await_count, 1)


if __name__ == '__main__':
    unittest.main()
