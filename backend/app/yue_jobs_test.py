"""Owned YuE inference, durable save and honest cancellation regressions."""
from __future__ import annotations

import asyncio
import base64
import io
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app import db, yue_jobs
from app.contracts import JsonObject
from app.yue_contracts import YueOptions, YueSubmitRequest


def audio_fixture() -> str:
    data = io.BytesIO()
    with wave.open(data, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(48000)
        wav.writeframes(b'\0\0' * 480)
    return base64.b64encode(data.getvalue()).decode('ascii')


class YueJobsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch.object(db, '_db', None), patch.object(db, 'DATA_DIR', root),
                        patch.object(db, 'DB_PATH', root / 'catalog.db'), patch.object(db, 'FILES_DIR', root / 'files'),
                        patch.object(yue_jobs, '_engine_running', return_value=True)]
        for item in self.patches: item.start()
        self.connection = db.get_db()

    async def asyncTearDown(self) -> None:
        await yue_jobs.shutdown()
        self.connection.close()
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def request(self, seed: int = 12) -> YueSubmitRequest:
        return YueSubmitRequest(lyrics='[Verse]\nA test', seed=seed, options=YueOptions(style='jazz'))

    async def test_completion_persists_track_without_a_browser(self) -> None:
        async def native(endpoint: str, payload: JsonObject) -> JsonObject:
            return {'audio': audio_fixture(), 'timing': {'wall_ms': 10, 'audio_duration_ms': 10}}
        with patch.object(yue_jobs, '_load_model', new=AsyncMock()), patch.object(yue_jobs, '_native_json', side_effect=native):
            job = yue_jobs.submit(self.request())
            await asyncio.wait_for(yue_jobs._tasks[job.id], 2)
        result = yue_jobs.get_job(job.id)
        self.assertEqual(result.status, 'done')
        self.assertIsNotNone(result.track)
        self.assertEqual(len(db.list_tracks('yue2')), 1)
        self.assertTrue(Path(db.list_tracks('yue2')[0]['audio_path']).is_file())
        self.assertFalse(yue_jobs.work_busy())

    async def test_cancel_queued_job_never_resets_another_job(self) -> None:
        entered = asyncio.Event()
        async def native(endpoint: str, payload: JsonObject) -> JsonObject:
            entered.set(); await asyncio.Future()
            return {}
        with patch.object(yue_jobs, '_load_model', new=AsyncMock()), \
             patch.object(yue_jobs, '_native_json', side_effect=native), \
             patch.object(yue_jobs, '_restart_owned_engine', new=AsyncMock()) as reset:
            first = yue_jobs.submit(self.request())
            await asyncio.wait_for(entered.wait(), 2)
            second = yue_jobs.submit(self.request(13))
            result = await yue_jobs.cancel(second.id)
            self.assertEqual(result.status, 'cancelled')
            reset.assert_not_awaited()
            self.assertEqual(yue_jobs.get_job(first.id).status, 'running')
            await yue_jobs.cancel(first.id)

    async def test_cancel_active_job_holds_next_job_until_restart_finishes(self) -> None:
        entered, restart_entered, restarted = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = 0
        async def native(endpoint: str, payload: JsonObject) -> JsonObject:
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set(); await asyncio.Future()
            return {'audio': audio_fixture()}
        async def restart() -> None:
            restart_entered.set(); await restarted.wait()
        with patch.object(yue_jobs, '_load_model', new=AsyncMock()), \
             patch.object(yue_jobs, '_native_json', side_effect=native), \
             patch.object(yue_jobs, '_restart_owned_engine', side_effect=restart):
            first = yue_jobs.submit(self.request())
            await entered.wait()
            second = yue_jobs.submit(self.request(13))
            cancellation = asyncio.create_task(yue_jobs.cancel(first.id))
            await restart_entered.wait()
            self.assertEqual(yue_jobs.get_job(first.id).status, 'stopping')
            self.assertEqual(yue_jobs.get_job(second.id).status, 'queued')
            self.assertEqual(calls, 1)
            restarted.set(); await cancellation
            await asyncio.wait_for(yue_jobs._tasks[second.id], 2)
        self.assertEqual(yue_jobs.get_job(second.id).status, 'done')

    async def test_failed_stop_retains_admission_and_requires_recovery(self) -> None:
        entered = asyncio.Event()
        async def native(endpoint: str, payload: JsonObject) -> JsonObject:
            entered.set(); await asyncio.Future()
            return {}
        with patch.object(yue_jobs, '_load_model', new=AsyncMock()), \
             patch.object(yue_jobs, '_native_json', side_effect=native), \
             patch.object(yue_jobs, '_restart_owned_engine', new=AsyncMock(side_effect=RuntimeError('private path'))):
            first = yue_jobs.submit(self.request())
            await entered.wait()
            result = await yue_jobs.cancel(first.id)
            self.assertEqual(result.status, 'stopping')
            self.assertEqual(result.error_code, 'engine_recovery_required')
            self.assertTrue(yue_jobs.work_busy())
            self.assertNotIn('private', result.model_dump_json())
        with patch.object(yue_jobs, '_restart_owned_engine', new=AsyncMock()):
            self.assertEqual((await yue_jobs.cancel(first.id)).status, 'cancelled')

    async def test_wrong_run_stale_or_malformed_progress_is_ignored(self) -> None:
        with patch.object(yue_jobs, 'launch'):
            first = yue_jobs.submit(self.request())
        path = yue_jobs.progress_path()
        stored = yue_jobs._read(first.id)
        stored.response.status, stored.response.stage = 'running', 'generating'
        yue_jobs._store(stored)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"run_id":"' + 'b' * 32 + '","phase":"decode","current":1,"total":2,"started_ms":1,"phase_started_ms":1,"updated_ms":2}')
        self.assertIsNone(yue_jobs.get_job(first.id).progress)
        now = int(time.time() * 1000)
        path.write_text('{"run_id":"' + first.id + '","phase":"acoustic","current":2,"total":10,"started_ms":' + str(now - 5000) + ',"phase_started_ms":' + str(now - 4000) + ',"updated_ms":' + str(now) + '}')
        measured = yue_jobs.get_job(first.id)
        self.assertIsNotNone(measured.progress)
        self.assertEqual(measured.phase_eta_seconds, 16)
        path.write_text(path.read_text().replace('acoustic', 'semantic'))
        self.assertIsNone(yue_jobs.get_job(first.id).phase_eta_seconds)
        path.write_text('[]')
        self.assertIsNone(yue_jobs.get_job(first.id).progress)
        path.write_text('x' * 8193)
        self.assertIsNone(yue_jobs.get_job(first.id).progress)

    async def test_score_artifact_remains_available_on_saved_track(self) -> None:
        score = 'X:1\nK:C\nC D E F|'
        with patch.object(yue_jobs, '_load_model', new=AsyncMock()), patch.object(yue_jobs, '_native_json', new=AsyncMock(return_value={
            'audio': audio_fixture(), 'artifacts': [{'id': 'score', 'payload': base64.b64encode(score.encode()).decode(), 'meta': {'format': 'abc'}}]})):
            job = yue_jobs.submit(self.request())
            await yue_jobs._tasks[job.id]
        row = db.list_tracks()[0]
        self.assertEqual(Path(row['abc_path']).read_text(), score)

    async def test_recovery_catalogues_completed_artifact_without_inference(self) -> None:
        with patch.object(yue_jobs, 'launch'):
            job = yue_jobs.submit(self.request())
        stored = yue_jobs._read(job.id)
        stored.response.status, stored.response.stage = 'running', 'saving'
        yue_jobs._store(stored)
        path = db.model_dir('yue2') / f'yue_{job.id}.wav'
        path.write_bytes(base64.b64decode(audio_fixture()))
        yue_jobs.recover()
        result = yue_jobs.get_job(job.id)
        self.assertEqual(result.status, 'done')
        self.assertIsNotNone(result.track)
        yue_jobs.recover()
        self.assertEqual(len(db.list_tracks()), 1)

    async def test_deleted_track_does_not_reappear_from_durable_job_feed(self) -> None:
        with patch.object(yue_jobs, '_load_model', new=AsyncMock()), patch.object(yue_jobs, '_native_json', new=AsyncMock(return_value={'audio': audio_fixture()})):
            job = yue_jobs.submit(self.request())
            await yue_jobs._tasks[job.id]
        row = db.list_tracks()[0]
        db.delete_track(row['id'])
        self.assertEqual(yue_jobs.list_jobs(), [])

    async def test_shutdown_cancellation_after_publication_finishes_completion(self) -> None:
        releasing, released = asyncio.Event(), asyncio.Event()
        original_release = yue_jobs._release
        calls = 0
        async def release(identifier: str) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                releasing.set()
                await released.wait()
            await original_release(identifier)
        with patch.object(yue_jobs, '_load_model', new=AsyncMock()), patch.object(yue_jobs, '_native_json', new=AsyncMock(return_value={'audio': audio_fixture()})), patch.object(yue_jobs, '_release', side_effect=release):
            job = yue_jobs.submit(self.request())
            task = yue_jobs._tasks[job.id]
            await releasing.wait()
            self.assertIsNotNone(yue_jobs.get_job(job.id).track)
            task.cancel()
            released.set()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(yue_jobs.get_job(job.id).status, 'done')

    async def test_engine_result_rejects_non_audio_without_publishing_track(self) -> None:
        with patch.object(yue_jobs, '_load_model', new=AsyncMock()), \
             patch.object(yue_jobs, '_native_json', new=AsyncMock(return_value={'audio': base64.b64encode(b'invalid').decode()})):
            job = yue_jobs.submit(self.request())
            with self.assertLogs('app.yue_jobs', level='ERROR'):
                await asyncio.wait_for(yue_jobs._tasks[job.id], 2)
        self.assertEqual(yue_jobs.get_job(job.id).status, 'failed')
        self.assertEqual(db.list_tracks(), [])

    def test_untrusted_native_options_are_bounded(self) -> None:
        for options in ({'style': 'jazz', 'num_inference_steps': 257},
                        {'style': 'jazz', 'semantic_top_p': 0},
                        {'style': 'jazz', 'abc': 'K:C\nC', 'cot': 'off'},
                        {'style': 'jazz', 'abc_file': '/private/file'},
                        {'style': 'jazz', 'semantic_min_tokens': 100, 'semantic_max_tokens': 99}):
            with self.subTest(options=options), self.assertRaises(ValidationError):
                YueOptions.model_validate(options)
