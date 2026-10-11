"""Redirected video storage never authorizes reads, writes or recursive deletion."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import video_jobs, video_projects, video_render
from app.video_contracts import CreateVideoProjectRequest


class VideoStorageSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.library = self.root / 'library'
        self.library.mkdir()
        self.enterContext(patch.dict(os.environ, {
            'OPENFABRIC_CONFIG': str(self.root / 'config.json'), 'OPENFABRIC_DATA_DIR': str(self.library),
            'SEED_VC_DIR': str(self.root / 'engine'), 'OPENFABRIC_MODULE_ROOT': str(self.root / 'modules'),
        }))
        for module in (video_projects, video_jobs):
            self.enterContext(patch.object(module, 'DATA_DIR', self.library))
        self.enterContext(patch.object(video_jobs, '_current', None))
        self.enterContext(patch.object(video_jobs, '_unverified', set()))
        self.enterContext(patch.object(video_render, '_unverified', set()))
        self.project = await video_projects.create(CreateVideoProjectRequest(name='Owned fixture', duration_sec=2))
        self.external_projects = self.root / 'external-projects'
        video_projects.projects_root().rename(self.external_projects)
        (self.library / 'video_projects').symlink_to(self.external_projects, target_is_directory=True)
        self.project_bytes = (self.external_projects / self.project.id / 'project.json').read_bytes()

    async def test_project_root_redirect_rejects_reads_writes_and_deletion(self) -> None:
        for action in (lambda: video_projects.get(self.project.id),
                       lambda: video_projects.list_projects(),
                       lambda: video_projects.artifact(self.project.id, 'project.json')):
            with self.assertRaises(video_projects.VideoProjectError):
                action()
        with self.assertRaises(video_projects.VideoProjectError):
            await video_projects.create(CreateVideoProjectRequest(name='Rejected', duration_sec=2))
        with self.assertRaises(video_projects.VideoProjectError):
            await video_render.delete(self.project.id)
        self.assertEqual((self.external_projects / self.project.id / 'project.json').read_bytes(), self.project_bytes)
        self.assertEqual(len(list(self.external_projects.iterdir())), 1)

    async def test_project_recovery_quarantines_root_without_following_workers(self) -> None:
        with patch('app.video_render.terminate_verified', new=AsyncMock()) as terminate, self.assertLogs('app.video_render', level='ERROR'):
            await video_render.recover()
            await video_render.recover()
        terminate.assert_not_called()
        self.assertTrue(video_render.work_busy())
        self.assertEqual((self.external_projects / self.project.id / 'project.json').read_bytes(), self.project_bytes)

    def _redirect_legacy(self) -> tuple[str, Path]:
        identifier = 'c' * 32
        external = self.root / 'external-videos' / identifier
        external.mkdir(parents=True)
        (external / 'video.json').write_text(json.dumps({'id': identifier, 'status': 'ready'}))
        (external / 'output.mp4').write_bytes(b'external movie evidence')
        (self.library / 'videos').symlink_to(external.parent, target_is_directory=True)
        return identifier, external

    async def test_legacy_root_redirect_rejects_reads_writes_and_deletion(self) -> None:
        identifier, external = self._redirect_legacy()
        before = (external / 'video.json').read_bytes()
        for action in (lambda: video_jobs.list_videos(), lambda: video_jobs.get_video(identifier),
                       lambda: video_jobs.output_file(identifier)):
            with self.assertRaises(video_jobs.VideoJobError):
                action()
        job = video_jobs.VideoJob(id=identifier, track_id=1, title='Fixture', prompt='Fixture',
            seconds=2, start_sec=0, frames=49, seed=1, created_at='now')
        with self.assertRaises(video_jobs.VideoJobError):
            video_jobs._write(job)
        with self.assertRaises(video_jobs.VideoJobError):
            await video_jobs.delete_video(identifier)
        self.assertEqual((external / 'video.json').read_bytes(), before)
        self.assertEqual((external / 'output.mp4').read_bytes(), b'external movie evidence')

    async def test_legacy_recovery_quarantines_root_without_touching_external_metadata(self) -> None:
        identifier, external = self._redirect_legacy()
        (external / 'video.json').write_text(json.dumps({'id': identifier, 'status': 'running', 'worker': {'fixture': True}}))
        before = (external / 'video.json').read_bytes()
        with patch('app.video_jobs.terminate_verified', new=AsyncMock()) as terminate, self.assertLogs(level='ERROR'):
            await video_jobs.recover()
            await video_jobs.recover()
        terminate.assert_not_called()
        self.assertTrue(video_jobs.work_busy())
        self.assertEqual((external / 'video.json').read_bytes(), before)

    async def test_repaired_roots_clear_quarantine_after_shutdown_and_recovery(self) -> None:
        _identifier, external = self._redirect_legacy()
        with self.assertLogs(level='ERROR'):
            await video_jobs.recover()
        self.assertTrue(video_jobs.work_busy())
        await video_jobs.shutdown()
        self.assertTrue(video_jobs.work_busy())
        (self.library / 'video_projects').unlink()
        self.external_projects.rename(self.library / 'video_projects')
        (self.library / 'videos').unlink()
        external.parent.rename(self.library / 'videos')
        await video_jobs.recover()
        self.assertFalse(video_jobs.work_busy())
        self.assertFalse(video_render.work_busy())
        self.assertEqual(video_projects.get(self.project.id).id, self.project.id)

    async def test_legacy_listing_and_reconciliation_skip_redirected_child(self) -> None:
        identifier = 'd' * 32
        external = self.root / 'external-child'
        external.mkdir()
        (external / 'video.json').write_text(json.dumps({'id': identifier, 'status': 'running'}))
        before = (external / 'video.json').read_bytes()
        (self.library / 'videos').mkdir()
        (self.library / 'videos' / identifier).symlink_to(external, target_is_directory=True)
        self.assertEqual(video_jobs.list_videos(), [])
        video_jobs.reconcile_interrupted(self.library / 'videos', None)
        self.assertEqual((external / 'video.json').read_bytes(), before)
        with self.assertRaises(video_jobs.VideoJobError):
            await video_jobs.delete_video(identifier)
