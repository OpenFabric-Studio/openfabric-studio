"""Queue admission freezes source bytes before any model can start."""
from __future__ import annotations
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from app import audio_versions, db, queued_music_sources as sources, song_sources
from app.music_analysis_contracts import VersionMusicSource
from app.music_conditioning_contracts import StableAudioConditioning
from app.music_queue_contracts import MusicQueueError


class QueuedMusicSourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await sources.recover()
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        for name, value in (('_db', None), ('DATA_DIR', self.root), ('FILES_DIR', self.root / 'files'), ('DB_PATH', self.root / 'library.db')):
            self.enterContext(patch.object(db, name, value))
        self.connection = db.get_db()
        await song_sources.recover()
        path = db.FILES_DIR / 'fixture.wav'; path.parent.mkdir(exist_ok=True)
        sf.write(path, np.zeros((4800, 2), dtype=np.float32), 48000, subtype='FLOAT')
        identifier = db.insert_track(model='upload', title='Fixture', lyrics='', seed=None, duration_ms=100, wall_ms=None, params={}, audio_path=path, abc_path=None)
        version = audio_versions.retain_original(identifier, path)
        self.source = VersionMusicSource(track_id=identifier, version_id=version.id)
        self.original = audio_versions.resolve_source(identifier, version.id)
        self.sha = hashlib.sha256(self.original.read_bytes()).hexdigest()
        self.options = StableAudioConditioning(mode='audio_to_audio', source=self.source, source_sha256=self.sha, noise_level=.6)

    async def asyncTearDown(self) -> None:
        await sources.shutdown()
        await song_sources.shutdown()
        self.connection.close()

    async def test_retry_reuses_one_verified_copy_after_the_original_track_is_deleted(self) -> None:
        intake = await sources.retain('same-submit', 'a' * 64, [self.options])
        self.assertEqual(len(intake), 1)
        self.original.unlink(); db.delete_track(self.source.track_id)
        replay = await sources.retain('same-submit', 'a' * 64, [self.options])
        self.assertEqual(replay, intake)
        path = await sources.resolve(intake[0], self.options)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), self.sha)
        self.assertNotIn(str(self.root), intake[0].model_dump_json())

    async def test_replay_refuses_changed_retained_bytes_and_never_substitutes_another_source(self) -> None:
        intake = await sources.retain('same-submit', 'b' * 64, [self.options])
        path = song_sources.snapshot_path(song_sources.owned_root(intake[0].owner_id), intake[0].snapshot)
        path.write_bytes(b'changed')
        with self.assertRaises(MusicQueueError) as caught:
            await sources.retain('same-submit', 'b' * 64, [self.options])
        self.assertEqual(caught.exception.code, 'conditioning_source_changed')
        self.assertEqual(path.read_bytes(), b'changed')

    async def test_failed_intake_journal_reuses_the_verified_capture_without_an_orphan_copy(self) -> None:
        with patch.object(sources, 'write_object', side_effect=OSError('injected journal failure')):
            with self.assertRaises(OSError):
                await sources.retain('journal-failed', 'c' * 64, [self.options])
        self.assertEqual(len(list((db.FILES_DIR / '_song_sources').rglob('raw.wav'))), 1)
        self.original.unlink(); db.delete_track(self.source.track_id)
        recovered = await sources.load_existing('journal-failed', 'c' * 64, [self.options])
        self.assertEqual(len(recovered), 1)
        self.assertEqual(await sources.retain('journal-failed', 'c' * 64, [self.options]), recovered)
        self.assertEqual(len(list((db.FILES_DIR / '_song_sources').rglob('raw.wav'))), 1)
