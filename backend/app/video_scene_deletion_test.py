"""Per-scene removal never touches a real movie or the user's library."""
from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app import video_scenes as scenes
from app.api.routes_video_scenes import router
from app.video_scene_contracts import VideoSceneCut, VideoSceneSource


class SceneDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.library = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(scenes, 'DATA_DIR', self.library))
        self.enterContext(patch.object(scenes, '_closing', False))
        app = FastAPI()
        app.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:9000')
        self.addAsyncCleanup(self.client.aclose)
        self.source_id = 'a' * 32
        self.cut_id = 'b' * 32
        self.source_url = '/api/videos/scenes/sources/' + self.source_id
        self.url = self.source_url + '/cuts/' + self.cut_id
        folder = scenes.directory(self.source_id)
        folder.mkdir(parents=True)
        self.original = folder / 'source.video'
        self.original.write_bytes(b'original movie preserved')
        self.original_bytes = self.original.read_bytes()
        source = VideoSceneSource(id=self.source_id, name='movie.mp4', origin='upload', duration_sec=100,
            width=96, height=64, fps=24, has_audio=True, bytes=len(self.original_bytes), created_at=scenes.now())
        scenes._write(folder / 'source.json', scenes.StoredSource(source=source, path='source.video',
            input_format='mov', signature=scenes._signature(self.original)))
        (folder / 'previews').mkdir()
        (folder / 'previews' / 'keep').write_bytes(b'preview preserved')
        self.fixture(self.cut_id)
        self.fixture('c' * 32)

    def fixture(self, identifier: str) -> VideoSceneCut:
        folder = scenes.cut_directory(self.source_id, identifier)
        folder.mkdir(parents=True, exist_ok=True)
        output = b'saved MP4 scene'
        cut = VideoSceneCut(id=identifier, source_id=self.source_id, name='Scene', start_sec=2, end_sec=4,
            seconds=2, status='ready', created_at=scenes.now(), updated_at=scenes.now(), width=96,
            height=64, has_audio=True, output_url=f'{self.source_url}/cuts/{identifier}/media',
            output_sha256=hashlib.sha256(output).hexdigest(), bytes=len(output))
        scenes.save_cut(cut)
        (folder / 'cut.mp4').write_bytes(output)
        return cut

    async def test_ready_scene_removal_preserves_source_previews_and_other_scenes(self) -> None:
        response = await self.client.delete(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'source_id': self.source_id, 'deleted': self.cut_id})
        self.assertEqual(self.original.read_bytes(), self.original_bytes)
        self.assertEqual((scenes.directory(self.source_id) / 'previews/keep').read_bytes(), b'preview preserved')
        self.assertTrue((scenes.cut_directory(self.source_id, 'c' * 32) / 'cut.mp4').is_file())
        self.assertFalse(scenes.cut_directory(self.source_id, self.cut_id).exists())
        listed = await self.client.get(self.source_url + '/cuts')
        self.assertEqual([cut['id'] for cut in listed.json()['cuts']], ['c' * 32])
        self.assertEqual((await self.client.get(self.url)).status_code, 404)
        self.assertEqual((await self.client.get(self.url + '/media')).status_code, 404)
        self.assertEqual((await self.client.delete(self.url)).status_code, 404)

    async def test_failed_and_cancelled_cuts_can_be_removed(self) -> None:
        for status in ('failed', 'cancelled'):
            cut = VideoSceneCut.model_validate({**scenes.get_cut(self.source_id, self.cut_id).model_dump(), 'status': status})
            scenes.save_cut(cut)
            response = await self.client.delete(self.url)
            self.assertEqual(response.status_code, 200)
            self.fixture(self.cut_id)

    async def test_active_states_are_rejected_and_media_is_retained(self) -> None:
        for status in ('queued', 'cutting'):
            cut = VideoSceneCut.model_validate({**scenes.get_cut(self.source_id, self.cut_id).model_dump(), 'status': status})
            scenes.save_cut(cut)
            response = await self.client.delete(self.url)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json(), {'detail': 'busy'})
            self.assertTrue((scenes.cut_directory(self.source_id, self.cut_id) / 'cut.mp4').exists())

    async def test_registered_cut_owner_blocks_inactive_record(self) -> None:
        task = asyncio.create_task(asyncio.sleep(30))
        try:
            with patch.dict(scenes._cuts, {self.cut_id: task}):
                response = await self.client.delete(self.url)
                self.assertEqual(response.status_code, 409)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(scenes.cut_directory(self.source_id, self.cut_id).exists())

    async def test_unresolved_worker_evidence_prevents_removal(self) -> None:
        folder = scenes.cut_directory(self.source_id, self.cut_id)
        ownership = folder / 'worker.json'
        ownership.write_bytes(b'unknown worker evidence')
        response = await self.client.delete(self.url)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'detail': 'worker_unresolved'})
        self.assertEqual(ownership.read_bytes(), b'unknown worker evidence')

    async def test_cross_origin_deletion_is_rejected(self) -> None:
        response = await self.client.delete(self.url, headers={'Origin': 'https://outside.example'})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(scenes.cut_directory(self.source_id, self.cut_id).is_dir())

    async def test_mismatched_source_cannot_remove_another_movies_cut(self) -> None:
        response = await self.client.delete('/api/videos/scenes/sources/' + 'd' * 32 + '/cuts/' + self.cut_id)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(scenes.cut_directory(self.source_id, self.cut_id).is_dir())

    async def test_original_movie_need_not_be_available_to_remove_a_saved_scene(self) -> None:
        self.original.unlink()
        response = await self.client.delete(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(scenes.get_cut(self.source_id, 'c' * 32).status, 'ready')

    async def test_native_movie_outside_the_scene_library_is_preserved(self) -> None:
        movie = (self.library / 'native.mp4').resolve()
        movie.write_bytes(self.original_bytes)
        document = scenes._source(self.source_id)
        document.source.origin = 'native'
        document.path = str(movie)
        document.signature = scenes._signature(movie)
        scenes._write(scenes.directory(self.source_id) / 'source.json', document)
        self.original.unlink()
        response = await self.client.delete(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(movie.read_bytes(), self.original_bytes)

    async def test_atomic_hide_failure_retains_record_and_media(self) -> None:
        with patch.object(Path, 'rename', side_effect=OSError('synthetic disk failure')):
            response = await self.client.delete(self.url)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(scenes.get_cut(self.source_id, self.cut_id).status, 'ready')
        self.assertTrue((scenes.cut_directory(self.source_id, self.cut_id) / 'cut.mp4').exists())

    async def test_interrupted_cleanup_is_hidden_and_retried_at_startup(self) -> None:
        with patch.object(scenes, '_cleanup_deleted_cut', side_effect=OSError('synthetic unlink failure'), create=True):
            response = await self.client.delete(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.cut_id, [cut.id for cut in scenes.list_cuts(self.source_id).cuts])
        tombstone = scenes.directory(self.source_id) / 'cuts' / ('.' + self.cut_id + '.deleting')
        self.assertTrue(tombstone.is_dir())
        await scenes.recover()
        self.assertFalse(tombstone.exists())
        self.assertEqual(self.original.read_bytes(), self.original_bytes)

    async def test_partial_media_cleanup_retains_its_marker_for_recovery(self) -> None:
        unlink = Path.unlink
        def fail_media(path: Path, missing_ok: bool = False) -> None:
            if path.name == 'cut.mp4' and path.parent.name.endswith('.deleting'):
                raise OSError('synthetic media deletion failure')
            unlink(path, missing_ok=missing_ok)
        with patch.object(Path, 'unlink', new=fail_media):
            response = await self.client.delete(self.url)
        self.assertEqual(response.status_code, 200)
        tombstone = scenes.directory(self.source_id) / 'cuts' / ('.' + self.cut_id + '.deleting')
        self.assertTrue((tombstone / 'deletion.json').is_file())
        self.assertTrue((tombstone / 'cut.mp4').is_file())
        await scenes.recover()
        self.assertFalse(tombstone.exists())
        self.assertEqual(self.original.read_bytes(), self.original_bytes)

    async def test_symlinked_cut_directory_cannot_delete_its_target(self) -> None:
        folder = scenes.cut_directory(self.source_id, self.cut_id)
        target = self.library / 'external'
        folder.rename(target)
        folder.symlink_to(target, target_is_directory=True)
        response = await self.client.delete(self.url)
        self.assertEqual(response.status_code, 503)
        self.assertEqual((target / 'cut.mp4').read_bytes(), b'saved MP4 scene')

    async def test_media_symlink_is_removed_without_following_it(self) -> None:
        target = self.library / 'external-media'
        target.write_bytes(b'preserve')
        (scenes.cut_directory(self.source_id, self.cut_id) / 'other').symlink_to(target)
        response = await self.client.delete(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(target.read_bytes(), b'preserve')

    async def test_unmarked_nonempty_tombstone_is_retained(self) -> None:
        tombstone = scenes.directory(self.source_id) / 'cuts' / ('.' + 'f' * 32 + '.deleting')
        tombstone.mkdir()
        (tombstone / 'unknown').write_bytes(b'preserve evidence')
        await scenes.recover()
        self.assertEqual((tombstone / 'unknown').read_bytes(), b'preserve evidence')
