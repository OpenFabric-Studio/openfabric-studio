"""Cloud music persists independently of the browser without retrying paid calls."""
from __future__ import annotations
import asyncio
import io
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, patch
from app import cloud_music, db
from app.cloud_music_contracts import CloudMusicSubmitRequest
from app.openrouter_contracts import OpenRouterQuote, OpenRouterReceipt
from app.openrouter_errors import OpenRouterError


def wav_bytes() -> bytes:
    data = io.BytesIO()
    with wave.open(data, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000); wav.writeframes(b'\0\0'*2400)
    return data.getvalue()


class CloudMusicTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); root = Path(self.temp.name)
        self.quote = OpenRouterQuote(id='a'*32,kind='music',model_id='google/lyria-3-clip-preview',model_fingerprint='b'*64,request_fingerprint='c'*64,estimated_usd=.04,expires_at=99999999999,transfers=['prompt'])
        self.receipt = OpenRouterReceipt(id='d'*32,owner_id='music:test',kind='music',model_id=self.quote.model_id,model_fingerprint='b'*64,request_fingerprint='c'*64,quote_id=self.quote.id,estimated_usd=.04,state='intent',created_at='2026-10-05',updated_at='2026-10-05')
        self.patches = [patch.object(cloud_music,'_closing',False),patch.object(db,'_db',None),patch.object(db,'DATA_DIR',root),patch.object(db,'DB_PATH',root/'catalog.db'),patch.object(db,'FILES_DIR',root/'files'),patch.object(cloud_music.catalog,'validate_quote',return_value=self.quote),patch.object(cloud_music.requests,'prepare',return_value=self.receipt),patch.object(cloud_music.requests,'get',return_value=self.receipt)]
        for item in self.patches: item.start()
        self.connection = db.get_db()
    async def asyncTearDown(self) -> None:
        await cloud_music.shutdown(); self.connection.close()
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()
    def body(self) -> CloudMusicSubmitRequest:
        return CloudMusicSubmitRequest(model=self.quote.model_id,prompt='Warm jazz',title='Test song',quote_id=self.quote.id,transfers_confirmed=True)
    async def test_result_is_validated_and_saved_as_a_normal_track(self) -> None:
        with patch.object(cloud_music,'_generate',new=AsyncMock(return_value=cloud_music.MusicAudio(wav_bytes(),'wav'))):
            job = cloud_music.submit(self.body()); await cloud_music._tasks[job.id]
        final = cloud_music.get(job.id); self.assertEqual(final.status,'done'); self.assertIsNotNone(final.track)
        self.assertEqual(len(db.list_tracks()),1)
        if final.track is None: self.fail('Missing saved track')
        row=db.get_track(final.track.id)
        if row is None: self.fail('Missing track row')
        self.assertEqual(final.track.model,'openrouter'); self.assertTrue(Path(row['audio_path']).is_file())
        self.assertEqual(final.track.params['model_id'],self.quote.model_id)
    async def test_corrupt_media_never_becomes_a_track(self) -> None:
        with patch.object(cloud_music,'_generate',new=AsyncMock(return_value=cloud_music.MusicAudio(b'not audio','wav'))):
            job=cloud_music.submit(self.body()); await cloud_music._tasks[job.id]
        self.assertEqual(cloud_music.get(job.id).status,'failed'); self.assertEqual(db.list_tracks(),[])
    async def test_unknown_submission_remains_unknown_after_recovery(self) -> None:
        generate=AsyncMock(side_effect=OpenRouterError('submission_unknown',409))
        with patch.object(cloud_music,'_generate',new=generate):
            job=cloud_music.submit(self.body()); await cloud_music._tasks[job.id]; await cloud_music.recover()
        self.assertEqual(generate.await_count,1); self.assertEqual(cloud_music.get(job.id).status,'submission_unknown'); self.assertEqual(db.list_tracks(),[])
    async def test_cancellation_drains_the_owned_task(self) -> None:
        started=asyncio.Event()
        async def generate(*_args: object) -> cloud_music.MusicAudio:
            started.set(); await asyncio.Event().wait(); raise AssertionError('unreachable')
        with patch.object(cloud_music,'_generate',side_effect=generate):
            job=cloud_music.submit(self.body()); await started.wait(); await cloud_music.cancel(job.id)
        self.assertFalse(cloud_music.work_busy()); self.assertEqual(cloud_music.get(job.id).status,'canceled_tracking'); self.assertEqual(db.list_tracks(),[])
    async def test_transfer_confirmation_is_required_before_intent(self) -> None:
        with self.assertRaises(OpenRouterError): cloud_music.submit(self.body().model_copy(update={'transfers_confirmed':False}))
        self.assertEqual(cloud_music.list_jobs().jobs,[])

    async def test_failed_catalog_write_can_retry_local_save_without_another_paid_call(self) -> None:
        generate=AsyncMock(return_value=cloud_music.MusicAudio(wav_bytes(),'wav'))
        with patch.object(cloud_music,'_generate',new=generate), patch.object(db,'insert_track',side_effect=OSError('interrupted local publication')):
            job=cloud_music.submit(self.body()); await cloud_music._tasks[job.id]
        self.assertEqual(cloud_music.get(job.id).status,'failed')
        final=cloud_music.retry_save(job.id)
        self.assertEqual(final.status,'done'); self.assertEqual(generate.await_count,1)
        cloud_music.retry_save(job.id); self.assertEqual(len(db.list_tracks()),1)

    async def test_changed_saved_media_is_rejected_on_local_retry(self) -> None:
        with patch.object(cloud_music,'_generate',new=AsyncMock(return_value=cloud_music.MusicAudio(wav_bytes(),'wav'))), patch.object(db,'insert_track',side_effect=OSError('interrupted')):
            job=cloud_music.submit(self.body()); await cloud_music._tasks[job.id]
        cloud_music._output(job).write_bytes(wav_bytes()+b'changed')
        with self.assertRaises(OpenRouterError): cloud_music.retry_save(job.id)
        self.assertEqual(db.list_tracks(),[])

    async def test_cancel_before_worker_starts_never_leaves_a_queued_job(self) -> None:
        generate=AsyncMock(return_value=cloud_music.MusicAudio(wav_bytes(),'wav'))
        with patch.object(cloud_music,'_generate',new=generate):
            job=cloud_music.submit(self.body()); await cloud_music.cancel(job.id)
        self.assertEqual(generate.await_count,0); self.assertEqual(cloud_music.get(job.id).status,'canceled_tracking')
        self.assertNotIn(job.id,cloud_music._tasks)

    async def test_missing_receipt_keeps_history_visible_and_does_not_block_local_recovery(self) -> None:
        job=cloud_music.submit(self.body())
        await cloud_music.cancel(job.id)
        with patch.object(cloud_music.requests,'get',side_effect=OpenRouterError('provider_storage_unavailable')):
            await cloud_music.recover()
            history=cloud_music.list_jobs()
            self.assertEqual(history.jobs[0].status,'submission_unknown')
            self.assertIsNone(history.jobs[0].receipt)
            self.assertFalse(history.jobs[0].can_retry_save)

    async def test_unsafe_retained_output_does_not_abort_recovery_or_read_outside_file(self) -> None:
        job=cloud_music.submit(self.body()); await cloud_music.cancel(job.id)
        job=cloud_music.get(job.id); job.status='running'; cloud_music._store(job)
        target=Path(self.temp.name)/'outside.wav'; target.write_bytes(b'preserve outside bytes')
        cloud_music._output(job).symlink_to(target)
        await cloud_music.recover()
        self.assertEqual(cloud_music.get(job.id).status,'failed')
        self.assertEqual(target.read_bytes(),b'preserve outside bytes')
