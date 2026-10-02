"""Replacement upload boundaries use a private catalog and CPU audio fixtures."""
from __future__ import annotations

import asyncio
import io
import os
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
import wave
from collections.abc import Callable
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI, HTTPException, UploadFile

from app import client_contracts, db, voice_build
from app.api import routes_tracks, routes_voices


def wav_bytes(seconds: float = .1) -> bytes:
    data = io.BytesIO()
    with wave.open(data, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(b'\x01\x00' * round(seconds * 8000))
    return data.getvalue()


class VoiceReplacementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        for name, value in [('DATA_DIR', self.root), ('DB_PATH', self.root / 'catalog.db'),
                            ('FILES_DIR', self.root / 'files'), ('VIDEOS_DIR', self.root / 'videos'), ('_db', None)]:
            self.enterContext(patch.object(db, name, value))
        self.enterContext(patch.object(voice_build, 'VOICES_DIR', self.root / 'voices'))
        self.enterContext(patch.object(voice_build, 'SEED_VC_DIR', self.root / 'engine'))
        self.enterContext(patch.object(voice_build, '_child_env', return_value=os.environ.copy()))
        self.voice_id = 'a' * 32
        self.voice = self.root / 'voices' / self.voice_id
        self.voice.mkdir(parents=True)
        meta = voice_build.new_voice_meta(self.voice_id, 'Singer')
        meta['status'] = 'ready'
        for filename, field in [('model.pth', 'checkpoint'), ('config.yml', 'config'), ('reference.wav', 'reference')]:
            path = self.voice / filename
            path.write_bytes(b'fixture')
            meta[field] = str(path)
        voice_build.write_meta(self.voice, meta)
        self.started = asyncio.Event()
        self.real_apply = voice_build._apply_inner
        self.enterContext(patch.object(voice_build, 'separate_file', new=AsyncMock(side_effect=AssertionError('Unmocked model work is forbidden'))))

        async def hold_conversion(job: voice_build.ApplyJob) -> None:
            self.started.set()
            await asyncio.Event().wait()

        self.enterContext(patch.object(voice_build, '_apply_inner', side_effect=hold_conversion))
        app = FastAPI()
        app.include_router(routes_voices.router)
        app.include_router(routes_tracks.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def asyncTearDown(self) -> None:
        await voice_build.shutdown()
        await self.client.aclose()
        if db._db is not None:
            db._db.close()

    async def replace(self, *, voice_id: str | None = None, filename: str = 'song.wav', data: bytes | None = None) -> httpx.Response:
        return await self.client.post('/api/voices/replace', data={'voice_id': voice_id or self.voice_id},
                                      files={'audio': (filename, wav_bytes() if data is None else data, 'audio/wav')})

    def test_response_schema_is_registered(self) -> None:
        response = getattr(client_contracts, 'VoiceReplacementResponse', None)
        self.assertIsNotNone(response)
        self.assertIn(response, client_contracts.EXTRA_CLIENT_MODELS)

    async def test_unknown_voice_rejected_before_library_writes(self) -> None:
        response = await self.replace(voice_id='b' * 32)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['detail'], 'voice_missing')
        self.assertEqual(db.list_tracks(), [])
        self.assertFalse((self.root / 'files').exists())

    async def test_replacement_persists_independent_original_and_result(self) -> None:
        original = wav_bytes()
        response = await self.replace(data=original, filename='../song.wav')
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['application']['status'], 'queued')
        self.assertEqual(result['application']['voice_id'], self.voice_id)
        self.assertEqual(result['track']['model'], 'upload')
        self.assertEqual(result['source_track']['params']['source'], 'voice_replacement_source')
        self.assertEqual(result['track']['params']['source'], 'voice_replacement')
        self.assertEqual(result['track']['params']['source_track_id'], result['source_track']['id'])
        self.assertEqual(result['track']['params']['source_filename'], 'song.wav')
        self.assertEqual(result['track']['params']['audio_format'], 'wav')
        source = Path(db.get_track(result['source_track']['id'])['audio_path'])
        target = Path(db.get_track(result['track']['id'])['audio_path'])
        self.assertNotEqual(source, target)
        self.assertEqual(source.read_bytes(), original)
        self.assertTrue(target.is_relative_to((self.root / 'files').resolve()))
        await self.started.wait()
        deleted = await self.client.delete(f"/api/tracks/{result['track']['id']}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertNotIn(result['track']['id'], voice_build._applies)
        self.assertEqual(source.read_bytes(), original)
        self.assertIsNotNone(db.get_track(result['source_track']['id']))

    async def test_cancellation_drains_a_blocked_upload_reader_before_closing(self) -> None:
        from app import voice_replacement as replacement
        entered, release = threading.Event(), threading.Event()

        class SlowReader(io.BytesIO):
            def read(self, size: int = -1) -> bytes:
                entered.set()
                release.wait(5)
                return super().read(size)

        reader = SlowReader(wav_bytes())
        task = asyncio.create_task(replacement.replace_voice(UploadFile(reader, filename='song.wav'), self.voice_id))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            task.cancel()
            await asyncio.sleep(.05)
            self.assertFalse(task.done(), 'Acceptance returned while its upload reader was still running')
            self.assertFalse(reader.closed)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(reader.closed)
        self.assertEqual(db.list_tracks(), [])
        self.assertEqual(list((self.root / 'files').rglob('*.*')), [])

    async def test_invalid_audio_rolls_back_every_file(self) -> None:
        response = await self.replace(data=b'not an audio stream')
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()['detail'], 'invalid_audio')
        self.assertEqual(db.list_tracks(), [])
        self.assertEqual(list((self.root / 'files').rglob('*.*')), [])

    async def test_unready_voice_rejected_before_writes(self) -> None:
        meta = voice_build.read_meta(self.voice)
        meta['status'] = 'training'
        voice_build.write_meta(self.voice, meta)
        response = await self.replace()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail'], 'not_ready')
        self.assertEqual(db.list_tracks(), [])
        self.assertFalse((self.root / 'files').exists())

    async def test_missing_voice_artifact_is_not_ready(self) -> None:
        (self.voice / 'model.pth').unlink()
        response = await self.replace()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail'], 'not_ready')
        self.assertFalse((self.root / 'files').exists())

    async def test_oversized_upload_is_bounded_and_rolled_back(self) -> None:
        from app import voice_replacement as replacement
        self.assertEqual(replacement.MAX_UPLOAD_BYTES, 256 * 1024 * 1024)
        with patch.object(replacement, 'MAX_UPLOAD_BYTES', 64):
            response = await self.replace()
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()['detail'], 'upload_too_large')
        self.assertEqual(db.list_tracks(), [])
        self.assertEqual(list((self.root / 'files').rglob('*.*')), [])

    async def test_unsupported_extension_rejected_before_writes(self) -> None:
        response = await self.replace(filename='song.exe')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['detail'], 'unsupported_audio_format')
        self.assertFalse((self.root / 'files').exists())

    async def test_storage_failure_rolls_back_both_tracks_and_files(self) -> None:
        real_insert = db.insert_track
        count = 0
        def insert(**kwargs: object) -> int:
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('private storage details')
            return real_insert(**kwargs)
        with patch.object(db, 'insert_track', side_effect=insert), self.assertLogs('app.voice_replacement', level='ERROR'):
            response = await self.replace()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()['detail'], 'replacement_storage_failed')
        self.assertNotIn('private storage', response.text)
        self.assertEqual(db.list_tracks(), [])
        self.assertEqual(list((self.root / 'files').rglob('*.*')), [])

    async def test_apply_persistence_failure_does_not_leave_a_registry_entry(self) -> None:
        with patch.object(voice_build, '_write_apply', side_effect=OSError('private status details')), self.assertLogs('app.voice_replacement', level='ERROR'):
            response = await self.replace()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(voice_build._applies, {})
        self.assertEqual(db.list_tracks(), [])
        self.assertEqual(list((self.root / 'files').rglob('*.*')), [])

    async def test_underlying_apply_metadata_write_failure_rejects_acceptance(self) -> None:
        with patch.object(voice_build, 'write_object', side_effect=OSError('disk full')), self.assertLogs('app.voice_replacement', level='ERROR'):
            response = await self.replace()
        self.assertEqual(response.status_code, 500, response.text)
        self.assertEqual(response.json()['detail'], 'replacement_storage_failed')
        self.assertEqual(voice_build._applies, {})
        self.assertEqual(db.list_tracks(), [])
        self.assertEqual(list((self.root / 'files').rglob('*.*')), [])

    async def test_apply_start_failure_after_enqueue_drains_and_rolls_back(self) -> None:
        real_start = voice_build.start_apply
        def fail(voice_id: str, track_id: int) -> dict:
            real_start(voice_id, track_id)
            raise OSError('failed after enqueue')
        with patch.object(voice_build, 'start_apply', side_effect=fail), self.assertLogs('app.voice_replacement', level='ERROR'):
            response = await self.replace()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(voice_build._applies, {})
        self.assertFalse(self.started.is_set(), 'Queued conversion started during rollback')
        self.assertEqual(db.list_tracks(), [])
        self.assertEqual(list((self.root / 'files').rglob('*.*')), [])

    async def test_cancel_endpoint_drains_conversion_and_preserves_both_rows(self) -> None:
        response = await self.replace()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        await self.started.wait()
        cancelled = await self.client.post(f"/api/voices/apply/{result['track']['id']}/cancel")
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()['status'], 'cancelled')
        self.assertNotIn(result['track']['id'], voice_build._applies)
        self.assertEqual(len(db.list_tracks()), 2)
        self.assertTrue(Path(db.get_track(result['source_track']['id'])['audio_path']).is_file())

    async def test_symlinked_upload_directory_cannot_escape_library(self) -> None:
        (self.root / 'files').mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        (self.root / 'files' / 'upload').symlink_to(outside, target_is_directory=True)
        response = await self.replace()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['detail'], 'invalid_storage')
        self.assertEqual(list(outside.iterdir()), [])

    async def test_new_apply_cannot_start_during_track_removal(self) -> None:
        response = await self.replace()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        track_id = result['track']['id']
        async with voice_build.protect_track_removal(track_id):
            with self.assertRaises(HTTPException) as error:
                voice_build.start_apply(self.voice_id, track_id)
            self.assertEqual(error.exception.detail, 'apply_busy')

    async def test_probe_rejects_nonfinite_nonpositive_and_overlong_audio(self) -> None:
        from app import voice_replacement as replacement
        self.assertEqual(replacement.MAX_DURATION_SECONDS, 7200)
        for output, expected in [(b'NaN', 'invalid_audio'), (b'inf', 'invalid_audio'),
                                 (b'0', 'invalid_audio'), (b'-1', 'invalid_audio'), (b'7201', 'audio_too_long')]:
            with self.subTest(output=output), patch.object(replacement, 'spawn_process', new=AsyncMock(return_value=Mock(returncode=0))), \
                 patch.object(replacement, 'communicate_process', new=AsyncMock(return_value=(output, b''))):
                with self.assertRaises(HTTPException) as error:
                    await replacement._duration(self.root / 'fixture.wav')
                self.assertEqual(error.exception.detail, expected)

    async def test_probe_failure_rolls_back_before_track_acceptance(self) -> None:
        from app import voice_replacement as replacement
        with patch.object(replacement, '_duration', new=AsyncMock(side_effect=HTTPException(status_code=400, detail='audio_too_long'))):
            response = await self.replace()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['detail'], 'audio_too_long')
        self.assertEqual(db.list_tracks(), [])
        self.assertEqual(list((self.root / 'files').rglob('*.*')), [])

    async def test_voice_becoming_unready_during_decode_rolls_back(self) -> None:
        from app import voice_replacement as replacement
        decode = replacement._decode
        async def invalidate(source: Path, destination: Path) -> float:
            seconds = await decode(source, destination)
            (self.voice / 'reference.wav').unlink()
            return seconds
        with patch.object(replacement, '_decode', side_effect=invalidate):
            response = await self.replace()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(db.list_tracks(), [])
        self.assertEqual(list((self.root / 'files').rglob('*.*')), [])

    async def test_cancelling_validation_kills_and_awaits_decoder_descendant(self) -> None:
        from app import voice_replacement as replacement
        marker = self.root / 'child.pid'
        fake = self.root / 'decoder'
        child = f"import os,time;from pathlib import Path;Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(60)"
        fake.write_text(f'#!{sys.executable}\nimport subprocess,sys\nsubprocess.Popen([sys.executable,"-c",{child!r}]).wait()\n')
        fake.chmod(0o700)
        real_tool = voice_build._tool
        with patch.object(voice_build, '_tool', side_effect=lambda name: str(fake) if name == 'ffmpeg' else real_tool(name)):
            task = asyncio.create_task(replacement.replace_voice(UploadFile(io.BytesIO(wav_bytes()), filename='song.wav'), self.voice_id))
            pid: int | None = None
            try:
                for _ in range(250):
                    if marker.exists():
                        break
                    await asyncio.sleep(.02)
                self.assertTrue(marker.exists())
                pid = int(marker.read_text())
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                state = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True, check=False).stdout.strip()
                self.assertTrue(not state or state.startswith('Z'), f'Decoder descendant is still running: {state}')
                self.assertEqual(db.list_tracks(), [])
                self.assertEqual(list((self.root / 'files').rglob('*.*')), [])
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if pid is not None:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    async def test_cancel_endpoint_reports_persistence_cleanup_failure(self) -> None:
        response = await self.replace()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        await self.started.wait()
        with patch.object(voice_build, '_write_apply', side_effect=OSError('private metadata details')), self.assertLogs('app.voice_build', level='ERROR'):
            response = await self.client.post(f"/api/voices/apply/{result['track']['id']}/cancel")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail'], 'apply_cleanup_failed')
        self.assertNotIn('private metadata', response.text)
        self.assertNotIn(result['track']['id'], voice_build._applies)
        self.assertEqual(len(db.list_tracks()), 2)

    async def test_cancellation_while_closing_upload_precedes_acceptance(self) -> None:
        from app import voice_replacement as replacement
        closing, release = asyncio.Event(), asyncio.Event()
        class ClosingUpload(UploadFile):
            async def close(self) -> None:
                closing.set()
                await release.wait()
                await super().close()
        upload = ClosingUpload(io.BytesIO(wav_bytes()), filename='song.wav')
        task = asyncio.create_task(replacement.replace_voice(upload, self.voice_id))
        await closing.wait()
        try:
            task.cancel()
            await asyncio.sleep(0)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(db.list_tracks(), [])
        self.assertEqual(voice_build._applies, {})
        self.assertFalse(self.started.is_set())
        self.assertEqual(list((self.root / 'files').rglob('*.*')), [])

    async def test_uploaded_playlist_cannot_read_another_library_file(self) -> None:
        directory = self.root / 'files' / 'upload'
        directory.mkdir(parents=True)
        secret = directory / 'secret.wav'
        secret.write_bytes(wav_bytes())
        payload = b"ffconcat version 1.0\nfile 'secret.wav'\nduration 0.1\n"
        response = await self.replace(data=payload, filename='song.wav')
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()['detail'], 'invalid_audio')
        self.assertEqual(db.list_tracks(), [])
        self.assertEqual(list(directory.iterdir()), [secret])

    async def test_retry_separates_original_after_result_was_already_published(self) -> None:
        response = await self.replace()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        track_id = result['track']['id']
        await voice_build.cancel_apply(track_id)
        original = Path(db.get_track(result['source_track']['id'])['audio_path'])
        target = Path(db.get_track(track_id)['audio_path'])
        target.write_bytes(b'already converted result')
        class Captured(Exception):
            pass
        separation = AsyncMock(side_effect=Captured)
        with patch.object(voice_build, '_engine_python', return_value=Path(sys.executable)), \
             patch('app.seed_vc_compat.ensure_compatibility'), patch.object(voice_build, 'ensure_whisper_float32_off_cuda'), \
             patch.object(voice_build, 'separate_file', new=separation):
            with self.assertRaises(Captured):
                await self.real_apply(voice_build.ApplyJob(voice_id=self.voice_id, track_id=track_id))
        self.assertEqual(separation.await_args.args[0], original)
        self.assertEqual(original.read_bytes(), wav_bytes())
        self.assertEqual(target.read_bytes(), b'already converted result')

    async def test_deleted_original_fails_retry_without_replacing_result(self) -> None:
        response = await self.replace()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        track_id = result['track']['id']
        await voice_build.cancel_apply(track_id)
        target = Path(db.get_track(track_id)['audio_path'])
        existing = target.read_bytes()
        db.delete_track(result['source_track']['id'])
        with self.assertRaises(voice_build.VoiceBuildError) as error:
            await self.real_apply(voice_build.ApplyJob(voice_id=self.voice_id, track_id=track_id))
        self.assertEqual(error.exception.code, 'replacement_source_missing')
        self.assertEqual(target.read_bytes(), existing)

    async def test_supported_audio_containers_decode_without_altering_original_bytes(self) -> None:
        import shutil
        source = self.root / 'fixture.wav'
        source.write_bytes(wav_bytes())
        encoders = {'mp3': 'libmp3lame', 'flac': 'flac', 'ogg': 'libopus', 'opus': 'libopus', 'm4a': 'aac'}
        for extension, encoder in encoders.items():
            with self.subTest(extension=extension):
                encoded = self.root / f'fixture.{extension}'
                subprocess.run([shutil.which('ffmpeg') or 'ffmpeg', '-v', 'error', '-y', '-i', str(source),
                                '-c:a', encoder, str(encoded)], check=True)
                uploaded = encoded.read_bytes()
                response = await self.replace(filename=f'file.{extension}', data=uploaded)
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                original = Path(db.get_track(result['source_track']['id'])['audio_path'])
                target = Path(db.get_track(result['track']['id'])['audio_path'])
                self.assertEqual(original.read_bytes(), uploaded)
                with wave.open(str(target), 'rb') as decoded:
                    self.assertEqual(decoded.getframerate(), 44100)
                    self.assertEqual(decoded.getnchannels(), 2)
                    self.assertGreater(decoded.getnframes(), 0)
                await voice_build.cancel_apply(result['track']['id'])

    async def test_replacement_paths_reject_same_file_wrong_origin_and_escape(self) -> None:
        import json
        response = await self.replace()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        target_id, original_id = result['track']['id'], result['source_track']['id']
        await voice_build.cancel_apply(target_id)
        original_row = db.get_track(original_id)
        original = Path(original_row['audio_path'])
        target = Path(db.get_track(target_id)['audio_path'])
        paths = voice_build._apply_paths(db.get_track(target_id))
        self.assertEqual(paths, (original, target))
        connection = db.get_db()
        invalid = [(str(target), 'upload', original_row['params_json']),
                   (str(original), 'ace_step', original_row['params_json']),
                   (str(original), 'upload', json.dumps({'source': 'user_upload'})),
                   (str(self.root / 'outside.wav'), 'upload', original_row['params_json'])]
        (self.root / 'outside.wav').write_bytes(wav_bytes())
        for path, origin, provenance in invalid:
            with self.subTest(path=path, origin=origin, provenance=provenance):
                connection.execute('UPDATE tracks SET audio_path=?,model=?,params_json=? WHERE id=?', (path, origin, provenance, original_id))
                connection.commit()
                with self.assertRaises(voice_build.VoiceBuildError) as error:
                    voice_build._apply_paths(db.get_track(target_id))
                self.assertEqual(error.exception.code, 'replacement_source_missing')
                self.assertTrue(target.is_file())

    async def test_owned_audio_tool_drains_split_output_without_closing_liveness_pipe(self) -> None:
        code = 'import sys,time;sys.stdout.buffer.write(b"a"*65536);sys.stdout.flush();time.sleep(.05);sys.stdout.buffer.write(b"b"*65536)'
        proc = await voice_build.spawn_apply_worker([sys.executable, '-c', code], self.root, os.environ.copy(), asyncio.subprocess.PIPE)
        output = await voice_build._communicate_apply(proc, 5)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(output, b'a' * 65536 + b'b' * 65536)

    async def test_apply_ffmpeg_preserves_success_and_measured_logs(self) -> None:
        source = self.root / 'tool.wav'
        source.write_bytes(wav_bytes())
        text = await voice_build._ffmpeg(['-i', str(source), '-af', 'volumedetect', '-f', 'null', '-'],
            voice_build.ApplyJob(voice_id=self.voice_id, track_id=1).slot, lambda: False)
        self.assertIn('mean_volume:', text)

    async def test_transport_rejects_oversized_declared_body_before_multipart_parser(self) -> None:
        from app import voice_replacement as replacement
        with patch('starlette.formparsers.MultiPartParser.parse', side_effect=AssertionError('Body was parsed before transport rejection')):
            response = await self.client.post('/api/voices/replace', headers={
                'content-type': 'multipart/form-data; boundary=fixture',
                'content-length': str(replacement.MAX_UPLOAD_BYTES + 1024 * 1024)}, content=b'')
        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(response.json()['detail'], 'upload_too_large')
        self.assertFalse((self.root / 'files').exists())

    async def test_transport_limits_chunked_multipart_and_closes_parser_files(self) -> None:
        from app import voice_replacement as replacement
        spooled: list[tempfile.SpooledTemporaryFile[bytes]] = []
        def make_file(*, max_size: int) -> tempfile.SpooledTemporaryFile[bytes]:
            file = tempfile.SpooledTemporaryFile(max_size=max_size)
            spooled.append(file)
            return file
        last_chunk_read = False
        async def body():
            nonlocal last_chunk_read
            yield b'--fixture\r\nContent-Disposition: form-data; name="audio"; filename="song.wav"\r\nContent-Type: audio/wav\r\n\r\n'
            yield b'x' * (128 * 1024)
            last_chunk_read = True
            yield b'\r\n--fixture--\r\n'
        with patch.object(replacement, 'MAX_UPLOAD_BYTES', 64), patch('starlette.formparsers.SpooledTemporaryFile', side_effect=make_file):
            response = await self.client.post('/api/voices/replace', headers={'content-type': 'multipart/form-data; boundary=fixture'}, content=body())
        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(response.json()['detail'], 'upload_too_large')
        self.assertFalse(last_chunk_read)
        self.assertTrue(spooled)
        self.assertTrue(all(file.closed for file in spooled))
        self.assertFalse((self.root / 'files').exists())

    async def test_exact_file_byte_limit_allows_multipart_overhead(self) -> None:
        from app import voice_replacement as replacement
        audio = wav_bytes(.01)
        with patch.object(replacement, 'MAX_UPLOAD_BYTES', len(audio)):
            response = await self.replace(data=audio)
        self.assertEqual(response.status_code, 200, response.text)

    async def test_failed_process_kill_retains_owner_blocks_retry_and_allows_later_cleanup(self) -> None:
        from app.job_lifecycle import kill_process_tree, spawn_process
        entered = asyncio.Event()
        children: list[asyncio.subprocess.Process] = []
        partial = self.root / 'retained.partial.wav'
        work = self.root / 'retained-work'
        async def conversion(job: voice_build.ApplyJob) -> None:
            proc = await spawn_process(sys.executable, '-c', 'import time;time.sleep(60)',
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            children.append(proc)
            job.slot.track(proc)
            work.mkdir()
            partial.write_bytes(b'partial')
            job.work_dir = work
            job.partial_outputs.add(partial)
            entered.set()
            await asyncio.Event().wait()
        try:
            with patch.object(voice_build, '_apply_inner', side_effect=conversion):
                accepted = await self.replace()
                self.assertEqual(accepted.status_code, 200, accepted.text)
                result = accepted.json()
                track_id = result['track']['id']
                await entered.wait()
                with patch.object(voice_build, 'kill_proc', new=AsyncMock(side_effect=OSError('kill denied'))), self.assertLogs('app.voice_build', level='ERROR'):
                    cancelled = await self.client.post(f'/api/voices/apply/{track_id}/cancel')
                    self.assertEqual(cancelled.status_code, 409, cancelled.text)
                    self.assertIsNone(children[0].returncode)
                    self.assertIn(track_id, voice_build._applies, 'Live child lost its application owner')
                    self.assertTrue(voice_build.work_busy())
                    self.assertTrue(partial.exists())
                    self.assertTrue(work.exists())
                    retry = await self.client.post('/api/voices/apply', json={'voice_id': self.voice_id, 'track_id': track_id})
                    self.assertEqual(retry.status_code, 409, retry.text)
                    self.assertEqual(retry.json()['detail'], 'apply_busy')
                    deleted = await self.client.delete(f'/api/tracks/{track_id}')
                    self.assertEqual(deleted.status_code, 409, deleted.text)
                    self.assertIsNotNone(db.get_track(track_id))
                cancelled = await self.client.post(f'/api/voices/apply/{track_id}/cancel')
                self.assertEqual(cancelled.status_code, 200, cancelled.text)
                self.assertEqual(cancelled.json()['status'], 'cancelled')
                self.assertIsNotNone(children[0].returncode)
                self.assertNotIn(track_id, voice_build._applies)
                self.assertFalse(voice_build.work_busy())
                self.assertFalse(partial.exists())
                self.assertFalse(work.exists())
                self.assertEqual(len(db.list_tracks()), 2)
        finally:
            for child in children:
                await kill_process_tree(child)

    async def test_separation_status_write_failure_retains_spawned_child(self) -> None:
        from app.job_lifecycle import kill_process_tree, spawn_process
        children: list[asyncio.subprocess.Process] = []
        async def separation(_audio: Path, _out_dir: Path, *,
                             on_proc: Callable[[asyncio.subprocess.Process | None], None],
                             **_kwargs: object) -> dict[str, Path]:
            proc = await spawn_process(sys.executable, '-c', 'import time;time.sleep(60)',
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            children.append(proc)
            with patch.object(voice_build, '_write_apply', side_effect=OSError('disk full')):
                on_proc(proc)
            raise AssertionError('Expected the status write to fail')
        try:
            accepted = await self.replace()
            track_id = accepted.json()['track']['id']
            await voice_build.cancel_apply(track_id)
            with patch.object(voice_build, '_apply_inner', side_effect=self.real_apply), \
                 patch.object(voice_build, '_engine_python', return_value=Path(sys.executable)), \
                 patch('app.seed_vc_compat.ensure_compatibility'), patch.object(voice_build, 'ensure_whisper_float32_off_cuda'), \
                 patch.object(voice_build, 'separate_file', side_effect=separation), self.assertLogs('app.voice_build', level='ERROR'):
                voice_build.start_apply(self.voice_id, track_id)
                job = voice_build._applies[track_id]
                if job.task is None:
                    self.fail('Conversion task was not created')
                await job.task
            self.assertIsNone(children[0].returncode)
            self.assertIn(track_id, voice_build._applies, 'Status persistence dropped a live child owner')
            self.assertIs(job.slot.proc, children[0])
            self.assertTrue(voice_build.work_busy())
            await voice_build.cancel_apply(track_id)
            self.assertIsNotNone(children[0].returncode)
            self.assertNotIn(track_id, voice_build._applies)
        finally:
            for child in children:
                await kill_process_tree(child)


@unittest.skipIf(sys.platform == 'win32', 'POSIX supervisor crash fixture')
class VoiceReplacementCrashTests(unittest.TestCase):
    def test_converter_child_stops_when_backend_parent_is_killed(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch).resolve()
            marker = root / 'child.pid'
            child = f'import os,time;from pathlib import Path;Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(60)'
            code = ('import asyncio,os;from pathlib import Path;from unittest.mock import patch;from app import voice_build as v\n'
                    'async def main():\n'
                    f' with patch.object(v,"LOG_DIR",Path({str(root)!r})),patch.object(v,"VOICES_DIR",Path({str(root / "voices")!r})),patch.object(v,"_child_env",return_value=os.environ.copy()):\n'
                    f'  await v._spawn([{sys.executable!r},"-c",{child!r}],cwd=Path({str(root)!r}),log_name="cpu",slot=v.ApplyJob(voice_id="a"*32,track_id=1).slot)\n'
                    'asyncio.run(main())')
            env = os.environ.copy()
            env['REMIQORA_CONFIG'] = str(root / 'config.json')
            env['REMIQORA_DATA_DIR'] = str(root / 'data')
            parent = subprocess.Popen([sys.executable, '-c', code], env=env)
            pid: int | None = None
            try:
                import time
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(marker.exists())
                pid = int(marker.read_text())
                parent.kill()
                parent.wait(5)
                state = ''
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    state = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True, check=False).stdout.strip()
                    if not state or state.startswith('Z'):
                        break
                    time.sleep(.02)
                self.assertTrue(not state or state.startswith('Z'), f'Conversion child survived parent crash: {state}')
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.wait(5)
                if pid is not None:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
