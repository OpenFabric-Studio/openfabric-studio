"""Stem exports preserve source identity and share owned export cleanup."""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

from app import audio_exports as exports, db
from app import audio_exports_test as export_test_support


class StemExportTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = export_test_support.AudioExportTests.asyncSetUp
    asyncTearDown = export_test_support.AudioExportTests.asyncTearDown

    def make_source(self) -> Path:
        path = db.FILES_DIR / 'vocals.wav'
        path.write_bytes(self.source.read_bytes())
        db.update_track_stems(self.track_id, {'vocals': str(path)})
        return path

    async def test_advertised_stem_manifests_serve_exact_media_and_reject_wrong_owner(self) -> None:
        import httpx
        from fastapi import FastAPI
        from app.api import routes_audio_exports,routes_stem_exports
        from app import export_provenance
        from app.export_provenance_contracts import ExportProvenance
        self.make_source()
        job=await exports.create_stem_export(self.track_id,'vocals','mp3')
        await asyncio.wait_for(exports._tasks[job.id],15)
        done=exports.get_stem_export(self.track_id,'vocals',job.id)
        self.assertEqual(done.status,'done',done.error_code)
        app=FastAPI();app.include_router(routes_audio_exports.router);app.include_router(routes_stem_exports.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as client:
            for url in (done.provenance_url,done.manifest_url):
                if url is None:raise AssertionError('missing stem manifest URL')
                response=await client.get(url)
                self.assertEqual(response.status_code,200,response.text)
                self.assertIn(f'/stems/vocals/exports/{job.id}/',url)
                wrong=await client.get(url.replace('/stems/vocals/','/stems/drums/'))
                self.assertEqual(wrong.status_code,404)
            if done.provenance_url is None:raise AssertionError('missing stem JSON URL')
            proof=ExportProvenance.model_validate_json((await client.get(done.provenance_url)).content)
            self.assertEqual(proof.artifact_sha256,export_provenance.digest(exports.stem_export_file(self.track_id,'vocals',job.id)))
            self.assertEqual(proof.subject,'stem')
            media=exports.stem_export_file(self.track_id,'vocals',job.id)
            media.write_bytes(b'tampered')
            self.assertEqual((await client.get(done.provenance_url)).status_code,409)

    async def test_stem_mp3_uses_exact_stem_and_survives_restart(self) -> None:
        source = self.make_source()
        job = await exports.create_stem_export(self.track_id, 'vocals', 'mp3')
        await asyncio.wait_for(exports._tasks[job.id], 15)
        done = exports.get_stem_export(self.track_id, 'vocals', job.id)
        self.assertEqual(done.status, 'done')
        self.assertEqual(done.stem_name, 'vocals')
        self.assertNotIn('version_id', done.model_dump())
        self.assertEqual(exports.load_document(self.track_id, job.id).source_relative, str(source.relative_to(db.FILES_DIR)))
        self.assertTrue(exports.stem_export_file(self.track_id, 'vocals', job.id).is_file())
        self.assertEqual(exports.list_exports(self.track_id, self.version_id).exports, [])
        await exports.recover_exports()
        self.assertEqual(exports.get_stem_export(self.track_id, 'vocals', job.id).status, 'done')

    async def test_missing_unknown_and_escaped_sources_are_rejected(self) -> None:
        for name in ('vocals', '../../private'):
            with self.assertRaises(exports.AudioExportError):
                await exports.create_stem_export(self.track_id, name, 'mp3')
        db.update_track_stems(self.track_id, {'vocals': str(self.root.parent / 'outside.wav')})
        with self.assertRaises(exports.AudioExportError):
            await exports.create_stem_export(self.track_id, 'vocals', 'mp3')

    async def test_delete_stems_drains_encoding_and_prevents_new_exports(self) -> None:
        self.make_source()
        entered = asyncio.Event()
        async def pending(document: exports.ExportDocument) -> None:
            entered.set()
            try:
                await asyncio.Future()
            finally:
                document.status = 'cancelled'
                exports.save_document(document)
        with patch.object(exports, '_run', side_effect=pending):
            job = await exports.create_stem_export(self.track_id, 'vocals', 'mp3')
            await entered.wait()
            async with exports.protect_stem_exports_removal(self.track_id):
                with self.assertRaises(exports.AudioExportError):
                    await exports.create_stem_export(self.track_id, 'vocals', 'mp3')
                self.assertTrue(exports._tasks.get(job.id) is None)
        self.assertEqual(exports.list_stem_exports(self.track_id, 'vocals').exports, [])
