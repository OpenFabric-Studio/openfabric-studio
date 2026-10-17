from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from app import music_analysis, music_analysis_test as support
from app.music_analysis_contracts import AnalysisResult, MusicAnalysisJob


class CompletedWorkspaceOwnershipTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support.MusicAnalysisTests.asyncSetUp
    asyncTearDown = support.MusicAnalysisTests.asyncTearDown

    def completed(self) -> tuple[str, Path]:
        source = self.root / 'files' / 'source.wav'
        source.parent.mkdir()
        source.write_bytes(b'preserved source fixture')
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        job = MusicAnalysisJob(id='e' * 32, source=self.source, created_at='2026-10-17T00:00:00Z')
        music_analysis._insert_job(job, self.request, 'f' * 64)
        music_analysis._change(job.id, status='running')
        workspace = music_analysis._workspace(job.id)
        workspace.mkdir(parents=True)
        (workspace / 'source.wav').write_bytes(source.read_bytes())
        with music_analysis._connection(write=True) as connection:
            connection.execute('UPDATE music_analysis_jobs SET snapshot_sha256=?,snapshot_name=? WHERE id=?',
                (digest, 'source.wav', job.id))
        body = support.result(self.source).model_dump()
        body.update(audio_sha256=digest, loudness={**body['loudness'], 'source_sha256': digest})
        result = AnalysisResult.model_validate(body)
        key = music_analysis.cache_key(digest, 'version', result.configuration_sha256)
        self.assertTrue(music_analysis._publish(job.id, music_analysis._record(job.id).revision, key, result))
        return job.id, source

    async def test_projection_exposes_only_verified_terminal_owner_metadata(self) -> None:
        identifier, source = self.completed()
        with patch.object(music_analysis, '_resolve_source', return_value=source):
            projection = await music_analysis.completed_workspace_cleanup_projection(identifier)
        self.assertEqual(projection.job_id, identifier)
        self.assertEqual(projection.job_revision, music_analysis._record(identifier).revision)
        self.assertEqual(projection.source, self.source)
        self.assertEqual(projection.source_path, source)
        self.assertEqual(projection.workspace, music_analysis._workspace(identifier))
        self.assertEqual(projection.audio_sha256, hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(projection.snapshot_name, 'source.wav')

    async def test_cleanup_guard_rechecks_revision_and_serializes_source_removal(self) -> None:
        identifier, source = self.completed()
        entered = asyncio.Event()

        async def remove() -> None:
            async with music_analysis.protect_track_removal(self.source.track_id):
                entered.set()

        with patch.object(music_analysis, '_resolve_source', return_value=source):
            projection = await music_analysis.completed_workspace_cleanup_projection(identifier)
            with self.assertRaises(music_analysis.MusicAnalysisError) as conflict:
                async with music_analysis.protect_completed_workspace_cleanup(identifier, projection.job_revision - 1):
                    self.fail('A stale cleanup plan was accepted')
            self.assertEqual(conflict.exception.code, 'analysis_revision_conflict')
            async with music_analysis.protect_completed_workspace_cleanup(identifier, projection.job_revision) as fresh:
                self.assertEqual(fresh, projection)
                deleting = asyncio.create_task(remove())
                await asyncio.sleep(0)
                self.assertFalse(entered.is_set())
            await asyncio.wait_for(deleting, 1)
        self.assertTrue(entered.is_set())
        self.assertTrue(source.is_file())

    async def test_busy_owners_and_source_mutation_flags_block_cleanup(self) -> None:
        identifier, source = self.completed()
        with patch.object(music_analysis, '_resolve_source', return_value=source):
            for flag in ('_deleting', '_replacing_stems'):
                with self.subTest(flag=flag), patch.object(music_analysis, flag, {self.source.track_id}):
                    with self.assertRaises(music_analysis.MusicAnalysisError):
                        await music_analysis.completed_workspace_cleanup_projection(identifier)
            with patch.object(music_analysis, '_sources', {identifier: self.source}):
                with self.assertRaises(music_analysis.MusicAnalysisError):
                    await music_analysis.completed_workspace_cleanup_projection(identifier)
            with patch.object(music_analysis, '_closing', True):
                with self.assertRaises(music_analysis.MusicAnalysisError):
                    await music_analysis.completed_workspace_cleanup_projection(identifier)

    async def test_corrupt_snapshot_metadata_and_symlink_workspace_are_preserved(self) -> None:
        identifier, source = self.completed()
        with patch.object(music_analysis, '_resolve_source', return_value=source):
            with music_analysis._connection(write=True) as connection:
                connection.execute('UPDATE music_analysis_jobs SET snapshot_name=? WHERE id=?', ('../outside.wav', identifier))
            with self.assertRaises(music_analysis.MusicAnalysisError):
                await music_analysis.completed_workspace_cleanup_projection(identifier)
            with music_analysis._connection(write=True) as connection:
                connection.execute('UPDATE music_analysis_jobs SET snapshot_name=? WHERE id=?', ('source.wav', identifier))
            workspace = music_analysis._workspace(identifier)
            retained = workspace.with_name('retained')
            workspace.rename(retained)
            workspace.symlink_to(retained, target_is_directory=True)
            with self.assertRaises(music_analysis.MusicAnalysisError):
                await music_analysis.completed_workspace_cleanup_projection(identifier)
        self.assertEqual(source.read_bytes(), b'preserved source fixture')
        self.assertEqual((retained / 'source.wav').read_bytes(), source.read_bytes())

    async def test_detached_workspace_keeps_owner_capability_but_never_resolves_caller_path(self) -> None:
        identifier, source = self.completed()
        workspace = music_analysis._workspace(identifier)
        detached = workspace.with_name('private-cleanup-stage')
        workspace.rename(detached)
        with patch.object(music_analysis, '_resolve_source', return_value=source):
            projection = await music_analysis.completed_workspace_cleanup_projection(identifier)
            self.assertEqual(projection.workspace, workspace)
            self.assertFalse(projection.workspace.exists())
            async with music_analysis.protect_completed_workspace_cleanup(identifier, projection.job_revision) as fresh:
                self.assertEqual(fresh.workspace, workspace)
        self.assertTrue((detached / 'source.wav').is_file())
        self.assertTrue(source.is_file())
