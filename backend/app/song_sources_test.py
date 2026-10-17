from __future__ import annotations

import hashlib
import asyncio
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from app import audio_versions, db, song_sources
from app.beat_song_mix_contracts import BeatSongMixError
from app.export_provenance_contracts import MusicEvidence
from app.music_analysis_contracts import VersionMusicSource


class SongSourcesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        for name, value in (('_db', None), ('DATA_DIR', self.root), ('FILES_DIR', self.root / 'files'),
                ('DB_PATH', self.root / 'library.db')):
            self.enterContext(patch.object(db, name, value))
        self.connection = db.get_db()
        await song_sources.recover()
        path = db.FILES_DIR / 'song.wav'
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(path, np.zeros((4800, 2), dtype=np.float32), 48000, subtype='FLOAT')
        track = db.insert_track(model='upload', title='Private title', lyrics='', seed=None, duration_ms=100,
            wall_ms=None, params={}, audio_path=path, abc_path=None)
        version = audio_versions.retain_original(track, path)
        self.source = VersionMusicSource(track_id=track, version_id=version.id)
        self.original = audio_versions.resolve_source(track, version.id)
        self.sha = hashlib.sha256(self.original.read_bytes()).hexdigest()

    async def asyncTearDown(self) -> None:
        await song_sources.shutdown()
        self.connection.close()

    async def test_retained_snapshot_survives_original_deletion_and_keeps_known_restrictions(self) -> None:
        root = song_sources.owned_root('a' * 32, create=True)
        with patch.object(song_sources, 'track_music_evidence', return_value=MusicEvidence(restriction='noncommercial')):
            snapshot = await song_sources.retain_source(self.source, root, source_id='b' * 32, expected_sha256=self.sha)
        self.original.unlink()
        retained = await song_sources.resolve_snapshot(root, snapshot)
        self.assertEqual(hashlib.sha256(retained.read_bytes()).hexdigest(), self.sha)
        self.assertEqual(snapshot.music_evidence.restriction, 'noncommercial')
        self.assertEqual((snapshot.sample_rate_hz, snapshot.frames, snapshot.channels), (48000, 4800, 2))
        self.assertNotIn(str(self.root), snapshot.model_dump_json())
        self.assertNotIn('Private title', snapshot.model_dump_json())
        with patch.object(song_sources, 'inspect_source', side_effect=AssertionError('resume reread mutable source')):
            reused = await song_sources.retain_source(self.source, root, source_id='b' * 32, expected_sha256=self.sha)
        self.assertEqual(reused, snapshot)

    async def test_bounds_and_destination_free_space_are_enforced_before_copy(self) -> None:
        from unittest.mock import AsyncMock
        root = song_sources.owned_root('7' * 32, create=True)
        for changes in ({'max_bytes': 10}, {'max_seconds': .01}):
            with self.subTest(changes=changes), patch.object(song_sources, 'copy_verified', new=AsyncMock()) as copy:
                with self.assertRaises(BeatSongMixError):
                    await song_sources.retain_source(self.source, root, source_id='8' * 32, expected_sha256=self.sha, **changes)
                copy.assert_not_awaited()
        with patch.object(song_sources.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 1})()), \
                patch.object(song_sources, 'copy_verified', new=AsyncMock()) as copy:
            with self.assertRaises(BeatSongMixError) as caught:
                await song_sources.retain_source(self.source, root, source_id='8' * 32, expected_sha256=self.sha)
            self.assertEqual(caught.exception.code, 'insufficient_disk')
            copy.assert_not_awaited()

    async def test_changed_source_is_rejected_without_partial_copy(self) -> None:
        root = song_sources.owned_root('c' * 32, create=True)
        with self.assertRaises(BeatSongMixError) as caught:
            await song_sources.retain_source(self.source, root, source_id='d' * 32, expected_sha256='f' * 64)
        self.assertEqual(caught.exception.code, 'song_source_changed')
        self.assertFalse((root / ('d' * 32)).exists())

    async def test_completed_raw_intent_recovers_after_completion_journal_write_failure_and_deletion(self) -> None:
        root = song_sources.owned_root('9' * 32, create=True)
        original_write = song_sources._write_snapshot
        def fail_completion(path, snapshot):
            if path.name == 'source.json':
                raise OSError('simulated journal failure')
            original_write(path, snapshot)
        with patch.object(song_sources, '_write_snapshot', side_effect=fail_completion):
            with self.assertRaises(OSError):
                await song_sources.retain_source(self.source, root, source_id='0' * 32, expected_sha256=self.sha)
        self.original.unlink()
        with patch.object(song_sources, 'inspect_source', side_effect=AssertionError('recovery reread deleted original')):
            recovered = await song_sources.load_retained_source(root, '0' * 32, self.source, self.sha,
                max_bytes=512 * 1024**2, max_seconds=380)
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered.sha256, self.sha)
            self.assertTrue((root / ('0' * 32) / 'source.json').is_file())

    async def test_snapshot_hash_and_symlink_containment_are_rechecked(self) -> None:
        root = song_sources.owned_root('e' * 32, create=True)
        snapshot = await song_sources.retain_source(self.source, root, source_id='f' * 32, expected_sha256=self.sha)
        path = song_sources.snapshot_path(root, snapshot)
        path.write_bytes(b'changed')
        with self.assertRaises(BeatSongMixError):
            await song_sources.resolve_snapshot(root, snapshot)
        path.unlink()
        path.symlink_to(self.original)
        with self.assertRaises(BeatSongMixError):
            song_sources.snapshot_path(root, snapshot)

    async def test_configured_parent_alias_accepts_own_returned_canonical_root(self) -> None:
        if not str(self.root).startswith('/private/var/'):
            self.skipTest('platform has no /var -> /private/var temporary parent alias')
        alias = Path(str(db.FILES_DIR).replace('/private/var/', '/var/', 1))
        with patch.object(db, 'FILES_DIR', alias):
            root = song_sources.owned_root('1' * 32, create=True)
            self.assertEqual(root, root.resolve())
            snapshot = await song_sources.retain_source(self.source, root, source_id='2' * 32, expected_sha256=self.sha)
            self.assertTrue((await song_sources.resolve_snapshot(root, snapshot)).is_file())
            target = root / ('3' * 32)
            target.symlink_to(self.root, target_is_directory=True)
            with self.assertRaises(BeatSongMixError):
                song_sources._root(target)

    async def test_failed_probe_drain_keeps_persisted_quarantine_busy_until_verified_recovery(self) -> None:
        from app import job_lifecycle
        actual_spawn = song_sources.spawn_owned
        processes = []
        spawned = asyncio.Event()

        async def slow(_argv, **kwargs):
            process = await actual_spawn([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
            processes.append(process)
            spawned.set()
            return process

        with patch.object(song_sources, 'spawn_owned', side_effect=slow), \
                patch.object(song_sources, 'kill_process_tree', side_effect=OSError('unverified cleanup')):
            task = asyncio.create_task(song_sources.inspect_source(self.source))
            await asyncio.wait_for(spawned.wait(), 10)
            task.cancel()
            with self.assertRaises(OSError):
                await task
        self.assertTrue(song_sources.work_busy())
        self.assertIsNone(processes[0].returncode)
        folders = list((db.FILES_DIR / '_song_sources' / '_probes').iterdir())
        self.assertEqual(len(folders), 1)
        self.assertTrue((folders[0] / 'identity.json').is_file())
        with patch.object(song_sources, 'terminate_verified', return_value=False):
            await song_sources.recover()
            self.assertTrue(song_sources.work_busy())
        await job_lifecycle.kill_process_tree(processes[0])
        if processes[0].stdin is not None:
            processes[0].stdin.close()
        await song_sources.recover()
        self.assertFalse(song_sources.work_busy())
