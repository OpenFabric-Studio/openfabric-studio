"""Committed QC originals survive upload cancellation and partial batch failure."""
from __future__ import annotations

import asyncio
import io
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile

from app import artists, db, recording_sessions, voice_build
from app.api import routes_voices
from app.artist_contracts import ArtistConsentRequest, CreateArtistRequest
from app.artist_production_test_fixtures import tone_wav_bytes
from app.recording_contracts import RecordingLabels, RecordingPurpose, RecordingSession


class RecordingUploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        for name, value in (('DATA_DIR', self.root), ('DB_PATH', self.root / 'catalog.db'),
                            ('FILES_DIR', self.root / 'files'), ('_db', None)):
            self.enterContext(patch.object(db, name, value))
        self.enterContext(patch.object(voice_build, 'VOICES_DIR', self.root / 'voices'))
        self.enterContext(patch.object(voice_build, 'SEED_VC_DIR', self.root / 'seed-vc'))
        self.voice_id = 'a' * 32
        self.voice = self.root / 'voices' / self.voice_id
        (self.voice / 'recordings').mkdir(parents=True)
        voice_build.write_meta(self.voice, voice_build.new_voice_meta(self.voice_id, 'Synthetic fixture'))
        self.artist = artists.create_artist(CreateArtistRequest(name='Synthetic fixture', voice_id=self.voice_id))
        artists.add_consent_revision(self.artist.id, ArtistConsentRequest(status='active', signed_evidence_ref='synthetic signed evidence',
            operator_label='Fixture operator', human_declaration='The operator reviewed this synthetic fixture permission.',
            allowed_operations=['train'], allowed_uses=['production']))

    async def asyncTearDown(self) -> None:
        if db._db is not None:
            db._db.close()

    def upload(self, name: str = 'take.wav', *, frequency: float = 220, data: bytes | None = None) -> UploadFile:
        return UploadFile(io.BytesIO(tone_wav_bytes(seconds=.2, frequency_hz=frequency) if data is None else data), filename=name)

    async def test_cancel_after_qc_commit_preserves_original_and_indexes_it_before_draining(self) -> None:
        committed, release = threading.Event(), threading.Event()
        inspect = recording_sessions.inspect_file
        def held(path: Path, voice_id: str, purpose: RecordingPurpose, labels: RecordingLabels) -> RecordingSession:
            record = inspect(path, voice_id, purpose, labels)
            committed.set()
            if not release.wait(5):
                raise AssertionError('Fixture QC drain was not released')
            return record
        upload, unvisited = self.upload(), self.upload('later.wav', frequency=330)
        with patch.object(recording_sessions, 'inspect_file', side_effect=held):
            task = asyncio.create_task(routes_voices.upload_recordings(self.voice_id, [upload, unvisited]))
            try:
                self.assertTrue(await asyncio.to_thread(committed.wait, 5))
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        source = self.voice / 'recordings' / 'take.wav'
        self.assertEqual(source.read_bytes(), tone_wav_bytes(seconds=.2))
        self.assertEqual(voice_build.read_meta(self.voice)['recordings'], [{'filename': 'take.wav', 'bytes': source.stat().st_size}])
        self.assertEqual(recording_sessions.list_sessions(self.voice_id)[0].relative_path, 'recordings/take.wav')
        self.assertTrue(upload.file.closed)
        self.assertTrue(unvisited.file.closed)

    async def test_a_later_invalid_upload_does_not_hide_an_earlier_accepted_original(self) -> None:
        first, invalid, unvisited = self.upload('first.wav'), self.upload('bad.wav', data=b'not audio'), self.upload('later.wav', frequency=330)
        with self.assertRaises(HTTPException) as caught:
            await routes_voices.upload_recordings(self.voice_id, [first, invalid, unvisited])
        self.assertEqual(caught.exception.detail, 'recording_invalid_audio')
        accepted = self.voice / 'recordings' / 'first.wav'
        self.assertTrue(accepted.is_file())
        self.assertEqual(voice_build.read_meta(self.voice)['recordings'], [{'filename': accepted.name, 'bytes': accepted.stat().st_size}])
        self.assertFalse((self.voice / 'recordings' / 'bad.wav').exists())
        self.assertTrue(all(upload.file.closed for upload in (first, invalid, unvisited)))

    async def test_skipped_and_unauthorized_uploads_are_closed(self) -> None:
        skipped, accepted = self.upload('bad.txt'), self.upload()
        await routes_voices.upload_recordings(self.voice_id, [skipped, accepted])
        self.assertTrue(skipped.file.closed)
        self.assertTrue(accepted.file.closed)
        artists.add_consent_revision(self.artist.id, ArtistConsentRequest(status='revoked',
            operator_label='Fixture operator', human_declaration='The synthetic fixture permission was revoked.'))
        blocked = self.upload('blocked.wav')
        with self.assertRaises(HTTPException):
            await routes_voices.upload_recordings(self.voice_id, [blocked])
        self.assertTrue(blocked.file.closed)
        self.assertFalse((self.voice / 'recordings' / 'blocked.wav').exists())

    async def test_invalid_labels_close_every_upload_before_creating_files(self) -> None:
        upload, unvisited = self.upload(), self.upload('later.wav')
        with self.assertRaises(HTTPException) as caught:
            await routes_voices.upload_recordings(self.voice_id, [upload, unvisited], recording_labels='{"language":"en","unknown":true}')
        self.assertEqual(caught.exception.detail, 'recording_invalid_labels')
        self.assertTrue(upload.file.closed)
        self.assertTrue(unvisited.file.closed)
        self.assertEqual(list((self.voice / 'recordings').iterdir()), [])

    async def test_revocation_during_qc_keeps_the_committed_original_but_rejects_success(self) -> None:
        committed, release = threading.Event(), threading.Event()
        inspect = recording_sessions.inspect_file
        def held(path: Path, voice_id: str, purpose: RecordingPurpose, labels: RecordingLabels) -> RecordingSession:
            record = inspect(path, voice_id, purpose, labels)
            committed.set()
            if not release.wait(5):
                raise AssertionError('Fixture QC drain was not released')
            return record
        upload = self.upload()
        with patch.object(recording_sessions, 'inspect_file', side_effect=held):
            task = asyncio.create_task(routes_voices.upload_recordings(self.voice_id, [upload]))
            try:
                self.assertTrue(await asyncio.to_thread(committed.wait, 5))
                artists.add_consent_revision(self.artist.id, ArtistConsentRequest(status='revoked',
                    operator_label='Fixture operator', human_declaration='The synthetic fixture permission was revoked.'))
            finally:
                release.set()
            with self.assertRaises(HTTPException) as caught:
                await task
        self.assertEqual(caught.exception.status_code, 403)
        source = self.voice / 'recordings' / 'take.wav'
        self.assertEqual(source.read_bytes(), tone_wav_bytes(seconds=.2))
        self.assertEqual(voice_build.read_meta(self.voice)['recordings'], [{'filename': source.name, 'bytes': source.stat().st_size}])
        self.assertEqual(len(recording_sessions.list_sessions(self.voice_id)), 1)
        self.assertTrue(upload.file.closed)

    async def test_a_copy_name_race_cannot_remove_another_accepted_original(self) -> None:
        await routes_voices.upload_recordings(self.voice_id, [self.upload()])
        original = self.voice / 'recordings' / 'take.wav'
        before = original.read_bytes()
        colliding = self.upload(frequency=330)
        with patch.object(routes_voices, '_unique_dest', return_value=original), self.assertRaises(HTTPException) as caught:
            await routes_voices.upload_recordings(self.voice_id, [colliding])
        self.assertEqual(caught.exception.detail, 'recording_upload_conflict')
        self.assertEqual(original.read_bytes(), before)
        self.assertTrue(colliding.file.closed)

    async def test_a_metadata_write_failure_keeps_the_qc_accepted_original(self) -> None:
        upload = self.upload()
        with self.assertLogs(routes_voices.logger, level='WARNING'), patch.object(routes_voices, 'write_meta', side_effect=OSError('synthetic disk full')), self.assertRaises(HTTPException) as caught:
            await routes_voices.upload_recordings(self.voice_id, [upload])
        self.assertEqual(caught.exception.detail, 'recording_metadata_write_failed')
        self.assertTrue((self.voice / 'recordings' / 'take.wav').is_file())
        self.assertEqual(len(recording_sessions.list_sessions(self.voice_id)), 1)
        self.assertTrue(upload.file.closed)

    async def test_a_later_upload_reconciles_a_qc_record_after_failed_metadata_publication(self) -> None:
        with self.assertLogs(routes_voices.logger, level='WARNING'), patch.object(routes_voices, 'write_meta', side_effect=OSError('synthetic disk full')), self.assertRaises(HTTPException):
            await routes_voices.upload_recordings(self.voice_id, [self.upload('first.wav')])
        await routes_voices.upload_recordings(self.voice_id, [self.upload('second.wav', frequency=330)])
        recordings = voice_build.read_meta(self.voice)['recordings']
        self.assertIsInstance(recordings, list)
        self.assertEqual(recordings, [{'filename': name, 'bytes': (self.voice / 'recordings' / name).stat().st_size}
                                      for name in ('first.wav', 'second.wav')])

    async def test_reconciliation_refuses_changed_bytes_even_with_original_size_and_mtime(self) -> None:
        with self.assertLogs(routes_voices.logger, level='WARNING'), patch.object(routes_voices, 'write_meta', side_effect=OSError('synthetic disk full')), self.assertRaises(HTTPException):
            await routes_voices.upload_recordings(self.voice_id, [self.upload('first.wav')])
        original = self.voice / 'recordings' / 'first.wav'
        before = original.stat()
        original.write_bytes(tone_wav_bytes(seconds=.2, frequency_hz=440))
        os.utime(original, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertLogs(routes_voices.logger, level='WARNING'):
            await routes_voices.upload_recordings(self.voice_id, [self.upload('second.wav', frequency=330)])
        self.assertEqual(voice_build.read_meta(self.voice)['recordings'], [{'filename': 'second.wav', 'bytes': (self.voice / 'recordings' / 'second.wav').stat().st_size}])
        self.assertTrue(original.is_file())
        self.assertEqual(len(recording_sessions.list_sessions(self.voice_id)), 2)

    async def test_repeated_cancellation_during_copy_drains_and_removes_only_unaccepted_partial(self) -> None:
        blocked, release = threading.Event(), threading.Event()
        class BlockedRead(io.BytesIO):
            reads: int = 0
            def read(self, size: int | None = -1) -> bytes:
                self.reads += 1
                if self.reads == 2:
                    blocked.set()
                    if not release.wait(5):
                        raise AssertionError('Fixture copy drain was not released')
                return super().read(size)
        stream = BlockedRead(tone_wav_bytes())
        upload, unvisited = UploadFile(stream, filename='partial.wav'), self.upload('later.wav')
        task = asyncio.create_task(routes_voices.upload_recordings(self.voice_id, [upload, unvisited]))
        try:
            self.assertTrue(await asyncio.to_thread(blocked.wait, 5))
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse((self.voice / 'recordings' / 'partial.wav').exists())
        self.assertEqual(recording_sessions.list_sessions(self.voice_id), [])
        self.assertEqual(voice_build.read_meta(self.voice)['recordings'], [])
        self.assertTrue(stream.closed)
        self.assertTrue(unvisited.file.closed)

    async def test_concurrent_uploads_preserve_both_metadata_updates(self) -> None:
        results = await asyncio.gather(routes_voices.upload_recordings(self.voice_id, [self.upload('first.wav')]),
                             routes_voices.upload_recordings(self.voice_id, [self.upload('second.wav', frequency=330)]),
                             return_exceptions=True)
        # Drain both owned uploads before teardown can close their catalog connection.
        for result in results:
            if isinstance(result, BaseException):
                raise result
        recordings = voice_build.read_meta(self.voice)['recordings']
        if not isinstance(recordings, list):
            self.fail('Recording metadata must be a list')
        self.assertCountEqual(recordings, [{'filename': name, 'bytes': (self.voice / 'recordings' / name).stat().st_size}
                                           for name in ('first.wav', 'second.wav')])


if __name__ == '__main__':
    unittest.main()
