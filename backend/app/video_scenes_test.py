"""Temporary CPU movie scenes; no user movies, native dialogs or cloud calls."""
from __future__ import annotations

import asyncio
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path
from typing import ClassVar
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, UploadFile
from pydantic import ValidationError

from app import video_scenes as scenes
from app.api.routes_video_scenes import router
from app.video_media import probe_media
from app.video_scene_contracts import VideoSceneCut, VideoSceneCutRequest, VideoScenePreview, VideoScenePreviewRequest, VideoSceneSource


class SceneTests(unittest.IsolatedAsyncioTestCase):
    fixtures: ClassVar[tempfile.TemporaryDirectory[str]]
    avi: ClassVar[Path]
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.avi = Path(cls.fixtures.name) / 'late-scene.avi'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=96x64:r=24:d=20',
            '-f', 'lavfi', '-i', 'color=blue:s=96x64:r=24:d=20', '-f', 'lavfi', '-i',
            'sine=frequency=440:duration=40', '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]',
            '-map', '[v]', '-map', '2:a', '-c:v', 'mjpeg', '-c:a', 'pcm_s16le', str(cls.avi)], check=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixtures.cleanup()

    async def asyncSetUp(self) -> None:
        self.library = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(scenes, 'DATA_DIR', self.library / 'library'))
        self.enterContext(patch.dict(os.environ, {'OPENFABRIC_CONFIG': str(self.library/'config.json'),
            'OPENFABRIC_DATA_DIR': str(self.library/'library'), 'SEED_VC_DIR': str(self.library/'engine')}))
        self.enterContext(patch.object(scenes, 'require_setup_idle'))
        await scenes.recover()
        self.original = self.library / 'movie.avi'
        shutil.copyfile(self.avi, self.original)

    async def asyncTearDown(self) -> None:
        await scenes.shutdown()

    async def source(self) -> VideoSceneSource:
        with patch.object(scenes, '_pick_file', AsyncMock(return_value=self.original)):
            return await scenes.pick()

    async def finish(self) -> None:
        await asyncio.wait_for(asyncio.gather(*scenes._cuts.values()), 30)

    async def test_native_picker_reads_avi_in_place_and_keeps_path_private(self) -> None:
        before = self.original.read_bytes()
        source = await self.source()
        self.assertEqual(source.origin, 'native')
        self.assertEqual(source.name, 'movie.avi')
        self.assertAlmostEqual(source.duration_sec, 40, delta=.1)
        self.assertTrue(source.has_audio)
        self.assertNotIn(str(self.original), source.model_dump_json())
        self.assertFalse((scenes.directory(source.id)/'source.video').exists())
        self.assertEqual(self.original.read_bytes(), before)

    async def test_native_picker_includes_mkv_in_supported_file_types(self) -> None:
        command = AsyncMock(return_value=(b'/tmp/movie.mkv\n', 0))
        with patch.object(scenes.sys, 'platform', 'darwin'), patch.object(scenes, '_capture_command', command):
            self.assertEqual(await scenes._pick_file(self.library), Path('/tmp/movie.mkv'))
        self.assertIn('"mkv"', command.call_args.args[1][2])

    async def test_upload_retains_owned_original_and_closes_upload(self) -> None:
        upload = UploadFile(io.BytesIO(self.avi.read_bytes()), filename='movie.avi')
        source = await scenes.create(upload)
        self.assertEqual(source.origin, 'upload')
        self.assertEqual((scenes.directory(source.id)/'source.video').read_bytes(), self.avi.read_bytes())
        self.assertTrue(upload.file.closed)
        self.assertEqual(scenes.get_source(source.id), source)

    async def test_mkv_native_and_uploaded_movies_preview_and_cut_with_audio(self) -> None:
        movie = self.library/'movie.MKV'
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(self.avi), '-t', '4',
            '-c:v', 'libx264', '-c:a', 'flac', str(movie)], check=True)
        original = movie.read_bytes()
        for origin in ('native', 'upload'):
            with self.subTest(origin=origin):
                if origin == 'native':
                    with patch.object(scenes, '_pick_file', AsyncMock(return_value=movie)):
                        source = await scenes.pick()
                else:
                    source = await scenes.create(UploadFile(io.BytesIO(original), filename=movie.name))
                self.assertEqual(source.origin, origin)
                self.assertTrue(source.has_audio)
                preview = await scenes.preview(source.id, VideoScenePreviewRequest(start_sec=1))
                self.assertGreater((await probe_media(scenes.preview_media(source.id, preview.id))).audio_duration, 0)
                cut = await scenes.create_cut(source.id, VideoSceneCutRequest(name='MKV scene', start_sec=1, end_sec=3))
                await self.finish()
                output, ready = await scenes.resolve_cut(source.id, cut.id)
                self.assertEqual(ready.status, 'ready', ready.error_code)
                info = await probe_media(output)
                self.assertAlmostEqual(info.video_duration, 2, delta=.1)
                self.assertAlmostEqual(info.audio_duration, 2, delta=.1)
                self.assertEqual(movie.read_bytes(), original)

    async def test_mkv_labelled_playlist_is_not_accepted_as_matroska(self) -> None:
        with self.assertRaises(scenes.VideoSceneError) as failure:
            await scenes.create(UploadFile(io.BytesIO(b'#EXTM3U\nhttp://127.0.0.1/private\n'), filename='movie.mkv'))
        self.assertEqual(failure.exception.code, 'invalid_media')
        self.assertEqual(scenes.list_sources().sources, [])

    async def test_late_preview_fast_seeks_and_never_converts_whole_movie(self) -> None:
        source = await self.source()
        calls: list[list[str]] = []
        command = scenes._command
        async def capture(path: Path, argv: list[str], *, timeout: float) -> None:
            calls.append(argv)
            await command(path, argv, timeout=timeout)
        with patch.object(scenes, '_command', capture):
            preview = await scenes.preview(source.id, VideoScenePreviewRequest(start_sec=30))
        self.assertEqual(preview.start_sec, 30)
        self.assertAlmostEqual(preview.duration_sec, 10, delta=.1)
        self.assertLess(calls[0].index('-ss'), calls[0].index('-i'))
        path = scenes.preview_media(source.id, preview.id)
        info = await probe_media(path)
        self.assertAlmostEqual(info.video_duration, 10, delta=.1)
        rgb = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-frames:v', '1',
            '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], check=True, capture_output=True).stdout
        self.assertGreater(rgb[2], rgb[0])
        for start in (1, 2, 3):
            await scenes.preview(source.id, VideoScenePreviewRequest(start_sec=start))
        self.assertEqual(len(list((scenes.directory(source.id)/'previews').iterdir())), 3)

    async def test_cut_preserves_audio_geometry_and_is_durable_checked_mp4(self) -> None:
        source = await self.source()
        before = self.original.read_bytes()
        cut = await scenes.create_cut(source.id, VideoSceneCutRequest(name='Blue scene', start_sec=31, end_sec=33.5))
        self.assertIn(cut.status, {'queued', 'cutting'})
        await self.finish()
        path, ready = await scenes.resolve_cut(source.id, cut.id)
        self.assertEqual(ready.status, 'ready', ready.error_code)
        self.assertEqual(ready.seconds, 2.5)
        info = await probe_media(path)
        self.assertEqual((info.width, info.height), (96, 64))
        self.assertAlmostEqual(info.video_duration, 2.5, delta=.1)
        self.assertAlmostEqual(info.audio_duration, 2.5, delta=.1)
        self.assertEqual(self.original.read_bytes(), before)
        await scenes.recover()
        self.assertEqual(scenes.list_cuts(source.id).cuts[0].status, 'ready')
        path.write_bytes(b'changed')
        with self.assertRaises(scenes.VideoSceneError) as failure:
            await scenes.resolve_cut(source.id, cut.id)
        self.assertEqual(failure.exception.code, 'cut_changed')

    async def test_interval_bounds_are_validated_before_workers_start(self) -> None:
        source = await self.source()
        for start, end in ((1, 1), (1, 602), (float('nan'), 3)):
            with self.assertRaises(ValidationError):
                VideoSceneCutRequest(name='Invalid', start_sec=start, end_sec=end)
        with self.assertRaises(scenes.VideoSceneError) as failure:
            await scenes.create_cut(source.id, VideoSceneCutRequest(name='Outside', start_sec=39, end_sec=41))
        self.assertEqual(failure.exception.code, 'invalid_interval')
        self.assertFalse(scenes.work_busy())

    async def test_source_changes_and_symlinks_fail_before_decoding(self) -> None:
        source = await self.source()
        self.original.write_bytes(b'changed')
        with self.assertRaises(scenes.VideoSceneError) as failure:
            await scenes.preview(source.id, VideoScenePreviewRequest(start_sec=1))
        self.assertEqual(failure.exception.code, 'source_changed')
        linked = self.library/'linked.avi'
        linked.symlink_to(self.avi)
        with patch.object(scenes, '_pick_file', AsyncMock(return_value=linked)):
            with self.assertRaises(scenes.VideoSceneError):
                await scenes.pick()

    async def test_upload_limit_and_avi_labelled_playlist_publish_nothing(self) -> None:
        with patch.object(scenes, 'MAX_UPLOAD_BYTES', 10):
            with self.assertRaises(scenes.VideoSceneError) as failure:
                await scenes.create(UploadFile(io.BytesIO(b'x'*11), filename='large.avi'))
        self.assertEqual(failure.exception.code, 'upload_too_large')
        with self.assertRaises(scenes.VideoSceneError):
            await scenes.create(UploadFile(io.BytesIO(b'#EXTM3U\nhttp://127.0.0.1:9/movie.ts\n'), filename='playlist.avi'))
        self.assertEqual(scenes.list_sources().sources, [])

    async def test_cancelled_cut_drains_task_and_cleans_partial_output(self) -> None:
        source = await self.source()
        entered = asyncio.Event()
        async def blocked(path: Path, argv: list[str], *, timeout: float) -> None:
            (path/'pending.mp4').write_bytes(b'partial')
            entered.set()
            await asyncio.Event().wait()
        with patch.object(scenes, '_command', blocked):
            cut = await scenes.create_cut(source.id, VideoSceneCutRequest(name='Cancel', start_sec=1, end_sec=3))
            await entered.wait()
            cancelled = await scenes.cancel(source.id, cut.id)
        self.assertEqual(cancelled.status, 'cancelled')
        self.assertFalse((scenes.cut_directory(source.id, cut.id)/'pending.mp4').exists())
        self.assertFalse(scenes.work_busy())

    async def test_recovery_adopts_verified_asset_and_never_unverified_partial(self) -> None:
        source = await self.source()
        cut = await scenes.create_cut(source.id, VideoSceneCutRequest(name='Recover', start_sec=1, end_sec=3))
        await self.finish()
        document = scenes.load_cut(source.id, cut.id)
        path = scenes.cut_directory(source.id, cut.id)
        (path/'cut.mp4').rename(path/'pending.mp4')
        document.status = 'cutting'
        scenes.save_cut(document)
        await scenes.recover()
        self.assertEqual(scenes.get_cut(source.id, cut.id).status, 'ready')
        document = scenes.load_cut(source.id, cut.id)
        document.status = 'cutting'
        document.output_sha256 = ''
        scenes.save_cut(document)
        await scenes.recover()
        self.assertEqual(scenes.get_cut(source.id, cut.id).error_code, 'interrupted')

    async def test_copied_worker_receipt_is_not_retargeted_or_forgotten(self) -> None:
        from app.video_process import WorkerIdentity
        source = await self.source()
        cut = await scenes.create_cut(source.id, VideoSceneCutRequest(name='Worker', start_sec=1, end_sec=3))
        await self.finish()
        path = scenes.cut_directory(source.id, cut.id)
        scenes._write(path/'worker.json', WorkerIdentity(pid=123, token='a'*32, receipt='/foreign/library/worker-receipt.json'))
        with patch.object(scenes, 'terminate_verified', AsyncMock()) as terminate:
            await scenes.recover()
            terminate.assert_not_called()
        self.assertTrue(scenes.work_busy())
        self.assertEqual(scenes.get_cut(source.id, cut.id).error_code, 'worker_unresolved')
        (path/'worker.json').unlink()
        await scenes.recover()

    async def test_copied_interrupted_import_uses_local_receipt_and_preserves_previous_library(self) -> None:
        from app.atomic_files import write_object
        from app.video_process import WorkerIdentity, WorkerReceipt
        source_id = 'c'*32
        original = self.library/'previous-library'/('.'+source_id+'.importing')
        original.mkdir(parents=True)
        write_object(original/'preparation.json', {'id':source_id, 'kind':'video_scene_import'})
        identity = WorkerIdentity(pid=999999, token='d'*32, receipt='worker-receipt.json')
        scenes._write(original/'worker.json', identity)
        receipt = WorkerReceipt(pid=identity.pid, token=identity.token, returncode=0)
        (original/'worker-receipt.json').write_text(receipt.model_dump_json())
        destination = scenes.root()/original.name
        destination.parent.mkdir(parents=True)
        shutil.copytree(original, destination)
        async def terminate(local: WorkerIdentity) -> bool:
            self.assertEqual(Path(local.receipt), destination/'worker-receipt.json')
            self.assertEqual(WorkerReceipt.model_validate_json(Path(local.receipt).read_bytes()), receipt)
            return True
        with patch.object(scenes, 'terminate_verified', side_effect=terminate) as worker:
            await scenes.recover()
            worker.assert_awaited_once()
        self.assertFalse(destination.exists())
        self.assertTrue(original.exists())
        self.assertFalse(scenes.work_busy())

    async def test_real_cut_cancellation_drains_owned_descendant_and_receipts(self) -> None:
        source = await self.source()
        original = scenes._command
        finished = asyncio.Event()
        async def sleep_child(path: Path, argv: list[str], *, timeout: float) -> None:
            (path/'pending.mp4').write_bytes(b'partial')
            try:
                await original(path, [sys.executable, '-c', 'import time; time.sleep(60)'], timeout=timeout)
            finally:
                finished.set()
        with patch.object(scenes, '_command', sleep_child):
            cut = await scenes.create_cut(source.id, VideoSceneCutRequest(name='Real cancellation', start_sec=1, end_sec=3))
            folder = scenes.cut_directory(source.id, cut.id)
            async with asyncio.timeout(5):
                while not (folder/'worker-receipt.json').is_file():
                    await asyncio.sleep(.01)
            task = scenes._cuts[cut.id]
            result = await scenes.cancel(source.id, cut.id)
        self.assertTrue(task.done())
        self.assertTrue(finished.is_set())
        self.assertEqual(result.status, 'cancelled')
        for name in ('pending.mp4', 'worker.json', 'worker-receipt.json'):
            self.assertFalse((folder/name).exists(), name)
        self.assertFalse(scenes.work_busy())

    async def test_native_limit_and_picker_cancel_publish_no_source(self) -> None:
        with patch.object(scenes, 'MAX_NATIVE_BYTES', 10):
            with self.assertRaises(scenes.VideoSceneError) as failure:
                await self.source()
        self.assertEqual(failure.exception.code, 'upload_too_large')
        with patch.object(scenes, '_pick_file', AsyncMock(side_effect=scenes.VideoSceneError('picker_cancelled'))):
            with self.assertRaises(scenes.VideoSceneError) as cancelled:
                await scenes.pick()
        self.assertEqual(cancelled.exception.code, 'picker_cancelled')
        self.assertEqual(scenes.list_sources().sources, [])
        self.assertFalse(scenes.work_busy())

    async def test_bounded_worker_output_failure_is_a_terminal_cut_error(self) -> None:
        from app.video_process import WorkerOutputError
        source = await self.source()
        with patch.object(scenes, 'read_owned_output', AsyncMock(side_effect=WorkerOutputError('worker_output_too_large'))):
            cut = await scenes.create_cut(source.id, VideoSceneCutRequest(name='Overflow', start_sec=1, end_sec=3))
            await asyncio.gather(*scenes._cuts.values(), return_exceptions=True)
        self.assertEqual((scenes.get_cut(source.id, cut.id).status, scenes.get_cut(source.id, cut.id).error_code), ('failed', 'invalid_media'))

    async def test_unsafe_root_recovery_preserves_outside_files_and_does_not_block_app_startup(self) -> None:
        from app.video_process import WorkerIdentity
        outside = self.library/'outside'
        outside.mkdir()
        scenes._write(outside/'worker.json', WorkerIdentity(pid=999999, token='f'*32, receipt='worker-receipt.json'))
        before = (outside/'worker.json').read_bytes()
        scene_root = scenes.root()
        scene_root.parent.mkdir(parents=True)
        scene_root.symlink_to(outside, target_is_directory=True)
        with patch.object(scenes, 'terminate_verified', AsyncMock()) as worker:
            await scenes.recover()
            await scenes.shutdown()
            worker.assert_not_called()
        self.assertEqual((outside/'worker.json').read_bytes(), before)
        with self.assertRaises(scenes.VideoSceneError):
            scenes.list_sources()
        scene_root.unlink()
        await scenes.recover()

    async def test_recovery_worker_rejects_arbitrary_outside_folder(self) -> None:
        from app.video_process import WorkerIdentity
        outside = self.library/'foreign-worker'
        outside.mkdir()
        scenes._write(outside/'worker.json', WorkerIdentity(pid=999999, token='f'*32, receipt='worker-receipt.json'))
        with patch.object(scenes, 'terminate_verified', AsyncMock(return_value=True)) as worker:
            with self.assertRaises(scenes.VideoSceneError):
                await scenes._recover_worker(outside)
            worker.assert_not_called()
        self.assertTrue((outside/'worker.json').exists())
        await scenes.recover()

    async def test_route_admission_busy_is_structured_and_partial_upload_spool_closes(self) -> None:
        from app.resource_admission import ResourceBusyError
        source = await self.source()
        app = FastAPI()
        app.include_router(router)
        files: list[tempfile.SpooledTemporaryFile[bytes]] = []
        def spool(*, max_size: int = 0) -> tempfile.SpooledTemporaryFile[bytes]:
            file = tempfile.SpooledTemporaryFile(max_size=max_size)
            files.append(file)
            return file
        async def chunks() -> AsyncIterator[bytes]:
            yield b'--scene\r\nContent-Disposition: form-data; name="video"; filename="movie.avi"\r\nContent-Type: video/x-msvideo\r\n\r\n'+b'a'*100
            yield b'b'*66000
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://127.0.0.1') as client:
            with patch.object(scenes, 'require_setup_idle', side_effect=ResourceBusyError('module_setup_busy')):
                busy = await client.post(f'/api/videos/scenes/sources/{source.id}/cuts', json={'name':'Busy', 'start_sec':1, 'end_sec':3})
            self.assertEqual(busy.status_code, 409)
            self.assertEqual(busy.json(), {'detail':'module_setup_busy'})
            with patch.object(scenes, 'MAX_UPLOAD_BYTES', 1), patch('starlette.formparsers.SpooledTemporaryFile', side_effect=spool):
                overflow = await client.post('/api/videos/scenes/sources', content=chunks(), headers={'content-type':'multipart/form-data; boundary=scene'})
            self.assertEqual(overflow.status_code, 413)
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].closed)

    async def test_corrupt_metadata_never_hides_workers_or_other_recoverable_cuts(self) -> None:
        from app.video_process import WorkerIdentity
        source = await self.source()
        cuts = []
        for name in ('Corrupt', 'Recover'):
            cut = await scenes.create_cut(source.id, VideoSceneCutRequest(name=name, start_sec=1, end_sec=3))
            await self.finish()
            cuts.append(cut)
        first, second = cuts
        first_folder = scenes.cut_directory(source.id, first.id)
        scenes._write(first_folder/'worker.json', WorkerIdentity(pid=999999, token='e'*32, receipt='worker-receipt.json'))
        (first_folder/'cut.json').write_bytes(b'corrupt cut evidence')
        (scenes.directory(source.id)/'source.json').write_bytes(b'corrupt source evidence')
        document = scenes.load_cut(source.id, second.id)
        document.status = 'cutting'
        scenes.save_cut(document)
        with patch.object(scenes, 'terminate_verified', AsyncMock(return_value=False)) as terminate:
            await scenes.recover()
            terminate.assert_awaited_once()
        self.assertTrue(scenes.work_busy())
        self.assertEqual(scenes.get_cut(source.id, second.id).status, 'ready')
        with patch.object(scenes, 'terminate_verified', AsyncMock(return_value=True)) as terminate:
            await scenes.recover()
            terminate.assert_awaited_once()
        self.assertFalse(scenes.work_busy())
        self.assertEqual((first_folder/'cut.json').read_bytes(), b'corrupt cut evidence')
        self.assertEqual((scenes.directory(source.id)/'source.json').read_bytes(), b'corrupt source evidence')

    async def test_maximum_ten_minute_export_is_a_real_checked_mp4(self) -> None:
        movie = self.library/'long-silent.avi'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=blue:s=16x16:r=1:d=601',
            '-c:v', 'mjpeg', str(movie)], check=True)
        with patch.object(scenes, '_pick_file', AsyncMock(return_value=movie)):
            source = await scenes.pick()
        cut = await scenes.create_cut(source.id, VideoSceneCutRequest(name='Ten minutes', start_sec=1, end_sec=601))
        await self.finish()
        path, ready = await scenes.resolve_cut(source.id, cut.id)
        self.assertEqual(ready.seconds, 600)
        self.assertAlmostEqual((await probe_media(path)).video_duration, 600, delta=.1)
        with self.assertRaises(ValidationError):
            VideoSceneCutRequest(name='Too long', start_sec=0, end_sec=600.01)

    async def test_preview_worker_metadata_symlink_stays_busy_without_outside_reads(self) -> None:
        source = await self.source()
        folder = scenes.directory(source.id)/'previews'/('f'*32)
        folder.mkdir(parents=True)
        outside = self.library/'outside-worker.json'
        outside.write_text('private outside evidence')
        (folder/'worker.json').symlink_to(outside)
        with patch.object(scenes, 'terminate_verified', AsyncMock(return_value=True)) as terminate:
            await scenes.recover()
            terminate.assert_not_called()
        self.assertTrue(scenes.work_busy())
        self.assertEqual(outside.read_text(), 'private outside evidence')
        (folder/'worker.json').unlink()
        await scenes.recover()

    async def test_routes_reject_cross_origin_raw_paths_and_symlink_media(self) -> None:
        source = await self.source()
        app = FastAPI()
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
            self.assertEqual((await client.post('/api/videos/scenes/sources/pick', headers={'origin':'https://evil.test'})).status_code, 403)
            self.assertEqual((await client.post('/api/videos/scenes/sources', json={'path':str(self.original)})).status_code, 422)
            response = await client.get('/api/videos/scenes/sources/'+source.id)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(str(self.original), response.text)
            preview = await scenes.preview(source.id, VideoScenePreviewRequest(start_sec=1))
            path = scenes.preview_media(source.id, preview.id)
            path.unlink()
            path.symlink_to(self.avi)
            self.assertEqual((await client.get(preview.url)).status_code, 503)

    async def test_public_media_contracts_reject_foreign_and_mismatched_ids(self) -> None:
        source = await self.source()
        preview = await scenes.preview(source.id, VideoScenePreviewRequest(start_sec=1))
        for url in ('https://evil.test/video.mp4', preview.url.replace(source.id, 'a'*32)):
            with self.assertRaises(ValidationError):
                VideoScenePreview.model_validate({**preview.model_dump(), 'url': url})
        cut = await scenes.create_cut(source.id, VideoSceneCutRequest(name='Media', start_sec=1, end_sec=3))
        await self.finish()
        ready = scenes.get_cut(source.id, cut.id)
        for url in ('https://evil.test/video.mp4', ready.output_url.replace(cut.id, 'a'*32)):
            with self.assertRaises(ValidationError):
                VideoSceneCut.model_validate({**ready.model_dump(), 'output_url': url})

    async def test_cut_decode_is_owned_and_does_not_use_short_preview_timeout(self) -> None:
        source = await self.source()
        calls: list[tuple[list[str], float]] = []
        command = scenes._command
        async def capture(path: Path, argv: list[str], *, timeout: float) -> None:
            calls.append((argv, timeout))
            await command(path, argv, timeout=timeout)
        with patch.object(scenes, '_command', capture):
            cut = await scenes.create_cut(source.id, VideoSceneCutRequest(name='Decode', start_sec=1, end_sec=3))
            await self.finish()
        self.assertEqual(scenes.get_cut(source.id, cut.id).status, 'ready')
        decode = [call for call in calls if 'null' in call[0]]
        self.assertEqual(len(decode), 1)
        self.assertEqual(decode[0][1], 3600)
        self.assertIn('file,pipe', decode[0][0])

    async def test_shutdown_drains_inflight_preview_and_keeps_ready_cuts(self) -> None:
        source = await self.source()
        entered = asyncio.Event()
        async def blocked(path: Path, argv: list[str], *, timeout: float) -> None:
            entered.set()
            await asyncio.Event().wait()
        with patch.object(scenes, '_command', blocked):
            request = asyncio.create_task(scenes.preview(source.id, VideoScenePreviewRequest(start_sec=1)))
            await entered.wait()
            self.assertTrue(scenes.work_busy())
            await scenes.shutdown()
            with self.assertRaises(asyncio.CancelledError):
                await request
        self.assertFalse(scenes.work_busy())

    async def test_silent_mp4_mov_webm_import_and_cut_remain_silent(self) -> None:
        for extension in ('mp4', 'mov', 'webm'):
            movie = self.library/('silent.'+extension)
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=blue:s=96x64:r=24:d=2',
                '-c:v', 'libvpx-vp9' if extension == 'webm' else 'libx264', '-pix_fmt', 'yuv420p', str(movie)], check=True)
            with patch.object(scenes, '_pick_file', AsyncMock(return_value=movie)):
                source = await scenes.pick()
            cut = await scenes.create_cut(source.id, VideoSceneCutRequest(name='Silent', start_sec=0, end_sec=2))
            await self.finish()
            path, ready = await scenes.resolve_cut(source.id, cut.id)
            self.assertFalse(ready.has_audio)
            self.assertEqual((await probe_media(path)).audio_duration, 0)

    async def test_rotated_mov_exports_visible_orientation_and_display_geometry(self) -> None:
        coded = self.library/'coded.mov'
        movie = self.library/'rotated.mov'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=48x64:r=24:d=3',
            '-f', 'lavfi', '-i', 'color=blue:s=48x64:r=24:d=3', '-filter_complex', '[0:v][1:v]hstack[v]',
            '-map', '[v]', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(coded)], check=True)
        subprocess.run(['ffmpeg', '-v', 'error', '-display_rotation', '90', '-i', str(coded),
            '-c', 'copy', str(movie)], check=True)
        original = movie.read_bytes()
        with patch.object(scenes, '_pick_file', AsyncMock(return_value=movie)):
            source = await scenes.pick()
        self.assertEqual((source.width, source.height), (64, 96))
        preview = await scenes.preview(source.id, VideoScenePreviewRequest(start_sec=0))
        self.assertEqual((preview.width, preview.height), (64, 96))
        cut = await scenes.create_cut(source.id, VideoSceneCutRequest(name='Rotated', start_sec=1, end_sec=3))
        await self.finish()
        output, ready = await scenes.resolve_cut(source.id, cut.id)
        self.assertEqual((ready.width, ready.height), (64, 96))
        info = await probe_media(output)
        self.assertEqual((info.width, info.height), (64, 96))
        self.assertEqual(info.rotation, 0)
        self.assertEqual(info.sample_aspect_ratio, 1)
        def rgb(path: Path) -> bytes:
            return subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-frames:v', '1',
                '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], check=True, capture_output=True).stdout
        expected = rgb(movie)
        actual = rgb(output)
        self.assertEqual(len(actual), 64*96*3)
        for y in (24, 72):
            offset = (y*64+32)*3
            self.assertEqual(actual[offset] > actual[offset+2], expected[offset] > expected[offset+2])
        self.assertEqual(movie.read_bytes(), original)

    async def test_coded_and_display_geometry_limits_both_apply(self) -> None:
        from app.video_media import MediaInfo
        for info in (MediaInfo(4098, 64, 24, 40, 0, sample_aspect_ratio=.5),
                     MediaInfo(96, 64, 24, 40, 0, sample_aspect_ratio=64)):
            with patch.object(scenes, 'probe_media', AsyncMock(return_value=info)):
                with self.assertRaises(scenes.VideoSceneError) as failure:
                    await self.source()
            self.assertEqual(failure.exception.code, 'invalid_media')
        self.assertEqual(scenes.list_sources().sources, [])

    async def test_import_retains_marker_through_publication_and_interrupted_staging_recovers(self) -> None:
        from app.atomic_files import read_object
        source = await self.source()
        published = scenes.directory(source.id)
        marker = published/'preparation.json'
        self.assertTrue(marker.is_file())
        self.assertEqual(read_object(marker), {'id':source.id, 'kind':'video_scene_import'})
        staging = scenes.root()/('.'+source.id+'.importing')
        shutil.copytree(published, staging)
        await scenes.recover()
        self.assertFalse(staging.exists())
        self.assertEqual(scenes.get_source(source.id), source)
        self.assertFalse(scenes.work_busy())

    async def test_native_above_absolute_limit_reports_upload_too_large_before_signature_schema(self) -> None:
        info = self.original.stat()
        oversized = os.stat_result((info.st_mode, info.st_ino, info.st_dev, info.st_nlink,
            info.st_uid, info.st_gid, 8*1024**3+1, info.st_atime, info.st_mtime, info.st_ctime))
        real_stat = Path.stat
        def stat_file(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
            if follow_symlinks and path.name == self.original.name:
                return oversized
            return real_stat(path, follow_symlinks=follow_symlinks)
        with patch.object(Path, 'stat', new=stat_file):
            with self.assertRaises(scenes.VideoSceneError) as failure:
                await self.source()
        self.assertEqual(failure.exception.code, 'upload_too_large')

    async def test_anamorphic_avi_exports_square_pixels_with_display_aspect(self) -> None:
        movie = self.library/'anamorphic.avi'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=48x64:r=24:d=3',
            '-f', 'lavfi', '-i', 'color=blue:s=48x64:r=24:d=3', '-filter_complex', '[0:v][1:v]hstack,setsar=2[v]',
            '-map', '[v]', '-c:v', 'mpeg4', '-pix_fmt', 'yuv420p', str(movie)], check=True)
        original = movie.read_bytes()
        with patch.object(scenes, '_pick_file', AsyncMock(return_value=movie)):
            source = await scenes.pick()
        self.assertEqual((source.width, source.height), (192, 64))
        preview = await scenes.preview(source.id, VideoScenePreviewRequest(start_sec=0))
        self.assertEqual((preview.width, preview.height), (192, 64))
        cut = await scenes.create_cut(source.id, VideoSceneCutRequest(name='Anamorphic', start_sec=1, end_sec=3))
        await self.finish()
        output, ready = await scenes.resolve_cut(source.id, cut.id)
        self.assertEqual((ready.width, ready.height), (192, 64))
        info = await probe_media(output)
        self.assertEqual((info.width, info.height), (192, 64))
        self.assertEqual(info.sample_aspect_ratio, 1)
        self.assertEqual(info.rotation, 0)
        rgb = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(output), '-frames:v', '1',
            '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], check=True, capture_output=True).stdout
        left = (32*192+48)*3
        right = (32*192+144)*3
        self.assertGreater(rgb[left], rgb[left+2])
        self.assertGreater(rgb[right+2], rgb[right])
        self.assertEqual(movie.read_bytes(), original)
