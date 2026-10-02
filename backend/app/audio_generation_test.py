"""Generation keeps a lossless source independently of requested exports."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from app import audio_exports, audio_versions
from app.audio_encoding import AudioEncodingSettings
from app import ace_jobs, db


class AudioGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch.object(db, '_db', None), patch.object(db, 'DATA_DIR', root), patch.object(db, 'DB_PATH', root / 'catalog.db'), patch.object(db, 'FILES_DIR', root / 'files')]
        for item in self.patches: item.start()
        self.connection = db.get_db()

    async def asyncTearDown(self) -> None:
        await ace_jobs.shutdown()
        await audio_exports.shutdown_exports()
        self.connection.close()
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    async def test_new_generation_requests_wav_but_remembers_requested_format(self) -> None:
        with patch.object(ace_jobs, '_engine_running', return_value=True), patch.object(ace_jobs, '_engine_json', return_value={'task_id': 'new', 'status': 'queued', 'queue_position': 0}) as engine, patch.object(ace_jobs, 'launch'):
            await ace_jobs.submit({'audio_format': 'mp3', 'batch_size': 1, '_source_audio_format': 'mp3', '_encoding_settings': {'private': True}}, 'Song', None, None)
        sent = engine.call_args.args[2]
        self.assertEqual(sent['audio_format'], 'wav')
        self.assertNotIn('_source_audio_format', sent)
        self.assertNotIn('_encoding_settings', sent)
        job = ace_jobs.get_job('new')
        self.assertEqual(job.audio_format, 'mp3')
        self.assertEqual(job.params['_source_audio_format'], 'wav')
        settings = job.params['_encoding_settings']
        self.assertIsInstance(settings, dict)
        if isinstance(settings, dict):
            mp3 = settings['mp3']
            self.assertIsInstance(mp3, dict)
            if isinstance(mp3, dict): self.assertEqual(mp3['bitrate_kbps'], 320)
        self.assertEqual(ace_jobs._candidate_path(job, 0).suffix, '.wav')

    async def test_legacy_generation_keeps_its_original_candidate_extension(self) -> None:
        ace_jobs.record_job('legacy', {'audio_format': 'flac', 'batch_size': 1}, 'Old', None)
        self.assertEqual(ace_jobs._candidate_path(ace_jobs.get_job('legacy'), 0).suffix, '.flac')

    async def test_batch_captures_originals_and_settings_before_independent_voice_work(self) -> None:
        snapshot = AudioEncodingSettings()
        snapshot.mp3.bitrate_kbps = 192
        params = {'audio_format':'mp3','batch_size':2,'_source_audio_format':'wav',
                  '_encoding_settings':ace_jobs._object.validate_json(snapshot.model_dump_json())}
        ace_jobs.record_job('batch', params, 'Song', 'a' * 32)
        async def download(_client: httpx.AsyncClient, _endpoint: str, target: Path) -> None:
            target.write_bytes(b'lossless fixture')
        exported: list[tuple[int, str, int]] = []
        async def export(track_id: int, version_id: str, _format: str, settings: AudioEncodingSettings, *, update_default: bool) -> None:
            self.assertEqual(len(db.list_tracks()), 2)
            self.assertTrue(update_default)
            self.assertEqual(_format, 'mp3')
            self.assertEqual(audio_versions.resolve_source(track_id, version_id).read_bytes(), b'lossless fixture')
            exported.append((track_id, version_id, settings.mp3.bitrate_kbps))
        def voice(_voice_id: str, track_id: int) -> None:
            self.assertEqual(len(exported), 2)
            self.assertTrue(audio_versions.list_versions(track_id).original_available)
        with patch.object(ace_jobs, '_download', side_effect=download), patch.object(audio_exports, 'create_export', side_effect=export), patch.object(ace_jobs, 'start_apply', side_effect=voice) as apply:
            async with httpx.AsyncClient() as client:
                results = [{'file':'/v1/audio?path=one'}, {'file':'/v1/audio?path=two'}]
                await ace_jobs.save_results(client, 'batch', results)
                await ace_jobs.save_results(client, 'batch', results)
        self.assertEqual(apply.call_count, 2)
        self.assertEqual(len(db.list_tracks()), 2)
        self.assertEqual([item[2] for item in exported], [192]*4)
        self.assertEqual([item[1] for item in exported[:2]], [item[1] for item in exported[2:]])
        self.assertEqual(ace_jobs.get_job('batch').status, 'done')
