from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
import httpx

from app import audio_exports, db, music_analysis
from app import audio_exports_test as export_test_support
from app.api import routes_stems
from app.api.routes_tracks import router
from app.music_analysis_contracts import MusicAnalysisRequest, StemMusicSource


class AnalysisSourceOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_stem_mutation_holds_analysis_protection_for_its_entire_body(self) -> None:
        events: list[str] = []

        @asynccontextmanager
        async def protect(track_id: int) -> AsyncIterator[None]:
            self.assertEqual(track_id, 42)
            self.assertTrue(audio_exports._lock.locked())
            events.append('protected')
            try:
                yield
            finally:
                events.append('released')

        with patch.object(music_analysis, 'protect_stem_replacement', side_effect=protect), \
                patch.object(audio_exports, '_running', {}), patch.object(audio_exports, '_deleting', set()), \
                patch.object(audio_exports, '_stem_deleting', set()):
            async with audio_exports.protect_stem_replacement(42):
                events.append('mutation')
        self.assertEqual(events, ['protected', 'mutation', 'released'])

    async def test_unverified_analysis_blocks_track_deletion_before_other_cleanup(self) -> None:
        @asynccontextmanager
        async def blocked(track_id: int) -> AsyncIterator[None]:
            raise music_analysis.MusicAnalysisError('worker_unverified')
            yield

        application = FastAPI()
        application.include_router(router)
        with patch.object(music_analysis, 'protect_track_removal', side_effect=blocked), \
                patch('app.reference_imports.protect_track_removal') as references, \
                patch('app.db.delete_track') as delete:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(application), base_url='http://127.0.0.1:9000') as client:
                response = await client.delete('/api/tracks/42')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {'detail': 'worker_unverified'})
        references.assert_not_called()
        delete.assert_not_called()


class AnalysisStemExportPreservationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = export_test_support.AudioExportTests.asyncSetUp
    asyncTearDown = export_test_support.AudioExportTests.asyncTearDown

    def make_stem(self) -> Path:
        source = db.stems_dir('ace_step', self.track_id) / 'vocals.wav'
        source.parent.mkdir(parents=True)
        source.write_bytes(self.source.read_bytes())
        db.update_track_stems(self.track_id, {'vocals': str(source)})
        return source

    async def test_failed_analysis_drain_preserves_source_catalog_and_completed_export(self) -> None:
        source = self.make_stem()
        export = await audio_exports.create_stem_export(self.track_id, 'vocals', 'mp3')
        await asyncio.wait_for(audio_exports._tasks[export.id], 15)
        completed = audio_exports.stem_export_file(self.track_id, 'vocals', export.id)
        source_bytes, export_bytes = source.read_bytes(), completed.read_bytes()

        @asynccontextmanager
        async def blocked(track_id: int) -> AsyncIterator[None]:
            self.assertTrue(audio_exports._lock.locked())
            raise music_analysis.MusicAnalysisError('worker_unverified')
            yield

        with patch.object(music_analysis, 'protect_stem_replacement', side_effect=blocked):
            with self.assertRaises(audio_exports.AudioExportError) as error:
                async with audio_exports.protect_stem_exports_removal(self.track_id):
                    self.fail('An unverified source reader permitted stem deletion')
        self.assertEqual(error.exception.code, 'worker_unverified')
        self.assertEqual(source.read_bytes(), source_bytes)
        self.assertIsNotNone(db.get_track(self.track_id)['stems_json'])
        self.assertEqual(completed.read_bytes(), export_bytes)
        self.assertEqual(audio_exports.get_stem_export(self.track_id, 'vocals', export.id).status, 'done')
        self.assertNotIn(self.track_id, audio_exports._stem_deleting)

    async def test_analysis_drain_precedes_source_deletion_and_blocks_new_source_readers(self) -> None:
        source = self.make_stem()
        reference = StemMusicSource(track_id=self.track_id, stem='vocals', sha256=audio_exports._digest(source))
        entered, release = asyncio.Event(), asyncio.Event()

        async def drain(identifier: str) -> None:
            self.assertEqual(identifier, 'a' * 32)
            self.assertTrue(audio_exports._lock.locked())
            entered.set()
            await release.wait()

        with patch.object(music_analysis, '_sources', {'a' * 32: reference}), \
                patch.object(music_analysis, '_lock', asyncio.Lock()), \
                patch.object(music_analysis, '_replacing_stems', set()), \
                patch.object(music_analysis, '_deleting', set()), \
                patch.object(music_analysis, '_closing', False), \
                patch.object(music_analysis, 'cancel', new=AsyncMock(side_effect=drain)) as cancel:
            deleting = asyncio.create_task(routes_stems.delete_stems(self.track_id))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                self.assertTrue(source.is_file())
                self.assertIsNotNone(db.get_track(self.track_id)['stems_json'])
                with self.assertRaises(music_analysis.MusicAnalysisError) as error:
                    await music_analysis.start(MusicAnalysisRequest(source=reference))
                self.assertEqual(error.exception.code, 'analysis_busy')
                release.set()
                await asyncio.wait_for(deleting, 1)
                cancel.assert_awaited_once_with('a' * 32)
            finally:
                release.set()
                await asyncio.gather(deleting, return_exceptions=True)
        self.assertFalse(source.exists())
        self.assertIsNone(db.get_track(self.track_id)['stems_json'])
