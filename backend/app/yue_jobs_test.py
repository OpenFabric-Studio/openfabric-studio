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

from fastapi import HTTPException
from pydantic import ValidationError

from app import db, music_lora, yue_jobs
from app.contracts import JsonObject
from app.export_provenance_contracts import MusicEvidence
from app.music_evidence import capture_music_evidence, track_music_evidence
from app.music_lora_contracts import MusicAdapter, MusicAdapterSelection
from app.music_lora_test import adapter_bytes
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

    async def test_queue_reservation_rejects_external_submit_before_job_publication(self) -> None:
        from app import music_queue_ownership as ownership
        with patch.object(ownership, '_owner', None), patch.object(ownership, '_resources_idle', new=AsyncMock(return_value=True)):
            owner = await ownership.reserve()
            with self.assertRaises(HTTPException) as caught:
                yue_jobs.submit(self.request())
            self.assertEqual(caught.exception.detail, 'music_queue_busy')
            self.assertEqual(yue_jobs.list_jobs(), [])
            with owner.context(), patch.object(yue_jobs, 'launch') as launch:
                job = yue_jobs.submit(self.request())
                launch.assert_called_once_with(job.id)
            self.assertTrue(await owner.release())

    def register_adapter(self, target: str) -> MusicAdapter:
        return music_lora.import_stream(io.BytesIO(adapter_bytes('yue2', target)), filename='style.safetensors',
            name='My ' + target, engine='yue2', target=target)

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
        assert result.track is not None
        self.assertTrue(MusicEvidence.model_validate(result.track.params['_music_evidence']).lineage_complete)
        self.assertTrue(track_music_evidence(result.track.id).lineage_complete)

    async def test_external_score_retains_known_restrictions_without_claiming_complete_lineage(self) -> None:
        request = self.request()
        request.options = YueOptions(style='jazz', cot='melody', abc='X:1\nK:C\nC D E F|')
        native = AsyncMock(return_value={'audio': audio_fixture()})
        with patch.object(yue_jobs, '_load_model', new=AsyncMock()), patch.object(yue_jobs, '_native_json', new=native):
            job = yue_jobs.submit(request)
            await asyncio.wait_for(yue_jobs._tasks[job.id], 2)
        track = yue_jobs.get_job(job.id).track
        assert track is not None
        evidence = MusicEvidence.model_validate(track.params['_music_evidence'])
        self.assertFalse(evidence.lineage_complete)
        self.assertEqual(evidence.components, capture_music_evidence('yue2').components)
        self.assertEqual(evidence.restriction, 'noncommercial')
        inherited = track_music_evidence(track.id)
        self.assertFalse(inherited.lineage_complete)
        self.assertEqual(inherited.restriction, 'noncommercial')
        self.assertEqual(native.await_args_list[0].args[1]['request']['options']['abc'], request.options.abc)

    async def test_musical_guidance_reaches_native_style_and_original_choices_are_saved(self) -> None:
        from app.generation_contracts import YueGenerationSettings
        from pydantic import TypeAdapter
        try:
            options = YueOptions.model_validate({'style': 'Folk', 'cot': 'full',
                'bpm': 88, 'key_scale': 'Am', 'time_signature': '6/8'})
        except ValidationError as error:
            self.fail(f'Musical choices were rejected: {error}')
        native = AsyncMock(return_value={'audio': audio_fixture()})
        with patch.object(yue_jobs, '_load_model', new=AsyncMock()), patch.object(yue_jobs, '_native_json', new=native):
            job = yue_jobs.submit(YueSubmitRequest(lyrics='[Verse]\nA test', seed=12, options=options,
                settings=YueGenerationSettings(engine='yue2', style='Folk', bpm=120, keyScale='C', timeSignature='4/4')))
            await asyncio.wait_for(yue_jobs._tasks[job.id], 2)
        native_options = native.await_args_list[0].args[1]['request']['options']
        self.assertEqual(native_options['style'], 'Folk\nTempo: 88 BPM\nKey: A minor\nTime signature: 6/8')
        self.assertNotIn('bpm', native_options)
        self.assertNotIn('key_scale', native_options)
        self.assertNotIn('time_signature', native_options)
        self.assertNotIn('abc', native_options)
        result = yue_jobs.get_job(job.id)
        self.assertEqual(result.status, 'done')
        self.assertEqual(result.options.style, 'Folk')
        params = TypeAdapter(JsonObject).validate_json(db.list_tracks('yue2')[0]['params_json'])
        self.assertEqual((params['style'], params['bpm'], params['key_scale'], params['time_signature']),
            ('Folk', 88, 'Am', '6/8'))
        snapshot = YueGenerationSettings.model_validate(params['_generation_settings'])
        self.assertEqual((snapshot.bpm, snapshot.keyScale, snapshot.timeSignature), (88, 'Am', '6/8'))
        self.assertIn('_music_evidence', params)
        self.assertIn('noncommercial', str(params['_music_evidence']))

    async def test_disabling_adapters_reloads_a_resident_adapted_session(self) -> None:
        resident = {**yue_jobs._COMPACT_ARENAS, 'yue2.model_gguf': 'yue2-3b-q8_0.gguf',
                    'yue2.ar_lora': '/private/ar.safetensors', 'yue2.ar_lora_scale': '0.5',
                    'yue2.nar_lora': '/private/nar.safetensors', 'yue2.nar_lora_scale': '1.0'}
        native = AsyncMock(side_effect=[{'data': [{'id': 'yue2', 'loaded': True, 'session_options': resident}]}, {}])
        with patch.object(yue_jobs, '_native_json', new=native):
            await yue_jobs._load_model('a' * 32, 'q8_0')
        self.assertEqual(native.await_count, 2)
        self.assertEqual(native.await_args_list[1].args[0], '/v1/models/load')
        requested = native.await_args_list[1].args[1]['session_options']
        self.assertFalse(any('lora' in key for key in requested))

    async def test_disabling_legacy_adapter_reloads_its_session(self) -> None:
        resident = {**yue_jobs._COMPACT_ARENAS, 'yue2.model_gguf': 'yue2-3b-q8_0.gguf',
                    'yue2.lora': '/private/ar.safetensors', 'yue2.lora_scale': '0.5'}
        native = AsyncMock(side_effect=[{'data': [{'id': 'yue2', 'loaded': True, 'session_options': resident}]}, {}])
        with patch.object(yue_jobs, '_native_json', new=native):
            await yue_jobs._load_model('a' * 32, 'q8_0')
        self.assertEqual(native.await_count, 2)
        requested = native.await_args_list[1].args[1]['session_options']
        self.assertFalse(any('lora' in key for key in requested))

    def test_adapter_selection_is_an_id_and_bounded_strength_not_a_native_path(self) -> None:
        raw = self.request().model_dump()
        raw['ar_adapter'] = {'id': 'a' * 32, 'strength': 0.5}
        raw['nar_adapter'] = {'id': 'b' * 32, 'strength': 0.0}
        try:
            restored = YueSubmitRequest.model_validate(raw).model_dump()
        except ValidationError as exc:
            self.fail(f'Valid managed adapter selections were rejected: {exc}')
        self.assertEqual(restored['ar_adapter'], raw['ar_adapter'])
        self.assertEqual(restored['nar_adapter'], raw['nar_adapter'])
        for bad in ({'id': '/private/ar.safetensors', 'strength': 1.0},
                    {'id': 'a' * 32, 'strength': 2.1}, {'id': 'a' * 32, 'strength': float('nan')},
                    {'id': 'a' * 32, 'strength': 1.0, 'path': '/private/file'}):
            with self.subTest(selection=bad), self.assertRaises(ValidationError):
                YueSubmitRequest.model_validate({**raw, 'ar_adapter': bad})

    async def test_native_load_uses_independent_adapters_and_saves_their_provenance(self) -> None:
        ar, nar = self.register_adapter('ar'), self.register_adapter('nar')
        request = self.request()
        request.ar_adapter = MusicAdapterSelection(id=ar.id, strength=0.5)
        request.nar_adapter = MusicAdapterSelection(id=nar.id, strength=0.0)
        calls: list[tuple[str, JsonObject | None]] = []
        async def native(endpoint: str, payload: JsonObject | None) -> JsonObject:
            calls.append((endpoint, payload))
            if endpoint.startswith('/v1/models?'):
                return {'data': []}
            return {'audio': audio_fixture()} if endpoint == '/v1/tasks/run' else {}
        with patch.object(yue_jobs, '_native_json', side_effect=native):
            queued = yue_jobs.submit(request)
            self.assertEqual(queued.ar_adapter, request.ar_adapter)
            self.assertEqual(queued.nar_adapter, request.nar_adapter)
            await asyncio.wait_for(yue_jobs._tasks[queued.id], 2)
        loaded = calls[1][1]
        assert loaded is not None
        session = loaded['session_options']
        self.assertEqual(session, {**yue_jobs._COMPACT_ARENAS, 'yue2.model_gguf': 'yue2-3b-q8_0.gguf',
            'yue2.ar_lora': str(music_lora.resolve(request.ar_adapter, engine='yue2', target='ar')),
            'yue2.ar_lora_scale': '0.5',
            'yue2.nar_lora': str(music_lora.resolve(request.nar_adapter, engine='yue2', target='nar')),
            'yue2.nar_lora_scale': '0.0'})
        generated = calls[2][1]
        assert generated is not None
        self.assertNotIn('ar_adapter', str(generated))
        self.assertNotIn('nar_adapter', str(generated))
        track = yue_jobs.get_job(queued.id).track
        assert track is not None
        self.assertEqual(track.params['ar_adapter'], {'id': ar.id, 'strength': 0.5})
        self.assertEqual(track.params['nar_adapter'], {'id': nar.id, 'strength': 0.0})
        self.assertEqual(track.params['_music_adapters'], [
            {**ar.model_dump(mode='json'), 'strength': 0.5}, {**nar.model_dump(mode='json'), 'strength': 0.0}])
        self.assertIn('arAdapter', str(track.params['_generation_settings']))
        self.assertNotIn(str(db.DATA_DIR), str(track.params))
        evidence = track_music_evidence(track.id)
        self.assertTrue(evidence.lineage_complete)
        self.assertEqual([adapter.sha256 for adapter in evidence.adapters], [ar.sha256, nar.sha256])

    def test_incompatible_adapter_is_rejected_before_a_job_is_created(self) -> None:
        adapter = self.register_adapter('nar')
        request = self.request()
        request.ar_adapter = MusicAdapterSelection(id=adapter.id)
        with patch.object(yue_jobs, 'launch'), self.assertRaises(HTTPException) as caught:
            yue_jobs.submit(request)
        self.assertEqual(caught.exception.detail, 'music_lora_target_mismatch')
        self.assertEqual(yue_jobs.list_jobs(), [])

    def test_legacy_runtime_rejects_adapter_requests_but_preserves_plain_generation(self) -> None:
        adapter = self.register_adapter('ar')
        request = self.request()
        request.ar_adapter = MusicAdapterSelection(id=adapter.id)
        with patch.object(yue_jobs, 'YUE2_LORA_AVAILABLE', False), patch.object(yue_jobs, 'launch'):
            with self.assertRaises(HTTPException) as caught:
                yue_jobs.submit(request)
            self.assertEqual(caught.exception.detail, 'music_lora_runtime_unsupported')
            self.assertEqual(yue_jobs.submit(self.request()).status, 'queued')
        self.assertEqual(len(yue_jobs.list_jobs()), 1)

    async def test_changed_runtime_in_queue_cannot_silently_ignore_adapters(self) -> None:
        adapter = self.register_adapter('ar')
        request = self.request()
        request.ar_adapter = MusicAdapterSelection(id=adapter.id)
        with patch.object(yue_jobs, 'launch'):
            queued = yue_jobs.submit(request)
        native = AsyncMock(return_value={'audio': audio_fixture()})
        with patch.object(yue_jobs, 'YUE2_LORA_AVAILABLE', False), \
             patch.object(yue_jobs, '_native_json', new=native), self.assertLogs('app.yue_jobs', level='ERROR'):
            yue_jobs.launch(queued.id)
            await asyncio.wait_for(yue_jobs._tasks[queued.id], 2)
        native.assert_not_awaited()
        self.assertEqual(yue_jobs.get_job(queued.id).error_code, 'music_lora_runtime_unsupported')

    async def test_queued_adapter_manifest_must_match_accepted_provenance(self) -> None:
        adapter = self.register_adapter('ar')
        request = self.request()
        request.ar_adapter = MusicAdapterSelection(id=adapter.id)
        with patch.object(yue_jobs, 'launch'):
            queued = yue_jobs.submit(request)
        manifest = db.DATA_DIR / 'music-lora' / adapter.id / 'manifest.json'
        manifest.write_text(adapter.model_copy(update={'name': 'Changed after admission'}).model_dump_json())
        native = AsyncMock(return_value={'audio': audio_fixture()})
        with patch.object(yue_jobs, '_native_json', new=native), self.assertLogs('app.yue_jobs', level='ERROR'):
            yue_jobs.launch(queued.id)
            await asyncio.wait_for(yue_jobs._tasks[queued.id], 2)
        native.assert_not_awaited()
        self.assertEqual(yue_jobs.get_job(queued.id).error_code, 'music_lora_adapter_changed')

    async def test_changed_adapter_in_queue_fails_before_native_mutation(self) -> None:
        adapter = self.register_adapter('ar')
        request = self.request()
        request.ar_adapter = MusicAdapterSelection(id=adapter.id)
        with patch.object(yue_jobs, 'launch'):
            queued = yue_jobs.submit(request)
        path = music_lora.resolve(request.ar_adapter, engine='yue2', target='ar')
        path.write_bytes(path.read_bytes() + b'changed')
        native = AsyncMock(return_value={'audio': audio_fixture()})
        with patch.object(yue_jobs, '_native_json', new=native), self.assertLogs('app.yue_jobs', level='ERROR'):
            yue_jobs.launch(queued.id)
            await asyncio.wait_for(yue_jobs._tasks[queued.id], 2)
        native.assert_not_awaited()
        result = yue_jobs.get_job(queued.id)
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.error_code, 'music_lora_adapter_changed')
        self.assertNotIn(str(db.DATA_DIR), result.model_dump_json())

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

    async def test_catalog_failure_has_save_error_and_recovers_retained_audio_without_inference(self) -> None:
        native = AsyncMock(return_value={'audio': audio_fixture()})
        with patch.object(yue_jobs, '_load_model', new=AsyncMock()), \
             patch.object(yue_jobs, '_native_json', new=native), \
             patch.object(db, 'insert_track', side_effect=OSError('private storage failure')), \
             self.assertLogs('app.yue_jobs', level='ERROR'):
            job = yue_jobs.submit(self.request())
            await asyncio.wait_for(yue_jobs._tasks[job.id], 2)
        failed = yue_jobs.get_job(job.id)
        self.assertEqual(failed.status, 'failed')
        self.assertEqual(failed.error_code, 'save_failed')
        retained = db.FILES_DIR / 'yue2' / f'yue_{job.id}.wav'
        self.assertTrue(retained.is_file())
        self.assertEqual(db.list_tracks(), [])
        with patch.object(yue_jobs, '_native_json', new=native):
            yue_jobs.recover()
            yue_jobs.recover()
        recovered = yue_jobs.get_job(job.id)
        self.assertEqual(recovered.status, 'done')
        self.assertIsNotNone(recovered.track)
        self.assertEqual(len(db.list_tracks()), 1)
        self.assertEqual(native.await_count, 1)
        self.assertTrue(retained.is_file())

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
        self.assertEqual(yue_jobs.get_job(job.id).error_code, 'generation_failed')
        self.assertEqual(db.list_tracks(), [])

    def test_untrusted_native_options_are_bounded(self) -> None:
        for options in ({'style': 'jazz', 'num_inference_steps': 257},
                        {'style': 'jazz', 'semantic_top_p': 0},
                        {'style': 'jazz', 'abc': 'K:C\nC', 'cot': 'off'},
                        {'style': 'jazz', 'abc_file': '/private/file'},
                        {'style': 'jazz', 'semantic_min_tokens': 100, 'semantic_max_tokens': 99}):
            with self.subTest(options=options), self.assertRaises(ValidationError):
                YueOptions.model_validate(options)
